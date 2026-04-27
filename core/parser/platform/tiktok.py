"""TikTok 解析器实现。"""
import asyncio
import json
import re
import shutil
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import aiohttp

from ...constants import Config
from ...logger import logger
from ..utils import SkipParse, build_request_headers, is_live_url
from .base import BaseVideoParser
from .short_video_shared import ShortVideoParserMixin


TIKTOK_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
TIKTOK_REFERER = "https://www.tiktok.com/"
TIKTOK_ORIGIN = "https://www.tiktok.com"


class TikTokParser(ShortVideoParserMixin, BaseVideoParser):

    """TikTok 解析器实现。"""

    def __init__(
        self,
        use_proxy: bool = False,
        proxy_url: str = None
    ):
        super().__init__("tiktok")
        self.use_proxy = bool(use_proxy)
        self.proxy_url = proxy_url
        self.tiktok_headers = {
            "User-Agent": TIKTOK_USER_AGENT,
            "Referer": TIKTOK_REFERER,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        }
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)

    def _get_proxy(self) -> Optional[str]:
        if self.use_proxy and self.proxy_url:
            return self.proxy_url
        return None

    @staticmethod
    async def _terminate_subprocess(process, label: str) -> None:
        """终止并回收外部子进程，避免取消路径遗留进程。"""
        try:
            if process.returncode is None:
                process.kill()
        except ProcessLookupError:
            pass
        except Exception as e:
            logger.warning(f"{label} 进程终止失败: {e}")
        try:
            await process.communicate()
        except Exception as e:
            logger.warning(f"{label} 进程回收失败: {e}")

    @classmethod
    def _is_tiktok_url(cls, url: str) -> bool:
        return cls._host_matches(cls._get_host(url), "tiktok.com")

    @staticmethod
    def _build_tiktok_author(nickname: str, unique_id: str) -> str:
        normalized_unique_id = str(unique_id or "").lstrip("@")
        if normalized_unique_id:
            return (
                f"{nickname}(@{normalized_unique_id})"
                if nickname else f"@{normalized_unique_id}"
            )
        return nickname

    @staticmethod
    def _build_tiktok_display_url(
        page_url: str,
        unique_id: str,
        item_id: str,
        is_gallery: bool
    ) -> str:
        normalized_unique_id = str(unique_id or "").lstrip("@")
        normalized_item_id = str(item_id or "").strip()
        if normalized_unique_id and normalized_item_id:
            content_type = "photo" if is_gallery else "video"
            return (
                f"https://www.tiktok.com/@{normalized_unique_id}/"
                f"{content_type}/{normalized_item_id}"
            )
        return TikTokParser._strip_query_and_fragment(page_url)

    @classmethod
    def _is_supported_tiktok_media_url(cls, url: str) -> bool:
        if not cls._is_tiktok_url(url):
            return False
        try:
            parsed = urlparse(url)
        except Exception:
            return False
        path = parsed.path or ""
        host = cls._get_host(url)
        if host in {"vm.tiktok.com", "vt.tiktok.com"}:
            return True
        if path.startswith("/t/"):
            return True
        if re.search(r"/@[^/]+/(?:video|photo)/\d+", path):
            return True
        if host == "m.tiktok.com" and re.search(r"/v/\d+(?:\.html)?$", path):
            return True
        return False

    def can_parse(self, url: str) -> bool:
        """判断是否可以解析此 URL。"""
        if not url:
            logger.debug(f"[{self.name}] can_parse: URL为空")
            return False

        if self._is_supported_tiktok_media_url(url):
            logger.debug(f"[{self.name}] can_parse: 匹配TikTok链接 {url}")
            return True

        logger.debug(f"[{self.name}] can_parse: 无法解析 {url}")
        return False

    def extract_links(self, text: str) -> List[str]:
        """从文本中提取 TikTok 链接。"""
        result_links: List[str] = []
        seen_keys = set()
        seen_urls = set()

        patterns = [
            (
                r"https?://(?:vm|vt)\.tiktok\.com/[^\s<>\"'()]+",
                lambda match, url: f"tiktok:short:{url.lower()}",
            ),
            (
                r"https?://(?:www\.)?tiktok\.com/t/[^\s<>\"'()]+",
                lambda match, url: f"tiktok:t:{url.lower()}",
            ),
            (
                r"https?://(?:www\.|m\.)?tiktok\.com/@[^\s/]+/"
                r"(?:video|photo)/(\d+)[^\s<>\"'()]*",
                lambda match, url: f"tiktok:item:{match.group(1)}",
            ),
            (
                r"https?://m\.tiktok\.com/v/(\d+)(?:\.html)?[^\s<>\"'()]*",
                lambda match, url: f"tiktok:item:{match.group(1)}",
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

    @classmethod
    def _extract_tiktok_item_from_json(
        cls,
        data: Dict[str, Any],
        item_id: str = ""
    ) -> Optional[Dict[str, Any]]:
        """从 TikTok 页面 JSON 中抽取 itemStruct。"""
        fallback_item = None

        if "__DEFAULT_SCOPE__" in data:
            scope = data.get("__DEFAULT_SCOPE__", {})
            detail = scope.get("webapp.video-detail") or {}
            item_info = detail.get("itemInfo") or {}
            item = item_info.get("itemStruct") or {}
            if item and (not item_id or str(item.get("id")) == str(item_id)):
                return item
            user_detail = scope.get("webapp.user-detail") or {}
            for candidate in user_detail.get("itemList") or []:
                if not isinstance(candidate, dict):
                    continue
                if not item_id or str(candidate.get("id")) == str(item_id):
                    return candidate
            if item:
                fallback_item = item

        if "ItemModule" in data and isinstance(data["ItemModule"], dict):
            if item_id and item_id in data["ItemModule"]:
                return data["ItemModule"][item_id]
            for item in data["ItemModule"].values():
                if isinstance(item, dict):
                    fallback_item = fallback_item or item

        for candidate in cls._walk_dicts(data):
            item = None
            if isinstance(candidate.get("itemStruct"), dict):
                item = candidate.get("itemStruct")
            elif isinstance(candidate.get("itemInfo"), dict):
                item = candidate["itemInfo"].get("itemStruct")
            elif (
                candidate.get("id") and
                (
                    candidate.get("video") or
                    candidate.get("imagePost") or
                    candidate.get("imagePostInfo")
                )
            ):
                item = candidate

            if not isinstance(item, dict) or not item:
                continue
            if not fallback_item:
                fallback_item = item
            if not item_id or str(item.get("id")) == str(item_id):
                return item

        return fallback_item

    async def fetch_tiktok_oembed(
        self,
        session: aiohttp.ClientSession,
        page_url: str
    ) -> Optional[Dict[str, Any]]:
        """获取 TikTok oEmbed 元数据。"""
        try:
            async with session.get(
                "https://www.tiktok.com/oembed",
                params={"url": page_url},
                headers=self.tiktok_headers,
                proxy=self._get_proxy(),
            ) as response:
                if response.status >= 400:
                    return None
                data = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError):
            return None

        return data if isinstance(data, dict) else None

    async def fetch_tiktok_page(
        self,
        page_url: str
    ) -> Optional[Dict[str, str]]:
        """优先使用系统 curl 拉取 TikTok 页面，规避 aiohttp 指纹触发的 WAF。"""
        curl_path = shutil.which("curl") or shutil.which("curl.exe")
        if not curl_path:
            return None

        marker = "__CURL_EFFECTIVE_URL__:"
        last_page_data = None
        for attempt in range(5):
            curl_args = [
                curl_path,
                "-L",
                "-sS",
                "--compressed",
                "--connect-timeout",
                str(Config.TIKTOK_CURL_CONNECT_TIMEOUT),
                "--max-time",
                str(Config.TIKTOK_CURL_MAX_TIME),
                "-A",
                TIKTOK_USER_AGENT,
                "-H",
                f"Referer: {TIKTOK_REFERER}",
                "-H",
                "Accept-Language: en-US,en;q=0.9",
            ]
            proxy = self._get_proxy()
            if proxy:
                curl_args.extend(["-x", proxy])
            curl_args.extend([
                "-w",
                f"\n{marker}%{{url_effective}}",
                page_url,
            ])
            process = await asyncio.create_subprocess_exec(
                *curl_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, _ = await asyncio.wait_for(
                    process.communicate(),
                    timeout=Config.TIKTOK_CURL_MAX_TIME + 5
                )
            except asyncio.TimeoutError:
                await self._terminate_subprocess(process, "TikTok curl")
                logger.warning(f"TikTok curl 超时: {page_url}")
                continue
            except asyncio.CancelledError:
                await self._terminate_subprocess(process, "TikTok curl")
                raise
            if process.returncode != 0 or not stdout:
                continue

            output = stdout.decode("utf-8", errors="replace")
            if marker not in output:
                continue

            html_text, effective_url = output.rsplit(marker, 1)
            effective_url = effective_url.strip()
            if not effective_url:
                continue

            page_data = {
                "url": effective_url,
                "html": html_text,
            }
            last_page_data = page_data

            if (
                "__UNIVERSAL_DATA_FOR_REHYDRATION__" in html_text or
                "playAddr" in html_text
            ) and "Please wait..." not in html_text:
                return page_data

            if attempt < 4:
                await asyncio.sleep(0.6)

        return last_page_data

    def _extract_tiktok_video_url_list(
        self,
        video_info: Dict[str, Any]
    ) -> List[str]:
        urls: List[str] = []
        self._extend_unique_urls(
            urls,
            self._extract_nested_http_urls(video_info.get("playAddr"))
        )
        self._extend_unique_urls(
            urls,
            self._extract_nested_http_urls(video_info.get("downloadAddr"))
        )
        self._extend_unique_urls(
            urls,
            self._extract_nested_http_urls(video_info.get("PlayAddrStruct"))
        )
        for bitrate_info in video_info.get("bitrateInfo") or []:
            self._extend_unique_urls(
                urls,
                self._extract_nested_http_urls(
                    (bitrate_info or {}).get("PlayAddr")
                )
            )
        return urls

    def _extract_tiktok_image_url_lists(
        self,
        item_info: Dict[str, Any]
    ) -> List[List[str]]:
        image_root = item_info.get("imagePostInfo") or item_info.get("imagePost")
        if not image_root:
            return []

        if isinstance(image_root, list):
            image_items = image_root
        elif isinstance(image_root, dict):
            image_items = None
            for key in (
                "images",
                "imageList",
                "imagePostImages",
                "imagePostImageList",
            ):
                value = image_root.get(key)
                if isinstance(value, list):
                    image_items = value
                    break
            if image_items is None:
                image_items = image_root.get("images") or []
        else:
            image_items = []

        image_url_lists: List[List[str]] = []
        for image_item in image_items:
            urls: List[str] = []
            if isinstance(image_item, dict):
                for key in (
                    "imageURL",
                    "imageUrl",
                    "displayImage",
                    "originImage",
                    "downloadImage",
                    "ownerWatermarkImage",
                    "ownerWatermarkUrl",
                    "image",
                    "urlList",
                    "url_list",
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

            if urls:
                image_url_lists.append(urls)

        return image_url_lists

    def _extract_tiktok_video_url_list_from_html(
        self,
        html_text: str
    ) -> List[str]:
        match = re.search(r'"playAddr":"([^"]+)"', html_text)
        if not match:
            return []

        decoded_url = self._decode_json_string(match.group(1))
        if decoded_url.startswith(("http://", "https://")):
            return [decoded_url]
        return []

    def _build_tiktok_result_from_item(
        self,
        item_info: Dict[str, Any],
        normalized_page_url: str,
        detail_data: Optional[Dict[str, Any]] = None,
        oembed_info: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        author_info = item_info.get("author", {})
        unique_id = (
            author_info.get("uniqueId")
            or author_info.get("unique_id")
            or (oembed_info or {}).get("author_unique_id")
            or ""
        )
        nickname = (
            author_info.get("nickname")
            or (oembed_info or {}).get("author_name")
            or ""
        )

        image_url_lists = self._extract_tiktok_image_url_lists(item_info)
        is_gallery = bool(image_url_lists)
        video_url_list: List[str] = []
        if not is_gallery:
            video_url_list = self._extract_tiktok_video_url_list(
                item_info.get("video") or {}
            )
        if not video_url_list and not image_url_lists:
            return None

        share_meta = (
            detail_data.get("shareMeta")
            if isinstance(detail_data, dict) else {}
        ) or {}
        title = (
            item_info.get("desc")
            or share_meta.get("desc")
            or share_meta.get("title")
            or (oembed_info or {}).get("title")
            or "TikTok"
        )
        item_id = str(item_info.get("id") or "").strip()
        return {
            "title": title,
            "author": self._build_tiktok_author(nickname, unique_id),
            "timestamp": self._format_timestamp(
                item_info.get("createTime") or item_info.get("create_time")
            ),
            "video_url_list": video_url_list,
            "image_url_lists": image_url_lists,
            "is_gallery": is_gallery,
            "display_url": self._build_tiktok_display_url(
                normalized_page_url,
                unique_id,
                item_id,
                is_gallery
            ),
            "user_agent": TIKTOK_USER_AGENT,
            "use_image_proxy": self.use_proxy,
            "use_video_proxy": self.use_proxy,
            "proxy_url": self._get_proxy(),
        }

    async def fetch_tiktok_info(
        self,
        session: aiohttp.ClientSession,
        page_url: str,
        response_text: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """获取 TikTok 视频 / 图集信息。"""
        normalized_page_url = self._strip_query_and_fragment(page_url)
        item_match = (
            re.search(r"/(?:video|photo)/(\d+)", normalized_page_url) or
            re.search(r"/v/(\d+)(?:\.html)?$", normalized_page_url)
        )
        item_id = item_match.group(1) if item_match else ""

        if response_text is None:
            try:
                async with session.get(
                    normalized_page_url,
                    headers=self.tiktok_headers,
                    proxy=self._get_proxy(),
                ) as response:
                    if response.status >= 400:
                        return None
                    response_text = await response.text()
            except (aiohttp.ClientError, asyncio.TimeoutError):
                return None

        oembed_info = await self.fetch_tiktok_oembed(session, normalized_page_url)
        for script_id in (
            "__UNIVERSAL_DATA_FOR_REHYDRATION__",
            "SIGI_STATE",
        ):
            script_json = self.extract_script_json(response_text, script_id)
            if not script_json:
                continue
            try:
                json_data = json.loads(script_json)
            except Exception:
                continue

            default_scope = json_data.get("__DEFAULT_SCOPE__", {})
            detail_data = default_scope.get("webapp.video-detail", {})
            item_info = {}
            if isinstance(detail_data, dict):
                item_info = (detail_data.get("itemInfo") or {}).get(
                    "itemStruct",
                    {}
                )
            if not item_info:
                item_info = self._extract_tiktok_item_from_json(
                    json_data,
                    item_id
                ) or {}

            if item_info:
                result = self._build_tiktok_result_from_item(
                    item_info,
                    normalized_page_url,
                    detail_data if isinstance(detail_data, dict) else None,
                    oembed_info
                )
                if result:
                    return result

        video_url_list = self._extract_tiktok_video_url_list_from_html(
            response_text
        )
        if not video_url_list:
            return None

        author = ""
        title = "TikTok"
        display_url = normalized_page_url
        if oembed_info:
            title = oembed_info.get("title") or title
            author = self._build_tiktok_author(
                oembed_info.get("author_name", ""),
                oembed_info.get("author_unique_id", "")
            )
            item_id = str(oembed_info.get("embed_product_id") or "").strip()
            display_url = self._build_tiktok_display_url(
                normalized_page_url,
                oembed_info.get("author_unique_id", ""),
                item_id,
                False
            )

        return {
            "title": title,
            "author": author,
            "timestamp": "",
            "video_url_list": video_url_list,
            "image_url_lists": [],
            "is_gallery": False,
            "display_url": display_url,
            "user_agent": TIKTOK_USER_AGENT,
            "use_image_proxy": self.use_proxy,
            "use_video_proxy": self.use_proxy,
            "proxy_url": self._get_proxy(),
        }

    @classmethod
    def _is_short_redirect_url(cls, url: str) -> bool:
        host = cls._get_host(url)
        try:
            path = urlparse(url).path or ""
        except Exception:
            path = ""
        if host in {"vm.tiktok.com", "vt.tiktok.com"}:
            return True
        return cls._is_tiktok_url(url) and path.startswith("/t/")

    async def get_redirected_url(
        self,
        session: aiohttp.ClientSession,
        url: str
    ) -> str:
        """获取重定向后的 URL。"""
        try:
            async with session.head(
                url,
                headers=self.tiktok_headers,
                allow_redirects=True,
                proxy=self._get_proxy(),
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
                headers=self.tiktok_headers,
                allow_redirects=True,
                proxy=self._get_proxy(),
            ) as response:
                return str(response.url)
        except asyncio.CancelledError:
            raise

    async def _parse_tiktok(
        self,
        session: aiohttp.ClientSession,
        original_url: str
    ) -> Dict[str, Any]:
        logger.debug(f"[{self.name}] parse: 检测到TikTok链接")

        page_data = await self.fetch_tiktok_page(original_url)
        if page_data:
            final_url = page_data.get("url", original_url)
            if is_live_url(final_url) or is_live_url(original_url):
                raise SkipParse("直播域名链接不解析")

            result = await self.fetch_tiktok_info(
                session,
                final_url,
                response_text=page_data.get("html", "")
            )
            if result:
                return result

        redirected_url = await self.get_redirected_url(session, original_url)
        if is_live_url(redirected_url) or is_live_url(original_url):
            raise SkipParse("直播域名链接不解析")

        target_url = (
            redirected_url
            if self._is_tiktok_url(redirected_url)
            else original_url
        )
        result = await self.fetch_tiktok_info(session, target_url)
        if not result:
            raise RuntimeError(f"无法获取TikTok视频信息: {original_url}")
        return result

    @staticmethod
    def _build_result_headers(user_agent: str) -> Dict[str, Dict[str, str]]:
        return {
            "image_headers": build_request_headers(
                is_video=False,
                referer=TIKTOK_REFERER,
                origin=TIKTOK_ORIGIN,
                user_agent=user_agent,
            ),
            "video_headers": build_request_headers(
                is_video=True,
                referer=TIKTOK_REFERER,
                origin=TIKTOK_ORIGIN,
                user_agent=user_agent,
            ),
        }

    async def parse(
        self,
        session: aiohttp.ClientSession,
        url: str
    ) -> Optional[Dict[str, Any]]:
        """解析单个 TikTok 链接。"""
        logger.debug(f"[{self.name}] parse: 开始解析 {url}")
        async with self.semaphore:
            result = await self._parse_tiktok(session, url)
            is_gallery = bool(result.get("is_gallery", False))
            image_url_lists = [
                url_list
                for url_list in result.get("image_url_lists", [])
                if url_list
            ]
            video_url_list = result.get("video_url_list") or []
            title = result.get("title", "")
            author = result.get("author", "")
            timestamp = result.get("timestamp", "")
            display_url = result.get("display_url", url)
            user_agent = result.get("user_agent", TIKTOK_USER_AGENT)
            headers = self._build_result_headers(user_agent)
            proxy_fields = {}
            for key in ("use_image_proxy", "use_video_proxy", "proxy_url"):
                if key in result:
                    proxy_fields[key] = result.get(key)

            if is_gallery:
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
                    "platform": "tiktok",
                    "parser_name": self.name,
                    "video_urls": [],
                    "image_urls": image_url_lists,
                    "image_headers": headers["image_headers"],
                    "video_headers": headers["video_headers"],
                    **proxy_fields,
                }

            if not video_url_list:
                logger.debug(f"[{self.name}] parse: 无法获取视频URL {url}")
                raise RuntimeError(f"无法获取视频URL: {url}")

            parsed_result = {
                "url": display_url,
                "title": title,
                "author": author,
                "desc": "",
                "timestamp": timestamp,
                "platform": "tiktok",
                "parser_name": self.name,
                "video_urls": [video_url_list],
                "image_urls": [],
                "image_headers": headers["image_headers"],
                "video_headers": headers["video_headers"],
                **proxy_fields,
            }
            logger.debug(
                f"[{self.name}] parse: 解析完成(tiktok) {url}, "
                f"title={title[:50]}"
            )
            return parsed_result
