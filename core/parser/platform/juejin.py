"""稀土掘金解析器，匿名读取公开文章图文及有限条热门评论。"""

import asyncio
import html
import json
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urljoin, urlsplit

import aiohttp

from ...logger import logger

from ...constants import Config
from ...types import MediaMetadata
from ..utils import build_request_headers
from .base import BaseVideoParser


BASE_URL = "https://juejin.cn"
API_URL = "https://api.juejin.cn"
ARTICLE_HOSTS = frozenset({"juejin.cn", "juejin.im"})
ARTICLE_URL_RE = re.compile(
    r"(?<![A-Za-z0-9_.:/@%?=&#-])(?:https?://|//)?juejin\.(?:cn|im)"
    r"[^\s<>\"'`()\[\]{}，。！？；：、（）【】《》「」,;!]*",
    re.IGNORECASE,
)
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_COMMENT_PAGES = 3
MAX_REPLY_REQUESTS = 3
CHINA_TIMEZONE = timezone(timedelta(hours=8))


def _article_id(value: Any) -> str:
    """只接受官方文章主链接，不将子页面、凭据或异常端口视为文章。"""
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
        if (parts.scheme.lower() not in {"http", "https"}
                or (parts.hostname or "").lower() not in ARTICLE_HOSTS
                or parts.username is not None or parts.password is not None
                or parts.port not in {None, 80, 443}):
            return ""
    except ValueError:
        return ""
    match = re.fullmatch(r"/post/([1-9][0-9]{0,19})/?", parts.path)
    return match.group(1) if match else ""


def _http_url(value: Any, image: bool = False) -> str:
    """保留正文链接；下载图片仅接受掘金使用的公开图床域名。"""
    if not isinstance(value, str) or not value.strip():
        return ""
    value = html.unescape(value.strip())
    if re.search(r"[\s\x00-\x1f\x7f\\]", value):
        return ""
    value = urljoin(BASE_URL + "/", value)
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        if (parts.scheme not in {"http", "https"} or not host
                or parts.username is not None or parts.password is not None
                or parts.port not in {None, 80, 443}):
            return ""
        if image and not (
            re.fullmatch(r"p[0-9]+-(?:juejin|xtjj-sign)\.byteimg\.com", host)
            or host in {"user-gold-cdn.xitu.io", "lc-gold-cdn.xitu.io"}
        ):
            return ""
        if not image and host == "link.juejin.cn":
            target = parse_qs(parts.query).get("target", [])
            if target and urlsplit(target[0]).hostname != "link.juejin.cn":
                return _http_url(target[0])
    except ValueError:
        return ""
    return value


def _integer(value: Any) -> Optional[int]:
    """读取非负整数，未知统计保留缺省。"""
    if type(value) is int:
        return value if value >= 0 else None
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,20}", value):
        return int(value)
    return None


def _timestamp(value: Any) -> str:
    """将接口秒时间戳转换为北京时间。"""
    seconds = _integer(value)
    if not seconds:
        return ""
    try:
        return datetime.fromtimestamp(seconds, CHINA_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OverflowError, OSError):
        return ""


