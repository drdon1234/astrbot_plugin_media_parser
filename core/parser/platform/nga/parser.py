"""NGA 解析器，通过客户端接口提取公开帖子的首楼图文和评论。"""

import asyncio
import html
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import aiohttp

from ....logger import logger

from ....constants import Config
from ....types import MediaMetadata
from ...utils import build_request_headers
from ..base import BaseVideoParser
from .content import parse_content


NGA_API = "https://ngabbs.com/app_api.php?__lib=post&__act=list"
CLIENT_UA = "NGA_skull/6.0.5(iPhone10,3;iOS 12.0.1)"
MAX_COMMENT_PAGES = 20
NGA_HOSTS = frozenset({"bbs.nga.cn", "nga.178.com", "ngabbs.com"})
THREAD_ID_RE = re.compile(r"[1-9][0-9]{0,19}")
NGA_URL_RE = re.compile(
    r"(?<![A-Za-z0-9_.:/@%?=&#-])(?:https?://|//)?"
    r"(?:bbs\.nga\.cn|nga\.178\.com|ngabbs\.com)"
    r"(?::[0-9]+)?/[^\s<>\"'()，。！？；：、（）【】《》「」,;!]+",
    re.IGNORECASE,
)


def _thread_id(url: str) -> str:
    """只从可信主机的帖子入口提取唯一有效的主题编号。"""
    if not isinstance(url, str) or not url.strip():
        return ""
    normalized = html.unescape(url.strip())
    if normalized.startswith("//"):
        normalized = "https:" + normalized
    elif "://" not in normalized:
        normalized = "https://" + normalized
    try:
        parsed = urlparse(normalized)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or (parsed.hostname or "").lower() not in NGA_HOSTS
            or parsed.username or parsed.password
            or parsed.port not in {None, 80, 443}
            or parsed.path != "/read.php"
        ):
            return ""
        values = parse_qs(parsed.query, keep_blank_values=True).get("tid", [])
    except (TypeError, ValueError):
        return ""
    if values and len(set(values)) == 1 and THREAD_ID_RE.fullmatch(values[0]):
        return values[0]
    return ""


