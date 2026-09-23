"""虎扑解析器，提取公开帖子的主帖图文、原生视频与亮评。"""

import asyncio
import html
import json
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp

from ...constants import Config
from ...types import MediaMetadata
from ..utils import build_request_headers
from .base import BaseVideoParser


HUPU_URL_RE = re.compile(
    r"(?<![A-Za-z0-9_.:/@%?=&#-])(?:https?://|//)?(?:bbs|m)\.hupu\.com"
    r"(?::[0-9]+)?/[^\s<>\"'()，。！？；：、（）【】《》「」,;!]+",
    re.IGNORECASE,
)
CHINA_TIMEZONE = timezone(timedelta(hours=8))
MAX_PAGE_BYTES = 8 * 1024 * 1024


def _thread_id(url: str) -> str:
    """从可信的电脑或手机端帖子路径中读取主题标识。"""
    if not isinstance(url, str) or not url.strip():
        return ""
    normalized = html.unescape(url.strip())
    if normalized.startswith("//"):
        normalized = "https:" + normalized
    elif "://" not in normalized:
        normalized = "https://" + normalized
    try:
        parsed = urlparse(normalized)
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or host not in {"bbs.hupu.com", "m.hupu.com"}
            or parsed.username or parsed.password
            or parsed.port not in {None, 80, 443}
        ):
            return ""
    except ValueError:
        return ""
    prefix = "/bbs/" if host == "m.hupu.com" else "/"
    match = re.fullmatch(prefix + r"([1-9][0-9]{0,19})(?:_[0-9]+)?\.html", parsed.path)
    return match.group(1) if match else ""


def _http_url(value: Any) -> str:
    """读取 HTTP 地址，保留签名与查询参数。"""
    if not isinstance(value, str):
        return ""
    url = html.unescape(value.strip())
    if url.startswith("//"):
        url = "https:" + url
    try:
        parsed = urlparse(url)
        if (
            parsed.scheme.lower() in {"http", "https"}
            and parsed.hostname and not parsed.username and not parsed.password
            and parsed.port in {None, 80, 443}
        ):
            return url
    except ValueError:
        pass
    return ""


def _media_url(value: Any) -> str:
    """只允许虎扑自有媒体域，避免用户正文触发任意主机下载。"""
    url = _http_url(value)
    host = (urlparse(url).hostname or "").lower()
    return url if any(
        host == domain or host.endswith("." + domain)
        for domain in ("hupu.com", "hoopchina.com.cn")
    ) else ""


def _video_url(value: Any) -> str:
    """区分可下载的视频资源与播放器网页。"""
    url = _media_url(value)
    path = urlparse(url).path.lower()
    if path.endswith(".m3u8"):
        return "m3u8:" + url
    return url if path.endswith((".mp4", ".webm", ".mov", ".m4v", ".flv")) else ""


def _timestamp(value: Any) -> str:
    """将页面中的毫秒时间戳统一转换为北京时间。"""
    try:
        milliseconds = float(value)
        if milliseconds <= 0:
            return ""
        return datetime.fromtimestamp(milliseconds / 1000, CHINA_TIMEZONE).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _is_restricted(item: Dict[str, Any]) -> bool:
    """识别页面明确标记的隐藏、审核或删除内容。"""
    return any(
        item.get(key) in (True, 1, "1", "true")
        for key in ("isAudit", "isHidden", "isDelete", "isSelfDelete", "hidePost")
    )


