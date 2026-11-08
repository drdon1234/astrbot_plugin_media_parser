"""红果短剧解析器，按分享中的分集获取完整视频与剧集资料。"""

import asyncio
import base64
import binascii
import html
import json
import math
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

import aiohttp

from ....logger import logger

from ....constants import Config
from ....types import MediaMetadata
from ...utils import build_request_headers
from ..base import BaseVideoParser
from .sign import decode_video_key
from .web import HongguoClient


BASE_URL = "https://hongguoduanju.com"
WEB_HOSTS = frozenset({"hongguoduanju.com", "www.hongguoduanju.com"})
SHARE_HOSTS = frozenset({"novelquickapp.com", "www.novelquickapp.com"})
SHARE_PATHS = frozenset({
    "/hongguo/ug/pages/video-animation-share",
    "/hongguo/ug/pages/video-detail-share",
})
NUMERIC_ID = re.compile(r"[1-9][0-9]{0,31}")
URL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.:/@%?=&#-])(?:https?://|//)?(?:www\.)?"
    r"(?:hongguoduanju\.com|novelquickapp\.com)"
    r"[^\s<>\"'`()\[\]{}，。！？；：、（）【】《》「」,;!\u3400-\u9fff]*",
    re.IGNORECASE,
)
MEDIA_DOMAINS = ("byteimg.com", "douyinpic.com", "douyinvod.com", "qznovelvod.com", "snssdk.com")
MAX_PAGE_BYTES = 4 * 1024 * 1024


def _identity(value: str) -> Tuple[str, str, str]:
    """校验入口并提取系列与指定集，短链暂不带内容编号。"""
    if not isinstance(value, str) or not value.strip():
        return "", "", ""
    value = html.unescape(value.strip())
    if re.search(r"[\s\x00-\x1f\x7f\\]", value):
        return "", "", ""
    if value.startswith("//"):
        value = "https:" + value
    elif "://" not in value:
        value = "https://" + value
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        if (parts.scheme.lower() not in {"http", "https"}
                or parts.username or parts.password or parts.port not in {None, 80, 443}):
            return "", "", ""
        query = parse_qs(parts.query, keep_blank_values=True, max_num_fields=64)
    except ValueError:
        return "", "", ""
    path = parts.path.rstrip("/")
    if host in SHARE_HOSTS and re.fullmatch(r"/s/[A-Za-z0-9_-]{1,128}", path):
        return f"https://{host}{path}/", "", ""
    series_id, video_id = "", ""
    if host in WEB_HOSTS:
        match = re.fullmatch(r"/player/([1-9][0-9]{0,31})/([1-9][0-9]{0,31})", path)
        if match:
            series_id, video_id = match.groups()
            return f"{BASE_URL}/player/{series_id}/{video_id}", series_id, video_id
        if path == "/detail":
            values = query.get("series_id", [])
            if len(values) == 1:
                series_id = values[0]
    elif host in SHARE_HOSTS and path in SHARE_PATHS:
        values = query.get("video_id", [])
        episodes = query.get("vid", [])
        if len(values) != 1 or len(episodes) != 1:
            return "", "", ""
        series_id, video_id = values[0], episodes[0]
        if not NUMERIC_ID.fullmatch(video_id):
            return "", "", ""
    if not NUMERIC_ID.fullmatch(series_id):
        return "", "", ""
    params = {"video_id": series_id, "vid": video_id} if video_id else {"series_id": series_id}
    canonical = urlunsplit(("https", host, path, urlencode(params), ""))
    return canonical, series_id, video_id


