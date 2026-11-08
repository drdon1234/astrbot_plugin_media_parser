"""酷安解析器，读取公开动态、问答、图文和站内详情概况。"""

import asyncio
import base64
import hashlib
import html
import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit

import aiohttp

from ...logger import logger

from ...constants import Config
from ...types import MediaMetadata
from ..utils import build_content_blocks, build_request_headers, join_content_text
from .base import BaseVideoParser


BASE_URL = "https://www.coolapk.com"
API_URL = "https://api.coolapk.com/v6/feed/detail"
FEED_URL_RE = re.compile(
    r"(?<![A-Za-z0-9_.:/@%?=&#-])(?:(?:https?://|//)?(?:www\.|m\.)?coolapk(?:1s)?\.com|cool(?:market|apk)://)"
    r"[^\s<>\"'`()\[\]{}，。！？；：、（）【】《》「」,;!]*",
    re.IGNORECASE,
)
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
CHINA_TIMEZONE = timezone(timedelta(hours=8))
APP_UA = "Dalvik/2.1.0 (Linux; U; Android 10) +CoolMarket/11.0-2101202"
CONTENT_HOSTS = {"coolapk.com", "www.coolapk.com", "m.coolapk.com", "coolapk1s.com", "www.coolapk1s.com"}
FEED_PATHS = {"feed", "picture", "question", "vote"}
DETAIL_ROUTES = {
    "apk": ("apk/detail", "id"), "collection": ("collection/detail", "id"),
    "dyh": ("dyh/detail", "dyhId"), "product": ("product/detail", "id"),
    "product_name": ("product/detail", "name"), "u": ("user/profile", "uid"),
    "t": ("topic/tagDetail", "tag"), "reply": ("feed/replyDetail", "id"),
}


def _target(value: str, depth: int = 0) -> Optional[Tuple[str, str, str]]:
    """识别站内内容与客户端深链，包装链接只解码、不访问目标站点。"""
    if not isinstance(value, str) or not value.strip():
        return None
    value = html.unescape(value.strip())
    if re.search(r"[\s\x00-\x1f\x7f\\]", value):
        return None
    if value.startswith("//"):
        value = "https:" + value
    elif "://" not in value:
        value = "https://" + value
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        if parts.username or parts.password or parts.port not in {None, 80, 443}:
            return None
        if parts.scheme.lower() in {"coolmarket", "coolapk"}:
            if host in CONTENT_HOSTS or host == "com.coolapk.market":
                path = parts.path
            elif host in FEED_PATHS | set(DETAIL_ROUTES) | {"game", "appba"}:
                path = "/" + host + parts.path
            else:
                return None
        elif parts.scheme.lower() in {"http", "https"} and host in CONTENT_HOSTS:
            path = parts.path
        else:
            return None
        query = parse_qs(parts.query)
    except ValueError:
        return None
    if path.rstrip("/") == "/link" and depth < 3:
        values = query.get("url", [])
        return _target(urljoin(BASE_URL + "/", values[0]), depth + 1) if len(values) == 1 else None
    if path.rstrip("/") == "/product/detail":
        for key, kind in (("id", "product"), ("name", "product_name")):
            values = query.get(key, [])
            if len(values) == 1:
                path = f"/{kind}/{quote(values[0], safe='')}"
                break
    if path.rstrip("/") == "/feed/replyDetail":
        values = query.get("id", [])
        if len(values) == 1:
            path = f"/reply/{values[0]}"
    match = re.fullmatch(r"/([A-Za-z_]+)/([^/]+)/?", path)
    if not match:
        return None
    kind, identity = match.group(1), unquote(match.group(2))
    if re.search(r"[\x00-\x1f\x7f/\\]", identity) or not identity.strip() or len(identity) > 200:
        return None
    if kind in FEED_PATHS:
        kind = "feed"
    elif kind in {"game", "appba"}:
        kind = "apk"
    if kind not in {"feed", *DETAIL_ROUTES}:
        return None
    if kind in {"feed", "reply", "collection", "dyh", "product"}:
        if not re.fullmatch(r"[1-9][0-9]{0,19}", identity):
            return None
    if kind == "apk" and not re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*", identity):
        return None
    encoded = quote(identity, safe="")
    page = f"{BASE_URL}/{kind}/{encoded}"
    if kind == "product_name":
        page = f"{BASE_URL}/product/detail?name={encoded}"
    return kind, identity, page


