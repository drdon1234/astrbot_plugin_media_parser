# -*- coding: utf-8 -*-
"""
消息发送模块
统一管理消息发送逻辑
集中所有 astrbot 消息组件和事件相关的导入
"""
from typing import Any, List

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Nodes, Plain, Image, Node

from .node_builder import is_pure_image_gallery
from ..file_cleaner import cleanup_files


class MessageSender:
    """消息发送器，负责统一管理消息发送逻辑"""

    def __init__(self, logger=None):
        """初始化消息发送器

        Args:
            logger: 日志记录器（可选）
        """
        self.logger = logger

    def get_sender_info(self, event: AstrMessageEvent) -> tuple:
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

    async def send_packed_results(
        self,
        event: AstrMessageEvent,
        link_metadata: list,
        sender_name: str,
        sender_id: Any,
        large_video_threshold_mb: float = 0.0
    ):
        """发送打包的结果（使用Nodes）

        Args:
            event: 消息事件对象
            link_metadata: 链接元数据列表
            sender_name: 发送者名称
            sender_id: 发送者ID
            large_video_threshold_mb: 大视频阈值(MB)
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
                    cleanup_files(normal_video_files_to_cleanup)

        if large_media_link_nodes:
            await self.send_large_media_results(
                event,
                large_media_metadata,
                large_media_link_nodes,
                sender_name,
                sender_id,
                large_video_threshold_mb
            )

    async def send_large_media_results(
        self,
        event: AstrMessageEvent,
        metadata: list,
        link_nodes_list: list,
        sender_name: str,
        sender_id: Any,
        large_video_threshold_mb: float = 0.0
    ):
        """发送大媒体结果（单独发送）

        Args:
            event: 消息事件对象
            metadata: 元数据列表
            link_nodes_list: 链接节点列表
            sender_name: 发送者名称
            sender_id: 发送者ID
            large_video_threshold_mb: 大视频阈值(MB)
        """
        separator = "-------------------------------------"
        threshold_mb = (
            int(large_video_threshold_mb)
            if large_video_threshold_mb > 0
            else 50
        )
        notice_text = (
            f"⚠️ 链接中包含超过{threshold_mb}MB的视频时"
            f"将单独发送所有媒体"
        )
        all_video_files_to_cleanup = []
        try:
            await event.send(event.plain_result(notice_text))
            for link_idx, link_nodes in enumerate(link_nodes_list):
                link_video_files = []
                if link_idx < len(metadata):
                    link_video_files = metadata[link_idx].get('video_files', [])
                all_video_files_to_cleanup.extend(link_video_files)
                try:
                    for node in link_nodes:
                        if node is not None:
                            try:
                                await event.send(event.chain_result([node]))
                            except Exception as e:
                                if self.logger:
                                    self.logger.warning(f"发送大媒体节点失败: {e}")
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"发送大媒体链接失败: {e}")
                finally:
                    cleanup_files(link_video_files)
                if link_idx < len(link_nodes_list) - 1:
                    try:
                        await event.send(event.plain_result(separator))
                    except Exception as e:
                        if self.logger:
                            self.logger.warning(f"发送分隔符失败: {e}")
        except Exception as e:
            if self.logger:
                self.logger.exception(f"发送大媒体结果失败: {e}")
            cleanup_files(all_video_files_to_cleanup)
            raise

    async def send_unpacked_results(
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
                                if self.logger:
                                    self.logger.warning(f"发送节点失败: {e}")
            finally:
                cleanup_files(link_video_files)
            if link_idx < len(all_link_nodes) - 1:
                await event.send(event.plain_result(separator))

