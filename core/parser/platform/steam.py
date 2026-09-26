"""Steam 游戏详情页、社区指南与创意工坊物品解析器。"""

import asyncio
import html as html_lib
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import ParseResult, parse_qs, urlparse

import aiohttp

from ...logger import logger

from ...constants import Config
from ...types import MediaMetadata
from ..utils import build_request_headers
from .base import BaseVideoParser
from .xiaoheihe import XiaoheiheParser


STEAM_API_URL = "https://store.steampowered.com/api/appdetails/"
STEAM_HOSTS = {"store.steampowered.com"}
STEAM_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
STEAM_COMMUNITY_HOSTS = {"steamcommunity.com"}
STEAM_COMMUNITY_FILE_URL = "https://steamcommunity.com/sharedfiles/filedetails/"
STEAM_UGC_IMAGE_HOST = "images.steamusercontent.com"
STEAM_UGC_FULL_IMAGE_QUERY = (
    "imw=5000&imh=5000&ima=fit&impolicy=Letterbox&imcolor=%23000000&letterbox=false"
)
STEAM_ACCOUNT_ID_BASE = 76561197960265728
STEAM_URL_PATTERN = re.compile(
    r"https?://store\.steampowered\.com/app/[^\s<>\"'()]+"
    r"|https?://steamcommunity\.com/(?:sharedfiles|workshop)/filedetails"
    r"(?:(?![<>\"'()])[\x21-\x7e])*",
    re.IGNORECASE,
)
YOUTUBE_WATCH_URL = "https://www.youtube.com/watch?v={}"


