"""抖音 Web 详情接口传输层。

该接口并非公开稳定 API，所有易变参数和会话状态集中在本模块，解析器只消费
经过目标作品 ID 校验的数据。签名算法见 :mod:`douyin_sign`。
"""

import asyncio
import json
import time
from http.cookies import SimpleCookie
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import aiohttp

from ...logger import logger
from .douyin_sign import generate_abogus


DOUYIN_WEB_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
)
DOUYIN_DETAIL_API = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
DOUYIN_TTWID_URL = "https://ttwid.bytedance.com/ttwid/union/register/"
DOUYIN_REFERER = "https://www.douyin.com/"
DEFAULT_TTWID_TTL = 6 * 60 * 60
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)


class DouyinWebClient:
    """管理 Web API 的签名请求和有界生命周期 ttwid。"""

    def __init__(self) -> None:
        self._ttwid = ""
        self._ttwid_expires_at = 0.0
        self._ttwid_lock = asyncio.Lock()

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
            except Exception:
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

    def _has_valid_ttwid(self) -> bool:
        return bool(self._ttwid and time.monotonic() < self._ttwid_expires_at)

    async def _get_ttwid(
        self,
        session: aiohttp.ClientSession,
        *,
        force_refresh: bool = False,
        stale_ttwid: str = "",
    ) -> str:
        if not force_refresh and self._has_valid_ttwid():
            return self._ttwid

        async with self._ttwid_lock:
            if not force_refresh and self._has_valid_ttwid():
                return self._ttwid
            if force_refresh:
                # 另一个协程已经替换了本次失败使用的令牌时直接复用，
                # 避免并发失败触发串行重复注册。
                if self._has_valid_ttwid() and (
                    not stale_ttwid or self._ttwid != stale_ttwid
                ):
                    return self._ttwid
                self._ttwid = ""
                self._ttwid_expires_at = 0.0

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
                        return ""
                    await response.read()
                    ttwid, max_age = self._parse_ttwid(response)
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError):
                return ""

            if not ttwid:
                return ""
            ttl = max_age if max_age > 0 else DEFAULT_TTWID_TTL
            # 即使服务端给出很长 Max-Age，也定期刷新逆向接口会话状态。
            ttl = min(ttl, DEFAULT_TTWID_TTL)
            self._ttwid = ttwid
            self._ttwid_expires_at = time.monotonic() + max(ttl, 60)
            return self._ttwid

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

    async def _request_once(
        self,
        session: aiohttp.ClientSession,
        item_id: str,
        referer: str,
        ttwid: str,
    ) -> tuple[Optional[Dict[str, Any]], bool]:
        """返回 ``(数据, 是否值得刷新会话后重试)``。"""
        params = self._build_params(item_id)
        param_string = urlencode(params)
        signature = generate_abogus(
            param_string,
            body="",
            user_agent=DOUYIN_WEB_USER_AGENT,
            options=[0, 1, 8],
        )
        url = f"{DOUYIN_DETAIL_API}?{param_string}&a_bogus={signature}"
        headers = {
            "User-Agent": DOUYIN_WEB_USER_AGENT,
            "Referer": referer or DOUYIN_REFERER,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Cookie": f"ttwid={ttwid}",
        }
        try:
            async with session.get(
                url,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            ) as response:
                if response.status in {401, 403}:
                    return None, True
                if response.status >= 400:
                    return None, False
                body = await response.text()
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None, False
        if not body or not body.lstrip().startswith("{"):
            return None, True
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return None, True
        if not isinstance(data, dict):
            return None, True
        status_code = data.get("status_code")
        if status_code not in (None, 0, "0"):
            return None, True
        if not self._contains_target(data, item_id):
            return None, True
        return data, False

    async def fetch_detail(
        self,
        session: aiohttp.ClientSession,
        item_id: str,
        referer: str = "",
    ) -> Optional[Dict[str, Any]]:
        """最多请求两次；只有会话类失败才刷新一次 ttwid。"""
        ttwid = await self._get_ttwid(session)
        if not ttwid:
            ttwid = await self._get_ttwid(session, force_refresh=True)
        if not ttwid:
            return None

        try:
            data, should_refresh = await self._request_once(
                session,
                item_id,
                referer,
                ttwid,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("生成或请求抖音Web详情失败", exc_info=True)
            return None
        if data is not None or not should_refresh:
            return data

        refreshed_ttwid = await self._get_ttwid(
            session,
            force_refresh=True,
            stale_ttwid=ttwid,
        )
        if not refreshed_ttwid:
            return None
        try:
            data, _ = await self._request_once(
                session,
                item_id,
                referer,
                refreshed_ttwid,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("刷新会话后请求抖音Web详情失败", exc_info=True)
            return None
        return data