class NgaParser(BaseVideoParser):
    """解析 NGA 公开帖子的首楼图文，并按配置附加热门或顺序回复。"""

    def __init__(self, hot_comment_count: int = 0) -> None:
        """初始化平台名、评论数量与请求并发限制。

        Args:
            hot_comment_count: 最多返回的评论条数，零表示关闭。
        """
        super().__init__("nga")
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)
        try:
            self.hot_comment_count = max(0, int(hot_comment_count))
        except (TypeError, ValueError, OverflowError):
            self.hot_comment_count = 0

    def can_parse(self, url: str) -> bool:
        """判断是否为支持的 NGA 帖子链接。

        Args:
            url: 待判断的链接。

        Returns:
            是否包含受支持的帖子地址和有效主题编号。
        """
        return bool(_thread_id(url))

    def extract_links(self, text: str) -> List[str]:
        """从分享文本提取帖子链接，并按主题编号去重。

        Args:
            text: 包含分享链接的消息文本。

        Returns:
            按出现顺序排列的规范帖子链接。
        """
        links: List[str] = []
        seen = set()
        for match in NGA_URL_RE.finditer(html.unescape(text or "")):
            link = match.group(0).rstrip(".,!?)]}>\"'，。！？；：）】》」")
            thread_id = _thread_id(link)
            if thread_id and thread_id not in seen:
                seen.add(thread_id)
                links.append(f"https://bbs.nga.cn/read.php?tid={thread_id}")
        return links

    # ── 接口请求与首楼校验 ──────────────────────────

    async def _fetch_thread(
        self, session: aiohttp.ClientSession, thread_id: str, *, page: int = 1
    ) -> Dict[str, Any]:
        """以访客身份请求客户端接口的指定页，不跟随跳转。"""
        data = {"tid": thread_id}
        if page > 1:
            data["page"] = str(page)
        try:
            async with session.post(
                NGA_API,
                data=data,
                headers={
                    "X-User-Agent": CLIENT_UA,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                cookies={"guestJs": str(int(time.time()))},
                timeout=aiohttp.ClientTimeout(total=25),
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise RuntimeError(f"NGA 帖子请求失败（HTTP {response.status}）")
                payload = await response.json(content_type=None, encoding="utf-8")
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            raise RuntimeError("NGA 帖子请求失败或响应格式错误") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("NGA 帖子接口返回的数据不是对象")
        if str(payload.get("code", "")) != "0":
            message = payload.get("msg")
            detail = (
                html.unescape(message.strip())[:300]
                if isinstance(message, str) and message.strip()
                else "帖子可能已删除、访问受限或接口拒绝请求"
            )
            raise RuntimeError(f"NGA 帖子获取失败：{detail}")
        return payload

    @staticmethod
    def _first_post(thread_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """通过首楼标识与主题编号确认正文，不能将回复当作首帖。"""
        posts = payload.get("result")
        if not isinstance(posts, list):
            raise RuntimeError("NGA 接口缺少楼层列表，帖子可能已删除或访问受限")
        first_posts = [
            post for post in posts
            if isinstance(post, dict)
            and str(post.get("pid", "")) == "0"
            and str(post.get("lou", "")) == "0"
        ]
        if len(first_posts) != 1:
            raise RuntimeError("NGA 接口未返回唯一首楼，帖子可能已删除或访问受限")
        post = first_posts[0]
        if str(post.get("tid", "")) != thread_id:
            raise RuntimeError("NGA 接口返回的帖子编号与请求不一致")
        if not isinstance(post.get("content"), str):
            raise RuntimeError("NGA 帖子首楼缺少完整正文")
        return post

    @staticmethod
    def _format_timestamp(post: Dict[str, Any]) -> str:
        """优先采用接口提供的发布时间，时间戳作为字段缺失时的补充。"""
        date = post.get("postdate")
        if isinstance(date, str) and date.strip():
            return date.strip()
        try:
            timestamp = int(post.get("postdatetimestamp"))
            if timestamp > 0:
                return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError, OSError, OverflowError):
            pass
        return ""

    @staticmethod
    def _author_name(payload: Dict[str, Any], post: Dict[str, Any]) -> str:
        """优先使用主题作者名，避免将接口中的 UID 占位名称作为昵称。"""
        author = post.get("author")
        author = author if isinstance(author, dict) else {}
        for value in (payload.get("tauthor"), author.get("nickname"), author.get("username")):
            if isinstance(value, str) and value.strip():
                return html.unescape(value.strip())
        return ""

    # ── 热门楼层与顺序回复 ──────────────────────────

    @staticmethod
    def _reply_posts(value: Any, thread_id: str) -> List[Dict[str, Any]]:
        """只接纳主题一致且带有明确回复编号和正楼层的楼层对象。"""
        if not isinstance(value, list):
            return []
        return [
            post for post in value
            if isinstance(post, dict)
            and str(post.get("tid", "")) == thread_id
            and THREAD_ID_RE.fullmatch(str(post.get("pid", "")))
            and THREAD_ID_RE.fullmatch(str(post.get("lou", "")))
        ]

    def _build_comment(
        self, post: Dict[str, Any], attach_prefix: str
    ) -> Optional[Dict[str, Any]]:
        """沿用正文转换规则生成评论文字，配图仅作为文字占位。"""
        content = post.get("content")
        if not isinstance(content, str):
            return None
        message, images = parse_content(content, attach_prefix, post.get("attches"))
        if images:
            message = (message + "\n[图片]").strip()
        if not message:
            return None
        author = post.get("author")
        author = author if isinstance(author, dict) else {}
        comment: Dict[str, Any] = {
            "id": str(post["pid"]),
            "username": self._author_name({}, post),
            "uid": str(author.get("uid") or ""),
            "message": message,
            "time": self._format_timestamp(post),
        }
        likes = post.get("vote_good")
        if isinstance(likes, (int, str)) and not isinstance(likes, bool):
            try:
                comment["likes"] = max(0, int(likes))
            except ValueError:
                pass
        return comment

    async def _fetch_hot_comments(
        self, session: aiohttp.ClientSession, thread_id: str, payload: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """优先取接口热门楼层，无可用热评时有界读取顺序回复。"""
        comments: List[Dict[str, Any]] = []
        seen = set()
        try:
            prefix = payload.get("attachPrefix")
            prefix = prefix if isinstance(prefix, str) else ""
            for post in self._reply_posts(payload.get("hot_post"), thread_id):
                comment = self._build_comment(post, prefix)
                if comment and comment["id"] not in seen:
                    seen.add(comment["id"])
                    comments.append(comment)
                    if len(comments) >= self.hot_comment_count:
                        break
            if comments:
                return comments

            last_floor = 0
            for page_number in range(1, MAX_COMMENT_PAGES + 1):
                if page_number > 1:
                    payload = await self._fetch_thread(session, thread_id, page=page_number)
                current_page = int(payload.get("currentPage") or 1)
                if current_page != page_number:
                    raise RuntimeError("NGA 评论接口返回的页码与请求不一致")
                posts = payload.get("result")
                if not isinstance(posts, list):
                    raise RuntimeError("NGA 评论接口缺少回复列表")
                prefix = payload.get("attachPrefix")
                prefix = prefix if isinstance(prefix, str) else ""
                previous_count = len(seen)
                replies = sorted(
                    self._reply_posts(posts, thread_id), key=lambda post: int(post["lou"])
                )
                for post in replies:
                    post_id = str(post["pid"])
                    floor = int(post["lou"])
                    if post_id in seen or floor <= last_floor:
                        continue
                    seen.add(post_id)
                    last_floor = floor
                    comment = self._build_comment(post, prefix)
                    if comment:
                        comments.append(comment)
                        if len(comments) >= self.hot_comment_count:
                            return comments
                total_pages = max(1, int(payload.get("totalPage") or 1))
                if len(seen) == previous_count or page_number >= total_pages:
                    break
        except asyncio.CancelledError:
            raise
        except (RuntimeError, TypeError, ValueError, OverflowError) as exc:
            logger.warning(f"[{self.name}] 评论获取失败，已保留首帖和已获取评论：{exc}")
        return comments

    async def parse(
        self, session: aiohttp.ClientSession, url: str
    ) -> Optional[MediaMetadata]:
        """获取公开帖子首楼与可选评论并转换为现有图文元数据。

        Args:
            session: 由解析管理器提供的会话。
            url: 带有主题编号的 NGA 帖子链接。

        Returns:
            首楼标题、作者、时间、正文、图片候选地址与可选评论。

        Raises:
            ValueError: 链接不受支持。
            RuntimeError: 请求失败、访问受限或返回的首楼无效。
        """
        async with self.semaphore:
            thread_id = _thread_id(url)
            if not thread_id:
                raise ValueError("不支持的 NGA 帖子链接")
            payload = await self._fetch_thread(session, thread_id)
            post = self._first_post(thread_id, payload)
            prefix = payload.get("attachPrefix")
            desc, images = parse_content(
                post["content"], prefix if isinstance(prefix, str) else "",
                post.get("attches"),
            )
            if not desc and not images:
                raise RuntimeError("NGA 帖子首楼没有可用正文或图片，可能已删除或访问受限")
            title = payload.get("tsubject")
            canonical_url = f"https://bbs.nga.cn/read.php?tid={thread_id}"
            metadata: MediaMetadata = {
                "url": canonical_url,
                "title": html.unescape(title.strip()) if isinstance(title, str) else "",
                "author": self._author_name(payload, post),
                "timestamp": self._format_timestamp(post),
                "desc": desc,
                "image_urls": images,
                "image_headers": build_request_headers(referer=canonical_url),
                # 图片 CDN 会拒绝 Python 默认握手，仅调整套件列表并保留证书校验。
                "image_tls_ciphers": "ECDHE+AESGCM:ECDHE+CHACHA20",
                "video_urls": [],
                "platform": "NGA",
            }
            if self.hot_comment_count:
                comments = await self._fetch_hot_comments(session, thread_id, payload)
                if comments:
                    metadata["hot_comments"] = comments
            logger.debug(f"[{self.name}] 解析完成：{thread_id}，图片 {len(images)} 张")
            return metadata
