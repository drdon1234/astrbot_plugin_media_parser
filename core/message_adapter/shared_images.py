"""按 OneBot 连接发送共享缓存中的图片并保护仍可能被读取的文件。"""

import asyncio
import os
import re
import time
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image, Nodes, Plain

from ..logger import logger

from ..downloader.fileio import run_blocking
from ..storage.cache_marker import (
    EXPIRY_FILE_NAME, SHARED_SEND_PREFIX, mark_subdir_expires_at, stamp_subdir,
)
from ..storage.file_cleaner import cleanup_directory


_UPLOAD_GRACE_SECONDS = 600


class SharedImageDelivery:
    """在插件实例内按平台实例和机器人账号记住共享目录的可用性。"""

    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()
        self._unavailable: set[tuple[str, str]] = set()

    def _prepare(self, content: list, folder: Path, quote_id: str) -> Optional[dict]:
        """仅为缓存根目录内的本地图片建立独立硬链接，不复制或改写原图。"""
        forward = len(content) == 1 and isinstance(content[0], Nodes)
        groups = [(n, n.content) for n in content[0].nodes] if forward else [(None, content)]
        messages = []
        links = []
        for node, parts in groups:
            segments = []
            for part in parts:
                if isinstance(part, Plain):
                    segments.append({"type": "text", "data": {"text": part.text}})
                    continue
                if not isinstance(part, Image) or not getattr(part, "path", None) or getattr(part, "url", None):
                    return None
                source = Path(part.path).resolve(strict=True)
                if not source.is_relative_to(self.root) or not source.is_file():
                    return None
                staged = folder / f"{len(links)}{source.suffix}"
                links.append((source, staged))
                segments.append({"type": "image", "data": {"file": staged.as_uri()}})
            if forward:
                messages.append({"type": "node", "data": {
                    "user_id": str(node.uin), "nickname": node.name, "content": segments,
                }})
            else:
                messages = segments
        if not links:
            return None
        folder.mkdir()
        try:
            stamp_subdir(str(folder))
            if not mark_subdir_expires_at(str(folder), time.time() + _UPLOAD_GRACE_SECONDS):
                raise OSError("无法保护共享图片")
            for source, staged in links:
                os.link(source, staged)
        except OSError:
            cleanup_directory(str(folder))
            raise
        if quote_id and not forward:
            messages.insert(0, {"type": "reply", "data": {"id": quote_id}})
        return {"messages" if forward else "message": messages}

    @staticmethod
    def _path_rejection(result: dict, folder: Path) -> tuple[bool, bool]:
        """仅接受协议端明确的读路径拒绝，区分单个文件和整个目录。"""
        if result.get("status") != "failed" or not result.get("retcode") or result.get("retcode") == "0":
            return False, False
        detail = unquote(" ".join(str(result.get(k) or "") for k in ("message", "wording"))).casefold()
        if not any(word in detail for word in (
            "enoent", "eacces", "enotdir", "no such file", "file not found", "permission denied",
            "文件不存在", "路径不存在", "目录不存在", "无权读取", "没有读取权限",
        )):
            return False, False
        # 日志中可能还有 QQ 自己的临时文件；只匹配本次提交的暂存路径。
        paths = (folder.as_posix().casefold(), folder.parent.as_posix().casefold())
        normalized = detail.replace("\\", "/")
        for path in paths:
            if re.search(re.escape(path) + r"""(?=/?(?:['"\s,，:：]|$))""", normalized):
                return True, True
        return paths[0] + "/" in normalized, False

    async def send(self, event: AstrMessageEvent, content: list, quote_id: str) -> Optional[str]:
        """发送共享图片。返回消息编号，安全切换至 Base64 时返回空值。

        Args:
            event: 当前 OneBot 消息事件。
            content: 普通消息组件或一条合并转发。
            quote_id: 首条消息可选的引用编号。
        """
        if not any(isinstance(part, (Image, Nodes)) for part in content):
            return None
        key = (event.get_platform_id(), event.get_self_id())
        if key in self._unavailable:
            return None
        folder = self.root / (SHARED_SEND_PREFIX + uuid.uuid4().hex)
        try:
            payload = await run_blocking(
                self._prepare, content, folder, quote_id,
                cancel_result_cleanup=lambda _: cleanup_directory(str(folder)),
            )
        except (OSError, ValueError, RuntimeError) as exc:
            logger.debug(f"共享图片准备失败，保留原图改用常规发送：{type(exc).__name__}")
            return None
        if payload is None:
            return None

        submitted = False
        try:
            group = event.get_group_id()
            target = "group" if group else "private"
            params = {"group_id": int(group)} if group else {"user_id": int(event.get_sender_id())}
            action = f"send_{target}_forward_msg" if "messages" in payload else f"send_{target}_msg"
            submitted = True
            result, = await asyncio.gather(
                event.bot.call_action(action, **params, self_id=key[1], **payload), return_exceptions=True,
            )
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, Exception):
                failure = getattr(result, "result", None)
                if isinstance(failure, dict):
                    rejected, directory = self._path_rejection(failure, folder)
                    if rejected:
                        if directory:
                            self._unavailable.add(key)
                        logger.info("协议端拒绝读取共享图片，本条改用 Base64 发送")
                        return None
                raise result
            mid = result.get("message_id") if isinstance(result, dict) else None
            if not mid:
                raise RuntimeError("共享图片提交后未取得消息编号，已保留暂存文件")
            return str(mid)
        finally:
            # 消息回执确认提交结果；发送端无法据此判断协议端何时关闭图片文件。
            if not submitted:
                await run_blocking(cleanup_directory, str(folder))
            elif not await run_blocking(mark_subdir_expires_at, str(folder), time.time() + _UPLOAD_GRACE_SECONDS):
                # 去掉旧截止时间，定期清理将按目录最近修改时间及保底宽限期回收。
                try:
                    (folder / EXPIRY_FILE_NAME).unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning(f"更新共享图片清理时间失败：{exc}")
