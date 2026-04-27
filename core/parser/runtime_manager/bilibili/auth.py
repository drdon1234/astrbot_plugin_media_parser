"""B 站鉴权运行时，管理 Cookie 校验、登录与凭据持久化。"""
import asyncio
import json
import os
import sys
import time
from http.cookies import SimpleCookie
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote, urlparse

import aiohttp

from ....logger import logger

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


async def _read_console_line(prompt: str, timeout_seconds: int) -> str:
    """可取消地读取一行控制台输入，避免占用不可终止的 executor 线程。"""
    timeout = max(1, int(timeout_seconds or 1))
    if os.name == "nt":
        return await _read_console_line_windows(prompt, timeout)
    return await _read_console_line_posix(prompt, timeout)


async def _read_console_line_windows(prompt: str, timeout_seconds: int) -> str:
    """Windows 控制台输入轮询；协程取消时不会留下阻塞线程。"""
    try:
        import msvcrt
    except ImportError:
        logger.warning("[bilibili] 当前环境不支持可取消控制台输入")
        return ""

    print(prompt, end="", flush=True)
    chars = []
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            has_key = msvcrt.kbhit()
        except OSError:
            logger.warning("[bilibili] 当前stdin不是可轮询的Windows控制台")
            print()
            return ""
        while has_key:
            ch = msvcrt.getwch()
            if ch in ("\r", "\n"):
                print()
                return "".join(chars)
            if ch == "\003":
                raise KeyboardInterrupt
            if ch == "\b":
                if chars:
                    chars.pop()
                    print("\b \b", end="", flush=True)
                continue
            if ch in ("\x00", "\xe0"):
                if msvcrt.kbhit():
                    msvcrt.getwch()
            else:
                chars.append(ch)
                print(ch, end="", flush=True)
            try:
                has_key = msvcrt.kbhit()
            except OSError:
                logger.warning("[bilibili] 当前stdin不是可轮询的Windows控制台")
                print()
                return ""
        await asyncio.sleep(0.05)
    print()
    return ""


async def _read_console_line_posix(prompt: str, timeout_seconds: int) -> str:
    """POSIX 控制台输入；使用 add_reader 支持取消。"""
    print(prompt, end="", flush=True)
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    try:
        fd = sys.stdin.fileno()
        loop.add_reader(fd, lambda: (
            None if future.done() else future.set_result(sys.stdin.readline())
        ))
    except (NotImplementedError, OSError, ValueError):
        logger.warning("[bilibili] 当前环境不支持可取消控制台输入")
        print()
        return ""

    try:
        line = await asyncio.wait_for(future, timeout=timeout_seconds)
        return (line or "").strip()
    except asyncio.TimeoutError:
        print()
        return ""
    finally:
        try:
            loop.remove_reader(fd)
        except Exception:
            pass


