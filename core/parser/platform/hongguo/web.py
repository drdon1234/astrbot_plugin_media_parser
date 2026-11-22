"""红果匿名播放与分集弹幕接口的签名请求和响应校验。"""

import json
import re
import secrets
import time
from typing import Any, Dict
from urllib.parse import urlencode

import aiohttp
from yarl import URL

from .sign import build_comment_signature, build_signature


_API_BASE = "https://api5-normal-sinfonlineb.fqnovel.com"
_MODEL_PATH = "/novel/player/video_model/v1/"
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_USER_AGENT = (
    "com.phoenix.read/73532 (Linux; U; Android 16; zh_CN; 25053RT47C; "
    "Build/BP2A.250605.031.A3; Cronet/TTNetVersion:04657795 2026-01-23 "
    "QuicVersion:c67e9834 2025-09-08)"
)


class HongguoEmptyResponse(ValueError):
    """接口响应成功但正文为空。"""


async def _read_json(response: aiohttp.ClientResponse) -> Dict[str, Any]:
    """按解压后的字节数限制响应，只接受 JSON 对象。"""
    if response.status != 200:
        raise ValueError(f"红果接口请求失败（HTTP {response.status}）")
    content = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        content.extend(chunk)
        if len(content) > _MAX_RESPONSE_BYTES:
            raise ValueError("红果接口响应过大")
    if not content:
        raise HongguoEmptyResponse("红果接口返回 HTTP 200，但响应正文为空")
    try:
        result = json.loads(content)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("红果接口响应格式异常") from exc
    if not isinstance(result, dict):
        raise ValueError("红果接口响应格式异常")
    return result


class HongguoClient:
    """在解析器生命周期内复用匿名设备标识。"""

    def __init__(self) -> None:
        self._device_id = str(10**18 + secrets.randbelow(8 * 10**18))
        self._install_id = str(10**18 + secrets.randbelow(8 * 10**18))

    async def request(
        self,
        session: aiohttp.ClientSession,
        path: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """按固定路径选择播放或弹幕签名，校验有限长度的 JSON 响应。

        Args:
            session: 解析器共享的异步网络会话。
            path: 已知的官方播放或分集弹幕接口路径。
            payload: 对应接口的请求参数。

        Returns:
            接口返回的数据对象。

        Raises:
            ValueError: 接口地址、状态或响应格式无效。
        """
        is_comment = re.fullmatch(r"/novel/commentapi/comment/list/[1-9][0-9]{0,31}/v1/", path) is not None
        if path != _MODEL_PATH and not is_comment:
            raise ValueError("红果接口路径无效")
        now = time.time()
        params = {
            "aid": "8662",
            "app_name": "novelread",
            "version_code": "73532",
            "version_name": "7.3.5.32",
            "manifest_version_code": "73532",
            "update_version_code": "73532",
            "channel": "update_64",
            "device_platform": "android",
            "os": "android",
            "ssmix": "a",
            "device_type": "25053RT47C",
            "device_brand": "Redmi",
            "language": "zh",
            "os_api": "36",
            "os_version": "16",
            "resolution": "1280*2772",
            "dpi": "520",
            "ac": "wifi",
            "device_id": self._device_id,
            "iid": self._install_id,
            "_rticket": str(int(now * 1000)),
        }
        query = urlencode(sorted(params.items()))
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        headers = {
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
            "X-XS-From-Web": "0",
            "Sdk-Version": "2",
            "Content-Type": "application/json; charset=utf-8",
            **(build_comment_signature(query, now) if is_comment else build_signature(query, body, now)),
        }
        if is_comment:
            headers.update({"comment-source": "601", "server-channel": "1000"})
        # 保留签名对应的 %2A 等编码，避免 aiohttp 再次编码导致验签失败。
        url = URL(f"{_API_BASE}{path}?{query}", encoded=True)
        async with session.post(
            url,
            data=body,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
            allow_redirects=False,
        ) as response:
            result = await _read_json(response)
        codes = [result[key] for key in ("code", "Code", "status_code") if key in result]
        base_response = result.get("BaseResp")
        if isinstance(base_response, dict) and "StatusCode" in base_response:
            codes.append(base_response["StatusCode"])
        if any(code not in (0, "0") for code in codes):
            raise ValueError("红果接口返回失败状态")
        return result

    async def guest_video_url(
        self, session: aiohttp.ClientSession, video_id: str, headers: Dict[str, str],
    ) -> str:
        """请求官网游客播放器返回的当前分集媒体地址。

        Args:
            session: 已读取官网播放页的会话。
            video_id: 已核验可播放的分集编号。
            headers: 对应播放页的请求头。

        Returns:
            官网播放器下发的媒体地址。
        """
        async with session.post(
            "https://hongguoduanju.com/api/video/protected/fallback-play-info?aid=8662",
            json={"item_id": video_id}, headers={**headers, "X-XS-From-Web": "1"},
            timeout=aiohttp.ClientTimeout(total=30), allow_redirects=False,
        ) as response:
            data = await _read_json(response)
        status = data.get("base_resp")
        code = status.get("StatusCode") if isinstance(status, dict) else None
        if type(code) is not int or code != 0 or not isinstance(data.get("main_url"), str):
            raise ValueError("红果官网未返回可播放的视频")
        return data["main_url"]
