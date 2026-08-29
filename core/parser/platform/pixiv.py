"""Pixiv 插画/漫画解析器。"""

# 负责：
# 1. 匹配 pixiv.net 插画链接（artworks / i）
# 2. 调用 Pixiv Web Ajax API 获取作品元信息
# 3. 返回图片直链集合供下载管理器处理

import asyncio
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import aiohttp

from ...logger import logger

from ...constants import Config
from ...types import MediaMetadata
from ..utils import build_request_headers
from .base import BaseVideoParser


# ── URL 正则 ──────────────────────────────────────────────

PIXIV_URL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.:/@-])(?:https?://)?(?:www\.)?pixiv\.net"
    r"/[^\s<>\"'()]+",
    re.IGNORECASE,
)


def _parse_pixiv_identity(url: str) -> Optional[str]:
    """严格解析 Pixiv 作品 URL 并返回作品 ID。"""
    if not isinstance(url, str) or not url.strip():
        return None
    normalized = url.strip()
    if "://" not in normalized:
        normalized = "https://" + normalized
    try:
        parsed = urlparse(normalized)
        if parsed.scheme.lower() not in {"http", "https"}:
            return None
        if parsed.username or parsed.password:
            return None
        if parsed.port not in {None, 80, 443}:
            return None
    except (TypeError, ValueError):
        return None
    host = (parsed.hostname or "").lower().strip(".")
    if host not in {"pixiv.net", "www.pixiv.net"}:
        return None
    match = re.fullmatch(
        r"/(?:en/)?(?:artworks|i)/(\d{5,12})/?",
        parsed.path or "",
        re.IGNORECASE,
    )
    return match.group(1) if match else None

PIXIV_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.7103.48 Safari/537.36"
)

PIXIV_ACCEPT_LANGUAGE = "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7,ja-JP;q=0.6,ja;q=0.5"


def _build_headers(illust_id: Optional[str] = None, cookie: str = "") -> Dict[str, str]:
    """构建 Pixiv API 请求头。"""
    headers = {
        "accept-language": PIXIV_ACCEPT_LANGUAGE,
        "user-agent": PIXIV_USER_AGENT,
    }
    if cookie:
        headers["cookie"] = cookie.strip()
    if illust_id:
        headers["referer"] = f"https://www.pixiv.net/artworks/{illust_id}"
    else:
        headers["referer"] = "https://www.pixiv.net/"
    return headers


async def _fetch_json(
    session: aiohttp.ClientSession,
    url: str,
    illust_id: str,
    stage: str,
    cookie: str = "",
    proxy: Optional[str] = None,
) -> Dict[str, Any]:
    """请求 Pixiv Ajax API 并验证返回 JSON（而非 HTML）。

    Raises:
        RuntimeError: 被 Cloudflare 拦截 / 返回 HTML / API error
        aiohttp.ClientResponseError: HTTP 状态码错误
    """
    headers = _build_headers(illust_id, cookie)
    proxy_arg = proxy or None

    async with session.get(url, headers=headers, proxy=proxy_arg) as resp:
        content_type = resp.headers.get("content-type", "")
        body_text = await resp.text()
        body_preview = body_text[:300]

        logger.debug(
            f"[Pixiv] {stage} 响应 → pid={illust_id}, "
            f"status={resp.status}, content-type={content_type}"
        )

        is_html = (
            "text/html" in content_type.lower()
            or body_text.lstrip().lower().startswith("<!doctype html")
        )
        if is_html:
            if "Just a moment" in body_text or "just a moment" in body_text:
                raise RuntimeError("Pixiv Ajax 被 Cloudflare 拦截，Cookie 可能已失效")
            raise RuntimeError(f"Pixiv Ajax 返回 HTML，无法解析：{body_preview!r}")

        resp.raise_for_status()

        try:
            data = await resp.json()
        except Exception as e:
            raise RuntimeError(
                f"Pixiv Ajax JSON 解析失败：{e} body={body_preview!r}"
            ) from e

        if not isinstance(data, dict):
            raise RuntimeError(f"Pixiv Ajax 返回的 JSON 不是对象：{stage}")

        if data.get("error"):
            err_msg = data.get("message") or "unknown error"
            raise RuntimeError(f"Pixiv API 返回错误：{err_msg}")

        return data


def _extract_pixiv_links(text: str) -> List[str]:
    """提取 Pixiv 作品链接，按作品 ID 去重并保留原始链接形态。"""
    links: List[str] = []
    seen: set[str] = set()
    for match in PIXIV_URL_PATTERN.finditer(text or ""):
        link = match.group(0).rstrip(".,!?)]}>\"'，。！？；：）】》」")
        illust_id = _parse_pixiv_identity(link)
        if illust_id and illust_id not in seen:
            seen.add(illust_id)
            links.append(link)
    return links


