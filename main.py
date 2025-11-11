# -*- coding: utf-8 -*-
import os
from typing import Any

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Nodes, Plain, Image, Video, Node
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.event_message_type import EventMessageType

from .parser_manager import ParserManager
from .parsers import (
    BilibiliParser,
    DouyinParser,
    TwitterParser,
    KuaishouParser
)


@register(
    "astrbot_plugin_video_parser",
    "drdon1234",
    "聚合解析流媒体平台链接，转换为媒体直链发送",
    "1.1.0"
)
class VideoParserPlugin(Star):

    def __init__(self, context: Context, config: dict):
        """初始化插件。

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
        media_size_settings = config.get("media_size_settings", {})
        max_media_size_mb = media_size_settings.get("max_media_size_mb", 0.0)
        large_media_threshold_mb = media_size_settings.get(
            "large_media_threshold_mb",
            100.0
        )
        if large_media_threshold_mb > 0:
            large_media_threshold_mb = min(large_media_threshold_mb, 100.0)
        self.large_media_threshold_mb = large_media_threshold_mb
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
        self.cache_dir_available = self._check_cache_dir_available(cache_dir)
        if self.cache_dir_available:
            os.makedirs(cache_dir, exist_ok=True)
        parser_enable_settings = config.get("parser_enable_settings", {})
        enable_bilibili = parser_enable_settings.get("enable_bilibili", True)
        enable_douyin = parser_enable_settings.get("enable_douyin", True)
        enable_twitter = parser_enable_settings.get("enable_twitter", True)
        enable_kuaishou = parser_enable_settings.get(
            "enable_kuaishou",
            True
        )
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
            raise ValueError(
                "至少需要启用一个视频解析器。"
                "请检查配置中的 parser_enable_settings 设置。"
            )
        self.parser_manager = ParserManager(parsers)

    def _check_cache_dir_available(self, cache_dir: str) -> bool:
        """检查缓存目录是否可用（可写）。

        Args:
            cache_dir: 缓存目录路径

        Returns:
            如果目录可用返回True，否则返回False
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
            except Exception as e:
                self.logger.error(f"检查缓存目录写入权限失败: {e}")
                return False
        except Exception as e:
            self.logger.error(f"检查缓存目录可用性失败: {e}")
            return False

    async def terminate(self):
        """插件终止时的清理工作。"""
        pass

    def _should_parse(self, message_str: str) -> bool:
        """判断是否应该解析消息。

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

    def _cleanup_files(self, file_paths: list):
        """清理文件列表。

        Args:
            file_paths: 文件路径列表
        """
        for file_path in file_paths:
            if file_path and os.path.exists(file_path):
                try:
                    os.unlink(file_path)
                except Exception as e:
                    self.logger.warning(
                        f"清理文件失败: {file_path}, 错误: {e}"
                    )

    def _cleanup_all_files(self, temp_files: list, video_files: list):
        """清理所有临时文件和视频文件。

        Args:
            temp_files: 临时文件列表
            video_files: 视频文件列表
        """
        if temp_files:
            self._cleanup_files(temp_files)
        if video_files:
            self._cleanup_files(video_files)

    def _is_pure_image_gallery(self, nodes: list) -> bool:
        """判断节点列表是否是纯图片图集。

        Args:
            nodes: 节点列表

        Returns:
            如果是纯图片图集返回True，否则返回False
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

    def _get_sender_info(self, event: AstrMessageEvent) -> tuple:
        """获取发送者信息。

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

    async def _send_packed_results(
        self,
        event: AstrMessageEvent,
        link_metadata: list,
        sender_name: str,
        sender_id: Any
    ):
        """发送打包的结果（使用Nodes）。

        Args:
            event: 消息事件对象
            link_metadata: 链接元数据列表
            sender_name: 发送者名称
            sender_id: 发送者ID
        """
        normal_metadata = [
            meta for meta in link_metadata if meta['is_normal']
        ]
        large_video_metadata = [
            meta for meta in link_metadata if meta['is_large_video']
        ]
        normal_link_nodes = [
            meta['link_nodes'] for meta in normal_metadata
        ]
        large_video_link_nodes = [
            meta['link_nodes'] for meta in large_video_metadata
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
                if self._is_pure_image_gallery(link_nodes):
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
                        self._cleanup_files(normal_video_files_to_cleanup)

        if large_video_link_nodes:
            await self._send_large_media_results(
                event,
                large_video_metadata,
                large_video_link_nodes,
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
        """发送大媒体结果（单独发送）。

        Args:
            event: 消息事件对象
            metadata: 元数据列表
            link_nodes_list: 链接节点列表
            sender_name: 发送者名称
            sender_id: 发送者ID
        """
        separator = "-------------------------------------"
        threshold_mb = (
            int(self.large_media_threshold_mb)
            if self.large_media_threshold_mb > 0
            else 50
        )
        notice_text = (
            f"⚠️ 链接中包含超过{threshold_mb}MB的媒体时"
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
                    self._cleanup_files(link_video_files)
            if link_idx < len(link_nodes_list) - 1:
                await event.send(event.plain_result(separator))

    async def _send_unpacked_results(
        self,
        event: AstrMessageEvent,
        all_link_nodes: list,
        link_metadata: list
    ):
        """发送非打包的结果（独立发送）。

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
                if self._is_pure_image_gallery(link_nodes):
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
                    self._cleanup_files(link_video_files)
            if link_idx < len(all_link_nodes) - 1:
                await event.send(event.plain_result(separator))

    @filter.event_message_type(EventMessageType.ALL)
    async def auto_parse(self, event: AstrMessageEvent):
        """自动解析消息中的视频链接。

        Args:
            event: 消息事件对象
        """
        if not self._should_parse(event.message_str):
            return
        links_with_parser = self.parser_manager.extract_all_links(
            event.message_str
        )
        if not links_with_parser:
            return
        await event.send(event.plain_result("视频解析bot为您服务 ٩( 'ω' )و"))
        sender_name, sender_id = self._get_sender_info(event)
        result = await self.parser_manager.build_nodes(
            event,
            self.is_auto_pack,
            sender_name,
            sender_id
        )
        if result is None:
            return
        all_link_nodes, link_metadata, temp_files, video_files, \
            normal_link_count = result
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
            self._cleanup_all_files(temp_files, video_files)
        except Exception as e:
            self.logger.exception(f"auto_parse方法执行失败: {e}")
            self._cleanup_all_files(temp_files, video_files)
            raise
