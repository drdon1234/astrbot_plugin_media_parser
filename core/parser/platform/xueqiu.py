"""雪球帖子解析器，覆盖普通帖、长文与转发帖的文本与媒体提取。"""

import asyncio
import html
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp

from ...constants import Config
from ...logger import logger
from ...types import MediaMetadata
from ..utils import build_request_headers
from .base import BaseVideoParser


DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# 雪球 Web 页面受 WAF 保护，访客令牌与详情接口走不受保护的两个入口
XUEQIU_TOKEN_URL = "https://xueqiu.com/service/csrf"
XUEQIU_STATUS_API = "https://api.xueqiu.com/statuses/show.json"
XUEQIU_STATUS_API_PATH = "/statuses/show.json"

XUEQIU_HOSTS = frozenset({"xueqiu.com", "www.xueqiu.com", "m.xueqiu.com"})
XUEQIU_COOKIE_DOMAINS = frozenset({"xueqiu.com", "api.xueqiu.com"})
XUEQIU_TOKEN_COOKIE = "xq_a_token"
# 令牌缺失或过期时接口统一返回该业务码，重新申请访客令牌后可重试
XUEQIU_TOKEN_ERROR_CODES = frozenset({"400016"})
# 表情图片属于站点静态资源，不是帖子配图
XUEQIU_EMOJI_HOSTS = frozenset({"assets.imedao.com"})

XUEQIU_URL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.:/@-])(?:https?://)?(?:www\.|m\.)?xueqiu\.com"
    r"/[^\s<>\"'()]+",
    re.IGNORECASE,
)
STATUS_PATH_RE = re.compile(r"^/(\d{3,20})/(\d{3,20})/?$")
IMG_TAG_RE = re.compile(r"<img[^>]*>", re.IGNORECASE)
IMG_ATTR_RE = re.compile(
    r"(src|title|alt)\s*=\s*[\"']([^\"']*)[\"']",
    re.IGNORECASE,
)
HTTP_URL_RE = re.compile(r"https?://[^\s<>\"']+")
VIDEO_URL_KEY_HINTS = ("video", "play", "media", "stream", "url", "m3u8")


def _parse_status_identity(url: str) -> Tuple[str, str]:
    """严格解析雪球帖子 URL，返回 (user_id, status_id)。"""
    if not isinstance(url, str) or not url.strip():
        return "", ""
    normalized = url.strip()
    if "://" not in normalized:
        normalized = "https://" + normalized
    try:
        parsed = urlparse(normalized)
        if parsed.scheme.lower() not in {"http", "https"}:
            return "", ""
        if parsed.username or parsed.password:
            return "", ""
        if parsed.port not in {None, 80, 443}:
            return "", ""
    except (TypeError, ValueError):
        return "", ""
    host = (parsed.hostname or "").lower().strip(".")
    if host not in XUEQIU_HOSTS:
        return "", ""
    match = STATUS_PATH_RE.match(parsed.path or "")
    if not match:
        return "", ""
    return match.group(1), match.group(2)


