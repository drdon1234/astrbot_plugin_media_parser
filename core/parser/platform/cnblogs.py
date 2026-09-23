"""博客园解析器，读取公开博客正文、作者和平台图床配图。"""

import asyncio
import html
import json
import re
from datetime import datetime
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import aiohttp

from ...constants import Config
from ...types import MediaMetadata
from ..utils import build_request_headers
from .base import BaseVideoParser


BASE_URL = "https://www.cnblogs.com"
ARTICLE_URL_RE = re.compile(
    r"(?<![A-Za-z0-9_.:/@%?=&#-])(?:https?://|//)?(?:www\.)?cnblogs\.com"
    r"[^\s<>\"'`()\[\]{}，。！？；：、（）【】《》「」,;!]*", re.IGNORECASE,
)
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def _article_parts(value: str) -> Optional[Tuple[str, str]]:
    """仅接纳官方博客主机上的完整文章路径。"""
    if not isinstance(value, str) or not value.strip():
        return None
    value = html.unescape(value.strip())
    if re.search(r"[\s\x00-\x1f\x7f\\]", value):
        return None
    if value.startswith("//"):
        value = "https:" + value
    elif "://" not in value:
        value = "https://" + value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or parsed.netloc.lower() not in {"cnblogs.com", "www.cnblogs.com"}:
        return None
    match = re.fullmatch(r"/([A-Za-z0-9_-]{1,100})/p/([1-9][0-9]{0,19})(?:\.html)?/?", parsed.path)
    return (match.group(1).lower(), match.group(2)) if match else None


def _content_url(value: Any, base_url: str, image: bool = False) -> str:
    """保留正文链接，仅允许官方图床进入下载流程并保留查询签名。"""
    if not isinstance(value, str) or not value.strip() or re.search(r"[\s\x00-\x1f\x7f\\]", value):
        return ""
    try:
        parsed = urlsplit(urljoin(base_url, value.strip()))
        host = (parsed.hostname or "").lower()
        if (parsed.scheme not in {"http", "https"} or not host
                or parsed.netloc.lower() != host):
            return ""
        if image and not re.fullmatch(r"(?:img[0-9]*|images[0-9]*|pic)\.cnblogs\.com", host):
            return ""
        return parsed._replace(fragment="").geturl() if image else parsed.geturl()
    except ValueError:
        return ""


