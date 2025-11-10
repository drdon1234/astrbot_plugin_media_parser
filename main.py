# -*- coding: utf-8 -*-
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.message_components import Nodes, Plain, Image, Video
from astrbot.core.star.filter.event_message_type import EventMessageType
from .parser_manager import ParserManager
from .parsers import BilibiliParser, DouyinParser, TwitterParser, KuaishouParser
import re
import os


@register("astrbot_plugin_video_parser", "drdon1234", "聚合解析流媒体平台链接，转换为媒体直链发送", "0.1.0")
class VideoParserPlugin(Star):

    def __init__(self, context: Context, config: dict):
        """
        初始化插件
        Args:
            context: 上下文对象
            config: 配置字典
        """
        super().__init__(context)
        self.is_auto_pack = config.get("is_auto_pack", True)
        trigger_settings = config.get("trigger_settings", {})
        self.is_auto_parse = trigger_settings.get("is_auto_parse", True)
        self.trigger_keywords = trigger_settings.get("trigger_keywords", ["视频解析", "解析视频"])
        media_size_settings = config.get("media_size_settings", {})
        max_media_size_mb = media_size_settings.get("max_media_size_mb", 0.0)
        large_media_threshold_mb = media_size_settings.get("large_media_threshold_mb", 100.0)
        if large_media_threshold_mb > 0:
            large_media_threshold_mb = min(large_media_threshold_mb, 100.0)
        self.large_media_threshold_mb = large_media_threshold_mb
        download_settings = config.get("download_settings", {})
        cache_dir = download_settings.get("cache_dir", "/app/sharedFolder/video_parser/cache")
        pre_download_all_media = download_settings.get("pre_download_all_media", False)
        max_concurrent_downloads = download_settings.get("max_concurrent_downloads", 3)
        self.cache_dir_available = self._check_cache_dir_available(cache_dir)
        if self.cache_dir_available:
            os.makedirs(cache_dir, exist_ok=True)
        parser_enable_settings = config.get("parser_enable_settings", {})
        enable_bilibili = parser_enable_settings.get("enable_bilibili", True)
        enable_douyin = parser_enable_settings.get("enable_douyin", True)
        enable_twitter = parser_enable_settings.get("enable_twitter", True)
        enable_kuaishou = parser_enable_settings.get("enable_kuaishou", True)
        twitter_proxy_settings = config.get("twitter_proxy_settings", {})
        use_image_proxy = twitter_proxy_settings.get("twitter_use_image_proxy", False)
        use_video_proxy = twitter_proxy_settings.get("twitter_use_video_proxy", False)
        proxy_url = twitter_proxy_settings.get("twitter_proxy_url", "")
        parsers = []
        if enable_bilibili:
            parsers.append(BilibiliParser(
                max_media_size_mb=max_media_size_mb,
                large_media_threshold_mb=large_media_threshold_mb,
                cache_dir=cache_dir,
                pre_download_all_media=pre_download_all_media,
                max_concurrent_downloads=max_concurrent_downloads
            ))
        if enable_douyin:
            parsers.append(DouyinParser(
                max_media_size_mb=max_media_size_mb,
                large_media_threshold_mb=large_media_threshold_mb,
                cache_dir=cache_dir,
                pre_download_all_media=pre_download_all_media,
                max_concurrent_downloads=max_concurrent_downloads
            ))
        if enable_twitter:
            parsers.append(TwitterParser(
                max_media_size_mb=max_media_size_mb,
                large_media_threshold_mb=large_media_threshold_mb,
                use_image_proxy=use_image_proxy,
                use_video_proxy=use_video_proxy,
                proxy_url=proxy_url,
                cache_dir=cache_dir,
                pre_download_all_media=pre_download_all_media,
                max_concurrent_downloads=max_concurrent_downloads
            ))
        if enable_kuaishou:
            parsers.append(KuaishouParser(
                max_media_size_mb=max_media_size_mb,
                large_media_threshold_mb=large_media_threshold_mb,
                cache_dir=cache_dir,
                pre_download_all_media=pre_download_all_media,
                max_concurrent_downloads=max_concurrent_downloads
            ))
        if not parsers:
            raise ValueError("至少需要启用一个视频解析器。请检查配置中的 parser_enable_settings 设置。")
        self.parser_manager = ParserManager(parsers)

    def _check_cache_dir_available(self, cache_dir: str) -> bool:
        """
        检查缓存目录是否可用（可写）
        Args:
            cache_dir: 缓存目录路径
        Returns:
            bool: 如果目录可用返回True，否则返回False
        """
        if not cache_dir:
            return False
        try:
            os.makedirs(cache_dir, exist_ok=True)
            test_file = os.path.join(cache_dir, ".test_write")
            try:
                with open(test_file, 'w') as f:
                    f.write("test")
                os.unlink(test_file)
                return True
            except Exception:
                return False
        except Exception:
            return False

    async def terminate(self):
        """
        插件终止时的清理工作
        """
        pass

    def _should_parse(self, message_str: str) -> bool:
        """
        判断是否应该解析消息
        Args:
            message_str: 消息文本
        Returns:
            bool: 布尔值
        """
        if self.is_auto_parse:
            return True
        for keyword in self.trigger_keywords:
            if keyword in message_str:
                return True
        platform_triggers = [
            r'.?B站解析|b站解析|bilibili解析',
            r'.?抖音解析',
            r'.?快手解析'
        ]
        for pattern in platform_triggers:
            if re.search(pattern, message_str):
                return True
        return False

    def _cleanup_files(self, file_paths: list):
        """
        清理文件列表
        Args:
            file_paths: 文件路径列表
        Returns:
            Any: 返回值
        """
        for file_path in file_paths:
            if file_path and os.path.exists(file_path):
                try:
                    os.unlink(file_path)
                except Exception:
                    pass

    def _cleanup_all_files(self, temp_files: list, video_files: list):
        """
        清理所有临时文件和视频文件
        Args:
            temp_files: 临时文件列表
            video_files: 视频文件列表
        Returns:
            Any: 返回值
        """
        if temp_files:
            self._cleanup_files(temp_files)
        if video_files:
            self._cleanup_files(video_files)

    def _is_pure_image_gallery(self, nodes: list) -> bool:
        """
        判断节点列表是否是纯图片图集
        Args:
            nodes: 节点列表
        Returns:
            bool: 布尔值
        """
        has_video = False
        has_image = False
        for node in nodes:
            if isinstance(node, Video):
                has_video = True
                break
            elif isinstance(node, Image):
                has_image = True
        return has_image and not has_video

    @filter.event_message_type(EventMessageType.ALL)
    async def auto_parse(self, event: AstrMessageEvent):
        """
        自动解析消息中的视频链接
        Args:
            event: 消息事件对象
        Returns:
            Any: 返回值
        """
        if not self._should_parse(event.message_str):
            return
        links_with_parser = self.parser_manager.extract_all_links(event.message_str)
        if not links_with_parser:
            return
        await event.send(event.plain_result("视频解析bot为您服务 ٩( 'ω' )و"))
        result = await self.parser_manager.build_nodes(event, self.is_auto_pack)
        if result is None:
            return
        all_link_nodes, link_metadata, temp_files, video_files, normal_link_count = result
        try:
            from astrbot.api.message_components import Node
            sender_name = "视频解析bot"
            platform = event.get_platform_name()
            sender_id = event.get_self_id()
            if platform not in ("wechatpadpro", "webchat", "gewechat"):
                try:
                    sender_id = int(sender_id)
                except (ValueError, TypeError):
                    sender_id = 10000
            separator = "-------------------------------------"
            if self.is_auto_pack:
                normal_metadata = [meta for meta in link_metadata if meta['is_normal']]
                large_video_metadata = [meta for meta in link_metadata if meta['is_large_video']]
                normal_link_nodes = [meta['link_nodes'] for meta in normal_metadata]
                large_video_link_nodes = [meta['link_nodes'] for meta in large_video_metadata]
                if normal_link_nodes:
                    flat_nodes = []
                    normal_video_files_to_cleanup = []
                    for link_idx, link_nodes in enumerate(normal_link_nodes):
                        if link_idx < len(normal_metadata):
                            link_video_files = normal_metadata[link_idx].get('video_files', [])
                            if link_video_files:
                                normal_video_files_to_cleanup.extend(link_video_files)
                        if self._is_pure_image_gallery(link_nodes):
                            texts = [node for node in link_nodes if isinstance(node, Plain)]
                            images = [node for node in link_nodes if isinstance(node, Image)]
                            for text in texts:
                                flat_nodes.append(Node(name=sender_name, uin=sender_id, content=[text]))
                            if images:
                                flat_nodes.append(Node(name=sender_name, uin=sender_id, content=images))
                        else:
                            for node in link_nodes:
                                if node is not None:
                                    flat_nodes.append(Node(name=sender_name, uin=sender_id, content=[node]))
                        if link_idx < len(normal_link_nodes) - 1:
                            flat_nodes.append(Node(name=sender_name, uin=sender_id, content=[Plain(separator)]))
                    if flat_nodes:
                        try:
                            await event.send(event.chain_result([Nodes(flat_nodes)]))
                        finally:
                            # 确保即使发送失败也清理文件
                            if normal_video_files_to_cleanup:
                                self._cleanup_files(normal_video_files_to_cleanup)
                if large_video_link_nodes:
                    threshold_mb = int(self.large_media_threshold_mb) if self.large_media_threshold_mb > 0 else 50
                    notice_text = f"⚠️ 链接中包含超过{threshold_mb}MB的媒体时将单独发送所有媒体"
                    await event.send(event.plain_result(notice_text))
                    for link_idx, link_nodes in enumerate(large_video_link_nodes):
                        link_video_files = []
                        if link_idx < len(large_video_metadata):
                            link_video_files = large_video_metadata[link_idx].get('video_files', [])
                        try:
                            for node in link_nodes:
                                if node is not None:
                                    try:
                                        await event.send(event.chain_result([node]))
                                    except Exception:
                                        pass
                        finally:
                            # 确保即使发送失败也清理文件
                            if link_video_files:
                                self._cleanup_files(link_video_files)
                        if link_idx < len(large_video_link_nodes) - 1:
                            await event.send(event.plain_result(separator))
            else:
                for link_idx, (link_nodes, metadata) in enumerate(zip(all_link_nodes, link_metadata)):
                    link_video_files = metadata.get('video_files', [])
                    try:
                        if self._is_pure_image_gallery(link_nodes):
                            texts = [node for node in link_nodes if isinstance(node, Plain)]
                            images = [node for node in link_nodes if isinstance(node, Image)]
                            for text in texts:
                                await event.send(event.chain_result([text]))
                            if images:
                                await event.send(event.chain_result(images))
                        else:
                            for node in link_nodes:
                                if node is not None:
                                    try:
                                        await event.send(event.chain_result([node]))
                                    except Exception:
                                        pass
                    finally:
                        # 确保即使发送失败也清理文件
                        if link_video_files:
                            self._cleanup_files(link_video_files)
                    if link_idx < len(all_link_nodes) - 1:
                        await event.send(event.plain_result(separator))
            self._cleanup_all_files(temp_files, video_files)
        except Exception as e:
            self._cleanup_all_files(temp_files, video_files)
            raise
