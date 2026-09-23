"""CSDN 博客解析器，读取公开正文、配图与有限数量的最新评论。"""

import asyncio
import html
import json
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import aiohttp

from ...logger import logger

from ...constants import Config
from ...types import MediaMetadata
from ..utils import build_request_headers
from .base import BaseVideoParser


BASE_URL = "https://blog.csdn.net"
ARTICLE_URL_RE = re.compile(
    r"(?<![A-Za-z0-9_.:/@%?=&#-])(?:https?://|//)?blog\.csdn\.net"
    r"[^\s<>\"'`()\[\]{}，。！？；：、（）【】《》「」,;!]*", re.IGNORECASE,
)
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_COMMENT_PAGES = 5
CHINA_TIMEZONE = timezone(timedelta(hours=8))


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
    if parsed.scheme.lower() not in {"http", "https"} or parsed.netloc.lower() != "blog.csdn.net":
        return None
    match = re.fullmatch(r"/([A-Za-z0-9_-]{1,100})/article/details/([1-9][0-9]{0,19})/?", parsed.path)
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
        if image and not (host == "csdnimg.cn" or host.endswith(".csdnimg.cn")):
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
        self.fields: Dict[str, List[str]] = {"body": [], "title": []}
        self.images: List[List[str]] = []
        self.meta: Dict[str, str] = {}
        self.canonical = ""
        self.body_count = 0
        self.scripts: List[str] = []
        self._script: Optional[List[str]] = None
        self.has_paywall = False
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
        if in_head and tag == "meta":
            self.meta[values.get("property") or values.get("name") or ""] = values.get("content") or ""
        if in_head and tag == "link" and values.get("rel") == "canonical":
            self.canonical = values.get("href") or ""
        if in_head and tag == "script":
            self._script = []
        classes = set((values.get("class") or "").split())
        if "vip-mask" in classes:
            self.has_paywall = True
        hidden = (hidden or tag in {"script", "style", "noscript", "template", "iframe"}
                  or "hidden" in values or values.get("aria-hidden") == "true"
                  or bool(classes & {"hide-article-box", "vip-mask", "recommend-box", "advertisement", "d-none"})
                  or bool(re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", values.get("style") or "", re.I)))
        if not hidden and values.get("id") == "content_views":
            field = "body"
            self.body_count += 1
        elif not hidden and values.get("id") == "articleContentId" and not field:
            field = "title"
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
            self.scripts.append("".join(self._script))
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


class CsdnParser(BaseVideoParser):
    """解析公开 CSDN 博客与未折叠评论，付费文章仅输出公开预览。"""

    def __init__(self, hot_comment_count: int = 0) -> None:
        """初始化解析器。

        Args:
            hot_comment_count: 评论和回复的总条数上限，零表示不请求评论。
        """
        super().__init__("csdn")
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)
        try:
            self.hot_comment_count = min(100, max(0, int(hot_comment_count)))
        except (TypeError, ValueError, OverflowError):
            self.hot_comment_count = 0

    def can_parse(self, url: str) -> bool:
        """判断链接是否为受支持的博客文章。

        Args:
            url: 待判断的链接。

        Returns:
            是否为官方博客主机上的文章链接。
        """
        return _article_parts(url) is not None

    def extract_links(self, text: str) -> List[str]:
        """提取文章并按文章编号去重，保留首次出现的原链接。

        Args:
            text: 含分享链接的消息文本。

        Returns:
            按出现顺序排列的文章链接。
        """
        links: List[str] = []
        seen = set()
        for match in ARTICLE_URL_RE.finditer(text or ""):
            candidate = match.group(0).rstrip(".,!?:")
            parts = _article_parts(candidate)
            if parts and parts[1] not in seen:
                seen.add(parts[1])
                links.append(candidate)
        return links

    async def _request(self, session: aiohttp.ClientSession, url: str, referer: str, post: bool = False) -> str:
        """有界读取公开页面和只读评论接口，不跟随外部重定向。"""
        request = session.post if post else session.get
        try:
            async with request(
                url, headers=build_request_headers(referer=referer, custom_headers={"Accept": "application/json,text/html"}),
                timeout=aiohttp.ClientTimeout(total=Config.DEFAULT_TIMEOUT), allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise RuntimeError(f"CSDN 请求失败（HTTP {response.status}），文章可能不可见或访问受限")
                chunks = []
                size = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise RuntimeError("CSDN 响应过大，已停止读取")
                    chunks.append(chunk)
                return b"".join(chunks).decode("utf-8")
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError) as exc:
            raise RuntimeError("CSDN 网络请求失败或响应编码无效") from exc

    async def _comments(self, session: aiohttp.ClientSession, article_id: str, url: str) -> List[Dict[str, Any]]:
        """按页面顺序限量读取最新评论与回复，失败时保留已有结果。"""
        comments: Dict[str, Dict[str, Any]] = {}
        try:
            for page in range(1, MAX_COMMENT_PAGES + 1):
                endpoint = f"{BASE_URL}/phoenix/web/v1/comment/list/{article_id}?page={page}&size=10&fold=unfold"
                payload = json.loads(await self._request(session, endpoint, url, post=True))
                if not isinstance(payload, dict) or payload.get("code") != 200:
                    raise RuntimeError("CSDN 评论接口未返回有效数据")
                data = payload.get("data")
                if not isinstance(data, dict) or not isinstance(data.get("list"), list):
                    raise RuntimeError("CSDN 评论列表格式无效")
                previous = len(comments)
                for thread in data["list"]:
                    if not isinstance(thread, dict):
                        continue
                    replies = thread.get("sub")
                    items = [thread.get("info")] + (replies if isinstance(replies, list) else [])
                    for item in items:
                        if not isinstance(item, dict) or str(item.get("articleId")) != article_id:
                            continue
                        identity = str(item.get("commentId", ""))
                        message, username = item.get("content"), item.get("nickName") or item.get("userName")
                        if (not re.fullmatch(r"[1-9][0-9]*", identity) or identity in comments
                                or not isinstance(message, str) or not isinstance(username, str) or not username.strip()):
                            continue
                        content = _ArticleHTML(url)
                        content.feed('<div id="content_views">' + re.sub(r"\[face\].*?\[/face\]", "[表情]", message, flags=re.S) + "</div>")
                        message = content.text()
                        if not message:
                            continue
                        if item.get("parentId") and isinstance(item.get("parentNickName"), str):
                            message = f"回复 {item['parentNickName']}：{message}"
                        comment = {"id": identity, "username": username.strip(), "message": message}
                        if isinstance(item.get("userName"), str):
                            comment["uid"] = item["userName"]
                        if isinstance(item.get("postTime"), str):
                            comment["time"] = item["postTime"]
                        likes = item.get("digg")
                        if type(likes) is int and likes >= 0:
                            comment["likes"] = likes
                        comments[identity] = comment
                        if len(comments) >= self.hot_comment_count:
                            return list(comments.values())
                pages = data.get("pageCount")
                if len(comments) == previous or (type(pages) is int and page >= pages):
                    break
        except asyncio.CancelledError:
            raise
        except (RuntimeError, ValueError) as exc:
            logger.warning(f"[csdn] 评论获取失败，已保留文章正文和图片：{exc}")
        return list(comments.values())

    async def parse(self, session: aiohttp.ClientSession, url: str) -> Optional[MediaMetadata]:
        """读取公开博客图文，按配置获取有限评论。

        Args:
            session: 解析管理器提供的异步会话。
            url: CSDN 博客文章链接。

        Returns:
            文章标题、作者、时间、正文、配图及可选评论。

        Raises:
            ValueError: 链接不受支持。
            RuntimeError: 页面不可见、正文无效或身份不一致。
        """
        parts = _article_parts(url)
        if not parts:
            raise ValueError("不支持的 CSDN 博客链接")
        user, article_id = parts
        canonical = f"{BASE_URL}/{user}/article/details/{article_id}"
        async with self.semaphore:
            source = await self._request(session, canonical, BASE_URL + "/")
            page = _ArticleHTML(canonical)
            page.feed(source)
            page.close()
            if _article_parts(page.canonical) != parts:
                raise RuntimeError("CSDN 返回的文章身份与请求不一致")
            title, author, body = page.text("title"), page.meta.get("article:author", "").strip(), page.text()
            if page.body_count != 1 or not title or not author or not (body or page.images):
                raise RuntimeError("CSDN 页面未包含有效公开正文，可能需要登录或验证")
            try:
                published = datetime.fromisoformat(page.meta.get("article:published_time", ""))
                if published.tzinfo is None:
                    raise ValueError("缺少时区")
            except ValueError as exc:
                raise RuntimeError("CSDN 页面缺少有效发布时间") from exc
            # 仅标注服务端公开返回的预览，不请求解锁内容。
            preview = page.has_paywall or bool(re.search(r"\bvar\s+isVipArticle\s*=\s*true\s*;", "\n".join(page.scripts)))
            if preview:
                body = "【付费文章公开预览，完整内容请前往原文查看】\n\n" + body
            metadata: MediaMetadata = {
                "url": canonical, "platform": self.name, "title": title, "author": author,
                "timestamp": published.astimezone(CHINA_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S"),
                "desc": body, "image_urls": page.images, "video_urls": [],
                "image_headers": build_request_headers(referer=canonical),
            }
            if preview:
                metadata["is_preview_only"] = True
            if self.hot_comment_count:
                comments = await self._comments(session, article_id, canonical)
                if comments:
                    metadata["hot_comments"] = comments
            return metadata
