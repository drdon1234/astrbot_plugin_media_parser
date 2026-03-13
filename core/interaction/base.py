import asyncio
from abc import ABC, abstractmethod
from typing import Any, Optional, Set

from astrbot.api.event import AstrMessageEvent


class AdminAssistManager(ABC):
    """管理员协助交互基类。"""

    def __init__(
        self,
        context: Any,
        admin_id: str,
        enabled: bool,
        reply_timeout_minutes: int,
        request_cooldown_minutes: int
    ):
        self.context = context
        self.admin_id = str(admin_id or "").strip()
        self.enabled = bool(enabled and self.admin_id)

        self.reply_timeout_seconds = max(1, int(reply_timeout_minutes) * 60)
        self.request_cooldown_seconds = max(
            1,
            int(request_cooldown_minutes) * 60
        )

        self._admin_private_origin: Optional[str] = None
        self._waiting_confirm = False
        self._confirm_deadline = 0.0
        self._last_request_at = 0.0

        self._lock = asyncio.Lock()
        self._tasks: Set[asyncio.Task] = set()

    def _normalize_sender_id(self, sender_id: Any) -> str:
        return str(sender_id or "").strip()

    def _is_admin_private_event(self, event: AstrMessageEvent) -> bool:
        if not event.is_private_chat():
            return False
        sender_id = self._normalize_sender_id(event.get_sender_id())
        return bool(self.admin_id and sender_id == self.admin_id)

    def try_update_admin_origin(self, event: AstrMessageEvent) -> None:
        """若消息来自管理员私聊，更新可用的私聊会话标识。"""
        if self._is_admin_private_event(event):
            self._admin_private_origin = event.unified_msg_origin

    def _new_task(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _send_private_text(self, unified_msg_origin: str, text: str) -> None:
        if not unified_msg_origin:
            return
        try:
            from astrbot.api.event import MessageChain
            chain = MessageChain().message(text)
            await self.context.send_message(unified_msg_origin, chain)
            return
        except Exception:
            pass

        await self.context.send_message(unified_msg_origin, text)

    @abstractmethod
    async def handle_admin_reply(
        self,
        event: AstrMessageEvent,
        *args: Any,
        **kwargs: Any
    ) -> bool:
        """处理管理员回复消息。"""
        raise NotImplementedError

    @abstractmethod
    def trigger_assist_request(self, reason: str) -> None:
        """触发一次协助请求。"""
        raise NotImplementedError

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