class _NextDataParser(HTMLParser):
    """从页面指定的脚本节点读取服务端首屏数据。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.chunks: List[str] = []
        self.collecting = False

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        """开始读取页面状态脚本。"""
        if tag == "script" and dict(attrs).get("id") == "__NEXT_DATA__":
            self.collecting = True

    def handle_endtag(self, tag: str) -> None:
        """结束当前页面状态脚本。"""
        if tag == "script":
            self.collecting = False

    def handle_data(self, data: str) -> None:
        """保存脚本原文，避免二次解码 JSON 字符串。"""
        if self.collecting:
            self.chunks.append(data)


class _ContentParser(HTMLParser):
    """读取单段主帖或评论内容，不扫描页面推荐和头像。"""

    BLOCK_TAGS = frozenset({"p", "div", "li", "ul", "ol", "blockquote", "h1", "h2", "h3", "h4", "tr"})
    VOID_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})
    HIDDEN_TAGS = frozenset({"script", "style", "noscript", "template"})

    def __init__(self, image_marker: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.images: List[List[str]] = []
        self.videos: List[List[str]] = []
        self.covers: List[List[str]] = []
        self.stack: List[Tuple[str, bool, str, int]] = []
        self.image_marker = image_marker

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        """收集可见文本和此内容节点自己的媒体。"""
        values = dict(attrs)
        style = re.sub(r"\s+", "", values.get("style") or "").lower()
        hidden = (
            bool(self.stack and self.stack[-1][1])
            or tag in self.HIDDEN_TAGS or "hidden" in values
            or "display:none" in style or "visibility:hidden" in style
        )
        if tag not in self.VOID_TAGS:
            reference = _http_url(values.get("href")) if tag == "a" else _media_url(values.get("poster"))
            self.stack.append((tag, hidden, reference, len(self.parts)))
        if hidden:
            return
        if tag in self.BLOCK_TAGS or tag in {"br", "hr"}:
            self.parts.append("\n")
        if tag == "img":
            candidates = list(dict.fromkeys(
                url for key in ("data-gif", "data-original", "data-origin", "data-src", "src")
                if (url := _media_url(values.get(key)))
            ))
            if candidates and not any(set(candidates) & set(group) for group in self.images):
                self.images.append(candidates)
            if self.image_marker:
                self.parts.append(values.get("alt") or "[图片]")
        elif tag in {"video", "source"}:
            parent_video = next((entry for entry in reversed(self.stack) if entry[0] == "video"), None)
            if tag == "source" and parent_video is None:
                return
            video = _video_url(values.get("src"))
            if video and [video] not in self.videos:
                self.videos.append([video])
                cover = _media_url(values.get("poster")) or (parent_video[2] if parent_video else "")
                self.covers.append([cover] if cover else [])

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        """处理自闭合内容节点。"""
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        """保留段落边界与正文超链接。"""
        for index in range(len(self.stack) - 1, -1, -1):
            name, hidden, href, start = self.stack[index]
            if name != tag:
                continue
            del self.stack[index:]
            if not hidden:
                if tag == "a" and href and href != "".join(self.parts[start:]).strip():
                    self.parts.append(f"（{href}）")
                if tag in self.BLOCK_TAGS:
                    self.parts.append("\n")
            break

    def handle_data(self, data: str) -> None:
        """记录可见文字。"""
        if not self.stack or not self.stack[-1][1]:
            self.parts.append(data)

    def text(self) -> str:
        """返回保留段落结构的正文。

        Returns:
            已去除标签与多余空白的文本。
        """
        lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in "".join(self.parts).splitlines()]
        return "\n".join(line for line in lines if line)


class HupuParser(BaseVideoParser):
    """解析虎扑公开帖子，统一电脑与手机分享入口。"""

    def __init__(self, hot_comment_count: int = 0) -> None:
        """初始化虎扑解析器与评论数量上限。"""
        super().__init__("hupu")
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)
        try:
            self.hot_comment_count = max(0, int(hot_comment_count))
        except (TypeError, ValueError, OverflowError):
            self.hot_comment_count = 0

    def can_parse(self, url: str) -> bool:
        """判断是否为支持的虎扑帖子链接。

        Args:
            url: 待判断的链接。

        Returns:
            是否包含可信域名和明确的帖子标识。
        """
        return bool(_thread_id(url))

    def extract_links(self, text: str) -> List[str]:
        """提取分享链接并按帖子标识去重。

        Args:
            text: 包含分享链接的消息文本。

        Returns:
            按出现顺序排列的电脑端规范链接。
        """
        links: List[str] = []
        for match in HUPU_URL_RE.finditer(text or ""):
            identity = _thread_id(match.group(0).rstrip(".,!?)]}>\"'，。！？；：）】》」"))
            canonical = f"https://bbs.hupu.com/{identity}.html"
            if identity and canonical not in links:
                links.append(canonical)
        return links

    # ── 页面获取与主帖校验 ──────────────────────────

    async def _fetch_detail(self, session: aiohttp.ClientSession, url: str) -> Dict[str, Any]:
        """读取公开帖子首屏状态，拒绝登录跳转和无法验证的页面。"""
        headers = build_request_headers(referer="https://bbs.hupu.com/")
        headers["Accept"] = "text/html,application/xhtml+xml"
        try:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=25), allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise RuntimeError(f"虎扑帖子请求失败（HTTP {response.status}），帖子可能已删除或访问受限")
                chunks = []
                size = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    size += len(chunk)
                    if size > MAX_PAGE_BYTES:
                        raise RuntimeError("虎扑帖子页面过大，已停止读取")
                    chunks.append(chunk)
                body = b"".join(chunks).decode("utf-8")
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError) as exc:
            raise RuntimeError("虎扑帖子页面请求失败") from exc
        parser = _NextDataParser()
        parser.feed(body)
        parser.close()
        try:
            state = json.loads("".join(parser.chunks))
        except (ValueError, TypeError) as exc:
            raise RuntimeError("虎扑页面缺少有效帖子数据，可能触发验证或访问受限") from exc
        props = state.get("props") if isinstance(state, dict) else None
        page = props.get("pageProps") if isinstance(props, dict) else None
        if not isinstance(page, dict):
            raise RuntimeError("虎扑页面缺少帖子数据")
        error = page.get("detailErrorInfo")
        if isinstance(error, dict) and str(error.get("code", 200)) != "200":
            raise RuntimeError("虎扑帖子不可访问，可能已删除或需要登录")
        detail = page.get("detail")
        if not isinstance(detail, dict):
            raise RuntimeError("虎扑页面没有返回主帖内容")
        return detail

    @staticmethod
    def _validate_thread(detail: Dict[str, Any], identity: str) -> Dict[str, Any]:
        """验证主帖身份及其公开可见状态。"""
        thread = detail.get("thread")
        if not isinstance(thread, dict) or str(thread.get("tid", "")) != identity:
            raise RuntimeError("虎扑页面返回的帖子身份与请求不一致")
        for item in (detail, thread):
            visibility = item.get("visibleRange")
            if (visibility and visibility != "ALL_SEE") or _is_restricted(item):
                raise RuntimeError("虎扑帖子访问受限，无法获取公开完整内容")
        if str(thread.get("status", 0)) != "0":
            raise RuntimeError("虎扑帖子状态异常，可能已删除或正在审核")
        return thread

    # ── 正文与评论提取 ──────────────────────────────

    @staticmethod
    def _content(thread: Dict[str, Any], canonical: str) -> _ContentParser:
        """解析主帖正文，对独立比赛组件明确保留原帖查看提示。"""
        parser = _ContentParser()
        content = thread.get("content")
        if not isinstance(content, str):
            return parser
        stripped = content.strip()
        if stripped.startswith(("{", "[")):
            try:
                structured = json.loads(stripped)
            except ValueError:
                structured = None
            if isinstance(structured, dict) and structured.get("type") == "iframe-match":
                parser.parts.append(f"比赛战报请打开原帖查看：{canonical}")
                return parser
            if isinstance(structured, (dict, list)):
                raise RuntimeError("虎扑帖子包含尚未支持的内容组件，无法获取完整正文")
        parser.feed(content)
        parser.close()
        return parser

    def _comments(self, detail: Dict[str, Any]) -> List[Dict[str, Any]]:
        """亮评优先，使用首屏普通回复补足数量并排除重复和不可见评论。"""
        if not self.hot_comment_count:
            return []
        lights = detail.get("lights")
        replies = detail.get("replies")
        ordinary = replies.get("list") if isinstance(replies, dict) else []
        candidates = (lights if isinstance(lights, list) else []) + (ordinary if isinstance(ordinary, list) else [])
        comments: List[Dict[str, Any]] = []
        seen = set()
        for item in candidates:
            if not isinstance(item, dict) or _is_restricted(item):
                continue
            parser = _ContentParser(image_marker=True)
            content = item.get("content")
            if not isinstance(content, str):
                continue
            parser.feed(content)
            parser.close()
            message = parser.text()
            if not message:
                continue
            author = item.get("author")
            author = author if isinstance(author, dict) else {}
            uid = str(author.get("puid") or item.get("authorId") or "")
            identity = str(item.get("pid") or "")
            key = identity or (uid, str(item.get("createdAt", "")), message)
            if key in seen:
                continue
            seen.add(key)
            comment: Dict[str, Any] = {
                "id": identity,
                "username": str(author.get("puname") or "匿名用户"),
                "uid": uid,
                "message": message,
                "time": _timestamp(item.get("createdAt")),
            }
            for field in ("allLightCount", "count"):
                likes = item.get(field)
                if isinstance(likes, (str, int)) and not isinstance(likes, bool):
                    if str(likes).isdigit():
                        comment["likes"] = int(likes)
                        break
            comments.append(comment)
            if len(comments) >= self.hot_comment_count:
                break
        return comments

    async def parse(self, session: aiohttp.ClientSession, url: str) -> Optional[MediaMetadata]:
        """解析公开主帖的正文、媒体和配置数量内的评论。

        Args:
            session: 共用的异步 HTTP 会话。
            url: 虎扑电脑或手机端帖子链接。

        Returns:
            符合项目统一契约的帖子元数据。

        Raises:
            ValueError: 链接不受支持。
            RuntimeError: 页面不可访问、身份不符或缺少实际内容。
        """
        identity = _thread_id(url)
        if not identity:
            raise ValueError("不支持的虎扑帖子链接")
        canonical = f"https://bbs.hupu.com/{identity}.html"
        async with self.semaphore:
            detail = await self._fetch_detail(session, canonical)
        thread = self._validate_thread(detail, identity)
        content = self._content(thread, canonical)
        videos = content.videos
        covers = content.covers
        native_video = _video_url(thread.get("video"))
        if native_video and [native_video] not in videos:
            videos.insert(0, [native_video])
            cover = _media_url(thread.get("videoCover"))
            covers.insert(0, [cover] if cover else [])
        if thread.get("hasVideo") and not videos:
            raise RuntimeError("虎扑主帖视频尚不可用或没有返回可下载地址")
        description = content.text()
        if not description and not content.images and not videos:
            raise RuntimeError("虎扑页面只返回帖子空壳，未获取到正文或媒体")
        author = thread.get("author")
        author = author if isinstance(author, dict) else {}
        metadata: MediaMetadata = {
            "url": canonical,
            "title": html.unescape(str(thread.get("title") or "")),
            "author": str(author.get("puname") or ""),
            "desc": description,
            "timestamp": _timestamp(thread.get("createdAt")),
            "platform": "虎扑",
            "image_urls": content.images,
            "video_urls": videos,
            "video_cover_urls": covers,
            "image_headers": build_request_headers(referer=canonical),
            "video_headers": build_request_headers(is_video=True, referer=canonical),
        }
        if self.hot_comment_count:
            metadata["hot_comments"] = self._comments(detail)
        return metadata
