"""微信解析器，分流公众号匿名图文解析和视频号登录态解析。"""

import asyncio
import html
import math
import re
import secrets
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp

from ....constants import Config
from ....types import MediaMetadata
from ...utils import build_request_headers
from ..base import BaseVideoParser
from .article import parse_article_page


WECHAT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)
ARTICLE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/131 Safari/537.36"
)
WEIXIN_HOST = "weixin.qq.com"
CHANNELS_HOST = "channels.weixin.qq.com"
ARTICLE_HOST = "mp.weixin.qq.com"
CHANNELS_REFERER = "https://channels.weixin.qq.com/"
URL_TRAILING_PUNCTUATION = ".,!?)]}>\"'，。！？；：）】》」"
HTTP_URL_RE = re.compile(
    r"(?<![A-Za-z0-9_.:/@-])https?://[^\s<>\"'()]+", re.IGNORECASE
)
SHORT_PATH_RE = re.compile(r"^/sph/[A-Za-z0-9]+/?$", re.IGNORECASE)
PREVIEW_PATH_RE = re.compile(
    r"^/finder-preview/pages/(?:sph|feed)/?$", re.IGNORECASE
)
ARTICLE_PATH_RE = re.compile(r"^/s/[A-Za-z0-9_-]+/?$")


class WechatParser(BaseVideoParser):
    """解析公众号文章与视频号分享链接，返回统一媒体元数据。"""

    PARSE_URL = "https://yuanbao.tencent.com/api/weixin/get_parse_result"
    FEED_INFO_URL = (
        "https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info"
    )

    def __init__(
        self,
        yuanbao_cookie: str = "",
    ) -> None:
        """初始化微信解析器。

        Args:
            yuanbao_cookie: 腾讯元宝网页 Cookie，用于短链换取视频号令牌。
        """
        super().__init__("wechat")
        self.yuanbao_cookie = str(yuanbao_cookie or "").strip()
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)
        self.yuanbao_headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Content-Type": "application/json",
            "Origin": "https://yuanbao.tencent.com",
            "Referer": "https://yuanbao.tencent.com/chat",
            "User-Agent": WECHAT_USER_AGENT,
            "X-Language": "zh-CN",
            "X-Platform": "mac",
            "X-Requested-With": "XMLHttpRequest",
            "X-Source": "web",
        }
        self.media_headers = build_request_headers(
            is_video=True,
            referer=CHANNELS_REFERER,
            origin="https://channels.weixin.qq.com",
            user_agent=WECHAT_USER_AGENT,
        )

    def can_parse(self, url: str) -> bool:
        """判断是否可以解析微信公众号或视频号链接。

        Args:
            url: 待判断的链接。

        Returns:
            是否为受支持的公众号文章或视频号分享链接。
        """
        return self._identify_url(url) is not None

    def extract_links(self, text: str) -> List[str]:
        """从文本中提取公众号文章、视频号短链和预览长链并去重。

        Args:
            text: 包含分享链接的消息文本。

        Returns:
            保留原始顺序的去重链接列表。
        """
        links: List[str] = []
        seen = set()
        for match in HTTP_URL_RE.finditer(text or ""):
            link = html.unescape(match.group(0).rstrip(URL_TRAILING_PUNCTUATION))
            if not self.can_parse(link):
                continue
            if link in seen:
                continue
            seen.add(link)
            links.append(link)
        return links

    @staticmethod
    def _identify_url(url: str) -> Optional[str]:
        """返回微信链接类型，非法链接返回 None。"""
        if not isinstance(url, str) or not url.strip():
            return None
        try:
            parsed = urlparse(html.unescape(url.strip()))
            if parsed.scheme.lower() not in {"http", "https"}:
                return None
            if parsed.username or parsed.password:
                return None
            if parsed.port not in {None, 80, 443}:
                return None
        except (TypeError, ValueError):
            return None

        host = (parsed.hostname or "").lower().strip(".")
        path = parsed.path or ""
        if host == ARTICLE_HOST:
            if ARTICLE_PATH_RE.fullmatch(path):
                return "article"
            query = parse_qs(parsed.query)
            if path.rstrip("/") == "/s" and all(
                query.get(key, [""])[0].strip() for key in ("__biz", "mid", "idx", "sn")
            ):
                return "article"
        if host == WEIXIN_HOST and SHORT_PATH_RE.fullmatch(path):
            return "short"
        if host == CHANNELS_HOST and PREVIEW_PATH_RE.fullmatch(path):
            return "preview"
        return None

    @staticmethod
    def _extract_token_eid(url: str) -> Tuple[str, str]:
        """从预览长链查询参数提取 token 与 eid。"""
        if not isinstance(url, str) or not url.strip():
            return "", ""
        try:
            query = parse_qs(urlparse(html.unescape(url.strip())).query)
        except (TypeError, ValueError):
            return "", ""
        token = (query.get("token") or [""])[0].strip()
        export_id = (query.get("eid") or query.get("exportId") or [""])[0].strip()
        return token, export_id

    @staticmethod
    async def _read_json_response(response: aiohttp.ClientResponse) -> Dict[str, Any]:
        """读取并校验接口 JSON 响应。"""
        try:
            payload = await response.json(content_type=None)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("微信视频号接口返回了无效 JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("微信视频号接口返回的 JSON 不是对象")
        return payload

    async def _parse_share_url(
        self,
        session: aiohttp.ClientSession,
        share_url: str,
    ) -> Tuple[str, str]:
        """调用腾讯元宝接口，将视频号短链换成 token 和 eid。"""
        if not self.yuanbao_cookie:
            raise RuntimeError(
                "微信视频号解析需要配置腾讯元宝 Cookie；"
                "请登录 yuanbao.tencent.com 后复制 Cookie"
            )

        headers = {**self.yuanbao_headers, "cookie": self.yuanbao_cookie}
        payload = {
            "type": "video_channel_url",
            "url": share_url,
            "scene": 1,
        }
        try:
            async with session.post(
                self.PARSE_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
                allow_redirects=False,
            ) as response:
                if response.status == 401:
                    raise RuntimeError("腾讯元宝 Cookie 已失效，请重新登录并更新 Cookie")
                if response.status != 200:
                    raise RuntimeError(
                        f"腾讯元宝接口请求失败（HTTP {response.status}）"
                    )
                data = await self._read_json_response(response)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError as exc:
            raise RuntimeError("腾讯元宝接口请求超时") from exc
        except aiohttp.ClientError as exc:
            raise RuntimeError("腾讯元宝接口网络请求失败") from exc

        result = data.get("data") or {}
        if not isinstance(result, dict):
            raise RuntimeError("腾讯元宝接口返回的数据格式无效")

        playable_url = result.get("playable_url") or ""
        token, export_id = self._extract_token_eid(str(playable_url))
        export_id = export_id or str(result.get("wx_export_id") or "").strip()
        if not token or not export_id:
            raise RuntimeError(
                "腾讯元宝未返回有效的视频号 token 或 eid，"
                "请检查 Cookie 登录状态和链接是否可访问"
            )
        return token, export_id

    async def _get_feed_info(
        self,
        session: aiohttp.ClientSession,
        export_id: str,
        token: str,
    ) -> Dict[str, Any]:
        """调用视频号预览接口，获取视频直链和媒体信息。"""
        request_id = f"{int(time.time()):x}-{secrets.token_hex(4)}"
        api_url = (
            f"{self.FEED_INFO_URL}?_rid={request_id}"
            "&_pageUrl=https%3A%2F%2Fchannels.weixin.qq.com"
            "%2Ffinder-preview%2Fpages%2Ffeed"
        )
        query = urlencode(
            {
                "entry_card_type": 48,
                "comment_scene": 39,
                "appid": 0,
                "token": token,
                "entry_scene": 0,
                "eid": export_id,
            }
        )
        referer = f"https://channels.weixin.qq.com/finder-preview/pages/feed?{query}"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://channels.weixin.qq.com",
            "Referer": referer,
            "User-Agent": WECHAT_USER_AGENT,
        }
        payload = {
            "baseReq": {"generalToken": token},
            "exportId": export_id,
        }
        try:
            async with session.post(
                api_url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise RuntimeError(
                        f"视频号预览接口请求失败（HTTP {response.status}）"
                    )
                data = await self._read_json_response(response)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError as exc:
            raise RuntimeError("视频号预览接口请求超时") from exc
        except aiohttp.ClientError as exc:
            raise RuntimeError("视频号预览接口网络请求失败") from exc

        err_code = data.get("errCode")
        if err_code not in (None, 0):
            raise RuntimeError(
                f"视频号预览接口返回错误: {data.get('errMsg') or err_code}"
            )
        return data

    @staticmethod
    def _pick_video_url(feed_info: Dict[str, Any]) -> str:
        """按编码优先级提取视频直链。"""
        for key in ("h264VideoInfo", "h265VideoInfo"):
            info = feed_info.get(key)
            if isinstance(info, dict):
                video_url = str(info.get("videoUrl") or "").strip()
                if video_url:
                    return video_url
        return str(feed_info.get("videoUrl") or "").strip()

    @staticmethod
    def _pick_duration(feed_info: Dict[str, Any]) -> Optional[int]:
        """将接口返回的秒级时长转换为毫秒。"""
        for key in ("h264VideoInfo", "h265VideoInfo"):
            info = feed_info.get(key)
            if not isinstance(info, dict):
                continue
            try:
                seconds = float(info.get("duration"))
            except (TypeError, ValueError, OverflowError):
                continue
            milliseconds = seconds * 1000
            if math.isfinite(milliseconds) and milliseconds >= 0:
                return round(milliseconds)
        return None

    @staticmethod
    def _format_timestamp(value: Any) -> str:
        """将视频号秒级时间戳格式化为日期。"""
        try:
            timestamp = int(value)
            if timestamp > 10**12:
                timestamp //= 1000
            return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OverflowError, OSError):
            return ""

    @staticmethod
    def _build_stats(feed_info: Dict[str, Any]) -> str:
        """拼接互动统计文本。"""
        pairs = (
            ("赞", feed_info.get("likeCountFmt")),
            ("收藏", feed_info.get("favCountFmt")),
            ("评论", feed_info.get("commentCountFmt")),
            ("转发", feed_info.get("forwardCountFmt")),
        )
        return " · ".join(
            f"{label} {value}" for label, value in pairs if str(value or "").strip()
        )

    def _build_metadata(
        self,
        feed: Dict[str, Any],
    ) -> MediaMetadata:
        """将视频号接口响应转换为统一媒体元数据。"""
        data = feed.get("data") or {}
        if not isinstance(data, dict):
            raise RuntimeError("视频号预览接口缺少 data 数据")

        feed_info = data.get("feedInfo") or {}
        author_info = data.get("authorInfo") or {}
        if not isinstance(feed_info, dict) or not isinstance(author_info, dict):
            raise RuntimeError("视频号预览接口返回的数据结构无效")

        error_info = data.get("errMsg")
        if isinstance(error_info, dict) and error_info.get("type"):
            raise RuntimeError(
                str(error_info.get("title") or "该视频号内容无法解析").strip()
            )

        video_url = self._pick_video_url(feed_info)
        if not video_url:
            raise RuntimeError(
                "未获取到视频号视频直链，可能是内容已删除、权限不足或当前内容不是视频"
            )

        cover_url = str(feed_info.get("coverUrl") or "").strip()
        video_headers = dict(self.media_headers)
        result: MediaMetadata = {
            "title": str(feed_info.get("description") or "").strip(),
            "author": str(author_info.get("nickname") or "视频号用户").strip(),
            "desc": self._build_stats(feed_info),
            "timestamp": self._format_timestamp(feed_info.get("createtime")),
            "platform": self.name,
            "video_urls": [[video_url]],
            "video_headers": video_headers,
            "image_headers": build_request_headers(
                is_video=False,
                referer=CHANNELS_REFERER,
                origin="https://channels.weixin.qq.com",
                user_agent=WECHAT_USER_AGENT,
            ),
            "video_force_download": True,
        }
        duration = self._pick_duration(feed_info)
        if duration is not None:
            result["timelength_ms"] = duration
        if cover_url:
            result["video_cover_urls"] = [[cover_url]]
        return result

    async def parse(
        self,
        session: aiohttp.ClientSession,
        url: str,
    ) -> Optional[MediaMetadata]:
        """解析微信链接并返回图文或视频元数据。

        Args:
            session: 由解析管理器提供的 HTTP 会话。
            url: 公众号文章链接、视频号短链或预览长链。

        Returns:
            包含正文、图片或视频的统一媒体元数据。

        Raises:
            ValueError: 链接不受支持。
            RuntimeError: 页面受限、凭据不可用或解析失败。
        """
        identity = self._identify_url(url)
        if identity is None:
            raise ValueError("无法识别的微信链接")

        async with self.semaphore:
            if identity == "article":
                return await self._parse_article(session, html.unescape(url.strip()))
            token, export_id = self._extract_token_eid(url)
            if not (token and export_id):
                token, export_id = await self._parse_share_url(session, url.strip())
            feed = await self._get_feed_info(session, export_id, token)
            return self._build_metadata(feed)

    async def _parse_article(
        self, session: aiohttp.ClientSession, url: str
    ) -> MediaMetadata:
        """匿名读取公众号文章，不携带视频号的元宝登录态。"""
        headers = {
            "User-Agent": ARTICLE_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://mp.weixin.qq.com/",
        }
        try:
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    raise RuntimeError(
                        f"微信公众号页面请求失败（HTTP {response.status}）"
                    )
                if urlparse(str(response.url)).path == "/mp/wappoc_appmsgcaptcha":
                    raise RuntimeError("微信公众号页面需要验证，暂时无法匿名解析")
                page = await response.text(encoding="utf-8", errors="replace")
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError as exc:
            raise RuntimeError("微信公众号页面请求超时") from exc
        except aiohttp.ClientError as exc:
            raise RuntimeError("微信公众号页面网络请求失败") from exc

        result = parse_article_page(page, url)
        result["platform"] = self.name
        result["image_headers"] = build_request_headers(
            is_video=False,
            referer="https://mp.weixin.qq.com/",
            user_agent=ARTICLE_USER_AGENT,
        )
        return result
