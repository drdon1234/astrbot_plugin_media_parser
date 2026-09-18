"""抖音 Web 详情接口传输层。"""

# 该接口并非公开稳定 API，所有易变参数和会话状态集中在本模块，解析器只消费
# 经过目标作品 ID 校验的数据。签名算法见 :mod:`sign`。

import asyncio
import json
import time
from dataclasses import dataclass
from enum import Enum
from http.cookies import CookieError, SimpleCookie
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import aiohttp

from ....logger import logger

from .sign import generate_abogus, generate_browser_fingerprint


DOUYIN_WEB_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
)
DOUYIN_DETAIL_API = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
DOUYIN_TTWID_URL = "https://ttwid.bytedance.com/ttwid/union/register/"
DOUYIN_OPEN_ORIGIN = "https://open.douyin.com"
DOUYIN_REFERER = "https://www.douyin.com/"
DEFAULT_TTWID_TTL = 6 * 60 * 60
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)
DETAIL_MAX_ATTEMPTS = 4
DETAIL_RETRY_BASE_DELAY = 0.25
IDENTITY_REFRESH_FAILURE_COOLDOWN = 1.0


class _DetailRetryAction(Enum):
    """详情请求失败后的处理动作。"""

    STOP = "stop"
    RETRY = "retry"
    RESIGN = "resign"
    IDENTITY_REJECTED = "identity_rejected"


@dataclass(frozen=True)
class _WebIdentity:
    """一次性发布的 Web 会话身份快照。"""

    ttwid: str
    browser_fp: str
    expires_at: float
    generation: int