class _ContentParser(HTMLParser):
    """清洗文章正文 HTML，并保留代码、列表、表格、链接和正文图片。"""

    BLOCKS = {"p", "div", "pre", "blockquote", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}
    VOIDS = {"br", "hr", "img", "input", "meta", "link", "source", "wbr", "area", "embed"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.images: List[List[str]] = []
        self.stack: List[Tuple[str, bool, str, int, bool]] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        """排除隐藏节点，记录正文结构与受信图片。"""
        values = dict(attrs)
        hidden = bool(self.stack and self.stack[-1][1]) or tag in {
            "style", "script", "noscript", "template", "iframe", "svg",
        }
        hidden = hidden or "hidden" in values or values.get("aria-hidden") == "true" or bool(
            re.search(r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\b", values.get("style") or "", re.I)
        )
        pre = tag == "pre" or bool(self.stack and self.stack[-1][4])
        if tag not in self.VOIDS:
            self.stack.append((tag, hidden, _http_url(values.get("href")) if tag == "a" else "", len(self.parts), pre))
        if hidden:
            return
        if tag in self.BLOCKS or tag in {"br", "hr"}:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("• ")
        if tag in {"td", "th"}:
            self.parts.append("\t")
        if tag == "img":
            image = _http_url(values.get("data-src") or values.get("src"), image=True)
            if image and [image] not in self.images:
                self.images.append([image])

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        """处理自闭合节点。"""
        self.handle_starttag(tag, attrs)
        if tag not in self.VOIDS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        """保留链接目标和块级段落边界。"""
        for index in range(len(self.stack) - 1, -1, -1):
            name, hidden, href, start, _ = self.stack[index]
            if name == tag:
                del self.stack[index:]
                if not hidden:
                    if href and href != "".join(self.parts[start:]).strip():
                        self.parts.append(f"（{href}）")
                    if tag in self.BLOCKS:
                        self.parts.append("\n")
                break

    def handle_data(self, data: str) -> None:
        """代码块保留原始缩进，普通文字合并 HTML 排版空白。"""
        if not self.stack or not self.stack[-1][1]:
            self.parts.append(data if self.stack and self.stack[-1][4] else re.sub(r"\s+", " ", data))

    def text(self) -> str:
        """返回保留代码缩进和段落换行的可见正文。

        Returns:
            清洗后的文章正文。
        """
        return "".join(self.parts).strip()


class JuejinParser(BaseVideoParser):
    """解析掘金公开技术文章，可选读取热门评论和有限回复。"""

    def __init__(self, hot_comment_count: int = 0) -> None:
        """初始化解析器。

        Args:
            hot_comment_count: 评论与回复的合计输出上限，零表示关闭。
        """
        super().__init__("juejin")
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)
        try:
            self.hot_comment_count = max(0, int(hot_comment_count))
        except (TypeError, ValueError, OverflowError):
            self.hot_comment_count = 0

    def can_parse(self, url: str) -> bool:
        """判断是否为受支持的掘金文章链接。

        Args:
            url: 待判断的链接。

        Returns:
            是否包含可信域名和文章编号。
        """
        return bool(_article_id(url))

    def extract_links(self, text: str) -> List[str]:
        """按文章编号去重，并保留首次出现的原文链接。

        Args:
            text: 消息文本。

        Returns:
            按消息出现顺序排列的文章链接。
        """
        links: List[str] = []
        seen = set()
        for match in ARTICLE_URL_RE.finditer(text or ""):
            candidate = match.group(0).rstrip(".,!?:")
            identity = _article_id(candidate)
            if identity and identity not in seen:
                seen.add(identity)
                links.append(candidate)
        return links

    async def _request(
        self, session: aiohttp.ClientSession, path: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """有界读取公开查询接口，不跟随重定向或发送平台登录凭据。"""
        try:
            async with session.post(
                API_URL + path, json=payload,
                headers=build_request_headers(referer=BASE_URL + "/", origin=BASE_URL, custom_headers={"Accept": "application/json"}),
                timeout=aiohttp.ClientTimeout(total=Config.DEFAULT_TIMEOUT), allow_redirects=False,
            ) as response:
                if response.status in {401, 403, 429}:
                    raise RuntimeError("掘金访问受限或请求过于频繁，请稍后再试")
                if response.status != 200:
                    raise RuntimeError(f"掘金请求失败（HTTP {response.status}）")
                chunks = []
                size = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise RuntimeError("掘金响应过大，已停止读取")
                    chunks.append(chunk)
                result = json.loads(b"".join(chunks))
                if not isinstance(result, dict) or type(result.get("err_no")) is not int:
                    raise RuntimeError("掘金接口返回的数据格式无效")
                if result["err_no"] != 0:
                    raise RuntimeError("掘金内容不可见、访问受限或接口查询失败")
                return result
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, UnicodeError) as exc:
            raise RuntimeError("掘金网络请求失败或响应无效") from exc

    @staticmethod
    def _comment(item: Any, identity: str, parent: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """校验评论归属并映射到统一评论字段。"""
        if not isinstance(item, dict):
            return None
        info = item.get("reply_info" if parent else "comment_info")
        user = item.get("user_info")
        if not isinstance(info, dict) or not isinstance(user, dict):
            return None
        prefix = "reply" if parent else "comment"
        comment_id = _integer(info.get(f"{prefix}_id"))
        message = info.get(f"{prefix}_content")
        username = user.get("user_name")
        if (not comment_id or str(info.get("item_id")) != identity or info.get("item_type") != 2
                or info.get(f"{prefix}_status") != 1
                or not isinstance(message, str) or not message.strip()
                or not isinstance(username, str) or not username.strip()
                or (parent and str(info.get("reply_comment_id")) != parent["id"])):
            return None
        comment = {
            "id": str(comment_id), "username": username.strip(),
            "uid": str(user.get("user_id") or info.get("user_id") or ""),
            "message": html.unescape(message).strip(), "time": _timestamp(info.get("ctime")),
        }
        if parent:
            comment["message"] = f"评论「{parent['message'][:40]}」下的回复：\n{comment['message']}"
        likes = _integer(info.get("digg_count"))
        if likes is not None:
            comment["likes"] = likes
        return comment

    async def _comments(self, session: aiohttp.ClientSession, identity: str) -> List[Dict[str, Any]]:
        """优先读取热门一级评论，未满限额时补充其楼中楼，失败保留已取结果。"""
        comments: Dict[str, Dict[str, Any]] = {}
        roots: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        try:
            cursor = "0"
            cursors = {cursor}
            for _ in range(MAX_COMMENT_PAGES):
                payload = await self._request(session, "/interact_api/v1/comment/list", {
                    "item_id": identity, "item_type": 2, "cursor": cursor,
                    "limit": min(20, self.hot_comment_count - len(comments)), "sort": 1, "client_type": 2608,
                })
                items = payload.get("data")
                if not isinstance(items, list):
                    raise RuntimeError("掘金评论接口未返回有效列表")
                previous = len(comments)
                for item in items:
                    comment = self._comment(item, identity)
                    if comment and comment["id"] not in comments:
                        comments[comment["id"]] = comment
                        roots.append((comment, item))
                    if len(comments) >= self.hot_comment_count:
                        return list(comments.values())
                cursor = payload.get("cursor")
                if (not payload.get("has_more") or len(comments) == previous
                        or not isinstance(cursor, str) or cursor in cursors):
                    break
                cursors.add(cursor)
            requests = 0
            for parent, source in roots:
                previews = source.get("reply_infos")
                preview_ids = set()
                for item in previews if isinstance(previews, list) else []:
                    comment = self._comment(item, identity, parent)
                    if comment:
                        comments.setdefault(comment["id"], comment)
                        preview_ids.add(comment["id"])
                    if len(comments) >= self.hot_comment_count:
                        return list(comments.values())
                reply_count = _integer(source["comment_info"].get("reply_count")) or 0
                if reply_count <= len(preview_ids) or requests >= MAX_REPLY_REQUESTS:
                    continue
                requests += 1
                payload = await self._request(session, "/interact_api/v1/reply/list", {
                    "item_id": identity, "item_type": 2, "comment_id": parent["id"],
                    "cursor": "0", "limit": min(20, self.hot_comment_count - len(comments) + len(preview_ids)),
                    "client_type": 2608,
                })
                items = payload.get("data")
                if not isinstance(items, list):
                    raise RuntimeError("掘金回复接口未返回有效列表")
                for item in items:
                    comment = self._comment(item, identity, parent)
                    if comment:
                        comments.setdefault(comment["id"], comment)
                    if len(comments) >= self.hot_comment_count:
                        return list(comments.values())
        except asyncio.CancelledError:
            raise
        except RuntimeError as exc:
            logger.warning(f"[juejin] 评论补充失败，已保留文章与已取得的评论：{exc}")
        return list(comments.values())

    async def parse(self, session: aiohttp.ClientSession, url: str) -> Optional[MediaMetadata]:
        """读取公开技术文章，按配置补充有限条评论。

        Args:
            session: 解析管理器提供的异步 HTTP 会话。
            url: 掘金文章主链接。

        Returns:
            标题、作者、时间、正文、正文配图和可选评论。

        Raises:
            ValueError: 链接不受支持。
            RuntimeError: 内容不可见、响应无效或文章身份不符。
        """
        identity = _article_id(url)
        if not identity:
            raise ValueError("不支持的掘金文章链接")
        async with self.semaphore:
            payload = await self._request(session, "/content_api/v1/article/detail", {
                "article_id": identity, "client_type": 2608, "req_from": 0,
                "need_theme": True, "forbid_count": True, "is_pre_load": True, "prefer_html": True,
            })
            data = payload.get("data")
            info = data.get("article_info") if isinstance(data, dict) else None
            user = data.get("author_user_info") if isinstance(data, dict) else None
            if (not isinstance(info, dict) or not isinstance(user, dict)
                    or str(data.get("article_id")) != identity or str(info.get("article_id")) != identity):
                raise RuntimeError("掘金返回的文章身份与请求不一致")
            if info.get("visible_level") != 0 or info.get("status") != 2:
                raise RuntimeError("掘金文章不是可读取的公开正文")
            title, author, body = info.get("title"), user.get("user_name"), info.get("web_html_content")
            if (not isinstance(title, str) or not title.strip() or not isinstance(author, str)
                    or not author.strip() or not isinstance(body, str) or not body.strip()):
                raise RuntimeError("掘金文章缺少有效正文、标题或作者")
            content = _ContentParser()
            content.feed(body)
            content.close()
            if not content.text() and not content.images:
                raise RuntimeError("掘金文章未返回可见正文")
            metadata: MediaMetadata = {
                "url": f"{BASE_URL}/post/{identity}", "title": html.unescape(title).strip(),
                "author": author.strip(), "timestamp": _timestamp(info.get("ctime")),
                "desc": content.text(), "image_urls": content.images, "video_urls": [],
                "image_headers": build_request_headers(referer=BASE_URL + "/"), "platform": "稀土掘金",
            }
            if self.hot_comment_count:
                comments = await self._comments(session, identity)
                if comments:
                    metadata["hot_comments"] = comments
            return metadata
