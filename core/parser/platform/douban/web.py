"""豆瓣匿名请求与公开媒体访客校验，隔离内容会话并限制请求范围。"""

import asyncio
import hashlib
import json
import re
import time
from html.parser import HTMLParser
from http.cookies import SimpleCookie
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import aiohttp
from yarl import URL

from ....logger import logger

from ....types import MediaMetadata


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
PAGE_HOSTS = frozenset({
    "douban.com", "www.douban.com", "m.douban.com", "movie.douban.com",
    "book.douban.com", "music.douban.com", "read.douban.com", "sec.douban.com",
    "dou.bz", "www.dou.bz", "doubanurl.cn", "www.doubanurl.cn",
    "static.arkread.com",
})
MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_REDIRECTS = 5


def _checked_url(url: str) -> str:
    """只允许豆瓣已知页面与自有静态资源，阻止非预期跳转。"""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if (
            parts.scheme not in {"https", "http"}
            or parts.username is not None or parts.password is not None
            or parts.port not in {None, 80, 443}
            or any(ord(char) < 32 for char in url)
            or not (host in PAGE_HOSTS or host.endswith(".doubanio.com"))
        ):
            raise ValueError("豆瓣请求地址不在允许范围内")
    except (TypeError, ValueError) as exc:
        raise ValueError("豆瓣请求地址不在允许范围内") from exc
    return url


