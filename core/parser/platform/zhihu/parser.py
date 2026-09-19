"""知乎问答与专栏文章解析器，使用匿名网页接口获取正文。"""

import asyncio
import html
import re
import time
from datetime import datetime
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp

from ....constants import Config
from ....types import MediaMetadata
from ...utils import SkipParse
from ..base import BaseVideoParser
from .sign import sign_article


ZHIHU_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)
ZHIHU_HOSTS = frozenset({"zhihu.com", "www.zhihu.com"})
ZHUANLAN_HOST = "zhuanlan.zhihu.com"
ZHIHU_MAX_CONCURRENT = 2
GUEST_COOKIE_DEFAULT_TTL = 600.0
ZHIHU_RETRY_DELAY = 1.0
URL_TRAILING_PUNCTUATION = ".,!?)]}>\"'，。！？；：）】》」"
HTTP_URL_RE = re.compile(
    r"https?://[^\s<>\"'()，。！？；：、（）【】《》「」]+",
    re.IGNORECASE,
)
ANSWER_PATH_RE = re.compile(r"^/question/([0-9]+)/answer/([0-9]+)/?$")
ARTICLE_PATH_RE = re.compile(r"^/p/([0-9]+)/?$")


class _RichContentParser(HTMLParser):
    """从知乎正文 HTML 中提取可见文本和图片地址。"""

    _BLOCK_TAGS = frozenset(
        {
            "p", "div", "section", "article", "li", "blockquote", "pre", "tr",
            "h1", "h2", "h3", "h4", "h5", "h6",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: List[str] = []
        self.image_urls: List[List[str]] = []
        self._image_seen = set()
        self._ignored_tags: List[str] = []

    def handle_starttag(
        self, tag: str, attrs: List[Tuple[str, Optional[str]]]
    ) -> None:
        """处理正文标签、换行标签和图片标签。"""
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "template"}:
            self._ignored_tags.append(tag)
        if self._ignored_tags:
            return
        values = dict(attrs)
        if tag == "br":
            self.text_parts.append("\n")
        elif tag == "img":
            candidates = (
                values.get("data-original"),
                values.get("data-actualsrc"),
                values.get("data-src"),
                values.get("src"),
            )
            image_url = ""
            for candidate in candidates:
                image_url = self._normalize_media_url(candidate)
                if image_url:
                    break
            if image_url and image_url not in self._image_seen:
                self._image_seen.add(image_url)
                self.image_urls.append([image_url])
            elif not image_url and values.get("alt"):
                self.text_parts.append(values["alt"])
        if tag in self._BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        """处理忽略标签和块级标签结束。"""
        tag = tag.lower()
        if self._ignored_tags:
            if tag == self._ignored_tags[-1]:
                self._ignored_tags.pop()
            return
        if tag in self._BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        """保存正文中的可见文字。"""
        if not self._ignored_tags:
            self.text_parts.append(data)

    @staticmethod
    def _normalize_media_url(value: Any) -> str:
        """校验并规范化正文图片地址。"""
        if not isinstance(value, str):
            return ""
        url = html.unescape(value).strip()
        if url.startswith("//"):
            url = "https:" + url
        try:
            parsed = urlparse(url)
            if (
                parsed.scheme.lower() not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.port == 0
            ):
                return ""
        except ValueError:
            return ""
        return url

    def text(self) -> str:
        """返回经过空白清理的正文文本。"""
        text = (
            "".join(self.text_parts)
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\xa0", " ")
        )
        lines = [
            re.sub(r"[ \t\f\v]+", " ", line).strip()
            for line in text.split("\n")
        ]
        compact: List[str] = []
        previous_blank = False
        for line in lines:
            if line:
                compact.append(line)
                previous_blank = False
            elif compact and not previous_blank:
                compact.append("")
                previous_blank = True
        return "\n".join(compact).strip()


def _format_timestamp(value: Any) -> str:
    """将知乎 Unix 时间戳格式化为本地可读时间。"""
    try:
        timestamp = int(value)
        if timestamp > 10**12:
            timestamp //= 1000
    except (TypeError, ValueError):
        return ""
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return ""


def _error_message(payload: Any, status: int) -> str:
    """从知乎错误 JSON 中提取不含敏感信息的中文错误。"""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "").strip()
            code = error.get("code")
            if message and code is not None:
                return f"知乎接口请求失败（HTTP {status}，错误码 {code}）：{message}"
            if message:
                return f"知乎接口请求失败（HTTP {status}）：{message}"
    return f"知乎接口请求失败（HTTP {status}）"


