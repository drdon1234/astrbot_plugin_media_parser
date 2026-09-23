"""V2EX 解析器，匿名读取公开主题图文与按感谢数优先展示的回复。"""

import asyncio
import html
import ipaddress
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


BASE_URL = "https://www.v2ex.com"
TOPIC_HOSTS = frozenset({"v2ex.com", "www.v2ex.com"})
TOPIC_URL_RE = re.compile(
    r"(?<![A-Za-z0-9_.:/@%?=&#-])(?:https?://|//)?(?:www\.)?v2ex\.com"
    r"[^\s<>\"'`()\[\]{}，。！？；：、（）【】《》「」,;!]*",
    re.IGNORECASE,
)
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_COMMENT_PAGES = 5
CHINA_TIMEZONE = timezone(timedelta(hours=8))


def _topic_id(value: str) -> str:
    """仅接纳可信主机上明确的主题路径，忽略分享参数和楼层锚点。"""
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
                or (parts.hostname or "").lower() not in TOPIC_HOSTS
                or parts.username or parts.password or parts.port not in {None, 80, 443}):
            return ""
    except ValueError:
        return ""
    match = re.fullmatch(r"/t/([1-9][0-9]{0,19})/?", parts.path)
    return match.group(1) if match else ""


def _http_url(value: Any) -> str:
    """保留正文中的 HTTP 链接，拒绝凭据、异常端口和本地地址。"""
    if not isinstance(value, str) or not value.strip():
        return ""
    value = html.unescape(value.strip())
    if re.search(r"[\s\x00-\x1f\x7f\\]", value):
        return ""
    value = urljoin(BASE_URL + "/", value)
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower().rstrip(".")
        if (parts.scheme not in {"http", "https"} or not host
                or parts.username or parts.password or parts.port not in {None, 80, 443}
                or "." not in host or host.endswith((".localhost", ".local", ".internal"))):
            return ""
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            return ""
    except ValueError:
        return ""
    return value


def _integer(value: Any) -> Optional[int]:
    """读取明确的非负整数字段，未知或无效值不当作零。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,20}", value):
        return int(value)
    return None


def _timestamp(value: Any) -> str:
    """统一 Unix 秒数与网页 ISO 时间为北京时间。"""
    try:
        seconds = _integer(value)
        if seconds is not None and seconds > 0:
            date = datetime.fromtimestamp(seconds, CHINA_TIMEZONE)
        elif isinstance(value, str):
            date = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if date.tzinfo is None:
                return ""
            date = date.astimezone(CHINA_TIMEZONE)
        else:
            return ""
        return date.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return ""


class _ContentParser(HTMLParser):
    """将正文 HTML 转换为文字和配图，评论图片仅输出占位文字。"""

    BLOCKS = {"p", "div", "pre", "blockquote", "li", "ul", "ol", "h1", "h2", "h3", "tr"}
    VOIDS = {"br", "hr", "img", "input", "meta", "link", "source", "wbr"}

    def __init__(self, image_marker: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self.image_marker = image_marker
        self.parts: List[str] = []
        self.images: List[List[str]] = []
        self.stack: List[Tuple[str, bool, str, int]] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        """保留段落和链接，排除脚本等非正文内容。"""
        values = dict(attrs)
        hidden = (bool(self.stack) and self.stack[-1][1]) or tag in {"script", "style", "noscript"}
        if tag not in self.VOIDS:
            self.stack.append((tag, hidden, _http_url(values.get("href")) if tag == "a" else "", len(self.parts)))
        if hidden:
            return
        if tag in self.BLOCKS or tag in {"br", "hr"}:
            self.parts.append("\n")
        if tag == "img":
            image = _http_url(values.get("src"))
            if image and [image] not in self.images:
                self.images.append([image])
            if self.image_marker:
                self.parts.append("[图片]")

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        """处理自闭合 HTML 节点。"""
        self.handle_starttag(tag, attrs)
        if tag not in self.VOIDS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        """结束段落，并保留有名称的超链接目标。"""
        for index in range(len(self.stack) - 1, -1, -1):
            name, hidden, href, start = self.stack[index]
            if name == tag:
                del self.stack[index:]
                if not hidden:
                    if href and href != "".join(self.parts[start:]).strip():
                        self.parts.append(f"（{href}）")
                    if tag in self.BLOCKS:
                        self.parts.append("\n")
                break

    def handle_data(self, data: str) -> None:
        """记录可见文字，保留代码和段落内部内容。"""
        if not self.stack or not self.stack[-1][1]:
            self.parts.append(data)

    def text(self) -> str:
        """返回去除多余空行的正文。

        Returns:
            保留文字、链接与换行的正文。
        """
        return "\n".join(line.rstrip() for line in "".join(self.parts).splitlines() if line.strip()).strip()


class _ReplyPageParser(HTMLParser):
    """读取网页 JSON-LD、回复编号与页码，供感谢数和回复身份交叉校验。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.documents: List[str] = []
        self.reply_ids: List[str] = []
        self.page = 1
        self.pages = 1
        self._script: Optional[List[str]] = None

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        """记录结构化数据脚本和站点回复容器。"""
        values = dict(attrs)
        classes = (values.get("class") or "").split()
        if tag == "script" and values.get("type") == "application/ld+json":
            self._script = []
        identity = re.fullmatch(r"r_([1-9][0-9]*)", values.get("id") or "")
        if tag == "div" and "cell" in classes and identity:
            self.reply_ids.append(identity.group(1))
        if tag == "input" and "page_input" in classes:
            self.page = _integer(values.get("value")) or 1
            self.pages = _integer(values.get("max")) or 1

    def handle_endtag(self, tag: str) -> None:
        """收集完整结构化数据脚本。"""
        if tag == "script" and self._script is not None:
            self.documents.append("".join(self._script))
            self._script = None

    def handle_data(self, data: str) -> None:
        """原样保留 JSON 文本。"""
        if self._script is not None:
            self._script.append(data)


