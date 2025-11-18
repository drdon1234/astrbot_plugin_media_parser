# -*- coding: utf-8 -*-
import asyncio
import json
from typing import Any, Dict

import aiohttp

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Nodes, Plain, Image, Video, Node
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.event_message_type import EventMessageType

from .core.parser_manager import ParserManager
from .core.download_manager import DownloadManager
from .core.node_builder import build_all_nodes, is_pure_image_gallery
from .core.file_manager import cleanup_files
from .core.constants import Config
from .parsers import (
    BilibiliParser,
    DouyinParser,
    KuaishouParser,
    WeiboParser,
    XiaohongshuParser,
    TwitterParser
)


@register(
    "astrbot_plugin_media_parser",
    "drdon1234",
    "聚合解析流媒体平台链接，转换为媒体直链发送",
    "3.2.0"
)
class VideoParserPlugin(Star):

    def __init__(self, context: Context, config: dict):
        """初始化插件

        Args:
            context: 上下文对象
            config: 配置字典

        Raises:
            ValueError: 当没有启用任何解析器时
        """
        super().__init__(context)
        self.logger = logger
        self.is_auto_pack = config.get("is_auto_pack", True)
        trigger_settings = config.get("trigger_settings", {})
        self.is_auto_parse = trigger_settings.get("is_auto_parse", True)
        self.trigger_keywords = trigger_settings.get(
            "trigger_keywords",
            ["视频解析", "解析视频"]
        )
        video_size_settings = config.get("video_size_settings", {})
        max_video_size_mb = video_size_settings.get("max_video_size_mb", 0.0)
        large_video_threshold_mb = video_size_settings.get(
            "large_video_threshold_mb",
            Config.MAX_LARGE_VIDEO_THRESHOLD_MB
        )
        if large_video_threshold_mb > 0:
            large_video_threshold_mb = min(
                large_video_threshold_mb,
                Config.MAX_LARGE_VIDEO_THRESHOLD_MB
            )
        self.max_video_size_mb = max_video_size_mb
        self.large_video_threshold_mb = large_video_threshold_mb
        download_settings = config.get("download_settings", {})
        cache_dir = download_settings.get(
            "cache_dir",
            "/app/sharedFolder/video_parser/cache"
        )
        pre_download_all_media = download_settings.get(
            "pre_download_all_media",
            False
        )
        max_concurrent_downloads = download_settings.get(
            "max_concurrent_downloads",
            3
        )
        parser_enable_settings = config.get("parser_enable_settings", {})
        enable_bilibili = parser_enable_settings.get("enable_bilibili", True)
        enable_douyin = parser_enable_settings.get("enable_douyin", True)
        enable_kuaishou = parser_enable_settings.get(
            "enable_kuaishou",
            True
        )
        enable_weibo = parser_enable_settings.get(
            "enable_weibo",
            True
        )
        enable_xiaohongshu = parser_enable_settings.get(
            "enable_xiaohongshu",
            True
        )
        enable_twitter = parser_enable_settings.get("enable_twitter", True)
        twitter_proxy_settings = config.get("twitter_proxy_settings", {})
        use_image_proxy = twitter_proxy_settings.get(
            "twitter_use_image_proxy",
            False
        )
        use_video_proxy = twitter_proxy_settings.get(
            "twitter_use_video_proxy",
            False
        )
        proxy_url = twitter_proxy_settings.get("twitter_proxy_url", "")
        
        parsers = []
        if enable_bilibili:
            parsers.append(BilibiliParser())
        if enable_douyin:
            parsers.append(DouyinParser())
        if enable_kuaishou:
            parsers.append(KuaishouParser())
        if enable_weibo:
            parsers.append(WeiboParser())
        if enable_xiaohongshu:
            parsers.append(XiaohongshuParser())
        if enable_twitter:
            parsers.append(TwitterParser(
                use_image_proxy=use_image_proxy,
                use_video_proxy=use_video_proxy,
                proxy_url=proxy_url
            ))
        if not parsers:
            raise ValueError(
                "至少需要启用一个视频解析器。"
                "请检查配置中的 parser_enable_settings 设置。"
            )
        
        self.parser_manager = ParserManager(parsers)
        
        self.download_manager = DownloadManager(
            max_video_size_mb=max_video_size_mb,
            large_video_threshold_mb=large_video_threshold_mb,
            cache_dir=cache_dir,
            pre_download_all_media=pre_download_all_media,
            max_concurrent_downloads=max_concurrent_downloads
        )
        
        self.twitter_proxy_url = proxy_url if (use_image_proxy or use_video_proxy) else None

    async def terminate(self):
        """插件终止时的清理工作"""
        pass

    def _should_parse(self, message_str: str) -> bool:
        """判断是否应该解析消息

        Args:
            message_str: 消息文本

        Returns:
            如果应该解析返回True，否则返回False
        """
        if self.is_auto_parse:
            return True
        for keyword in self.trigger_keywords:
            if keyword in message_str:
                return True
        return False

    def _get_sender_info(self, event: AstrMessageEvent) -> tuple:
        """获取发送者信息

        Args:
            event: 消息事件对象

        Returns:
            包含发送者名称和ID的元组 (sender_name, sender_id)
        """
        sender_name = "视频解析bot"
        platform = event.get_platform_name()
        sender_id = event.get_self_id()
        if platform not in ("wechatpadpro", "webchat", "gewechat"):
            try:
                sender_id = int(sender_id)
            except (ValueError, TypeError):
                sender_id = 10000
        return sender_name, sender_id

    def _get_bilibili_headers(self, url: str, metadata: Dict[str, Any]) -> tuple:
        """获取B站请求头和Referer

        Args:
            url: URL
            metadata: 元数据字典

        Returns:
            (headers, referer) 元组
        """
        page_url = metadata.get('page_url', url)
        referer_url = page_url if page_url else url
        headers = {
            "User-Agent": Config.USER_AGENT_DESKTOP,
            "Referer": referer_url,
            "Origin": "https://www.bilibili.com"
        }
        return headers, referer_url

    def _get_douyin_headers(self, url: str) -> tuple:
        """获取抖音请求头和Referer

        Args:
            url: URL

        Returns:
            (headers, referer) 元组
        """
        headers = {
            'User-Agent': Config.USER_AGENT_MOBILE,
            'Referer': 'https://www.douyin.com/'
        }
        return headers, url

    def _get_weibo_headers(self, url: str) -> tuple:
        """获取微博请求头和Referer

        Args:
            url: URL

        Returns:
            (headers, referer) 元组
        """
        headers = {
            'User-Agent': Config.USER_AGENT_DESKTOP,
            'Referer': 'https://weibo.com/'
        }
        return headers, url

    def _get_xiaohongshu_headers(self, url: str, metadata: Dict[str, Any]) -> tuple:
        """获取小红书请求头和Referer

        Args:
            url: URL
            metadata: 元数据字典

        Returns:
            (headers, referer) 元组
        """
        page_url = metadata.get('page_url', url)
        headers = {
            'User-Agent': Config.USER_AGENT_DESKTOP,
            'Referer': page_url if page_url else 'https://www.xiaohongshu.com/'
        }
        referer = page_url if page_url else url
        return headers, referer

    def _get_twitter_headers(self, url: str) -> tuple:
        """获取Twitter请求头和Referer

        Args:
            url: URL

        Returns:
            (headers, referer, proxy) 元组
        """
        proxy = None
        if self.twitter_proxy_url:
            proxy = self.twitter_proxy_url
        headers = {
            'User-Agent': Config.USER_AGENT_DESKTOP
        }
        return headers, url, proxy

    def _get_headers_and_referer(
        self,
        metadata: Dict[str, Any]
    ) -> tuple:
        """根据元数据获取请求头和Referer

        Args:
            metadata: 元数据字典

        Returns:
            包含(headers, referer, proxy)的元组
        """
        url = metadata.get('url', '')
        headers = None
        referer = url
        proxy = None
        
        if 'bilibili.com' in url or 'b23.tv' in url:
            headers, referer = self._get_bilibili_headers(url, metadata)
        elif 'douyin.com' in url:
            headers, referer = self._get_douyin_headers(url)
        elif 'weibo.com' in url or 'weibo.cn' in url:
            headers, referer = self._get_weibo_headers(url)
        elif 'xiaohongshu.com' in url or 'xhslink.com' in url:
            headers, referer = self._get_xiaohongshu_headers(url, metadata)
        elif 'twitter.com' in url or 'x.com' in url:
            result = self._get_twitter_headers(url)
            if len(result) == 3:
                headers, referer, proxy = result
            else:
                headers, referer = result
        
        return headers, referer, proxy

    async def _send_packed_results(
        self,
        event: AstrMessageEvent,
        link_metadata: list,
        sender_name: str,
        sender_id: Any
    ):
        """发送打包的结果（使用Nodes）

        Args:
            event: 消息事件对象
            link_metadata: 链接元数据列表
            sender_name: 发送者名称
            sender_id: 发送者ID
        """
        normal_metadata = [
            meta for meta in link_metadata if meta.get('is_normal', True)
        ]
        large_media_metadata = [
            meta for meta in link_metadata if meta.get('is_large_media', False)
        ]
        normal_link_nodes = [
            meta['link_nodes'] for meta in normal_metadata
        ]
        large_media_link_nodes = [
            meta['link_nodes'] for meta in large_media_metadata
        ]
        separator = "-------------------------------------"

        if normal_link_nodes:
            flat_nodes = []
            normal_video_files_to_cleanup = []
            for link_idx, link_nodes in enumerate(normal_link_nodes):
                if link_idx < len(normal_metadata):
                    link_video_files = normal_metadata[link_idx].get(
                        'video_files',
                        []
                    )
                    if link_video_files:
                        normal_video_files_to_cleanup.extend(
                            link_video_files
                        )
                if is_pure_image_gallery(link_nodes):
                    texts = [
                        node for node in link_nodes
                        if isinstance(node, Plain)
                    ]
                    images = [
                        node for node in link_nodes
                        if isinstance(node, Image)
                    ]
                    for text in texts:
                        flat_nodes.append(Node(
                            name=sender_name,
                            uin=sender_id,
                            content=[text]
                        ))
                    if images:
                        flat_nodes.append(Node(
                            name=sender_name,
                            uin=sender_id,
                            content=images
                        ))
                else:
                    for node in link_nodes:
                        if node is not None:
                            flat_nodes.append(Node(
                                name=sender_name,
                                uin=sender_id,
                                content=[node]
                            ))
                if link_idx < len(normal_link_nodes) - 1:
                    flat_nodes.append(Node(
                        name=sender_name,
                        uin=sender_id,
                        content=[Plain(separator)]
                    ))
            if flat_nodes:
                try:
                    await event.send(event.chain_result([Nodes(flat_nodes)]))
                finally:
                    if normal_video_files_to_cleanup:
                        cleanup_files(normal_video_files_to_cleanup)

        if large_media_link_nodes:
            await self._send_large_media_results(
                event,
                large_media_metadata,
                large_media_link_nodes,
                sender_name,
                sender_id
            )

    async def _send_large_media_results(
        self,
        event: AstrMessageEvent,
        metadata: list,
        link_nodes_list: list,
        sender_name: str,
        sender_id: Any
    ):
        """发送大媒体结果（单独发送）

        Args:
            event: 消息事件对象
            metadata: 元数据列表
            link_nodes_list: 链接节点列表
            sender_name: 发送者名称
            sender_id: 发送者ID
        """
        separator = "-------------------------------------"
        threshold_mb = (
            int(self.large_video_threshold_mb)
            if self.large_video_threshold_mb > 0
            else 50
        )
        notice_text = (
            f"⚠️ 链接中包含超过{threshold_mb}MB的视频时"
            f"将单独发送所有媒体"
        )
        await event.send(event.plain_result(notice_text))
        for link_idx, link_nodes in enumerate(link_nodes_list):
            link_video_files = []
            if link_idx < len(metadata):
                link_video_files = metadata[link_idx].get('video_files', [])
            try:
                for node in link_nodes:
                    if node is not None:
                        try:
                            await event.send(event.chain_result([node]))
                        except Exception as e:
                            self.logger.warning(f"发送大媒体节点失败: {e}")
            finally:
                if link_video_files:
                    cleanup_files(link_video_files)
            if link_idx < len(link_nodes_list) - 1:
                await event.send(event.plain_result(separator))

    async def _send_unpacked_results(
        self,
        event: AstrMessageEvent,
        all_link_nodes: list,
        link_metadata: list
    ):
        """发送非打包的结果（独立发送）

        Args:
            event: 消息事件对象
            all_link_nodes: 所有链接节点列表
            link_metadata: 链接元数据列表
        """
        separator = "-------------------------------------"
        for link_idx, (link_nodes, metadata) in enumerate(
            zip(all_link_nodes, link_metadata)
        ):
            link_video_files = metadata.get('video_files', [])
            try:
                if is_pure_image_gallery(link_nodes):
                    texts = [
                        node for node in link_nodes
                        if isinstance(node, Plain)
                    ]
                    images = [
                        node for node in link_nodes
                        if isinstance(node, Image)
                    ]
                    for text in texts:
                        await event.send(event.chain_result([text]))
                    if images:
                        await event.send(event.chain_result(images))
                else:
                    for node in link_nodes:
                        if node is not None:
                            try:
                                await event.send(event.chain_result([node]))
                            except Exception as e:
                                self.logger.warning(f"发送节点失败: {e}")
            finally:
                if link_video_files:
                    cleanup_files(link_video_files)
            if link_idx < len(all_link_nodes) - 1:
                await event.send(event.plain_result(separator))

    @filter.event_message_type(EventMessageType.ALL)
    async def auto_parse(self, event: AstrMessageEvent):
        """自动解析消息中的视频链接

        Args:
            event: 消息事件对象
        """
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
        
        await event.send(event.plain_result("流媒体解析bot为您服务 ٩( 'ω' )و"))
        sender_name, sender_id = self._get_sender_info(event)
        
        timeout = aiohttp.ClientTimeout(total=Config.DEFAULT_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            metadata_list = await self.parser_manager.parse_text(
                message_text,
                session
            )
            if not metadata_list:
                return
            
            async def process_single_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
                """处理单个元数据

                Args:
                    metadata: 元数据字典

                Returns:
                    处理后的元数据字典
                """
                if metadata.get('error'):
                    return metadata
                
                headers, referer, proxy = self._get_headers_and_referer(metadata)
                
                try:
                    processed_metadata = await self.download_manager.process_metadata(
                        session,
                        metadata,
                        headers,
                        referer,
                        proxy
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
                    self.logger.exception(f"处理元数据失败: {metadata.get('url', '')}, 错误: {result}")
                    metadata['error'] = str(result)
                    processed_metadata_list.append(metadata)
                elif isinstance(result, dict):
                    processed_metadata_list.append(result)
                else:
                    metadata = metadata_list[i] if i < len(metadata_list) else {}
                    metadata['error'] = 'Unknown error'
                    processed_metadata_list.append(metadata)
            
            all_link_nodes, link_metadata, temp_files, video_files = build_all_nodes(
                processed_metadata_list,
                self.is_auto_pack,
                sender_name,
                sender_id,
                self.large_video_threshold_mb,
                self.max_video_size_mb
            )
            
            if not all_link_nodes:
                cleanup_files(temp_files + video_files)
                return
            
            try:
                if self.is_auto_pack:
                    await self._send_packed_results(
                        event,
                        link_metadata,
                        sender_name,
                        sender_id
                    )
                else:
                    await self._send_unpacked_results(
                        event,
                        all_link_nodes,
                        link_metadata
                    )
                cleanup_files(temp_files + video_files)
            except Exception as e:
                self.logger.exception(f"auto_parse方法执行失败: {e}")
                cleanup_files(temp_files + video_files)
                raise
