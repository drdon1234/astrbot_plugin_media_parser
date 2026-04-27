"""Shared helpers for Douyin and TikTok parsers."""
import html as html_lib
import json
import re
from datetime import datetime
from typing import Any, List, Optional
from urllib.parse import urlparse, urlunparse


URL_TRAILING_PUNCTUATION = ".,!?)]}>\"'，。！？；：）】》」"
HTTP_URL_RE = re.compile(r"https?://[^\s<>\"']+")


class ShortVideoParserMixin:
    """Small shared URL, HTML and JSON helpers for short-video parsers."""

    @staticmethod
    def _host_matches(host: str, *suffixes: str) -> bool:
        if not host:
            return False
        normalized = host.lower().strip(".")
        return any(
            normalized == suffix or normalized.endswith(f".{suffix}")
            for suffix in suffixes
        )

    @classmethod
    def _get_host(cls, url: str) -> str:
        try:
            return (urlparse(url).hostname or "").lower().strip(".")
        except Exception:
            return ""

    @staticmethod
    def _clean_extracted_url(url: str) -> str:
        if not url:
            return ""
        return url.rstrip(URL_TRAILING_PUNCTUATION)

    @staticmethod
    def _strip_query_and_fragment(url: str) -> str:
        if not url:
            return url
        parsed = urlparse(url)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))

    @staticmethod
    def _format_timestamp(timestamp_value: Any) -> str:
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
    def _extend_unique_urls(target: List[str], candidates: List[str]) -> None:
        for url in candidates:
            if url and url not in target:
                target.append(url)

    @staticmethod
    def _decode_json_string(value: str) -> str:
        if not value:
            return ""
        try:
            return json.loads(f'"{value}"')
        except Exception:
            return value.replace("\\u002F", "/").replace("\\/", "/")

    @classmethod
    def _extract_nested_http_urls(
        cls,
        value: Any,
        depth: int = 0,
        max_depth: int = 4
    ) -> List[str]:
        if depth > max_depth or value is None:
            return []

        if isinstance(value, str):
            decoded = cls._decode_json_string(value)
            if decoded.startswith(("http://", "https://")):
                return [cls._clean_extracted_url(decoded)]
            return [
                cls._clean_extracted_url(url)
                for url in HTTP_URL_RE.findall(decoded)
            ]

        urls: List[str] = []
        if isinstance(value, list):
            for item in value:
                cls._extend_unique_urls(
                    urls,
                    cls._extract_nested_http_urls(
                        item,
                        depth=depth + 1,
                        max_depth=max_depth
                    )
                )
            return urls

        if isinstance(value, dict):
            preferred_keys = (
                "urlList",
                "url_list",
                "UrlList",
                "urls",
                "url",
                "Url",
                "playAddr",
                "downloadAddr",
                "PlayAddr",
                "PlayAddrStruct",
                "imageURL",
                "imageUrl",
                "displayImage",
                "originImage",
                "downloadImage",
                "ownerWatermarkImage",
                "ownerWatermarkUrl",
                "image",
                "cover",
            )
            for key in preferred_keys:
                if key in value:
                    cls._extend_unique_urls(
                        urls,
                        cls._extract_nested_http_urls(
                            value.get(key),
                            depth=depth + 1,
                            max_depth=max_depth
                        )
                    )
            return urls

        return []

    @staticmethod
    def extract_router_data(text: str) -> Optional[str]:
        """Extract `window._ROUTER_DATA` JSON from HTML."""
        start_flag = "window._ROUTER_DATA = "
        start_idx = text.find(start_flag)
        if start_idx == -1:
            return None
        brace_start = text.find("{", start_idx)
        if brace_start == -1:
            return None

        index = brace_start
        stack = []
        while index < len(text):
            if text[index] == "{":
                stack.append("{")
            elif text[index] == "}":
                stack.pop()
                if not stack:
                    return text[brace_start:index + 1]
            index += 1
        return None

    @staticmethod
    def extract_script_json(text: str, script_id: str) -> Optional[str]:
        """Extract JSON content from a script tag by id."""
        pattern = (
            rf"<script[^>]+id=[\"']{re.escape(script_id)}[\"'][^>]*>"
            rf"(.*?)</script>"
        )
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if not match:
            return None
        return html_lib.unescape(match.group(1).strip())

    @classmethod
    def _walk_dicts(cls, obj: Any):
        """Depth-first walk over dict/list values."""
        if isinstance(obj, dict):
            yield obj
            for value in obj.values():
                yield from cls._walk_dicts(value)
        elif isinstance(obj, list):
            for value in obj:
                yield from cls._walk_dicts(value)