def _link_url(value: Any) -> str:
    """展开站内跳转包装并保留正文 HTTP 链接，不发送网络请求。"""
    if not isinstance(value, str):
        return ""
    value = html.unescape(value.strip())
    for _ in range(4):
        if re.search(r"[\x00-\x1f\x7f\\]", value):
            return ""
        value = urljoin(BASE_URL + "/", value)
        try:
            parts = urlsplit(value)
            if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
                return ""
        except ValueError:
            return ""
        if parts.hostname in CONTENT_HOSTS and parts.path.rstrip("/") == "/link":
            targets = parse_qs(parts.query).get("url", [])
            if len(targets) != 1:
                return ""
            value = targets[0]
            continue
        return value
    return ""


def _image_url(value: Any) -> str:
    """保留酷安图片 CDN 的原始地址，不改写签名或尺寸参数。"""
    if not isinstance(value, str):
        return ""
    value = html.unescape(value)
    if re.search(r"[\s\x00-\x1f\x7f\\]", value):
        return ""
    if value.startswith("//"):
        value = "https:" + value
    try:
        parts = urlsplit(value)
        if (parts.scheme not in {"http", "https"} or parts.hostname != "image.coolapk.com"
                or parts.username or parts.password or parts.port not in {None, 80, 443}):
            return ""
    except ValueError:
        return ""
    return value


def _source_id(feed: Dict[str, Any]) -> str:
    """统一转发字段中的数字编号与站内路径，仅展开动态原帖。"""
    value = feed.get("forwardid")
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        value = str(value)
        if re.fullmatch(r"[1-9][0-9]{0,19}", value):
            return value
        target = _target(urljoin(BASE_URL + "/", value)) if value else None
        if target and target[0] == "feed":
            return target[1]
    return ""