class XueqiuParser(BaseVideoParser):
    """雪球帖子解析器。"""

    def __init__(self):
        super().__init__("xueqiu")
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)

    # ── URL 匹配 ──────────────────────────────────────────

    def can_parse(self, url: str) -> bool:
        """判断是否可以解析此URL

        Args:
            url: 雪球帖子链接

        Returns:
            是否可以解析
        """
        return _parse_status_identity(url) != ("", "")

    def extract_links(self, text: str) -> List[str]:
        """从文本中提取雪球帖子链接

        Args:
            text: 输入文本

        Returns:
            提取到的链接列表，按帖子 ID 去重并保留原始链接形态
        """
        links: List[str] = []
        seen: set = set()
        for match in XUEQIU_URL_PATTERN.finditer(text or ""):
            link = match.group(0).rstrip(".,!?)]}>\"'，。！？；：）】》」")
            _, status_id = _parse_status_identity(link)
            if not status_id or status_id in seen:
                continue
            seen.add(status_id)
            links.append(link)
        if links:
            logger.debug(
                f"[{self.name}] extract_links: 提取到 {len(links)} 个链接: {links[:3]}"
                f"{'...' if len(links) > 3 else ''}"
            )
        return links

    # ── 请求环境 ──────────────────────────────────────────

    @staticmethod
    def _build_status_page_url(user_id: str, status_id: str) -> str:
        """构建帖子规范页面地址，用于 Referer 与图片下载来源。"""
        if user_id:
            return f"https://xueqiu.com/{user_id}/{status_id}"
        return "https://xueqiu.com/"

    @staticmethod
    def _build_api_headers(referer: str) -> Dict[str, str]:
        """构建雪球接口请求头。"""
        return {
            "User-Agent": DESKTOP_UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Referer": referer or "https://xueqiu.com/",
        }

    @staticmethod
    def _read_guest_token(session: aiohttp.ClientSession) -> str:
        """从会话 Cookie 中读取雪球访客令牌。"""
        for cookie in session.cookie_jar:
            if cookie.key != XUEQIU_TOKEN_COOKIE:
                continue
            domain = str(cookie["domain"] or "").lstrip(".").lower()
            if domain and domain not in XUEQIU_COOKIE_DOMAINS:
                continue
            value = str(cookie.value or "").strip()
            if value:
                return value
        return ""

    async def _ensure_guest_token(
        self,
        session: aiohttp.ClientSession,
        force: bool = False,
    ) -> None:
        """申请雪球访客令牌并写入会话 Cookie。

        Args:
            session: aiohttp会话
            force: 是否忽略已有令牌强制重新申请

        Raises:
            RuntimeError: 令牌接口未下发访客令牌
        """
        if not force and self._read_guest_token(session):
            return
        async with session.get(
            XUEQIU_TOKEN_URL,
            params={"api": XUEQIU_STATUS_API_PATH},
            headers=self._build_api_headers("https://xueqiu.com/"),
        ) as response:
            await response.read()
        if not self._read_guest_token(session):
            raise RuntimeError("未能获取雪球访客令牌")

    @staticmethod
    def _decode_api_payload(body_text: str) -> Dict[str, Any]:
        """解析雪球接口响应，拒绝 WAF 拦截页与非对象 JSON。"""
        preview = (body_text or "")[:200]
        if "aliyun_waf" in (body_text or ""):
            raise RuntimeError("雪球接口被 WAF 拦截，无法获取帖子数据")
        try:
            payload = json.loads(body_text)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"雪球接口返回的不是 JSON：{preview!r}"
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError("雪球接口返回的 JSON 不是对象")
        return payload

    async def _fetch_status(
        self,
        session: aiohttp.ClientSession,
        status_id: str,
        referer: str,
    ) -> Dict[str, Any]:
        """请求雪球帖子详情，令牌失效时重新申请一次后重试。

        Args:
            session: aiohttp会话
            status_id: 帖子 ID
            referer: 帖子页面地址

        Returns:
            帖子详情字典

        Raises:
            RuntimeError: 接口返回业务错误、身份不一致或响应不可用
        """
        headers = self._build_api_headers(referer)
        for attempt in range(2):
            await self._ensure_guest_token(session, force=attempt > 0)
            async with session.get(
                XUEQIU_STATUS_API,
                params={"id": status_id},
                headers=headers,
            ) as response:
                status_code = response.status
                body_text = await response.text()

            payload = self._decode_api_payload(body_text)
            error_code = str(payload.get("error_code") or "").strip()
            if error_code in XUEQIU_TOKEN_ERROR_CODES and attempt == 0:
                logger.debug(
                    f"[{self.name}] 雪球访客令牌失效，重新申请后重试: sid={status_id}"
                )
                continue
            if error_code:
                error_desc = str(payload.get("error_description") or "").strip()
                raise RuntimeError(
                    f"雪球详情接口返回错误: {error_desc or '未知错误'}({error_code})"
                )
            if status_code != 200:
                raise RuntimeError(f"雪球详情接口返回 HTTP {status_code}")

            returned_id = payload.get("id")
            if returned_id in (None, "") or str(returned_id) != str(status_id):
                raise RuntimeError("雪球详情接口返回了其他帖子的数据")
            return payload

        raise RuntimeError("雪球访客令牌失效，帖子详情获取失败")

    # ── 文本处理 ──────────────────────────────────────────

    @staticmethod
    def _first_non_empty(*values: Any) -> str:
        for value in values:
            value_str = str(value or "").strip()
            if value_str:
                return value_str
        return ""

    @staticmethod
    def _format_timestamp(timestamp_value: Any) -> str:
        """将雪球毫秒时间戳格式化为日期。"""
        if timestamp_value in (None, ""):
            return ""
        try:
            timestamp = int(timestamp_value)
            if timestamp > 10**12:
                timestamp //= 1000
            return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OSError):
            return ""

    @staticmethod
    def _normalize_http_url(url: str) -> str:
        """归一化协议相对地址。"""
        normalized = str(url or "").strip()
        if not normalized:
            return ""
        if normalized.startswith("//"):
            return "https:" + normalized
        if normalized.startswith(("http://", "https://")):
            return normalized
        return ""

    @classmethod
    def _is_emoji_image(cls, url: str) -> bool:
        """判断图片地址是否为雪球表情等站点静态资源。"""
        normalized = cls._normalize_http_url(url)
        if not normalized:
            return False
        try:
            host = (urlparse(normalized).hostname or "").lower().strip(".")
        except (TypeError, ValueError):
            return False
        return host in XUEQIU_EMOJI_HOSTS

    @staticmethod
    def _parse_img_attrs(img_tag: str) -> Dict[str, str]:
        """提取 img 标签的 src / title / alt 属性。"""
        return {
            key.lower(): value
            for key, value in IMG_ATTR_RE.findall(img_tag or "")
        }

    @classmethod
    def _replace_img_tags(cls, content_html: str) -> str:
        """表情图片还原为文字标记，配图标签直接移除。"""

        def replace(match: "re.Match[str]") -> str:
            attrs = cls._parse_img_attrs(match.group(0))
            if not cls._is_emoji_image(attrs.get("src", "")):
                return ""
            return cls._first_non_empty(attrs.get("title"), attrs.get("alt"))

        return IMG_TAG_RE.sub(replace, content_html or "")

    @classmethod
    def _clean_html_text(cls, content_html: str) -> str:
        """将雪球富文本正文转换为可读纯文本。"""
        if not content_html:
            return ""
        text = cls._replace_img_tags(content_html)
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(
            r"</(p|div|section|article|li|blockquote|h[1-6])>",
            "\n",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text)
        lines = [
            re.sub(r"[ \t\u00a0]+", " ", line).strip()
            for line in text.splitlines()
        ]
        return "\n".join(line for line in lines if line)

    @classmethod
    def _extract_body_html(cls, status: Dict[str, Any]) -> str:
        """取帖子完整正文 HTML，截断字段仅作兜底。"""
        return cls._first_non_empty(status.get("text"), status.get("description"))

    @classmethod
    def _extract_author(cls, status: Dict[str, Any]) -> str:
        """取帖子作者昵称。"""
        user = status.get("user")
        if not isinstance(user, dict):
            user = {}
        return cls._first_non_empty(user.get("screen_name"), user.get("name"))

    @classmethod
    def _extract_title(cls, status: Dict[str, Any]) -> str:
        """取帖子标题；普通帖没有标题时返回空串。"""
        return cls._first_non_empty(
            status.get("title"),
            status.get("rawTitle"),
            status.get("topic_title"),
        )

    @classmethod
    def _build_desc(cls, status: Dict[str, Any]) -> str:
        """合并本帖正文与被转发原帖，避免丢失任一侧内容。"""
        desc = cls._clean_html_text(cls._extract_body_html(status))
        retweeted = status.get("retweeted_status")
        if not isinstance(retweeted, dict):
            return desc

        quote_parts = ["转发原帖："]
        author = cls._extract_author(retweeted)
        if author:
            quote_parts.append(author)
        title = cls._extract_title(retweeted)
        if title:
            quote_parts.append(title)
        body = cls._clean_html_text(cls._extract_body_html(retweeted))
        if body:
            quote_parts.append(body)
        if len(quote_parts) == 1:
            return desc

        quote_desc = "\n".join(quote_parts)
        return f"{desc}\n\n{quote_desc}" if desc else quote_desc

    # ── 媒体提取 ──────────────────────────────────────────

    @classmethod
    def _split_image_variants(cls, raw_url: str) -> List[str]:
        """把雪球图片地址拆成 原图 / 原始形态 两个候选。"""
        normalized = cls._normalize_http_url(raw_url)
        if not normalized or cls._is_emoji_image(normalized):
            return []
        original = normalized.split("!", 1)[0]
        candidates = [original]
        if normalized != original:
            candidates.append(normalized)
        return candidates

    @classmethod
    def _collect_status_image_lists(
        cls,
        status: Dict[str, Any],
    ) -> List[List[str]]:
        """按正文顺序收集单个帖子的图片候选组。"""
        image_lists: List[List[str]] = []
        seen: set = set()

        def push(raw_url: str) -> None:
            candidates = cls._split_image_variants(raw_url)
            if not candidates:
                return
            key = candidates[0]
            if key in seen:
                return
            seen.add(key)
            image_lists.append(candidates)

        # 长文正文按阅读顺序内嵌配图，优先保留该顺序
        for match in IMG_TAG_RE.finditer(cls._extract_body_html(status)):
            push(cls._parse_img_attrs(match.group(0)).get("src", ""))

        for raw_url in str(status.get("pic") or "").split(","):
            push(raw_url)

        image_info_list = status.get("image_info_list")
        if isinstance(image_info_list, list):
            for item in image_info_list:
                if not isinstance(item, dict):
                    continue
                filename = str(item.get("filename") or "").strip()
                if filename:
                    push(f"https://xqimg.imedao.com/{filename}")

        return image_lists

    @classmethod
    def _merge_image_lists(
        cls,
        *list_groups: List[List[str]],
    ) -> List[List[str]]:
        """按顺序合并多个帖子的图片候选组并去重。"""
        merged: List[List[str]] = []
        seen: set = set()
        for group in list_groups:
            for candidates in group:
                if not candidates or candidates[0] in seen:
                    continue
                seen.add(candidates[0])
                merged.append(list(candidates))
        return merged

    @classmethod
    def _collect_video_candidates(cls, container: Any) -> List[str]:
        """从视频容器中递归收集可播放地址候选。"""
        candidates: List[str] = []
        seen: set = set()

        def push(raw_url: str) -> None:
            normalized = cls._normalize_http_url(raw_url)
            if not normalized or normalized in seen:
                return
            lowered = normalized.lower()
            if not (
                lowered.endswith(".mp4")
                or ".mp4?" in lowered
                or ".m3u8" in lowered
                or "/play" in lowered
            ):
                return
            seen.add(normalized)
            if ".m3u8" in lowered:
                candidates.append(f"m3u8:{normalized}")
            else:
                candidates.append(normalized)

        def walk(node: Any, key_hint: str = "") -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    walk(value, str(key))
            elif isinstance(node, list):
                for value in node:
                    walk(value, key_hint)
            elif isinstance(node, str):
                key_lower = key_hint.lower()
                if not any(token in key_lower for token in VIDEO_URL_KEY_HINTS):
                    return
                if node.startswith(("http://", "https://", "//")):
                    push(node)
                    return
                for matched in HTTP_URL_RE.findall(node):
                    push(matched)

        walk(container)
        return candidates

    @classmethod
    def _collect_status_video_lists(
        cls,
        status: Dict[str, Any],
    ) -> List[List[str]]:
        """收集单个帖子的视频候选组。

        雪球一条帖子最多挂一个视频，`video_info` 与 `vod_info` 描述的是同一个
        视频，因此合并为一个候选组，不拆成多个独立视频项。
        """
        candidates: List[str] = []
        for key in ("video_info", "vod_info"):
            container = status.get(key)
            if not container:
                continue
            for url in cls._collect_video_candidates(container):
                if url not in candidates:
                    candidates.append(url)
        return [candidates] if candidates else []

    # ── 解析 ──────────────────────────────────────────────

    async def parse(
        self,
        session: aiohttp.ClientSession,
        url: str,
    ) -> Optional[MediaMetadata]:
        """解析单个雪球帖子链接

        Args:
            session: aiohttp会话
            url: 雪球帖子链接

        Returns:
            解析结果元数据
        """
        async with self.semaphore:
            return await self._parse(session, url)

    async def _parse(
        self,
        session: aiohttp.ClientSession,
        url: str,
    ) -> Optional[MediaMetadata]:
        user_id, status_id = _parse_status_identity(url)
        if not status_id:
            logger.debug(f"[{self.name}] parse: URL 不是雪球帖子链接: {url}")
            return None

        logger.debug(f"[{self.name}] parse: 开始解析 sid={status_id}")
        status = await self._fetch_status(
            session,
            status_id,
            self._build_status_page_url(user_id, status_id),
        )

        author_id = self._first_non_empty(status.get("user_id"), user_id)
        page_url = self._build_status_page_url(author_id, status_id)

        retweeted = status.get("retweeted_status")
        image_urls = self._merge_image_lists(
            self._collect_status_image_lists(status),
            (
                self._collect_status_image_lists(retweeted)
                if isinstance(retweeted, dict)
                else []
            ),
        )
        video_urls = self._collect_status_video_lists(status)
        if not video_urls and isinstance(retweeted, dict):
            video_urls = self._collect_status_video_lists(retweeted)

        title = self._extract_title(status)
        desc = self._build_desc(status)
        if not title and not desc and not image_urls and not video_urls:
            raise RuntimeError("雪球帖子未解析到可用内容")

        metadata: MediaMetadata = {
            "url": url,
            "source_url": url,
            "title": title,
            "author": self._extract_author(status),
            "desc": desc,
            "timestamp": self._format_timestamp(status.get("created_at")),
            "platform": "xueqiu",
            "parser_name": "xueqiu",
            "video_urls": video_urls,
            "image_urls": image_urls,
            "image_headers": build_request_headers(
                is_video=False,
                referer=page_url,
                user_agent=DESKTOP_UA,
            ),
            "video_headers": build_request_headers(
                is_video=True,
                referer=page_url,
                user_agent=DESKTOP_UA,
            ),
        }
        # HLS 播放地址必须先落盘再发送
        if any(
            url_item.startswith("m3u8:")
            for candidates in video_urls
            for url_item in candidates
        ):
            metadata["video_force_download"] = True

        logger.debug(
            f"[{self.name}] parse: 解析完成 sid={status_id}, "
            f"title={metadata.get('title', '')[:50]}, "
            f"video_count={len(video_urls)}, image_count={len(image_urls)}"
        )
        return metadata
