"""AcFun 解析器，支持视频、动态与番剧页面。"""

import asyncio
import html
import json
import re
from datetime import datetime
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urljoin, urlparse

import aiohttp

from ...logger import logger

from ...constants import Config
from ...types import MediaMetadata
from ..utils import build_request_headers
from .base import BaseVideoParser


DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
ACFUN_HOSTS = frozenset({"acfun.cn", "www.acfun.cn", "m.acfun.cn"})
ACFUN_URL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.:/@%?=&#-])(?:https?://|//)?(?:www\.|m\.)?acfun\.cn"
    r"(?::[0-9]+)?/[^\s<>\"'()，。！？；：、（）【】《》「」,;!\u3400-\u9fff]+",
    re.IGNORECASE,
)
VIDEO_PATH_RE = re.compile(r"^/v/ac([0-9]+(?:_[1-9][0-9]*)?)/?$", re.IGNORECASE)
ARTICLE_PATH_RE = re.compile(r"^/a/ac([0-9]+)/?$", re.IGNORECASE)
BANGUMI_PATH_RE = re.compile(
    r"^/bangumi/aa([0-9]+(?:_[0-9]+_[0-9]+)?)/?$", re.IGNORECASE
)
SHARE_PATH_RE = re.compile(r"^/v/?$", re.IGNORECASE)
ARTICLE_API = "https://www.acfun.cn/rest/pc-direct/article/info"
PLAY_API = "https://www.acfun.cn/rest/pc-direct/play/playInfo/ksPlayJson"


def _parse_acfun_identity(url: str) -> Tuple[str, str]:
    """解析内容类型与包含分 P、分集信息的标识，拒绝站外链接。"""
    if not isinstance(url, str) or not url.strip():
        return "", ""
    normalized = html.unescape(url.strip())
    if normalized.startswith("//"):
        normalized = "https:" + normalized
    elif "://" not in normalized:
        normalized = "https://" + normalized
    try:
        parsed = urlparse(normalized)
        port = parsed.port
    except (TypeError, ValueError):
        return "", ""
    if parsed.scheme.lower() not in {"http", "https"}:
        return "", ""
    if parsed.username or parsed.password or port not in {None, 80, 443}:
        return "", ""
    host = (parsed.hostname or "").lower()
    if host not in ACFUN_HOSTS:
        return "", ""

    path = parsed.path or ""
    match = VIDEO_PATH_RE.fullmatch(path)
    if match:
        return "video", re.sub(r"_1$", "", match.group(1))
    match = ARTICLE_PATH_RE.fullmatch(path)
    if match:
        return "article", match.group(1)
    match = BANGUMI_PATH_RE.fullmatch(path)
    if match:
        parts = match.group(1).split("_")
        highlights = parse_qs(parsed.query, keep_blank_values=True).get("ac")
        if highlights is not None:
            if len(highlights) != 1 or not re.fullmatch(r"[1-9][0-9]*", highlights[0]):
                return "", ""
            # 番剧 ac 参数选择花絮，不能作为追踪参数移除。
            return "bangumi", f"{parts[0]}?ac={highlights[0]}"
        if len(parts) == 3:
            return "bangumi", f"{parts[0]}_36188_{parts[2]}"
        return "bangumi", parts[0]
    if SHARE_PATH_RE.fullmatch(path):
        share_id = (parse_qs(parsed.query).get("ac") or [""])[0]
        if re.fullmatch(r"[0-9]+(?:_[1-9][0-9]*)?", share_id):
            return "video", re.sub(r"_1$", "", share_id)
    return "", ""


def _canonical_url(kind: str, identity: str) -> str:
    """统一站点、协议和追踪参数，保留影响内容选择的路径。"""
    path = {"video": "v/ac", "article": "a/ac", "bangumi": "bangumi/aa"}[kind]
    return f"https://www.acfun.cn/{path}{identity}"


def _media_url(value: Any) -> str:
    """仅接收无用户信息的 HTTP 媒体地址。"""
    if not isinstance(value, str):
        return ""
    url = value.strip()
    if url.startswith("//"):
        url = "https:" + url
    try:
        parsed = urlparse(url)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port == 0
        ):
            return ""
    except ValueError:
        return ""
    return url