def _media_url(value: Any) -> str:
    """读取平台 CDN 的直接或 Base64 地址，拒绝裁剪预览。"""
    if not isinstance(value, str) or len(value) > 16384:
        return ""
    value = value.strip()
    if not value.startswith(("https://", "http://", "//")):
        try:
            value = base64.b64decode(value + "=" * (-len(value) % 4), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeError, ValueError):
            return ""
    if value.startswith("//"):
        value = "https:" + value
    if re.search(r"[\s\x00-\x1f\x7f\\]", value):
        return ""
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        if (parts.scheme not in {"http", "https"} or parts.username or parts.password
                or parts.port not in {None, 80, 443}
                or not any(host == domain or host.endswith("." + domain) for domain in MEDIA_DOMAINS)):
            return ""
        query = parse_qs(parts.query, keep_blank_values=True, max_num_fields=128)
        if "start" in query or "end" in query:
            return ""
    except ValueError:
        return ""
    return value


class _PageState(HTMLParser):
    """只读取页面 JSON，不执行内联脚本。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: List[Tuple[bool, str]] = []
        self._active = False
        self._is_json = False
        self._parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        """记录内联脚本及框架的 JSON 数据节点。"""
        if tag == "script":
            values = dict(attrs)
            self._active = not values.get("src")
            self._is_json = values.get("id") == "__MODERN_ROUTER_DATA__"
            self._parts = []

    def handle_data(self, data: str) -> None:
        """收集脚本中的原始文本。"""
        if self._active:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        """封存完整脚本供 JSON 解码器读取。"""
        if tag == "script" and self._active:
            self.scripts.append((self._is_json, "".join(self._parts)))
            self._active = False


class HongguoParser(BaseVideoParser):
    """解析红果短链、剧集详情与指定分集，不展开整部短剧。"""

    def __init__(self, hot_comment_count: int = 0) -> None:
        """初始化分集解析器。

        Args:
            hot_comment_count: 有限弹幕窗口的输出条数上限，零表示不请求。
        """
        super().__init__("hongguo")
        try:
            self.hot_comment_count = max(0, int(hot_comment_count))
        except (TypeError, ValueError):
            self.hot_comment_count = 0
        self.client = HongguoClient()
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)
        self.headers = build_request_headers(referer=BASE_URL + "/")

    def can_parse(self, url: str) -> bool:
        """判断是否是红果支持的分享或剧集入口。

        Args:
            url: 待判断的链接。

        Returns:
            是否为有效入口。
        """
        return bool(_identity(url)[0])

    def extract_links(self, text: str) -> List[str]:
        """提取有效链接，保留出现顺序并去除重复入口。

        Args:
            text: 消息文本。

        Returns:
            红果链接列表。
        """
        links = []
        seen = set()
        for match in URL_PATTERN.finditer(text or ""):
            value = match.group().rstrip(".,")
            canonical, _, _ = _identity(value)
            if canonical and canonical not in seen:
                seen.add(canonical)
                links.append(value)
        return links

    async def _resolve(self, session: aiohttp.ClientSession, url: str) -> Tuple[str, str]:
        """逐跳展开短链，只接受已知红果入口；vid 优先于分享预览编号。"""
        current = url
        for _ in range(5):
            canonical, series_id, video_id = _identity(current)
            if not canonical:
                raise RuntimeError("红果分享跳转到了不支持的地址")
            if series_id:
                return series_id, video_id
            async with session.get(
                canonical, headers=self.headers, allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=Config.DEFAULT_TIMEOUT),
            ) as response:
                if response.status not in {301, 302, 303, 307, 308}:
                    raise RuntimeError("红果短链未返回有效跳转，链接可能已失效")
                location = response.headers.get("Location", "")
                if not location:
                    raise RuntimeError("红果短链缺少跳转地址")
                current = urljoin(canonical, location)
        raise RuntimeError("红果分享跳转次数过多")

    async def _detail(self, session: aiohttp.ClientSession, series_id: str) -> Dict[str, Any]:
        """从官网详情取得完整分集表和作品资料。"""
        async with session.get(
            f"{BASE_URL}/detail?series_id={series_id}", headers=self.headers,
            allow_redirects=False, timeout=aiohttp.ClientTimeout(total=Config.DEFAULT_TIMEOUT),
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"红果剧集详情请求失败（HTTP {response.status}）")
            chunks = []
            size = 0
            async for chunk in response.content.iter_chunked(64 * 1024):
                size += len(chunk)
                if size > MAX_PAGE_BYTES:
                    raise RuntimeError("红果剧集页面过大，已停止读取")
                chunks.append(chunk)
        parser = _PageState()
        parser.feed(b"".join(chunks).decode("utf-8"))
        parser.close()
        for is_json, script in parser.scripts:
            match = re.search(r"(?:window\.)?_ROUTER_DATA\s*=\s*(?=\{)", script)
            if not is_json and not match:
                continue
            raw = script.strip() if is_json else script[match.end():]
            try:
                data = json.JSONDecoder().raw_decode(raw)[0]
            except (ValueError, RecursionError):
                continue
            loader = data.get("loaderData") if isinstance(data, dict) else None
            page = loader.get("detail_page") if isinstance(loader, dict) else None
            if isinstance(page, dict) and page.get("isSuccess") is not False:
                detail = page.get("seriesDetail")
                if isinstance(detail, dict):
                    return detail
        raise RuntimeError("红果官网未返回有效剧集资料")

    @staticmethod
    def _select_media(model: Dict[str, Any]) -> Tuple[List[str], Dict[str, str]]:
        """优先高清同画质 H.264，并为每个候选保留独立解密密钥。"""
        variants = model.get("video_list")
        if isinstance(variants, dict):
            variants = list(variants.values())
        if not isinstance(variants, list):
            raise RuntimeError("红果未返回完整视频播放列表")
        choices = []
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            meta = variant.get("video_meta") or {}
            encryption = variant.get("encrypt_info") or {}
            if not isinstance(meta, dict) or not isinstance(encryption, dict):
                continue
            codec = str(meta.get("codec_type") or "").lower()
            if codec not in {"", "h264", "avc1", "h265", "hevc", "bytevc1"}:
                continue
            gear = str(variant.get("gear_des_key", "")).lower()
            if "bytevc2" in gear:
                continue
            method = encryption.get("encryption_method")
            if method not in (None, "", "cenc-aes-ctr"):
                continue
            key = ""
            # 档位明确声明加密时，缺失密钥信息不能把密文当成普通视频。
            if (encryption.get("spade_a") or encryption.get("encrypt") or method
                    or "1:encrypt" in gear):
                try:
                    key = decode_video_key(encryption.get("spade_a", ""))
                except ValueError:
                    continue
            try:
                definition = re.search(r"[0-9]+", str(meta.get("definition", "")))
                quality = int(definition[0]) if definition else min(int(meta.get("vwidth", 0)), int(meta.get("vheight", 0)))
            except (TypeError, ValueError, OverflowError):
                quality = 0
            addresses = []
            for field in ("main_url", "backup_url", "backup_url_1", "backup_url_2", "backup_urls", "url_list"):
                values = variant.get(field)
                for value in values if isinstance(values, list) else [values]:
                    address = _media_url(value)
                    if address and address not in addresses:
                        addresses.append(address)
            for address in addresses:
                choices.append((quality, codec in {"h264", "avc1"}, address, key))
        choices.sort(key=lambda item: item[:2], reverse=True)
        urls: List[str] = []
        keys: Dict[str, str] = {}
        for _, _, address, key in choices:
            if address in urls:
                if keys.get(address, "") != key:
                    raise RuntimeError("红果媒体候选的解密信息不一致")
                continue
            urls.append(address)
            if key:
                keys[address] = key
        if not urls:
            raise RuntimeError("红果未返回可用的完整视频，编码或媒体密钥可能不受支持")
        return urls, keys

    async def _fetch_hot_comments(
        self, session: aiohttp.ClientSession, series_id: str, video_id: str, duration_ms: int,
    ) -> List[Dict[str, Any]]:
        """读取至多三个分集弹幕窗口，校验归属并保留失败前的有效内容。"""
        comments, seen, cursors = [], set(), set()
        if self.hot_comment_count <= 0:
            return comments
        offset, cursor = 0, ""
        try:
            for _ in range(3):
                payload = await self.client.request(session, f"/novel/commentapi/comment/list/{video_id}/v1/", {
                    "comment_source": 601, "server_channel": 1000, "group_id": video_id,
                    "group_type": 30, "comment_type": 20, "sort": 1,
                    "count": min(90, self.hot_comment_count - len(comments)), "cursor": cursor,
                    "aid": 8662, "compliance_status": 0,
                    "business_param": {"book_id": series_id, "start_offset_time": offset,
                                       "playlet_item_duration": duration_ms, "need_danmaku_guide_type": [1, 3, 4, 2]},
                })
                data = payload.get("data")
                if not isinstance(data, dict) or not isinstance(data.get("data_list"), list):
                    raise ValueError("红果弹幕列表格式无效")
                for row in data["data_list"]:
                    comment = row.get("comment") if isinstance(row, dict) else None
                    if not isinstance(comment, dict):
                        continue
                    common, expand, stat = (comment.get(key) for key in ("common", "expand", "stat"))
                    if not isinstance(common, dict) or str(common.get("group_id")) != video_id:
                        continue
                    content, identity = common.get("content"), comment.get("comment_id")
                    if type(identity) not in (str, int) or not NUMERIC_ID.fullmatch(str(identity)) or str(identity) in seen:
                        continue
                    message = content.get("text") if isinstance(content, dict) else None
                    if not isinstance(message, str) or not message.strip():
                        continue
                    user = common.get("user_info")
                    user = user if isinstance(user, dict) else {}
                    profile = user.get("base_info")
                    profile = profile if isinstance(profile, dict) else {}
                    position = expand.get("offset_time") if isinstance(expand, dict) else None
                    if type(position) is int and position >= 0 and (not duration_ms or position < duration_ms):
                        seconds = position // 1000
                        message = f"[弹幕位置 {seconds // 60:02d}:{seconds % 60:02d}] {message.strip()}"
                    time_text = ""
                    created = common.get("create_timestamp")
                    if type(created) is int and created > 0:
                        try:
                            time_text = datetime.fromtimestamp(created, timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
                        except (ValueError, OverflowError, OSError):
                            pass
                    item = {
                        "id": str(identity), "username": str(profile.get("name") or "未知用户"),
                        "uid": str(user.get("user_id") or ""),
                        "message": message.strip(), "time": time_text,
                    }
                    likes = stat.get("digg_count") if isinstance(stat, dict) else None
                    if type(likes) is int and likes >= 0:
                        item["likes"] = likes
                    seen.add(str(identity))
                    comments.append(item)
                    if len(comments) >= self.hot_comment_count:
                        return comments
                info, extra = data.get("common_list_info"), data.get("extra")
                if not isinstance(info, dict) or info.get("has_more") is not True or not isinstance(extra, dict):
                    break
                next_cursor, next_offset = info.get("cursor"), extra.get("next_query_danmaku_list_time")
                if (not isinstance(next_cursor, str) or not next_cursor or next_cursor in cursors
                        or type(next_offset) is not int or next_offset <= offset
                        or (duration_ms > 0 and next_offset >= duration_ms)):
                    break
                cursors.add(next_cursor)
                cursor, offset = next_cursor, next_offset
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, RuntimeError) as exc:
            logger.warning(f"红果分集弹幕获取失败，已保留已有内容与弹幕：{type(exc).__name__}")
        return comments

    async def parse(self, session: aiohttp.ClientSession, url: str) -> Optional[MediaMetadata]:
        """读取剧集资料、指定集播放候选与可选分集弹幕。

        Args:
            session: 解析管理器提供的 HTTP 会话。
            url: 红果短链、详情或分集链接。

        Returns:
            剧集资料、可用视频及解密信息、可选弹幕；取流失败时说明视频不可用。

        Raises:
            ValueError: 入口不受支持。
            RuntimeError: 剧集身份不符或详情接口不可用。
        """
        canonical, _, _ = _identity(url)
        if not canonical:
            raise ValueError("不支持的红果链接")
        async with self.semaphore:
            try:
                series_id, video_id = await self._resolve(session, canonical)
                detail = await self._detail(session, series_id)
                if str(detail.get("series_id", "")) != series_id:
                    raise RuntimeError("红果返回了其他剧集的资料")
                title = detail.get("series_name")
                videos = detail.get("vid_list")
                if (not isinstance(title, str) or not title.strip() or not isinstance(videos, list)
                        or not videos or any(not isinstance(vid, str) or not NUMERIC_ID.fullmatch(vid) for vid in videos)
                        or len(set(videos)) != len(videos)):
                    raise RuntimeError("红果剧集标题或分集列表无效")
                video_id = video_id or videos[0]
                if video_id not in videos:
                    raise RuntimeError("红果剧集不包含所请求的分集")
                episode = videos.index(video_id) + 1
                addresses, keys, duration = [], {}, 0
                video_error = False
                try:
                    payload = await self.client.request(session, "/novel/player/video_model/v1/", {
                        "video_id": video_id, "content_type": 1,
                        "biz_param": {"need_all_video_definition": True, "video_platform": 3},
                    })
                    data = payload.get("data")
                    model = data.get("video_model") if isinstance(data, dict) else None
                    if isinstance(model, str):
                        model = json.loads(model)
                    if not isinstance(model, dict):
                        raise RuntimeError("红果播放信息格式无效")
                    addresses, keys = self._select_media(model)
                    try:
                        duration = float(model.get("video_duration", 0))
                    except (TypeError, ValueError, OverflowError):
                        duration = 0
                    if not math.isfinite(duration) or duration <= 0:
                        raise RuntimeError("红果完整视频时长无效")
                except asyncio.CancelledError:
                    raise
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, RuntimeError, UnicodeError) as exc:
                    addresses, keys, duration = [], {}, 0
                    video_error = True
                    logger.warning(f"红果完整视频取流失败，继续保留剧集资料与分集弹幕：{type(exc).__name__}")
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError, ValueError) as exc:
                raise RuntimeError("红果网络请求失败或响应格式无效") from exc

        desc = detail.get("series_intro")
        cover = _media_url(detail.get("series_cover"))
        metadata: MediaMetadata = {
            "url": canonical, "platform": "hongguo", "title": f"{title.strip()} 第{episode}集",
            "desc": f"第{episode}集 / 共{len(videos)}集" + (f"\n{desc.strip()}" if isinstance(desc, str) and desc.strip() else ""),
            "video_urls": [addresses] if addresses else [], "video_cover_urls": [[cover]] if cover and addresses else [],
            "image_headers": self.headers.copy(),
            "video_headers": build_request_headers(is_video=True, referer="https://novel.snssdk.com/"),
            "video_force_download": True,
            "timelength_ms": round(duration * 1000) if duration else None,
        }
        if video_error:
            metadata["access_status"] = "unavailable"
            metadata["access_message"] = "当前完整视频取流失败，已保留剧集资料及可获取的分集弹幕"
        if keys:
            metadata["video_decryption_keys"] = keys
        celebrities = detail.get("celebrities")
        if isinstance(celebrities, list):
            names = [item["nickname"].strip() for item in celebrities
                     if isinstance(item, dict) and isinstance(item.get("nickname"), str) and item["nickname"].strip()]
            if names:
                metadata["author"] = " / ".join(dict.fromkeys(names))
        try:
            published = int(detail.get("first_visible_time", 0))
            if published > 0:
                metadata["timestamp"] = datetime.fromtimestamp(
                    published, timezone(timedelta(hours=8)),
                ).strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError, OSError, OverflowError):
            pass
        if self.hot_comment_count:
            async with self.semaphore:
                comments = await self._fetch_hot_comments(session, series_id, video_id, (metadata.get("timelength_ms") or 0))
            if comments:
                metadata["hot_comments"] = comments
                metadata["hot_comments_label"] = "分集弹幕"
        return metadata
