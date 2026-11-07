"""酷安帖子解析器，通过匿名客户端接口提取完整正文与原图。"""

import asyncio
import html
import json
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, List, Optional, Tuple, Union
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import aiohttp

from ....constants import Config
from ....types import MediaMetadata
from ...utils import build_content_blocks, build_request_headers, join_content_text
from ..base import BaseVideoParser
from .sign import build_headers, load_or_create_device


BASE_URL = "https://www.coolapk.com"
FEED_HOSTS = frozenset({"coolapk.com", "www.coolapk.com", "m.coolapk.com"})
FEED_URL_RE = re.compile(
    r"(?<![A-Za-z0-9_.:/@%?=&#-])(?:https?://|//)?(?:www\.|m\.)?coolapk\.com"
    r"[^\s<>\"'`()\[\]{}，。！？；：、（）【】《》「」,;!]*",
    re.IGNORECASE,
)
MAX_PAGE_BYTES = 2 * 1024 * 1024


def _feed_id(value: Any) -> str:
    """只接受酷安官方帖子路径，分享参数不参与帖子身份。"""
    if not isinstance(value, str) or not value.strip():
        return ""
    value = html.unescape(value.strip())
    if re.search(r"[\s\x00-\x1f\x7f\\]", value):
        return ""
    if value.startswith("//"):
        value = "https:" + value
    elif "://" not in value:
        value = "https://" + value
    try:
        parts = urlsplit(value)
        if (
            parts.scheme.lower() not in {"http", "https"}
            or (parts.hostname or "").lower() not in FEED_HOSTS
            or parts.username is not None or parts.password is not None
            or parts.port not in {None, 80, 443}
        ):
            return ""
    except ValueError:
        return ""
    match = re.fullmatch(r"/feed/([1-9][0-9]{0,19})/?", parts.path)
    return match.group(1) if match else ""


def _feed_url(value: str, identity: str) -> str:
    """保留展示动态正文所需的分享参数，统一主机及帖子路径。"""
    canonical = f"{BASE_URL}/feed/{identity}"
    normalized = html.unescape(value.strip())
    if normalized.startswith("//"):
        normalized = "https:" + normalized
    elif "://" not in normalized:
        normalized = "https://" + normalized
    try:
        values = parse_qs(urlsplit(normalized).query, max_num_fields=50).get("s", [])
    except ValueError:
        return canonical
    if values and values[0]:
        return canonical + "?" + urlencode({"s": values[0]})
    return canonical


def _image_url(value: Any) -> str:
    """仅下载酷安官方图床原图，不把表情、头像或未知主机当成配图。"""
    if not isinstance(value, str):
        return ""
    value = html.unescape(value.strip())
    if re.search(r"[\s\x00-\x1f\x7f\\]", value):
        return ""
    if value.startswith("//"):
        value = "https:" + value
    try:
        parts = urlsplit(value)
        if (parts.scheme not in {"http", "https"}
                or parts.hostname != "image.coolapk.com"
                or parts.username is not None or parts.password is not None
                or parts.port not in {None, 80, 443}
                or not parts.path or "/emoticons/" in parts.path):
            return ""
    except ValueError:
        return ""
    return urlunsplit(("https", "image.coolapk.com", parts.path, parts.query, ""))