def _video_url(value: Any) -> str:
    """为 HLS 地址添加下载路由前缀。"""
    url = _media_url(value)
    if url and urlparse(url).path.lower().endswith(".m3u8"):
        return f"m3u8:{url}"
    return url


class _ArticleContent(HTMLParser):
    """按正文结构提取文本、配图及同一视频的候选地址。"""

    def __init__(self) -> None:
        """初始化正文提取状态。"""
        super().__init__(convert_charrefs=True)
        self.text: List[str] = []
        self.images: List[List[str]] = []
        self.videos: List[List[str]] = []
        self._video: Optional[List[str]] = None
        self._ignored: List[str] = []
        self._image_seen = set()

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        """处理正文标签，忽略脚本和页面样式。

        Args:
            tag: HTML 标签名。
            attrs: 标签属性。
        """
        if tag in {"script", "style", "noscript", "template"}:
            self._ignored.append(tag)
        if self._ignored:
            return
        values = dict(attrs)
        if tag == "br":
            self.text.append("\n")
        elif tag == "img":
            # 懒加载地址优先，避免将占位图当作作品图片。
            candidates = [
                _media_url(values.get(key))
                for key in ("data-original", "data-src", "src")
            ]
            url = next((url for url in candidates if url), "")
            if url and url not in self._image_seen:
                self._image_seen.add(url)
                self.images.append([url])
            elif not url and values.get("alt"):
                self.text.append(values["alt"])
        elif tag == "video":
            self._video = []
            self.videos.append(self._video)
            url = _video_url(values.get("src"))
            if url:
                self._video.append(url)
        elif tag == "source" and self._video is not None:
            url = _video_url(values.get("src"))
            if url and url not in self._video:
                self._video.append(url)

    def handle_endtag(self, tag: str) -> None:
        """保留段落换行并结束当前视频候选组。

        Args:
            tag: HTML 标签名。
        """
        if self._ignored:
            if tag == self._ignored[-1]:
                self._ignored.pop()
            return
        if tag == "video":
            self._video = None
        if tag in {
            "p", "div", "section", "article", "li", "blockquote",
            "h1", "h2", "h3", "h4", "h5", "h6",
        }:
            self.text.append("\n")

    def handle_data(self, data: str) -> None:
        """仅保留正文可见文字。

        Args:
            data: 标签之间的文字。
        """
        if not self._ignored:
            self.text.append(data)


