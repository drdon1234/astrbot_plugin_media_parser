"""B 站鉴权运行时，管理 Cookie 校验、登录与凭据持久化。"""

import asyncio
import hashlib
import json
import os
import tempfile
import time
from http.cookies import CookieError, SimpleCookie
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import aiohttp

from ....logger import logger

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class BilibiliAuthRuntime:
    """B站登录态运行时管理器。"""

    QRCODE_GENERATE_URL = (
        "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
    )
    QRCODE_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
    NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
    _PRIMARY_COOKIE_KEYS = ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5")
    _CALLBACK_COOKIE_KEYS = frozenset((*_PRIMARY_COOKIE_KEYS, "sid"))

    def __init__(
        self,
        enabled: bool,
        configured_cookie: str = "",
        credential_path: str = "",
    ):
        """初始化鉴权运行时并准备凭据缓存状态。"""
        self.enabled = enabled
        self._configured_cookie = (configured_cookie or "").strip()
        self.credential_path = credential_path

        self._runtime_credentials: Dict[str, Any] = {}
        self._runtime_cookie_header: str = ""
        self._credential_revision = 0
        self._credential_lock = asyncio.Lock()

        self._last_cookie_fingerprint: str = ""
        self._last_validation_ok: Optional[bool] = None
        self._last_validation_at: float = 0.0
        self._valid_ttl_seconds = 300
        self._invalid_ttl_seconds = 60

        self._cookie_unavailable_reason: str = ""
        self._cookie_unavailable_warned: bool = False

        self._load_credentials()

    @property
    def cookie_unavailable_reason(self) -> str:
        """返回当前 Cookie 不可用原因（若存在）。"""
        return self._cookie_unavailable_reason

    def mark_cookie_unavailable(self, reason: str) -> None:
        """标记 Cookie 不可用并记录原因。"""
        reason = reason or "cookie_unavailable"
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
        cookies: Dict[str, str] = {}
        stored_cookies = credentials.get("cookies")
        if isinstance(stored_cookies, dict):
            for key, value in stored_cookies.items():
                key_text = str(key or "").strip()
                value_text = str(value or "").strip()
                if key_text and value_text:
                    cookies[key_text] = value_text

        # Keep compatibility with credentials written before the structured
        # cookie map was introduced, while retaining any extra server cookies.
        raw_header = str(credentials.get("cookie_header", "") or "").strip()
        if raw_header:
            parsed_header = SimpleCookie()
            try:
                parsed_header.load(raw_header)
            except (CookieError, ValueError):
                parsed_header = SimpleCookie()
            for key, morsel in parsed_header.items():
                value_text = str(morsel.value or "").strip()
                if key and value_text and key not in cookies:
                    cookies[key] = value_text

        for key in BilibiliAuthRuntime._PRIMARY_COOKIE_KEYS:
            value = str(credentials.get(key, "") or "").strip()
            if value:
                cookies[key] = value

        cookie_parts = []
        priority = {
            key: index for index, key in enumerate(BilibiliAuthRuntime._PRIMARY_COOKIE_KEYS)
        }
        for key in sorted(cookies, key=lambda item: (priority.get(item, 999), item)):
            cookie_parts.append(f"{key}={cookies[key]}")
        if cookie_parts:
            return "; ".join(cookie_parts)
        return raw_header

    @staticmethod
    def _cookie_fingerprint(cookie_header: str) -> str:
        """生成用于缓存命中的 Cookie 指纹。"""
        if not cookie_header:
            return ""
        return hashlib.sha256(cookie_header.encode("utf-8")).hexdigest()

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
                self._credential_revision += 1
        except Exception as e:
            logger.warning(f"[bilibili] 读取运行时Cookie文件失败: {e}")

    def _save_credentials(self) -> None:
        """将凭据写入本地持久化文件。"""
        if not self.credential_path:
            return
        temp_path = ""
        try:
            parent_dir = os.path.dirname(self.credential_path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            fd, temp_path = tempfile.mkstemp(
                prefix=os.path.basename(self.credential_path) + ".",
                suffix=".tmp",
                dir=parent_dir or ".",
            )
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file_obj:
                json.dump(
                    self._runtime_credentials,
                    file_obj,
                    ensure_ascii=False,
                    indent=2,
                )
                file_obj.flush()
                os.fsync(file_obj.fileno())
            try:
                os.chmod(temp_path, 0o600)
            except OSError:
                pass
            os.replace(temp_path, self.credential_path)
            temp_path = ""
        except Exception as e:
            logger.warning(f"[bilibili] 保存运行时Cookie文件失败: {e}")
        finally:
            if temp_path:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def _active_cookie(self) -> Tuple[str, str]:
        """返回当前优先使用的 Cookie 来源和请求头。"""
        if self._runtime_cookie_header:
            return "runtime", self._runtime_cookie_header
        if self._configured_cookie:
            return "configured", self._configured_cookie
        return "", ""

    async def _validate_cookie(
        self, session: aiohttp.ClientSession, cookie_header: str
    ) -> Optional[bool]:
        """异步校验 Cookie 的可用性。"""
        headers = {
            "User-Agent": UA,
            "Referer": "https://www.bilibili.com",
            "Origin": "https://www.bilibili.com",
            "Cookie": cookie_header,
        }
        try:
            async with session.get(
                self.NAV_URL, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
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
        self, session: aiohttp.ClientSession, cookie_header: str, force: bool = False
    ) -> Optional[bool]:
        """带缓存地异步校验 Cookie 可用性。"""
        fingerprint = self._cookie_fingerprint(cookie_header)
        now = time.monotonic()

        if (
            not force
            and fingerprint
            and fingerprint == self._last_cookie_fingerprint
            and self._last_validation_ok is not None
        ):
            ttl = (
                self._valid_ttl_seconds
                if self._last_validation_ok
                else self._invalid_ttl_seconds
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
        self, session: aiohttp.ClientSession
    ) -> str:
        """获取可直接用于请求的 Cookie 请求头。"""
        if not self.enabled:
            return ""

        while True:
            async with self._credential_lock:
                source, cookie_header = self._active_cookie()
                revision = self._credential_revision
            if not cookie_header:
                self.mark_cookie_unavailable("missing_cookie")
                return ""

            result = await self._validate_cookie_with_cache(session, cookie_header)

            async with self._credential_lock:
                current_source, current_cookie = self._active_cookie()
                if (
                    revision != self._credential_revision
                    or source != current_source
                    or cookie_header != current_cookie
                ):
                    # A concurrent QR login installed newer credentials while the
                    # old Cookie was being checked. Validate the new snapshot
                    # instead of returning or deleting stale state.
                    continue

                if result is True:
                    self._clear_cookie_unavailable_state()
                    return cookie_header
                if result is None:
                    return cookie_header

                if source == "runtime":
                    self._runtime_credentials = {}
                    self._runtime_cookie_header = ""
                    self._credential_revision += 1
                    await asyncio.to_thread(self._save_credentials)
                    self._reset_validation_cache()
                    # The next iteration validates the configured fallback, if
                    # present, against its own state snapshot.
                    if not self._configured_cookie:
                        self.mark_cookie_unavailable("cookie_invalid")
                        return ""
                    continue

                self.mark_cookie_unavailable("cookie_invalid")
                return ""

    async def generate_login_payload(
        self, session: aiohttp.ClientSession
    ) -> Dict[str, str]:
        """异步生成扫码登录所需的展示载荷。"""
        headers = {
            "User-Agent": UA,
            "Referer": "https://www.bilibili.com",
            "Origin": "https://www.bilibili.com",
        }
        async with session.get(
            self.QRCODE_GENERATE_URL,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
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

        return {
            "login_url": login_url,
            "qrcode_key": qrcode_key,
            "created_at": str(int(time.time())),
        }

    def _extract_credentials(
        self, resp: aiohttp.ClientResponse, poll_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """从登录接口响应中提取凭据字段。"""
        cookies_dict: Dict[str, str] = {}

        getall = getattr(resp.headers, "getall", None)
        if callable(getall):
            set_cookie_headers = getall("Set-Cookie", [])
        else:
            single_header = resp.headers.get("Set-Cookie", "")
            set_cookie_headers = [single_header] if single_header else []
        for set_cookie in set_cookie_headers:
            simple_cookie = SimpleCookie()
            simple_cookie.load(set_cookie)
            for key, morsel in simple_cookie.items():
                cookies_dict[key] = morsel.value

        callback_url = str(poll_result.get("url", "")).strip()
        if callback_url:
            parsed = urlparse(callback_url)
            callback_query = parse_qs(parsed.query, keep_blank_values=False)
            for key, values in callback_query.items():
                value = str(values[0] if values else "").strip()
                if key in self._CALLBACK_COOKIE_KEYS and key not in cookies_dict and value:
                    cookies_dict[key] = value

        refresh_token = str(poll_result.get("refresh_token", "")).strip()
        credentials = {
            "SESSDATA": cookies_dict.get("SESSDATA", ""),
            "bili_jct": cookies_dict.get("bili_jct", ""),
            "DedeUserID": cookies_dict.get("DedeUserID", ""),
            "DedeUserID__ckMd5": cookies_dict.get("DedeUserID__ckMd5", ""),
            "sid": cookies_dict.get("sid", ""),
            "cookies": dict(cookies_dict),
            "refresh_token": refresh_token,
            "login_time": int(time.time()),
        }
        credentials["cookie_header"] = self._build_cookie_header(credentials)
        if not credentials.get("SESSDATA") or not credentials["cookie_header"]:
            raise RuntimeError("B站扫码登录成功但响应未包含有效 Cookie")
        return credentials

    async def _install_runtime_credentials(self, credentials: Dict[str, Any]) -> None:
        """Atomically install credentials returned by a completed QR login."""
        async with self._credential_lock:
            self._runtime_credentials = dict(credentials)
            self._runtime_cookie_header = self._build_cookie_header(credentials)
            self._credential_revision += 1
            self._clear_cookie_unavailable_state()
            self._reset_validation_cache()
            await asyncio.to_thread(self._save_credentials)

    async def poll_login_until_complete(
        self, session: aiohttp.ClientSession, qrcode_key: str, timeout_seconds: int
    ) -> Dict[str, Any]:
        """轮询登录状态直到完成或超时。"""
        deadline = time.monotonic() + max(1, timeout_seconds)
        headers = {
            "User-Agent": UA,
            "Referer": "https://www.bilibili.com",
            "Origin": "https://www.bilibili.com",
        }

        while time.monotonic() < deadline:
            await asyncio.sleep(2)
            async with session.get(
                self.QRCODE_POLL_URL,
                params={"qrcode_key": qrcode_key},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                poll_data = await resp.json()
                poll_result = poll_data.get("data", {}) or {}
                code = poll_result.get("code")

                if code == 0:
                    credentials = self._extract_credentials(resp, poll_result)
                    await self._install_runtime_credentials(credentials)
                    return {"status": "success"}
                if code == 86038:
                    return {"status": "expired"}
                if code in (86090, 86101):
                    continue

        return {"status": "timeout"}
