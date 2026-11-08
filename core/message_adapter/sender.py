"""消息发送封装，统一不同会话场景下的发送行为。"""

import asyncio
from pathlib import Path
from typing import Any, List, Optional

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import File, Nodes, Plain, Image, Node, Record, Reply

from ..logger import logger

from ..constants import Config
from ..message_text import MAX_MESSAGE_TEXT_LENGTH, split_message_text
from .node_builder import is_pure_image_gallery


class MessageDeliveryError(RuntimeError):
    """预期发送的内容全部失败。"""


class MessageSender:
    """消息发送器，封装统一的私聊/群聊发送接口。"""

    @staticmethod
    def _message_contents(
        nodes: List[Any], preserve_order: bool, image_send_mode: str
    ) -> List[List[Any]]:
        """按图片发送方式分组，正文穿插时不跨文字合并图片。"""
        ordered = [node for node in nodes if node is not None]
        is_gallery = not preserve_order and is_pure_image_gallery(ordered)
        if is_gallery:
            ordered = [node for node in ordered if isinstance(node, Plain)] + [
                node for node in ordered if isinstance(node, Image)
            ]
        group_images = image_send_mode == Config.IMAGE_SEND_MODE_GROUP or (
            image_send_mode == Config.IMAGE_SEND_MODE_ORIGINAL and is_gallery
        )
        contents: List[List[Any]] = []
        for node in ordered:
            if (
                group_images
                and isinstance(node, Image)
                and contents
                and isinstance(contents[-1][0], Image)
            ):
                contents[-1].append(node)
            else:
                contents.append([node])
        return contents

    @staticmethod
    def _build_forward_nodes(
        content: List[Any],
        sender_name: str,
        sender_id: Any,
        max_length: int = MAX_MESSAGE_TEXT_LENGTH,
    ) -> List[Node]:
        """长文拆为多个转发节点，保留全文、换行和同一合并卡片。"""
        if len(content) == 1 and isinstance(content[0], Plain):
            return [
                Node(name=sender_name, uin=str(sender_id), content=[Plain(chunk)])
                for chunk in split_message_text(
                    content[0].text, max_length=max_length
                )
            ]
        return [Node(name=sender_name, uin=str(sender_id), content=content)]

    @staticmethod
    def _image_from_reference(reference: str) -> Image:
        """根据本地路径或 Token URL 构建图片节点。"""
        text = str(reference or "").strip()
        if text.lower().startswith(("http://", "https://")):
            scheme, _, remainder = text.partition(":")
            return Image.fromURL(f"{scheme.lower()}:{remainder}")
        return Image.fromFileSystem(text)

    @staticmethod
    def _metadata_for_link(link_metadata: Optional[List[dict]], link_idx: int) -> dict:
        if not link_metadata or link_idx >= len(link_metadata):
            return {}
        meta = link_metadata[link_idx]
        return meta if isinstance(meta, dict) else {}

    @staticmethod
    async def _try_send_content(
        event: AstrMessageEvent,
        content: List[Any],
        *,
        quote_message_id: str = "",
    ) -> Optional[Exception]:
        """收集单次发送的适配器异常，任务取消始终向上传播。"""
        if quote_message_id:
            content = [Reply(id=quote_message_id), *content]

        async def send() -> None:
            await event.send(event.chain_result(content))

        result, = await asyncio.gather(send(), return_exceptions=True)
        if isinstance(result, asyncio.CancelledError):
            raise result
        return result if isinstance(result, Exception) else None

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
        result = await MessageSender._try_send_content(
            event, [Plain(f"{label}有 {len(errors)} 项发送失败，其余内容已发送。")]
        )
        if result is not None:
            logger.warning(f"发送部分失败提示失败: {result}")

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
        text_metadata_images: Optional[List[str]] = None,
        image_send_mode: str = Config.IMAGE_SEND_MODE_ORIGINAL,
        forward_text_length: int = MAX_MESSAGE_TEXT_LENGTH,
    ) -> None:
        """使用 Nodes 合并转发发送结果。

        Args:
            event: 消息事件对象
            link_metadata: 链接元数据列表
            sender_name: 发送者名称
            sender_id: 发送者ID
            large_video_threshold_mb: 大视频阈值(MB)
            text_metadata_images: 已渲染的文本元数据图片路径或 Token URL，按页排列
            image_send_mode: 相邻图片合为一条消息或逐张发送。
            forward_text_length: 合并转发文本节点的分段长度。
        """
        normal_metadata = [
            meta for meta in link_metadata if meta.get("is_normal", True)
        ]
        ordered_links = {
            id(meta["link_nodes"])
            for meta in normal_metadata
            if meta.get("preserve_order") and meta.get("link_nodes")
        }
        large_media_metadata = [
            meta for meta in link_metadata if meta.get("is_large_media", False)
        ]
        normal_link_nodes = [
            meta["link_nodes"] for meta in normal_metadata if meta.get("link_nodes")
        ]
        audio_nodes = [
            node for link_nodes in normal_link_nodes for node in link_nodes
            if isinstance(node, (Record, File))
        ]
        normal_link_nodes = [
            (
                [node for node in link_nodes if not isinstance(node, (Record, File))],
                id(link_nodes) in ordered_links,
            )
            for link_nodes in normal_link_nodes
        ]
        normal_link_nodes = [
            (nodes, preserve_order) for nodes, preserve_order in normal_link_nodes if nodes
        ]
        large_media_link_nodes = [
            meta["link_nodes"] for meta in large_media_metadata if meta.get("link_nodes")
        ]
        separator = "-------------------------------------"
        expected = 0
        succeeded = 0
        errors: list[Exception] = []
        rendered_images = []
        for reference in text_metadata_images or []:
            try:
                rendered_images.append(self._image_from_reference(reference))
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                expected += 1
                errors.append(exc)
                logger.warning(f"构建文本元数据图片节点失败: {exc}")

        if normal_link_nodes or rendered_images:
            flat_nodes = []
            for content in self._message_contents(
                rendered_images, True, image_send_mode
            ):
                flat_nodes.extend(self._build_forward_nodes(
                    content, sender_name, sender_id, forward_text_length
                ))
            for link_idx, (link_nodes, preserve_order) in enumerate(normal_link_nodes):
                for content in self._message_contents(
                    link_nodes, preserve_order, image_send_mode
                ):
                    flat_nodes.extend(self._build_forward_nodes(
                        content, sender_name, sender_id, forward_text_length
                    ))
                if link_idx < len(normal_link_nodes) - 1:
                    flat_nodes.extend(self._build_forward_nodes(
                        [Plain(separator)], sender_name, sender_id, forward_text_length
                    ))
            if flat_nodes:
                expected += 1
                result = await self._try_send_content(event, [Nodes(flat_nodes)])
                if result is not None:
                    errors.append(result)
                    logger.warning(f"发送聚合消息失败: {result}")
                else:
                    succeeded += 1

        # 语音与音频文件始终独立发送，不依赖客户端对转发内音频的支持。
        for node in audio_nodes:
            expected += 1
            result = await self._try_send_content(event, [node])
            if result is not None:
                errors.append(result)
                logger.warning(f"发送音频失败: {result}")
            else:
                succeeded += 1

        if large_media_link_nodes:
            (
                large_expected,
                large_succeeded,
                large_errors,
            ) = await self.send_large_media_results(
                event,
                large_media_link_nodes,
                large_video_threshold_mb,
                image_send_mode=image_send_mode,
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
        *,
        image_send_mode: str = Config.IMAGE_SEND_MODE_ORIGINAL,
    ) -> tuple[int, int, list[Exception]]:
        """发送大媒体结果（单独发送）

        Args:
            event: 消息事件对象
            link_nodes_list: 链接节点列表
            large_video_threshold_mb: 大视频阈值(MB)
            image_send_mode: 相邻图片合为一条消息或逐张发送。

        Returns:
            预期发送数量、成功数量和发送错误列表。
        """
        separator = "-------------------------------------"
        threshold_mb = (
            int(large_video_threshold_mb) if large_video_threshold_mb > 0 else 50
        )
        notice_text = f"⚠️ 链接中包含超过{threshold_mb}MB的视频时将单独发送所有媒体"
        result = await MessageSender._try_send_content(event, [Plain(notice_text)])
        if result is not None:
            logger.warning(f"发送大媒体提示失败: {result}")
        expected = 0
        succeeded = 0
        errors: list[Exception] = []
        for link_idx, link_nodes in enumerate(link_nodes_list):
            for content in self._message_contents(link_nodes, True, image_send_mode):
                expected += 1
                result = await self._try_send_content(event, content)
                if result is not None:
                    errors.append(result)
                    logger.warning(f"发送大媒体节点失败: {result}")
                else:
                    succeeded += 1
            if link_idx < len(link_nodes_list) - 1:
                result = await MessageSender._try_send_content(event, [Plain(separator)])
                if result is not None:
                    logger.warning(f"发送分隔符失败: {result}")
        return expected, succeeded, errors

    async def send_individual_results(
        self,
        event: AstrMessageEvent,
        all_link_nodes: list,
        link_metadata: Optional[List[dict]] = None,
        *,
        quote_user_message: bool = False,
        quote_message_id: str = "",
        text_metadata_images: Optional[List[str]] = None,
        image_send_mode: str = Config.IMAGE_SEND_MODE_ORIGINAL,
    ) -> None:
        """发送非聚合结果（逐项独立发送）。

        Args:
            event: 消息事件对象
            all_link_nodes: 所有链接节点列表
            link_metadata: 每条链接的构建辅助信息
            quote_user_message: 文本元数据是否引用对应的用户消息
            quote_message_id: 被引用的用户消息 ID
            text_metadata_images: 已渲染的文本元数据图片路径或 Token URL，按页排列
            image_send_mode: 相邻图片合为一条消息或逐张发送。
        """
        separator = "-------------------------------------"
        quote_message_id = str(quote_message_id or "").strip()
        expected = 0
        succeeded = 0
        errors: list[Exception] = []
        rendered_images = []
        for reference in text_metadata_images or []:
            try:
                rendered_images.append(self._image_from_reference(reference))
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                expected += 1
                errors.append(exc)
                logger.warning(f"构建文本元数据图片节点失败: {exc}")
        for group_index, content in enumerate(self._message_contents(
            rendered_images, True, image_send_mode
        )):
            expected += 1
            result = await self._try_send_content(
                event,
                content,
                quote_message_id=(
                    quote_message_id
                    if quote_user_message and group_index == 0
                    else ""
                ),
            )
            if result is not None:
                errors.append(result)
                logger.warning(f"发送文本元数据图片失败: {result}")
            else:
                succeeded += 1

        non_empty_indexes = [
            index for index, link_nodes in enumerate(all_link_nodes) if link_nodes
        ]
        for link_idx, link_nodes in enumerate(all_link_nodes):
            if not link_nodes:
                continue
            meta = self._metadata_for_link(link_metadata, link_idx)
            metadata_text_node = meta.get("metadata_text_node")
            for content in self._message_contents(
                link_nodes, bool(meta.get("preserve_order")), image_send_mode
            ):
                expected += 1
                result = await self._try_send_content(
                    event,
                    content,
                    quote_message_id=(
                        quote_message_id
                        if quote_user_message
                        and any(node is metadata_text_node for node in content)
                        else ""
                    ),
                )
                if result is not None:
                    errors.append(result)
                    logger.warning(f"发送节点失败: {result}")
                else:
                    succeeded += 1
            if link_idx in non_empty_indexes[:-1]:
                result = await MessageSender._try_send_content(event, [Plain(separator)])
                if result is not None:
                    logger.warning(f"发送分隔符失败: {result}")
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
        forward_text_length: int = MAX_MESSAGE_TEXT_LENGTH,
        image_send_mode: str = Config.IMAGE_SEND_MODE_ORIGINAL,
    ) -> None:
        """发送独立翻译节点。

        Args:
            event: 消息事件对象。
            translation_link_nodes: 各条链接的翻译节点列表。
            should_aggregate_nodes: 是否使用合并转发。
            sender_name: 合并转发节点的发送者名称。
            sender_id: 合并转发节点的发送者 ID。
            forward_text_length: 合并转发文本节点的分段长度。
            image_send_mode: 翻译分页图片的发送方式。
        """
        non_empty = [
            (idx, nodes) for idx, nodes in enumerate(translation_link_nodes) if nodes
        ]
        if not non_empty:
            return

        if should_aggregate_nodes:
            flat_nodes = []
            for _, nodes in non_empty:
                for content in self._message_contents(nodes, True, image_send_mode):
                    flat_nodes.extend(self._build_forward_nodes(
                        content, sender_name, sender_id, forward_text_length
                    ))
            if flat_nodes:
                result = await self._try_send_content(event, [Nodes(flat_nodes)])
                if result is not None:
                    await self._finish_best_effort_delivery(
                        event,
                        label="翻译结果",
                        expected=1,
                        succeeded=0,
                        errors=[result],
                    )
            return

        separator = "-------------------------------------"
        expected = 0
        succeeded = 0
        errors: list[Exception] = []
        for item_idx, (_, nodes) in enumerate(non_empty):
            for content in self._message_contents(nodes, True, image_send_mode):
                expected += 1
                result = await self._try_send_content(event, content)
                if result is not None:
                    errors.append(result)
                    logger.warning(f"发送翻译节点失败: {result}")
                else:
                    succeeded += 1
            if item_idx < len(non_empty) - 1:
                result = await MessageSender._try_send_content(event, [Plain(separator)])
                if result is not None:
                    logger.warning(f"发送翻译分隔符失败: {result}")
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
        """发送本地 ZIP 文件。

        Args:
            event: 消息事件对象。
            archive_path: 已生成的 ZIP 文件路径。
        """
        try:
            from astrbot.api.message_components import File
        except ImportError as exc:
            raise RuntimeError("当前 AstrBot 版本不支持文件消息组件") from exc

        file_component = File(
            name=Path(archive_path).name,
            file=archive_path,
        )
        await event.send(event.chain_result([file_component]))
