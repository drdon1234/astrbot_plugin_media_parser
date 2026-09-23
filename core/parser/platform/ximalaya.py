"""喜马拉雅解析器，匿名读取单集音频、简介、封面与首屏高赞评论。"""

import asyncio
import html
import json
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import aiohttp

from ...logger import logger

from ...constants import Config
from ...types import MediaMetadata
from ..utils import build_request_headers
from .base import BaseVideoParser


BASE_URL = "https://www.ximalaya.com"
TRACK_HOSTS = frozenset({"ximalaya.com", "www.ximalaya.com", "m.ximalaya.com"})
TRACK_URL_RE = re.compile(
    r"(?<![A-Za-z0-9_.:/@%?=&#-])(?:https?://|//)?(?:www\.|m\.)?ximalaya\.com"
    r"[^\s<>\"'`()\[\]{}，。！？；：、（）【】《》「」,;!]*",
    re.IGNORECASE,
)
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_COMMENT_COUNT = 20
CHINA_TIMEZONE = timezone(timedelta(hours=8))


def _track_id(value: str) -> str:
    """仅接受可信域名上的单集路径，排除专辑和评论详情页。"""
    if not isinstance(value, str) or not value.strip():
        return ""
    value = html.unescape(value.strip())
    if re.search(r"[\s\x00-\x1f\x7f\\]", value):
        return ""
    if value.startswith("//"):
        value = "https:" + value
    elif "://" not in value:
        value = "https://" + value
    try:
        parts = urlsplit(value)
        if (parts.scheme.lower() not in {"http", "https"}
                or (parts.hostname or "").lower() not in TRACK_HOSTS
                or parts.username or parts.password or parts.port not in {None, 80, 443}):
            return ""
    except ValueError:
        return ""
    match = re.fullmatch(r"/(?:[1-9][0-9]{0,19}/)?sound/([1-9][0-9]{0,19})/?", parts.path)
    return match.group(1) if match else ""


def _media_url(value: Any) -> str:
    """规范化平台媒体地址，不接受站外主机、凭据和异常端口。"""
    if not isinstance(value, str) or not value.strip():
        return ""
    value = html.unescape(value.strip())
    if re.search(r"[\s\x00-\x1f\x7f\\]", value):
        return ""
    if value.startswith("//"):
        value = "https:" + value
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        if (parts.scheme not in {"http", "https"}
                or not any(host == domain or host.endswith("." + domain)
                           for domain in ("xmcdn.com", "ximalaya.com"))
                or parts.username or parts.password or parts.port not in {None, 80, 443}):
            return ""
    except ValueError:
        return ""
    return value


