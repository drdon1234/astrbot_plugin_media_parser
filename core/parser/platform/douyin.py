"""抖音解析器实现。"""
import asyncio
import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import aiohttp

from ...constants import Config
from ...logger import logger
from ..utils import SkipParse, build_request_headers, is_live_url
from .base import BaseVideoParser
from .short_video_shared import ShortVideoParserMixin


DOUYIN_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 8.0.0; SM-G955U Build/R16NW) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/116.0.0.0 Mobile Safari/537.36"
)
DOUYIN_REFERER = "https://www.douyin.com/"


class DouyinParser(ShortVideoParserMixin, BaseVideoParser):

    """抖音解析器实现。"""

    def __init__(self):
        super().__init__("douyin")
        self.douyin_headers = {
            "User-Agent": DOUYIN_USER_AGENT,
            "Referer": (
                "https://www.douyin.com/?is_from_mobile_home=1&recommend=1"
            ),
            "Accept-Encoding": "gzip, deflate",
        }
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)

    @classmethod
    def _is_douyin_url(cls, url: str) -> bool:
        return cls._host_matches(
            cls._get_host(url),
            "douyin.com",
            "iesdouyin.com"
        )

    @staticmethod
    def _build_douyin_author(nickname: str, unique_id: str) -> str:
        if unique_id:
            return (
                f"{nickname}(uid:{unique_id})"
                if nickname else f"(uid:{unique_id})"
            )
        return nickname

    @classmethod
    def _is_supported_douyin_media_url(cls, url: str) -> bool:
        if not cls._is_douyin_url(url):
            return False
        try:
            parsed = urlparse(url)
        except Exception:
            return False
        path = parsed.path or ""
        host = cls._get_host(url)
        if host == "v.douyin.com":
            return True
        if re.search(r"/(?:share/)?(?:video|note|slides)/\d+", path):
            return True
        if re.search(r"\d{19}", path):
            return True
        return False

    def can_parse(self, url: str) -> bool:
        """判断是否可以解析此 URL。"""
        if not url:
            logger.debug(f"[{self.name}] can_parse: URL为空")
            return False

        if self._is_supported_douyin_media_url(url):
            logger.debug(f"[{self.name}] can_parse: 匹配抖音链接 {url}")
            return True

        logger.debug(f"[{self.name}] can_parse: 无法解析 {url}")
        return False

    def extract_links(self, text: str) -> List[str]:
        """从文本中提取抖音链接。"""
        result_links: List[str] = []
        seen_keys = set()
        seen_urls = set()

        patterns = [
            (
                r"https?://v\.douyin\.com/[^\s<>\"'()]+",
                lambda match, url: f"douyin:short:{url.lower()}",
            ),
            (
                r"https?://(?:www\.)?douyin\.com/note/(\d+)[^\s<>\"'()]*",
                lambda match, url: f"douyin:note:{match.group(1)}",
            ),
            (
                r"https?://(?:www\.)?douyin\.com/slides/(\d+)[^\s<>\"'()]*",
                lambda match, url: f"douyin:slides:{match.group(1)}",
            ),
            (
                r"https?://(?:www\.)?douyin\.com/video/(\d+)[^\s<>\"'()]*",
                lambda match, url: f"douyin:video:{match.group(1)}",
            ),
            (
                r"https?://(?:www\.)?douyin\.com/[^\s<>\"'()]*?(\d{19})"
                r"[^\s<>\"'()]*",
                lambda match, url: f"douyin:item:{match.group(1)}",
            ),
        ]

        for pattern, build_key in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                matched_url = self._clean_extracted_url(match.group(0))
                if not matched_url:
                    continue
                key = build_key(match, matched_url)
                if key in seen_keys or matched_url in seen_urls:
                    continue
                seen_keys.add(key)
                seen_urls.add(matched_url)
                result_links.append(matched_url)

        if result_links:
            logger.debug(
                f"[{self.name}] extract_links: 提取到 {len(result_links)} 个链接: "
                f"{result_links[:3]}{'...' if len(result_links) > 3 else ''}"
            )
        else:
            logger.debug(f"[{self.name}] extract_links: 未提取到链接")

        return result_links

    @staticmethod
    def _build_douyin_play_url(video_uri: str) -> str:
        if video_uri.endswith(".mp3") or video_uri.startswith(
            ("http://", "https://")
        ):
            return video_uri
        return (
            "https://www.douyin.com/aweme/v1/play/"
            f"?video_id={video_uri}"
        )

    @staticmethod
    def _looks_like_video_url(url: str) -> bool:
        normalized = str(url or "").lower()
        if not normalized.startswith(("http://", "https://")):
            return False
        return any(
            marker in normalized
            for marker in (
                ".mp4",
                ".m3u8",
                "mime_type=video",
                "video_id=",
                "/aweme/v1/play/",
                "/video/",
                "videoplayback",
                "douyinvod",
            )
        )

    def _extract_douyin_play_addr_urls(self, play_addr: Any) -> List[str]:
        urls: List[str] = []
        if not play_addr:
            return urls

        if isinstance(play_addr, dict):
            video_uri = str(play_addr.get("uri") or "").strip()
            if video_uri:
                self._extend_unique_urls(
                    urls,
                    [self._build_douyin_play_url(video_uri)]
                )
            self._extend_unique_urls(
                urls,
                self._extract_nested_http_urls(play_addr.get("url_list"))
            )
            self._extend_unique_urls(
                urls,
                self._extract_nested_http_urls(play_addr.get("urlList"))
            )
        else:
            self._extend_unique_urls(
                urls,
                self._extract_nested_http_urls(play_addr)
            )

        return [
            url
            for url in urls
            if self._looks_like_video_url(url) or "video_id=" in url
        ]

    def _extract_douyin_video_url_list(
        self,
        video_info: Any
    ) -> List[str]:
        """从标准 video 结构中提取一个视频媒体的备用 URL 列表。"""
        if not isinstance(video_info, dict):
            return []

        urls: List[str] = []
        for key in (
            "play_addr",
            "playAddr",
            "PlayAddr",
            "PlayAddrStruct",
            "download_addr",
            "downloadAddr",
            "play_addr_h264",
            "play_addr_265",
        ):
            self._extend_unique_urls(
                urls,
                self._extract_douyin_play_addr_urls(video_info.get(key))
            )

        for bitrate_info in video_info.get("bit_rate") or []:
            if not isinstance(bitrate_info, dict):
                continue
            for key in ("play_addr", "playAddr", "PlayAddr"):
                self._extend_unique_urls(
                    urls,
                    self._extract_douyin_play_addr_urls(
                        bitrate_info.get(key)
                    )
                )

        return urls

    def _extract_douyin_slide_video_url_list(self, image_item: Any) -> List[str]:
        """从 slides/images 条目中提取内嵌视频段 URL。"""
        if not isinstance(image_item, dict):
            return []

        video_roots = []
        for key in (
            "video",
            "video_info",
            "videoInfo",
            "video_clip",
            "videoClip",
            "clip",
            "clip_info",
            "clipInfo",
        ):
            value = image_item.get(key)
            if value:
                video_roots.append(value)

        if any(
            key in image_item
            for key in (
                "play_addr",
                "playAddr",
                "download_addr",
                "downloadAddr",
                "bit_rate",
            )
        ):
            video_roots.append(image_item)

        urls: List[str] = []
        for video_root in video_roots:
            if isinstance(video_root, dict):
                self._extend_unique_urls(
                    urls,
                    self._extract_douyin_video_url_list(video_root)
                )
            else:
                candidates = self._extract_nested_http_urls(video_root)
                self._extend_unique_urls(
                    urls,
                    [
                        candidate
                        for candidate in candidates
                        if self._looks_like_video_url(candidate)
                    ]
                )
        return urls

    def _extract_douyin_image_url_list(self, image_item: Any) -> List[str]:
        """从图片条目中提取图片 URL，避免把视频 URL 误收进图片。"""
        urls: List[str] = []
        if isinstance(image_item, dict):
            for key in (
                "url_list",
                "urlList",
                "UrlList",
                "urls",
                "url",
                "image",
                "imageURL",
                "imageUrl",
                "displayImage",
                "originImage",
                "downloadImage",
                "ownerWatermarkImage",
                "ownerWatermarkUrl",
                "cover",
                "origin_cover",
            ):
                if key in image_item:
                    self._extend_unique_urls(
                        urls,
                        self._extract_nested_http_urls(image_item.get(key))
                    )
        else:
            self._extend_unique_urls(
                urls,
                self._extract_nested_http_urls(image_item)
            )

        return [
            url
            for url in urls
            if not self._looks_like_video_url(url)
        ]

    def _extract_douyin_video_cover_url_list(
        self,
        video_info: Any
    ) -> List[str]:
        """从 video 结构中提取封面 URL。"""
        if not isinstance(video_info, dict):
            return []

        urls: List[str] = []
        for key in (
            "cover",
            "cover_url",
            "coverUrl",
            "origin_cover",
            "originCover",
            "dynamic_cover",
            "dynamicCover",
            "animated_cover",
            "animatedCover",
            "poster",
        ):
            if key in video_info:
                self._extend_unique_urls(
                    urls,
                    self._extract_nested_http_urls(video_info.get(key))
                )

        return [
            url
            for url in urls
            if not self._looks_like_video_url(url)
        ]

    def _extract_douyin_slide_cover_url_list(
        self,
        image_item: Any
    ) -> List[str]:
        """从 slides/images 视频条目中提取封面 URL。"""
        if not isinstance(image_item, dict):
            return []

        urls = self._extract_douyin_image_url_list(image_item)
        for key in (
            "video",
            "video_info",
            "videoInfo",
            "video_clip",
            "videoClip",
            "clip",
            "clip_info",
            "clipInfo",
        ):
            self._extend_unique_urls(
                urls,
                self._extract_douyin_video_cover_url_list(image_item.get(key))
            )
        return urls

    def _extract_douyin_media_url_lists(
        self,
        item_info: Dict[str, Any]
    ) -> tuple[List[List[str]], List[List[str]], List[List[str]]]:
        """提取视频段和图片段；视频存在时不再把条目降级成图集。"""
        video_url_lists: List[List[str]] = []
        image_url_lists: List[List[str]] = []
        video_cover_url_lists: List[List[str]] = []

        top_level_video_urls = self._extract_douyin_video_url_list(
            item_info.get("video")
        )
        if top_level_video_urls:
            video_url_lists.append(top_level_video_urls)
            video_cover_url_lists.append(
                self._extract_douyin_video_cover_url_list(item_info.get("video"))
            )
            return video_url_lists, image_url_lists, video_cover_url_lists

        for image_item in item_info.get("images") or []:
            slide_video_urls = self._extract_douyin_slide_video_url_list(
                image_item
            )
            if slide_video_urls:
                video_url_lists.append(slide_video_urls)
                video_cover_url_lists.append(
                    self._extract_douyin_slide_cover_url_list(image_item)
                )
                continue

            image_urls = self._extract_douyin_image_url_list(image_item)
            if image_urls:
                image_url_lists.append(image_urls)

        return video_url_lists, image_url_lists, video_cover_url_lists

    def _build_douyin_result_from_item(
        self,
        item_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        author_info = item_info.get("author", {})
        nickname = author_info.get("nickname", "")
        unique_id = author_info.get("unique_id", "")
        (
            video_url_lists,
            image_url_lists,
            video_cover_url_lists,
        ) = self._extract_douyin_media_url_lists(item_info)

        return {
            "title": item_info.get("desc", ""),
            "author": self._build_douyin_author(nickname, unique_id),
            "timestamp": self._format_timestamp(item_info.get("create_time")),
            "video_url_lists": video_url_lists,
            "video_url_list": video_url_lists[0] if video_url_lists else [],
            "video_cover_urls": video_cover_url_lists,
            "image_url_lists": image_url_lists,
            "is_gallery": bool(image_url_lists and not video_url_lists),
            "user_agent": DOUYIN_USER_AGENT,
        }

    @staticmethod
    def _extract_douyin_item_from_info(
        video_info: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        for key in ("item_list", "aweme_details", "aweme_list"):
            items = video_info.get(key)
            if isinstance(items, list) and items:
                for item in items:
                    if isinstance(item, dict) and item:
                        return item

        item = video_info.get("aweme_detail")
        if isinstance(item, dict) and item:
            return item

        return None

    async def fetch_douyin_slides_info(
        self,
        session: aiohttp.ClientSession,
        item_id: str,
        referer: str = ""
    ) -> Optional[Dict[str, Any]]:
        """通过前端 slidesinfo 接口获取图文/视频混排作品。"""
        headers = dict(self.douyin_headers)
        headers.update({
            "Accept": "application/json, text/plain, */*",
            "Referer": referer or DOUYIN_REFERER,
        })
        try:
            async with session.get(
                "https://www.iesdouyin.com/web/api/v2/aweme/slidesinfo/",
                params={
                    "aweme_ids": f"[{item_id}]",
                    "request_source": "200",
                },
                headers=headers,
            ) as response:
                if response.status >= 400:
                    return None
                data = await response.json(content_type=None)
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            json.JSONDecodeError,
        ):
            return None

        if not isinstance(data, dict):
            return None

        item_info = self._extract_douyin_item_from_info(data)
        if not item_info:
            return None

        return self._build_douyin_result_from_item(item_info)

    async def fetch_douyin_info(
        self,
        session: aiohttp.ClientSession,
        item_id: str,
        is_note: bool = False,
        is_slides: bool = False,
        referer: str = ""
    ) -> Optional[Dict[str, Any]]:
        """获取抖音视频 / 笔记信息。"""
        if is_slides:
            result = await self.fetch_douyin_slides_info(
                session,
                item_id,
                referer=referer
            )
            if result:
                return result
            url = f"https://www.iesdouyin.com/share/slides/{item_id}/"
        elif is_note:
            url = f"https://www.iesdouyin.com/share/note/{item_id}/"
        else:
            url = f"https://www.iesdouyin.com/share/video/{item_id}/"

        try:
            async with session.get(url, headers=self.douyin_headers) as response:
                if response.status >= 400:
                    return None
                response_text = await response.text()

            json_str = self.extract_router_data(response_text)
            if not json_str:
                return None

            json_str = json_str.replace("\\u002F", "/").replace("\\/", "/")
            try:
                json_data = json.loads(json_str)
            except Exception:
                return None

            loader_data = json_data.get("loaderData", {})
            video_info = None
            for value in loader_data.values():
                if isinstance(value, dict) and "videoInfoRes" in value:
                    video_info = value["videoInfoRes"]
                    break
                if isinstance(value, dict) and "noteDetailRes" in value:
                    video_info = value["noteDetailRes"]
                    break
                if isinstance(value, dict) and "slidesInfoRes" in value:
                    video_info = value["slidesInfoRes"]
                    break

            if not video_info:
                return None

            item_info = self._extract_douyin_item_from_info(video_info)
            if not item_info:
                return None
            return self._build_douyin_result_from_item(item_info)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

    @classmethod
    def _is_short_redirect_url(cls, url: str) -> bool:
        return cls._get_host(url) == "v.douyin.com"

    async def get_redirected_url(
        self,
        session: aiohttp.ClientSession,
        url: str
    ) -> str:
        """获取重定向后的 URL。"""
        try:
            async with session.head(
                url,
                headers=self.douyin_headers,
                allow_redirects=True,
            ) as response:
                redirected_url = str(response.url)
                if (
                    response.status < 400
                    and (
                        redirected_url != url
                        or not self._is_short_redirect_url(url)
                    )
                ):
                    return redirected_url
                logger.debug(
                    f"[{self.name}] HEAD未解析出有效跳转，回退GET: "
                    f"{url}, status={response.status}, "
                    f"redirected={redirected_url}"
                )
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError):
            logger.debug(f"[{self.name}] HEAD跳转解析失败，回退GET: {url}")

        try:
            async with session.get(
                url,
                headers=self.douyin_headers,
                allow_redirects=True,
            ) as response:
                return str(response.url)
        except asyncio.CancelledError:
            raise

    async def _parse_douyin(
        self,
        session: aiohttp.ClientSession,
        original_url: str,
        redirected_url: str
    ) -> Dict[str, Any]:
        is_note = "/note/" in redirected_url or "/note/" in original_url
        is_slides = "/slides/" in redirected_url or "/slides/" in original_url
        if is_note or is_slides:
            logger.debug(f"[{self.name}] parse: 检测到抖音笔记/图文类型")
            note_match = re.search(r"/(?:note|slides)/(\d+)", redirected_url)
            if not note_match:
                note_match = re.search(r"/(?:note|slides)/(\d+)", original_url)
            if not note_match:
                raise RuntimeError(f"无法解析此URL: {original_url}")

            note_id = note_match.group(1)
            result = await self.fetch_douyin_info(
                session,
                note_id,
                is_note=is_note and not is_slides,
                is_slides=is_slides,
                referer=redirected_url
            )
            display_url = (
                f"https://www.douyin.com/slides/{note_id}"
                if is_slides else
                f"https://www.douyin.com/note/{note_id}"
            )
        else:
            video_match = re.search(r"/video/(\d+)", redirected_url)
            if video_match:
                item_id = video_match.group(1)
            else:
                match = (
                    re.search(r"(\d{19})", redirected_url) or
                    re.search(r"(\d{19})", original_url)
                )
                if not match:
                    raise RuntimeError(f"无法解析此URL: {original_url}")
                item_id = match.group(1)

            result = await self.fetch_douyin_info(
                session,
                item_id,
                is_note=False
            )
            display_url = original_url

        if not result:
            raise RuntimeError(f"无法获取视频信息: {original_url}")

        result["display_url"] = display_url
        return result

    @staticmethod
    def _build_result_headers(user_agent: str) -> Dict[str, Dict[str, str]]:
        return {
            "image_headers": build_request_headers(
                is_video=False,
                referer=DOUYIN_REFERER,
                user_agent=user_agent,
            ),
            "video_headers": build_request_headers(
                is_video=True,
                referer=DOUYIN_REFERER,
                user_agent=user_agent,
            ),
        }

    async def parse(
        self,
        session: aiohttp.ClientSession,
        url: str
    ) -> Optional[Dict[str, Any]]:
        """解析单个抖音链接。"""
        logger.debug(f"[{self.name}] parse: 开始解析 {url}")
        async with self.semaphore:
            redirected_url = await self.get_redirected_url(session, url)
            if redirected_url != url:
                logger.debug(
                    f"[{self.name}] parse: URL重定向 {url} -> {redirected_url}"
                )

            if is_live_url(redirected_url) or is_live_url(url):
                logger.debug(
                    f"[{self.name}] parse: 检测到直播域名链接，跳过解析 "
                    f"{url} -> {redirected_url}"
                )
                raise SkipParse("直播域名链接不解析")

            result = await self._parse_douyin(session, url, redirected_url)
            is_gallery = bool(result.get("is_gallery", False))
            image_url_lists = [
                url_list
                for url_list in result.get("image_url_lists", [])
                if url_list
            ]
            video_url_lists = [
                url_list
                for url_list in result.get("video_url_lists", [])
                if url_list
            ]
            video_cover_urls = result.get("video_cover_urls") or []
            if not video_url_lists:
                video_url_list = result.get("video_url_list") or []
                if video_url_list:
                    video_url_lists = [video_url_list]
            title = result.get("title", "")
            author = result.get("author", "")
            timestamp = result.get("timestamp", "")
            display_url = result.get("display_url", url)
            user_agent = result.get("user_agent", DOUYIN_USER_AGENT)
            headers = self._build_result_headers(user_agent)

            if is_gallery and not video_url_lists:
                logger.debug(
                    f"[{self.name}] parse: 检测到图片集，共"
                    f"{len(image_url_lists)}张图片"
                )
                return {
                    "url": display_url,
                    "title": title,
                    "author": author,
                    "desc": "",
                    "timestamp": timestamp,
                    "platform": "douyin",
                    "parser_name": self.name,
                    "video_urls": [],
                    "video_cover_urls": [],
                    "image_urls": image_url_lists,
                    "image_headers": headers["image_headers"],
                    "video_headers": headers["video_headers"],
                }

            if not video_url_lists:
                logger.debug(f"[{self.name}] parse: 无法获取视频URL {url}")
                raise RuntimeError(f"无法获取视频URL: {url}")

            parsed_result = {
                "url": display_url,
                "title": title,
                "author": author,
                "desc": "",
                "timestamp": timestamp,
                "platform": "douyin",
                "parser_name": self.name,
                "video_urls": video_url_lists,
                "video_cover_urls": video_cover_urls,
                "image_urls": image_url_lists,
                "image_headers": headers["image_headers"],
                "video_headers": headers["video_headers"],
            }
            logger.debug(
                f"[{self.name}] parse: 解析完成(douyin) {url}, "
                f"title={title[:50]}"
            )
            return parsed_result