class AcfunParser(BaseVideoParser):
    """解析 AcFun 视频、动态和番剧页面。"""

    def __init__(self) -> None:
        """初始化 AcFun 解析器。"""
        super().__init__("acfun")
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)
        self._headers = {
            "User-Agent": DESKTOP_UA,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8",
        }

    def can_parse(self, url: str) -> bool:
        """判断是否可以解析此 AcFun URL。

        Args:
            url: 待判断的链接。

        Returns:
            是否为支持的 AcFun 内容链接。
        """
        return _parse_acfun_identity(url) != ("", "")

    def extract_links(self, text: str) -> List[str]:
        """提取链接并按作品及分 P、分集标识去重。

        Args:
            text: 包含分享链接的消息文本。

        Returns:
            按出现顺序排列的规范链接。
        """
        links: List[str] = []
        seen = set()
        for match in ACFUN_URL_PATTERN.finditer(text or ""):
            link = match.group(0).rstrip(".,!?)]}>\"'，。！？；：）】》」")
            kind, content_id = _parse_acfun_identity(link)
            if not content_id:
                continue
            # 投稿和文章共用 ac 编号，移动分享入口也可能指向文章。
            identity = ("bangumi" if kind == "bangumi" else "ac", content_id)
            if identity in seen:
                continue
            seen.add(identity)
            links.append(_canonical_url(kind, content_id))
        if links:
            logger.debug(f"[{self.name}] 提取到 {len(links)} 个链接")
        return links

    # ── 页面与接口请求 ──────────────────────────────

    async def _fetch_page(self, session: aiohttp.ClientSession, url: str) -> str:
        """读取页面，仅允许重定向到同一内容的 AcFun 链接。"""
        original_kind, original_id = _parse_acfun_identity(url)
        for _ in range(4):
            try:
                async with session.get(
                    url,
                    headers={**self._headers, "Referer": "https://www.acfun.cn/"},
                    timeout=aiohttp.ClientTimeout(total=25),
                    allow_redirects=False,
                ) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        target = urljoin(url, response.headers.get("Location", ""))
                        kind, content_id = _parse_acfun_identity(target)
                        if (
                            not kind
                            or content_id != original_id
                            or (kind == "bangumi") != (original_kind == "bangumi")
                        ):
                            raise RuntimeError("AcFun 页面重定向到其他内容或不支持的地址")
                        url = target
                        continue
                    if response.status != 200:
                        raise RuntimeError(f"AcFun 页面请求失败（HTTP {response.status}）")
                    return await response.text(encoding="utf-8", errors="replace")
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                raise RuntimeError("AcFun 页面请求失败") from exc
        raise RuntimeError("AcFun 页面重定向次数过多")

    async def _fetch_api(
        self,
        session: aiohttp.ClientSession,
        endpoint: str,
        params: Dict[str, str],
        referer: str,
    ) -> Dict[str, Any]:
        """读取 AcFun 接口并校验业务状态。"""
        try:
            async with session.get(
                endpoint,
                params=params,
                headers={**self._headers, "Referer": referer},
                timeout=aiohttp.ClientTimeout(total=25),
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise RuntimeError(f"AcFun 接口请求失败（HTTP {response.status}）")
                data = await response.json()
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            raise RuntimeError("AcFun 接口请求失败或响应格式错误") from exc
        if not isinstance(data, dict) or data.get("result") != 0:
            raise RuntimeError("AcFun 接口未返回可用内容，内容可能已删除或访问受限")
        return data

    @staticmethod
    def _extract_state(page: str) -> Tuple[str, Dict[str, Any]]:
        """直接解码页面状态对象，不依赖脚本空格和后续分号。"""
        kinds = {"videoInfo": "video", "articleInfo": "article", "bangumiData": "bangumi"}
        match = re.search(r"window\.(videoInfo|articleInfo|bangumiData)\s*=\s*", page)
        if not match:
            raise RuntimeError("AcFun 页面缺少可解析的数据")
        try:
            value, _ = json.JSONDecoder().raw_decode(page[match.end():].lstrip())
        except json.JSONDecodeError as exc:
            raise RuntimeError("AcFun 页面内嵌数据格式错误") from exc
        if not isinstance(value, dict):
            raise RuntimeError("AcFun 页面内嵌数据不是对象")
        return kinds[match.group(1)], value

    @staticmethod
    def _validate_state(
        kind: str, identity: str, page_kind: str, state: Dict[str, Any]
    ) -> None:
        """核对作品及当前选集身份，禁止将其他内容作为回退结果。"""
        parts = identity.split("_")
        id_key = {"video": "dougaId", "article": "articleId", "bangumi": "bangumiId"}[page_kind]
        if (
            (kind == "bangumi") != (page_kind == "bangumi")
            or str(state.get(id_key, "")) != parts[0]
            or (kind == "article" and page_kind != "article")
        ):
            raise RuntimeError("AcFun 页面内容与请求的作品 ID 不一致")
        if page_kind == "article":
            if len(parts) != 1:
                raise RuntimeError("AcFun 文章不支持视频分 P 链接")
            return
        current = state.get("currentVideoInfo")
        if not isinstance(current, dict) or not current.get("id"):
            raise RuntimeError("AcFun 页面缺少当前视频信息，指定分 P 或分集可能不存在")
        current_id = str(current["id"])
        selected_id = state.get("videoId" if page_kind == "bangumi" else "currentVideoId")
        if selected_id is not None and str(selected_id) != current_id:
            raise RuntimeError("AcFun 页面当前视频 ID 不一致")
        if page_kind == "bangumi":
            if len(parts) == 3 and str(state.get("itemId", "")) != parts[2]:
                raise RuntimeError("AcFun 页面返回了其他番剧集数")
            return

        selected_part = int(parts[1]) if len(parts) == 2 else 1
        playlist = state.get("videoList")
        verified = False
        if isinstance(playlist, list) and playlist:
            if selected_part > len(playlist):
                raise RuntimeError("AcFun 指定的视频分 P 不存在")
            selected = playlist[selected_part - 1]
            if not isinstance(selected, dict) or str(selected.get("id", "")) != current_id:
                raise RuntimeError("AcFun 页面返回了其他视频分 P")
            verified = True
        for item in (state, current):
            if item.get("priority") is not None:
                if str(item["priority"]) != str(selected_part - 1):
                    raise RuntimeError("AcFun 页面返回了其他视频分 P")
                verified = True
        if selected_part > 1 and not verified:
            raise RuntimeError("AcFun 页面无法确认指定的视频分 P")

    @staticmethod
    def _resolve_highlight(
        identity: str, page_kind: str, state: Dict[str, Any]
    ) -> Tuple[str, str]:
        """将番剧花絮索引解析为经页面清单确认的投稿和视频 ID。"""
        series_id, _, index = identity.partition("?ac=")
        if page_kind != "bangumi" or str(state.get("bangumiId", "")) != series_id:
            raise RuntimeError("AcFun 花絮页面与请求的番剧 ID 不一致")
        highlights = state.get("sidelights")
        position = int(index) - 1
        if not isinstance(highlights, list) or position >= len(highlights):
            raise RuntimeError("AcFun 指定的番剧花絮不存在")
        item = highlights[position]
        if not isinstance(item, dict) or item.get("type") != 2:
            raise RuntimeError("AcFun 花絮不是可解析的视频投稿")
        content_id, video_id = str(item.get("contentId", "")), str(item.get("videoId", ""))
        if (
            not re.fullmatch(r"[0-9]+", content_id)
            or not re.fullmatch(r"[0-9]+", video_id)
            or str(item.get("vindex", position)) != str(position)
        ):
            raise RuntimeError("AcFun 花絮清单中的视频身份无效")
        return content_id, video_id

    # ── 媒体与元数据 ────────────────────────────────

    @staticmethod
    def _first_non_empty(*values: Any) -> str:
        """返回第一个非空字符串。"""
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _format_timestamp(value: Any) -> str:
        """将 AcFun 时间字段统一为可读字符串。"""
        if value in (None, ""):
            return ""
        if isinstance(value, str) and not value.strip().isdigit():
            return value.strip()
        try:
            timestamp = int(value)
            if timestamp > 10**12:
                timestamp //= 1000
            return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError, OSError, OverflowError):
            return ""

    @staticmethod
    def _cover_candidates(state: Dict[str, Any], bangumi: bool = False) -> List[str]:
        """提取同一封面的不同 CDN 地址，番剧优先使用本集封面。"""
        groups: List[List[Any]] = []
        urls = state.get("coverCdnUrls")
        groups.append([
            item.get("url") if isinstance(item, dict) else item
            for item in urls
        ] if isinstance(urls, list) else [])
        groups[-1].append(state.get("coverUrl"))
        info_keys = ("imgInfo", "coverImgHInfo", "coverImgVInfo") if bangumi else ("coverImgInfo",)
        if bangumi:
            groups.insert(0, [state.get("image")])
        for key in info_keys:
            info = state.get(key)
            if not isinstance(info, dict):
                continue
            values = [info.get("thumbnailImageCdnUrl")]
            for name in ("originImage", "expandedImage", "smallSharedImage", "thumbnailImage"):
                image_info = info.get(name)
                if isinstance(image_info, dict) and isinstance(image_info.get("cdnUrls"), list):
                    values.extend(
                        item.get("url") for item in image_info["cdnUrls"] if isinstance(item, dict)
                    )
            groups.append(values)
        for values in groups:
            candidates = list(dict.fromkeys(url for value in values if (url := _media_url(value))))
            if candidates:
                return candidates
        return []

    @staticmethod
    def _video_candidates(current_video: Dict[str, Any]) -> List[str]:
        """按 H.264 优先、清晰度降序返回同一视频的完整候选流。"""
        def number(value: Any) -> int:
            try:
                return max(0, int(value or 0))
            except (TypeError, ValueError, OverflowError):
                return 0

        result: List[str] = []
        seen = set()
        for key in ("ksPlayJson", "ksPlayJsonHevc"):
            payload = current_video.get(key)
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    continue
            if not isinstance(payload, dict):
                continue
            adaptations = payload.get("adaptationSet")
            if not isinstance(adaptations, list):
                continue
            representations: List[Dict[str, Any]] = []
            for adaptation in adaptations:
                if not isinstance(adaptation, dict):
                    continue
                items = adaptation.get("representation")
                if isinstance(items, list):
                    representations.extend(item for item in items if isinstance(item, dict))
            representations.sort(
                key=lambda item: (
                    number(item.get("width")) * number(item.get("height")),
                    number(item.get("maxBitrate")),
                    number(item.get("avgBitrate")),
                    number(item.get("frameRate")),
                ),
                reverse=True,
            )
            for representation in representations:
                values = [representation.get("url")]
                backups = representation.get("backupUrl")
                if isinstance(backups, list):
                    values.extend(backups)
                elif isinstance(backups, str):
                    values.append(backups)
                for value in values:
                    url = _video_url(value)
                    if url and url not in seen:
                        seen.add(url)
                        result.append(url)
        return result

    def _build_video_metadata(
        self, source_url: str, state: Dict[str, Any], page_kind: str
    ) -> MediaMetadata:
        """根据已校验的视频或番剧状态构造媒体元数据。"""
        current = state["currentVideoInfo"]
        candidates = self._video_candidates(current)
        if not candidates:
            raise RuntimeError("AcFun 未找到可用视频地址，内容可能已删除或访问受限")
        title = self._first_non_empty(state.get("title"), current.get("title"))
        if page_kind == "bangumi":
            series = self._first_non_empty(state.get("bangumiTitle"))
            episode = self._first_non_empty(state.get("episodeName"), title)
            title = self._first_non_empty(
                state.get("showTitle"), " ".join(filter(None, (series, episode)))
            )
        elif isinstance(state.get("videoList"), list) and len(state["videoList"]) > 1:
            part_title = self._first_non_empty(current.get("title"))
            if part_title and part_title != title:
                title = f"{title} - {part_title}" if title else part_title
        user = state.get("user") if isinstance(state.get("user"), dict) else {}
        metadata: MediaMetadata = {
            "url": source_url,
            "title": title,
            "author": self._first_non_empty(user.get("name")),
            "desc": self._first_non_empty(
                state.get("description"), state.get("introduction"), state.get("bangumiIntro")
            ),
            "timestamp": self._format_timestamp(
                state.get("createTimeMillis") or state.get("createTime")
                or current.get("uploadTime") or state.get("updateTime")
            ),
            "platform": "acfun",
            "video_urls": [candidates],
            "image_urls": [],
            "image_headers": build_request_headers(
                is_video=False, referer=source_url, user_agent=DESKTOP_UA
            ),
            "video_headers": build_request_headers(
                is_video=True, referer=source_url, user_agent=DESKTOP_UA
            ),
        }
        duration = current.get("durationMillis", state.get("durationMillis"))
        try:
            metadata["timelength_ms"] = max(0, int(duration)) if duration is not None else None
        except (TypeError, ValueError, OverflowError):
            metadata["timelength_ms"] = None
        covers = self._cover_candidates(state, bangumi=page_kind == "bangumi")
        if covers:
            metadata["video_cover_urls"] = [covers]
        return metadata

    def _build_article_metadata(self, source_url: str, state: Dict[str, Any]) -> MediaMetadata:
        """提取文章正文媒体，封面仅在正文没有媒体时补充。"""
        parts = state.get("parts") if isinstance(state.get("parts"), list) else []
        content = _ArticleContent()
        content.feed("\n".join(
            part["content"] for part in parts
            if isinstance(part, dict) and isinstance(part.get("content"), str)
        ))
        content.close()
        lines = [
            re.sub(r"[ \t\u00a0]+", " ", line).strip()
            for line in "".join(content.text).splitlines()
        ]
        videos: List[List[str]] = []
        seen = set()
        for candidates in content.videos:
            if candidates and not any(url in seen for url in candidates):
                videos.append(candidates)
                seen.update(candidates)
        images = content.images
        if not images and not videos:
            covers = self._cover_candidates(state)
            if covers:
                images = [covers]
        user = state.get("user") if isinstance(state.get("user"), dict) else {}
        metadata: MediaMetadata = {
            "url": source_url,
            "title": self._first_non_empty(state.get("title")),
            "author": self._first_non_empty(user.get("name")),
            "desc": "\n".join(line for line in lines if line)
            or self._first_non_empty(state.get("description")),
            "timestamp": self._format_timestamp(
                state.get("createTimeMillis") or state.get("createTime")
            ),
            "platform": "acfun",
            "video_urls": videos,
            "image_urls": images,
            "image_headers": build_request_headers(
                is_video=False, referer=source_url, user_agent=DESKTOP_UA
            ),
            "video_headers": build_request_headers(
                is_video=True, referer=source_url, user_agent=DESKTOP_UA
            ),
        }
        if not metadata["title"] and not metadata["desc"] and not images and not videos:
            raise RuntimeError("AcFun 动态未解析到可用内容")
        return metadata

    async def parse(
        self, session: aiohttp.ClientSession, url: str
    ) -> Optional[MediaMetadata]:
        """解析单个 AcFun 视频、动态或番剧链接。

        Args:
            session: 由解析管理器提供的 HTTP 会话。
            url: 待解析的内容链接。

        Returns:
            媒体元数据；不支持的链接返回空值。

        Raises:
            RuntimeError: 请求失败、内容身份不符或没有可用媒体。
        """
        kind, identity = _parse_acfun_identity(url)
        if not identity:
            return None
        source_url = _canonical_url(kind, identity)
        async with self.semaphore:
            try:
                page = await self._fetch_page(session, source_url)
                page_kind, state = self._extract_state(page)
            except RuntimeError as page_error:
                # ac 分享入口也可能是文章；只对没有选集信息的链接尝试文章接口。
                if kind == "bangumi" or "_" in identity:
                    raise
                try:
                    state = await self._fetch_api(
                        session, ARTICLE_API, {"articleId": identity}, source_url
                    )
                except RuntimeError:
                    raise page_error
                page_kind = "article"

            highlight_video_id = ""
            if kind == "bangumi" and "?ac=" in identity:
                identity, highlight_video_id = self._resolve_highlight(identity, page_kind, state)
                kind = "video"
                source_url = _canonical_url(kind, identity)
                page = await self._fetch_page(session, source_url)
                page_kind, state = self._extract_state(page)

            # 身份不符必须失败，不能再通过接口回退掩盖串内容问题。
            self._validate_state(kind, identity, page_kind, state)
            if highlight_video_id and (
                page_kind != "video" or str(state["currentVideoInfo"]["id"]) != highlight_video_id
            ):
                raise RuntimeError("AcFun 花絮投稿与番剧清单中的视频 ID 不一致")
            if page_kind == "article":
                return self._build_article_metadata(_canonical_url("article", identity), state)
            current = state["currentVideoInfo"]
            if not self._video_candidates(current):
                resource_id = identity.split("_")[0]
                data = await self._fetch_api(
                    session,
                    PLAY_API,
                    {
                        "videoId": str(current["id"]),
                        "resourceId": resource_id,
                        "resourceType": "1" if page_kind == "bangumi" else "2",
                        "mkey": str(state.get("mkey") or ""),
                    },
                    source_url,
                )
                play_info = data.get("playInfo")
                if isinstance(play_info, dict):
                    for key in ("ksPlayJson", "ksPlayJsonHevc"):
                        current[key] = play_info.get(key)
            return self._build_video_metadata(source_url, state, page_kind)


__all__ = ["AcfunParser"]
