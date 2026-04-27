"""闲鱼商品页解析器。"""
import asyncio
import hashlib
import html
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

import aiohttp

from ...constants import Config
from ...logger import logger
from ..utils import SkipParse, build_request_headers, is_live_url
from .base import BaseVideoParser


MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Mobile Safari/537.36"
)

XIANYU_MTOP_APP_KEY = "34839810"
XIANYU_MTOP_JSV = "2.7.2"
XIANYU_MTOP_TIMEOUT = "20000"
XIANYU_MTOP_BASE = "https://h5api.m.goofish.com"
XIANYU_DETAIL_API = "mtop.taobao.idle.awesome.detail"
XIANYU_DETAIL_API_VERSION = "1.0"
HTTP_URL_RE = re.compile(r"https?://[^\s<>\"']+")


class XianyuParser(BaseVideoParser):

    """闲鱼商品页解析器。"""

    def __init__(self):
        super().__init__("xianyu")
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)

    @staticmethod
    def _get_host(url: str) -> str:
        try:
            return (urlparse(url).hostname or "").lower().strip(".")
        except Exception:
            return ""

    @classmethod
    def _is_short_share_url(cls, url: str) -> bool:
        return cls._get_host(url) == "m.tb.cn"

    @classmethod
    def _is_goofish_item_url(cls, url: str) -> bool:
        host = cls._get_host(url)
        if host not in {"www.goofish.com", "h5.m.goofish.com"}:
            return False
        try:
            return (urlparse(url).path or "").rstrip("/") == "/item"
        except Exception:
            return False

    def can_parse(self, url: str) -> bool:
        if not url:
            return False
        return self._is_short_share_url(url) or self._is_goofish_item_url(url)

    def extract_links(self, text: str) -> List[str]:
        result_links: List[str] = []
        seen = set()
        patterns = [
            r"https?://m\.tb\.cn/[^\s<>\"'()]+",
            r"https?://(?:www\.)?goofish\.com/item[^\s<>\"'()]+",
            r"https?://h5\.m\.goofish\.com/item[^\s<>\"'()]+",
        ]

        for pattern in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                link = match.group(0).rstrip(".,!?)]}>\"'，。！？；：）】》」")
                key = link.lower()
                if key in seen:
                    continue
                seen.add(key)
                result_links.append(link)

        if result_links:
            logger.debug(
                f"[{self.name}] extract_links: 提取到 {len(result_links)} 个链接: "
                f"{result_links[:3]}{'...' if len(result_links) > 3 else ''}"
            )
        else:
            logger.debug(f"[{self.name}] extract_links: 未提取到链接")
        return result_links

    @staticmethod
    def _build_html_headers(user_agent: str) -> Dict[str, str]:
        return {
            "User-Agent": user_agent,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        }

    @staticmethod
    def _build_mobile_item_url(item_id: str) -> str:
        return f"https://h5.m.goofish.com/item?id={item_id}&itemId={item_id}"

    @staticmethod
    def _build_pc_item_url(item_id: str) -> str:
        return f"https://www.goofish.com/item?id={item_id}"

    def _extract_redirect_url_from_short_page(self, html_text: str) -> str:
        patterns = [
            r"var\s+url\s*=\s*'([^']+)'",
            r'var\s+url\s*=\s*"([^"]+)"',
            r"window\.location(?:\.replace)?\((['\"])(https?://[^'\"]+)\1\)",
        ]
        for pattern in patterns:
            match = re.search(pattern, html_text, re.IGNORECASE)
            if not match:
                continue
            raw_url = match.group(1 if match.lastindex == 1 else 2)
            decoded = html.unescape(raw_url)
            decoded = decoded.replace("\\u002F", "/").replace("\\/", "/")
            return unquote(decoded)
        return ""

    def _extract_item_id_from_url(self, url: str) -> str:
        try:
            parsed = urlparse(url)
        except Exception:
            return ""

        query = parse_qs(parsed.query, keep_blank_values=True)
        for key in ("id", "itemId", "item_id"):
            values = query.get(key) or []
            for value in values:
                value_str = str(value or "").strip()
                if re.fullmatch(r"\d{8,20}", value_str):
                    return value_str

        for segment in (parsed.path or "").split("/"):
            if re.fullmatch(r"\d{8,20}", segment):
                return segment
        return ""

    async def _resolve_source_context(
        self,
        session: aiohttp.ClientSession,
        url: str
    ) -> Dict[str, str]:
        source_url = url
        mobile_url = ""
        pc_url = ""
        item_id = self._extract_item_id_from_url(url)

        if self._is_short_share_url(url):
            async with session.get(
                url,
                headers=self._build_html_headers(MOBILE_UA),
                allow_redirects=True,
            ) as response:
                final_url = str(response.url)
                html_text = await response.text()

            redirect_url = self._extract_redirect_url_from_short_page(html_text)
            candidate_url = redirect_url or final_url
            if not self._is_goofish_item_url(candidate_url):
                raise SkipParse("短链未展开为闲鱼商品页")

            source_url = url
            mobile_url = (
                candidate_url if self._get_host(candidate_url) == "h5.m.goofish.com"
                else ""
            )
            item_id = self._extract_item_id_from_url(candidate_url) or item_id

        elif self._is_goofish_item_url(url):
            host = self._get_host(url)
            if host == "h5.m.goofish.com":
                mobile_url = url
            else:
                pc_url = url

        if not item_id:
            raise RuntimeError("无法从闲鱼链接中提取商品 id")

        if not mobile_url:
            mobile_url = self._build_mobile_item_url(item_id)
        if not pc_url:
            pc_url = self._build_pc_item_url(item_id)

        return {
            "item_id": item_id,
            "source_url": source_url,
            "mobile_url": mobile_url,
            "pc_url": pc_url,
        }

    @staticmethod
    def _build_mtop_headers(user_agent: str, referer: str) -> Dict[str, str]:
        return {
            "User-Agent": user_agent,
            "Accept": "application/json",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Origin": "https://h5.m.goofish.com",
            "Referer": referer,
        }

    @staticmethod
    def _build_mtop_params(
        api: str,
        version: str,
        sign: str,
        timestamp_ms: str,
    ) -> Dict[str, str]:
        return {
            "jsv": XIANYU_MTOP_JSV,
            "appKey": XIANYU_MTOP_APP_KEY,
            "t": timestamp_ms,
            "sign": sign,
            "v": version,
            "type": "originaljson",
            "accountSite": "xianyu",
            "dataType": "json",
            "timeout": XIANYU_MTOP_TIMEOUT,
            "api": api,
            "sessionOption": "AutoLoginOnly",
        }

    @staticmethod
    def _extract_token_from_cookie_jar(
        session: aiohttp.ClientSession
    ) -> str:
        cookies = session.cookie_jar.filter_cookies(XIANYU_MTOP_BASE)
        raw_token = ""
        cookie = cookies.get("_m_h5_tk")
        if cookie is not None:
            raw_token = cookie.value
        if not raw_token:
            return ""
        return raw_token.split("_", 1)[0]

    async def _prime_mtop_token(
        self,
        session: aiohttp.ClientSession,
        api: str,
        version: str,
        data_str: str,
        headers: Dict[str, str],
    ) -> None:
        url = f"{XIANYU_MTOP_BASE}/h5/{api}/{version}/"
        params = self._build_mtop_params(
            api=api,
            version=version,
            sign="",
            timestamp_ms=str(int(datetime.now().timestamp() * 1000)),
        )
        async with session.post(
            url,
            params=params,
            data={"data": data_str},
            headers=headers,
        ) as response:
            await response.text()

    async def _call_signed_mtop(
        self,
        session: aiohttp.ClientSession,
        api: str,
        version: str,
        data_obj: Dict[str, Any],
        referer: str,
        user_agent: str = MOBILE_UA,
    ) -> Dict[str, Any]:
        data_str = json.dumps(
            data_obj,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        headers = self._build_mtop_headers(user_agent, referer)
        url = f"{XIANYU_MTOP_BASE}/h5/{api}/{version}/"

        token = self._extract_token_from_cookie_jar(session)
        if not token:
            await self._prime_mtop_token(session, api, version, data_str, headers)
            token = self._extract_token_from_cookie_jar(session)
        if not token:
            raise RuntimeError("无法获取闲鱼 MTop 令牌")

        for attempt in range(2):
            timestamp_ms = str(int(datetime.now().timestamp() * 1000))
            sign = hashlib.md5(
                f"{token}&{timestamp_ms}&{XIANYU_MTOP_APP_KEY}&{data_str}".encode("utf-8")
            ).hexdigest()
            params = self._build_mtop_params(
                api=api,
                version=version,
                sign=sign,
                timestamp_ms=timestamp_ms,
            )
            async with session.post(
                url,
                params=params,
                data={"data": data_str},
                headers=headers,
            ) as response:
                payload = await response.json(content_type=None)

            ret_list = payload.get("ret") or []
            if any(
                "FAIL_SYS_TOKEN" in str(item or "")
                for item in ret_list
            ):
                if attempt == 0:
                    await self._prime_mtop_token(
                        session,
                        api,
                        version,
                        data_str,
                        headers,
                    )
                    token = self._extract_token_from_cookie_jar(session)
                    if token:
                        continue
                raise RuntimeError(f"闲鱼接口令牌失效: {ret_list}")

            if ret_list and not all(
                str(item or "").startswith("SUCCESS")
                for item in ret_list
            ):
                raise RuntimeError(
                    "闲鱼详情接口返回失败: "
                    + " | ".join(str(item or "") for item in ret_list)
                )

            return payload.get("data") or {}

        raise RuntimeError("闲鱼详情接口请求失败")

    def _format_timestamp(self, timestamp_value: Any) -> str:
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
    def _normalize_http_url(url: str) -> str:
        if not url:
            return ""
        normalized = str(url).strip()
        if normalized.startswith("//"):
            normalized = "https:" + normalized
        elif normalized.startswith("http://"):
            normalized = "https://" + normalized[7:]
        return normalized

    @staticmethod
    def _first_non_empty(*values: Any) -> str:
        for value in values:
            value_str = str(value or "").strip()
            if value_str:
                return value_str
        return ""

    def _extract_seller_name(self, detail_data: Dict[str, Any]) -> str:
        floating = (
            ((detail_data.get("flowData") or {}).get("floating") or {})
            .get("components") or []
        )
        for component in floating:
            data = component.get("data") or {}
            nickname = self._first_non_empty(
                data.get("nick"),
                data.get("userInfo", {}).get("nick"),
            )
            if nickname and "***" not in nickname:
                return nickname

        share_data = ((detail_data.get("itemDO") or {}).get("shareData") or {})
        share_info_raw = share_data.get("shareInfoJsonString") or ""
        if share_info_raw:
            try:
                share_info = json.loads(share_info_raw)
                nickname = self._first_non_empty(
                    (
                        (share_info.get("contentParams") or {})
                        .get("headerParams") or {}
                    ).get("title")
                )
                if nickname:
                    return nickname
            except (TypeError, ValueError, json.JSONDecodeError):
                pass

        seller_do = detail_data.get("sellerDO") or {}
        return self._first_non_empty(
            seller_do.get("nick"),
            seller_do.get("desensitizationNick"),
        )

    def _extract_seller_id(self, detail_data: Dict[str, Any]) -> str:
        seller_do = detail_data.get("sellerDO") or {}
        seller_id = seller_do.get("sellerId")
        if seller_id not in (None, ""):
            return str(seller_id)

        floating = (
            ((detail_data.get("flowData") or {}).get("floating") or {})
            .get("components") or []
        )
        for component in floating:
            data = component.get("data") or {}
            seller_id = data.get("sellerId")
            if seller_id not in (None, ""):
                return str(seller_id)
        return ""

    def _extract_text_description(self, detail_data: Dict[str, Any]) -> str:
        item_do = detail_data.get("itemDO") or {}
        desc = self._first_non_empty(item_do.get("desc"))
        if desc:
            return desc

        sections = (
            (((detail_data.get("flowData") or {}).get("body") or {}).get("sections"))
            or []
        )
        for section in sections:
            for component in section.get("components") or []:
                data = component.get("data") or {}
                desc = self._first_non_empty(data.get("desc"))
                if desc:
                    return desc
        return ""

    @staticmethod
    def _collect_item_tags(item_do: Dict[str, Any]) -> List[str]:
        tags: List[str] = []
        for item in item_do.get("itemLabelExtList") or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("propertyText") or "").strip()
            value = str(item.get("text") or item.get("valueText") or "").strip()
            if key and value:
                tags.append(f"{key}：{value}")
        return tags

    def _extract_image_url_lists(self, detail_data: Dict[str, Any]) -> List[List[str]]:
        image_lists: List[List[str]] = []
        seen = set()

        def push(url: str) -> None:
            normalized = self._normalize_http_url(url)
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            image_lists.append([normalized])

        item_do = detail_data.get("itemDO") or {}
        for image in item_do.get("imageInfos") or []:
            if isinstance(image, dict):
                push(image.get("url") or "")

        sections = (
            (((detail_data.get("flowData") or {}).get("body") or {}).get("sections"))
            or []
        )
        for section in sections:
            for component in section.get("components") or []:
                data = component.get("data") or {}
                for image in data.get("imageInfos") or []:
                    if isinstance(image, dict):
                        push(image.get("url") or "")

        share_data = ((item_do.get("shareData") or {}).get("shareInfoJsonString") or "")
        if share_data:
            try:
                share_info = json.loads(share_data)
                images = (
                    (((share_info.get("contentParams") or {}).get("mainParams") or {})
                     .get("images")) or []
                )
                for image in images:
                    if isinstance(image, dict):
                        push(image.get("image") or "")
            except (TypeError, ValueError, json.JSONDecodeError):
                pass

        return image_lists

    def _extract_video_url_lists(self, detail_data: Dict[str, Any]) -> List[List[str]]:
        video_lists: List[List[str]] = []
        seen = set()
        # 当前闲鱼商品发布规则下，一个商品最多仅携带一个视频；
        # 这里收集到的多个 URL 视为同一视频的多条候选链路。
        current_candidates: List[str] = []

        def push_candidate(url: str) -> None:
            normalized = self._normalize_http_url(url)
            if not normalized or normalized in seen:
                return
            if not (
                normalized.endswith(".mp4")
                or ".m3u8" in normalized
                or "/play/" in normalized
                or "video" in normalized.lower()
            ):
                return
            seen.add(normalized)
            current_candidates.append(normalized)

        def walk(obj: Any, key_hint: str = "") -> None:
            if isinstance(obj, dict):
                for key, value in obj.items():
                    walk(value, str(key))
            elif isinstance(obj, list):
                for value in obj:
                    walk(value, key_hint)
            elif isinstance(obj, str):
                key_lower = key_hint.lower()
                if not any(
                    token in key_lower
                    for token in ("video", "play", "media", "stream", "url")
                ):
                    return
                decoded = obj.replace("\\u002F", "/").replace("\\/", "/")
                if decoded.startswith(("http://", "https://", "//")):
                    push_candidate(decoded)
                    return
                for matched in HTTP_URL_RE.findall(decoded):
                    push_candidate(matched)

        walk(detail_data)
        if current_candidates:
            video_lists.append(current_candidates)
        return video_lists

    def _build_description(self, detail_data: Dict[str, Any]) -> str:
        item_do = detail_data.get("itemDO") or {}
        seller_do = detail_data.get("sellerDO") or {}
        lines: List[str] = []

        sold_price = self._first_non_empty(item_do.get("soldPrice"))
        price_unit = self._first_non_empty(item_do.get("priceUnit"))
        if sold_price:
            lines.append(f"价格：¥{sold_price}{price_unit}")

        transport_fee = self._first_non_empty(item_do.get("transportFee"))
        if transport_fee:
            lines.append(f"运费：¥{transport_fee}")

        location = self._first_non_empty(
            seller_do.get("publishCity"),
            seller_do.get("city"),
        )
        if location:
            lines.append(f"位置：{location}")

        tags = self._collect_item_tags(item_do)
        if tags:
            lines.extend(tags[:6])

        desc = self._extract_text_description(detail_data)
        if desc:
            lines.append(desc)

        compact_lines: List[str] = []
        seen = set()
        for line in lines:
            normalized = str(line or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            compact_lines.append(normalized)
        return "\n".join(compact_lines)

    def _build_metadata_from_detail_data(
        self,
        source_url: str,
        item_id: str,
        detail_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        item_do = detail_data.get("itemDO") or {}
        title = self._first_non_empty(item_do.get("title"))
        if not title:
            raise RuntimeError("闲鱼详情缺少标题")

        seller_name = self._extract_seller_name(detail_data)
        seller_id = self._extract_seller_id(detail_data)
        author = ""
        if seller_name and seller_id:
            author = f"{seller_name}(主页id:{seller_id})"
        elif seller_name:
            author = seller_name
        elif seller_id:
            author = f"(主页id:{seller_id})"

        referer = self._build_mobile_item_url(item_id)
        image_urls = self._extract_image_url_lists(detail_data)
        video_urls = self._extract_video_url_lists(detail_data)
        timestamp = self._format_timestamp(item_do.get("gmtCreate"))

        return {
            "url": source_url,
            "title": title,
            "author": author,
            "desc": self._build_description(detail_data),
            "timestamp": timestamp,
            "video_urls": video_urls,
            "image_urls": image_urls,
            "image_headers": build_request_headers(
                is_video=False,
                referer=referer,
                user_agent=MOBILE_UA,
            ),
            "video_headers": build_request_headers(
                is_video=True,
                referer=referer,
                user_agent=MOBILE_UA,
            ),
        }

    async def parse(
        self,
        session: aiohttp.ClientSession,
        url: str
    ) -> Optional[Dict[str, Any]]:
        logger.debug(f"[{self.name}] parse: 开始解析 {url}")
        async with self.semaphore:
            context = await self._resolve_source_context(session, url)
            item_id = context["item_id"]

            if is_live_url(context["mobile_url"]) or is_live_url(context["pc_url"]):
                raise SkipParse("直播域名链接不解析")

            detail_data = await self._call_signed_mtop(
                session,
                api=XIANYU_DETAIL_API,
                version=XIANYU_DETAIL_API_VERSION,
                data_obj={"itemId": item_id},
                referer=context["mobile_url"],
                user_agent=MOBILE_UA,
            )
            if not detail_data:
                raise RuntimeError("闲鱼详情接口返回空数据")

            metadata = self._build_metadata_from_detail_data(
                source_url=context["source_url"],
                item_id=item_id,
                detail_data=detail_data,
            )
            logger.debug(
                f"[{self.name}] parse: 解析完成 {url}, "
                f"title={metadata.get('title', '')[:50]}, "
                f"video_count={len(metadata.get('video_urls', []))}, "
                f"image_count={len(metadata.get('image_urls', []))}"
            )
            return metadata
