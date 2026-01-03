import asyncio
import json
from typing import Any, Dict

import aiohttp

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.event_message_type import EventMessageType

from .core.parser import ParserManager
from .core.downloader import DownloadManager
from .core.file_cleaner import cleanup_files, cleanup_directory
from .core.constants import Config
from .core.message_adapter import MessageManager
from .core.config_manager import ConfigManager


@register(
    "astrbot_plugin_media_parser",
    "drdon1234",
    "聚合解析流媒体平台链接，转换为媒体直链发送",
    "4.2.0"
)
class VideoParserPlugin(Star):

    def __init__(self, context: Context, config: dict):
        """初始化插件

        Args:
            context: 上下文对象
            config: 配置字典

        Raises:
            ValueError: 没有启用任何解析器时
        """
        super().__init__(context)
        self.logger = logger
        
        self.config_manager = ConfigManager(config)
        
        self.is_auto_pack = self.config_manager.is_auto_pack
        self.is_auto_parse = self.config_manager.is_auto_parse
        self.trigger_keywords = self.config_manager.trigger_keywords
        self.max_video_size_mb = self.config_manager.max_video_size_mb
        self.large_video_threshold_mb = self.config_manager.large_video_threshold_mb
        self.debug_mode = self.config_manager.debug_mode
        self.whitelist = {
            "enable": self.config_manager.whitelist_enable,
            "user": self.config_manager.whitelist_user,
            "group": self.config_manager.whitelist_group
        }
        
        parsers = self.config_manager.create_parsers()
        self.parser_manager = ParserManager(parsers)
        
        self.download_manager = DownloadManager(
            max_video_size_mb=self.max_video_size_mb,
            large_video_threshold_mb=self.large_video_threshold_mb,
            cache_dir=self.config_manager.cache_dir,
            pre_download_all_media=self.config_manager.pre_download_all_media,
            max_concurrent_downloads=self.config_manager.max_concurrent_downloads
        )
        
        self.proxy_addr = self.config_manager.proxy_addr
        
        self.message_manager = MessageManager(logger=self.logger)

    async def terminate(self):
        """插件终止时的清理工作"""
        await self.download_manager.shutdown()
        
        if self.download_manager.cache_dir:
            cleanup_directory(self.download_manager.cache_dir)

    def _should_parse(self, message_str: str) -> bool:
        """判断是否应该解析消息

        Args:
            message_str: 消息文本

        Returns:
            是否应该解析
        """
        if self.is_auto_parse:
            return True
        for keyword in self.trigger_keywords:
            if keyword in message_str:
                return True
        return False


    @filter.event_message_type(EventMessageType.ALL)
    async def auto_parse(self, event: AstrMessageEvent):
        """自动解析消息中的视频链接

        Args:
            event: 消息事件对象
        """
        # 白名单检查
        is_private = event.is_private_chat()
        sender_id = event.get_sender_id()
        group_id = None if is_private else event.get_group_id()

        if not self.whitelist["enable"]:
            allowed = True
        elif sender_id in self.whitelist["user"]:
            allowed = True
        elif not is_private and group_id in self.whitelist["group"]:
            allowed = True
        else:
            allowed = False

        if not allowed:
            return

        message_text = event.message_str
        try:
            messages = event.get_messages()
            if messages and len(messages) > 0:
                message_data = json.loads(messages[0].data)
                meta = message_data.get("meta") or {}
                detail_1 = meta.get("detail_1") or {}
                curl_link = detail_1.get("qqdocurl")
                if not curl_link:
                    news = meta.get("news") or {}
                    curl_link = news.get("jumpUrl")
                if curl_link:
                    message_text = curl_link
        except (AttributeError, KeyError, json.JSONDecodeError, IndexError, TypeError):
            pass
        
        if not self._should_parse(message_text):
            return
        
        links_with_parser = self.parser_manager.extract_all_links(
            message_text
        )
        if not links_with_parser:
            return
        
        if self.debug_mode:
            self.logger.debug(f"提取到 {len(links_with_parser)} 个可解析链接: {[link for link, _ in links_with_parser]}")
        
        sender_name, sender_id = self.message_manager.get_sender_info(event)
        
        timeout = aiohttp.ClientTimeout(total=Config.DEFAULT_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            metadata_list = await self.parser_manager.parse_text(
                message_text,
                session
            )
            if not metadata_list:
                if self.debug_mode:
                    self.logger.debug("解析后未获得任何元数据")
                return
            
            has_valid_metadata = any(
                not metadata.get('error') and 
                (bool(metadata.get('video_urls')) or bool(metadata.get('image_urls')))
                for metadata in metadata_list
            )
            
            if not has_valid_metadata:
                if self.debug_mode:
                    self.logger.debug("解析后未获得任何有效元数据（可能是直播链接或解析失败）")
                return
            
            await event.send(event.plain_result("流媒体解析bot为您服务 ٩( 'ω' )و"))
            
            if self.debug_mode:
                self.logger.debug(f"解析获得 {len(metadata_list)} 条元数据")
                for idx, metadata in enumerate(metadata_list):
                    self.logger.debug(
                        f"元数据[{idx}]: url={metadata.get('url')}, "
                        f"video_count={len(metadata.get('video_urls', []))}, "
                        f"image_count={len(metadata.get('image_urls', []))}, "
                        f"video_force_download={metadata.get('video_force_download')}"
                    )
            
            async def process_single_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
                """处理单个元数据

                Args:
                    metadata: 元数据字典

                Returns:
                    处理后的元数据字典，异常时包含error字段
                """
                if metadata.get('error'):
                    return metadata
                
                try:
                    processed_metadata = await self.download_manager.process_metadata(
                        session,
                        metadata,
                        proxy_addr=self.proxy_addr
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
                all_link_nodes, link_metadata, temp_files, video_files = self.message_manager.build_nodes(
                    processed_metadata_list,
                    self.is_auto_pack,
                    self.large_video_threshold_mb,
                    self.max_video_size_mb
                )
                
                if self.debug_mode:
                    self.logger.debug(
                        f"节点构建完成: {len(all_link_nodes)} 个链接节点, "
                        f"{len(temp_files)} 个临时文件, {len(video_files)} 个视频文件"
                    )
                
                if not all_link_nodes:
                    if self.debug_mode:
                        self.logger.debug("未构建任何节点，跳过发送")
                    return
                
                if self.debug_mode:
                    self.logger.debug(f"开始发送结果，打包模式: {self.is_auto_pack}")
                await self.message_manager.send_results(
                    event,
                    all_link_nodes,
                    link_metadata,
                    sender_name,
                    sender_id,
                    self.is_auto_pack,
                    self.large_video_threshold_mb
                )
                if self.debug_mode:
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
                    if self.debug_mode:
                        self.logger.debug(f"已清理临时文件: {len(temp_files)} 个, 视频文件: {len(video_files)} 个")
