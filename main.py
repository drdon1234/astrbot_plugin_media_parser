import asyncio
import copy
from typing import Any, Dict, Optional

import aiohttp

from .core.logger import logger

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Reply
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.event_message_type import EventMessageType

from .core.parser import ParserManager
from .core.parser.utils import extract_url_from_card_data
from .core.downloader import DownloadManager
from .core.storage import (
    cleanup_files,
    cleanup_marked_in,
    register_files_with_token_service,
)
from .core.constants import Config
from .core.message_adapter.sender import MessageSender
from .core.message_adapter.node_builder import (
    build_all_nodes,
    build_translation_nodes_for_all,
    summarize_node_counts,
)
from .core.translation import MetadataTranslator
from .core.config_manager import ConfigManager
from .core.interaction.platform.bilibili import BilibiliAdminCookieAssistManager


@register(
    "astrbot_plugin_media_parser",
    "drdon1234",
    "聚合解析流媒体平台链接，转换为媒体直链发送",
    "6.2.1"
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
        self.metadata_translator = MetadataTranslator(
            cfg.translation,
            self.context,
        )
        self.bilibili_auth_runtime = (
            self.bilibili_parser.get_auth_runtime()
            if self.bilibili_parser else
            None
        )

        self.download_manager = DownloadManager(
            max_video_size_mb=cfg.download.max_video_size_mb,
            large_video_threshold_mb=cfg.download.large_video_threshold_mb,
            cache_dir=cfg.download.cache_dir,
            cache_dir_available=cfg.download.cache_dir_available,
            max_concurrent_downloads=cfg.download.max_concurrent_downloads,
        )

        self.message_sender = MessageSender()
        self._cleanup_tasks: set[asyncio.Task] = set()
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
        await self._shutdown_delayed_cleanups()
        await self.admin_cookie_assist.shutdown()
        await self.download_manager.shutdown()

        if self.download_manager.cache_dir:
            cleanup_marked_in(self.download_manager.cache_dir)

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

    def _schedule_delayed_cleanup(self, files, delay: int):
        task = asyncio.create_task(self._delayed_cleanup(list(files), delay))
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    async def _shutdown_delayed_cleanups(self):
        tasks = list(self._cleanup_tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._cleanup_tasks.clear()

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
        messages = event.get_messages()
        if not messages:
            return [], ""

        reply_comp = None
        for comp in messages:
            if isinstance(comp, Reply):
                reply_comp = comp
                break
        if reply_comp is None:
            return [], ""

        reply_message_id = str(getattr(reply_comp, "id", "") or "").strip()
        reply_text = reply_comp.message_str or ""
        links = self.parser_manager.extract_all_links(reply_text)
        if links:
            return links, reply_message_id

        if reply_comp.chain:
            for comp in reply_comp.chain:
                card_url = extract_url_from_card_data(
                    getattr(comp, 'data', None)
                )
                if card_url:
                    links = self.parser_manager.extract_all_links(card_url)
                    if links:
                        return links, reply_message_id

        return [], ""

    @staticmethod
    def _has_sendable_rich_media(metadata_list) -> bool:
        """判断整条消息是否至少有一个可发送的富媒体。"""
        for metadata in metadata_list:
            video_modes = metadata.get("video_modes") or []
            image_modes = metadata.get("image_modes") or []
            if any(mode in ("local", "direct") for mode in video_modes):
                return True
            if any(mode in ("local", "direct") for mode in image_modes):
                return True
        return False

    @staticmethod
    def _has_text_metadata(metadata: Dict[str, Any]) -> bool:
        """判断解析结果是否包含可发送的文本元数据。"""
        return any(
            bool(str(metadata.get(key) or "").strip())
            for key in ("title", "author", "desc", "timestamp")
        )

    def _filter_links_by_output(self, links_with_parser):
        """过滤掉当前配置下不会产生任何输出的控制器链接。"""
        cfg = self.config_manager
        filtered = []
        for link, parser in links_with_parser:
            parser_name = getattr(parser, "name", "")
            if cfg.message.controller_has_any_output(parser_name):
                filtered.append((link, parser))
            elif cfg.admin.debug_mode:
                self.logger.debug(
                    f"控制器 {parser_name} 的文本元数据和富媒体均关闭，"
                    f"跳过链接: {link}"
                )
        return filtered

    def _apply_output_flags(self, metadata_list) -> None:
        """将每条解析结果的有效输出开关写入 metadata。"""
        for metadata in metadata_list:
            text_enabled, rich_enabled = (
                self.config_manager.message.output_for_metadata(metadata)
            )
            metadata["_enable_text_metadata"] = text_enabled
            metadata["_enable_rich_media"] = rich_enabled

    @staticmethod
    def _event_context(event: AstrMessageEvent) -> Dict[str, Any]:
        """提取 AstrBot 会话上下文，供内置大模型路由使用。"""
        umo = str(getattr(event, "unified_msg_origin", "") or "").strip()
        return {"_astrbot_unified_msg_origin": umo} if umo else {}

    def _start_translation_task(
        self,
        metadata_list,
        event: AstrMessageEvent,
    ):
        """后台启动翻译，使用元数据副本避免影响主消息先发。"""
        if not self.config_manager.translation.enabled:
            return None, []
        translation_metadata_list = copy.deepcopy(metadata_list)
        task = asyncio.create_task(
            self.metadata_translator.translate_metadata_list(
                translation_metadata_list,
                event_context=self._event_context(event),
            )
        )
        return task, translation_metadata_list

    @staticmethod
    async def _cancel_translation_task(task) -> None:
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _build_translation_nodes_after_task(
        self,
        task,
        translation_metadata_list,
    ):
        if task is None:
            return []
        try:
            await task
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.warning(f"等待翻译任务失败，跳过翻译节点: {e}")
            return []
        return build_translation_nodes_for_all(translation_metadata_list)

    def _metadata_has_output_candidate(
        self,
        metadata: Dict[str, Any]
    ) -> bool:
        """判断 metadata 在当前输出策略下是否可能构建出节点。"""
        if metadata.get("error"):
            return False

        text_enabled = bool(
            metadata.get(
                "_enable_text_metadata",
                True
            )
        )
        rich_enabled = bool(
            metadata.get(
                "_enable_rich_media",
                True
            )
        )
        has_media = bool(metadata.get("video_urls")) or bool(
            metadata.get("image_urls")
        )
        has_text = (
            self._has_text_metadata(metadata) or
            bool(metadata.get("access_message")) or
            has_media
        )
        return bool((rich_enabled and has_media) or (text_enabled and has_text))

    async def _handle_clean_cache(self, event: AstrMessageEvent):
        cache_dir = self.download_manager.cache_dir
        if not cache_dir:
            await event.send(event.plain_result("未配置媒体文件缓存目录"))
            return

        try:
            subdirs_cleaned, files_cleaned = cleanup_marked_in(cache_dir)
            msg = (
                "缓存清理完成: "
                f"{subdirs_cleaned} 个媒体子目录, {files_cleaned} 个文件"
            )
            await event.send(event.plain_result(msg))
            sender_id = str(event.get_sender_id() or "").strip()
            logger.info(
                f"管理员 {sender_id} 主动清理缓存: "
                f"{cache_dir}, {subdirs_cleaned} 个子目录, "
                f"{files_cleaned} 个文件"
            )
        except Exception as e:
            logger.warning(f"管理员清理缓存失败: {e}")
            await event.send(event.plain_result(f"清理失败: {e}"))

    # ── 主事件处理 ──────────────────────────────────────

    @filter.event_message_type(EventMessageType.ALL)
    async def auto_parse(self, event: AstrMessageEvent):
        cfg = self.config_manager
        self.admin_cookie_assist.try_update_admin_origin(event)

        if not cfg.message.has_any_output():
            if cfg.admin.debug_mode:
                self.logger.debug("文本元数据和富媒体均关闭，跳过解析")
            return

        is_private = event.is_private_chat()
        sender_id = event.get_sender_id()
        group_id = None if is_private else event.get_group_id()

        if not cfg.permission.check(is_private, sender_id, group_id):
            return

        original_message_text = event.message_str or ""
        parse_text = original_message_text
        quote_source_message_id = str(
            getattr(event.message_obj, "message_id", "") or ""
        ).strip()

        clean_kw = cfg.admin.clean_cache_keyword
        if clean_kw and original_message_text.strip() == clean_kw:
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
            parse_text = card_url

        links_with_parser = self.parser_manager.extract_all_links(
            parse_text
        )
        found_direct_links = bool(links_with_parser)
        if found_direct_links:
            links_with_parser = self._filter_links_by_output(links_with_parser)
            if not links_with_parser:
                return

        if not links_with_parser:
            if (
                cfg.trigger.reply_trigger
                and cfg.trigger.has_keyword(original_message_text)
            ):
                links_with_parser, reply_message_id = (
                    self._try_extract_reply_links(event)
                )
                if reply_message_id:
                    quote_source_message_id = reply_message_id
                links_with_parser = self._filter_links_by_output(
                    links_with_parser
                )
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

        if not cfg.trigger.should_parse(original_message_text):
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
                parse_text,
                session,
                links_with_parser=links_with_parser
            )
            self._trigger_bilibili_cookie_assist_if_needed()
            if not metadata_list:
                if cfg.admin.debug_mode:
                    self.logger.debug("解析后未获得任何元数据")
                return
            self._apply_output_flags(metadata_list)

            has_valid_metadata = any(
                self._metadata_has_output_candidate(metadata)
                for metadata in metadata_list
            )

            if not has_valid_metadata:
                if cfg.admin.debug_mode:
                    self.logger.debug(
                        "解析后未获得任何有效元数据"
                        "（可能是直播链接或解析失败）"
                    )
                return

            translation_task, translation_metadata_list = (
                self._start_translation_task(metadata_list, event)
            )

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

            opening_sent = False
            should_process_rich_media = any(
                bool(metadata.get("_enable_rich_media", True))
                for metadata in metadata_list
            )
            if should_process_rich_media:
                opening_lock = asyncio.Lock()

                async def send_opening_once() -> None:
                    nonlocal opening_sent
                    if not cfg.message.opening_enabled:
                        return
                    async with opening_lock:
                        if opening_sent:
                            return
                        msg_text = (
                            cfg.message.opening_content
                            or "流媒体解析bot为您服务 ٩( 'ω' )و"
                        )
                        try:
                            await event.send(event.plain_result(msg_text))
                            opening_sent = True
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            self.logger.warning(f"发送开场语失败: {e}")

                async def process_single(
                    metadata: Dict[str, Any]
                ) -> Dict[str, Any]:
                    if (
                        metadata.get('error') or
                        not metadata.get(
                            "_enable_rich_media",
                            True
                        )
                    ):
                        return metadata
                    try:
                        return await self.download_manager.process_metadata(
                            session,
                            metadata,
                            proxy_addr=cfg.proxy.address,
                            on_sendable_media=send_opening_once,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        self.logger.exception(
                            f"处理元数据失败: "
                            f"{metadata.get('url', '')}, 错误: {e}"
                        )
                        metadata['error'] = str(e)
                        return metadata

                async def process_indexed(
                    index: int,
                    metadata: Dict[str, Any]
                ):
                    return index, await process_single(metadata)

                tasks = [
                    asyncio.create_task(process_indexed(i, m))
                    for i, m in enumerate(metadata_list)
                ]
                processed_metadata_list = [None] * len(metadata_list)

                try:
                    for completed in asyncio.as_completed(tasks):
                        i, md = await completed
                        processed_metadata_list[i] = md

                        if (
                            not opening_sent and
                            self._has_sendable_rich_media([md])
                        ):
                            await send_opening_once()
                except asyncio.CancelledError:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    await self._cancel_translation_task(translation_task)
                    raise
                except Exception:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    await self._cancel_translation_task(translation_task)
                    raise

                processed_metadata_list = [
                    md if md is not None else metadata_list[i]
                    for i, md in enumerate(processed_metadata_list)
                ]
            else:
                if cfg.admin.debug_mode:
                    self.logger.debug("富媒体输出已关闭，跳过下载阶段")
                processed_metadata_list = metadata_list

            # ── 文件 Token 服务注册 ──────────────────────

            if cfg.relay.enabled:
                for metadata in processed_metadata_list:
                    if not metadata.get(
                        "_enable_rich_media",
                        True
                    ):
                        continue
                    await register_files_with_token_service(
                        metadata,
                        cfg.relay.callback_api_base,
                        cfg.relay.file_token_ttl,
                    )

            # ── 节点构建与发送 ───────────────────────────

            build_result = build_all_nodes(
                processed_metadata_list,
                cfg.message.pack_mode,
                cfg.download.large_video_threshold_mb,
                cfg.download.max_video_size_mb,
                True,
                True,
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
                if opening_sent:
                    try:
                        await event.send(event.plain_result(
                            "解析完成，但没有可发送的媒体内容，"
                            "可能是下载失败或媒体不可访问。"
                        ))
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        self.logger.warning(f"发送空结果提示失败: {e}")
                await self._cancel_translation_task(translation_task)
                cleanup_files(build_result.temp_files + build_result.video_files)
                return

            node_counts = summarize_node_counts(build_result.all_link_nodes)
            should_pack = cfg.message.should_pack(**node_counts)

            if cfg.admin.debug_mode:
                self.logger.debug(
                    f"开始发送结果，打包模式: {cfg.message.pack_mode}, "
                    f"实际打包: {should_pack}, "
                    f"图片节点: {node_counts['image_count']}, "
                    f"视频节点: {node_counts['video_count']}, "
                    f"总节点: {node_counts['node_count']}"
                )

            try:
                if should_pack:
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
                        build_result.link_metadata,
                        quote_user_message=cfg.message.quote_user_message,
                        quote_message_id=quote_source_message_id,
                    )

                translation_nodes = await self._build_translation_nodes_after_task(
                    translation_task,
                    translation_metadata_list,
                )
                await self.message_sender.send_translation_results(
                    event,
                    translation_nodes,
                    should_pack=should_pack,
                    sender_name=sender_name,
                    sender_id=sender_id,
                )

                if cfg.admin.debug_mode:
                    self.logger.debug("发送完成")
            except Exception as e:
                await self._cancel_translation_task(translation_task)
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
                    self._schedule_delayed_cleanup(all_files, delay)
                elif all_files:
                    cleanup_files(all_files)
                    if cfg.admin.debug_mode:
                        self.logger.debug(
                            f"已清理文件: "
                            f"临时 {len(build_result.temp_files)} 个, "
                            f"视频 {len(build_result.video_files)} 个"
                        )