class _TextParser(HTMLParser):
    """清理正文富文本，保留换行、表情替代文字和可见链接。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.hidden: List[str] = []
        self.links: List[Tuple[str, int]] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        """读取可见文字的段落与表情标记。"""
        if tag in {"script", "style"}:
            self.hidden.append(tag)
        if self.hidden:
            return
        if tag in {"br", "p", "div", "li", "blockquote"}:
            self.parts.append("\n")
        if tag == "img":
            self.parts.append(dict(attrs).get("alt") or "[图片]")
        if tag == "a":
            values = dict(attrs)
            # 话题与 @ 用户保留可见名称，其余链接保留目标地址。
            classes = (values.get("class") or "").split()
            href = "" if any(name in classes for name in {"feed-link-tag", "feed-link-uname"}) else _link_url(values.get("href"))
            self.links.append((href, len(self.parts)))

    def handle_endtag(self, tag: str) -> None:
        """恢复隐藏状态并结束段落。"""
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
            return
        if tag in {"p", "div", "li", "blockquote"}:
            self.parts.append("\n")
        if tag == "a" and self.links:
            href, start = self.links.pop()
            if href and href != "".join(self.parts[start:]).strip():
                self.parts.append(f"（{href}）")

    def handle_data(self, data: str) -> None:
        """收集正文，忽略脚本与样式。"""
        if not self.hidden:
            self.parts.append(data)


def _clean_text(value: str) -> str:
    """移除 HTML 标签及多余空行，保留正文原有文字。"""
    parser = _TextParser()
    parser.feed(re.sub(r"<(https?://[^<>\s]+)>", r"\1", value))
    parser.close()
    return "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())


def _timestamp(value: Any) -> str:
    """将接口 Unix 秒数转换为北京时间。"""
    try:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            return ""
        return datetime.fromtimestamp(int(value), CHINA_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return ""


class CoolapkParser(BaseVideoParser):
    """解析酷安公开内容与站内详情，不请求登录态或外部解析服务。"""

    def __init__(self, hot_comment_count: int = 0) -> None:
        """初始化并发控制与热评数量。

        Args:
            hot_comment_count: 详情首屏热评的输出上限，零表示不输出。
        """
        super().__init__("coolapk")
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)
        try:
            self.hot_comment_count = max(0, int(hot_comment_count))
        except (TypeError, ValueError, OverflowError):
            self.hot_comment_count = 0

    def can_parse(self, url: str) -> bool:
        """判断是否为已接入的酷安内容链接。

        Args:
            url: 待判断的链接。

        Returns:
            是否具有可信域名或客户端协议及可识别的内容身份。
        """
        return _target(url) is not None

    def extract_links(self, text: str) -> List[str]:
        """从消息中提取并按内容类型和身份去重。

        Args:
            text: 含分享链接的消息。

        Returns:
            规范化后的内容链接。
        """
        return [url for _, url in self.extract_links_with_positions(text)]

    def extract_links_with_positions(self, text: str) -> List[Tuple[int, str]]:
        """规范化分享链接，同时保留首次出现的原文位置。

        Args:
            text: 含分享链接的消息。

        Returns:
            原文位置与规范链接列表。
        """
        links = []
        seen = set()
        for match in FEED_URL_RE.finditer(text or ""):
            target = _target(match.group().rstrip(".,!;:"))
            if target and target[:2] not in seen:
                seen.add(target[:2])
                links.append((match.start(), target[2]))
        return links

    @staticmethod
    def _api_headers() -> Dict[str, str]:
        """为每次请求生成匿名客户端令牌，令牌不用于媒体下载。"""
        timestamp = int(time.time())
        device = str(uuid.uuid4())
        digest = hashlib.md5(str(timestamp).encode()).hexdigest()
        raw = f"token://com.coolapk.market/c67ef5943784d09750dcfbb31020f0ab?{digest}${device}&com.coolapk.market"
        token = hashlib.md5(base64.b64encode(raw.encode())).hexdigest() + device + hex(timestamp)
        return {
            "User-Agent": APP_UA,
            "X-Requested-With": "XMLHttpRequest",
            "X-App-Id": "com.coolapk.market",
            "X-App-Token": token,
            "X-App-Version": "11.0",
            "X-App-Code": "2101202",
            "X-Api-Version": "11",
            "X-Sdk-Int": "29",
            "X-Sdk-Locale": "zh-CN",
        }

    async def _fetch_feed(self, session: aiohttp.ClientSession, identity: str) -> Dict[str, Any]:
        """限量读取详情，并核对响应身份。"""
        return await self._fetch_detail(session, "feed", identity)

    async def _fetch_detail(self, session: aiohttp.ClientSession, kind: str, identity: str) -> Dict[str, Any]:
        """按内容类型选择固定接口，校验返回的编号、名称或包名。"""
        endpoint, parameter = ("feed/detail", "id") if kind == "feed" else DETAIL_ROUTES[kind]
        async with session.get(
            API_URL if kind == "feed" else "https://api.coolapk.com/v6/" + endpoint,
            params={parameter: identity}, headers=self._api_headers(),
            timeout=aiohttp.ClientTimeout(total=20), allow_redirects=False,
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"酷安详情接口返回 HTTP {response.status}")
            body = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise RuntimeError("酷安详情响应超过大小上限")
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise RuntimeError("酷安详情接口未返回有效 JSON") from exc
        feed = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(feed, dict):
            raise RuntimeError("酷安内容不可访问，可能已删除、需要登录或受到访问限制")
        identity_fields = {
            "apk": ("id", "apkname"), "u": ("uid", "username"),
            "t": ("title",), "product_name": ("title", "alias_title", "index_title", "short_title"),
        }.get(kind, ("id",))
        if not any(str(feed.get(key)) == identity for key in identity_fields):
            raise RuntimeError("酷安详情接口返回了其他内容的数据")
        record_id = feed.get("uid") if kind == "u" else feed.get("id")
        if not re.fullmatch(r"[1-9][0-9]{0,19}", str(record_id)):
            raise RuntimeError("酷安详情接口缺少有效内容编号")
        return feed

    @staticmethod
    def _content(feed: Dict[str, Any], include_source: bool = True) -> Tuple[str, List[List[str]], List[Dict[str, Any]]]:
        """优先按长文结构化块读取正文，其余动态保留正文与附件原图。"""
        images: List[List[str]] = []
        parts: List[Union[str, int]] = []

        def add_image(value: Any, inline: bool = False) -> None:
            image = _image_url(value)
            if image:
                if [image] not in images:
                    images.append([image])
                if inline:
                    parts.append(images.index([image]))

        raw = feed.get("message_raw_output")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = None
        structured = (isinstance(raw, list) and bool(raw)
                      and all(isinstance(item, dict) and item.get("type") in {"text", "image", "shareUrl"}
                              for item in raw))
        if structured:
            for item in raw:
                kind = item["type"]
                if kind == "image":
                    add_image(item.get("url") or item.get("uri"), inline=True)
                    caption = item.get("description")
                    if isinstance(caption, str) and caption.strip():
                        parts.append("\n" + caption + "\n")
                elif kind == "text":
                    message = item.get("message")
                    if isinstance(message, str):
                        parts.append(message + "\n")
                else:
                    title, link = item.get("title"), item.get("url")
                    if isinstance(title, str):
                        parts.append(title + "\n")
                    link = _link_url(link)
                    if link:
                        parts.append(link + "\n")
        else:
            message = feed.get("message")
            if isinstance(message, str):
                parts.append(message)
        attachments = feed.get("picArr")
        if isinstance(attachments, list):
            for image in attachments:
                add_image(image)
        # 问答、投票、点评、视频分享及清单的正文并不只在 message 字段中。
        feed_type = feed.get("feedType")
        if feed_type == "answer":
            question_id = str(feed.get("fid") or "")
            if re.fullmatch(r"[1-9][0-9]{0,19}", question_id):
                parts.append(f"\n问题：{BASE_URL}/feed/{question_id}\n")
        vote = feed.get("vote")
        if isinstance(vote, dict):
            vote_title = vote.get("message_title")
            if not feed.get("message_title") and isinstance(vote_title, str) and vote_title.strip():
                parts.append("\n投票：" + vote_title + "\n")
            options = vote.get("options")
            selected = str(feed.get("extra_key") or "").split(",") if feed_type == "voteComment" or str(feed.get("type")) == "17" else []
            if isinstance(options, list):
                parts.append("\n投票选项：\n")
                for option in options:
                    if not isinstance(option, dict) or not isinstance(option.get("title"), str):
                        continue
                    label = option["title"]
                    count = option.get("total_select_num")
                    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                        label += f"（{count} 票）"
                    if str(option.get("id")) in selected:
                        label += "（已选）"
                    parts.append(label + "\n")
        if feed_type == "rating" and isinstance(feed.get("star"), (int, float)):
            parts.append(f"\n评分：{feed['star']}\n")
        media_url = _link_url(feed.get("media_url")) if feed.get("media_url") else ""
        if media_url and media_url not in _clean_text(join_content_text(parts)):
            parts.append(f"\n媒体来源：{media_url}\n")
        album = feed.get("productAlbumDetailInfo")
        if isinstance(album, list):
            for item in album:
                if not isinstance(item, dict):
                    continue
                name, description = item.get("item_name"), item.get("item_description")
                # 等级字段有整数与字符串两种返回形式。
                rank = {"1": "夯", "2": "顶级", "3": "人上人", "4": "NPC", "5": "拉"}.get(str(item.get("level")))
                if rank:
                    parts.append(f"\n分级：{rank}\n")
                if isinstance(name, str):
                    parts.append("\n" + name + "\n")
                if isinstance(description, str):
                    parts.append(description + "\n")
                item_images = item.get("item_images")
                if isinstance(item_images, str):
                    for image in item_images.split(","):
                        add_image(image, inline=True)
        source = feed.get("sourceFeed")
        forward_id = _source_id(feed)
        if include_source and isinstance(source, dict) and str(source.get("id")) == forward_id:
            source_desc, source_images, source_blocks = CoolapkParser._content(source, include_source=False)
            source_author = source.get("username") or "未知作者"
            parts.append(f"\n转发自 {source_author}：{BASE_URL}/feed/{forward_id}\n")
            if source.get("issummary") in (1, "1"):
                parts.append("原帖摘要：\n")
            if isinstance(source.get("message_title"), str) and source["message_title"]:
                parts.append(source["message_title"] + "\n")
            indexes = {}
            for index, candidates in enumerate(source_images):
                add_image(candidates[0])
                indexes[index] = images.index(candidates)
            if source_blocks:
                for block in source_blocks:
                    parts.append(block["text"] + "\n" if block["type"] == "text" else indexes[block["index"]])
            elif source_desc:
                parts.append(source_desc + "\n")
        elif include_source and re.fullmatch(r"[1-9][0-9]{0,19}", forward_id):
            parts.append(f"\n原帖不可读取：{BASE_URL}/feed/{forward_id}\n")
        elif include_source and feed.get("forwardid"):
            forward_url = _link_url(str(feed["forwardid"]))
            if forward_url:
                parts.append(f"\n转发来源：{forward_url}\n")
        desc = _clean_text(join_content_text(parts))
        return desc, images, build_content_blocks(parts, _clean_text)

    @staticmethod
    def _overview(kind: str, data: Dict[str, Any], page_url: str) -> MediaMetadata:
        """按详情类型输出应用、收藏单、用户、话题、看看号及产品概况。"""
        if kind == "product_name":
            kind = "product"
        fields = {
            "apk": (("introduce", "简介"), ("apkversionname", "版本"), ("developername", "开发者"),
                    ("apksize", "大小"), ("rating_star", "评分"), ("changelog", "更新说明")),
            "collection": (("description", "简介"), ("item_num", "收藏数量")),
            "dyh": (("description", "简介"), ("author", "作者"), ("follownum", "关注人数")),
            "product": (("description", "简介"), ("search_keywords", "产品信息"),
                        ("release_time", "发布时间"), ("price_min", "起售价"),
                        ("rating_average_score", "评分"), ("rating_total_num", "评分人数")),
            "u": (("bio", "简介"), ("verify_title", "认证"), ("feed", "动态数量"), ("fans", "粉丝数量")),
            "t": (("description", "简介"), ("intro", "介绍"), ("keywords", "关键词"),
                  ("follownum", "关注人数"), ("commentnum", "动态数量")),
        }[kind]
        title = data.get("username") if kind == "u" else data.get("title")
        lines = []
        for key, label in fields:
            value = data.get(key)
            if isinstance(value, (str, int, float)) and not isinstance(value, bool) and str(value).strip():
                lines.append(f"{label}：{value}")
        image_values = []
        if kind == "apk" and isinstance(data.get("screenshots"), str):
            image_values = data["screenshots"].split(",")
        elif kind in {"dyh", "product", "product_name", "t", "collection"}:
            image_values = [data.get("cover_pic") if kind == "collection" else data.get("logo")]
        images = list(dict.fromkeys(image for value in image_values if (image := _image_url(value))))
        return {
            "url": page_url, "platform": "coolapk", "title": _clean_text(title) if isinstance(title, str) else "",
            "author": str(data.get("username") or data.get("developername") or data.get("author") or ""),
            "desc": _clean_text("\n".join(lines)), "image_urls": [[image] for image in images], "video_urls": [],
            "timestamp": _timestamp(data.get("lastupdate") or data.get("dateline") or data.get("update_time")),
            "image_headers": build_request_headers(is_video=False, referer=page_url),
        }

    def _hot_comments(self, feed: Dict[str, Any], identity: str) -> List[Dict[str, Any]]:
        """仅输出详情首屏热评，不递归展开楼中楼。"""
        rows = feed.get("hotReplyRows")
        if not self.hot_comment_count or not isinstance(rows, list):
            return []
        comments = []
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or str(row.get("fid")) != identity:
                continue
            comment_id = str(row.get("id") or "")
            message = row.get("message")
            if not comment_id or comment_id in seen or not isinstance(message, str):
                continue
            message = _clean_text(message)
            if not message:
                continue
            seen.add(comment_id)
            comment = {
                "id": comment_id, "username": str(row.get("username") or ""),
                "uid": str(row.get("uid") or ""), "message": message,
                "time": _timestamp(row.get("dateline")),
            }
            likes = row.get("likenum")
            if isinstance(likes, int) and not isinstance(likes, bool) and likes >= 0:
                comment["likes"] = likes
            comments.append(comment)
            if len(comments) >= self.hot_comment_count:
                break
        return comments

    async def parse(self, session: aiohttp.ClientSession, url: str) -> Optional[MediaMetadata]:
        """按链接内容类型读取正文、原图、热评或站内详情概况。

        Args:
            session: 共享 HTTP 会话。
            url: 酷安内容分享链接或客户端深链。

        Returns:
            内容元数据，未支持的链接返回空。
        """
        target = _target(url)
        if not target:
            return None
        kind, identity, page_url = target
        async with self.semaphore:
            feed = (await self._fetch_feed(session, identity) if kind == "feed"
                    else await self._fetch_detail(session, kind, identity))
            if kind not in {"feed", "reply"}:
                if kind == "product_name":
                    page_url = f"{BASE_URL}/product/{feed['id']}"
                elif kind == "apk" and isinstance(feed.get("apkname"), str):
                    page_url = f"{BASE_URL}/apk/{quote(feed['apkname'], safe='')}"
                elif kind == "u":
                    page_url = f"{BASE_URL}/u/{feed['uid']}"
                return self._overview(kind, feed, page_url)
            source = feed.get("sourceFeed")
            forward_id = _source_id(feed)
            if (kind == "feed" and re.fullmatch(r"[1-9][0-9]{0,19}", forward_id)
                    and (not isinstance(source, dict) or str(source.get("id")) != forward_id
                         or source.get("issummary") in (1, "1"))):
                try:
                    source = await self._fetch_feed(session, forward_id)
                except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as exc:
                    logger.warning(f"[coolapk] 转发原帖读取失败，保留当前动态：{exc}")
                else:
                    feed = dict(feed, sourceFeed=source)
            desc, images, blocks = self._content(feed)
            title = feed.get("message_title")
            title = _clean_text(title) if isinstance(title, str) else ""
            if not title and not desc and not images:
                raise RuntimeError("酷安动态未返回可用正文或图片")
            metadata: MediaMetadata = {
                "url": page_url, "platform": self.name,
                "title": title, "author": str(feed.get("username") or ""),
                "desc": desc, "timestamp": _timestamp(feed.get("dateline")),
                "image_urls": images, "video_urls": [],
                "image_headers": build_request_headers(is_video=False, referer=page_url),
            }
            if blocks:
                metadata["content_blocks"] = blocks
            comments = self._hot_comments(feed, identity)
            if comments:
                metadata["hot_comments"] = comments
            return metadata
