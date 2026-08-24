import asyncio
import copy
import tempfile
import uuid
from pathlib import Path
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
    cleanup_expired_marked_in,
    cleanup_files,
    cleanup_marked_in,
    mark_files_expire_after,
    ParseRecordManager,
    register_file_with_token_service,
    register_files_with_token_service,
)
from .core.constants import Config
from .core.message_adapter.sender import MessageDeliveryError, MessageSender
from .core.message_adapter.node_builder import (
    build_all_nodes,
    build_translation_nodes_for_all,
    collect_text_metadata,
    summarize_node_counts,
    strip_text_metadata_nodes,
)
from .core.message_adapter.text_renderer import render_text_metadata_image
from .core.message_adapter.archive_builder import (
    ArchiveSizeLimitError,
    build_zip_archive,
    cleanup_expired_zip_workspaces,
    cleanup_zip_archive,
)
from .core.translation import MetadataTranslator
from .core.config_manager import ConfigManager
from .core.interaction.platform.bilibili import BilibiliAdminCookieAssistManager


@register(
    "astrbot_plugin_media_parser",
    "drdon1234",
    "聚合解析流媒体平台链接，转换为媒体直链发送",
    "1.1.0",
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
            self.bilibili_parser.get_auth_runtime() if self.bilibili_parser else None
        )

        self.download_manager = DownloadManager(
            max_video_size_mb=cfg.download.max_video_size_mb,
            large_video_threshold_mb=cfg.download.large_video_threshold_mb,
            cache_dir=cfg.download.cache_dir,
            cache_dir_available=cfg.download.cache_dir_available,
            max_concurrent_downloads=cfg.download.max_concurrent_downloads,
            video_cover_only=cfg.message.media_display.video_cover_only,
        )

        self.message_sender = MessageSender()
        self._cleanup_tasks: set[asyncio.Task] = set()
        self._expired_cleanup_task: Optional[asyncio.Task] = None
        self._active_media_flows = 0
        self._cache_cleanup_lock = asyncio.Lock()
        rate_limit = cfg.parse_rate_limit
        self.parse_record_manager = ParseRecordManager(
            record_file=rate_limit.record_file,
            same_link_max_count=rate_limit.same_link.max_count,
            same_link_window_seconds=rate_limit.same_link.window_seconds,
            same_user_max_count=rate_limit.same_user.max_count,
            same_user_window_seconds=rate_limit.same_user.window_seconds,
        )
        self.admin_cookie_assist = BilibiliAdminCookieAssistManager(
            context=self.context,
            admin_id=cfg.permission.admin_id,
            enabled=(
                cfg.bilibili.cookie_runtime_enabled and cfg.bilibili.enable_admin_assist
            ),
            reply_timeout_minutes=cfg.bilibili.admin_reply_timeout_minutes,
            request_cooldown_minutes=cfg.bilibili.admin_request_cooldown_minutes,
            command=cfg.bilibili.admin_cookie_update_command,
        )
        self._start_expired_cache_cleanup()

    async def terminate(self):
        await self._shutdown_expired_cache_cleanup()
        await self._shutdown_delayed_cleanups()
        await self.admin_cookie_assist.shutdown()
        await self.download_manager.shutdown()

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
            async with self._cache_cleanup_lock:
                await self._run_blocking_to_completion(cleanup_files, files)
            logger.debug(f"延迟清理完成: {len(files)} 个文件")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"延迟清理文件失败: {e}")

    def _schedule_delayed_cleanup(self, files, delay: int):
        files = list(files)
        marked = mark_files_expire_after(files, delay)
        if marked and self.config_manager.admin.debug_mode:
            logger.debug(f"已写入 {marked} 个媒体缓存子目录的过期标记")
        task = asyncio.create_task(self._delayed_cleanup(files, delay))
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    @staticmethod
    async def _wait_for_task_completion(task: asyncio.Task):
        """即使调用方被取消，也观察并等待不可取消的线程任务真实结束。"""
        cancellation = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                if cancellation is None:
                    cancellation = exc
            except BaseException:
                break

        try:
            result = task.result()
        except BaseException as exc:
            if cancellation is not None:
                raise cancellation from exc
            raise
        return result, cancellation

    @classmethod
    async def _run_blocking_to_completion(cls, func, /, *args, **kwargs):
        task = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
        result, cancellation = await cls._wait_for_task_completion(task)
        if cancellation is not None:
            raise cancellation
        return result

    def _cleanup_expired_cache_once(self) -> None:
        cache_dir = self.download_manager.cache_dir
        try:
            if cache_dir:
                subdirs_cleaned, files_cleaned, failed_subdirs = (
                    cleanup_expired_marked_in(
                        cache_dir,
                        ttl_seconds=self.config_manager.relay.file_token_ttl,
                    )
                )
                if subdirs_cleaned:
                    logger.info(
                        f"已清理过期媒体缓存: {subdirs_cleaned} 个子目录, "
                        f"{files_cleaned} 个文件"
                    )
                if failed_subdirs:
                    logger.warning(f"有 {failed_subdirs} 个过期媒体缓存子目录清理失败")
            zip_cleaned, zip_failed = cleanup_expired_zip_workspaces()
            if zip_cleaned:
                logger.info(f"已清理 {zip_cleaned} 个过期ZIP工作目录")
            if zip_failed:
                logger.warning(f"有 {zip_failed} 个过期ZIP工作目录清理失败")
        except Exception as e:
            logger.warning(f"清理过期媒体缓存失败: {e}")

    def _expired_cleanup_interval(self) -> int:
        ttl = self.config_manager.relay.file_token_ttl
        try:
            ttl = int(ttl)
        except (TypeError, ValueError):
            ttl = 300
        return max(30, min(ttl, 300))

    async def _expired_cache_cleanup_loop(self) -> None:
        try:
            while True:
                async with self._cache_cleanup_lock:
                    if self._active_media_flows == 0:
                        await self._run_blocking_to_completion(
                            self._cleanup_expired_cache_once
                        )
                await asyncio.sleep(self._expired_cleanup_interval())
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"过期媒体缓存清理任务异常退出: {e}")

    def _start_expired_cache_cleanup(self) -> None:
        if self._expired_cleanup_task and not self._expired_cleanup_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._expired_cleanup_task = loop.create_task(
            self._expired_cache_cleanup_loop()
        )

    async def _shutdown_expired_cache_cleanup(self):
        task = self._expired_cleanup_task
        self._expired_cleanup_task = None
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _shutdown_delayed_cleanups(self):
        tasks = list(self._cleanup_tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._cleanup_tasks.clear()

    def _extract_urls_from_json_cards(self, event: AstrMessageEvent) -> list[str]:
        try:
            messages = event.get_messages()
            if not messages:
                return []
            urls: list[str] = []
            seen = set()
            for component in messages:
                url = extract_url_from_card_data(getattr(component, "data", None))
                if url and url not in seen:
                    seen.add(url)
                    urls.append(url)
            return urls
        except (AttributeError, IndexError, TypeError) as e:
            if self.config_manager.admin.debug_mode:
                self.logger.debug(f"提取JSON卡片链接失败: {e}")
            return []

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
        sources = [reply_comp.message_str or ""]
        if reply_comp.chain:
            for comp in reply_comp.chain:
                card_url = extract_url_from_card_data(getattr(comp, "data", None))
                if card_url:
                    sources.append(card_url)

        links = self.parser_manager.extract_all_links("\n".join(sources))
        if links:
            return links, reply_message_id

        return [], ""

    @staticmethod
    def _has_text_metadata(metadata: Dict[str, Any]) -> bool:
        """判断解析结果是否包含可发送的文本元数据。"""
        fields = metadata.get("_text_metadata_fields")
        if not isinstance(fields, dict):
            fields = {}
        candidates = (
            ("title", "title"),
            ("author", "author"),
            ("description", "desc"),
            ("timestamp", "timestamp"),
            ("original_link", "url"),
        )
        return any(
            bool(fields.get(field_name, True))
            and bool(str(metadata.get(metadata_key) or "").strip())
            for field_name, metadata_key in candidates
        )

    def _filter_links_by_output(self, links_with_parser):
        """过滤掉当前配置下不会产生任何输出的控制器链接。"""
        cfg = self.config_manager
        filtered = []
        for link, parser in links_with_parser:
            parser_name = getattr(parser, "name", "")
            if cfg.parser_output.controller_has_any_output(parser_name):
                filtered.append((link, parser))
            elif cfg.admin.debug_mode:
                self.logger.debug(
                    f"控制器 {parser_name} 的文本元数据和富媒体均关闭，跳过链接: {link}"
                )
        return filtered

    def _apply_output_flags(self, metadata_list) -> None:
        """将每条解析结果的有效输出开关写入 metadata。"""
        for metadata in metadata_list:
            text_enabled, rich_enabled = (
                self.config_manager.parser_output.output_for_metadata(metadata)
            )
            metadata["_enable_text_metadata"] = text_enabled
            metadata["_enable_rich_media"] = rich_enabled
            metadata["_text_metadata_fields"] = (
                self.config_manager.message.text_metadata.visibility()
            )

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
        """后台启动翻译并与下载并行，使用副本隔离解析元数据。"""
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

    async def _cancel_translation_task(self, task) -> None:
        if task is None:
            return
        if task.done():
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                self.logger.warning(f"后台翻译任务失败: {exc}")
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

    async def _render_text_metadata_output(
        self,
        build_result,
        translation_nodes,
        cfg,
    ) -> str:
        """将所有文本节点合并渲染为图片，失败时保留原文本节点。"""
        if not getattr(cfg.message.text_metadata, "render_to_image", False):
            return ""

        text = collect_text_metadata(
            build_result.all_link_nodes,
            translation_nodes,
        )
        if not text:
            return ""

        cache_root = str(cfg.download.cache_dir or "").strip()
        if cache_root:
            image_dir = Path(cache_root).resolve() / "rendered_text"
        else:
            image_dir = Path(tempfile.gettempdir()).resolve() / "astrbot_media_parser"
        image_path = image_dir / f"metadata_{uuid.uuid4().hex}.png"
        try:
            rendered_path = await render_text_metadata_image(
                text,
                str(image_path),
                style=getattr(
                    cfg.message.text_metadata,
                    "render_style",
                    "fresh",
                ),
                font_family=getattr(
                    cfg.message.text_metadata,
                    "render_font_family",
                    "noto_sans",
                ),
                font_size=getattr(
                    cfg.message.text_metadata,
                    "render_font_size",
                    24,
                ),
            )
        except asyncio.CancelledError:
            try:
                image_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        except (
            ImportError,
            OSError,
            RuntimeError,
            ValueError,
            asyncio.TimeoutError,
        ) as exc:
            try:
                image_path.unlink(missing_ok=True)
            except OSError:
                pass
            self.logger.warning(f"文本元数据图片渲染失败，保留原文本节点: {exc}")
            return ""

        strip_text_metadata_nodes(
            build_result.all_link_nodes,
            translation_nodes,
        )
        for metadata in build_result.link_metadata:
            metadata["metadata_text_node"] = None
        self.logger.debug(f"文本元数据已渲染为图片: {rendered_path}")
        return rendered_path

    def _metadata_has_output_candidate(self, metadata: Dict[str, Any]) -> bool:
        """判断 metadata 在当前输出策略下是否可能构建出节点。"""
        if metadata.get("error"):
            return True

        text_enabled = bool(metadata.get("_enable_text_metadata", True))
        rich_enabled = bool(metadata.get("_enable_rich_media", True))
        has_media = bool(metadata.get("video_urls")) or bool(metadata.get("image_urls"))
        has_text = (
            self._has_text_metadata(metadata)
            or bool(metadata.get("access_message"))
            or bool(metadata.get("hot_comments"))
        )
        return bool((rich_enabled and has_media) or (text_enabled and has_text))

    @staticmethod
    def _collect_metadata_files(metadata_list) -> list[str]:
        files: list[str] = []
        seen = set()
        for metadata in metadata_list:
            for path in metadata.get("file_paths") or []:
                path_text = str(path or "").strip()
                if path_text and path_text not in seen:
                    seen.add(path_text)
                    files.append(path_text)
        return files

    @staticmethod
    def _unique_files(*groups) -> list[str]:
        files: list[str] = []
        seen = set()
        for group in groups:
            for path in group or []:
                path_text = str(path or "").strip()
                if path_text and path_text not in seen:
                    seen.add(path_text)
                    files.append(path_text)
        return files

    async def _process_and_send_metadata(
        self,
        *,
        event: AstrMessageEvent,
        session: aiohttp.ClientSession,
        metadata_list,
        cfg,
        sender_name: str,
        sender_id: Any,
        quote_source_message_id: str,
        zip_requested: bool,
        translation_task,
        translation_metadata_list,
    ) -> None:
        """下载、构建和发送结果，并统一管理任务及文件生命周期。"""
        async with self._cache_cleanup_lock:
            self._active_media_flows += 1
        opening_sent = False
        archive_path = ""
        archive_sent = False
        build_result = None
        processed_metadata_list = metadata_list
        relay_registered = False
        text_metadata_image_path = ""
        text_metadata_image_ref = ""

        try:
            opening_lock = asyncio.Lock()
            opening_text = (
                cfg.message.opening.archive_content
                if zip_requested
                else cfg.message.opening.content
            )

            async def send_opening_once() -> None:
                nonlocal opening_sent
                if not cfg.message.opening.enabled:
                    return
                async with opening_lock:
                    if opening_sent:
                        return
                    msg_text = str(opening_text or "").strip()
                    if not msg_text:
                        return
                    try:
                        await event.send(event.plain_result(msg_text))
                        opening_sent = True
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        self.logger.warning(f"发送开场语失败: {exc}")

            should_process_rich_media = any(
                bool(metadata.get("_enable_rich_media", True))
                for metadata in metadata_list
            )
            if should_process_rich_media:
                async def process_single(metadata: Dict[str, Any]):
                    if metadata.get("error") or not metadata.get(
                        "_enable_rich_media", True
                    ):
                        return metadata
                    try:
                        return await self.download_manager.process_metadata(
                            session,
                            metadata,
                            proxy_addr=cfg.proxy.address,
                            on_sendable_media=send_opening_once,
                            video_cover_only=(False if zip_requested else None),
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        self.logger.exception(
                            f"处理元数据失败: {metadata.get('url', '')}, 错误: {exc}"
                        )
                        metadata["error"] = str(exc)
                        return metadata

                processed_metadata_list = await asyncio.gather(
                    *(process_single(metadata) for metadata in metadata_list)
                )
            elif cfg.admin.debug_mode:
                self.logger.debug("富媒体输出已关闭，跳过下载阶段")

            if zip_requested:
                await send_opening_once()
                await self._build_translation_nodes_after_task(
                    translation_task,
                    translation_metadata_list,
                )
                archive_task = asyncio.create_task(
                    asyncio.to_thread(
                        build_zip_archive,
                        processed_metadata_list,
                        translated_metadata_list=translation_metadata_list,
                        output_dir=cfg.download.cache_dir,
                        max_total_bytes=int(
                            cfg.message.archive.max_total_size_mb * 1024 * 1024
                        ),
                    )
                )
                (
                    archive_path,
                    archive_cancellation,
                ) = await self._wait_for_task_completion(archive_task)
                if archive_cancellation is not None:
                    await self._run_blocking_to_completion(
                        cleanup_zip_archive,
                        archive_path,
                    )
                    archive_path = ""
                    raise archive_cancellation
                await self.message_sender.send_zip_result(event, archive_path)
                archive_sent = True
                if cfg.admin.debug_mode:
                    self.logger.debug("ZIP归档发送完成")
                return

            if cfg.relay.enabled:
                for metadata in processed_metadata_list:
                    if not metadata.get("_enable_rich_media", True):
                        continue
                    await register_files_with_token_service(
                        metadata,
                        cfg.relay.callback_api_base,
                        cfg.relay.file_token_ttl,
                    )
                relay_registered = any(
                    bool(metadata.get("use_file_token_service"))
                    for metadata in processed_metadata_list
                )

            build_result = build_all_nodes(
                processed_metadata_list,
                cfg.download.large_video_threshold_mb,
                cfg.download.max_video_size_mb,
                True,
                True,
            )

            translation_nodes = await self._build_translation_nodes_after_task(
                translation_task,
                translation_metadata_list,
            )

            if not build_result.all_link_nodes and not any(translation_nodes):
                await event.send(
                    event.plain_result(
                        "解析完成，但没有可发送的内容，可能是下载失败或媒体不可访问。"
                    )
                )
                return

            text_metadata_image_path = await self._render_text_metadata_output(
                build_result,
                translation_nodes,
                cfg,
            )
            text_metadata_image_ref = text_metadata_image_path
            if text_metadata_image_path and cfg.relay.enabled:
                token_url = await register_file_with_token_service(
                    text_metadata_image_path,
                    cfg.relay.callback_api_base,
                    cfg.relay.file_token_ttl,
                )
                if token_url:
                    text_metadata_image_ref = token_url
                    relay_registered = True
            aggregatable_nodes = [
                meta["link_nodes"]
                for meta in build_result.link_metadata
                if meta.get("is_normal", True)
            ]
            aggregatable_nodes.extend(translation_nodes)
            node_counts = summarize_node_counts(aggregatable_nodes)
            should_aggregate_nodes = cfg.message.aggregation.should_aggregate_nodes(
                **node_counts
            )

            if cfg.admin.debug_mode:
                self.logger.debug(
                    f"开始发送结果，消息聚合模式: {cfg.message.aggregation.mode}, "
                    f"实际聚合: {should_aggregate_nodes}, "
                    f"图片节点: {node_counts['image_count']}, "
                    f"视频节点: {node_counts['video_count']}, "
                    f"总节点: {node_counts['node_count']}"
                )

            if should_aggregate_nodes:
                await self.message_sender.send_aggregated_results(
                    event,
                    build_result.link_metadata,
                    sender_name,
                    sender_id,
                    cfg.download.large_video_threshold_mb,
                    text_metadata_image=text_metadata_image_ref,
                )
            else:
                await self.message_sender.send_individual_results(
                    event,
                    build_result.all_link_nodes,
                    build_result.link_metadata,
                    quote_user_message=(cfg.message.text_metadata.quote_user_message),
                    quote_message_id=quote_source_message_id,
                    text_metadata_image=text_metadata_image_ref,
                )

            try:
                await self.message_sender.send_translation_results(
                    event,
                    translation_nodes,
                    should_aggregate_nodes=should_aggregate_nodes,
                    sender_name=sender_name,
                    sender_id=sender_id,
                )
            except MessageDeliveryError as exc:
                self.logger.warning(f"媒体已发送，但翻译结果发送失败: {exc}")
                try:
                    await event.send(
                        event.plain_result("媒体解析结果已发送，但翻译结果发送失败。")
                    )
                except Exception as notify_error:
                    self.logger.warning(f"发送翻译失败提示失败: {notify_error}")
            if cfg.admin.debug_mode:
                self.logger.debug("发送完成")
        except ArchiveSizeLimitError as exc:
            self.logger.warning(f"拒绝创建超出预算的ZIP归档: {exc}")
            try:
                await event.send(event.plain_result(f"无法创建ZIP：{exc}"))
            except Exception as notify_error:
                self.logger.warning(f"发送归档超限提示失败: {notify_error}")
        except Exception as exc:
            self.logger.exception(f"处理或发送解析结果失败: {exc}")
            try:
                await event.send(
                    event.plain_result("解析结果处理或发送失败，请稍后重试。")
                )
            except Exception as notify_error:
                self.logger.warning(f"发送失败提示失败: {notify_error}")
            raise
        finally:
            try:
                if archive_path:
                    if archive_sent:
                        # AstrBot 的 File 组件可能在 send 返回后才通过 Token
                        # 拉取本地文件，因此保留一个 TTL 后再清理。
                        self._schedule_delayed_cleanup(
                            [archive_path],
                            max(300, cfg.relay.file_token_ttl),
                        )
                    else:
                        await self._run_blocking_to_completion(
                            cleanup_zip_archive,
                            archive_path,
                        )

                build_temp_files = build_result.temp_files if build_result else []
                build_video_files = build_result.video_files if build_result else []
                all_files = self._unique_files(
                    self._collect_metadata_files(processed_metadata_list),
                    build_temp_files,
                    build_video_files,
                    [text_metadata_image_path] if text_metadata_image_path else [],
                )
                if all_files:
                    if relay_registered and not zip_requested:
                        delay = cfg.relay.file_token_ttl
                        self._schedule_delayed_cleanup(all_files, delay)
                    else:
                        await self._run_blocking_to_completion(
                            cleanup_files,
                            all_files,
                        )
            finally:
                self._active_media_flows = max(
                    0,
                    self._active_media_flows - 1,
                )

    async def _handle_clean_cache(self, event: AstrMessageEvent):
        cache_dir = self.download_manager.cache_dir
        if not cache_dir:
            await event.send(event.plain_result("未配置媒体文件缓存目录"))
            return

        busy = False
        cleanup_result = None
        cleanup_error = None
        async with self._cache_cleanup_lock:
            if self._active_media_flows:
                busy = True
            else:
                try:
                    cleanup_result = await self._run_blocking_to_completion(
                        cleanup_marked_in,
                        cache_dir,
                    )
                except Exception as exc:
                    cleanup_error = exc
                    logger.warning(f"管理员清理缓存失败: {exc}")

        if busy:
            await event.send(
                event.plain_result("当前仍有媒体正在处理，请稍后再清理缓存。")
            )
            return
        if cleanup_error is not None:
            await event.send(event.plain_result(f"清理失败: {cleanup_error}"))
            return

        subdirs_cleaned, files_cleaned, failed_subdirs = cleanup_result
        if failed_subdirs:
            msg = (
                "缓存清理部分完成: "
                f"已清理 {subdirs_cleaned} 个媒体子目录、{files_cleaned} 个文件，"
                f"{failed_subdirs} 个子目录失败；请检查日志和文件权限。"
            )
        else:
            msg = (
                f"缓存清理完成: {subdirs_cleaned} 个媒体子目录, {files_cleaned} 个文件"
            )
        await event.send(event.plain_result(msg))
        sender_id = str(event.get_sender_id() or "").strip()
        logger.info(
            f"管理员 {sender_id} 主动清理缓存: "
            f"{cache_dir}, {subdirs_cleaned} 个子目录, "
            f"{files_cleaned} 个文件, {failed_subdirs} 个失败"
        )

    # ── 主事件处理 ──────────────────────────────────────

    @filter.event_message_type(EventMessageType.ALL)
    async def auto_parse(self, event: AstrMessageEvent):
        self._start_expired_cache_cleanup()
        cfg = self.config_manager
        self.admin_cookie_assist.try_update_admin_origin(event)

        is_private = event.is_private_chat()
        sender_id = event.get_sender_id()
        group_id = None if is_private else event.get_group_id()

        self_id = str(event.get_self_id() or "").strip()
        if self_id and str(sender_id or "").strip() == self_id:
            return

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

        if await self.admin_cookie_assist.handle_admin_command(
            event, self.bilibili_auth_runtime
        ):
            return

        if not cfg.parser_output.has_any_output():
            if cfg.admin.debug_mode:
                self.logger.debug("文本元数据和富媒体均关闭，跳过解析")
            return

        card_urls = self._extract_urls_from_json_cards(event)
        if card_urls:
            if cfg.admin.debug_mode:
                self.logger.debug(f"[media_parser] 从JSON卡片提取到链接: {card_urls}")
            parse_text = "\n".join([original_message_text, *card_urls])

        zip_command = cfg.message.archive.command
        zip_requested = bool(
            zip_command and original_message_text.strip() == zip_command
        )
        if zip_requested:
            links_with_parser, reply_message_id = self._try_extract_reply_links(event)
            if reply_message_id:
                quote_source_message_id = reply_message_id
            links_with_parser = self._filter_links_by_output(links_with_parser)
            if not links_with_parser:
                await event.send(
                    event.plain_result("请引用包含可解析链接的消息后再发送归档命令。")
                )
                return
        else:
            links_with_parser = self.parser_manager.extract_all_links(parse_text)
            found_direct_links = bool(links_with_parser)
            if found_direct_links:
                links_with_parser = self._filter_links_by_output(links_with_parser)
                if not links_with_parser:
                    return

            if not links_with_parser:
                if cfg.trigger.reply_trigger and cfg.trigger.has_keyword(
                    original_message_text
                ):
                    links_with_parser, reply_message_id = self._try_extract_reply_links(
                        event
                    )
                    if reply_message_id:
                        quote_source_message_id = reply_message_id
                    links_with_parser = self._filter_links_by_output(links_with_parser)
                    if links_with_parser and cfg.admin.debug_mode:
                        self.logger.debug(
                            f"通过回复触发解析，提取到 {len(links_with_parser)} 个链接"
                        )
                if not links_with_parser:
                    await self.admin_cookie_assist.handle_admin_reply(
                        event, self.bilibili_auth_runtime
                    )
                    return

        if not zip_requested and not cfg.trigger.should_parse(original_message_text):
            return

        rate_limit_user_key = ParseRecordManager.build_user_key(
            event.get_platform_name(),
            sender_id,
        )
        if self.parse_record_manager.enabled:
            links_with_parser, blocked_links = await asyncio.to_thread(
                self.parse_record_manager.filter_links,
                links_with_parser,
                user_key=rate_limit_user_key,
            )
        else:
            blocked_links = []
        if blocked_links and cfg.admin.debug_mode:
            for blocked in blocked_links:
                self.logger.debug(
                    f"解析频率限制跳过链接: {blocked.link}, "
                    f"解析器={blocked.parser_name}, 原因={blocked.reason}"
                )
        if not links_with_parser:
            if cfg.admin.debug_mode:
                self.logger.debug("所有可解析链接均被解析频率限制拦截")
            if zip_requested:
                await event.send(
                    event.plain_result(
                        "引用消息中的链接当前触发了解析频率限制，请稍后再试。"
                    )
                )
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
                parse_text, session, links_with_parser=links_with_parser
            )
            if self.parse_record_manager.same_link.enabled:
                await asyncio.to_thread(
                    self.parse_record_manager.record_metadata_links,
                    metadata_list,
                )
            self._trigger_bilibili_cookie_assist_if_needed()
            if not metadata_list:
                if cfg.admin.debug_mode:
                    self.logger.debug("解析后未获得任何元数据")
                if zip_requested:
                    await event.send(
                        event.plain_result("引用消息中的链接未获得可归档解析结果。")
                    )
                return
            self._apply_output_flags(metadata_list)
            if zip_requested:
                # 归档是独立导出能力，不继承聊天展示的仅文本/仅媒体开关。
                for metadata in metadata_list:
                    metadata["_enable_text_metadata"] = True
                    metadata["_enable_rich_media"] = True
                    metadata["_text_metadata_fields"] = {
                        "title": True,
                        "author": True,
                        "timestamp": True,
                        "original_link": True,
                        "description": True,
                    }

            has_valid_metadata = any(
                self._metadata_has_output_candidate(metadata)
                for metadata in metadata_list
            )

            if not has_valid_metadata:
                if cfg.admin.debug_mode:
                    self.logger.debug(
                        "解析后未获得任何有效元数据（可能是直播链接或解析失败）"
                    )
                if zip_requested:
                    await event.send(
                        event.plain_result("引用消息中的链接没有可归档内容。")
                    )
                return

            translation_task, translation_metadata_list = self._start_translation_task(
                metadata_list, event
            )

            try:
                await self._process_and_send_metadata(
                    event=event,
                    session=session,
                    metadata_list=metadata_list,
                    cfg=cfg,
                    sender_name=sender_name,
                    sender_id=sender_id,
                    quote_source_message_id=quote_source_message_id,
                    zip_requested=zip_requested,
                    translation_task=translation_task,
                    translation_metadata_list=translation_metadata_list,
                )
            finally:
                await self._cancel_translation_task(translation_task)
