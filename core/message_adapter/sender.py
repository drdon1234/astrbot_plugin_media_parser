"""消息发送封装，统一不同会话场景下的发送行为。"""
from typing import Any, List, Optional

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Nodes, Plain, Image, Node, Reply

from .node_builder import is_pure_image_gallery
from ..logger import logger


class MessageSender:

    """消息发送器，封装统一的私聊/群聊发送接口。"""

    @staticmethod
    def _metadata_for_link(
        link_metadata: Optional[List[dict]],
        link_idx: int
    ) -> dict:
        if not link_metadata or link_idx >= len(link_metadata):
            return {}
        meta = link_metadata[link_idx]
        return meta if isinstance(meta, dict) else {}

    async def _send_single_node(
        self,
        event: AstrMessageEvent,
        node: Any,
        *,
        quote_message_id: str = "",
    ) -> None:
        content = []
        if quote_message_id:
            content.append(Reply(id=quote_message_id))
        content.append(node)
        await event.send(event.chain_result(content))

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
            for link_idx, link_nodes in enumerate(normal_link_nodes):
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
                await event.send(event.chain_result([Nodes(flat_nodes)]))

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
        await event.send(event.plain_result(notice_text))
        for link_idx, link_nodes in enumerate(link_nodes_list):
            for node in link_nodes:
                if node is not None:
                    try:
                        await event.send(event.chain_result([node]))
                    except Exception as e:
                        logger.warning(f"发送大媒体节点失败: {e}")
            if link_idx < len(link_nodes_list) - 1:
                try:
                    await event.send(event.plain_result(separator))
                except Exception as e:
                    logger.warning(f"发送分隔符失败: {e}")

    async def send_unpacked_results(
        self,
        event: AstrMessageEvent,
        all_link_nodes: list,
        link_metadata: Optional[List[dict]] = None,
        *,
        quote_user_message: bool = False,
        quote_message_id: str = "",
    ) -> None:
        """发送非打包的结果（独立发送）

        Args:
            event: 消息事件对象
            all_link_nodes: 所有链接节点列表
            link_metadata: 每条链接的构建辅助信息
            quote_user_message: 文本元数据是否引用对应的用户消息
            quote_message_id: 被引用的用户消息 ID
        """
        separator = "-------------------------------------"
        quote_message_id = str(quote_message_id or "").strip()
        for link_idx, link_nodes in enumerate(all_link_nodes):
            meta = self._metadata_for_link(link_metadata, link_idx)
            metadata_text_node = meta.get("metadata_text_node")
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
                    await self._send_single_node(
                        event,
                        text,
                        quote_message_id=(
                            quote_message_id
                            if quote_user_message and text is metadata_text_node
                            else ""
                        ),
                    )
                if images:
                    await event.send(event.chain_result(images))
            else:
                for node in link_nodes:
                    if node is not None:
                        try:
                            await self._send_single_node(
                                event,
                                node,
                                quote_message_id=(
                                    quote_message_id
                                    if (
                                        quote_user_message and
                                        node is metadata_text_node
                                    )
                                    else ""
                                ),
                            )
                        except Exception as e:
                            logger.warning(f"发送节点失败: {e}")
            if link_idx < len(all_link_nodes) - 1:
                await event.send(event.plain_result(separator))

    async def send_translation_results(
        self,
        event: AstrMessageEvent,
        translation_link_nodes: List[list],
        *,
        should_pack: bool,
        sender_name: str,
        sender_id: Any,
    ) -> None:
        """发送独立翻译节点。"""
        non_empty = [
            (idx, nodes)
            for idx, nodes in enumerate(translation_link_nodes)
            if nodes
        ]
        if not non_empty:
            return

        if should_pack:
            flat_nodes = []
            for _, nodes in non_empty:
                for node in nodes:
                    if node is not None:
                        flat_nodes.append(Node(
                            name=sender_name,
                            uin=sender_id,
                            content=[node],
                        ))
            if flat_nodes:
                await event.send(event.chain_result([Nodes(flat_nodes)]))
            return

        separator = "-------------------------------------"
        for item_idx, (_, nodes) in enumerate(non_empty):
            for node in nodes:
                if node is None:
                    continue
                try:
                    await self._send_single_node(event, node)
                except Exception as e:
                    logger.warning(f"发送翻译节点失败: {e}")
            if item_idx < len(non_empty) - 1:
                await event.send(event.plain_result(separator))

