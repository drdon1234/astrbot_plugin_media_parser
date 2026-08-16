"""B站管理员 Cookie 更新指令的回归测试。

覆盖管理员在私聊/群聊中触发“B站更新Cookie”指令的各种场景，
以及未配置管理员、非管理员、功能未启用时的提示行为。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.interaction.platform.bilibili.cookie_assist import (
    BilibiliAdminCookieAssistManager,
)


class FakeEvent:
    """模拟 AstrMessageEvent 的最小实现（duck typing）。"""

    def __init__(
        self,
        *,
        message_str: str,
        is_private: bool,
        sender_id: str,
        post_type: str = "message",
        unified_msg_origin: str = "aiocqhttp:test:session-1",
        is_astrbot_admin: bool = False,
    ):
        self._message_str = message_str
        self._is_private = is_private
        self._sender_id = sender_id
        self._origin = unified_msg_origin
        self._is_astrbot_admin = is_astrbot_admin
        self.message_obj = SimpleNamespace(
            raw_message=SimpleNamespace(get=lambda key: {"post_type": post_type}.get(key)),
        )
        self.sent = []
        self.chain_sent = []
        self.result_type = None

    @property
    def message_str(self) -> str:
        return self._message_str

    @property
    def unified_msg_origin(self) -> str:
        return self._origin

    def is_private_chat(self) -> bool:
        return self._is_private

    def get_sender_id(self) -> str:
        return self._sender_id

    def is_admin(self) -> bool:
        """模拟 AstrBot 全局管理员(admins_id 命中, waking_check 设置 role=admin)。"""
        return self._is_astrbot_admin

    def plain_result(self, text: str):
        self.result_type = "plain"
        return text

    def chain_result(self, chain):
        self.result_type = "chain"
        return chain

    async def send(self, content):
        self.sent.append(content)

    def get_messages(self):
        return []


def make_manager(
    *,
    admin_id: str = "admin123",
    enabled: bool = True,
    command: str = "B站更新Cookie",
):
    context = MagicMock()
    context.send_message = AsyncMock()
    return BilibiliAdminCookieAssistManager(
        context=context,
        admin_id=admin_id,
        enabled=enabled,
        reply_timeout_minutes=10,
        request_cooldown_minutes=1,
        command=command,
    )


@pytest.fixture
def manager():
    return make_manager()


def run(coro):
    return asyncio.run(coro)


class TestHandleAdminCommand:
    async def test_admin_private_message_triggers_login(self, manager):
        """管理员私聊发送指令应触发登录流程。"""
        event = FakeEvent(message_str="B站更新Cookie", is_private=True, sender_id="admin123")
        with patch.object(manager, "_start_login_flow", new=AsyncMock()) as start:
            handled = await manager.handle_admin_command(event, auth_runtime=object())
        assert handled is True
        start.assert_awaited_once()
        assert event.sent == []  # 二维码由 _start_login_flow 发送

    async def test_admin_group_message_triggers_login(self, manager):
        """管理员在群聊中发送指令也应触发登录流程（回归：修复前静默失败）。"""
        event = FakeEvent(
            message_str="B站更新Cookie",
            is_private=False,
            sender_id="admin123",
            unified_msg_origin="aiocqhttp:test:group-1",
        )
        with patch.object(manager, "_start_login_flow", new=AsyncMock()) as start:
            handled = await manager.handle_admin_command(event, auth_runtime=object())
        assert handled is True
        start.assert_awaited_once()

    async def test_admin_message_with_at_prefix(self, manager):
        """群里 @机器人 后指令文本不受影响（@ 不进入 message_str）。"""
        event = FakeEvent(message_str="B站更新Cookie", is_private=False, sender_id="admin123")
        with patch.object(manager, "_start_login_flow", new=AsyncMock()) as start:
            handled = await manager.handle_admin_command(event, auth_runtime=object())
        assert handled is True
        start.assert_awaited_once()

    async def test_non_admin_gets_clear_hint(self, manager):
        """非管理员发送指令应给出明确提示并消费消息。"""
        event = FakeEvent(message_str="B站更新Cookie", is_private=False, sender_id="other_user")
        handled = await manager.handle_admin_command(event, auth_runtime=object())
        assert handled is True  # 消息被消费，不会流向 LLM
        assert event.sent
        assert "仅管理员" in event.sent[0]

    async def test_missing_admin_id_gets_hint(self):
        """未配置管理员 ID 时应给出配置指引而非静默失败(用户真实场景: admin_id 空着等待用户填写)。"""
        manager = make_manager(admin_id="", enabled=False)
        event = FakeEvent(message_str="B站更新Cookie", is_private=True, sender_id="anyone")
        handled = await manager.handle_admin_command(event, auth_runtime=object())
        assert handled is True  # 消息被消费,不会流向 LLM
        assert event.sent
        assert "管理员 ID" in event.sent[0]

    async def test_astrbot_global_admin_without_plugin_admin_id(self):
        """AstrBot 全局管理员(admins_id 配置,插件 admin_id 留空)应能触发指令。

        用户真实场景: 在 AstrBot 全局配置了管理员, 但插件“权限控制→管理员 ID”
        留空等待用户填写; 此时指令应被插件消费而非流向 LLM。
        """
        manager = make_manager(admin_id="", enabled=True)
        event = FakeEvent(
            message_str="B站更新Cookie",
            is_private=True,
            sender_id="astrbot_admin_qq",
            is_astrbot_admin=True,
        )
        with patch.object(manager, "_start_login_flow", new=AsyncMock()) as start:
            handled = await manager.handle_admin_command(event, auth_runtime=object())
        assert handled is True
        start.assert_awaited_once()  # 全局管理员身份被识别,直接进入登录流程

    async def test_astrbot_global_admin_group_message(self):
        """AstrBot 全局管理员在群聊中发送指令也应触发(私聊/群聊均可)。"""
        manager = make_manager(admin_id="", enabled=True)
        event = FakeEvent(
            message_str="B站更新Cookie",
            is_private=False,
            sender_id="astrbot_admin_qq",
            is_astrbot_admin=True,
            unified_msg_origin="aiocqhttp:test:group-1",
        )
        with patch.object(manager, "_start_login_flow", new=AsyncMock()) as start:
            handled = await manager.handle_admin_command(event, auth_runtime=object())
        assert handled is True
        start.assert_awaited_once()

    async def test_disabled_feature_gets_hint(self):
        """功能未启用时给出提示。"""
        manager = make_manager(enabled=False)
        event = FakeEvent(message_str="B站更新Cookie", is_private=True, sender_id="admin123")
        handled = await manager.handle_admin_command(event, auth_runtime=object())
        assert handled is True
        assert event.sent
        assert "未启用" in event.sent[0]

    async def test_different_text_not_consumed(self, manager):
        """非指令文本不应被消费。"""
        event = FakeEvent(message_str="你好", is_private=True, sender_id="admin123")
        handled = await manager.handle_admin_command(event, auth_runtime=object())
        assert handled is False
        assert event.sent == []

    async def test_custom_command_text(self):
        """自定义指令文本生效。"""
        manager = make_manager(command="刷新B站Cookie")
        event = FakeEvent(message_str="刷新B站Cookie", is_private=True, sender_id="admin123")
        with patch.object(manager, "_start_login_flow", new=AsyncMock()) as start:
            handled = await manager.handle_admin_command(event, auth_runtime=object())
        assert handled is True
        start.assert_awaited_once()

    async def test_notice_event_ignored(self, manager):
        """Notice（如输入状态）不应被当作指令处理。"""
        event = FakeEvent(
            message_str="",
            is_private=True,
            sender_id="admin123",
            post_type="notice",
        )
        handled = await manager.handle_admin_command(event, auth_runtime=object())
        assert handled is False


class TestHandleAdminReply:
    async def test_private_admin_reply_confirm(self, manager):
        """管理员私聊回复“确定”应进入登录流程（自动协助确认路径）。"""
        # 模拟已存在待确认请求
        manager._waiting_confirm = True
        manager._confirm_deadline = 9999999999.0
        event = FakeEvent(message_str="确定", is_private=True, sender_id="admin123")
        with patch.object(manager, "_start_login_flow", new=AsyncMock()) as start:
            handled = await manager.handle_admin_reply(event, auth_runtime=object())
        assert handled is True
        start.assert_awaited_once()

    async def test_group_reply_not_confirmed(self, manager):
        """群聊中的回复不应消费自动确认会话（保持私聊确认机制）。"""
        manager._waiting_confirm = True
        manager._confirm_deadline = 9999999999.0
        event = FakeEvent(message_str="确定", is_private=False, sender_id="admin123")
        handled = await manager.handle_admin_reply(event, auth_runtime=object())
        assert handled is False


class TestLoginFlow:
    async def test_start_login_flow_sends_qr(self):
        """完整登录流程：生成二维码并开始轮询。"""
        manager = make_manager()
        auth_runtime = MagicMock()
        auth_runtime.generate_login_payload = AsyncMock(
            return_value={"login_url": "https://passport.bilibili.com/x/passport-login/web/qrcode/generate?x=1", "qrcode_key": "abc"}
        )
        event = FakeEvent(
            message_str="B站更新Cookie",
            is_private=True,
            sender_id="admin123",
            unified_msg_origin="aiocqhttp:test:session-1",
        )
        with patch.object(
            manager,
            "_send_local_login_qr",
            new=AsyncMock(),
        ) as send_qr, patch.object(
            manager,
            "_poll_login_and_notify",
            new=AsyncMock(),
        ) as poll:
            await manager._start_login_flow(event, auth_runtime)
            # 轮询任务由 _new_task 通过 create_task 调度，需让事件循环执行一次
            await asyncio.sleep(0)
        send_qr.assert_awaited_once()
        poll.assert_awaited_once()

    async def test_poll_login_success_resets_flag_and_notifies(self):
        """真实轮询成功路径：复位登录标志并向管理员发送成功消息。"""
        manager = make_manager()
        auth_runtime = MagicMock()
        auth_runtime.poll_login_until_complete = AsyncMock(
            return_value={"status": "success"}
        )
        with patch.object(
            manager, "_send_private_text", new=AsyncMock()
        ) as notify:
            await manager._poll_login_and_notify(
                auth_runtime=auth_runtime,
                qrcode_key="key-1",
                unified_msg_origin="aiocqhttp:test:session-1",
            )
        assert manager._login_in_progress is False
        notify.assert_awaited_once_with(
            "aiocqhttp:test:session-1", "B站扫码登录成功，Cookie已更新。"
        )

    async def test_start_login_flow_no_runtime(self):
        """auth_runtime 未初始化时给出明确提示。"""
        manager = make_manager()
        event = FakeEvent(message_str="B站更新Cookie", is_private=True, sender_id="admin123")
        await manager._start_login_flow(event, auth_runtime=None)
        assert event.sent
        assert "未初始化" in event.sent[0]


def test_is_admin_event_semantics():
    """_is_admin_event 只校验身份，_is_admin_private_event 额外要求私聊。"""
    manager = make_manager()

    group_event = FakeEvent(message_str="x", is_private=False, sender_id="admin123")
    private_event = FakeEvent(message_str="x", is_private=True, sender_id="admin123")
    stranger_group = FakeEvent(message_str="x", is_private=False, sender_id="other")

    assert manager._is_admin_event(group_event) is True
    assert manager._is_admin_event(stranger_group) is False
    assert manager._is_admin_private_event(group_event) is False
    assert manager._is_admin_private_event(private_event) is True