class V2exParser(BaseVideoParser):
    """解析公开主题主帖，并在已读取回复中优先展示感谢较多的评论。"""

    def __init__(self, hot_comment_count: int = 0) -> None:
        """初始化解析器与评论条数。

        Args:
            hot_comment_count: 评论输出上限，零表示不请求评论。
        """
        super().__init__("v2ex")
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)
        try:
            self.hot_comment_count = max(0, int(hot_comment_count))
        except (TypeError, ValueError, OverflowError):
            self.hot_comment_count = 0

    def can_parse(self, url: str) -> bool:
        """判断是否为公开主题入口。

        Args:
            url: 待判断的链接。

        Returns:
            链接是否包含可信域名与主题编号。
        """
        return bool(_topic_id(url))

    def extract_links(self, text: str) -> List[str]:
        """提取并按主题编号去重，保留原文链接以维持消息中的先后顺序。

        Args:
            text: 含分享链接的消息文本。

        Returns:
            按出现顺序排列的主题链接。
        """
        links: List[str] = []
        seen = set()
        for match in TOPIC_URL_RE.finditer(text or ""):
            value = match.group(0).rstrip(".,!?:")
            identity = _topic_id(value)
            if identity and identity not in seen:
                seen.add(identity)
                links.append(value)
        return links

    async def _request(self, session: aiohttp.ClientSession, url: str) -> str:
        """有界读取匿名响应，不跟随登录或其他重定向。"""
        try:
            async with session.get(
                url, headers=build_request_headers(custom_headers={"Accept": "application/json,text/html"}),
                timeout=aiohttp.ClientTimeout(total=25), allow_redirects=False,
            ) as response:
                if response.status == 429:
                    raise RuntimeError("V2EX 请求频率超限，请稍后再试")
                if response.status != 200:
                    raise RuntimeError(f"V2EX 请求失败（HTTP {response.status}），内容可能不可见或访问受限")
                chunks = []
                size = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise RuntimeError("V2EX 响应过大，已停止读取")
                    chunks.append(chunk)
                return b"".join(chunks).decode("utf-8")
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError) as exc:
            raise RuntimeError("V2EX 网络请求失败或响应编码无效") from exc

    @staticmethod
    def _content(item: Dict[str, Any], image_marker: bool = False) -> _ContentParser:
        """优先解析渲染正文；无渲染正文时保留接口原文。"""
        parser = _ContentParser(image_marker)
        rendered = item.get("content_rendered")
        if isinstance(rendered, str) and rendered.strip():
            parser.feed(rendered)
            parser.close()
        elif isinstance(item.get("content"), str):
            parser.parts.append(item["content"])
        return parser

    async def _thanks(
        self, session: aiohttp.ClientSession, identity: str, comments: Dict[str, Dict[str, Any]]
    ) -> Dict[str, int]:
        """最多读取五页感谢统计，按回复编号、作者与时间校验，不把它当作官方热评榜。"""
        thanks: Dict[str, int] = {}
        try:
            for page in range(1, MAX_COMMENT_PAGES + 1):
                parser = _ReplyPageParser()
                parser.feed(await self._request(session, f"{BASE_URL}/t/{identity}?p={page}"))
                parser.close()
                if parser.page != page:
                    raise RuntimeError("V2EX 评论网页页码不一致")
                topics = []
                for document in parser.documents:
                    item = json.loads(document)
                    if (isinstance(item, dict) and item.get("@type") == "DiscussionForumPosting"
                            and _topic_id(item.get("url")) == identity):
                        topics.append(item)
                if len(topics) != 1:
                    raise RuntimeError("V2EX 评论网页缺少当前主题数据")
                replies = topics[0].get("comment")
                if not isinstance(replies, list) or len(replies) != len(parser.reply_ids):
                    raise RuntimeError("V2EX 评论网页的回复身份无法对齐")
                previous = len(thanks)
                for reply_id, reply in zip(parser.reply_ids, replies):
                    source = comments.get(reply_id)
                    author = reply.get("author") if isinstance(reply, dict) else None
                    if (not source or not isinstance(author, dict)
                            or author.get("name") != source["username"]
                            or not source["time"] or _timestamp(reply.get("datePublished")) != source["time"]):
                        continue
                    statistic = reply.get("interactionStatistic")
                    if statistic is None:
                        thanks[reply_id] = 0
                    elif (isinstance(statistic, dict)
                            and statistic.get("interactionType") == "https://schema.org/LikeAction"):
                        count = _integer(statistic.get("userInteractionCount"))
                        if count is not None:
                            thanks[reply_id] = count
                if page >= parser.pages or len(thanks) == previous or len(thanks) >= len(comments):
                    break
        except asyncio.CancelledError:
            raise
        except (RuntimeError, ValueError) as exc:
            logger.warning(f"[v2ex] 感谢数获取失败，保留已取得统计并使用顺序回复补足：{exc}")
        return thanks

    async def _comments(self, session: aiohttp.ClientSession, identity: str) -> List[Dict[str, Any]]:
        """一次读取顺序回复，再用可取得的感谢数稳定排序，失败不影响主帖。"""
        try:
            # 旧接口实测忽略分页参数，不能通过重复请求模拟翻页。
            payload = json.loads(await self._request(session, f"{BASE_URL}/api/replies/show.json?topic_id={identity}"))
            if not isinstance(payload, list):
                raise RuntimeError("V2EX 回复接口未返回评论列表")
            comments: Dict[str, Dict[str, Any]] = {}
            for item in payload:
                if not isinstance(item, dict) or str(item.get("topic_id")) != identity:
                    continue
                reply_id = _integer(item.get("id"))
                member = item.get("member")
                if not reply_id or not isinstance(member, dict):
                    continue
                username = member.get("username")
                message = self._content(item, image_marker=True).text()
                if not isinstance(username, str) or not username.strip() or not message:
                    continue
                comments.setdefault(str(reply_id), {
                    "id": str(reply_id), "username": username,
                    "uid": str(member.get("id") or item.get("member_id") or ""),
                    "message": message, "time": _timestamp(item.get("created")),
                })
            if not comments:
                return []
            thanks = await self._thanks(session, identity, comments)
            for reply_id, count in thanks.items():
                comments[reply_id]["likes"] = count
            # Python 的稳定排序保留同感谢数、零感谢和未知感谢评论的楼层顺序。
            return sorted(comments.values(), key=lambda item: -item.get("likes", 0))[:self.hot_comment_count]
        except asyncio.CancelledError:
            raise
        except (RuntimeError, ValueError) as exc:
            logger.warning(f"[v2ex] 评论获取失败，已保留主帖正文和图片：{exc}")
            return []

    async def parse(self, session: aiohttp.ClientSession, url: str) -> Optional[MediaMetadata]:
        """匿名获取主题主帖与配置数量内的评论。

        Args:
            session: 解析管理器提供的 HTTP 会话。
            url: V2EX 主题链接。

        Returns:
            标题、作者、时间、主帖正文、配图与可选评论。

        Raises:
            ValueError: 链接不受支持。
            RuntimeError: 主题不可见、响应无效或身份不符。
        """
        identity = _topic_id(url)
        if not identity:
            raise ValueError("不支持的 V2EX 主题链接")
        async with self.semaphore:
            try:
                payload = json.loads(await self._request(session, f"{BASE_URL}/api/topics/show.json?id={identity}"))
            except ValueError as exc:
                raise RuntimeError("V2EX 主题接口返回的数据格式无效") from exc
            if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
                raise RuntimeError("V2EX 主题不存在、已删除或不是公开主题")
            topic = payload[0]
            if str(topic.get("id")) != identity or _topic_id(topic.get("url")) != identity:
                raise RuntimeError("V2EX 返回的主题身份与请求不一致")
            if topic.get("deleted") not in (None, 0, False):
                raise RuntimeError("V2EX 主题已删除")
            title = topic.get("title")
            member = topic.get("member")
            if (not isinstance(title, str) or not title.strip() or not isinstance(member, dict)
                    or not isinstance(member.get("username"), str)
                    or not any(isinstance(topic.get(key), str) for key in ("content", "content_rendered"))):
                raise RuntimeError("V2EX 主题缺少有效主帖数据")
            content = self._content(topic)
            metadata: MediaMetadata = {
                "url": f"{BASE_URL}/t/{identity}", "title": html.unescape(title),
                "author": member["username"], "timestamp": _timestamp(topic.get("created")),
                "desc": content.text(), "image_urls": content.images, "video_urls": [],
                # 网页配图使用 no-referrer，外部图床不附带主题 Referer 或账号信息。
                "image_headers": build_request_headers(), "platform": "V2EX",
            }
            if self.hot_comment_count and _integer(topic.get("replies")) != 0:
                comments = await self._comments(session, identity)
                if comments:
                    metadata["hot_comments"] = comments
            return metadata
