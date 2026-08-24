"""Steam 游戏详情页解析器。"""

import asyncio
import html as html_lib
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp

from ...constants import Config
from ...logger import logger
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
STEAM_URL_PATTERN = re.compile(
    r"https?://store\.steampowered\.com/app/[^\s<>\"'()]+",
    re.IGNORECASE,
)


class SteamParser(BaseVideoParser):
    """解析 Steam 商店游戏页并提取游戏详情与媒体。"""

    def __init__(
        self,
        use_xiaoheihe: bool = False,
        use_parse_proxy: bool = False,
        use_image_proxy: bool = True,
        use_video_proxy: bool = True,
        xiaoheihe_use_video_proxy: bool = True,
        xiaoheihe_use_parse_proxy: Optional[bool] = None,
        proxy_url: Optional[str] = None,
    ):
        """初始化 Steam 解析器。

        Args:
            use_xiaoheihe: 是否使用小黑盒完整游戏详情路径。
            use_parse_proxy: Steam 或小黑盒详情接口是否使用代理。
            use_image_proxy: Steam 游戏图片下载是否使用代理。
            use_video_proxy: Steam 游戏视频下载是否使用代理。
            xiaoheihe_use_video_proxy: 小黑盒路径的视频是否使用代理。
            xiaoheihe_use_parse_proxy: 小黑盒路径的详情接口是否使用代理；省略时沿用小黑盒视频代理开关。
            proxy_url: 代理地址。
        """
        super().__init__("steam")
        self.use_xiaoheihe = bool(use_xiaoheihe)
        self.use_parse_proxy = bool(use_parse_proxy)
        self.use_image_proxy = bool(use_image_proxy)
        self.use_video_proxy = bool(use_video_proxy)
        self.proxy_url = proxy_url
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
    def _parse_appid(url: str) -> Optional[str]:
        """从 Steam 游戏页 URL 中提取 appid。"""
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
        if host not in STEAM_HOSTS:
            return None
        match = re.match(r"^/app/(?P<appid>\d{1,12})(?:/|$)", parsed.path or "")
        if not match:
            return None
        appid = match.group("appid")
        return appid if int(appid) > 0 else None

    def can_parse(self, url: str) -> bool:
        """判断是否可以解析该 Steam 游戏页 URL。"""
        return self._parse_appid(url) is not None

    def extract_links(self, text: str) -> List[str]:
        """从文本中提取 Steam 游戏页链接并去重。"""
        links: List[str] = []
        seen_appids = set()
        for match in STEAM_URL_PATTERN.finditer(text or ""):
            link = match.group(0).rstrip(
                ".,!?)]}>\"'，。！？；：）】》」"
            )
            appid = self._parse_appid(link)
            if appid and appid not in seen_appids:
                seen_appids.add(appid)
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
        result["source_url"] = url
        result["steam_appid"] = appid
        result["use_image_proxy"] = self.use_image_proxy
        result["use_video_proxy"] = self.use_video_proxy
        result["proxy_url"] = (
            self.proxy_url if (self.use_image_proxy or self.use_video_proxy) else None
        )
        return result

    async def parse(
        self, session: aiohttp.ClientSession, url: str
    ) -> Optional[MediaMetadata]:
        """解析 Steam 游戏页并返回统一媒体元数据。"""
        async with self.semaphore:
            appid = self._parse_appid(url)
            if not appid:
                raise RuntimeError(f"无法从 Steam 游戏页提取 appid: {url}")
            logger.debug(
                f"[{self.name}] parse: appid={appid}, use_xiaoheihe={self.use_xiaoheihe}"
            )
            if self.use_xiaoheihe:
                return await self._parse_via_xiaoheihe(session, url, appid)

            game = await self._fetch_app_data(session, appid)
            name = str(game.get("name") or "").strip()
            if not name:
                raise RuntimeError("Steam API 未返回游戏名称")
            description, release_date = self._build_description(game)
            video_urls, video_cover_urls, image_urls = self._extract_media(game)
            canonical_url = f"https://store.steampowered.com/app/{appid}/"
            result: MediaMetadata = {
                "url": url,
                "source_url": url,
                "steam_appid": appid,
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
            logger.debug(
                f"[{self.name}] parse: 解析完成 appid={appid}, "
                f"video_count={len(video_urls)}, image_count={len(image_urls)}"
            )
            return result