def _integer(value: Any) -> Optional[int]:
    """读取非负整数字段，未知值不当作零。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,20}", value):
        return int(value)
    return None


def _comment_time(value: Any) -> str:
    """将接口毫秒时间戳转换为北京时间。"""
    milliseconds = _integer(value)
    if not milliseconds:
        return ""
    try:
        return datetime.fromtimestamp(milliseconds / 1000, CHINA_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return ""


class _TextParser(HTMLParser):
    """清理简介和评论中的 HTML，保留段落与表情文字。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        """保留段落、换行和图片提示，忽略脚本样式。"""
        if tag in {"script", "style", "noscript"}:
            self.hidden += 1
        if self.hidden:
            return
        if tag in {"p", "div", "li", "br", "hr", "blockquote"}:
            self.parts.append("\n")
        if tag == "img":
            values = dict(attrs)
            self.parts.append(values.get("alt") or ("[表情]" if "emoji" in (values.get("class") or "") else "[图片]"))

    def handle_endtag(self, tag: str) -> None:
        """结束隐藏区块或正文段落。"""
        if tag in {"script", "style", "noscript"}:
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and tag in {"p", "div", "li", "blockquote"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        """记录可见文字。"""
        if not self.hidden:
            self.parts.append(data)


def _text(value: Any) -> str:
    """清除 HTML 和多余空行，不将无效字段转成字符串。"""
    if not isinstance(value, str):
        return ""
    parser = _TextParser()
    parser.feed(value)
    parser.close()
    return "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())


class XimalayaParser(BaseVideoParser):
    """解析喜马拉雅单集，评论失败时保留已取得的详情和音频。"""

    def __init__(self, hot_comment_count: int = 0) -> None:
        """初始化单集解析器与评论数量。

        Args:
            hot_comment_count: 评论输出上限，零表示不请求评论。
        """
        super().__init__("ximalaya")
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)
        try:
            self.hot_comment_count = min(MAX_COMMENT_COUNT, max(0, int(hot_comment_count)))
        except (TypeError, ValueError, OverflowError):
            self.hot_comment_count = 0

    def can_parse(self, url: str) -> bool:
        """判断链接是否指向支持的单集。

        Args:
            url: 待判断的链接。

        Returns:
            是否包含可信域名与单集编号。
        """
        return bool(_track_id(url))

    def extract_links(self, text: str) -> List[str]:
        """提取单集链接并按编号去重，保留原链接以维持消息顺序。

        Args:
            text: 包含分享链接的消息文本。

        Returns:
            按出现顺序排列且不重复的单集链接。
        """
        links: List[str] = []
        seen = set()
        for match in TRACK_URL_RE.finditer(text or ""):
            value = match.group(0).rstrip(".,!?:")
            identity = _track_id(value)
            if identity and identity not in seen:
                seen.add(identity)
                links.append(value)
        return links

    async def _request_json(self, session: aiohttp.ClientSession, url: str) -> Dict[str, Any]:
        """有界读取固定平台接口，不跟随重定向。"""
        try:
            async with session.get(
                url,
                headers=build_request_headers(referer=BASE_URL + "/", custom_headers={"Accept": "application/json"}),
                timeout=aiohttp.ClientTimeout(total=25),
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise RuntimeError(f"喜马拉雅请求失败（HTTP {response.status}）")
                chunks = []
                size = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise RuntimeError("喜马拉雅响应过大，已停止读取")
                    chunks.append(chunk)
                payload = json.loads(b"".join(chunks).decode("utf-8"))
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError, ValueError) as exc:
            raise RuntimeError("喜马拉雅网络请求失败或响应格式无效") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("喜马拉雅接口未返回有效对象")
        return payload

    async def _comments(self, session: aiohttp.ClientSession, identity: str) -> List[Dict[str, Any]]:
        """仅读取移动端首屏高赞主评论，不递归楼中楼或自行重排。"""
        try:
            payload = await self._request_json(
                session,
                "https://m.ximalaya.com/m-revision/common/track/queryTrackCommentsFirstPage"
                f"?trackId={identity}&pageSize={self.hot_comment_count}&page=1",
            )
            data = payload.get("data")
            if payload.get("ret") != 0 or not isinstance(data, dict):
                raise RuntimeError("喜马拉雅评论接口未返回有效数据")
            if str(data.get("trackId")) != identity or not isinstance(data.get("comments"), list):
                raise RuntimeError("喜马拉雅评论身份或列表格式不符")
            comments: List[Dict[str, Any]] = []
            seen = set()
            for item in data["comments"]:
                if not isinstance(item, dict) or str(item.get("trackId")) != identity:
                    continue
                if _integer(item.get("parentId")) not in {None, 0}:
                    continue
                comment_id = _integer(item.get("id"))
                message = _text(item.get("content"))
                if not comment_id or comment_id in seen or not message:
                    continue
                seen.add(comment_id)
                comment: Dict[str, Any] = {
                    "id": str(comment_id), "username": _text(item.get("nickname")),
                    "uid": str(_integer(item.get("uid")) or ""),
                    "message": message, "time": _comment_time(item.get("createdAt")),
                }
                likes = _integer(item.get("likes"))
                if likes is not None:
                    comment["likes"] = likes
                comments.append(comment)
                if len(comments) >= self.hot_comment_count:
                    break
            return comments
        except asyncio.CancelledError:
            raise
        except RuntimeError as exc:
            logger.warning(f"[ximalaya] 高赞评论获取失败，已保留单集详情和音频：{exc}")
            return []

    async def parse(self, session: aiohttp.ClientSession, url: str) -> Optional[MediaMetadata]:
        """读取单集详情、公开音频候选和配置数量内的高赞评论。

        Args:
            session: 解析管理器提供的 HTTP 会话。
            url: 喜马拉雅单集链接。

        Returns:
            单集标题、主播、简介、封面、音频候选和可选评论。

        Raises:
            ValueError: 链接不是支持的单集。
            RuntimeError: 详情接口失效、内容不可用或身份不一致。
        """
        identity = _track_id(url)
        if not identity:
            raise ValueError("不支持的喜马拉雅单集链接")
        async with self.semaphore:
            payload = await self._request_json(session, f"https://m.ximalaya.com/tracks/{identity}.json")
            if str(payload.get("id")) != identity:
                raise RuntimeError("喜马拉雅单集不存在、暂不可访问或返回身份不一致")
            title = _text(payload.get("title"))
            if not title:
                raise RuntimeError("喜马拉雅单集缺少有效标题")
            audio_urls = list(dict.fromkeys(
                value for key in ("play_path_64", "play_path_32", "play_path")
                if (value := _media_url(payload.get(key)))
            ))
            # 试听与其他音频覆盖范围不同，不作为同一媒体的失败候选混用。
            regular_urls = [value for value in audio_urls if "_preview_" not in urlsplit(value).path]
            audio_urls = regular_urls or audio_urls
            cover_urls = list(dict.fromkeys(
                value for key in ("cover_url", "cover_url_142")
                if (value := _media_url(payload.get(key)))
            ))
            duration = _integer(payload.get("duration"))
            is_preview = bool(audio_urls and all("_preview_" in urlsplit(value).path for value in audio_urls))
            metadata: MediaMetadata = {
                "url": f"{BASE_URL}/sound/{identity}", "platform": "喜马拉雅",
                "title": title, "author": _text(payload.get("nickname")),
                "desc": _text(payload.get("intro")),
                "timestamp": _text(payload.get("formatted_created_at")),
                "video_urls": [], "audio_urls": [audio_urls] if audio_urls else [],
                "image_urls": [cover_urls] if cover_urls else [],
                "audio_headers": build_request_headers(referer=BASE_URL + "/", custom_headers={"Accept": "*/*"}),
                "image_headers": build_request_headers(referer=BASE_URL + "/"),
                "timelength_ms": duration * 1000 if duration else None,
                "is_preview_only": is_preview,
            }
            if not audio_urls:
                metadata["access_status"] = "unavailable"
                metadata["access_message"] = "当前接口未提供可播放音频，已保留单集信息"
            elif is_preview:
                metadata["access_status"] = "preview_only"
                metadata["access_message"] = "当前仅取得试听音频，试听时长未知"
            elif payload.get("is_paid") is False:
                metadata["access_status"] = "full"
                metadata["available_length_ms"] = metadata["timelength_ms"]
            else:
                metadata["access_status"] = "available"
            if self.hot_comment_count:
                comments = await self._comments(session, identity)
                if comments:
                    metadata["hot_comments"] = comments
            return metadata
