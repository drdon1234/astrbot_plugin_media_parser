import time
from typing import Optional, Any

import aiohttp

from astrbot.api.event import AstrMessageEvent

from ....logger import logger
from ...base import AdminAssistManager


class BilibiliAdminCookieAssistManager(AdminAssistManager):
    """B站Cookie管理员协助登录状态机（插件侧后台触发，不阻塞解析链）。"""

    def __init__(
        self,
        context,
        admin_id: str,
        enabled: bool,
        reply_timeout_minutes: int,
        request_cooldown_minutes: int
    ):
        super().__init__(
            context=context,
            admin_id=admin_id,
            enabled=enabled,
            reply_timeout_minutes=reply_timeout_minutes,
            request_cooldown_minutes=request_cooldown_minutes
        )

    async def handle_admin_reply(
        self,
        event: AstrMessageEvent,
        auth_runtime: Optional[Any]
    ) -> bool:
        """处理管理员私聊回复。

        Returns:
            bool: 是否命中并消费了协助会话回复。
        """
        if not self._is_admin_private_event(event):
            return False

        self._admin_private_origin = event.unified_msg_origin
        if not self.enabled:
            return False

        async with self._lock:
            if not self._waiting_confirm:
                return False

            now = time.time()
            if now > self._confirm_deadline:
                self._waiting_confirm = False
                await event.send(event.plain_result("本轮B站Cookie协助请求已超时。"))
                return True

            message_text = (event.message_str or "").strip()
            self._waiting_confirm = False

        if message_text != "确定":
            await event.send(event.plain_result("已取消本轮B站Cookie协助登录。"))
            return True

        if auth_runtime is None:
            await event.send(event.plain_result("B站登录运行时未初始化，无法发起协助登录。"))
            return True

        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                payload = await auth_runtime.generate_login_payload(session)
        except Exception as e:
            logger.warning(f"[bilibili] 生成管理员协助登录链接失败: {e}")
            await event.send(event.plain_result("生成B站登录链接失败，请稍后重试。"))
            return True

        await event.send(event.plain_result(
            "请使用以下任一方式完成登录：\n"
            f"登录链接: {payload['login_url']}\n"
            f"二维码链接: {payload['qr_code_url']}"
        ))

        self._new_task(
            self._poll_login_and_notify(
                auth_runtime=auth_runtime,
                qrcode_key=payload["qrcode_key"],
                unified_msg_origin=event.unified_msg_origin
            )
        )
        return True

    def trigger_assist_request(self, reason: str) -> None:
        if not self.enabled:
            return
        self._new_task(self._trigger_assist_request(reason))

    async def _trigger_assist_request(self, reason: str) -> None:
        async with self._lock:
            if self._waiting_confirm:
                return

            now = time.time()
            if now - self._last_request_at < self.request_cooldown_seconds:
                return
            if not self._admin_private_origin:
                logger.warning(
                    "[bilibili] 无管理员私聊会话可用，无法主动发送Cookie协助请求。"
                )
                return

            self._waiting_confirm = True
            self._confirm_deadline = now + self.reply_timeout_seconds
            self._last_request_at = now
            unified_msg_origin = self._admin_private_origin

        reason_text = reason or "cookie_unavailable"
        await self._send_private_text(
            unified_msg_origin,
            "检测到B站Cookie不可用，是否协助登录？\n"
            "回复“确定”将发送登录链接与二维码，其他任何回复均视为不协助。\n"
            f"本次原因: {reason_text}\n"
            f"有效期: {int(self.reply_timeout_seconds / 60)} 分钟。"
        )

    async def _poll_login_and_notify(
        self,
        auth_runtime: Any,
        qrcode_key: str,
        unified_msg_origin: str
    ) -> None:
        timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                result = await auth_runtime.poll_login_until_complete(
                    session=session,
                    qrcode_key=qrcode_key,
                    timeout_seconds=self.reply_timeout_seconds
                )
        except Exception as e:
            logger.warning(f"[bilibili] 管理员协助登录轮询失败: {e}")
            await self._send_private_text(
                unified_msg_origin,
                "B站登录轮询失败，请稍后重试。"
            )
            return

        status = result.get("status")
        if status == "success":
            await self._send_private_text(
                unified_msg_origin,
                "B站扫码登录成功，Cookie已更新。"
            )
            return

        if status == "expired":
            await self._send_private_text(
                unified_msg_origin,
                "B站二维码已过期，本轮协助登录结束。"
            )
            return

        await self._send_private_text(
            unified_msg_origin,
            "B站扫码登录超时，本轮协助登录结束。"
        )

