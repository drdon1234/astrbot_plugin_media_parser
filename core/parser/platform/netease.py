"""网易云音乐解析器，匿名读取单曲详情、可播放音频与热门评论。"""

import asyncio
import html
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import aiohttp

from ...logger import logger

from ...constants import Config
from ...types import MediaMetadata
from ..utils import build_request_headers, format_duration_ms
from .base import BaseVideoParser


BASE_URL = "https://music.163.com"
SONG_URL_RE = re.compile(
    r"(?<![A-Za-z0-9_./:@%?=&#-])(?:https?://|//)?music\.163\.com"
    r"(?::[0-9]+)?/[^\s<>\"'`()\[\]{}，。！？；：、（）【】《》「」,;!\u3400-\u9fff]+",
    re.IGNORECASE,
)
MAX_COMMENT_PAGES = 3
COMMENT_PAGE_SIZE = 20
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def _song_id(value: str) -> str:
    """仅从已支持的主机与单曲路径读取唯一正整数编号。"""
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
        parsed = urlparse(value)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or parsed.hostname != "music.163.com"
            or parsed.username or parsed.password
            or parsed.port not in {None, 80, 443}
        ):
            return ""
        path, query = parsed.path, parsed.query
        if path in {"", "/"}:
            fragment = urlparse(parsed.fragment)
            if fragment.scheme or fragment.netloc or fragment.fragment:
                return ""
            path, query = fragment.path, fragment.query
        if path not in {"/song", "/m/song"}:
            return ""
        ids = parse_qs(query, keep_blank_values=True).get("id", [])
    except (TypeError, ValueError):
        return ""
    if len(ids) != 1 or not re.fullmatch(r"[1-9][0-9]{0,19}", ids[0]):
        return ""
    return ids[0]


def _integer(value: Any) -> Optional[int]:
    """读取非负整数，未知或异常数值保留为空。"""
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result >= 0 else None


def _timestamp(value: Any) -> str:
    """将接口毫秒时间戳转换为现有元数据格式。"""
    milliseconds = _integer(value)
    if not milliseconds:
        return ""
    try:
        return datetime.fromtimestamp(milliseconds / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return ""


def _media_url(value: Any) -> str:
    """仅接收网易云媒体域名上的 HTTP 地址，排除凭据与异常端口。"""
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if re.search(r"[\s\x00-\x1f\x7f\\]", value):
        return ""
    if value.startswith("//"):
        value = "https:" + value
    try:
        parsed = urlparse(value)
        host = parsed.hostname or ""
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not any(host == domain or host.endswith("." + domain)
                       for domain in ("music.126.net", "music.163.com"))
            or parsed.username or parsed.password
            or parsed.port not in {None, 80, 443}
        ):
            return ""
    except ValueError:
        return ""
    return value


