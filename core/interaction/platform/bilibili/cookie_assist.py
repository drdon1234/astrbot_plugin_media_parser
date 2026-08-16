"""B 站 Cookie 辅助登录交互管理器。"""

import asyncio
import os
import tempfile
import time
from typing import Optional, Any

import aiohttp
import qrcode

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image, Plain

from ....logger import logger
from ...base import AdminAssistManager


class BilibiliAdminCookieAssistManager(AdminAssistManager):
    """B站Cookie管理员协助登录状态机（插件侧后台触发，不阻塞解析链）。"""

    QR_CODE_TTL_SECONDS = 180

    def __init__(
        self,
        context,
        admin_id: str,
        enabled: bool,
        reply_timeout_minutes: int,
        request_cooldown_minutes: int,
        command: str = "B站更新Cookie",
    ):
        """初始化 B 站 Cookie 辅助管理器。"""
        super().__init__(
            context=context,
            admin_id=admin_id,
            enabled=enabled,
            reply_timeout_minutes=reply_timeout_minutes,
            request_cooldown_minutes=request_cooldown_minutes,
        )
        self._confirm_timeout_task: Optional[asyncio.Task] = None
        self.command = str(command or "").strip()
        self._login_in_progress = False

    async def handle_admin_reply(
        self, event: AstrMessageEvent, auth_runtime: Optional[Any]
    ) -> bool:
        """处理管理员私聊回复。

        Returns:
            bool: 是否命中并消费了协助会话回复。
        """
        if not self._is_admin_private_event(event):
            return False
        if not self._is_user_message_event(event):
            return False

        self._admin_private_origin = event.unified_msg_origin
        if not self.enabled:
            return False

        async with self._lock:
            if not self._waiting_confirm:
                return False

            now = time.monotonic()
            if now > self._confirm_deadline:
                self._clear_confirmation_locked()
                expired = True
                message_text = ""
            else:
                expired = False
                message_text = (event.message_str or "").strip()
                self._clear_confirmation_locked()

        if expired:
            await event.send(event.plain_result("本轮B站Cookie协助请求已超时。"))
            return True

        if message_text != "确定":
            await event.send(event.plain_result("已取消本轮B站Cookie协助登录。"))
            return True

        if auth_runtime is None:
            await event.send(
                event.plain_result("B站登录运行时未初始化，无法发起协助登录。")
            )
            return True

        await self._start_login_flow(event, auth_runtime)
        return True

    async def handle_admin_command(
        self, event: AstrMessageEvent, auth_runtime: Optional[Any]
    ) -> bool:
        """处理管理员主动更新 Cookie 指令，不受自动请求冷却限制。

        指令文本匹配时必定消费该消息（返回 True），避免指令流向 LLM。
        管理员可在私聊或群聊会话中触发；非管理员/未配置管理员时会给出
        明确提示而不是静默失败。
        """
        if not self._is_user_message_event(event):
            return False
        message_text = (event.message_str or "").strip()
        if not self.command or message_text.casefold() != self.command.casefold():
            return False

        if self._is_admin_event(event):
            # 管理员(插件配置的 admin_id 或 AstrBot 全局管理员)可直接触发
            pass
        elif not self.admin_id:
            await event.send(
                event.plain_result(
                    "尚未配置管理员 ID，无法使用“B站更新Cookie”指令。"
                    "请在插件配置的“权限控制 → 管理员 ID”中填写你的用户 ID。"
                )
            )
            return True
        else:
            await event.send(
                event.plain_result("“B站更新Cookie”指令仅管理员可使用。")
            )
            return True

        self._admin_private_origin = event.unified_msg_origin
        if not self.enabled:
            await event.send(
                event.plain_result("B站管理员主动更新 Cookie 未启用，请先开启相关配置。")
            )
            return True

        async with self._lock:
            if self._waiting_confirm:
                self._clear_confirmation_locked()
            # A manual request supersedes automatic cooldown and prevents a
            # stale automatic notification from being queued immediately after it.
            self._last_request_at = time.monotonic()

        await self._start_login_flow(event, auth_runtime)
        return True

    async def _start_login_flow(self, event: AstrMessageEvent, auth_runtime: Any) -> None:
        """生成二维码并启动一次扫码轮询。"""
        if auth_runtime is None:
            await event.send(event.plain_result("B站登录运行时未初始化，无法发起协助登录。"))
            return

        async with self._lock:
            already_running = self._login_in_progress
            if not already_running:
                self._login_in_progress = True
        if already_running:
            await event.send(
                event.plain_result("已有一轮B站扫码登录正在进行，请先完成或等待其结束。")
            )
            return

        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                payload = await auth_runtime.generate_login_payload(session)
            await self._send_local_login_qr(event, payload["login_url"])
            self._new_task(
                self._poll_login_and_notify(
                    auth_runtime=auth_runtime,
                    qrcode_key=payload["qrcode_key"],
                    unified_msg_origin=event.unified_msg_origin,
                )
            )
        except asyncio.CancelledError:
            self._login_in_progress = False
            raise
        except Exception as exc:
            self._login_in_progress = False
            logger.warning(f"[bilibili] 生成管理员协助登录链接失败: {exc}")
            await event.send(event.plain_result("生成B站登录链接失败，请稍后重试。"))

    def trigger_assist_request(self, reason: str) -> None:
        """发起一次管理员辅助登录请求。"""
        if not self.enabled:
            return
        self._new_task(self._trigger_assist_request(reason))

    async def _trigger_assist_request(self, reason: str) -> None:
        """异步执行辅助登录请求提交流程。"""
        async with self._lock:
            now = time.monotonic()
            if self._login_in_progress:
                return
            if self._waiting_confirm:
                if now < self._confirm_deadline:
                    return
                self._clear_confirmation_locked()
            if now - self._last_request_at < self.request_cooldown_seconds:
                return
            if not self._admin_private_origin:
                logger.warning(
                    "[bilibili] 无管理员私聊会话可用，无法主动发送Cookie协助请求。"
                )
                return

            self._waiting_confirm = True
            deadline = now + self.reply_timeout_seconds
            self._confirm_deadline = deadline
            previous_request_at = self._last_request_at
            self._last_request_at = now
            unified_msg_origin = self._admin_private_origin
            self._confirm_timeout_task = self._new_task(
                self._expire_confirmation(deadline, unified_msg_origin)
            )

        reason_text = reason or "cookie_unavailable"
        try:
            await self._send_private_text(
                unified_msg_origin,
                "检测到B站Cookie不可用，是否协助登录？\n"
                "回复“确定”将发送登录链接与二维码，其他任何回复均视为不协助。\n"
                f"本次原因: {reason_text}\n"
                f"有效期: {int(self.reply_timeout_seconds / 60)} 分钟。",
            )
        except Exception:
            async with self._lock:
                if self._waiting_confirm and self._confirm_deadline == deadline:
                    self._clear_confirmation_locked()
                    self._last_request_at = previous_request_at
            raise

    def _clear_confirmation_locked(self) -> None:
        """Reset confirmation state; caller must hold ``self._lock``."""
        self._waiting_confirm = False
        self._confirm_deadline = 0.0
        timeout_task = self._confirm_timeout_task
        self._confirm_timeout_task = None
        if (
            timeout_task is not None
            and timeout_task is not asyncio.current_task()
            and not timeout_task.done()
        ):
            timeout_task.cancel()

    async def _expire_confirmation(
        self, deadline: float, unified_msg_origin: str
    ) -> None:
        """Actively expire a pending confirmation without waiting for a reply."""
        delay = max(0.0, deadline - time.monotonic())
        if delay:
            await asyncio.sleep(delay)
        async with self._lock:
            if not (self._waiting_confirm and self._confirm_deadline == deadline):
                return
            self._clear_confirmation_locked()
        await self._send_private_text(
            unified_msg_origin, "本轮B站Cookie协助请求已超时。"
        )

    @staticmethod
    def _create_local_qr_code(login_url: str) -> str:
        """Render a login QR code locally without disclosing its token."""
        fd, qr_path = tempfile.mkstemp(prefix="astrbot_bilibili_qr_", suffix=".png")
        os.close(fd)
        try:
            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=8,
                border=4,
            )
            qr.add_data(login_url)
            qr.make(fit=True)
            qr.make_image(fill_color="black", back_color="white").save(qr_path)
            return qr_path
        except Exception:
            try:
                os.remove(qr_path)
            except OSError:
                pass
            raise

    async def _send_local_login_qr(
        self, event: AstrMessageEvent, login_url: str
    ) -> None:
        """Send a locally rendered QR image and always remove its temp file."""
        qr_path = await asyncio.to_thread(self._create_local_qr_code, login_url)
        try:
            chain = [
                Plain(
                    "请使用哔哩哔哩客户端扫描下方二维码完成登录。\n"
                    "若当前平台无法显示图片，也可在管理员设备打开：\n"
                    f"{login_url}"
                ),
                Image.fromFileSystem(qr_path),
            ]
            await event.send(event.chain_result(chain))
        except Exception as image_error:
            logger.warning(
                "[bilibili] 发送本地登录二维码失败，回退为登录链接: "
                f"{type(image_error).__name__}"
            )
            try:
                await event.send(
                    event.plain_result(
                        "当前平台无法发送B站登录二维码，请在管理员设备打开：\n"
                        f"{login_url}"
                    )
                )
            except Exception as fallback_error:
                raise fallback_error from image_error
        finally:
            try:
                os.remove(qr_path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.warning(f"[bilibili] 清理临时登录二维码失败: {exc}")

    async def _poll_login_and_notify(
        self, auth_runtime: Any, qrcode_key: str, unified_msg_origin: str
    ) -> None:
        """异步轮询登录状态并向管理员反馈结果。"""
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    result = await auth_runtime.poll_login_until_complete(
                        session=session,
                        qrcode_key=qrcode_key,
                        timeout_seconds=min(
                            self.reply_timeout_seconds, self.QR_CODE_TTL_SECONDS
                        ),
                    )
            except Exception as exc:
                logger.warning(
                    f"[bilibili] 管理员协助登录轮询失败: {type(exc).__name__}"
                )
                await self._send_private_text(
                    unified_msg_origin, "B站登录轮询失败，请稍后重试。"
                )
                return

            status = result.get("status")
            if status == "success":
                await self._send_private_text(
                    unified_msg_origin, "B站扫码登录成功，Cookie已更新。"
                )
                return

            if status == "expired":
                await self._send_private_text(
                    unified_msg_origin, "B站二维码已过期，本轮协助登录结束。"
                )
                return

            await self._send_private_text(
                unified_msg_origin, "B站扫码登录超时，本轮协助登录结束。"
            )
        finally:
            self._login_in_progress = False
