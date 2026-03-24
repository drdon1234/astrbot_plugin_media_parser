import asyncio
from typing import Any, Dict, Optional

import aiohttp

from .core.logger import logger

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.event_message_type import EventMessageType

from .core.parser import ParserManager
from .core.parser.utils import extract_url_from_card_data
from .core.downloader import DownloadManager
from .core.storage import cleanup_files, CacheRegistry, register_files_with_token_service
from .core.constants import Config
from .core.message_adapter.sender import MessageSender
from .core.message_adapter.node_builder import build_all_nodes
from .core.config_manager import ConfigManager
from .core.interaction.platform.bilibili import BilibiliAdminCookieAssistManager


@register(
    "astrbot_plugin_media_parser",
    "drdon1234",
    "聚合解析流媒体平台链接，转换为媒体直链发送",
    "0.6.0"
)
class VideoParserPlugin(Star):

    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.logger = logger

        self.config_manager = ConfigManager(config)
        cfg = self.config_manager

        parsers = cfg.create_parsers()
        self.parser_manager = ParserManager(parsers)
        self.bilibili_parser = cfg.bilibili_parser
        self.bilibili_auth_runtime = (
            self.bilibili_parser.get_auth_runtime()
            if self.bilibili_parser else
            None
        )

        self.download_manager = DownloadManager(
            max_video_size_mb=cfg.download.max_video_size_mb,
            large_video_threshold_mb=cfg.download.large_video_threshold_mb,
            cache_dir=cfg.download.cache_dir,
            pre_download_all_media=cfg.download.pre_download_all_media,
            max_concurrent_downloads=cfg.download.max_concurrent_downloads,
        )

        self.cache_registry = CacheRegistry()
        if cfg.download.cache_dir:
            label = "media_relay" if cfg.relay.enabled else "pre_download"
            self.cache_registry.register(cfg.download.cache_dir, label)

        self.message_sender = MessageSender()
        self.admin_cookie_assist = BilibiliAdminCookieAssistManager(
            context=self.context,
            admin_id=cfg.permission.admin_id,
            enabled=(
                cfg.bilibili.cookie_runtime_enabled and
                cfg.bilibili.enable_admin_assist
            ),
            reply_timeout_minutes=cfg.bilibili.admin_reply_timeout_minutes,
            request_cooldown_minutes=cfg.bilibili.admin_request_cooldown_minutes,
        )

    async def terminate(self):
        await self.admin_cookie_assist.shutdown()
        await self.download_manager.shutdown()

        if self.download_manager.cache_dir:
            CacheRegistry.cleanup_marked_in(self.download_manager.cache_dir)

    # ── 内部辅助 ────────────────────────────────────────

    def _trigger_bilibili_cookie_assist_if_needed(self):
        if not self.bilibili_parser:
            return
        reason = self.bilibili_parser.consume_assist_request()
        if not reason:
            return
        self.admin_cookie_assist.trigger_assist_request(reason)

    async def _delayed_cleanup(self, files, delay: int):
        try:
            await asyncio.sleep(delay)
            cleanup_files(files)
            logger.debug(f"延迟清理完成: {len(files)} 个文件")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"延迟清理文件失败: {e}")

    def _extract_url_from_json_card(
        self, event: AstrMessageEvent
    ) -> Optional[str]:
        try:
            messages = event.get_messages()
            if not messages:
                return None
            return extract_url_from_card_data(messages[0].data)
        except (AttributeError, IndexError, TypeError) as e:
            if self.config_manager.admin.debug_mode:
                self.logger.debug(f"提取JSON卡片链接失败: {e}")
            return None

    def _try_extract_reply_links(self, event: AstrMessageEvent):
        try:
            from astrbot.api.message_components import Reply
        except ImportError:
            return []

        messages = event.get_messages()
        if not messages:
            return []

        reply_comp = None
        for comp in messages:
            if isinstance(comp, Reply):
                reply_comp = comp
                break
        if reply_comp is None:
            return []

        reply_text = reply_comp.message_str or ""
        links = self.parser_manager.extract_all_links(reply_text)
        if links:
            return links

        if reply_comp.chain:
            for comp in reply_comp.chain:
                card_url = extract_url_from_card_data(
                    getattr(comp, 'data', None)
                )
                if card_url:
                    links = self.parser_manager.extract_all_links(card_url)
                    if links:
                        return links

        return []

    async def _handle_clean_cache(self, event: AstrMessageEvent):
        registered = self.cache_registry.get_all()
        if not registered:
            await event.send(event.plain_result("无已注册的缓存目录"))
            return

        try:
            subdirs_cleaned, files_cleaned, skipped = (
                self.cache_registry.cleanup_all()
            )
            parts = [
                f"缓存清理完成: "
                f"{subdirs_cleaned} 个媒体子目录, {files_cleaned} 个文件"
            ]
            if skipped:
                parts.append(
                    f"以下根目录无可清理内容: {', '.join(skipped)}"
                )
            msg = "\n".join(parts)
            await event.send(event.plain_result(msg))
            sender_id = str(event.get_sender_id() or "").strip()
            logger.info(
                f"管理员 {sender_id} 主动清理缓存: "
                f"{subdirs_cleaned} 个子目录, {files_cleaned} 个文件"
            )
        except Exception as e:
            logger.warning(f"管理员清理缓存失败: {e}")
            await event.send(event.plain_result(f"清理失败: {e}"))

    # ── 主事件处理 ──────────────────────────────────────

    @filter.event_message_type(EventMessageType.ALL)
    async def auto_parse(self, event: AstrMessageEvent):
        cfg = self.config_manager
        self.admin_cookie_assist.try_update_admin_origin(event)

        is_private = event.is_private_chat()
        sender_id = event.get_sender_id()
        group_id = None if is_private else event.get_group_id()

        if not cfg.permission.check(is_private, sender_id, group_id):
            return

        message_text = event.message_str

        clean_kw = cfg.admin.clean_cache_keyword
        if clean_kw and message_text.strip() == clean_kw:
            if (
                is_private
                and cfg.permission.admin_id
                and str(sender_id or "").strip() == cfg.permission.admin_id
            ):
                await self._handle_clean_cache(event)
            return

        card_url = self._extract_url_from_json_card(event)
        if card_url:
            if cfg.admin.debug_mode:
                self.logger.debug(
                    f"[media_parser] 从JSON卡片提取到链接: {card_url}"
                )
            message_text = card_url

        links_with_parser = self.parser_manager.extract_all_links(
            message_text
        )

        if not links_with_parser:
            if (
                cfg.trigger.reply_trigger
                and cfg.trigger.has_keyword(event.message_str)
            ):
                links_with_parser = self._try_extract_reply_links(event)
                if links_with_parser and cfg.admin.debug_mode:
                    self.logger.debug(
                        f"通过回复触发解析，提取到 "
                        f"{len(links_with_parser)} 个链接"
                    )
            if not links_with_parser:
                await self.admin_cookie_assist.handle_admin_reply(
                    event,
                    self.bilibili_auth_runtime
                )
                return

        if not cfg.trigger.should_parse(message_text):
            return

        if cfg.admin.debug_mode:
            self.logger.debug(
                f"提取到 {len(links_with_parser)} 个可解析链接: "
                f"{[link for link, _ in links_with_parser]}"
            )

        sender_name, sender_id = self.message_sender.get_sender_info(event)

        timeout = aiohttp.ClientTimeout(total=Config.DEFAULT_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            metadata_list = await self.parser_manager.parse_text(
                message_text,
                session,
                links_with_parser=links_with_parser
            )
            self._trigger_bilibili_cookie_assist_if_needed()
            if not metadata_list:
                if cfg.admin.debug_mode:
                    self.logger.debug("解析后未获得任何元数据")
                return

            has_valid_metadata = any(
                not metadata.get('error') and
                (
                    bool(metadata.get('video_urls')) or
                    bool(metadata.get('image_urls')) or
                    bool(metadata.get('access_message'))
                )
                for metadata in metadata_list
            )

            if not has_valid_metadata:
                if cfg.admin.debug_mode:
                    self.logger.debug(
                        "解析后未获得任何有效元数据"
                        "（可能是直播链接或解析失败）"
                    )
                return

            if cfg.message.opening_enabled:
                msg_text = (
                    cfg.message.opening_content
                    or "流媒体解析bot为您服务 ٩( 'ω' )و"
                )
                await event.send(event.plain_result(msg_text))

            if cfg.admin.debug_mode:
                self.logger.debug(
                    f"解析获得 {len(metadata_list)} 条元数据"
                )
                for idx, metadata in enumerate(metadata_list):
                    self.logger.debug(
                        f"元数据[{idx}]: url={metadata.get('url')}, "
                        f"video_count={len(metadata.get('video_urls', []))}, "
                        f"image_count={len(metadata.get('image_urls', []))}, "
                        f"video_force_download="
                        f"{metadata.get('video_force_download')}"
                    )

            # ── 元数据处理（下载）────────────────────────

            async def process_single(
                metadata: Dict[str, Any]
            ) -> Dict[str, Any]:
                if metadata.get('error'):
                    return metadata
                try:
                    return await self.download_manager.process_metadata(
                        session,
                        metadata,
                        proxy_addr=cfg.proxy.address
                    )
                except (Exception, asyncio.CancelledError) as e:
                    self.logger.exception(
                        f"处理元数据失败: "
                        f"{metadata.get('url', '')}, 错误: {e}"
                    )
                    metadata['error'] = str(e)
                    return metadata

            tasks = [process_single(m) for m in metadata_list]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            processed_metadata_list = []
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    md = metadata_list[i] if i < len(metadata_list) else {}
                    error_msg = str(result)
                    self.logger.exception(
                        f"处理元数据时发生未捕获的异常: "
                        f"{md.get('url', '未知URL')}, "
                        f"错误类型: {type(result).__name__}, "
                        f"错误: {error_msg}"
                    )
                    md['error'] = error_msg
                    processed_metadata_list.append(md)
                elif isinstance(result, dict):
                    processed_metadata_list.append(result)
                else:
                    md = metadata_list[i] if i < len(metadata_list) else {}
                    error_msg = f'未知错误类型: {type(result).__name__}'
                    self.logger.warning(
                        f"处理元数据返回了意外的结果类型: "
                        f"{md.get('url', '未知URL')}, "
                        f"类型: {type(result).__name__}"
                    )
                    md['error'] = error_msg
                    processed_metadata_list.append(md)

            # ── 文件 Token 服务注册 ──────────────────────

            if cfg.relay.enabled:
                for metadata in processed_metadata_list:
                    await register_files_with_token_service(
                        metadata,
                        cfg.relay.callback_api_base,
                        cfg.relay.file_token_ttl,
                    )

            # ── 节点构建与发送 ───────────────────────────

            build_result = build_all_nodes(
                processed_metadata_list,
                cfg.message.auto_pack,
                cfg.download.large_video_threshold_mb,
                cfg.download.max_video_size_mb,
                cfg.message.text_metadata,
            )

            if cfg.admin.debug_mode:
                self.logger.debug(
                    f"节点构建完成: "
                    f"{len(build_result.all_link_nodes)} 个链接节点, "
                    f"{len(build_result.temp_files)} 个临时文件, "
                    f"{len(build_result.video_files)} 个视频文件"
                )

            if not build_result.all_link_nodes:
                if cfg.admin.debug_mode:
                    self.logger.debug("未构建任何节点，跳过发送")
                return

            if cfg.admin.debug_mode:
                self.logger.debug(
                    f"开始发送结果，打包模式: {cfg.message.auto_pack}"
                )

            try:
                if cfg.message.auto_pack:
                    await self.message_sender.send_packed_results(
                        event,
                        build_result.link_metadata,
                        sender_name,
                        sender_id,
                        cfg.download.large_video_threshold_mb,
                    )
                else:
                    await self.message_sender.send_unpacked_results(
                        event,
                        build_result.all_link_nodes,
                    )

                if cfg.admin.debug_mode:
                    self.logger.debug("发送完成")
            except Exception as e:
                self.logger.exception(
                    f"发送消息失败: {e}, "
                    f"临时文件数: {len(build_result.temp_files)}, "
                    f"视频文件数: {len(build_result.video_files)}"
                )
                raise
            finally:
                all_files = (
                    build_result.temp_files + build_result.video_files
                )
                if cfg.relay.enabled and all_files:
                    delay = cfg.relay.file_token_ttl
                    if cfg.admin.debug_mode:
                        self.logger.debug(
                            f"文件Token服务模式下延迟 {delay}s 后清理 "
                            f"{len(all_files)} 个文件"
                        )
                    asyncio.create_task(
                        self._delayed_cleanup(all_files, delay)
                    )
                elif all_files:
                    cleanup_files(all_files)
                    if cfg.admin.debug_mode:
                        self.logger.debug(
                            f"已清理文件: "
                            f"临时 {len(build_result.temp_files)} 个, "
                            f"视频 {len(build_result.video_files)} 个"
                        )
