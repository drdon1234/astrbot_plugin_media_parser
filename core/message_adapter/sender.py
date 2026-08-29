"""消息发送封装，统一不同会话场景下的发送行为。"""

from pathlib import Path
from typing import Any, List, Optional

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Nodes, Plain, Image, Node, Reply

from ..logger import logger

from .node_builder import is_pure_image_gallery


class MessageDeliveryError(RuntimeError):
    """预期发送的内容全部失败。"""


class MessageSender:
    """消息发送器，封装统一的私聊/群聊发送接口。"""

    @staticmethod
    def _image_from_reference(reference: str) -> Image:
        """根据本地路径或 Token URL 构建图片节点。"""
        text = str(reference or "").strip()
        if text.lower().startswith(("http://", "https://")):
            return Image.fromURL(text)
        return Image.fromFileSystem(text)

    @staticmethod
    def _metadata_for_link(link_metadata: Optional[List[dict]], link_idx: int) -> dict:
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

    @staticmethod
    async def _finish_best_effort_delivery(
        event: AstrMessageEvent,
        *,
        label: str,
        expected: int,
        succeeded: int,
        errors: list[Exception],
    ) -> None:
        if expected <= 0 or not errors:
            return
        if succeeded <= 0:
            raise MessageDeliveryError(
                f"{label}全部发送失败（{len(errors)}项）"
            ) from errors[0]
        try:
            await event.send(
                event.plain_result(
                    f"{label}有 {len(errors)} 项发送失败，其余内容已发送。"
                )
            )
        except Exception as exc:
            logger.warning(f"发送部分失败提示失败: {exc}")

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

    async def send_aggregated_results(
        self,
        event: AstrMessageEvent,
        link_metadata: list,
        sender_name: str,
        sender_id: Any,
        large_video_threshold_mb: float = 0.0,
        text_metadata_image: str = "",
    ):
        """使用 Nodes 合并转发发送结果。

        Args:
            event: 消息事件对象
            link_metadata: 链接元数据列表
            sender_name: 发送者名称
            sender_id: 发送者ID
            large_video_threshold_mb: 大视频阈值(MB)
            text_metadata_image: 已渲染的文本元数据图片路径
        """
        normal_metadata = [
            meta for meta in link_metadata if meta.get("is_normal", True)
        ]
        large_media_metadata = [
            meta for meta in link_metadata if meta.get("is_large_media", False)
        ]
        normal_link_nodes = [
            meta["link_nodes"] for meta in normal_metadata if meta.get("link_nodes")
        ]
        large_media_link_nodes = [
            meta["link_nodes"] for meta in large_media_metadata if meta.get("link_nodes")
        ]
        separator = "-------------------------------------"
        expected = 0
        succeeded = 0
        errors: list[Exception] = []
        rendered_image = None
        if text_metadata_image:
            try:
                rendered_image = self._image_from_reference(text_metadata_image)
            except Exception as exc:
                expected += 1
                errors.append(exc)
                logger.warning(f"构建文本元数据图片节点失败: {exc}")

        if normal_link_nodes or rendered_image is not None:
            flat_nodes = []
            if rendered_image is not None:
                flat_nodes.append(
                    Node(
                        name=sender_name,
                        uin=sender_id,
                        content=[rendered_image],
                    )
                )
            for link_idx, link_nodes in enumerate(normal_link_nodes):
                if is_pure_image_gallery(link_nodes):
                    texts = [node for node in link_nodes if isinstance(node, Plain)]
                    images = [node for node in link_nodes if isinstance(node, Image)]
                    for text in texts:
                        flat_nodes.append(
                            Node(name=sender_name, uin=sender_id, content=[text])
                        )
                    if images:
                        flat_nodes.append(
                            Node(name=sender_name, uin=sender_id, content=images)
                        )
                else:
                    for node in link_nodes:
                        if node is not None:
                            flat_nodes.append(
                                Node(name=sender_name, uin=sender_id, content=[node])
                            )
                if link_idx < len(normal_link_nodes) - 1:
                    flat_nodes.append(
                        Node(
                            name=sender_name, uin=sender_id, content=[Plain(separator)]
                        )
                    )
            if flat_nodes:
                expected += 1
                try:
                    await event.send(event.chain_result([Nodes(flat_nodes)]))
                    succeeded += 1
                except Exception as exc:
                    errors.append(exc)
                    logger.warning(f"发送聚合消息失败: {exc}")

        if large_media_link_nodes:
            (
                large_expected,
                large_succeeded,
                large_errors,
            ) = await self.send_large_media_results(
                event,
                large_media_link_nodes,
                large_video_threshold_mb,
            )
            expected += large_expected
            succeeded += large_succeeded
            errors.extend(large_errors)

        await self._finish_best_effort_delivery(
            event,
            label="解析结果",
            expected=expected,
            succeeded=succeeded,
            errors=errors,
        )

    async def send_large_media_results(
        self,
        event: AstrMessageEvent,
        link_nodes_list: list,
        large_video_threshold_mb: float = 0.0,
    ) -> tuple[int, int, list[Exception]]:
        """发送大媒体结果（单独发送）

        Args:
            event: 消息事件对象
            link_nodes_list: 链接节点列表
            large_video_threshold_mb: 大视频阈值(MB)
        """
        separator = "-------------------------------------"
        threshold_mb = (
            int(large_video_threshold_mb) if large_video_threshold_mb > 0 else 50
        )
        notice_text = f"⚠️ 链接中包含超过{threshold_mb}MB的视频时将单独发送所有媒体"
        try:
            await event.send(event.plain_result(notice_text))
        except Exception as exc:
            logger.warning(f"发送大媒体提示失败: {exc}")
        expected = 0
        succeeded = 0
        errors: list[Exception] = []
        for link_idx, link_nodes in enumerate(link_nodes_list):
            for node in link_nodes:
                if node is not None:
                    expected += 1
                    try:
                        await event.send(event.chain_result([node]))
                        succeeded += 1
                    except Exception as e:
                        errors.append(e)
                        logger.warning(f"发送大媒体节点失败: {e}")
            if link_idx < len(link_nodes_list) - 1:
                try:
                    await event.send(event.plain_result(separator))
                except Exception as e:
                    logger.warning(f"发送分隔符失败: {e}")
        return expected, succeeded, errors

    async def send_individual_results(
        self,
        event: AstrMessageEvent,
        all_link_nodes: list,
        link_metadata: Optional[List[dict]] = None,
        *,
        quote_user_message: bool = False,
        quote_message_id: str = "",
        text_metadata_image: str = "",
    ) -> None:
        """发送非聚合结果（逐项独立发送）。

        Args:
            event: 消息事件对象
            all_link_nodes: 所有链接节点列表
            link_metadata: 每条链接的构建辅助信息
            quote_user_message: 文本元数据是否引用对应的用户消息
            quote_message_id: 被引用的用户消息 ID
            text_metadata_image: 已渲染的文本元数据图片路径
        """
        separator = "-------------------------------------"
        quote_message_id = str(quote_message_id or "").strip()
        expected = 0
        succeeded = 0
        errors: list[Exception] = []
        if text_metadata_image:
            expected += 1
            try:
                image_node = self._image_from_reference(text_metadata_image)
                await self._send_single_node(
                    event,
                    image_node,
                    quote_message_id=(quote_message_id if quote_user_message else ""),
                )
                succeeded += 1
            except Exception as exc:
                errors.append(exc)
                logger.warning(f"发送文本元数据图片失败: {exc}")

        non_empty_indexes = [
            index for index, link_nodes in enumerate(all_link_nodes) if link_nodes
        ]
        for link_idx, link_nodes in enumerate(all_link_nodes):
            if not link_nodes:
                continue
            meta = self._metadata_for_link(link_metadata, link_idx)
            metadata_text_node = meta.get("metadata_text_node")
            if is_pure_image_gallery(link_nodes):
                texts = [node for node in link_nodes if isinstance(node, Plain)]
                images = [node for node in link_nodes if isinstance(node, Image)]
                for text in texts:
                    expected += 1
                    try:
                        await self._send_single_node(
                            event,
                            text,
                            quote_message_id=(
                                quote_message_id
                                if quote_user_message and text is metadata_text_node
                                else ""
                            ),
                        )
                        succeeded += 1
                    except Exception as exc:
                        errors.append(exc)
                        logger.warning(f"发送文本节点失败: {exc}")
                if images:
                    expected += 1
                    try:
                        await event.send(event.chain_result(images))
                        succeeded += 1
                    except Exception as exc:
                        errors.append(exc)
                        logger.warning(f"发送图片组失败: {exc}")
            else:
                for node in link_nodes:
                    if node is not None:
                        expected += 1
                        try:
                            await self._send_single_node(
                                event,
                                node,
                                quote_message_id=(
                                    quote_message_id
                                    if (
                                        quote_user_message
                                        and node is metadata_text_node
                                    )
                                    else ""
                                ),
                            )
                            succeeded += 1
                        except Exception as e:
                            errors.append(e)
                            logger.warning(f"发送节点失败: {e}")
            if link_idx in non_empty_indexes[:-1]:
                try:
                    await event.send(event.plain_result(separator))
                except Exception as exc:
                    logger.warning(f"发送分隔符失败: {exc}")
        await self._finish_best_effort_delivery(
            event,
            label="解析结果",
            expected=expected,
            succeeded=succeeded,
            errors=errors,
        )

    async def send_translation_results(
        self,
        event: AstrMessageEvent,
        translation_link_nodes: List[list],
        *,
        should_aggregate_nodes: bool,
        sender_name: str,
        sender_id: Any,
    ) -> None:
        """发送独立翻译节点。"""
        non_empty = [
            (idx, nodes) for idx, nodes in enumerate(translation_link_nodes) if nodes
        ]
        if not non_empty:
            return

        if should_aggregate_nodes:
            flat_nodes = []
            for _, nodes in non_empty:
                for node in nodes:
                    if node is not None:
                        flat_nodes.append(
                            Node(
                                name=sender_name,
                                uin=sender_id,
                                content=[node],
                            )
                        )
            if flat_nodes:
                try:
                    await event.send(event.chain_result([Nodes(flat_nodes)]))
                except Exception as exc:
                    await self._finish_best_effort_delivery(
                        event,
                        label="翻译结果",
                        expected=1,
                        succeeded=0,
                        errors=[exc],
                    )
            return

        separator = "-------------------------------------"
        expected = 0
        succeeded = 0
        errors: list[Exception] = []
        for item_idx, (_, nodes) in enumerate(non_empty):
            for node in nodes:
                if node is None:
                    continue
                expected += 1
                try:
                    await self._send_single_node(event, node)
                    succeeded += 1
                except Exception as e:
                    errors.append(e)
                    logger.warning(f"发送翻译节点失败: {e}")
            if item_idx < len(non_empty) - 1:
                try:
                    await event.send(event.plain_result(separator))
                except Exception as exc:
                    logger.warning(f"发送翻译分隔符失败: {exc}")
        await self._finish_best_effort_delivery(
            event,
            label="翻译结果",
            expected=expected,
            succeeded=succeeded,
            errors=errors,
        )

    async def send_zip_result(
        self,
        event: AstrMessageEvent,
        archive_path: str,
    ) -> None:
        """发送本地 ZIP 文件。"""
        try:
            from astrbot.api.message_components import File
        except ImportError as exc:
            raise RuntimeError("当前 AstrBot 版本不支持文件消息组件") from exc

        file_component = File(
            name=Path(archive_path).name,
            file=archive_path,
        )
        await event.send(event.chain_result([file_component]))
