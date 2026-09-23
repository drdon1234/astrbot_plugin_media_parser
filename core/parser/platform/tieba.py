"""百度贴吧解析器，提取电脑端与手机端帖子首楼的图文和视频。"""

import asyncio
import hashlib
import html
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urljoin, urlparse

import aiohttp

from ...logger import logger

from ...constants import Config
from ...types import MediaMetadata
from ..utils import build_request_headers
from .base import BaseVideoParser


TIEBA_API = "https://tieba.baidu.com/c/f/pb/page"
CLIENT_VERSION = "12.35.1.0"
MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Mobile Safari/537.36"
)
TIEBA_HOSTS = frozenset({"tieba.baidu.com", "tiebac.baidu.com", "wapp.baidu.com"})
TIEBA_URL_RE = re.compile(
    r"(?<![A-Za-z0-9_.:/@%?=&#-])(?:https?://|//)?(?:tiebac?|wapp)\.baidu\.com"
    r"(?::[0-9]+)?/[^\s<>\"'()，。！？；：、（）【】《》「」,;!]+",
    re.IGNORECASE,
)
THREAD_ID_RE = re.compile(r"[1-9][0-9]{0,19}")


def _parse_tieba_url(url: str) -> Tuple[str, str]:
    """识别可信贴吧主机上各种帖子入口中的主题标识。"""
    if not isinstance(url, str) or not url.strip():
        return "", ""
    normalized = html.unescape(url.strip())
    if normalized.startswith("//"):
        normalized = "https:" + normalized
    elif "://" not in normalized:
        normalized = "https://" + normalized
    try:
        parsed = urlparse(normalized)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or (parsed.hostname or "").lower() not in TIEBA_HOSTS
            or parsed.username or parsed.password
            or parsed.port not in {None, 80, 443}
        ):
            return "", ""
    except (TypeError, ValueError):
        return "", ""

    path = parsed.path.rstrip("/")
    post = re.fullmatch(r"/p/([1-9][0-9]{0,19})", path)
    if post:
        return "post", post.group(1)
    query = parse_qs(parsed.query, keep_blank_values=True)
    keys: Tuple[str, ...] = ()
    if re.fullmatch(r"/mo/q[^/]*/m", path):
        keys = ("kz",)
    elif path == "/f":
        keys = ("kz", "z")
    elif path == "/mo/q/movideo/page":
        keys = ("thread_id",)
    values = [value for key in keys for value in query.get(key, [])]
    if values and len(set(values)) == 1 and THREAD_ID_RE.fullmatch(values[0]):
        return "post", values[0]
    return "", ""


def _media_url(value: Any) -> str:
    """清理媒体地址，保留签名参数并仅接受 HTTP 资源。"""
    if not isinstance(value, str):
        return ""
    url = html.unescape(value.strip())
    if url.startswith("//"):
        url = "https:" + url
    try:
        parsed = urlparse(url)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in {None, 80, 443}
        ):
            return ""
    except ValueError:
        return ""
    # 贴吧媒体 CDN 同时支持 HTTPS，保留路径和签名查询串不作改写。
    if parsed.scheme.lower() == "http" and (
        parsed.hostname == "baidu.com" or parsed.hostname.endswith(".baidu.com")
        or parsed.hostname == "bdstatic.com" or parsed.hostname.endswith(".bdstatic.com")
    ):
        url = "https:" + url[len(parsed.scheme) + 1:]
    return url


def _candidates(*values: Any) -> List[str]:
    """按质量优先顺序收集同一媒体的可用地址。"""
    return list(dict.fromkeys(url for value in values if (url := _media_url(value))))


def _video_url(value: Any) -> str:
    """只将实际播放资源交给下载器，排除视频网页和外站播放器。"""
    url = _media_url(value)
    if not url:
        return ""
    path = urlparse(url).path.lower()
    if path.endswith(".m3u8"):
        return f"m3u8:{url}"
    if path.endswith((".mp4", ".webm", ".flv", ".mov", ".m4v")):
        return url
    return ""