class SteamParser(BaseVideoParser):
    """解析 Steam 商店游戏页、社区指南与创意工坊物品。"""

    def __init__(
        self,
        use_xiaoheihe: bool = False,
        use_parse_proxy: bool = False,
        use_image_proxy: bool = True,
        use_video_proxy: bool = True,
        xiaoheihe_use_video_proxy: bool = True,
        xiaoheihe_use_parse_proxy: Optional[bool] = None,
        proxy_url: Optional[str] = None,
        hot_comment_count: int = 0,
    ) -> None:
        """初始化 Steam 解析器。

        Args:
            use_xiaoheihe: 是否使用小黑盒完整游戏详情路径。
            use_parse_proxy: Steam 或小黑盒详情接口是否使用代理。
            use_image_proxy: Steam 游戏图片下载是否使用代理。
            use_video_proxy: Steam 游戏视频下载是否使用代理。
            xiaoheihe_use_video_proxy: 小黑盒路径的视频是否使用代理。
            xiaoheihe_use_parse_proxy: 小黑盒路径的详情接口是否使用代理；省略时沿用小黑盒视频代理开关。
            proxy_url: 代理地址。
            hot_comment_count: 附加游戏评测数量，0 表示关闭。
        """
        super().__init__("steam")
        self.use_xiaoheihe = bool(use_xiaoheihe)
        self.use_parse_proxy = bool(use_parse_proxy)
        self.use_image_proxy = bool(use_image_proxy)
        self.use_video_proxy = bool(use_video_proxy)
        self.proxy_url = proxy_url
        try:
            self.hot_comment_count = max(0, int(hot_comment_count))
        except (TypeError, ValueError, OverflowError):
            self.hot_comment_count = 0
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)
        self._default_headers = {
            "User-Agent": STEAM_USER_AGENT,
            "Accept": "application/json",
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        self._xiaoheihe_parser: Optional[XiaoheiheParser] = None
        if self.use_xiaoheihe:
            self._xiaoheihe_parser = XiaoheiheParser(
                use_video_proxy=xiaoheihe_use_video_proxy,
                proxy_url=proxy_url,
                use_parse_proxy=(
                    self.use_parse_proxy
                    if xiaoheihe_use_parse_proxy is None
                    else xiaoheihe_use_parse_proxy
                ),
            )

    @staticmethod
    def _parse_steam_url(url: str, hosts: Iterable[str]) -> Optional[ParseResult]:
        """校验协议、端口与主机后返回 URL 解析结果。"""
        if not isinstance(url, str) or not url.strip():
            return None
        try:
            parsed = urlparse(url.strip())
        except (TypeError, ValueError):
            return None
        try:
            port = parsed.port
        except ValueError:
            return None
        if parsed.scheme.lower() not in {"http", "https"}:
            return None
        if parsed.username or parsed.password or port not in {None, 80, 443}:
            return None
        host = (parsed.hostname or "").lower().strip(".")
        return parsed if host in hosts else None

    @classmethod
    def _parse_appid(cls, url: str) -> Optional[str]:
        """从 Steam 游戏页 URL 中提取 appid。"""
        parsed = cls._parse_steam_url(url, STEAM_HOSTS)
        if parsed is None:
            return None
        match = re.match(r"^/app/(?P<appid>\d{1,12})(?:/|$)", parsed.path or "")
        if not match:
            return None
        appid = match.group("appid")
        return appid if int(appid) > 0 else None

    @classmethod
    def _parse_file_id(cls, url: str) -> Optional[str]:
        """从 Steam 社区指南或创意工坊物品 URL 中提取物品 ID。"""
        parsed = cls._parse_steam_url(url, STEAM_COMMUNITY_HOSTS)
        if parsed is None:
            return None
        if not re.match(
            r"^/(?:sharedfiles|workshop)/filedetails/?$", parsed.path or "",
            re.IGNORECASE,
        ):
            return None
        values = parse_qs(parsed.query or "").get("id") or []
        if len(values) != 1 or not re.fullmatch(r"\d{1,20}", values[0]):
            return None
        return values[0] if int(values[0]) > 0 else None

    def can_parse(self, url: str) -> bool:
        """判断是否可以解析该 Steam 游戏页、指南或创意工坊 URL。"""
        return (
            self._parse_appid(url) is not None
            or self._parse_file_id(url) is not None
        )

    def extract_links(self, text: str) -> List[str]:
        """从文本中提取 Steam 游戏页、指南与创意工坊链接并去重。"""
        links: List[str] = []
        seen_ids = set()
        for match in STEAM_URL_PATTERN.finditer(text or ""):
            link = match.group(0).rstrip(
                ".,!?)]}>\"'，。！？；：）】》」"
            )
            appid = self._parse_appid(link)
            file_id = None if appid else self._parse_file_id(link)
            identity = ("app", appid) if appid else ("file", file_id)
            if identity[1] and identity not in seen_ids:
                seen_ids.add(identity)
                links.append(link)
        return links

    @staticmethod
    def _unique_keep_order(values: Iterable[str]) -> List[str]:
        """去重并保持首次出现顺序。"""
        seen = set()
        result: List[str] = []
        for value in values:
            if not isinstance(value, str) or not value or value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

    @staticmethod
    def _normalize_url(value: Any) -> Optional[str]:
        """校验并规范化 Steam 返回的媒体 URL。"""
        if not isinstance(value, str):
            return None
        normalized = html_lib.unescape(value).replace("\\/", "/").strip()
        if normalized.startswith("//"):
            normalized = "https:" + normalized
        try:
            parsed = urlparse(normalized)
        except (TypeError, ValueError):
            return None
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None
        return normalized

    @staticmethod
    def _strip_html(text: str) -> str:
        """将 Steam 简介 HTML 清理为可发送的纯文本。"""
        if not text:
            return ""
        value = re.sub(r"(?is)<script[^>]*>.*?</script>", "", text)
        value = re.sub(r"(?is)<style[^>]*>.*?</style>", "", value)
        value = re.sub(r"(?is)<video[^>]*>.*?</video>", "", value)
        value = re.sub(r"(?i)</p\s*>", "\n\n", value)
        value = re.sub(r"(?i)<p[^>]*>", "", value)
        value = re.sub(r"(?i)</div\s*>", "\n", value)
        value = re.sub(r"(?i)<div[^>]*>", "", value)
        value = re.sub(r"(?i)</h[1-6]\s*>", "\n", value)
        value = re.sub(r"(?i)<h[1-6][^>]*>", "\n", value)
        value = re.sub(r"(?i)<br\s*/?>", "\n", value)
        value = re.sub(r"(?i)</li\s*>", "\n", value)
        value = re.sub(r"(?i)<li[^>]*>", "\n・", value)
        value = re.sub(r"<[^>]+>", "", value)
        value = html_lib.unescape(value)
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"[ \t]+\n", "\n", value)
        value = re.sub(r"\n[ \t]+", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()

    @staticmethod
    def _format_release_date(value: Any) -> str:
        """格式化 Steam 发行日期。"""
        text = html_lib.unescape(str(value or "")).strip()
        text = re.sub(r"\s+", "", text)
        match = re.match(r"^(\d{4})年(\d{1,2})月(\d{1,2})日?$", text)
        if match:
            return f"{match.group(1)}.{int(match.group(2))}.{int(match.group(3))}"
        match = re.match(r"^(\d{4})[年\-/\.](\d{1,2})[月\-/\.](\d{1,2})日?$", text)
        if match:
            return f"{match.group(1)}.{int(match.group(2))}.{int(match.group(3))}"
        return str(value or "").strip()

    async def _fetch_app_data(
        self, session: aiohttp.ClientSession, appid: str
    ) -> Dict[str, Any]:
        """调用 Steam `appdetails` 接口获取游戏详情。"""
        async with session.get(
            STEAM_API_URL,
            params={"appids": appid, "l": "schinese", "cc": "cn"},
            headers=self._default_headers,
            proxy=self.proxy_url if self.use_parse_proxy else None,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as response:
            response.raise_for_status()
            payload = await response.json(content_type=None)
        if not isinstance(payload, dict):
            raise RuntimeError("Steam API 返回的 JSON 不是对象")
        entry = payload.get(appid)
        if not isinstance(entry, dict) or entry.get("success") is not True:
            raise RuntimeError("Steam API 未返回有效游戏详情")
        game = entry.get("data")
        if not isinstance(game, dict):
            raise RuntimeError("Steam API 游戏详情不是对象")
        returned_appid = game.get("steam_appid")
        if returned_appid not in (None, "") and str(returned_appid) != appid:
            raise RuntimeError("Steam API 返回了其他游戏的数据")
        return game

    def _extract_description_media(
        self, html_text: str
    ) -> Tuple[List[List[str]], List[str]]:
        """从 Steam 简介 HTML 中提取视频候选和图片。"""
        video_groups: List[List[str]] = []
        image_urls: List[str] = []

        for video_block in re.findall(
            r"<video\b[^>]*>.*?</video>", html_text or "", re.IGNORECASE | re.DOTALL
        ):
            candidates = []
            for source in re.findall(
                r"<source\b[^>]*\bsrc\s*=\s*(['\"])(.*?)\1",
                video_block,
                re.IGNORECASE | re.DOTALL,
            ):
                candidate = self._normalize_url(source[1])
                if candidate:
                    candidates.append(candidate)
            if candidates:
                video_groups.append(self._unique_keep_order(candidates))

            poster_match = re.search(
                r"\bposter\s*=\s*(['\"])(.*?)\1",
                video_block,
                re.IGNORECASE | re.DOTALL,
            )
            if poster_match:
                poster = self._normalize_url(poster_match.group(2))
                if poster:
                    image_urls.append(poster)

        for tag in re.findall(r"<img\b[^>]*>", html_text or "", re.IGNORECASE):
            match = re.search(
                r"\bdata-big-src\s*=\s*(['\"]?)([^\s'\">]+)\1",
                tag,
                re.IGNORECASE,
            ) or re.search(
                r"\bsrc\s*=\s*(['\"])(.*?)\1", tag, re.IGNORECASE
            )
            if match:
                image = self._normalize_url(match.group(2))
                if image:
                    image_urls.append(image)

        return video_groups, self._unique_keep_order(image_urls)

    def _extract_media(
        self, game: Dict[str, Any]
    ) -> Tuple[List[List[str]], List[List[str]], List[List[str]]]:
        """从 Steam 详情字段提取视频、视频封面和图片。"""
        video_items: List[Tuple[List[str], List[str]]] = []
        image_urls: List[str] = []

        for key in ("header_image", "capsule_image"):
            image = self._normalize_url(game.get(key))
            if image:
                image_urls.append(image)

        screenshots = game.get("screenshots")
        if isinstance(screenshots, list):
            for item in screenshots:
                if not isinstance(item, dict):
                    continue
                image = self._normalize_url(
                    item.get("path_full") or item.get("path_thumbnail")
                )
                if image:
                    image_urls.append(image)

        movies = game.get("movies")
        if isinstance(movies, list):
            for movie in movies:
                if not isinstance(movie, dict):
                    continue
                thumbnail = self._normalize_url(movie.get("thumbnail"))
                if thumbnail:
                    image_urls.append(thumbnail)
                candidates: List[str] = []
                hls_url = self._normalize_url(movie.get("hls_h264"))
                if hls_url:
                    candidates.append(f"m3u8:{hls_url}")
                for key in ("mp4", "webm"):
                    direct_url = self._normalize_url(movie.get(key))
                    if direct_url:
                        candidates.append(direct_url)
                if candidates:
                    video_items.append(
                        (
                            self._unique_keep_order(candidates),
                            [thumbnail] if thumbnail else [],
                        )
                    )

        description = game.get("detailed_description") or game.get("about_the_game")
        inline_videos, inline_images = self._extract_description_media(
            description if isinstance(description, str) else ""
        )
        video_items.extend((candidates, []) for candidates in inline_videos)
        image_urls.extend(inline_images)

        unique_video_urls: List[List[str]] = []
        unique_video_covers: List[List[str]] = []
        seen_video_groups = set()
        for candidates, covers in video_items:
            normalized_candidates = self._unique_keep_order(candidates)
            if not normalized_candidates:
                continue
            group_key = tuple(normalized_candidates)
            if group_key in seen_video_groups:
                continue
            seen_video_groups.add(group_key)
            unique_video_urls.append(normalized_candidates)
            unique_video_covers.append(self._unique_keep_order(covers))
        unique_images = [[image] for image in self._unique_keep_order(image_urls)]
        return unique_video_urls, unique_video_covers, unique_images

    @staticmethod
    def _extract_genres(game: Dict[str, Any]) -> str:
        """提取 Steam 类型标签。"""
        genres = game.get("genres")
        if not isinstance(genres, list):
            return ""
        values = []
        for item in genres:
            if not isinstance(item, dict):
                continue
            value = str(item.get("description") or item.get("name") or "").strip()
            if value:
                values.append(value)
        return " / ".join(dict.fromkeys(values))

    @staticmethod
    def _string_values(value: Any) -> List[str]:
        """提取列表中的非空字符串并去重。"""
        if not isinstance(value, list):
            return []
        values = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        return list(dict.fromkeys(values))

    @staticmethod
    def _format_price(game: Dict[str, Any]) -> List[str]:
        """格式化 Steam 价格信息。"""
        if game.get("is_free"):
            return ["价格：免费"]
        overview = game.get("price_overview")
        if not isinstance(overview, dict):
            return []
        initial = str(overview.get("initial_formatted") or "").strip()
        final = str(overview.get("final_formatted") or "").strip()
        if initial and final and initial != final:
            return [f"价格：{initial}", f"当前价格：{final}"]
        value = final or initial
        return [f"价格：{value}"] if value else []

    def _build_description(self, game: Dict[str, Any]) -> Tuple[str, str]:
        """构建游戏摘要文本与发行日期。"""
        raw_intro = (
            game.get("about_the_game")
            or game.get("detailed_description")
            or game.get("short_description")
        )
        intro = self._strip_html(raw_intro) if isinstance(raw_intro, str) else ""
        release_info = game.get("release_date")
        release_date = ""
        if isinstance(release_info, dict):
            release_date = self._format_release_date(release_info.get("date"))
            if release_info.get("coming_soon") and release_date:
                release_date = f"即将发行（{release_date}）"
        else:
            release_date = self._format_release_date(release_info)

        lines = ["", "", "=============", intro, "=============", ""]
        genres = self._extract_genres(game)
        if genres:
            lines.append(f"类型：{genres}")
        if release_date:
            lines.append(f"发行日期：{release_date}")
        developers = self._string_values(game.get("developers"))
        if developers:
            lines.append(f"开发商：{', '.join(developers)}")
        publishers = self._string_values(game.get("publishers"))
        if publishers:
            lines.append(f"发行商：{', '.join(publishers)}")
        lines.extend(self._format_price(game))
        languages = str(game.get("supported_languages") or "").strip()
        if languages:
            languages = re.sub(r"<[^>]+>", "", languages).strip()
            lines.append(f"支持语言：{languages}")
        return "\n".join(lines).rstrip(), release_date

    async def _parse_via_xiaoheihe(
        self, session: aiohttp.ClientSession, url: str, appid: str
    ) -> MediaMetadata:
        """使用小黑盒完整游戏详情路径解析 Steam appid。"""
        if self._xiaoheihe_parser is None:
            raise RuntimeError("小黑盒路径解析器未初始化")
        xiaoheihe_url = f"https://www.xiaoheihe.cn/app/topic/game/pc/{appid}"
        result = await self._xiaoheihe_parser.parse(session, xiaoheihe_url)
        if not isinstance(result, dict):
            raise RuntimeError("小黑盒路径未返回有效游戏详情")
        result = dict(result)
        result["url"] = url
        result["use_image_proxy"] = self.use_image_proxy
        result["use_video_proxy"] = self.use_video_proxy
        result["proxy_url"] = (
            self.proxy_url if (self.use_image_proxy or self.use_video_proxy) else None
        )
        return result

    @staticmethod
    def _format_timestamp(value: Any) -> str:
        """将秒级时间戳格式化为本地时间文本。"""
        try:
            timestamp = int(value or 0)
            return (
                datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
                if timestamp > 0 else ""
            )
        except (TypeError, ValueError, OverflowError, OSError):
            return ""

    def _normalize_review(self, item: Any) -> Optional[Dict[str, Any]]:
        """将玩家评测映射为评论，并保留推荐态度。"""
        if not isinstance(item, dict):
            return None
        review_id = str(item.get("recommendationid") or "").strip()
        message = item.get("review")
        if not review_id or not isinstance(message, str):
            return None
        message = re.sub(
            r"\[/?(?:b|i|u|strike|spoiler|h[1-6]|url|quote|list|olist|\*|code)(?:=[^\]]*)?\]",
            "", message, flags=re.IGNORECASE,
        )
        message = self._strip_html(message)
        if not message:
            return None
        author = item.get("author")
        author = author if isinstance(author, dict) else {}
        comment: Dict[str, Any] = {
            "id": review_id,
            "username": str(author.get("personaname") or "Steam 玩家"),
            "uid": str(author.get("steamid") or ""),
            "message": message,
            "time": self._format_timestamp(item.get("timestamp_created")),
        }
        if isinstance(item.get("voted_up"), bool):
            attitude = "推荐" if item["voted_up"] else "不推荐"
            comment["message"] = f"【游戏评测：{attitude}】\n{message}"
        try:
            if item.get("votes_up") is not None:
                comment["likes"] = max(0, int(item["votes_up"]))
        except (TypeError, ValueError, OverflowError):
            pass
        return comment

    async def _fetch_hot_comments(
        self, session: aiohttp.ClientSession, appid: str
    ) -> List[Dict[str, Any]]:
        """分页读取中文有用评测，不足时补充其他语言的普通评测。"""
        comments: List[Dict[str, Any]] = []
        if not self.hot_comment_count:
            return comments
        seen = set()
        for language, sort_filter in (("schinese", "all"), ("all", "recent")):
            cursor = "*"
            visited_cursors = set()
            source_seen = set()
            for _ in range(5):
                if len(comments) >= self.hot_comment_count:
                    return comments
                try:
                    async with session.get(
                        f"https://store.steampowered.com/appreviews/{appid}",
                        params={
                            "json": "1", "filter": sort_filter,
                            "language": language, "day_range": "365",
                            "num_per_page": str(min(100, self.hot_comment_count)),
                            "purchase_type": "all", "cursor": cursor,
                        },
                        headers=self._default_headers,
                        proxy=self.proxy_url if self.use_parse_proxy else None,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as response:
                        response.raise_for_status()
                        payload = await response.json(content_type=None)
                    if not isinstance(payload, dict) or payload.get("success") != 1:
                        raise ValueError("Steam 评测接口未返回成功结果")
                    rows = payload.get("reviews")
                    if not isinstance(rows, list):
                        raise ValueError("Steam 评测列表格式无效")
                    if not rows:
                        break
                    previous_count = len(source_seen)
                    for row in rows:
                        comment = self._normalize_review(row)
                        if not comment:
                            continue
                        source_seen.add(comment["id"])
                        if comment["id"] in seen:
                            continue
                        seen.add(comment["id"])
                        comments.append(comment)
                        if len(comments) >= self.hot_comment_count:
                            return comments
                    next_cursor = payload.get("cursor")
                    if (
                        len(source_seen) == previous_count or not isinstance(next_cursor, str)
                        or not next_cursor or next_cursor == cursor
                        or next_cursor in visited_cursors
                    ):
                        break
                    visited_cursors.add(cursor)
                    cursor = next_cursor
                except asyncio.CancelledError:
                    raise
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError) as exc:
                    logger.warning(f"[{self.name}] 游戏评测获取失败，已保留游戏详情：{exc}")
                    return comments
        return comments

    # ── 社区指南与创意工坊 ──────────────────────────────

    @staticmethod
    def _div_blocks(html_text: str, class_name: str, limit: int = 0) -> List[str]:
        """按 div 嵌套层级读取指定 class 的内部 HTML。"""
        opener = re.compile(
            r"<div\b[^>]*\bclass\s*=\s*\"(?:[^\"]*\s)?"
            + re.escape(class_name)
            + r"(?:\s[^\"]*)?\"[^>]*>",
            re.IGNORECASE,
        )
        div_tag = re.compile(r"<(/?)div\b[^>]*>", re.IGNORECASE)
        text = html_text or ""
        blocks: List[str] = []
        position = 0
        while True:
            match = opener.search(text, position)
            if not match:
                break
            depth = 1
            end = len(text)
            for tag in div_tag.finditer(text, match.end()):
                depth += -1 if tag.group(1) else 1
                if depth == 0:
                    end = tag.start()
                    break
            blocks.append(text[match.end():end])
            if limit and len(blocks) >= limit:
                break
            position = max(end, match.end())
        return blocks

    def _first_block_text(self, html_text: str, class_name: str) -> str:
        """读取指定 class 首个 div 的纯文本。"""
        blocks = self._div_blocks(html_text, class_name, limit=1)
        return self._community_text(blocks[0]) if blocks else ""

    def _community_text(self, fragment: str) -> str:
        """将社区 BBCode 渲染结果清理为纯文本，保留表格行与视频链接。"""
        value = re.sub(
            r"(?is)<div\b[^>]*\bclass\s*=\s*\"[^\"]*\bsharedFilePreviewYouTubeVideo\b"
            r"[^\"]*\"[^>]*\bid\s*=\s*\"([\w-]{11})\"[^>]*>\s*</div>",
            lambda match: f"\n视频：{YOUTUBE_WATCH_URL.format(match.group(1))}\n",
            fragment or "",
        )
        value = re.sub(r"(?i)(</div>)\s+(?=<div\b)", r"\1", value)
        value = re.sub(
            r"(?is)<div\b[^>]*\bclass\s*=\s*\"bb_table_t[dh]\"[^>]*>(.*?)</div>",
            r"\1 | ",
            value,
        )
        value = self._strip_html(value)
        value = re.sub(r"\n\s*\n(?=・)", "\n", value)
        return re.sub(r"[ \t]*\|[ \t]*(?=\n|$)", "", value).strip()

    def _normalize_community_image(self, value: Any) -> Optional[str]:
        """规范化社区图片地址，排除表情与站点界面素材。"""
        image = self._normalize_url(value)
        if not image:
            return None
        parsed = urlparse(image)
        host = (parsed.hostname or "").lower()
        path = parsed.path or ""
        if "/economy/emoticon/" in path:
            return None
        if host.endswith("steamstatic.com") and path.startswith("/public/"):
            return None
        if host == STEAM_UGC_IMAGE_HOST and path.startswith("/ugc/"):
            return f"https://{STEAM_UGC_IMAGE_HOST}{path}?{STEAM_UGC_FULL_IMAGE_QUERY}"
        return image

    def _extract_community_images(self, fragment: str) -> List[str]:
        """提取社区正文中的图片。"""
        images: List[str] = []
        for tag in re.findall(r"<img\b[^>]*>", fragment or "", re.IGNORECASE):
            match = re.search(
                r"\bsrc\s*=\s*(['\"])(.*?)\1", tag, re.IGNORECASE | re.DOTALL
            )
            image = self._normalize_community_image(match.group(2)) if match else None
            if image:
                images.append(image)
        return images

    def _extract_preview_media(self, html_text: str) -> Tuple[List[str], List[str]]:
        """提取社区物品封面、预览截图与 YouTube 预览视频链接。"""
        images: List[str] = []
        match = re.search(
            r"<meta\s+property=\"og:image\"\s+content=\"([^\"]+)\"",
            html_text or "",
            re.IGNORECASE,
        )
        if match:
            images.append(self._normalize_community_image(match.group(1)))
        block = re.search(
            r"rgFullScreenshotURLs\s*=\s*\[(.*?)\];", html_text or "", re.DOTALL
        )
        if block:
            for image in re.findall(r"'url'\s*:\s*'([^']+)'", block.group(1)):
                images.append(self._normalize_community_image(image))
        block = re.search(
            r"rgMovieFlashvars\s*=\s*\{(.*?)\};", html_text or "", re.DOTALL
        )
        videos = [
            YOUTUBE_WATCH_URL.format(video_id)
            for video_id in re.findall(
                r"YOUTUBE_VIDEO_ID\s*:\s*\"([\w-]{11})\"", block.group(1)
            )
        ] if block else []
        return [image for image in images if image], self._unique_keep_order(videos)

    def _build_community_details(self, html_text: str) -> Tuple[List[str], str]:
        """提取社区物品的评分、标签、依赖、发布时间与访问统计。"""
        lines: List[str] = []
        stars = re.search(r"sharedfiles/(\d)-star_large\.png", html_text)
        num_ratings = self._first_block_text(html_text, "numRatings")
        if stars:
            rating = f"评分：{stars.group(1)} 星"
            lines.append(f"{rating}（{num_ratings}）" if num_ratings else rating)

        for block in re.findall(
            r"<div\b[^>]*\bclass=\"workshopTags\"[^>]*>(.*?)</div>",
            html_text,
            re.IGNORECASE | re.DOTALL,
        ):
            title_match = re.search(
                r"<span\b[^>]*workshopTagsTitle[^>]*>(.*?)</span>", block, re.DOTALL
            )
            values = [
                self._strip_html(value)
                for value in re.findall(r"<a\b[^>]*>(.*?)</a>", block, re.DOTALL)
            ]
            values = [value for value in values if value]
            if not title_match or not values:
                continue
            title = self._strip_html(title_match.group(1)).rstrip(":：").strip()
            if title:
                lines.append(f"{title}：{', '.join(dict.fromkeys(values))}")

        required_items = [
            self._strip_html(item)
            for item in re.findall(
                r"class=\"requiredItem\">(.*?)</div>", html_text, re.DOTALL
            )
        ]
        required_items = [item for item in required_items if item]
        if required_items:
            lines.append(f"必需物品：{', '.join(dict.fromkeys(required_items))}")

        stats_tables = "".join(
            re.findall(
                r"<table\b[^>]*class=\"stats_table\"[^>]*>.*?</table>",
                html_text,
                re.DOTALL,
            )
        )
        stat_labels = re.findall(
            r"class=\"detailsStatLeft\">(.*?)</div>", html_text, re.DOTALL
        )
        stat_values = re.findall(
            r"class=\"detailsStatRight\">(.*?)</div>", html_text, re.DOTALL
        )
        pairs = list(zip(stat_labels, stat_values)) + re.findall(
            r"<tr>\s*<td>(.*?)</td>\s*<td>(.*?)</td>\s*</tr>", stats_tables, re.DOTALL
        )
        published = ""
        for label, value in pairs:
            label = self._strip_html(label)
            value = self._strip_html(value)
            # 统计数值在不同页面类型中可能位于标签前或标签后
            if re.fullmatch(r"[\d,.]+", label) and not re.fullmatch(r"[\d,.]+", value):
                label, value = value, label
            if not label or not value:
                continue
            lines.append(f"{label}：{value}")
            if label == "发表于":
                published = value
        return lines, published

    def _extract_page_comments(self, html_text: str) -> List[Dict[str, Any]]:
        """读取社区页面首屏的公开评论。"""
        comments: List[Dict[str, Any]] = []
        if not self.hot_comment_count:
            return comments
        starts = list(
            re.finditer(
                r"<div\b[^>]*?\bclass=\"commentthread_comment[\s\"][^>]*?"
                r"\bid=\"comment_(\d+)\"",
                html_text or "",
            )
        )
        for index, start in enumerate(starts):
            end = starts[index + 1].start() if index + 1 < len(starts) else len(html_text)
            segment = html_text[start.start():end]
            message = self._first_block_text(segment, "commentthread_comment_text")
            if not message:
                continue
            author_match = re.search(r"<bdi>(.*?)</bdi>", segment, re.DOTALL)
            profile_match = re.search(
                r"commentthread_author_link\"[^>]*\bdata-miniprofile=\"(\d+)\"", segment
            )
            time_match = re.search(r"\bdata-timestamp=\"(\d+)\"", segment)
            comments.append(
                {
                    "id": start.group(1),
                    "username": (
                        self._strip_html(author_match.group(1)) if author_match else ""
                    ) or "Steam 用户",
                    "uid": (
                        str(STEAM_ACCOUNT_ID_BASE + int(profile_match.group(1)))
                        if profile_match else ""
                    ),
                    "message": message,
                    "time": self._format_timestamp(
                        time_match.group(1) if time_match else 0
                    ),
                }
            )
            if len(comments) >= self.hot_comment_count:
                break
        return comments

    async def _fetch_community_page(
        self, session: aiohttp.ClientSession, file_id: str
    ) -> str:
        """请求 Steam 社区物品详情页 HTML。"""
        async with session.get(
            STEAM_COMMUNITY_FILE_URL,
            params={"id": file_id, "l": "schinese"},
            headers={
                **self._default_headers,
                "Accept": "text/html,application/xhtml+xml",
            },
            proxy=self.proxy_url if self.use_parse_proxy else None,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as response:
            response.raise_for_status()
            return await response.text()

    async def _parse_community_file(
        self, session: aiohttp.ClientSession, url: str, file_id: str
    ) -> MediaMetadata:
        """解析 Steam 社区指南、创意工坊物品或合集页。"""
        html_text = await self._fetch_community_page(session, file_id)
        title = self._first_block_text(html_text, "workshopItemTitle")
        if not title:
            error_match = re.search(
                r"<div\b[^>]*id=\"message\"[^>]*>.*?<h3>(.*?)</h3>",
                html_text,
                re.DOTALL,
            )
            error_text = self._strip_html(error_match.group(1)) if error_match else ""
            raise RuntimeError(
                f"Steam 社区返回错误：{error_text}" if error_text
                else "Steam 社区页面未包含可解析的指南或创意工坊内容，可能需要登录或内容不可见"
            )
        page_id = re.search(r"\bpublishedfileid\s*=\s*'(\d+)'", html_text)
        if page_id and page_id.group(1) != file_id:
            raise RuntimeError("Steam 社区返回了其他物品的页面")

        if 'class="guideTopContent"' in html_text:
            kind = "指南"
            fragments = self._div_blocks(html_text, "guideTopDescription", limit=1)
            parts = [self._community_text(fragments[0])] if fragments else []
            for section in self._div_blocks(html_text, "subSection"):
                section_title = self._first_block_text(section, "subSectionTitle")
                section_desc = self._div_blocks(section, "subSectionDesc", limit=1)
                fragments.extend(section_desc)
                section_text = (
                    self._community_text(section_desc[0]) if section_desc else ""
                )
                if section_title:
                    section_text = f"【{section_title}】\n{section_text}".rstrip()
                parts.append(section_text)
            intro = "\n\n".join(part for part in parts if part)
        else:
            kind = (
                "创意工坊合集" if 'id="mainContentsCollection"' in html_text
                else "创意工坊物品"
            )
            fragments = self._div_blocks(
                html_text, "workshopItemDescription", limit=1
            )
            intro = self._community_text(fragments[0]) if fragments else ""

        preview_images, preview_videos = self._extract_preview_media(html_text)
        content_images: List[str] = []
        for fragment in fragments:
            content_images.extend(self._extract_community_images(fragment))
        image_urls = [
            [image]
            for image in self._unique_keep_order(preview_images + content_images)
        ]

        creators = self._div_blocks(html_text, "creatorsBlock", limit=1)
        authors = [
            self._strip_html(name)
            for name in re.findall(
                r"class=\"friendBlockContent\">(.*?)<br",
                creators[0] if creators else "",
                re.DOTALL,
            )
        ]
        app_name = self._first_block_text(html_text, "apphub_AppName")
        detail_lines, published = self._build_community_details(html_text)
        lines = ["", "", "=============", intro, "=============", ""] if intro else [""]
        if app_name:
            lines.append(f"游戏：{app_name}")
        lines.append(f"分类：{kind}")
        lines.extend(detail_lines)
        lines.extend(f"预览视频：{video}" for video in preview_videos)

        canonical_url = f"{STEAM_COMMUNITY_FILE_URL}?id={file_id}"
        result: MediaMetadata = {
            "url": url,
            "title": title,
            "author": ", ".join(dict.fromkeys(name for name in authors if name)),
            "desc": "\n".join(lines).rstrip(),
            "timestamp": published,
            "video_urls": [],
            "image_urls": image_urls,
            "image_headers": build_request_headers(
                is_video=False, referer=canonical_url
            ),
            "use_image_proxy": self.use_image_proxy,
            "proxy_url": self.proxy_url if self.use_image_proxy else None,
        }
        comments = self._extract_page_comments(html_text)
        if comments:
            result["hot_comments"] = comments
        logger.debug(
            f"[{self.name}] parse: 社区物品解析完成 id={file_id}, kind={kind}, "
            f"image_count={len(image_urls)}"
        )
        return result

    async def parse(
        self, session: aiohttp.ClientSession, url: str
    ) -> Optional[MediaMetadata]:
        """解析 Steam 游戏页、社区指南或创意工坊物品并返回统一媒体元数据。"""
        async with self.semaphore:
            file_id = self._parse_file_id(url)
            if file_id:
                logger.debug(f"[{self.name}] parse: file_id={file_id}")
                return await self._parse_community_file(session, url, file_id)
            appid = self._parse_appid(url)
            if not appid:
                raise RuntimeError(f"无法从 Steam 游戏页提取 appid: {url}")
            logger.debug(
                f"[{self.name}] parse: appid={appid}, use_xiaoheihe={self.use_xiaoheihe}"
            )
            if self.use_xiaoheihe:
                result = await self._parse_via_xiaoheihe(session, url, appid)
                if self.hot_comment_count:
                    comments = await self._fetch_hot_comments(session, appid)
                    if comments:
                        result["hot_comments"] = comments
                return result

            game = await self._fetch_app_data(session, appid)
            name = str(game.get("name") or "").strip()
            if not name:
                raise RuntimeError("Steam API 未返回游戏名称")
            description, release_date = self._build_description(game)
            video_urls, video_cover_urls, image_urls = self._extract_media(game)
            canonical_url = f"https://store.steampowered.com/app/{appid}/"
            result: MediaMetadata = {
                "url": url,
                "title": name,
                "author": ", ".join(self._string_values(game.get("developers"))),
                "desc": description,
                "timestamp": release_date,
                "video_urls": video_urls,
                "video_cover_urls": video_cover_urls,
                "image_urls": image_urls,
                "image_headers": build_request_headers(
                    is_video=False, referer=canonical_url
                ),
                "video_headers": build_request_headers(
                    is_video=True, referer=canonical_url
                ),
                "use_image_proxy": self.use_image_proxy,
                "use_video_proxy": self.use_video_proxy,
                "proxy_url": (
                    self.proxy_url
                    if (self.use_image_proxy or self.use_video_proxy)
                    else None
                ),
            }
            if video_urls:
                result["video_force_download"] = True
            if self.hot_comment_count:
                comments = await self._fetch_hot_comments(session, appid)
                if comments:
                    result["hot_comments"] = comments
            logger.debug(
                f"[{self.name}] parse: 解析完成 appid={appid}, "
                f"video_count={len(video_urls)}, image_count={len(image_urls)}"
            )
            return result
