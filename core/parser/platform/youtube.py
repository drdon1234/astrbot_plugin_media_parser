"""YouTube 链接解析器，使用内置播放器接口提取短时效媒体直链。"""

import asyncio
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

import aiohttp

from ...logger import logger

from ...constants import Config
from ...types import MediaMetadata
from ..utils import build_request_headers
from .base import BaseVideoParser


YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}
YOUTUBE_NOCOOKIE_HOSTS = {"youtube-nocookie.com", "www.youtube-nocookie.com"}
YOUTU_BE_HOSTS = {"youtu.be", "www.youtu.be"}
YOUTUBE_URL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.:/@-])https?://(?:(?:(?:www|m|music)\.)?youtube\.com|(?:www\.)?youtube-nocookie\.com|(?:www\.)?youtu\.be)/[^\s<>\"'()]+",
    re.IGNORECASE,
)
YOUTUBE_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,32}$")
YOUTUBE_PLAYER_API = "https://www.youtube.com/youtubei/v1/player"
ANDROID_CLIENT_VERSION = "20.10.38"
ANDROID_USER_AGENT = (
    "com.google.android.youtube/20.10.38 "
    "(Linux; U; Android 11) gzip"
)
DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
MAX_MUXED_CANDIDATES = 4


class YoutubeParser(BaseVideoParser):
    """解析 YouTube 常见单视频页、短链接、嵌入链接和分享跳转。"""

    def __init__(
        self,
        use_proxy: bool = False,
        proxy_url: Optional[str] = None,
        hot_comment_count: int = 0,
    ) -> None:
        """初始化 YouTube 解析器。

        Args:
            use_proxy: 解析与视频下载是否使用代理。
            proxy_url: 代理地址。
            hot_comment_count: 最多读取的根评论数量，0 表示关闭。
        """
        super().__init__("youtube")
        try:
            self.hot_comment_count = max(0, int(hot_comment_count))
        except (TypeError, ValueError, OverflowError):
            self.hot_comment_count = 0
        self.use_proxy = bool(use_proxy)
        self.proxy_url = proxy_url if self.use_proxy else None
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)

    @classmethod
    def _video_id_from_url(cls, url: str, _depth: int = 0) -> Optional[str]:
        """从 YouTube URL 提取视频 ID，并拒绝非 YouTube 主机。"""
        if not isinstance(url, str) or not url.strip():
            return None
        try:
            parsed = urlparse(url.strip())
            port = parsed.port
        except (TypeError, ValueError):
            return None
        if parsed.scheme.lower() not in {"http", "https"}:
            return None
        if parsed.username or parsed.password or port not in {None, 80, 443}:
            return None

        host = (parsed.hostname or "").lower().strip(".")
        path_parts = [part for part in (parsed.path or "").split("/") if part]
        video_id = ""
        if host in YOUTU_BE_HOSTS:
            if len(path_parts) == 1:
                video_id = path_parts[0]
        elif host in YOUTUBE_HOSTS:
            route = path_parts[0].lower() if path_parts else ""
            if host in YOUTUBE_NOCOOKIE_HOSTS and route != "embed":
                return None
            if route == "watch":
                video_id = (parse_qs(parsed.query).get("v") or [""])[0]
            elif len(path_parts) >= 2 and route in {
                "shorts",
                "embed",
                "v",
                "e",
            }:
                video_id = path_parts[1]
            elif route == "attribution_link" and _depth == 0:
                nested_url = (parse_qs(parsed.query).get("u") or [""])[0].strip()
                nested_url = unquote(nested_url)
                if nested_url.startswith("//"):
                    nested_url = f"https:{nested_url}"
                elif nested_url.startswith("/"):
                    nested_url = f"https://www.youtube.com{nested_url}"
                return cls._video_id_from_url(nested_url, _depth=1)

        video_id = video_id.strip()
        return video_id if YOUTUBE_VIDEO_ID_PATTERN.fullmatch(video_id) else None

    @classmethod
    def _canonical_url(cls, video_id: str) -> str:
        """构造稳定的规范视频页 URL。"""
        return f"https://www.youtube.com/watch?v={video_id}"

    def can_parse(self, url: str) -> bool:
        """判断是否可以解析此 YouTube URL。

        Args:
            url: 待判断的链接。

        Returns:
            是否为受支持的 YouTube 视频链接。
        """
        return self._video_id_from_url(url) is not None

    def extract_links(self, text: str) -> List[str]:
        """从文本中提取 YouTube 视频链接并按视频 ID 去重。

        Args:
            text: 包含待解析链接的文本。

        Returns:
            去重后的 YouTube 视频链接列表。
        """
        links: List[str] = []
        seen_ids = set()
        for match in YOUTUBE_URL_PATTERN.finditer(text or ""):
            link = match.group(0).rstrip(".,!?)]}>\"'，。！？；：）】》」")
            video_id = self._video_id_from_url(link)
            if video_id and video_id not in seen_ids:
                seen_ids.add(video_id)
                links.append(link)
        return links

    @staticmethod
    def _extract_initial_player_response(page: str) -> Optional[Dict[str, Any]]:
        """从页面脚本中读取嵌套 JSON 播放信息。"""
        if not page:
            return None
        decoder = json.JSONDecoder()
        for marker in ("ytInitialPlayerResponse =", "ytInitialPlayerResponse="):
            marker_index = page.find(marker)
            if marker_index < 0:
                continue
            payload = page[marker_index + len(marker) :].lstrip()
            try:
                value, _ = decoder.raw_decode(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        return None

    @staticmethod
    def _extract_bootstrap_value(page: str, key: str) -> str:
        """从页面启动配置中提取字符串值。"""
        match = re.search(
            rf'"{re.escape(key)}"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"',
            page or "",
        )
        if not match:
            return ""
        try:
            return json.loads(f'"{match.group(1)}"')
        except json.JSONDecodeError:
            return match.group(1)

    async def _fetch_page(
        self,
        session: aiohttp.ClientSession,
        page_url: str,
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        """读取 YouTube 页面及其初始播放信息。"""
        headers = {
            "User-Agent": DESKTOP_USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        try:
            async with session.get(
                page_url,
                headers=headers,
                proxy=self.proxy_url,
                timeout=aiohttp.ClientTimeout(total=25),
            ) as response:
                response.raise_for_status()
                page = await response.text()
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise RuntimeError(f"YouTube 页面请求失败: {exc}") from exc
        return page, self._extract_initial_player_response(page)

    async def _fetch_android_player(
        self,
        session: aiohttp.ClientSession,
        api_key: str,
        video_id: str,
        visitor_data: str,
    ) -> Optional[Dict[str, Any]]:
        """调用 YouTube 内置 Android 播放接口获取带签名直链。"""
        if not api_key:
            return None
        payload = {
            "context": {
                "client": {
                    "clientName": "ANDROID",
                    "clientVersion": ANDROID_CLIENT_VERSION,
                    "androidSdkVersion": 30,
                    "hl": "zh-CN",
                    "gl": "CN",
                }
            },
            "videoId": video_id,
            "contentCheckOk": True,
            "racyCheckOk": True,
        }
        headers = {
            "Content-Type": "application/json",
            "User-Agent": ANDROID_USER_AGENT,
            "X-YouTube-Client-Name": "3",
            "X-YouTube-Client-Version": ANDROID_CLIENT_VERSION,
        }
        if visitor_data:
            headers["X-Goog-Visitor-Id"] = visitor_data
        try:
            async with session.post(
                YOUTUBE_PLAYER_API,
                params={"key": api_key},
                json=payload,
                headers=headers,
                proxy=self.proxy_url,
                timeout=aiohttp.ClientTimeout(total=25),
            ) as response:
                response.raise_for_status()
                result = await response.json(content_type=None)
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
            logger.debug(f"[{self.name}] Android 播放接口请求失败: {exc}")
            return None
        return result if isinstance(result, dict) else None

    @staticmethod
    def _valid_media_url(value: Any) -> Optional[str]:
        """校验播放直链。"""
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = urlparse(value.strip())
        except (TypeError, ValueError):
            return None
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None
        return value.strip()

    # ── 评论 ──────────────────────────────────────────────

    @staticmethod
    def _page_json_objects(page: str, pattern: str) -> Iterable[Dict[str, Any]]:
        """解码页面脚本赋值中的完整 JSON 对象。"""
        decoder = json.JSONDecoder()
        for match in re.finditer(pattern, page):
            try:
                value, _ = decoder.raw_decode(page[match.end():].lstrip())
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value

    @staticmethod
    def _walk_dicts(value: Any) -> Iterable[Dict[str, Any]]:
        """遍历页面状态中的字典以定位评论面板。"""
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from YoutubeParser._walk_dicts(child)
        elif isinstance(value, list):
            for child in value:
                yield from YoutubeParser._walk_dicts(child)

    @staticmethod
    def _continuation_token(endpoint: Any) -> str:
        """读取已定位的评论入口令牌。"""
        if not isinstance(endpoint, dict):
            return ""
        command = endpoint.get("continuationCommand")
        if not isinstance(command, dict):
            return ""
        token = command.get("token")
        return token if isinstance(token, str) else ""

    @classmethod
    def _initial_comment_token(cls, initial: Dict[str, Any]) -> str:
        """优先使用评论面板的热门入口，否则使用根评论默认入口。"""
        fallback = ""
        for node in cls._walk_dicts(initial):
            panel = node.get("engagementPanelSectionListRenderer")
            header = node.get("commentsHeaderRenderer")
            if isinstance(panel, dict) and "comments-section" in str(panel.get("targetId")):
                header = panel.get("header")
            if isinstance(header, dict):
                for child in cls._walk_dicts(header):
                    menu = child.get("sortFilterSubMenuRenderer")
                    if not isinstance(menu, dict):
                        continue
                    entries = menu.get("subMenuItems")
                    if not isinstance(entries, list):
                        continue
                    entries = [item for item in entries if isinstance(item, dict)]
                    preferred = [item for item in entries if re.search(
                        r"top|热门|熱門|最佳|相关|相關", str(item.get("title") or ""), re.I
                    )]
                    for item in preferred + entries:
                        token = cls._continuation_token(item.get("serviceEndpoint"))
                        if token:
                            return token
            section = node.get("itemSectionRenderer")
            if not isinstance(section, dict) or section.get("sectionIdentifier") != "comment-item-section":
                continue
            entries = section.get("contents")
            for item in entries if isinstance(entries, list) else []:
                continuation = item.get("continuationItemRenderer") if isinstance(item, dict) else None
                if isinstance(continuation, dict) and not fallback:
                    fallback = cls._continuation_token(continuation.get("continuationEndpoint"))
        return fallback

    @classmethod
    def _comment_page(
        cls, payload: Dict[str, Any]
    ) -> Tuple[List[Dict[str, Any]], str]:
        """按根评论呈现顺序关联实体，仅提取根列表的下一页令牌。"""
        entities = {}
        for node in cls._walk_dicts(payload.get("frameworkUpdates")):
            entity = node.get("commentEntityPayload")
            if isinstance(entity, dict) and isinstance(entity.get("key"), str):
                entities[entity["key"]] = entity
        items: List[Dict[str, Any]] = []
        for key in ("onResponseReceivedEndpoints", "onResponseReceivedActions"):
            endpoints = payload.get(key)
            for endpoint in endpoints if isinstance(endpoints, list) else []:
                if not isinstance(endpoint, dict):
                    continue
                for command_name in ("reloadContinuationItemsCommand", "appendContinuationItemsAction"):
                    command = endpoint.get(command_name)
                    if not isinstance(command, dict):
                        continue
                    if "comments-section" not in str(command.get("targetId") or ""):
                        continue
                    values = command.get("continuationItems")
                    if isinstance(values, list):
                        items.extend(item for item in values if isinstance(item, dict))
        comments = []
        next_token = ""
        for item in items:
            continuation = item.get("continuationItemRenderer")
            if isinstance(continuation, dict):
                next_token = cls._continuation_token(continuation.get("continuationEndpoint"))
                continue
            thread = item.get("commentThreadRenderer")
            if not isinstance(thread, dict):
                continue
            container = thread.get("commentViewModel")
            view = container.get("commentViewModel") if isinstance(container, dict) else None
            if not isinstance(view, dict):
                continue
            entity = entities.get(view.get("commentKey"))
            if not isinstance(entity, dict):
                continue
            properties = entity.get("properties")
            if not isinstance(properties, dict) or properties.get("replyLevel", 0) != 0:
                continue
            comment_id = str(properties.get("commentId") or "")
            if not comment_id or (view.get("commentId") and view["commentId"] != comment_id):
                continue
            content = properties.get("content")
            message = str(content.get("content") or "").strip() if isinstance(content, dict) else ""
            if not message:
                continue
            author = entity.get("author")
            author = author if isinstance(author, dict) else {}
            comment = {
                "id": comment_id,
                "uid": str(author.get("channelId") or ""),
                "username": str(author.get("displayName") or ""),
                "message": message,
                "time": str(properties.get("publishedTime") or ""),
            }
            toolbar = entity.get("toolbar")
            if isinstance(toolbar, dict):
                likes = toolbar.get("likeCountNotliked")
                if isinstance(likes, (str, int)) and str(likes).strip():
                    comment["likes"] = likes
            comments.append(comment)
        return comments, next_token

    async def _fetch_hot_comments(
        self, session: aiohttp.ClientSession, page: str, canonical_url: str
    ) -> List[Dict[str, Any]]:
        """复用观看页网页客户端上下文，有限读取热门根评论。"""
        comments: List[Dict[str, Any]] = []
        if not self.hot_comment_count:
            return comments
        try:
            config: Dict[str, Any] = {}
            for value in self._page_json_objects(page, r"ytcfg\.set\s*\("):
                config.update(value)
            initial = next(self._page_json_objects(page, r"\bytInitialData\s*="), {})
            context = config.get("INNERTUBE_CONTEXT")
            client = context.get("client") if isinstance(context, dict) else None
            if not isinstance(client, dict):
                return comments
            token = self._initial_comment_token(initial)
            seen_tokens = set()
            seen_ids = set()
            headers = {
                "User-Agent": str(client.get("userAgent") or DESKTOP_USER_AGENT),
                "Origin": "https://www.youtube.com",
                "Referer": canonical_url,
                "X-Youtube-Client-Name": str(config.get("INNERTUBE_CONTEXT_CLIENT_NAME") or 1),
                "X-Youtube-Client-Version": str(client.get("clientVersion") or ""),
            }
            if client.get("visitorData"):
                headers["X-Goog-Visitor-Id"] = str(client["visitorData"])
            params = {"prettyPrint": "false"}
            if config.get("INNERTUBE_API_KEY"):
                params["key"] = str(config["INNERTUBE_API_KEY"])
            for _ in range(20):
                if not token or token in seen_tokens:
                    break
                seen_tokens.add(token)
                async with session.post(
                    "https://www.youtube.com/youtubei/v1/next",
                    params=params,
                    json={"context": context, "continuation": token},
                    headers=headers,
                    proxy=self.proxy_url,
                    timeout=aiohttp.ClientTimeout(total=25),
                ) as response:
                    response.raise_for_status()
                    payload = await response.json(content_type=None)
                if not isinstance(payload, dict) or payload.get("error"):
                    raise RuntimeError("YouTube 评论接口未返回有效数据")
                entries, token = self._comment_page(payload)
                previous_count = len(seen_ids)
                for comment in entries:
                    if comment["id"] in seen_ids:
                        continue
                    seen_ids.add(comment["id"])
                    comments.append(comment)
                    if len(comments) >= self.hot_comment_count:
                        return comments
                if entries and len(seen_ids) == previous_count:
                    break
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, TypeError, ValueError, RecursionError) as exc:
            logger.warning(f"[{self.name}] 评论获取失败，已保留视频：{exc}")
        return comments

    @staticmethod
    def _format_number(value: Any) -> int:
        """安全转换播放格式中的数值。"""
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError, OverflowError):
            return 0

    @classmethod
    def _format_score(cls, item: Dict[str, Any], is_video: bool) -> Tuple[int, int, int]:
        """按容器兼容性、画质和码率排序播放格式。"""
        mime_type = str(item.get("mimeType") or "").lower()
        if is_video:
            container_score = (
                3
                if mime_type.startswith("video/mp4") and "avc1" in mime_type
                else 2
                if mime_type.startswith("video/mp4")
                else 1
                if mime_type.startswith("video/")
                else 0
            )
        else:
            container_score = (
                2
                if mime_type.startswith("audio/mp4")
                else 1
                if mime_type.startswith("audio/")
                else 0
            )
        return (
            container_score,
            cls._format_number(item.get("height")) if is_video else 0,
            cls._format_number(item.get("bitrate")),
        )

    @classmethod
    def _build_video_urls(cls, streaming_data: Dict[str, Any]) -> List[str]:
        """将 muxed 与 DASH 格式转换为下载器候选 URL。"""
        raw_formats: List[Dict[str, Any]] = []
        for key in ("formats", "adaptiveFormats"):
            values = streaming_data.get(key) or []
            if isinstance(values, list):
                raw_formats.extend(item for item in values if isinstance(item, dict))

        muxed: List[Tuple[Dict[str, Any], str]] = []
        video_only: List[Tuple[Dict[str, Any], str]] = []
        audio_only: List[Tuple[Dict[str, Any], str]] = []
        for item in raw_formats:
            media_url = cls._valid_media_url(item.get("url"))
            if not media_url:
                continue
            mime_type = str(item.get("mimeType") or "").lower()
            has_video = mime_type.startswith("video/") or bool(item.get("width"))
            has_audio = mime_type.startswith("audio/") or bool(item.get("audioQuality"))
            if not has_audio and has_video:
                has_audio = bool(
                    re.search(r"(?:mp4a|opus|vorbis|ac-3|ec-3)", mime_type)
                )
            if has_video and has_audio:
                muxed.append((item, media_url))
            elif has_video:
                video_only.append((item, media_url))
            elif has_audio:
                audio_only.append((item, media_url))

        candidates: List[str] = []
        if video_only and audio_only:
            video = max(video_only, key=lambda pair: cls._format_score(pair[0], True))
            audio = max(audio_only, key=lambda pair: cls._format_score(pair[0], False))
            candidates.append(f"dash:{video[1]}||{audio[1]}")

        muxed.sort(key=lambda pair: cls._format_score(pair[0], True), reverse=True)
        for _, media_url in muxed[:MAX_MUXED_CANDIDATES]:
            if media_url not in candidates:
                candidates.append(media_url)
        return candidates

    @staticmethod
    def _first_thumbnail(details: Dict[str, Any], microformat: Dict[str, Any]) -> str:
        """选择最高质量的可用封面。"""
        containers = [
            details.get("thumbnail"),
            microformat.get("thumbnail"),
        ]
        thumbnails: List[Dict[str, Any]] = []
        for container in containers:
            if isinstance(container, dict) and isinstance(container.get("thumbnails"), list):
                thumbnails.extend(
                    item for item in container["thumbnails"] if isinstance(item, dict)
                )
        for item in sorted(
            thumbnails,
            key=lambda value: (
                YoutubeParser._format_number(value.get("width")),
                YoutubeParser._format_number(value.get("height")),
            ),
            reverse=True,
        ):
            media_url = YoutubeParser._valid_media_url(item.get("url"))
            if media_url:
                return media_url
        return ""

    @staticmethod
    def _unique(values: Iterable[str]) -> List[str]:
        """去重并保持候选顺序。"""
        result: List[str] = []
        seen = set()
        for value in values:
            if value and value not in seen:
                seen.add(value)
                result.append(value)
        return result

    async def parse(
        self,
        session: aiohttp.ClientSession,
        url: str,
    ) -> Optional[MediaMetadata]:
        """解析 YouTube 视频元数据与短时效媒体直链。

        Args:
            session: aiohttp 会话。
            url: YouTube 视频链接。

        Returns:
            包含标题、作者、封面和视频候选直链的媒体元数据。
        """
        async with self.semaphore:
            video_id = self._video_id_from_url(url)
            if not video_id:
                raise RuntimeError(f"无法解析 YouTube 视频 ID: {url}")
            canonical_url = self._canonical_url(video_id)
            page, initial_response = await self._fetch_page(session, canonical_url)

            api_key = self._extract_bootstrap_value(page, "INNERTUBE_API_KEY")
            visitor_data = self._extract_bootstrap_value(page, "VISITOR_DATA")
            player_response = await self._fetch_android_player(
                session,
                api_key,
                video_id,
                visitor_data,
            )
            api_playability = (
                player_response.get("playabilityStatus")
                if isinstance(player_response, dict)
                else None
            )
            if not isinstance(api_playability, dict) or api_playability.get("status") != "OK":
                player_response = initial_response

            if not isinstance(player_response, dict):
                raise RuntimeError("YouTube 页面未返回有效播放信息")
            playability = player_response.get("playabilityStatus") or {}
            if not isinstance(playability, dict):
                playability = {}
            if playability.get("status") not in (None, "OK"):
                reason = playability.get("reason") or "视频不可播放"
                raise RuntimeError(f"YouTube 视频不可用: {reason}")

            streaming_data = player_response.get("streamingData") or {}
            if not isinstance(streaming_data, dict):
                streaming_data = {}
            video_urls = self._build_video_urls(streaming_data)
            if not video_urls and player_response is not initial_response:
                fallback_streaming_data = (
                    initial_response.get("streamingData")
                    if isinstance(initial_response, dict)
                    else None
                )
                if isinstance(fallback_streaming_data, dict):
                    fallback_urls = self._build_video_urls(fallback_streaming_data)
                    if fallback_urls:
                        player_response = initial_response
                        video_urls = fallback_urls
            if not video_urls:
                raise RuntimeError(
                    "YouTube 未返回可用视频直链，可能受到地区、登录或反爬限制"
                )

            details = player_response.get("videoDetails") or {}
            microformat = (
                (player_response.get("microformat") or {}).get(
                    "playerMicroformatRenderer", {}
                )
            )
            if not isinstance(details, dict):
                details = {}
            if not isinstance(microformat, dict):
                microformat = {}
            title = str(details.get("title") or microformat.get("title") or "").strip()
            author = str(
                details.get("author")
                or microformat.get("ownerChannelName")
                or ""
            ).strip()
            description = str(details.get("shortDescription") or "").strip()
            timestamp = str(
                microformat.get("publishDate")
                or microformat.get("uploadDate")
                or ""
            ).strip()
            thumbnail = self._first_thumbnail(details, microformat)
            duration_ms = self._format_number(details.get("lengthSeconds")) * 1000

            video_headers = build_request_headers(
                is_video=True,
                referer=canonical_url,
                origin="https://www.youtube.com",
                user_agent=ANDROID_USER_AGENT,
            )
            metadata: MediaMetadata = {
                "url": canonical_url,
                "title": title,
                "author": author,
                "desc": description,
                "timestamp": timestamp,
                "platform": "youtube",
                "timelength_ms": duration_ms or None,
                "video_urls": [self._unique(video_urls)],
                "video_cover_urls": [[thumbnail]] if thumbnail else [],
                "image_urls": [],
                "image_headers": build_request_headers(
                    is_video=False,
                    referer=canonical_url,
                    origin="https://www.youtube.com",
                    user_agent=DESKTOP_USER_AGENT,
                ),
                "video_headers": video_headers,
                "use_video_proxy": bool(self.proxy_url),
                "proxy_url": self.proxy_url,
            }
            if self.hot_comment_count:
                comments = await self._fetch_hot_comments(session, page, canonical_url)
                if comments:
                    metadata["hot_comments"] = comments
            logger.debug(
                f"[{self.name}] 解析完成 video_id={video_id}, "
                f"候选数={len(video_urls)}, title={title[:50]}"
            )
            return metadata