class BilibiliAuthRuntime:
    """B站登录态运行时管理器。"""

    QRCODE_GENERATE_URL = (
        "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
    )
    QRCODE_POLL_URL = (
        "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
    )
    NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
    _PRIMARY_COOKIE_KEYS = ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5")

    def __init__(
        self,
        enabled: bool,
        configured_cookie: str = "",
        credential_path: str = "",
        local_debug_mode: bool = False
    ):
        """初始化鉴权运行时并准备凭据缓存状态。"""
        self.enabled = enabled
        self._configured_cookie = (configured_cookie or "").strip()
        self.credential_path = credential_path
        self.local_debug_mode = local_debug_mode

        self._runtime_credentials: Dict[str, Any] = {}
        self._runtime_cookie_header: str = ""

        self._last_cookie_fingerprint: str = ""
        self._last_validation_ok: Optional[bool] = None
        self._last_validation_at: float = 0.0
        self._valid_ttl_seconds = 300
        self._invalid_ttl_seconds = 60

        self._cookie_unavailable_reason: str = ""
        self._cookie_unavailable_warned: bool = False
        self._local_prompt_asked: bool = False

        self._load_credentials()

    @property
    def cookie_unavailable_reason(self) -> str:
        """返回当前 Cookie 不可用原因（若存在）。"""
        return self._cookie_unavailable_reason

    def set_configured_cookie(self, cookie_header: str) -> None:
        """更新配置来源的 Cookie 并重置相关缓存。"""
        self._configured_cookie = (cookie_header or "").strip()
        self._reset_validation_cache()

    def mark_cookie_unavailable(self, reason: str) -> None:
        """标记 Cookie 不可用并记录原因。"""
        reason = reason or "cookie_unavailable"
        if self._cookie_unavailable_reason != reason:
            self._local_prompt_asked = False
        self._cookie_unavailable_reason = reason
        if self.enabled and not self._cookie_unavailable_warned:
            reason_text = {
                "missing_cookie": "未配置可用Cookie",
                "cookie_invalid": "Cookie已失效或无效",
            }.get(reason, reason)
            logger.warning(
                f"[bilibili] 已开启Cookie解析，但当前Cookie不可用（{reason_text}），"
                "将回退为无Cookie模式继续解析。"
            )
            self._cookie_unavailable_warned = True

    def _clear_cookie_unavailable_state(self) -> None:
        """清除 Cookie 不可用状态标记。"""
        self._cookie_unavailable_reason = ""
        self._cookie_unavailable_warned = False

    def _reset_validation_cache(self) -> None:
        """重置 Cookie 校验缓存。"""
        self._last_cookie_fingerprint = ""
        self._last_validation_ok = None
        self._last_validation_at = 0.0

    @staticmethod
    def _build_cookie_header(credentials: Dict[str, Any]) -> str:
        """将凭据字典转换为 Cookie 请求头字符串。"""
        keys = BilibiliAuthRuntime._PRIMARY_COOKIE_KEYS
        cookie_parts = []
        for key in keys:
            value = str(credentials.get(key, "") or "").strip()
            if value:
                cookie_parts.append(f"{key}={value}")
        if cookie_parts:
            return "; ".join(cookie_parts)
        return str(credentials.get("cookie_header", "") or "").strip()

    @staticmethod
    def _cookie_fingerprint(cookie_header: str) -> str:
        """生成用于缓存命中的 Cookie 指纹。"""
        if not cookie_header:
            return ""
        return f"{len(cookie_header)}:{hash(cookie_header)}"

    def _load_credentials(self) -> None:
        """从本地持久化文件加载凭据。"""
        if not self.credential_path:
            return
        if not os.path.exists(self.credential_path):
            return
        try:
            with open(self.credential_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._runtime_credentials = data
                self._runtime_cookie_header = self._build_cookie_header(data)
        except Exception as e:
            logger.warning(f"[bilibili] 读取运行时Cookie文件失败: {e}")

    def _save_credentials(self) -> None:
        """将凭据写入本地持久化文件。"""
        if not self.credential_path:
            return
        try:
            parent_dir = os.path.dirname(self.credential_path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            with open(self.credential_path, "w", encoding="utf-8") as f:
                json.dump(self._runtime_credentials, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[bilibili] 保存运行时Cookie文件失败: {e}")

    def _active_cookie(self) -> Tuple[str, str]:
        """返回当前优先使用的 Cookie 字典。"""
        if self._runtime_cookie_header:
            return "runtime", self._runtime_cookie_header
        if self._configured_cookie:
            return "configured", self._configured_cookie
        return "", ""

    async def _validate_cookie(
        self,
        session: aiohttp.ClientSession,
        cookie_header: str
    ) -> Optional[bool]:
        """异步校验 Cookie 的可用性。"""
        headers = {
            "User-Agent": UA,
            "Referer": "https://www.bilibili.com",
            "Origin": "https://www.bilibili.com",
            "Cookie": cookie_header
        }
        try:
            async with session.get(
                self.NAV_URL,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.content_type != "application/json":
                    return None
                data = await resp.json()
        except Exception:
            return None

        if data.get("code") != 0:
            try:
                error_code = int(data.get("code", 0))
            except (TypeError, ValueError):
                error_code = 0
            if error_code == -101:
                return False
            return None
        nav_data = data.get("data") or {}
        if "isLogin" not in nav_data:
            return None
        return bool(nav_data.get("isLogin"))

    async def _validate_cookie_with_cache(
        self,
        session: aiohttp.ClientSession,
        cookie_header: str,
        force: bool = False
    ) -> Optional[bool]:
        """带缓存地异步校验 Cookie 可用性。"""
        fingerprint = self._cookie_fingerprint(cookie_header)
        now = time.time()

        if (
            not force and
            fingerprint and
            fingerprint == self._last_cookie_fingerprint and
            self._last_validation_ok is not None
        ):
            ttl = (
                self._valid_ttl_seconds
                if self._last_validation_ok else
                self._invalid_ttl_seconds
            )
            if now - self._last_validation_at < ttl:
                return self._last_validation_ok

        result = await self._validate_cookie(session, cookie_header)
        if result is not None:
            self._last_cookie_fingerprint = fingerprint
            self._last_validation_ok = result
            self._last_validation_at = now
        return result

    async def get_cookie_header_for_request(
        self,
        session: aiohttp.ClientSession
    ) -> str:
        """获取可直接用于请求的 Cookie 请求头。"""
        if not self.enabled:
            return ""

        source, cookie_header = self._active_cookie()
        if not cookie_header:
            self.mark_cookie_unavailable("missing_cookie")
            return ""

        result = await self._validate_cookie_with_cache(session, cookie_header)
        if result is True:
            self._clear_cookie_unavailable_state()
            return cookie_header
        if result is None:
            return cookie_header

        if source == "runtime":
            self._runtime_credentials = {}
            self._runtime_cookie_header = ""
            self._save_credentials()
            self._reset_validation_cache()

            fallback_cookie = self._configured_cookie
            if fallback_cookie:
                fallback_result = await self._validate_cookie_with_cache(
                    session,
                    fallback_cookie,
                    force=True
                )
                if fallback_result is True:
                    self._clear_cookie_unavailable_state()
                    return fallback_cookie
                if fallback_result is None:
                    return fallback_cookie

        self.mark_cookie_unavailable("cookie_invalid")
        return ""

    async def generate_login_payload(
        self,
        session: aiohttp.ClientSession
    ) -> Dict[str, str]:
        """异步生成扫码登录所需的展示载荷。"""
        headers = {
            "User-Agent": UA,
            "Referer": "https://www.bilibili.com",
            "Origin": "https://www.bilibili.com"
        }
        async with session.get(
            self.QRCODE_GENERATE_URL,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            data = await resp.json()

        if data.get("code") != 0:
            raise RuntimeError(
                f"generate qrcode failed: {data.get('code')} {data.get('message')}"
            )

        payload = data.get("data") or {}
        login_url = str(payload.get("url", "")).strip()
        qrcode_key = str(payload.get("qrcode_key", "")).strip()
        if not login_url or not qrcode_key:
            raise RuntimeError("generate qrcode failed: empty login_url or qrcode_key")

        qr_code_url = (
            "https://api.qrserver.com/v1/create-qr-code/?size=400x400&data="
            f"{quote(login_url, safe='')}"
        )
        return {
            "login_url": login_url,
            "qr_code_url": qr_code_url,
            "qrcode_key": qrcode_key,
            "created_at": str(int(time.time()))
        }

    def _extract_credentials(
        self,
        resp: aiohttp.ClientResponse,
        poll_result: Dict[str, Any]
    ) -> None:
        """从登录接口响应中提取凭据字段。"""
        cookies_dict: Dict[str, str] = {}

        set_cookie_headers = resp.headers.getall("Set-Cookie", [])
        for set_cookie in set_cookie_headers:
            simple_cookie = SimpleCookie()
            simple_cookie.load(set_cookie)
            for key, morsel in simple_cookie.items():
                cookies_dict[key] = morsel.value

        callback_url = str(poll_result.get("url", "")).strip()
        if callback_url:
            parsed = urlparse(callback_url)
            for pair in parsed.query.split("&"):
                if "=" not in pair:
                    continue
                key, value = pair.split("=", 1)
                if (
                    key in self._PRIMARY_COOKIE_KEYS and
                    key not in cookies_dict and
                    value
                ):
                    cookies_dict[key] = value

        refresh_token = str(poll_result.get("refresh_token", "")).strip()
        self._runtime_credentials = {
            "SESSDATA": cookies_dict.get("SESSDATA", ""),
            "bili_jct": cookies_dict.get("bili_jct", ""),
            "DedeUserID": cookies_dict.get("DedeUserID", ""),
            "DedeUserID__ckMd5": cookies_dict.get("DedeUserID__ckMd5", ""),
            "cookie_header": self._build_cookie_header(cookies_dict),
            "refresh_token": refresh_token,
            "login_time": int(time.time())
        }
        self._runtime_cookie_header = self._build_cookie_header(self._runtime_credentials)
        self._clear_cookie_unavailable_state()
        self._local_prompt_asked = False
        self._reset_validation_cache()
        self._save_credentials()

    async def poll_login_until_complete(
        self,
        session: aiohttp.ClientSession,
        qrcode_key: str,
        timeout_seconds: int
    ) -> Dict[str, Any]:
        """轮询登录状态直到完成或超时。"""
        deadline = time.time() + max(1, timeout_seconds)
        headers = {
            "User-Agent": UA,
            "Referer": "https://www.bilibili.com",
            "Origin": "https://www.bilibili.com"
        }

        while time.time() < deadline:
            await asyncio.sleep(2)
            async with session.get(
                self.QRCODE_POLL_URL,
                params={"qrcode_key": qrcode_key},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                poll_data = await resp.json()
                poll_result = poll_data.get("data", {}) or {}
                code = poll_result.get("code")

                if code == 0:
                    self._extract_credentials(resp, poll_result)
                    return {"status": "success"}
                if code == 86038:
                    return {"status": "expired"}
                if code in (86090, 86101):
                    continue

        return {"status": "timeout"}

    async def try_local_blocking_assist_once(
        self,
        session: aiohttp.ClientSession,
        timeout_seconds: int
    ) -> str:
        """本地调试模式：先生成登录链接，再阻塞确认一次是否协助登录。"""
        if not self.local_debug_mode or not self.enabled:
            return await self.get_cookie_header_for_request(session)

        cookie_header = await self.get_cookie_header_for_request(session)
        if cookie_header:
            return cookie_header

        if self._local_prompt_asked:
            return ""
        self._local_prompt_asked = True

        try:
            payload = await self.generate_login_payload(session)
        except Exception as e:
            logger.warning(f"[bilibili] 本地调试生成登录链接失败: {e}")
            return ""

        print("\n" + "=" * 60)
        print("B站Cookie不可用，检测到本地调试模式。")
        print(f"登录链接: {payload['login_url']}")
        print(f"二维码链接: {payload['qr_code_url']}")
        print("=" * 60)

        answer = await _read_console_line(
            "是否协助登录? (y/n): ",
            timeout_seconds=max(1, timeout_seconds)
        )
        answer = (answer or "").strip().lower()
        if answer not in ("y", "yes", "是", "确定"):
            print("已跳过本轮协助登录。")
            return ""

        print("已进入扫码等待...")
        result = await self.poll_login_until_complete(
            session,
            payload["qrcode_key"],
            timeout_seconds=max(1, timeout_seconds)
        )

        if result.get("status") == "success":
            print("B站登录成功，Cookie已更新。")
            cookie_header = await self.get_cookie_header_for_request(session)
            return cookie_header

        print(f"B站扫码未完成，状态: {result.get('status')}")
        return ""