class NeteaseParser(BaseVideoParser):
    """解析网易云单曲，保留音频不可用时的详情与热评。"""

    def __init__(self, hot_comment_count: int = 0) -> None:
        """初始化请求并发限制和热评条数。

        Args:
            hot_comment_count: 热评输出上限，零表示不请求评论。
        """
        super().__init__("netease")
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)
        try:
            self.hot_comment_count = max(0, int(hot_comment_count))
        except (TypeError, ValueError, OverflowError):
            self.hot_comment_count = 0
        self._headers = build_request_headers(
            referer=BASE_URL + "/", custom_headers={"Accept": "application/json"}
        )

    def can_parse(self, url: str) -> bool:
        """判断是否为支持的网易云单曲链接。

        Args:
            url: 待判断的链接。

        Returns:
            是否包含可信主机、单曲路径和有效编号。
        """
        return bool(_song_id(url))

    def extract_links(self, text: str) -> List[str]:
        """按单曲编号去重并生成规范链接。

        Args:
            text: 包含歌曲分享链接的消息文本。

        Returns:
            按首次出现顺序排列的歌曲链接。
        """
        links: List[str] = []
        seen = set()
        for match in SONG_URL_RE.finditer(text or ""):
            identity = _song_id(match.group(0).rstrip(".,!?)]}>\"'，。！？；：）】》」"))
            if identity and identity not in seen:
                seen.add(identity)
                links.append(f"{BASE_URL}/song?id={identity}")
        return links

    async def _fetch_json(
        self, session: aiohttp.ClientSession, endpoint: str, params: Dict[str, str]
    ) -> Dict[str, Any]:
        """读取匿名接口，拒绝重定向和非成功业务响应。"""
        try:
            async with session.get(
                BASE_URL + endpoint, params=params, headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=25), allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise RuntimeError(f"网易云音乐接口请求失败（HTTP {response.status}）")
                chunks = []
                size = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise RuntimeError("网易云音乐响应过大，已停止读取")
                    chunks.append(chunk)
                payload = json.loads(b"".join(chunks).decode("utf-8"))
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            raise RuntimeError("网易云音乐请求失败或响应格式无效") from exc
        if not isinstance(payload, dict) or payload.get("code") != 200:
            raise RuntimeError("网易云音乐接口未返回可用内容")
        return payload

    async def _attach_audio(
        self, session: aiohttp.ClientSession, identity: str, metadata: MediaMetadata
    ) -> None:
        """按平台实际返回值记录完整音频、试听或不可播放状态。"""
        payload = await self._fetch_json(
            session, "/api/song/enhance/player/url/v1",
            {"ids": f"[{identity}]", "level": "standard", "encodeType": "mp3"},
        )
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise RuntimeError("网易云音乐音频响应缺少歌曲列表")
        entries = [row for row in rows if isinstance(row, dict) and str(row.get("id")) == identity]
        if len(entries) != 1:
            raise RuntimeError("网易云音乐音频响应的歌曲编号不一致")
        entry = entries[0]
        audio_url = _media_url(entry.get("url")) if entry.get("code") == 200 else ""
        if not audio_url:
            restricted = _integer(entry.get("fee")) in {1, 4}
            metadata.update({
                "access_status": "restricted" if restricted else "unavailable",
                "access_message": (
                    "当前匿名状态无法获取音频，歌曲可能需要会员或购买权限"
                    if restricted else "当前歌曲暂无可用音频，可能受版权或地区限制"
                ),
            })
            return

        available = _integer(entry.get("time")) or None
        trial = entry.get("freeTrialInfo")
        preview = isinstance(trial, dict) and bool(trial)
        if preview:
            start, end = _integer(trial.get("start")), _integer(trial.get("end"))
            if start is not None and end is not None and end > start:
                available = (end - start) * 1000
            else:
                # 未取得可信试听区间时，不把歌曲总时长当作片段时长。
                available = None
        full = metadata.get("timelength_ms")
        if available and full and available + 1000 < full:
            preview = True
        full_access = bool(not preview and available and full and available + 1000 >= full)
        metadata.update({
            "audio_urls": [[audio_url]],
            "access_status": "preview_only" if preview else "full" if full_access else "available",
            "is_preview_only": preview,
            "available_length_ms": available,
            "access_message": (
                f"仅可试听 {format_duration_ms(available)} / {format_duration_ms(full)}"
                if preview and available and full else "当前仅可获取试听片段"
                if preview else "当前歌曲可解析完整音频" if full_access
                else "当前已取得可播放音频，完整时长尚无法确认"
            ),
        })

    async def _fetch_hot_comments(
        self, session: aiohttp.ClientSession, identity: str
    ) -> List[Dict[str, Any]]:
        """有限分页获取热门评论，保留失败前已取得的有效评论。"""
        comments: List[Dict[str, Any]] = []
        if not self.hot_comment_count:
            return comments
        seen = set()
        page_size = min(self.hot_comment_count, COMMENT_PAGE_SIZE)
        try:
            for page in range(MAX_COMMENT_PAGES):
                payload = await self._fetch_json(
                    session, f"/api/v1/resource/hotcomments/R_SO_4_{identity}",
                    {"limit": str(page_size), "offset": str(page * page_size)},
                )
                rows = payload.get("hotComments")
                if not isinstance(rows, list):
                    raise RuntimeError("网易云音乐热评响应缺少评论列表")
                previous = len(seen)
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    comment_id = _integer(row.get("commentId"))
                    if not comment_id or comment_id in seen:
                        continue
                    seen.add(comment_id)
                    user, message = row.get("user"), row.get("content")
                    if not isinstance(user, dict) or not isinstance(message, str) or not message.strip():
                        continue
                    username = user.get("nickname")
                    if not isinstance(username, str) or not username.strip():
                        continue
                    comment = {
                        "id": str(comment_id), "username": username.strip(),
                        "uid": str(user.get("userId") or ""), "message": message.strip(),
                        "time": _timestamp(row.get("time")),
                    }
                    likes = _integer(row.get("likedCount"))
                    if likes is not None:
                        comment["likes"] = likes
                    comments.append(comment)
                    if len(comments) >= self.hot_comment_count:
                        return comments
                if payload.get("hasMore") is not True or not rows or len(seen) == previous:
                    break
        except asyncio.CancelledError:
            raise
        except RuntimeError as exc:
            logger.warning(f"[netease] 热评获取失败，已保留歌曲详情和已取得的评论：{exc}")
        return comments

    async def parse(self, session: aiohttp.ClientSession, url: str) -> Optional[MediaMetadata]:
        """获取单曲详情，并尽力附加当前可播放音频与热门评论。

        Args:
            session: 解析管理器提供的 HTTP 会话。
            url: 网易云音乐单曲链接。

        Returns:
            单曲图文信息、封面、可选音频和热评。

        Raises:
            ValueError: 链接不受支持。
            RuntimeError: 单曲详情不可用或歌曲编号不一致。
        """
        identity = _song_id(url)
        if not identity:
            raise ValueError("不支持的网易云音乐单曲链接")
        async with self.semaphore:
            payload = await self._fetch_json(session, "/api/song/detail/", {"ids": f"[{identity}]"})
            songs = payload.get("songs")
            if not isinstance(songs, list) or len(songs) != 1 or not isinstance(songs[0], dict):
                raise RuntimeError("网易云音乐单曲不存在或详情不可见")
            song = songs[0]
            if str(song.get("id")) != identity:
                raise RuntimeError("网易云音乐返回的歌曲编号与请求不一致")
            title = song.get("name")
            if not isinstance(title, str) or not title.strip():
                raise RuntimeError("网易云音乐单曲缺少有效标题")
            artists = song.get("artists")
            author = " / ".join(
                row["name"].strip() for row in artists
                if isinstance(row, dict) and isinstance(row.get("name"), str) and row["name"].strip()
            ) if isinstance(artists, list) else ""
            album = song.get("album")
            album = album if isinstance(album, dict) else {}
            cover = _media_url(album.get("picUrl"))
            album_name = album.get("name")
            metadata: MediaMetadata = {
                "url": f"{BASE_URL}/song?id={identity}", "title": title.strip(),
                "author": author, "platform": "网易云音乐",
                "desc": f"专辑：{album_name.strip()}" if isinstance(album_name, str) and album_name.strip() else "",
                "timestamp": _timestamp(album.get("publishTime")),
                "image_urls": [[cover]] if cover else [], "video_urls": [], "audio_urls": [],
                "image_headers": build_request_headers(referer=BASE_URL + "/"),
                "audio_headers": build_request_headers(is_video=True, referer=BASE_URL + "/"),
                "timelength_ms": _integer(song.get("duration")) or None,
                "available_length_ms": None, "is_preview_only": False,
                "access_status": "unavailable", "access_message": "当前暂时无法获取音频",
            }
            try:
                await self._attach_audio(session, identity, metadata)
            except asyncio.CancelledError:
                raise
            except RuntimeError as exc:
                logger.warning(f"[netease] 音频获取失败，已保留歌曲详情：{exc}")
            comments = await self._fetch_hot_comments(session, identity)
            if comments:
                metadata["hot_comments"] = comments
            return metadata