class PixivParser(BaseVideoParser):
    """Pixiv 插画/漫画解析器。"""

    def __init__(
        self,
        cookie: str = "",
        proxy: Optional[str] = None,
    ):
        super().__init__("pixiv")
        self.cookie = cookie
        self.proxy = proxy
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)

    # ── URL 匹配 ──────────────────────────────────────────

    def can_parse(self, url: str) -> bool:
        return _parse_pixiv_identity(url) is not None

    def extract_links(self, text: str) -> List[str]:
        return _extract_pixiv_links(text)

    # ── 解析 ──────────────────────────────────────────────

    async def parse(
        self,
        session: aiohttp.ClientSession,
        url: str,
    ) -> Optional[MediaMetadata]:
        async with self.semaphore:
            return await self._parse(session, url)

    async def _parse(
        self,
        session: aiohttp.ClientSession,
        url: str,
    ) -> Optional[MediaMetadata]:
        t_start = time.time()

        illust_id = _parse_pixiv_identity(url)
        if not illust_id:
            logger.debug(f"[Pixiv] URL 不匹配 pixiv 模式: {url}")
            return None

        logger.info(f"[Pixiv] 开始解析作品 → pid={illust_id}")

        if not self.cookie:
            logger.warning(f"[Pixiv] 未配置 Cookie，可能无法获取原图: pid={illust_id}")

        # 获取作品元信息
        info_url = f"https://www.pixiv.net/ajax/illust/{illust_id}"
        info_data = await _fetch_json(
            session,
            info_url,
            illust_id,
            "元信息",
            self.cookie,
            proxy=self.proxy,
        )
        body = info_data.get("body") or {}
        if not isinstance(body, dict):
            raise RuntimeError("Pixiv 元信息 body 不是对象")
        returned_id = body.get("illustId") or body.get("illust_id")
        if returned_id not in (None, "") and str(returned_id) != illust_id:
            raise RuntimeError("Pixiv API 返回了其他作品的数据")

        # 获取多页图片 URL
        pages_url = f"https://www.pixiv.net/ajax/illust/{illust_id}/pages?lang=zh"
        pages_data = await _fetch_json(
            session,
            pages_url,
            illust_id,
            "pages",
            self.cookie,
            proxy=self.proxy,
        )
        raw_pages = pages_data.get("body") or []
        if not isinstance(raw_pages, list):
            raise RuntimeError("Pixiv pages body 不是列表")
        if not raw_pages:
            raise RuntimeError("Pixiv 作品未包含可下载图片")

        # ── 解析图片列表 ──
        image_urls: List[List[str]] = []
        page_count = len(raw_pages)

        for item in raw_pages:
            if not isinstance(item, dict):
                logger.warning(f"[Pixiv] pid={illust_id} 页面数据不是对象，跳过")
                continue
            urls = item.get("urls") or {}
            if not isinstance(urls, dict):
                logger.warning(f"[Pixiv] pid={illust_id} 页面 URL 数据不是对象，跳过")
                continue
            original = urls.get("original")
            regular = urls.get("regular") or urls.get("small") or original

            if not original and not regular:
                logger.warning(f"[Pixiv] pid={illust_id} 页面缺少图片 URL，跳过")
                continue

            candidates = []
            # 原图优先
            if original:
                candidates.append(original)
            if regular and regular != original:
                candidates.append(regular)
            if candidates:
                image_urls.append(candidates)

        if not image_urls:
            raise RuntimeError("Pixiv 作品未包含有效图片 URL")

        # ── 标签处理 ──
        tags_container = body.get("tags") or {}
        tags_raw = (
            tags_container.get("tags") or [] if isinstance(tags_container, dict) else []
        )
        tags: List[str] = []
        for item in tags_raw if isinstance(tags_raw, list) else []:
            if not isinstance(item, dict):
                continue
            tag_name = item.get("tag")
            if not tag_name:
                continue
            if tag_name == "R-18":
                tag_name = "R18"
            translation = item.get("translation") or {}
            if not isinstance(translation, dict):
                translation = {}
            if translation.get("en"):
                tag_name = translation["en"]
            tags.append(str(tag_name).replace(" ", "_"))

        # ── 构建元数据 ──
        title = str(body.get("illustTitle") or body.get("title") or "Untitled")
        user_name = str(body.get("userName") or "Unknown")
        user_id = str(body.get("userId") or "")

        def safe_int(value: Any) -> int:
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        x_restrict = safe_int(body.get("xRestrict"))
        ai_type = safe_int(body.get("aiType"))
        sanity_level = safe_int(body.get("sl"))

        # 构建描述（包含标签 + 限制信息）
        desc_parts = []
        if tags:
            desc_parts.append(" | ".join(f"#{t}" for t in tags[:20]))
        restriction_label = ""
        if x_restrict == 1:
            restriction_label = "R-18"
        elif x_restrict == 2:
            restriction_label = "R-18G"
        if ai_type == 2:
            restriction_label = (
                restriction_label + " / " if restriction_label else ""
            ) + "AI生成"
        if restriction_label:
            desc_parts.append(f"[{restriction_label}]")
        desc = "  ".join(desc_parts) if desc_parts else ""

        metadata: MediaMetadata = {
            "url": url,
            "source_url": url,
            "title": title,
            "author": user_name,
            "platform": "pixiv",
            "parser_name": "pixiv",
            "desc": desc,
            "image_urls": image_urls,
            "video_urls": [],
            "image_headers": build_request_headers(
                is_video=False,
                referer=f"https://www.pixiv.net/artworks/{illust_id}",
                user_agent=PIXIV_USER_AGENT,
            ),
            "video_headers": {},
            "use_image_proxy": bool(self.proxy),
            "proxy_url": self.proxy,
            "has_valid_media": bool(image_urls),
        }

        # 附加 Pixiv 特有字段
        metadata["pixiv_illust_id"] = illust_id
        metadata["pixiv_user_id"] = user_id
        metadata["pixiv_x_restrict"] = x_restrict
        metadata["pixiv_ai_type"] = ai_type
        metadata["pixiv_sanity_level"] = sanity_level
        metadata["pixiv_page_count"] = page_count

        elapsed = time.time() - t_start
        r18_label = ""
        if x_restrict == 1:
            r18_label = " [R-18]"
        elif x_restrict == 2:
            r18_label = " [R-18G]"
        ai_label = " [AI]" if ai_type == 2 else ""
        logger.info(
            f"[Pixiv] 解析完成 → pid={illust_id} "
            f"「{title}」by {user_name} "
            f"共 {page_count} 页 / {len(image_urls)} 张{r18_label}{ai_label} "
            f"({elapsed:.2f}s)"
        )

        return metadata
