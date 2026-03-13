import asyncio
import json
from typing import Any, Dict, Optional

import aiohttp

from .core.logger import logger

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.event_message_type import EventMessageType

from .core.parser import ParserManager
from .core.downloader import DownloadManager
from .core.file_cleaner import cleanup_files, cleanup_directory
from .core.constants import Config
from .core.message_adapter.sender import MessageSender
from .core.message_adapter.node_builder import build_all_nodes
from .core.config_manager import ConfigManager
from .core.interaction.platform.bilibili import BilibiliAdminCookieAssistManager


@register(
    "astrbot_plugin_media_parser",
    "drdon1234",
    "聚合解析流媒体平台链接，转换为媒体直链发送",
    "0.5.0"
)
class VideoParserPlugin(Star):

    def __init__(self, context: Context, config: dict):
        """初始化插件"""
        super().__init__(context)
        self.logger = logger
        
        self.config_manager = ConfigManager(config)
        
        parsers = self.config_manager.create_parsers()
        self.parser_manager = ParserManager(parsers)
        self.bilibili_parser = self.config_manager.bilibili_parser
        self.bilibili_auth_runtime = (
            self.bilibili_parser.get_auth_runtime()
            if self.bilibili_parser else
            None
        )
        
        self.download_manager = DownloadManager(
            max_video_size_mb=self.config_manager.max_video_size_mb,
            large_video_threshold_mb=self.config_manager.large_video_threshold_mb,
            cache_dir=self.config_manager.cache_dir,
            pre_download_all_media=self.config_manager.pre_download_all_media,
            max_concurrent_downloads=self.config_manager.max_concurrent_downloads
        )
        
        self.message_sender = MessageSender()
        self.admin_cookie_assist = BilibiliAdminCookieAssistManager(
            context=self.context,
            admin_id=self.config_manager.admin_id,
            enabled=(
                self.config_manager.bilibili_cookie_runtime_enabled and
                self.config_manager.bilibili_enable_admin_assist_on_expire
            ),
            reply_timeout_minutes=self.config_manager.bilibili_admin_reply_timeout_minutes,
            request_cooldown_minutes=self.config_manager.bilibili_admin_request_cooldown_minutes
        )

    async def terminate(self):
        """插件终止时的清理工作"""
        await self.admin_cookie_assist.shutdown()
        await self.download_manager.shutdown()
        
        if self.download_manager.cache_dir:
            cleanup_directory(self.download_manager.cache_dir)

    def _trigger_bilibili_cookie_assist_if_needed(self):
        if not self.bilibili_parser:
            return
        reason = self.bilibili_parser.consume_assist_request()
        if not reason:
            return
        self.admin_cookie_assist.trigger_assist_request(reason)

    def _check_permission(self, is_private: bool, sender_id: Any, group_id: Any) -> bool:
        """检查用户或群组是否有权限使用解析"""
        admin_id = self.config_manager.admin_id
        sender_id_str = str(sender_id or "").strip()
        group_id_str = "" if is_private else str(group_id or "").strip()

        if admin_id and sender_id_str == str(admin_id):
            return True

        w_enable = self.config_manager.whitelist_enable
        w_user = self.config_manager.whitelist_user
        w_group = self.config_manager.whitelist_group
        b_enable = self.config_manager.blacklist_enable
        b_user = self.config_manager.blacklist_user
        b_group = self.config_manager.blacklist_group

        allowed = None
        if w_enable and sender_id_str in w_user:
            allowed = True
        elif b_enable and sender_id_str in b_user:
            allowed = False
        elif w_enable and group_id_str and group_id_str in w_group:
            allowed = True
        elif b_enable and group_id_str and group_id_str in b_group:
            allowed = False
            
        if allowed is None:
            allowed = not w_enable

        return allowed
        
    def _extract_url_from_json_card(self, event: AstrMessageEvent) -> Optional[str]:
        """尝试从QQ结构化卡片消息中提取URL"""
        try:
            messages = event.get_messages()
            if not messages:
                return None
            first_msg = messages[0]
            msg_data = first_msg.data
            curl_link = None
    
            if isinstance(msg_data, dict) and not msg_data.get('data'):
                meta = msg_data.get("meta") or {}
                detail_1 = meta.get("detail_1") or {}
                curl_link = detail_1.get("qqdocurl")
                if not curl_link:
                    news = meta.get("news") or {}
                    curl_link = news.get("jumpUrl")
    
            if not curl_link:
                json_str = msg_data.get('data', '') if isinstance(msg_data, dict) else msg_data
                if json_str and isinstance(json_str, str):
                    message_data = json.loads(json_str)
                    meta = message_data.get("meta") or {}
                    detail_1 = meta.get("detail_1") or {}
                    curl_link = detail_1.get("qqdocurl")
                    if not curl_link:
                        news = meta.get("news") or {}
                        curl_link = news.get("jumpUrl")
                        
            return curl_link
        except (AttributeError, KeyError, json.JSONDecodeError, IndexError, TypeError) as e:
            if self.config_manager.debug_mode:
                self.logger.debug(f"提取JSON卡片链接失败: {e}")
            return None

    def _should_parse(self, message_str: str) -> bool:
        """判断是否应该解析消息"""
        if self.config_manager.is_auto_parse:
            return True
        for keyword in self.config_manager.trigger_keywords:
            if keyword in message_str:
                return True
        return False


    @filter.event_message_type(EventMessageType.ALL)
    async def auto_parse(self, event: AstrMessageEvent):
        """自动解析消息中的视频链接"""
        self.admin_cookie_assist.try_update_admin_origin(event)

        is_private = event.is_private_chat()
        sender_id = event.get_sender_id()
        group_id = None if is_private else event.get_group_id()

        if not self._check_permission(is_private, sender_id, group_id):
            return

        message_text = event.message_str
        card_url = self._extract_url_from_json_card(event)
        
        if card_url:
            if self.config_manager.debug_mode:
                self.logger.debug(f"[media_parser] 从JSON卡片提取到链接: {card_url}")
            message_text = card_url
        
        links_with_parser = self.parser_manager.extract_all_links(message_text)

        if not links_with_parser:
            await self.admin_cookie_assist.handle_admin_reply(
                event,
                self.bilibili_auth_runtime
            )
            return
        
        if not self._should_parse(message_text):
            return
        
        if self.config_manager.debug_mode:
            self.logger.debug(f"提取到 {len(links_with_parser)} 个可解析链接: {[link for link, _ in links_with_parser]}")
        
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
                if self.config_manager.debug_mode:
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
                if self.config_manager.debug_mode:
                    self.logger.debug("解析后未获得任何有效元数据（可能是直播链接或解析失败）")
                return
            
            if self.config_manager.enable_opening_msg:
                msg_text = self.config_manager.opening_msg_content if self.config_manager.opening_msg_content else "流媒体解析bot为您服务 ٩( 'ω' )و"
                await event.send(event.plain_result(msg_text))
            
            if self.config_manager.debug_mode:
                self.logger.debug(f"解析获得 {len(metadata_list)} 条元数据")
                for idx, metadata in enumerate(metadata_list):
                    self.logger.debug(
                        f"元数据[{idx}]: url={metadata.get('url')}, "
                        f"video_count={len(metadata.get('video_urls', []))}, "
                        f"image_count={len(metadata.get('image_urls', []))}, "
                        f"video_force_download={metadata.get('video_force_download')}"
                    )
            
            async def process_single_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
                if metadata.get('error'):
                    return metadata
                
                try:
                    processed_metadata = await self.download_manager.process_metadata(
                        session,
                        metadata,
                        proxy_addr=self.config_manager.proxy_addr
                    )
                    return processed_metadata
                except Exception as e:
                    self.logger.exception(f"处理元数据失败: {metadata.get('url', '')}, 错误: {e}")
                    metadata['error'] = str(e)
                    return metadata
            
            tasks = [process_single_metadata(metadata) for metadata in metadata_list]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            processed_metadata_list = []
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    metadata = metadata_list[i] if i < len(metadata_list) else {}
                    error_msg = str(result)
                    self.logger.exception(
                        f"处理元数据时发生未捕获的异常: {metadata.get('url', '未知URL')}, "
                        f"错误类型: {type(result).__name__}, 错误: {error_msg}"
                    )
                    metadata['error'] = error_msg
                    processed_metadata_list.append(metadata)
                elif isinstance(result, dict):
                    processed_metadata_list.append(result)
                else:
                    metadata = metadata_list[i] if i < len(metadata_list) else {}
                    error_msg = f'未知错误类型: {type(result).__name__}'
                    self.logger.warning(
                        f"处理元数据返回了意外的结果类型: {metadata.get('url', '未知URL')}, "
                        f"类型: {type(result).__name__}"
                    )
                    metadata['error'] = error_msg
                    processed_metadata_list.append(metadata)
            
            temp_files = []
            video_files = []
            try:
                all_link_nodes, link_metadata, temp_files, video_files = build_all_nodes(
                    processed_metadata_list,
                    self.config_manager.is_auto_pack,
                    self.config_manager.large_video_threshold_mb,
                    self.config_manager.max_video_size_mb,
                    self.config_manager.enable_text_metadata
                )
                
                if self.config_manager.debug_mode:
                    self.logger.debug(
                        f"节点构建完成: {len(all_link_nodes)} 个链接节点, "
                        f"{len(temp_files)} 个临时文件, {len(video_files)} 个视频文件"
                    )
                
                if not all_link_nodes:
                    if self.config_manager.debug_mode:
                        self.logger.debug("未构建任何节点，跳过发送")
                    return
                
                if self.config_manager.debug_mode:
                    self.logger.debug(f"开始发送结果，打包模式: {self.config_manager.is_auto_pack}")
                
                if self.config_manager.is_auto_pack:
                    await self.message_sender.send_packed_results(
                        event,
                        link_metadata,
                        sender_name,
                        sender_id,
                        self.config_manager.large_video_threshold_mb
                    )
                else:
                    await self.message_sender.send_unpacked_results(
                        event,
                        all_link_nodes,
                        link_metadata
                    )

                if self.config_manager.debug_mode:
                    self.logger.debug("发送完成")
            except Exception as e:
                self.logger.exception(
                    f"构建节点或发送消息失败: {e}, "
                    f"临时文件数: {len(temp_files)}, 视频文件数: {len(video_files)}"
                )
                raise
            finally:
                if temp_files or video_files:
                    cleanup_files(temp_files + video_files)
                    if self.config_manager.debug_mode:
                        self.logger.debug(f"已清理临时文件: {len(temp_files)} 个, 视频文件: {len(video_files)} 个")