class DouyinWebClient:
    """管理 Web API 的签名请求和有界生命周期身份。"""

    def __init__(self) -> None:
        self._identity: Optional[_WebIdentity] = None
        self._identity_generation = 0
        self._identity_lock = asyncio.Lock()
        self._failed_refresh_generation = -1
        self._failed_refresh_until = 0.0

    @staticmethod
    def _build_params(item_id: str) -> Dict[str, Any]:
        # 仅发送当前接口必需的稳定标识，避免复制浏览器版本等易失参数。
        return {
            "device_platform": "webapp",
            "aid": "6383",
            "channel": "channel_pc_web",
            "aweme_id": str(item_id),
        }

    @staticmethod
    def _build_open_params(item_id: str) -> Dict[str, str]:
        """构造开放平台请求上下文所需的最小详情参数。"""
        return {
            "aweme_id": str(item_id),
            "aid": "6383",
        }

    @staticmethod
    def _registration_payload() -> Dict[str, Any]:
        return {
            "region": "cn",
            "aid": 1768,
            "needFid": False,
            "service": "www.ixigua.com",
            "migrate_info": {"ticket": "", "source": "node"},
            "cbUrlProtocol": "https",
            "union": True,
        }

    @staticmethod
    def _parse_ttwid(response: aiohttp.ClientResponse) -> tuple[str, int]:
        morsel = response.cookies.get("ttwid")
        if morsel is not None and morsel.value:
            try:
                max_age = int(morsel["max-age"] or 0)
            except (TypeError, ValueError):
                max_age = 0
            return morsel.value, max_age

        for header in response.headers.getall("Set-Cookie", []):
            cookie = SimpleCookie()
            try:
                cookie.load(header)
            except CookieError:
                continue
            morsel = cookie.get("ttwid")
            if morsel is None or not morsel.value:
                continue
            try:
                max_age = int(morsel["max-age"] or 0)
            except (TypeError, ValueError):
                max_age = 0
            return morsel.value, max_age
        return "", 0

    def _current_identity(self) -> Optional[_WebIdentity]:
        """返回当前有效的 Web 身份快照。"""
        identity = self._identity
        if identity is not None and time.monotonic() < identity.expires_at:
            return identity
        return None

    def _record_refresh_failure(
        self,
        force_refresh: bool,
        stale_generation: int,
    ) -> None:
        """短期记住同代刷新失败，避免并发调用串行重复注册。"""
        if not force_refresh:
            return
        self._failed_refresh_generation = stale_generation
        self._failed_refresh_until = (
            time.monotonic() + IDENTITY_REFRESH_FAILURE_COOLDOWN
        )

    async def _get_identity(
        self,
        session: aiohttp.ClientSession,
        *,
        force_refresh: bool = False,
        stale_generation: int = -1,
    ) -> Optional[_WebIdentity]:
        current_identity = self._current_identity()
        if not force_refresh and current_identity is not None:
            return current_identity

        async with self._identity_lock:
            current_identity = self._current_identity()
            if not force_refresh and current_identity is not None:
                return current_identity
            if force_refresh:
                # 另一个协程已经替换了本次失败使用的令牌时直接复用，
                # 避免并发失败触发串行重复注册。
                if (
                    current_identity is not None
                    and current_identity.generation != stale_generation
                ):
                    return current_identity
                if (
                    self._failed_refresh_generation == stale_generation
                    and time.monotonic() < self._failed_refresh_until
                ):
                    return None

            headers = {
                "User-Agent": DOUYIN_WEB_USER_AGENT,
                "Content-Type": "application/json; charset=utf-8",
            }
            try:
                async with session.post(
                    DOUYIN_TTWID_URL,
                    json=self._registration_payload(),
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                ) as response:
                    if response.status >= 400:
                        self._record_refresh_failure(
                            force_refresh,
                            stale_generation,
                        )
                        return None
                    await response.read()
                    ttwid, max_age = self._parse_ttwid(response)
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError):
                self._record_refresh_failure(
                    force_refresh,
                    stale_generation,
                )
                return None

            if not ttwid:
                self._record_refresh_failure(
                    force_refresh,
                    stale_generation,
                )
                return None
            ttl = max_age if max_age > 0 else DEFAULT_TTWID_TTL
            # 即使服务端给出很长 Max-Age，也定期刷新逆向接口会话状态。
            ttl = min(ttl, DEFAULT_TTWID_TTL)
            self._identity_generation += 1
            identity = _WebIdentity(
                ttwid=ttwid,
                browser_fp=generate_browser_fingerprint(),
                expires_at=time.monotonic() + max(ttl, 60),
                generation=self._identity_generation,
            )
            self._identity = identity
            self._failed_refresh_generation = -1
            self._failed_refresh_until = 0.0
            return identity

    @staticmethod
    def _contains_target(data: Dict[str, Any], item_id: str) -> bool:
        candidates = []
        detail = data.get("aweme_detail")
        if isinstance(detail, dict):
            candidates.append(detail)
        for key in ("aweme_details", "aweme_list", "item_list"):
            value = data.get(key)
            if isinstance(value, list):
                candidates.extend(item for item in value if isinstance(item, dict))
        return any(
            str(item.get("aweme_id") or item.get("id") or "") == str(item_id)
            for item in candidates
        )

    async def _fetch_open_detail(
        self,
        session: aiohttp.ClientSession,
        item_id: str,
    ) -> Optional[Dict[str, Any]]:
        """使用开放平台请求上下文获取目标作品详情。"""
        headers = {
            "User-Agent": DOUYIN_WEB_USER_AGENT,
            "Origin": DOUYIN_OPEN_ORIGIN,
            "Referer": f"{DOUYIN_OPEN_ORIGIN}/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        try:
            async with session.get(
                DOUYIN_DETAIL_API,
                params=self._build_open_params(item_id),
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            ) as response:
                if response.status >= 400:
                    return None
                body = await response.text()
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

        if not body or not body.lstrip().startswith("{"):
            return None
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        if data.get("status_code") not in (None, 0, "0"):
            return None
        if not self._contains_target(data, item_id):
            return None
        return data

    async def _request_once(
        self,
        session: aiohttp.ClientSession,
        item_id: str,
        referer: str,
        identity: _WebIdentity,
    ) -> tuple[Optional[Dict[str, Any]], _DetailRetryAction]:
        """返回详情数据及失败后的处理动作。"""
        params = self._build_params(item_id)
        param_string = urlencode(params)
        signature = generate_abogus(
            param_string,
            body="",
            user_agent=DOUYIN_WEB_USER_AGENT,
            options=[0, 1, 8],
            fp=identity.browser_fp,
        )
        url = f"{DOUYIN_DETAIL_API}?{param_string}&a_bogus={signature}"
        headers = {
            "User-Agent": DOUYIN_WEB_USER_AGENT,
            "Referer": referer or DOUYIN_REFERER,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Cookie": f"ttwid={identity.ttwid}",
        }
        try:
            async with session.get(
                url,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            ) as response:
                if response.status == 401:
                    return None, _DetailRetryAction.IDENTITY_REJECTED
                if response.status == 403:
                    body = await response.text()
                    normalized_body = body.lower()
                    if (
                        "uifid" in normalized_body
                        and "not found" in normalized_body
                    ):
                        return None, _DetailRetryAction.IDENTITY_REJECTED
                    return None, _DetailRetryAction.RESIGN
                if response.status == 408 or response.status >= 500:
                    return None, _DetailRetryAction.RETRY
                if response.status >= 400:
                    return None, _DetailRetryAction.STOP
                body = await response.text()
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None, _DetailRetryAction.RETRY
        if not body or not body.lstrip().startswith("{"):
            return None, _DetailRetryAction.RESIGN
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return None, _DetailRetryAction.RESIGN
        if not isinstance(data, dict):
            return None, _DetailRetryAction.RESIGN
        status_code = data.get("status_code")
        if status_code not in (None, 0, "0"):
            return None, _DetailRetryAction.STOP
        if not self._contains_target(data, item_id):
            return None, _DetailRetryAction.RESIGN
        return data, _DetailRetryAction.STOP

    async def _fetch_signed_detail(
        self,
        session: aiohttp.ClientSession,
        item_id: str,
        referer: str = "",
    ) -> Optional[Dict[str, Any]]:
        """通过签名会话有界重试详情请求。"""
        identity = await self._get_identity(session)
        if identity is None:
            identity = await self._get_identity(
                session,
                force_refresh=True,
            )
        if identity is None:
            return None

        identity_refreshed = False
        session_failure_count = 0
        transient_retry_used = False
        for attempt in range(DETAIL_MAX_ATTEMPTS):
            data, retry_action = await self._request_once(
                session,
                item_id,
                referer,
                identity,
            )

            if data is not None or retry_action is _DetailRetryAction.STOP:
                return data
            if retry_action is _DetailRetryAction.RETRY:
                session_failure_count = 0
                if transient_retry_used:
                    return None
                transient_retry_used = True
            elif retry_action is _DetailRetryAction.IDENTITY_REJECTED:
                session_failure_count += 1
            else:
                session_failure_count = 0
            if attempt + 1 >= DETAIL_MAX_ATTEMPTS:
                break

            if (
                retry_action is _DetailRetryAction.IDENTITY_REJECTED
                and session_failure_count >= 2
                and not identity_refreshed
            ):
                refreshed_identity = await self._get_identity(
                    session,
                    force_refresh=True,
                    stale_generation=identity.generation,
                )
                identity_refreshed = True
                session_failure_count = 0
                if refreshed_identity is not None:
                    identity = refreshed_identity
                else:
                    logger.debug("刷新抖音Web身份失败，复用当前身份继续重试")

            retry_delay = DETAIL_RETRY_BASE_DELAY * (2 ** attempt)
            logger.debug(
                f"抖音Web详情请求未返回有效数据，{retry_delay:.2f}秒后重试 "
                f"({attempt + 2}/{DETAIL_MAX_ATTEMPTS})"
            )
            await asyncio.sleep(retry_delay)

        logger.debug(
            f"抖音Web详情请求达到最大尝试次数: {DETAIL_MAX_ATTEMPTS}"
        )
        return None

    async def fetch_detail(
        self,
        session: aiohttp.ClientSession,
        item_id: str,
        referer: str = "",
    ) -> Optional[Dict[str, Any]]:
        """获取目标作品详情。

        Args:
            session: HTTP 会话
            item_id: 目标作品 ID
            referer: 签名会话回退路径使用的来源页

        Returns:
            包含目标作品的详情响应，所有路径均失败时返回 None
        """
        data = await self._fetch_open_detail(session, item_id)
        if data is not None:
            return data

        logger.debug(
            f"开放平台请求上下文未返回有效抖音详情，回退签名会话: {item_id}"
        )
        return await self._fetch_signed_detail(
            session,
            item_id,
            referer=referer,
        )