class TiebaParser(BaseVideoParser):
    """解析百度贴吧帖子首楼，支持图文、动图与原生视频。"""

    def __init__(self) -> None:
        """初始化贴吧解析器及并发限制。"""
        super().__init__("tieba")
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)

    def can_parse(self, url: str) -> bool:
        """判断是否为可解析的贴吧帖子链接。

        Args:
            url: 待判断的链接。

        Returns:
            是否为包含明确帖子身份的受支持链接。
        """
        return _parse_tieba_url(url) != ("", "")

    def extract_links(self, text: str) -> List[str]:
        """提取手机与电脑分享链接，并按帖子身份去重。

        Args:
            text: 包含分享链接的消息文本。

        Returns:
            按出现顺序排列的规范链接。
        """
        links: List[str] = []
        seen = set()
        for match in TIEBA_URL_RE.finditer(text or ""):
            link = match.group(0).rstrip(".,!?)]}>\"'，。！？；：）】》」")
            kind, identity = _parse_tieba_url(link)
            if not kind or (kind, identity) in seen:
                continue
            seen.add((kind, identity))
            links.append(f"https://tieba.baidu.com/p/{identity}")
        return links

    # ── 帖子接口请求 ────────────────────────────────

    async def _fetch_thread(
        self,
        session: aiohttp.ClientSession,
        thread_id: str,
    ) -> Dict[str, Any]:
        """请求帖子第一页，以客户端完整内容字段获取首楼。"""
        data = {
            "_client_type": "2",
            "_client_version": CLIENT_VERSION,
            "kz": thread_id,
            "lz": "1",
            "pn": "1",
            "rn": "2",
            "with_floor": "0",
        }
        signature = "".join(f"{key}={data[key]}" for key in sorted(data)) + "tiebaclient!!!"
        data["sign"] = hashlib.md5(signature.encode("utf-8")).hexdigest().upper()
        try:
            async with session.post(
                TIEBA_API,
                data=data,
                headers={
                    "User-Agent": MOBILE_UA,
                    "Referer": f"https://tieba.baidu.com/p/{thread_id}",
                    "Accept": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=25),
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise RuntimeError(f"贴吧帖子请求失败（HTTP {response.status}）")
                payload = await response.json(content_type=None, encoding="utf-8")
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            raise RuntimeError("贴吧帖子请求失败或响应格式错误") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("贴吧帖子接口返回的数据不是对象")
        if str(payload.get("error_code", "")) != "0":
            message = payload.get("error_msg")
            detail = (
                message.strip() if isinstance(message, str) and message.strip()
                else "帖子可能已删除或访问受限"
            )
            raise RuntimeError(f"贴吧帖子获取失败：{detail}")
        return payload

    # ── 首楼身份与内容提取 ──────────────────────────

    @staticmethod
    def _first_post(
        thread_id: str, payload: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """要求接口明确返回请求的主题帖及其首楼，不能以回复补位。"""
        thread = payload.get("thread")
        if not isinstance(thread, dict) or str(thread.get("id", "")) != thread_id:
            raise RuntimeError("贴吧接口返回的帖子身份与请求不一致")
        posts = payload.get("post_list")
        if not isinstance(posts, list):
            raise RuntimeError("贴吧接口缺少首楼内容，帖子可能已删除或访问受限")
        first_posts = [
            post for post in posts
            if isinstance(post, dict) and str(post.get("floor", "")) == "1"
        ]
        if len(first_posts) != 1:
            raise RuntimeError("贴吧接口未返回唯一首楼内容，不能使用回复替代正文")
        post = first_posts[0]
        first_post_id = thread.get("post_id")
        if (
            first_post_id not in (None, "", 0, "0")
            and str(post.get("id", "")) != str(first_post_id)
        ):
            raise RuntimeError("贴吧接口返回的首楼身份不一致")
        if not isinstance(post.get("content"), list):
            raise RuntimeError("贴吧帖子首楼缺少完整正文")
        return thread, post

    @staticmethod
    def _format_timestamp(value: Any) -> str:
        """将帖子发布时间转换为本地可读日期。"""
        try:
            timestamp = int(value)
            if timestamp > 0:
                return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
            return ""
        except (TypeError, ValueError, OSError, OverflowError):
            return ""

    @staticmethod
    def _author_name(thread: Dict[str, Any], post: Dict[str, Any], payload: Dict[str, Any]) -> str:
        """优先使用主题作者昵称，必要时按首楼作者标识查找用户。"""
        author = thread.get("author")
        if not isinstance(author, dict):
            author = {}
        if not author:
            users = payload.get("user_list")
            if isinstance(users, list):
                author = next((
                    user for user in users if isinstance(user, dict)
                    and str(user.get("id", "")) == str(post.get("author_id", ""))
                ), {})
        for key in ("name_show", "name"):
            value = author.get(key)
            if isinstance(value, str) and value.strip():
                return html.unescape(value.strip())
        return ""

    @staticmethod
    def _video_info_candidates(info: Dict[str, Any]) -> List[str]:
        """提取接口明确标注的视频播放地址，按清晰度从高到低排列。"""
        descriptions = info.get("video_desc")
        descriptions = (
            [item for item in descriptions if isinstance(item, dict)]
            if isinstance(descriptions, list) else []
        )

        def quality(item: Dict[str, Any]) -> int:
            try:
                width = max(0, int(item.get("video_width") or 0))
                height = max(0, int(item.get("video_height") or 0))
                return width * height
            except (TypeError, ValueError, OverflowError):
                return 0

        values = [item.get("video_url") for item in sorted(descriptions, key=quality, reverse=True)]
        values.append(info.get("video_url"))
        return list(dict.fromkeys(url for value in values if (url := _video_url(value))))

    @staticmethod
    def _fragment_text(fragment: Dict[str, Any]) -> str:
        """提取正文文字、链接卡片和表情含义。"""
        kind = str(fragment.get("type", ""))
        value = fragment.get("text")
        text = html.unescape(value) if isinstance(value, str) else ""
        if kind in {"0", "4", "9", "18", "27", "40"}:
            return text
        if kind == "7":
            return "\n"
        if kind == "1":
            link = _media_url(fragment.get("link"))
            return f"{text}（{link}）" if text and link and link != text else text or link
        if kind in {"35", "36", "37"}:
            card = fragment.get("tiebaplus_info")
            card = card if isinstance(card, dict) else {}
            label = card.get("desc")
            label = text or (html.unescape(label) if isinstance(label, str) else "")
            link = _media_url(card.get("jump_url"))
            return f"{label}（{link}）" if label and link else label or link
        if kind in {"2", "11"}:
            label = fragment.get("c")
            if isinstance(label, str) and label.strip():
                return f"[{html.unescape(label.strip())}]"
            return ""
        if kind == "10":
            return "\n[语音内容请打开原帖收听]\n"
        return ""

    @staticmethod
    def _clean_text(text: str) -> str:
        """合并内容片段并保留原有段落，不删除用户输入的标签或比较符。"""
        return "\n".join(
            re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in text.splitlines()
        ).strip()

    def _build_metadata(self, thread_id: str, payload: Dict[str, Any]) -> MediaMetadata:
        """根据已校验的首楼内容生成统一元数据，不遍历回复和推荐。"""
        thread, post = self._first_post(thread_id, payload)
        texts: List[str] = []
        images: List[List[str]] = []
        videos: List[List[str]] = []
        covers: List[List[str]] = []
        image_seen = set()
        video_seen = set()
        video_fragments: List[Dict[str, Any]] = []
        for fragment in post["content"]:
            if not isinstance(fragment, dict):
                continue
            kind = str(fragment.get("type", ""))
            texts.append(self._fragment_text(fragment))
            if kind in {"3", "11", "16", "20"}:
                if kind == "11":
                    # 贴图表情是独立图片；dynamic 可能是相对 static 的动图文件名。
                    static = _media_url(fragment.get("static"))
                    dynamic = fragment.get("dynamic")
                    dynamic = urljoin(static, dynamic) if isinstance(dynamic, str) else ""
                    candidates = _candidates(dynamic, static, fragment.get("src"))
                else:
                    graffiti = fragment.get("graffiti_info")
                    graffiti = graffiti if isinstance(graffiti, dict) else {}
                    meme = fragment.get("meme_info")
                    meme = meme if isinstance(meme, dict) else {}
                    candidates = _candidates(
                        fragment.get("origin_src"), graffiti.get("url"), meme.get("pic_url"),
                        fragment.get("big_cdn_src"), fragment.get("cdn_src_active"),
                        fragment.get("cdn_src"), fragment.get("src"), meme.get("thumbnail"),
                    )
                if candidates and not image_seen.intersection(candidates):
                    images.append(candidates)
                    image_seen.update(candidates)
            elif kind == "5":
                video_fragments.append(fragment)

        info = thread.get("video_info")
        info = info if isinstance(info, dict) else {}
        info_candidates = self._video_info_candidates(info)
        info_covers = _candidates(*(info.get(key) for key in (
            "small_thumbnail_url", "thumbnail_url", "first_frame_thumbnail"
        )))
        for fragment in video_fragments:
            candidates = list(dict.fromkeys(url for value in (
                fragment.get("link"), fragment.get("video_url")
            ) if (url := _video_url(value))))
            # 主题视频信息描述首楼的原生视频；只在唯一视频或地址一致时并入候选。
            matching_info = (
                len(video_fragments) == 1
                or bool(set(candidates).intersection(info_candidates))
            )
            if matching_info:
                candidates = list(dict.fromkeys(info_candidates + candidates))
            if not candidates:
                if str(fragment.get("e_type", "")) == "15":
                    raise RuntimeError("贴吧原生视频缺少可用播放地址，内容可能已删除或访问受限")
                link = _media_url(fragment.get("link")) or _media_url(fragment.get("text"))
                texts.append(f"\n[外站视频请打开原帖查看]{' ' + link if link else ''}\n")
                continue
            if video_seen.intersection(candidates):
                continue
            video_seen.update(candidates)
            videos.append(candidates)
            fragment_covers = _candidates(fragment.get("src"))
            covers.append(list(dict.fromkeys(
                (info_covers if matching_info else []) + fragment_covers
            )))
        if info_candidates and not video_fragments:
            videos.append(info_candidates)
            covers.append(info_covers)

        url = f"https://tieba.baidu.com/p/{thread_id}"
        title = thread.get("title")
        metadata: MediaMetadata = {
            "url": url,
            "title": html.unescape(title.strip()) if isinstance(title, str) else "",
            "author": self._author_name(thread, post, payload),
            "desc": self._clean_text("".join(texts)),
            "timestamp": self._format_timestamp(post.get("time") or thread.get("create_time")),
            "platform": self.name,
            "image_urls": images,
            "video_urls": videos,
            "image_headers": build_request_headers(
                is_video=False, referer=url, user_agent=MOBILE_UA
            ),
            "video_headers": build_request_headers(
                is_video=True, referer=url, user_agent=MOBILE_UA
            ),
        }
        if videos:
            metadata["video_cover_urls"] = covers
            try:
                fragment_duration = (
                    video_fragments[0].get("during_time") if len(video_fragments) == 1 else 0
                )
                duration = int(info.get("video_duration") or fragment_duration or 0)
                if duration > 0 and len(videos) == 1:
                    metadata["timelength_ms"] = duration * 1000
            except (TypeError, ValueError, OverflowError):
                pass
        return metadata

    async def _append_shared_post(
        self,
        session: aiohttp.ClientSession,
        thread: Dict[str, Any],
        metadata: MediaMetadata,
    ) -> None:
        """转发卡片仅展开一层原帖，复用完整首楼解析并保留转发者身份。"""
        if str(thread.get("is_share_thread", "")) != "1":
            return
        origin = thread.get("origin_thread_info")
        origin = origin if isinstance(origin, dict) else {}
        origin_id = str(origin.get("tid", ""))
        if not THREAD_ID_RE.fullmatch(origin_id) or origin_id == str(thread.get("id", "")):
            metadata["desc"] = (metadata["desc"] + "\n\n[转发原帖信息不完整]").strip()
            return
        origin_url = f"https://tieba.baidu.com/p/{origin_id}"
        try:
            payload = await self._fetch_thread(session, origin_id)
            shared = self._build_metadata(origin_id, payload)
        except asyncio.CancelledError:
            raise
        except (RuntimeError, ValueError) as exc:
            logger.debug(f"[{self.name}] 转发原帖 {origin_id} 无法读取：{exc}")
            metadata["desc"] = (
                metadata["desc"] + f"\n\n转发原帖：{origin_url}\n[原帖已删除或暂时无法读取]"
            ).strip()
            return
        lines = ["转发原帖：" + shared["title"], shared["author"], origin_url, shared["desc"]]
        metadata["desc"] = (metadata["desc"] + "\n\n" + "\n".join(filter(None, lines))).strip()
        image_seen = {url for group in metadata["image_urls"] for url in group}
        for group in shared["image_urls"]:
            if not image_seen.intersection(group):
                metadata["image_urls"].append(group)
                image_seen.update(group)
        video_seen = {url for group in metadata["video_urls"] for url in group}
        covers = metadata.setdefault("video_cover_urls", [[] for _ in metadata["video_urls"]])
        shared_covers = shared.get("video_cover_urls", [])
        for index, group in enumerate(shared["video_urls"]):
            if not video_seen.intersection(group):
                metadata["video_urls"].append(group)
                covers.append(shared_covers[index] if index < len(shared_covers) else [])
                video_seen.update(group)
        if len(metadata["video_urls"]) == 1 and not metadata.get("timelength_ms"):
            if shared.get("timelength_ms"):
                metadata["timelength_ms"] = shared["timelength_ms"]
        elif len(metadata["video_urls"]) > 1:
            metadata.pop("timelength_ms", None)

    async def parse(
        self, session: aiohttp.ClientSession, url: str
    ) -> Optional[MediaMetadata]:
        """解析贴吧链接对应的首楼正文及媒体。

        Args:
            session: 由解析管理器提供的会话。
            url: 电脑端或手机端帖子链接。

        Returns:
            首楼标题、作者、时间、正文与图片和视频候选地址。

        Raises:
            ValueError: 链接不受支持。
            RuntimeError: 请求失败、帖子失效或首楼内容不完整。
        """
        async with self.semaphore:
            _, thread_id = _parse_tieba_url(url)
            if not thread_id:
                raise ValueError("不支持的百度贴吧帖子链接")
            payload = await self._fetch_thread(session, thread_id)
            metadata = self._build_metadata(thread_id, payload)
            await self._append_shared_post(session, payload["thread"], metadata)
            logger.debug(
                f"[{self.name}] 解析完成：{thread_id}，"
                f"图片 {len(metadata['image_urls'])} 张，视频 {len(metadata['video_urls'])} 个"
            )
            return metadata