class _ChallengeForm(HTMLParser):
    """读取已知匿名校验表单，不执行页面脚本。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.action = ""
        self.fields: Dict[str, str] = {}
        self.in_form = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        """仅收集校验表单中的隐藏字段。"""
        values = dict(attrs)
        if tag == "form":
            self.in_form = values.get("id") == "sec"
            if self.in_form:
                self.action = values.get("action") or ""
        if self.in_form and tag == "input" and values.get("name") in {"tok", "cha", "sol", "red"}:
            self.fields[values["name"]] = values.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        """结束表单字段收集。"""
        if tag == "form":
            self.in_form = False


async def _solve_challenge(challenge: str) -> str:
    """有界计算固定四位前缀的匿名校验，并定期让出事件循环。"""
    started = time.monotonic()
    for nonce in range(1, 2000001):
        if hashlib.sha512((challenge + str(nonce)).encode()).digest()[:2] == b"\x00\x00":
            return str(nonce)
        if nonce % 4096 == 0:
            await asyncio.sleep(0)
            if time.monotonic() - started > 5:
                break
    raise RuntimeError("豆瓣匿名校验超出计算限制")


def _media_cookies(source: str) -> Dict[str, str]:
    """静态识别已验证的图片加法校验结构，规则变化时停止处理。"""
    if not all(value in source for value in ("__tst_status=", "EO_Bot_Ssid=", "location.href=location.href.replace")):
        return {}
    values = re.search(
        r"var e=\{WTKkN:(\d{1,12}),bOYDu:(\d{1,12}),dtzqS:function\(a,n\)\{return a\+n\},wyeCN:(\d{1,12})",
        source,
    )
    number = re.search(r't=a\[_0x649a\("0x7"\)\]\(t,(\d{1,16})\)', source)
    if not number:
        number = re.search(r't,(\d{1,16})\);continue;case"4"', source)
    if not values or not number:
        return {}
    return {
        "__tst_status": str(sum(map(int, values.groups()))) + "#",
        "EO_Bot_Ssid": number.group(1),
    }


class DoubanWeb:
    """管理一次解析的匿名会话与按媒体域名隔离的下载凭据。

    Args:
        session: 调用方共享会话，仅向其写入已验证的媒体访客 Cookie。
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.download_session = session
        self.session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self) -> "DoubanWeb":
        """创建独立匿名会话，复用调用方连接池。"""
        self.session = aiohttp.ClientSession(
            connector=self.download_session.connector,
            connector_owner=False,
            headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=25),
            trust_env=False,
        )
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        """关闭匿名会话，保留调用方连接池。"""
        if self.session is not None:
            await self.session.close()

    async def _request(
        self, url: str, method: str = "GET", *, data: Any = None,
        payload: Any = None, headers: Optional[Dict[str, str]] = None,
        max_bytes: int = MAX_BODY_BYTES, prefix_only: bool = False,
    ) -> Tuple[str, str, bytes, str]:
        """逐次验证跳转，并有界读取响应，避免将校验页面当作内容。"""
        if self.session is None:
            raise RuntimeError("豆瓣匿名会话尚未初始化")
        current = _checked_url(url)
        challenge_target = ""
        request_headers = dict(headers or {})
        for _ in range(MAX_REDIRECTS + 1):
            async with self.session.request(
                method, current, data=data, json=payload, headers=request_headers,
                allow_redirects=False,
            ) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location:
                        raise RuntimeError("豆瓣返回了缺少目标的跳转")
                    target = _checked_url(urljoin(current, location))
                    if urlsplit(target).hostname == "sec.douban.com":
                        challenge_target = current
                    if method != "GET":
                        if response.status in {307, 308}:
                            raise RuntimeError("豆瓣提交请求返回了非预期跳转")
                        method, data, payload = "GET", None, None
                    if urlsplit(target).hostname != urlsplit(current).hostname:
                        request_headers = {
                            key: value for key, value in request_headers.items()
                            if key.lower() in {"referer", "range"}
                        }
                    current = target
                    continue
                if response.status not in {200, 206}:
                    raise RuntimeError(f"豆瓣公开内容请求失败（HTTP {response.status}）")
                body = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    body.extend(chunk[:max_bytes - len(body)] if prefix_only else chunk)
                    if prefix_only and len(body) >= max_bytes:
                        break
                    if len(body) > max_bytes:
                        raise RuntimeError("豆瓣响应超出大小限制")
                return current, response.headers.get("Content-Type", ""), bytes(body), challenge_target
        raise RuntimeError("豆瓣链接跳转次数过多")

    async def _page(self, url: str, referer: Optional[str] = None) -> Tuple[str, str]:
        """获取公开页面，并最多完成一次已知匿名计算校验。"""
        headers = {"Referer": _checked_url(referer)} if referer else {}
        current, _, raw, challenge_target = await self._request(url, headers=headers)
        source = raw.decode("utf-8-sig", errors="replace")
        if urlsplit(current).hostname == "sec.douban.com":
            form = _ChallengeForm()
            form.feed(source)
            action = urljoin(current, form.action)
            red = form.fields.get("red", "")
            if (
                action != "https://sec.douban.com/c"
                or set(form.fields) != {"tok", "cha", "sol", "red"}
                or not form.fields["tok"] or not form.fields["cha"]
                or len(form.fields["cha"]) > 1024
                or "SHA-512" not in source
                or not re.search(r"difficulty\s*=\s*4\b", source)
                or URL(_checked_url(red)).with_fragment(None) != URL(challenge_target or url).with_fragment(None)
            ):
                raise RuntimeError("豆瓣匿名校验规则或目标发生变化")
            form.fields["sol"] = await _solve_challenge(form.fields["cha"])
            current, _, raw, _ = await self._request(
                action, "POST", data=form.fields,
                headers={"Referer": current, "Origin": "https://sec.douban.com"},
            )
            source = raw.decode("utf-8-sig", errors="replace")
            if urlsplit(current).hostname == "sec.douban.com":
                raise RuntimeError("豆瓣匿名校验未通过")
        return current, source

    async def get_page(self, url: str, referer: Optional[str] = None) -> Tuple[str, str]:
        """读取公开页面及实际地址，供内容解析器核对跳转后的身份。

        Args:
            url: 豆瓣公开页面地址。
            referer: 对应公开内容页面。

        Returns:
            最终页面地址与页面正文。
        """
        return await self._page(url, referer)

    async def get_text(self, url: str, referer: Optional[str] = None) -> str:
        """读取匿名公开页面或脚本。

        Args:
            url: 豆瓣页面或自有静态资源地址。
            referer: 对应公开内容页面。

        Returns:
            解码后的页面正文。
        """
        _, source = await self.get_page(url, referer)
        return source

    async def get_json(self, url: str, referer: Optional[str] = None) -> Dict[str, Any]:
        """读取匿名 JSON 接口。

        Args:
            url: 豆瓣只读接口地址。
            referer: 对应公开内容页面。

        Returns:
            接口返回的对象。
        """
        payload = json.loads(await self.get_text(url, referer))
        if not isinstance(payload, dict):
            raise ValueError("豆瓣接口返回的数据不是对象")
        return payload

    async def post_json(
        self, url: str, payload: Any, headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """提交豆瓣阅读的只读查询。

        Args:
            url: 阅读查询接口地址。
            payload: 已公开的持久化查询与变量。
            headers: 页面提供的匿名校验请求头。

        Returns:
            接口返回的对象。
        """
        if url != "https://read.douban.com/j/graphql":
            raise ValueError("豆瓣阅读查询地址无效")
        _, _, raw, _ = await self._request(url, "POST", payload=payload, headers=headers)
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("豆瓣阅读接口返回的数据不是对象")
        return result

    async def resolve_url(self, url: str) -> str:
        """展开豆瓣公开短链接。

        Args:
            url: 待展开的豆瓣链接。

        Returns:
            验证跳转范围后的目标地址。
        """
        current, _ = await self.get_page(url)
        return current

    async def _prepare_host(self, url: str) -> None:
        """验证一个图片主机，并将临时访客 Cookie 限定到该主机。"""
        headers = {"Referer": "https://www.douban.com/", "Range": "bytes=0-4095"}
        current, content_type, prefix, _ = await self._request(
            url, headers=headers, max_bytes=4096, prefix_only=True,
        )
        if "text/html" not in content_type:
            return
        cookies = _media_cookies(prefix.decode("utf-8", errors="replace"))
        if not cookies:
            raise RuntimeError("豆瓣图片返回了无法识别的访客校验")
        if not (urlsplit(current).hostname or "").endswith(".doubanio.com"):
            raise RuntimeError("豆瓣图片跳转到了非媒体主机")
        scoped = SimpleCookie()
        for key, value in cookies.items():
            scoped[key] = value
            scoped[key]["path"] = "/"
            scoped[key]["max-age"] = "600"
        self.session.cookie_jar.update_cookies(scoped, response_url=URL(current))
        _, _, verified, _ = await self._request(
            current, headers=headers, max_bytes=32, prefix_only=True,
        )
        if not (
            verified.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a"))
            or (verified.startswith(b"RIFF") and verified[8:12] == b"WEBP")
            or verified[4:12] in {b"ftypavif", b"ftypavis"}
        ):
            raise RuntimeError("豆瓣图片匿名校验后仍未返回可识别图片")
        # 不把 Cookie 填入通用媒体请求头，避免跨主机传递访客凭据。
        for cookie in scoped.values():
            cookie["domain"] = ""
        self.download_session.cookie_jar.update_cookies(scoped, response_url=URL(current))

    async def prepare_media(self, metadata: MediaMetadata) -> None:
        """为图片下载准备按主机隔离的匿名访客状态。

        Args:
            metadata: 当前内容的图片与视频封面元数据。
        """
        hosts: Dict[str, str] = {}
        for field in ("image_urls", "video_cover_urls"):
            for candidates in metadata.get(field, []):
                for url in candidates:
                    try:
                        _checked_url(url)
                        host = urlsplit(url).hostname or ""
                    except (TypeError, ValueError):
                        continue
                    if host.endswith(".doubanio.com"):
                        hosts.setdefault(host, url)
        for url in list(hosts.values())[:8]:
            try:
                await self._prepare_host(url)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, RuntimeError) as exc:
                logger.debug(f"豆瓣图片访客状态准备失败，交由下载器处理：{exc}")