class _ArticleHTML(HTMLParser):
    """限定正文容器，保留代码换行并排除广告、脚本和隐藏内容。"""

    VOIDS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    BLOCKS = {"p", "div", "pre", "blockquote", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table"}

    def __init__(self, url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.url = url
        self.fields: Dict[str, List[str]] = {"body": [], "title": [], "date": []}
        self.images: List[List[str]] = []
        self.canonical = ""
        self.body_count = 0
        self.documents: List[str] = []
        self._script: Optional[List[str]] = None
        self.stack: List[Tuple[str, str, bool, bool, str, int]] = []

    def _newline(self, field: str) -> None:
        """为段落边界补充单个换行。"""
        parts = self.fields[field]
        if parts and not parts[-1].endswith("\n"):
            parts.append("\n")

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        """读取可信页面元数据和正文节点。"""
        values = dict(attrs)
        parent = self.stack[-1] if self.stack else ("", "", False, False, "", 0)
        field, hidden, pre = parent[1:4]
        in_head = any(frame[0] == "head" for frame in self.stack)
        if in_head and tag == "link" and values.get("rel") == "canonical":
            self.canonical = values.get("href") or ""
        if in_head and tag == "script" and values.get("type") == "application/ld+json":
            self._script = []
        classes = set((values.get("class") or "").split())
        hidden = (hidden or tag in {"script", "style", "noscript", "template", "iframe"}
                  or "hidden" in values or values.get("aria-hidden") == "true"
                  or bool(classes & {"cnblogs_code_toolbar", "code_img_closed", "code_img_opened", "advertisement", "d-none"})
                  or bool(re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", values.get("style") or "", re.I)))
        if not hidden and values.get("id") == "cnblogs_post_body":
            field = "body"
            self.body_count += 1
        elif not hidden and values.get("id") == "cb_post_title_url" and not field:
            field = "title"
        elif not hidden and values.get("id") == "post-date" and not field:
            field = "date"
        pre = pre or tag == "pre"
        href = _content_url(values.get("href"), self.url) if tag == "a" and field == "body" else ""
        if tag not in self.VOIDS:
            self.stack.append((tag, field, hidden, pre, href, len(self.fields.get(field, []))))
        if not field or hidden:
            return
        if tag in self.BLOCKS or tag in {"br", "hr"}:
            self._newline(field)
        if tag == "li":
            self.fields[field].append("• ")
        if tag in {"td", "th"}:
            self.fields[field].append("\t")
        if field == "body" and tag == "img":
            image = _content_url(values.get("data-src") or values.get("src"), self.url, image=True)
            if image and [image] not in self.images:
                self.images.append([image])

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        """避免自闭合图片提前结束正文容器。"""
        self.handle_starttag(tag, attrs)
        if tag not in self.VOIDS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        """保留段落边界和有名称链接的目标。"""
        if tag == "script" and self._script is not None:
            self.documents.append("".join(self._script))
            self._script = None
        for index in range(len(self.stack) - 1, -1, -1):
            name, field, hidden, _, href, start = self.stack[index]
            if name == tag:
                del self.stack[index:]
                if field and not hidden:
                    if href and href != "".join(self.fields[field][start:]).strip():
                        self.fields[field].append(f"（{href}）")
                    if tag in self.BLOCKS:
                        self._newline(field)
                break

    def handle_data(self, data: str) -> None:
        """代码块保留缩进，普通文本合并排版空白。"""
        if self._script is not None:
            self._script.append(data)
        if self.stack:
            _, field, hidden, pre, _, _ = self.stack[-1]
            if field and not hidden:
                self.fields[field].append(data.replace("\r\n", "\n") if pre else re.sub(r"\s+", " ", data))

    def text(self, field: str = "body") -> str:
        """读取已清洗的正文或标题。

        Args:
            field: 要读取的文本字段。

        Returns:
            保留代码缩进和段落换行的文本。
        """
        return "\n".join(line.rstrip() for line in "".join(self.fields[field]).splitlines()).strip()


class CnblogsParser(BaseVideoParser):
    """解析匿名可见的博客园文章，不请求需要登录的评论。"""

    def __init__(self) -> None:
        """初始化公开博客解析器。"""
        super().__init__("cnblogs")
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)

    def can_parse(self, url: str) -> bool:
        """判断链接是否为受支持的博客文章。

        Args:
            url: 待判断的链接。

        Returns:
            是否为官方博客主机上的文章链接。
        """
        return _article_parts(url) is not None

    def extract_links(self, text: str) -> List[str]:
        """按文章身份提取去重，保留原文中的链接形式。

        Args:
            text: 含分享链接的消息文本。

        Returns:
            按出现顺序排列的文章链接。
        """
        links: List[str] = []
        seen = set()
        for match in ARTICLE_URL_RE.finditer(text or ""):
            candidate = match.group(0).rstrip(".,!?:")
            identity = _article_parts(candidate)
            if identity and identity not in seen:
                seen.add(identity)
                links.append(candidate)
        return links

    async def _request(self, session: aiohttp.ClientSession, url: str) -> str:
        """有界读取博客页面，仅跟随同一文章的规范路径重定向。"""
        identity = _article_parts(url)
        try:
            for redirect in range(3):
                async with session.get(
                    url, headers=build_request_headers(custom_headers={"Accept": "text/html"}),
                    timeout=aiohttp.ClientTimeout(total=Config.DEFAULT_TIMEOUT), allow_redirects=False,
                ) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        location = response.headers.get("Location", "")
                        target = urljoin(url, location)
                        if not location or _article_parts(target) != identity:
                            raise RuntimeError("博客园文章需要登录或返回了无效重定向")
                        if redirect == 2:
                            raise RuntimeError("博客园文章重定向次数过多")
                        url = target
                        continue
                    if response.status != 200:
                        raise RuntimeError(f"博客园请求失败（HTTP {response.status}），文章可能不可见或访问受限")
                    chunks = []
                    size = 0
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        size += len(chunk)
                        if size > MAX_RESPONSE_BYTES:
                            raise RuntimeError("博客园响应过大，已停止读取")
                        chunks.append(chunk)
                    return b"".join(chunks).decode("utf-8")
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError) as exc:
            raise RuntimeError("博客园网络请求失败或响应编码无效") from exc
        raise RuntimeError("博客园未返回有效文章")

    @staticmethod
    def _author(page: _ArticleHTML, identity: Tuple[str, str]) -> str:
        """校验文章结构化数据与作者主页，再提取作者名称。"""
        for document in page.documents:
            try:
                value = json.loads(document)
            except ValueError:
                continue
            if (not isinstance(value, dict) or value.get("@type") != "BlogPosting"
                    or _article_parts(value.get("@id")) != identity):
                continue
            author = value.get("author")
            if not isinstance(author, dict) or not isinstance(author.get("name"), str):
                continue
            author_url = _content_url(author.get("url") or author.get("@id"), page.url)
            if not author_url:
                continue
            parsed = urlsplit(author_url)
            if (parsed.netloc.lower() in {"cnblogs.com", "www.cnblogs.com"}
                    and parsed.path.strip("/").lower() == identity[0]
                    and not parsed.query and not parsed.fragment):
                return html.unescape(author["name"]).strip()
        return ""

    async def parse(self, session: aiohttp.ClientSession, url: str) -> Optional[MediaMetadata]:
        """解析公开博客正文与平台图床图片。

        Args:
            session: 解析管理器提供的异步会话。
            url: 博客园文章链接。

        Returns:
            标题、作者、发布时间、正文及配图。

        Raises:
            ValueError: 链接不受支持。
            RuntimeError: 页面不可见、正文无效或身份不一致。
        """
        identity = _article_parts(url)
        if not identity:
            raise ValueError("不支持的博客园文章链接")
        canonical = f"{BASE_URL}/{identity[0]}/p/{identity[1]}"
        async with self.semaphore:
            page = _ArticleHTML(canonical)
            page.feed(await self._request(session, canonical))
            page.close()
            if _article_parts(page.canonical) != identity:
                raise RuntimeError("博客园返回的文章身份与请求不一致")
            title, author, body = page.text("title"), self._author(page, identity), page.text()
            if page.body_count != 1 or not title or not author or not (body or page.images):
                raise RuntimeError("博客园页面未包含有效公开正文，可能需要登录或验证")
            try:
                published = datetime.fromisoformat(page.text("date"))
            except ValueError as exc:
                raise RuntimeError("博客园页面缺少有效发布时间") from exc
            return {
                "url": canonical, "platform": self.name, "title": title, "author": author,
                "timestamp": published.strftime("%Y-%m-%d %H:%M:%S"), "desc": body,
                "image_urls": page.images, "video_urls": [],
                "image_headers": build_request_headers(referer=canonical),
            }
