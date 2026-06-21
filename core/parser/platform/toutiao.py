"""今日头条解析器。"""
import asyncio
import base64
import binascii
import html
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

import aiohttp

from ...constants import Config
from ...logger import logger
from ..utils import SkipParse, build_request_headers
from .base import BaseVideoParser


MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Mobile Safari/537.36"
)

VOD_API_BASE = "https://vod.bytedanceapi.com/"
MAX_ARTICLE_IMAGE_REFRESHES = 5
URL_TAIL_RE = r"[^\s<>\"'()，。！？；：）】》」]*"


class ToutiaoParser(BaseVideoParser):

    """今日头条文章/视频解析器。"""

    ARTICLE_LINK_RE = re.compile(
        rf"https?://(?:www\.)?toutiao\.com/article/\d+{URL_TAIL_RE}",
        re.IGNORECASE,
    )
    MOBILE_ARTICLE_LINK_RE = re.compile(
        rf"https?://m\.toutiao\.com/article/\d+{URL_TAIL_RE}",
        re.IGNORECASE,
    )
    VIDEO_LINK_RE = re.compile(
        rf"https?://(?:www\.)?toutiao\.com/video/\d+{URL_TAIL_RE}",
        re.IGNORECASE,
    )
    MOBILE_VIDEO_LINK_RE = re.compile(
        rf"https?://m\.toutiao\.com/video/\d+{URL_TAIL_RE}",
        re.IGNORECASE,
    )
    W_LINK_RE = re.compile(
        rf"https?://(?:www|m)\.toutiao\.com/w/\d+{URL_TAIL_RE}",
        re.IGNORECASE,
    )
    SHORT_LINK_RE = re.compile(
        rf"https?://m\.toutiao\.com/is/{URL_TAIL_RE[:-1]}+",
        re.IGNORECASE,
    )
    PAGE_ID_RE = re.compile(r"/(article|video|w)/(\d+)", re.IGNORECASE)
    CANONICAL_PAGE_RE = re.compile(
        r"https?://m\.toutiao\.com/(article|video|w)/(\d+)/?",
        re.IGNORECASE,
    )
    SCRIPT_RE = re.compile(
        r"<script[^>]*>\s*(%7B.*?%7D)\s*</script>",
        re.IGNORECASE | re.DOTALL,
    )
    IMG_SRC_RE = re.compile(
        r"<img[^>]+src=[\"']([^\"']+)[\"'][^>]*>",
        re.IGNORECASE,
    )

    def __init__(self, article_image_refreshes: int = MAX_ARTICLE_IMAGE_REFRESHES):
        super().__init__("toutiao")
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)
        try:
            self.article_image_refreshes = max(1, int(article_image_refreshes))
        except (TypeError, ValueError):
            self.article_image_refreshes = MAX_ARTICLE_IMAGE_REFRESHES

    def can_parse(self, url: str) -> bool:
        """判断是否可以解析今日头条链接。"""
        if not url:
            return False
        host = (urlparse(url).hostname or "").lower().strip(".")
        if host not in {"www.toutiao.com", "m.toutiao.com"}:
            return False
        return any(
            pattern.search(url)
            for pattern in (
                self.ARTICLE_LINK_RE,
                self.MOBILE_ARTICLE_LINK_RE,
                self.VIDEO_LINK_RE,
                self.MOBILE_VIDEO_LINK_RE,
                self.W_LINK_RE,
                self.SHORT_LINK_RE,
            )
        )

    def extract_links(self, text: str) -> List[str]:
        """从文本中提取今日头条链接。"""
        links: List[str] = []
        seen = set()
        for pattern in (
            self.ARTICLE_LINK_RE,
            self.MOBILE_ARTICLE_LINK_RE,
            self.VIDEO_LINK_RE,
            self.MOBILE_VIDEO_LINK_RE,
            self.W_LINK_RE,
            self.SHORT_LINK_RE,
        ):
            for match in pattern.finditer(text):
                link = match.group(0).rstrip(".,!?)]}>\"'，。！？；：）】》」")
                key = link.lower()
                if key in seen:
                    continue
                seen.add(key)
                links.append(link)
        return links

    @staticmethod
    def _build_page_headers(referer: str = "") -> Dict[str, str]:
        headers = {
            "User-Agent": MOBILE_UA,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        }
        if referer:
            headers["Referer"] = referer
        return headers

    @staticmethod
    def _build_vod_headers(referer: str) -> Dict[str, str]:
        return {
            "User-Agent": MOBILE_UA,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Referer": referer,
        }

    @classmethod
    def _extract_content_identity(cls, url: str) -> Tuple[str, str]:
        match = cls.PAGE_ID_RE.search(url or "")
        if not match:
            return "", ""
        return match.group(1).lower(), match.group(2)

    @staticmethod
    def _build_canonical_page_url(content_type: str, content_id: str) -> str:
        if content_type == "w":
            return f"https://m.toutiao.com/w/{content_id}/"
        return f"https://m.toutiao.com/{content_type}/{content_id}/"

    @classmethod
    def _extract_canonical_page_url_from_html(cls, html_text: str) -> str:
        match = cls.CANONICAL_PAGE_RE.search(html_text or "")
        if not match:
            return ""
        return cls._build_canonical_page_url(match.group(1).lower(), match.group(2))

    async def _resolve_content_context(
        self,
        session: aiohttp.ClientSession,
        url: str,
    ) -> Dict[str, str]:
        content_type, content_id = self._extract_content_identity(url)
        page_url = (
            self._build_canonical_page_url(content_type, content_id)
            if content_type and content_id
            else ""
        )

        if page_url:
            return {
                "source_url": url,
                "content_type": content_type,
                "content_id": content_id,
                "page_url": page_url,
            }

        if not self.SHORT_LINK_RE.search(url or ""):
            raise SkipParse("不是支持的今日头条链接")

        async with session.get(
            url,
            headers=self._build_page_headers(),
            allow_redirects=True,
        ) as response:
            final_url = str(response.url)
            html_text = await response.text()

        content_type, content_id = self._extract_content_identity(final_url)
        if not (content_type and content_id):
            page_url = self._extract_canonical_page_url_from_html(html_text)
            content_type, content_id = self._extract_content_identity(page_url)

        if not (content_type and content_id):
            raise RuntimeError("无法从今日头条短链中提取内容 ID")

        return {
            "source_url": url,
            "content_type": content_type,
            "content_id": content_id,
            "page_url": self._build_canonical_page_url(content_type, content_id),
        }

    async def _fetch_page_html(
        self,
        session: aiohttp.ClientSession,
        page_url: str,
    ) -> str:
        async with session.get(
            page_url,
            headers=self._build_page_headers(),
            allow_redirects=True,
        ) as response:
            if response.status != 200:
                body = await response.text()
                raise RuntimeError(
                    f"获取今日头条页面失败: HTTP {response.status}, {body[:200]}"
                )
            return await response.text()

    def _extract_state_json_text(self, html_text: str) -> str:
        """提取页面内百分号编码的状态 JSON 文本。"""
        for match in self.SCRIPT_RE.finditer(html_text or ""):
            encoded = match.group(1).strip()
            decoded = unquote(encoded)
            if "\"articleInfo\"" not in decoded:
                continue
            json.loads(decoded)
            return decoded

        fallback_match = re.search(
            r"(%7B%22sessionConfig%22.*?%7D)",
            html_text or "",
            re.IGNORECASE | re.DOTALL,
        )
        if fallback_match:
            decoded = unquote(fallback_match.group(1))
            if "\"articleInfo\"" in decoded:
                json.loads(decoded)
                return decoded

        raise RuntimeError("无法从今日头条页面中提取状态数据")

    @staticmethod
    def _format_timestamp(timestamp_value: Any) -> str:
        if timestamp_value in (None, ""):
            return ""
        try:
            timestamp = int(timestamp_value)
            if timestamp > 10 ** 12:
                timestamp //= 1000
            return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OSError):
            return ""

    @staticmethod
    def _first_non_empty(*values: Any) -> str:
        for value in values:
            value_str = str(value or "").strip()
            if value_str:
                return value_str
        return ""

    def _format_author(self, article_info: Dict[str, Any]) -> str:
        media_user = article_info.get("mediaUser") or {}
        thread_base = self._get_thread_base(article_info)
        thread_user_info = ((thread_base.get("user") or {}).get("info") or {})
        screen_name = self._first_non_empty(
            media_user.get("screenName"),
            article_info.get("detailSource"),
            thread_user_info.get("name"),
        )
        user_id = self._first_non_empty(
            media_user.get("id"),
            article_info.get("creatorUid"),
            thread_user_info.get("userId"),
        )
        if screen_name and user_id:
            return f"{screen_name}(uid:{user_id})"
        return screen_name or user_id

    @staticmethod
    def _get_thread_base(article_info: Dict[str, Any]) -> Dict[str, Any]:
        return ((article_info.get("thread") or {}).get("threadBase") or {})

    def _extract_article_content_html(
        self,
        article_info: Dict[str, Any],
    ) -> str:
        thread_base = self._get_thread_base(article_info)
        return self._first_non_empty(
            article_info.get("content"),
            thread_base.get("richContent"),
            thread_base.get("content"),
        )

    @staticmethod
    def _clean_html_text(content_html: str) -> str:
        if not content_html:
            return ""
        text = re.sub(r"<br\s*/?>", "\n", content_html, flags=re.IGNORECASE)
        text = re.sub(r"</(p|div|section|article|li|h[1-6])>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<img[^>]*>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text)
        lines = [
            re.sub(r"\s+", " ", line).strip()
            for line in text.splitlines()
        ]
        return "\n".join(line for line in lines if line)

    def _extract_image_urls_from_content(self, content_html: str) -> List[List[str]]:
        image_urls: List[List[str]] = []
        seen = set()
        for match in self.IMG_SRC_RE.finditer(content_html or ""):
            url = html.unescape(match.group(1).strip())
            if not url or url in seen:
                continue
            seen.add(url)
            image_urls.append([url])
        return image_urls

    @staticmethod
    def _extract_image_urls_from_image_list_items(
        image_list_items: Any,
    ) -> List[List[str]]:
        image_urls: List[List[str]] = []
        if not isinstance(image_list_items, list):
            return image_urls

        for item in image_list_items:
            if not isinstance(item, dict):
                continue
            url_list: List[str] = []
            for value in (item.get("url"), item.get("webUrl")):
                value_str = str(value or "").strip()
                if value_str and value_str not in url_list:
                    url_list.append(value_str)

            nested_url_items = item.get("urlList") or item.get("url_list") or []
            if isinstance(nested_url_items, list):
                for nested in nested_url_items:
                    if not isinstance(nested, dict):
                        continue
                    nested_url = str(nested.get("url") or "").strip()
                    if nested_url and nested_url not in url_list:
                        url_list.append(nested_url)

            if url_list:
                image_urls.append(url_list)

        return image_urls

    def _extract_thread_image_urls(
        self,
        thread_base: Dict[str, Any],
    ) -> List[List[str]]:
        merged: List[List[str]] = []
        for key in (
            "largeImageList",
            "originImageList",
            "ugcCutImageList",
            "thumbImageList",
        ):
            merged = self._merge_image_candidate_lists(
                merged,
                self._extract_image_urls_from_image_list_items(
                    thread_base.get(key)
                ),
            )
        return merged

    def _extract_article_image_urls(
        self,
        article_info: Dict[str, Any],
    ) -> List[List[str]]:
        return self._merge_image_candidate_lists(
            self._extract_image_urls_from_content(
                self._extract_article_content_html(article_info)
            ),
            self._extract_thread_image_urls(self._get_thread_base(article_info)),
        )

    def _merge_image_candidate_lists(
        self,
        current_lists: List[List[str]],
        new_lists: List[List[str]],
    ) -> List[List[str]]:
        merged = [list(url_list) for url_list in current_lists]
        for index, new_url_list in enumerate(new_lists):
            if index >= len(merged):
                merged.append([])
            for url in new_url_list:
                if not url or url in merged[index]:
                    continue
                merged[index].append(url)
        return merged

    async def _collect_article_image_candidates(
        self,
        session: aiohttp.ClientSession,
        page_url: str,
        state: Dict[str, Any],
    ) -> List[List[str]]:
        article_info = state.get("articleInfo") or {}
        merged_lists = self._extract_article_image_urls(article_info)
        if not merged_lists:
            return []

        for _ in range(1, self.article_image_refreshes):
            refreshed_html = await self._fetch_page_html(session, page_url)
            refreshed_state = json.loads(
                self._extract_state_json_text(refreshed_html)
            )
            refreshed_article_info = refreshed_state.get("articleInfo") or {}
            refreshed_lists = self._extract_article_image_urls(
                refreshed_article_info
            )
            merged_lists = self._merge_image_candidate_lists(
                merged_lists,
                refreshed_lists,
            )
        return merged_lists

    def _build_article_metadata_from_state(
        self,
        source_url: str,
        page_url: str,
        state: Dict[str, Any],
        image_urls: Optional[List[List[str]]] = None,
    ) -> Dict[str, Any]:
        article_info = state.get("articleInfo") or {}
        thread_base = self._get_thread_base(article_info)
        seo_tdk = state.get("seoTDK") or {}
        title = self._first_non_empty(
            article_info.get("title"),
            thread_base.get("title"),
            seo_tdk.get("title"),
        )
        if not title:
            raise RuntimeError("今日头条文章缺少标题")

        content_html = self._extract_article_content_html(article_info)
        return {
            "url": source_url,
            "source_url": source_url,
            "title": title,
            "author": self._format_author(article_info),
            "desc": self._clean_html_text(content_html),
            "timestamp": self._format_timestamp(
                self._first_non_empty(
                    article_info.get("publishTime"),
                    thread_base.get("createTime"),
                    seo_tdk.get("publishTime"),
                )
            ),
            "video_urls": [],
            "image_urls": (
                image_urls
                if image_urls is not None
                else self._extract_article_image_urls(article_info)
            ),
            "image_headers": build_request_headers(
                is_video=False,
                referer=page_url,
                user_agent=MOBILE_UA,
            ),
            "video_headers": build_request_headers(
                is_video=True,
                referer=page_url,
                user_agent=MOBILE_UA,
            ),
        }

    @staticmethod
    def _decode_base64_text(token: str) -> str:
        normalized = str(token or "").strip()
        if not normalized:
            raise RuntimeError("今日头条视频缺少播放令牌")
        normalized += "=" * (-len(normalized) % 4)
        try:
            return base64.b64decode(normalized).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise RuntimeError("今日头条视频播放令牌解码失败") from exc

    def _extract_vod_query_from_token(self, token: str) -> str:
        token_json = json.loads(self._decode_base64_text(token))
        query = self._first_non_empty(token_json.get("GetPlayInfoToken"))
        if not query:
            raise RuntimeError("今日头条视频播放令牌中缺少 GetPlayInfoToken")
        return query.replace("\\u0026", "&").replace("\u0026", "&")

    async def _fetch_vod_payload(
        self,
        session: aiohttp.ClientSession,
        query: str,
        referer: str,
    ) -> Dict[str, Any]:
        url = f"{VOD_API_BASE}?{query}"
        async with session.get(
            url,
            headers=self._build_vod_headers(referer),
        ) as response:
            if response.status != 200:
                body = await response.text()
                raise RuntimeError(
                    f"获取今日头条视频信息失败: HTTP {response.status}, {body[:200]}"
                )
            return await response.json(content_type=None)

    @staticmethod
    def _collect_video_urls(vod_payload: Dict[str, Any]) -> List[List[str]]:
        play_info_list = (
            (((vod_payload.get("Result") or {}).get("Data") or {}).get("PlayInfoList"))
            or []
        )
        ranked_urls: List[Tuple[int, str]] = []
        seen = set()
        for item in play_info_list:
            if not isinstance(item, dict):
                continue
            url = str(item.get("MainPlayUrl") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            try:
                bitrate = int(item.get("Bitrate") or 0)
            except (TypeError, ValueError):
                bitrate = 0
            ranked_urls.append((bitrate, url))

        ranked_urls.sort(key=lambda item: item[0], reverse=True)
        if not ranked_urls:
            return []
        return [[url for _, url in ranked_urls]]

    def _build_video_metadata_from_state(
        self,
        source_url: str,
        page_url: str,
        state: Dict[str, Any],
        vod_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        article_info = state.get("articleInfo") or {}
        title = self._first_non_empty(article_info.get("title"))
        if not title:
            raise RuntimeError("今日头条视频缺少标题")

        return {
            "url": source_url,
            "source_url": source_url,
            "title": title,
            "author": self._format_author(article_info),
            "desc": self._clean_html_text(str(article_info.get("content") or "")),
            "timestamp": self._format_timestamp(article_info.get("publishTime")),
            "video_urls": self._collect_video_urls(vod_payload),
            "image_urls": [],
            "image_headers": build_request_headers(
                is_video=False,
                referer=page_url,
                user_agent=MOBILE_UA,
            ),
            "video_headers": build_request_headers(
                is_video=True,
                referer=page_url,
                user_agent=MOBILE_UA,
            ),
        }

    async def parse(
        self,
        session: aiohttp.ClientSession,
        url: str,
    ) -> Optional[Dict[str, Any]]:
        logger.debug(f"[{self.name}] parse: 开始解析 {url}")
        async with self.semaphore:
            context = await self._resolve_content_context(session, url)
            page_url = context["page_url"]
            html_text = await self._fetch_page_html(session, page_url)
            state_json_text = self._extract_state_json_text(html_text)
            state = json.loads(state_json_text)
            content_type = context["content_type"]
            article_info = state.get("articleInfo") or {}

            if content_type == "w":
                token = self._first_non_empty(article_info.get("playAuthTokenV2"))
                content_type = "video" if token else "article"

            if content_type == "article":
                metadata = self._build_article_metadata_from_state(
                    source_url=context["source_url"],
                    page_url=page_url,
                    state=state,
                    image_urls=[],
                )
                metadata["image_urls"] = (
                    await self._collect_article_image_candidates(
                        session,
                        page_url,
                        state,
                    )
                )
            elif content_type == "video":
                token = self._first_non_empty(article_info.get("playAuthTokenV2"))
                if not token:
                    raise RuntimeError("今日头条视频缺少 playAuthTokenV2")
                query = self._extract_vod_query_from_token(token)
                vod_payload = await self._fetch_vod_payload(session, query, page_url)
                metadata = self._build_video_metadata_from_state(
                    source_url=context["source_url"],
                    page_url=page_url,
                    state=state,
                    vod_payload=vod_payload,
                )
            else:
                raise SkipParse("不支持的今日头条内容类型")

            logger.debug(
                f"[{self.name}] parse: 解析完成 {url}, "
                f"video_count={len(metadata.get('video_urls', []))}, "
                f"image_count={len(metadata.get('image_urls', []))}"
            )
            return metadata