class ZhihuParser(BaseVideoParser):
    """解析知乎指定回答和专栏文章。"""

    ANSWER_API = "https://api.zhihu.com/v4/answers/{answer_id}?include=content,author,question"
    ARTICLE_API = "https://zhuanlan.zhihu.com/api/articles/{article_id}?ws_qiangzhisafe=0"
    EXPLORE_URL = "https://www.zhihu.com/explore"

    def __init__(self) -> None:
        """初始化知乎解析器和匿名访客会话状态。"""
        super().__init__("zhihu")
        self.semaphore = asyncio.Semaphore(
            max(1, min(Config.PARSER_MAX_CONCURRENT, ZHIHU_MAX_CONCURRENT))
        )
        self._guest_lock = asyncio.Lock()
        self._guest_dc0 = ""
        self._guest_expires_at = 0.0
        self._headers = {
            "User-Agent": ZHIHU_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    # ── 链接识别 ──────────────────────────────────────────

    @staticmethod
    def _identify_url(url: str) -> Optional[Tuple[str, str, str]]:
        """识别严格的知乎回答或专栏文章链接。"""
        if not isinstance(url, str) or not url.strip():
            return None
        try:
            parsed = urlparse(html.unescape(url.strip()))
            if parsed.scheme.lower() not in {"http", "https"}:
                return None
            if (
                parsed.username
                or parsed.password
                or parsed.port not in {None, 80, 443}
            ):
                return None
        except (TypeError, ValueError):
            return None
        host = (parsed.hostname or "").lower().strip(".")
        answer_match = ANSWER_PATH_RE.fullmatch(parsed.path or "")
        if host in ZHIHU_HOSTS and answer_match:
            return "answer", answer_match.group(1), answer_match.group(2)
        article_match = ARTICLE_PATH_RE.fullmatch(parsed.path or "")
        if host == ZHUANLAN_HOST and article_match:
            return "article", "", article_match.group(1)
        return None

    @classmethod
    def _canonical_url(cls, identity: Tuple[str, str, str]) -> str:
        """生成不带追踪参数的规范链接。"""
        kind, question_id, content_id = identity
        if kind == "answer":
            return f"https://www.zhihu.com/question/{question_id}/answer/{content_id}"
        return f"https://zhuanlan.zhihu.com/p/{content_id}"

    def can_parse(self, url: str) -> bool:
        """判断是否可以解析此知乎回答或专栏文章链接。"""
        return self._identify_url(url) is not None

    def extract_links(self, text: str) -> List[str]:
        """从文本中提取知乎回答和专栏文章链接并去重。"""
        links: List[str] = []
        seen = set()
        for match in HTTP_URL_RE.finditer(text or ""):
            link = html.unescape(match.group(0).rstrip(URL_TRAILING_PUNCTUATION))
            identity = self._identify_url(link)
            if identity is None:
                continue
            canonical = self._canonical_url(identity)
            if canonical in seen:
                continue
            seen.add(canonical)
            links.append(canonical)
        return links

    # ── 匿名请求 ──────────────────────────────────────────

    async def _get_guest_dc0(self, session: aiohttp.ClientSession) -> str:
        """获取并缓存本解析器实例的匿名访客 d_c0。"""
        if self._guest_dc0 and time.monotonic() < self._guest_expires_at:
            return self._guest_dc0
        async with self._guest_lock:
            if self._guest_dc0 and time.monotonic() < self._guest_expires_at:
                return self._guest_dc0
            async with session.get(
                self.EXPLORE_URL,
                headers={**self._headers, "Cookie": ""},
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                await response.read()
                if response.status != 200:
                    raise RuntimeError(f"知乎匿名访客初始化失败（HTTP {response.status}）")
                for cookie_line in response.headers.getall("Set-Cookie", []):
                    if not cookie_line.startswith("d_c0="):
                        continue
                    value = cookie_line.split(";", 1)[0][5:].split("|", 1)[0].rstrip("=")
                    if value:
                        self._guest_dc0 = value
                        max_age = None
                        for attribute in cookie_line.split(";")[1:]:
                            name, separator, raw_value = attribute.strip().partition("=")
                            if name.lower() == "max-age" and separator:
                                try:
                                    max_age = max(60.0, float(raw_value))
                                except ValueError:
                                    max_age = None
                                break
                        self._guest_expires_at = time.monotonic() + min(
                            max_age if max_age is not None else GUEST_COOKIE_DEFAULT_TTL,
                            1800.0,
                        )
                        return value
            raise RuntimeError("知乎匿名访客初始化失败：未取得 d_c0")

    async def _invalidate_guest_dc0(self, dc0: str) -> None:
        """仅在缓存仍是指定访客值时使其失效。"""
        async with self._guest_lock:
            if self._guest_dc0 == dc0:
                self._guest_dc0 = ""
                self._guest_expires_at = 0.0

    async def _get_json(
        self,
        session: aiohttp.ClientSession,
        url: str,
        headers: Dict[str, str],
    ) -> Dict[str, Any]:
        """请求知乎 JSON 接口并统一处理状态码。"""
        async with session.get(
            url,
            headers=headers,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            try:
                payload = await response.json(content_type=None)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"知乎接口返回了无效 JSON（HTTP {response.status}）"
                ) from exc
            if not isinstance(payload, dict):
                raise RuntimeError("知乎接口返回的 JSON 不是对象")
            if response.status != 200:
                raise RuntimeError(_error_message(payload, response.status))
            return payload

    # ── 正文与元数据 ──────────────────────────────────────

    @staticmethod
    def _parse_content(content: str) -> Tuple[str, List[List[str]]]:
        """从正文 HTML 中提取文本和图片。"""
        parser = _RichContentParser()
        parser.feed(content)
        parser.close()
        return parser.text(), parser.image_urls

    @staticmethod
    def _metadata_from_payload(
        canonical_url: str,
        payload: Dict[str, Any],
        content: str,
        strict_full_content: bool,
    ) -> MediaMetadata:
        """校验知乎实体并转换为统一媒体元数据。"""
        actual_id = str(payload.get("id") or "")
        expected_id = canonical_url.rstrip("/").rsplit("/", 1)[-1]
        if actual_id != expected_id:
            raise RuntimeError("知乎接口返回的内容 ID 与链接不匹配")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("知乎接口返回的正文为空")
        if strict_full_content and (
            payload.get("content_need_truncated")
            or payload.get("force_login_when_click_read_more")
        ):
            raise RuntimeError("知乎文章正文需要登录或被截断")
        desc, image_urls = ZhihuParser._parse_content(content)
        author = payload.get("author") or {}
        author_name = author.get("name") if isinstance(author, dict) else str(author)
        return {
            "url": canonical_url,
            "title": str(payload.get("title") or "").strip(),
            "author": str(author_name or "").strip(),
            "desc": desc,
            "timestamp": _format_timestamp(
                payload.get("created") or payload.get("createdTime")
            ),
            "platform": "zhihu",
            "video_urls": [],
            "image_urls": image_urls,
            "image_headers": {
                "User-Agent": ZHIHU_USER_AGENT,
                "Referer": canonical_url,
            },
        }

    async def _parse_answer(
        self,
        session: aiohttp.ClientSession,
        canonical_url: str,
        question_id: str,
        answer_id: str,
    ) -> MediaMetadata:
        """请求并解析知乎回答接口。"""
        api_url = self.ANSWER_API.format(answer_id=answer_id)
        headers = {
            **self._headers,
            "Referer": canonical_url,
            "Cookie": "",
        }
        payload = await self._get_json(session, api_url, headers)
        if str(payload.get("id") or "") != answer_id:
            raise RuntimeError("知乎接口返回的回答 ID 与链接不匹配")
        question = payload.get("question") or {}
        if not isinstance(question, dict):
            raise RuntimeError("知乎接口返回的问题信息格式无效")
        if str(question.get("id") or "") != question_id:
            raise RuntimeError("知乎接口返回的问题 ID 与链接不匹配")
        content = payload.get("content")
        result = self._metadata_from_payload(canonical_url, payload, content, False)
        result["title"] = str(question.get("title") or "").strip()
        return result

    async def _parse_article(
        self,
        session: aiohttp.ClientSession,
        canonical_url: str,
        article_id: str,
    ) -> MediaMetadata:
        """获取匿名访客签名并解析知乎专栏文章接口。"""
        api_path = f"/api/articles/{article_id}?ws_qiangzhisafe=0"
        api_url = self.ARTICLE_API.format(article_id=article_id)
        payload: Dict[str, Any]
        for attempt in range(2):
            dc0 = await self._get_guest_dc0(session)
            headers = {
                **self._headers,
                "Accept": "application/json, text/plain, */*",
                "Referer": canonical_url,
                "x-api-version": "3.0.91",
                "x-app-za": "OS=Web",
                "x-requested-with": "fetch",
                "x-zse-93": "101_3_3.0",
                "x-zse-96": sign_article(api_path, dc0),
                "Cookie": f"d_c0={dc0}",
            }
            try:
                payload = await self._get_json(session, api_url, headers)
            except RuntimeError as exc:
                if attempt == 0 and re.search(r"HTTP (?:403|429)\b", str(exc)):
                    await self._invalidate_guest_dc0(dc0)
                    await asyncio.sleep(ZHIHU_RETRY_DELAY)
                    continue
                raise
            break
        else:
            raise RuntimeError("知乎文章接口请求失败")
        if payload.get("type") not in (None, "article"):
            raise RuntimeError("知乎接口返回的实体类型不是文章")
        return self._metadata_from_payload(
            canonical_url, payload, payload.get("content"), True
        )

    async def parse(
        self,
        session: aiohttp.ClientSession,
        url: str,
    ) -> Optional[MediaMetadata]:
        """解析知乎回答或专栏文章链接。"""
        identity = self._identify_url(url)
        if identity is None:
            raise SkipParse("不是受支持的知乎回答或专栏文章链接")
        canonical_url = self._canonical_url(identity)
        async with self.semaphore:
            # 知乎匿名请求必须与其他平台的登录 Cookie 隔离，避免污染共享会话。
            async with aiohttp.ClientSession(
                cookie_jar=aiohttp.DummyCookieJar(),
                trust_env=False,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as isolated_session:
                if identity[0] == "answer":
                    return await self._parse_answer(
                        isolated_session,
                        canonical_url,
                        identity[1],
                        identity[2],
                    )
                return await self._parse_article(
                    isolated_session,
                    canonical_url,
                    identity[2],
                )