def _clean_text(value: str) -> str:
    """统一段落空白，不截断正文，也不删除正文中的话题文字。"""
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    value = re.sub(r"[ \t]+\n", "\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


class _BodyParser(HTMLParser):
    """从接口正文保留段落、行内表情说明和原图位置。

    Args:
        images: 与正文块共享的原图列表。
    """

    def __init__(self, images: List[str]) -> None:
        """初始化正文解析状态。

        Args:
            images: 与正文块共享的原图列表。
        """
        super().__init__(convert_charrefs=True)
        self.images = images
        self.parts: List[Union[str, int]] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        """保留可见段落与图片，跳过脚本和样式内容。

        Args:
            tag: 当前开始标签的名称。
            attrs: 当前标签的属性名称和值。
        """
        values = dict(attrs)
        if tag in {"script", "style"}:
            self.hidden += 1
        if self.hidden:
            return
        if tag == "br":
            self.parts.append("\n")
        elif tag in {"p", "div", "li", "h1", "h2", "h3", "blockquote"}:
            self.parts.append("\n\n")
        elif tag == "img":
            source = values.get("src") or values.get("data-src") or ""
            if "feed-emoji" in (values.get("class") or "").split() or "/emoticons/" in source:
                self.parts.append(values.get("alt") or "")
                return
            image = _image_url(source)
            if image:
                if image not in self.images:
                    self.images.append(image)
                self.parts.append(self.images.index(image))

    def handle_endtag(self, tag: str) -> None:
        """保留段落边界。

        Args:
            tag: 当前结束标签的名称。
        """
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and tag in {"p", "div", "li", "h1", "h2", "h3", "blockquote"}:
            self.parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        """追加可见正文文字。

        Args:
            data: 当前标签之间的文本。
        """
        if not self.hidden:
            self.parts.append(data)


class CoolapkParser(BaseVideoParser):
    """酷安匿名图文详情解析，复用已有正文块与原图发送链路。

    Args:
        runtime_dir: 酷安设备码的持久化目录，缺省时使用默认运行时目录。
    """

    def __init__(self, runtime_dir: Optional[str] = None) -> None:
        """在运行时目录持久化匿名设备码，不保存账号凭据。

        Args:
            runtime_dir: 配置管理器提供的酷安运行时目录。
        """
        super().__init__("coolapk")
        self.device_code = load_or_create_device(
            runtime_dir or Config.build_runtime_dir(Config.DEFAULT_CACHE_DIR, "coolapk")
        )
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)

    def can_parse(self, url: str) -> bool:
        """判断是否为酷安帖子链接。

        Args:
            url: 待判断的链接。

        Returns:
            是否为官方帖子主链接。
        """
        return bool(_feed_id(url))

    def extract_links(self, text: str) -> List[str]:
        """从分享消息提取并去重酷安帖子链接。

        Args:
            text: 消息正文。

        Returns:
            保留必要分享参数的规范帖子链接。
        """
        return [url for _, url in self.extract_links_with_positions(text)]

    def extract_links_with_positions(self, text: str) -> List[Tuple[int, str]]:
        """保留原文位置，避免分享链接规范化后打乱消息顺序。

        Args:
            text: 消息正文。

        Returns:
            首次出现的位置及规范帖子链接。
        """
        links = []
        seen = {}
        for match in FEED_URL_RE.finditer(text):
            identity = _feed_id(match.group())
            if not identity:
                continue
            canonical = _feed_url(match.group(), identity)
            if identity not in seen:
                seen[identity] = len(links)
                links.append((match.start(), canonical))
            else:
                index = seen[identity]
                position, previous = links[index]
                if "?s=" in canonical and "?s=" not in previous:
                    links[index] = (position, canonical)
        return links

    def _metadata(self, payload: Any, identity: str) -> MediaMetadata:
        """校验帖子身份和完整性，提取原始正文及有序配图。"""
        if not isinstance(payload, dict):
            raise RuntimeError("酷安返回了无效的帖子详情")
        data = payload.get("data")
        if not isinstance(data, dict) and isinstance(payload.get("responseData"), dict):
            data = payload["responseData"].get("data")
        if not isinstance(data, dict) or str(data.get("id", "")) != identity:
            raise RuntimeError("酷安未返回当前帖子详情，帖子可能已删除或访问受限")
        if data.get("issummary") not in (None, 0, "0", False, ""):
            raise RuntimeError("酷安仅返回摘要，无法读取完整正文")

        images: List[str] = []
        parts: List[Union[str, int]] = []
        raw = data.get("message_raw_output")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = None
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text" and isinstance(item.get("message"), str):
                    body = _BodyParser(images)
                    body.feed(item["message"])
                    body.close()
                    parts.extend(body.parts)
                    parts.append("\n")
                elif item.get("type") == "image":
                    image = _image_url(item.get("url"))
                    if image:
                        if image not in images:
                            images.append(image)
                        parts.append(images.index(image))
        if not _clean_text(join_content_text(parts)):
            body = _BodyParser(images)
            message = data.get("message")
            body.feed(message if isinstance(message, str) else "")
            body.close()
            parts = body.parts

        for field in ("picArr", "pics", "pic"):
            values = data.get(field)
            if isinstance(values, str):
                values = values.split(",")
            if not isinstance(values, list):
                continue
            for value in values:
                image = _image_url(value)
                if image and image not in images:
                    images.append(image)
        # 正文块已提供顺序时补上后置配图，纯图集不虚构正文位置。
        if (isinstance(raw, list) and raw) or any(isinstance(part, int) for part in parts):
            parts.extend(index for index in range(len(images)) if index not in parts)
        desc = _clean_text(join_content_text(parts))
        if not desc and not images:
            raise RuntimeError("酷安帖子没有可读取的正文或配图")
        blocks = build_content_blocks(parts, _clean_text)
        title = ""
        for field in ("message_title", "editor_title", "title"):
            value = data.get(field)
            if not isinstance(value, str) or not value.strip():
                continue
            body = _BodyParser([])
            body.feed(value)
            title = _clean_text(join_content_text(body.parts))
            if title and title != f"{data.get('username', '')}的动态":
                break
            title = ""
        if not title:
            title = desc.split("\n", 1)[0][:120] or f"酷安帖子 {identity}"
        result: MediaMetadata = {
            "url": f"{BASE_URL}/feed/{identity}",
            "platform": self.name,
            "title": title,
            "author": str(data.get("username") or ""),
            "desc": desc,
            "video_urls": [],
            "image_urls": [[image] for image in images],
            "image_headers": build_request_headers(referer=BASE_URL + "/"),
        }
        if blocks:
            result["content_blocks"] = blocks
        for field in ("create_time", "dateline"):
            try:
                stamp = int(data.get(field))
                if stamp > 0:
                    result["timestamp"] = datetime.fromtimestamp(
                        stamp, timezone(timedelta(hours=8))
                    ).strftime("%Y-%m-%d %H:%M:%S")
                    break
            except (TypeError, ValueError, OSError, OverflowError):
                continue
        return result

    async def parse(self, session: aiohttp.ClientSession, url: str) -> Optional[MediaMetadata]:
        """免登录读取帖子 JSON，发送正文和原图，无需浏览器。

        Args:
            session: 解析管理器提供的异步会话。
            url: 酷安帖子或带参数的分享链接。

        Returns:
            完整正文、真实标题与官方原图地址。

        Raises:
            ValueError: 链接不受支持。
            RuntimeError: 详情不可访问、非 JSON、只返回摘要或缺少内容。
        """
        identity = _feed_id(url)
        if not identity:
            raise ValueError("不支持的酷安帖子链接")
        canonical_url = _feed_url(url, identity)
        async with self.semaphore:
            async with session.get(
                canonical_url, headers=build_headers(self.device_code),
                timeout=aiohttp.ClientTimeout(total=Config.DEFAULT_TIMEOUT),
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise RuntimeError(f"酷安详情请求失败：HTTP {response.status}")
                body = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body) > MAX_PAGE_BYTES:
                        raise RuntimeError("酷安详情超过大小限制")
                try:
                    payload = json.loads(body.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise RuntimeError("酷安未返回图文详情，帖子可能已删除或访问受限") from exc
        result = self._metadata(payload, identity)
        result["url"] = canonical_url
        return result
