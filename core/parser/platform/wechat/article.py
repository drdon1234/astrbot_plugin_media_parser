"""公众号文章页面提取，读取服务端返回的正文、图集和元数据。"""

import re
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from ....types import MediaMetadata


VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}
BLOCK_TAGS = {
    "article", "blockquote", "br", "div", "figcaption", "figure", "h1",
    "h2", "h3", "h4", "h5", "h6", "hr", "li", "ol", "p", "section",
    "table", "tr", "ul",
}
IGNORED_TAGS = {"script", "style", "noscript", "template"}
FIELD_IDS = {
    "activity-name": "title",
    "js_name": "account",
    "js_content": "content",
    "publish_time": "timestamp",
}
CHINA_TIMEZONE = timezone(timedelta(hours=8))
JS_BRACKET_PAIRS = {"{": "}", "[": "]", "(": ")"}
JS_SIMPLE_ESCAPES = {
    "\\": "\\",
    "'": "'",
    '"': '"',
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
}


def _clean_text(text: str) -> str:
    """整理段落空白，保留正文中的换行。"""
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


# ── 图集脚本数据提取 ──────────────────────────


def _append_js_escape(source: str, index: int, value: List[str]) -> int:
    """解码反斜杠起始的单个 JavaScript 转义。"""
    if index + 1 >= len(source):
        value.append("\\")
        return len(source)
    escaped = source[index + 1]
    if escaped in JS_SIMPLE_ESCAPES:
        value.append(JS_SIMPLE_ESCAPES[escaped])
        return index + 2
    hex_digits = source[index + 2:index + 4]
    if escaped == "x" and re.fullmatch(r"[0-9A-Fa-f]{2}", hex_digits):
        value.append(chr(int(hex_digits, 16)))
        return index + 4
    unicode_digits = source[index + 2:index + 6]
    if escaped == "u" and re.fullmatch(r"[0-9A-Fa-f]{4}", unicode_digits):
        codepoint = int(unicode_digits, 16)
        next_escape = source[index + 6:index + 8]
        next_digits = source[index + 8:index + 12]
        if (
            0xD800 <= codepoint <= 0xDBFF
            and next_escape == "\\u"
            and re.fullmatch(r"[0-9A-Fa-f]{4}", next_digits)
        ):
            low = int(next_digits, 16)
            if 0xDC00 <= low <= 0xDFFF:
                codepoint = (
                    0x10000 + ((codepoint - 0xD800) << 10) + low - 0xDC00
                )
                value.append(chr(codepoint))
                return index + 12
        value.append(chr(codepoint))
        return index + 6
    if escaped == "\r":
        return index + 3 if source[index + 2:index + 3] == "\n" else index + 2
    if escaped == "\n":
        return index + 2
    value.append(escaped)
    return index + 2


def _read_js_string(source: str, start: int) -> Tuple[Optional[str], int]:
    """读取一个 JavaScript 字符串，并有限解码常见转义。"""
    if start >= len(source) or source[start] not in {"'", '"', "`"}:
        return None, start
    quote = source[start]
    value: List[str] = []
    index = start + 1
    while index < len(source):
        character = source[index]
        if character == quote:
            return "".join(value), index + 1
        if character != "\\":
            value.append(character)
            index += 1
            continue
        if index + 1 >= len(source):
            return None, len(source)
        index = _append_js_escape(source, index, value)
    return None, len(source)


def _decode_js_escapes(source: str) -> str:
    """有限解码不带外围引号的 JavaScript 转义文本。"""
    value: List[str] = []
    index = 0
    while index < len(source):
        if source[index] != "\\":
            value.append(source[index])
            index += 1
            continue
        index = _append_js_escape(source, index, value)
    return "".join(value)


def _skip_js_space(source: str, start: int) -> int:
    """跳过 JavaScript 空白和注释。"""
    index = start
    while index < len(source):
        if source[index].isspace():
            index += 1
            continue
        if source.startswith("//", index):
            newline = source.find("\n", index + 2)
            return len(source) if newline < 0 else _skip_js_space(source, newline + 1)
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            return len(source) if end < 0 else _skip_js_space(source, end + 2)
        break
    return index


def _balanced_end(source: str, start: int) -> Optional[int]:
    """返回括号结构结束位置，忽略字符串和注释中的括号。"""
    if start >= len(source) or source[start] not in JS_BRACKET_PAIRS:
        return None
    stack = [JS_BRACKET_PAIRS[source[start]]]
    index = start + 1
    while index < len(source):
        character = source[index]
        if character in {"'", '"', "`"}:
            value, index = _read_js_string(source, index)
            if value is None:
                return None
            continue
        if source.startswith("//", index):
            newline = source.find("\n", index + 2)
            index = len(source) if newline < 0 else newline + 1
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end < 0:
                return None
            index = end + 2
            continue
        if character in JS_BRACKET_PAIRS:
            stack.append(JS_BRACKET_PAIRS[character])
        elif character == stack[-1]:
            stack.pop()
            if not stack:
                return index + 1
        index += 1
    return None


def _find_value_end(source: str, start: int, closing: str) -> int:
    """查找对象属性或数组成员的顶层结束位置。"""
    stack: List[str] = []
    index = start
    while index < len(source):
        character = source[index]
        if character in {"'", '"', "`"}:
            value, index = _read_js_string(source, index)
            if value is None:
                return len(source)
            continue
        if source.startswith("//", index):
            newline = source.find("\n", index + 2)
            index = len(source) if newline < 0 else newline + 1
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            index = len(source) if end < 0 else end + 2
            continue
        if character in JS_BRACKET_PAIRS:
            stack.append(JS_BRACKET_PAIRS[character])
        elif stack and character == stack[-1]:
            stack.pop()
        elif not stack and character in {",", closing}:
            return index
        index += 1
    return len(source)


def _top_level_property(source: str, property_name: str) -> Optional[str]:
    """从 JavaScript 对象读取指定顶层属性的原始值。"""
    if not source.startswith("{"):
        return None
    index = 1
    while index < len(source):
        index = _skip_js_space(source, index)
        while index < len(source) and source[index] == ",":
            index = _skip_js_space(source, index + 1)
        if index >= len(source) or source[index] == "}":
            return None
        if source[index] in {"'", '"'}:
            key, key_end = _read_js_string(source, index)
            if key is None:
                return None
        else:
            match = re.match(r"[A-Za-z_$][A-Za-z0-9_$]*", source[index:])
            if not match:
                value_end = _find_value_end(source, index, "}")
                index = value_end + 1
                continue
            key = match.group(0)
            key_end = index + len(key)
        colon = _skip_js_space(source, key_end)
        if colon >= len(source) or source[colon] != ":":
            value_end = _find_value_end(source, colon, "}")
            index = value_end + 1
            continue
        value_start = _skip_js_space(source, colon + 1)
        value_end = _find_value_end(source, value_start, "}")
        if key == property_name:
            return source[value_start:value_end].strip()
        index = value_end + 1
    return None


def _assigned_object(page: str, name: str) -> str:
    """读取页面中明确赋值的 JavaScript 对象。"""
    match = re.search(rf"\b{re.escape(name)}\s*=\s*(\{{)", page)
    if not match:
        return ""
    start = match.start(1)
    end = _balanced_end(page, start)
    return page[start:end] if end is not None else ""


def _property_string(source: str, property_name: str) -> str:
    """读取对象顶层字符串属性，并解码 HTML 实体。"""
    raw_value = _top_level_property(source, property_name)
    if not raw_value:
        return ""
    start = _skip_js_space(raw_value, 0)
    value, _ = _read_js_string(raw_value, start)
    return unescape(value) if value is not None else ""


def _property_scalar(source: str, property_name: str) -> str:
    """读取对象顶层字符串或数字标量。"""
    raw_value = _top_level_property(source, property_name)
    if not raw_value:
        return ""
    start = _skip_js_space(raw_value, 0)
    if start < len(raw_value) and raw_value[start] in {"'", '"'}:
        value, _ = _read_js_string(raw_value, start)
        return value or ""
    match = re.match(r"[-+]?\d+", raw_value[start:])
    return match.group(0) if match else ""


def _normalize_image_url(source_url: str, value: str) -> str:
    """校验并补全文章图片地址。"""
    try:
        image_url = urljoin(source_url, unescape(value.strip())) if value.strip() else ""
        parsed = urlparse(image_url)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    if parsed.username or parsed.password:
        return ""
    return image_url


def _gallery_images(cgi_data: str, source_url: str) -> List[List[str]]:
    """按顺序读取纯图集列表中每个对象的顶层 CDN 地址。"""
    raw_list = _top_level_property(cgi_data, "picture_page_info_list")
    if not raw_list:
        return []
    start = _skip_js_space(raw_list, 0)
    if start >= len(raw_list) or raw_list[start] != "[":
        return []
    end = _balanced_end(raw_list, start)
    if end is None:
        return []
    images: List[List[str]] = []
    seen = set()
    index = start + 1
    while index < end - 1:
        index = _skip_js_space(raw_list, index)
        while index < end - 1 and raw_list[index] == ",":
            index = _skip_js_space(raw_list, index + 1)
        if index >= end - 1:
            break
        if raw_list[index] != "{":
            index = _find_value_end(raw_list, index, "]") + 1
            continue
        object_end = _balanced_end(raw_list, index)
        if object_end is None or object_end > end:
            return []
        item = raw_list[index:object_end]
        image_url = _normalize_image_url(
            source_url, _property_string(item, "cdn_url")
        )
        if image_url and image_url not in seen:
            seen.add(image_url)
            images.append([image_url])
        index = object_end
    return images


# ── HTML 文本与元数据提取 ──────────────────────


class _SummaryHTMLParser(HTMLParser):
    """将公众号摘要中的转义标签还原为纯文本。"""

    def __init__(self) -> None:
        """初始化摘要文本缓冲区。"""
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []

    def handle_starttag(
        self, tag: str, attrs: List[Tuple[str, Optional[str]]]
    ) -> None:
        """保留块标签和换行标签的文本边界。"""
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        """在块标签和相邻链接之间补充分隔。"""
        if tag in BLOCK_TAGS or tag == "a":
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        """收集摘要可见文本。"""
        self.parts.append(data)


def _clean_summary(summary: str) -> str:
    """解码公众号摘要中的 JavaScript 转义与 HTML 标签。"""
    parser = _SummaryHTMLParser()
    parser.feed(unescape(_decode_js_escapes(summary)))
    parser.close()
    return _clean_text("".join(parser.parts))


class _ArticleHTMLParser(HTMLParser):
    """限定正文容器提取图文，避免把头像和页面控件作为文章输出。"""

    def __init__(self, source_url: str) -> None:
        """初始化文章字段与标签栈。"""
        super().__init__(convert_charrefs=True)
        self.source_url = source_url
        self.meta: Dict[str, str] = {}
        self.fields: Dict[str, List[str]] = {name: [] for name in FIELD_IDS.values()}
        self.images: List[List[str]] = []
        self.visible_text: List[str] = []
        self.has_content = False
        self._seen_images = set()
        self._stack: List[Tuple[str, str, bool]] = []

    def handle_starttag(
        self, tag: str, attrs: List[Tuple[str, Optional[str]]]
    ) -> None:
        """读取字段容器、元标签与正文图片。

        Args:
            tag: HTML 标签名。
            attrs: 标签属性列表。
        """
        attributes = dict(attrs)
        parent_field = self._stack[-1][1] if self._stack else ""
        parent_ignored = self._stack[-1][2] if self._stack else False
        ignored = parent_ignored or tag in IGNORED_TAGS
        element_id = attributes.get("id") or ""
        field = FIELD_IDS.get(element_id, parent_field)

        if not ignored:
            if tag == "meta":
                key = attributes.get("property") or attributes.get("name") or ""
                value = attributes.get("content") or ""
                if key and value:
                    self.meta[key.lower()] = value.strip()
            if element_id == "js_content":
                self.has_content = True
            if field == "content":
                if tag in BLOCK_TAGS:
                    self.fields[field].append("\n")
                elif tag in {"td", "th"}:
                    self.fields[field].append(" ")
                if tag == "img":
                    self._add_image(attributes)
            if tag in BLOCK_TAGS:
                self.visible_text.append("\n")

        if tag not in VOID_TAGS:
            self._stack.append((tag, field, ignored))

    def handle_endtag(self, tag: str) -> None:
        """结束当前字段，保留段落分隔。

        Args:
            tag: HTML 结束标签名。
        """
        for index in range(len(self._stack) - 1, -1, -1):
            open_tag, field, ignored = self._stack[index]
            if open_tag != tag:
                continue
            if not ignored and tag in BLOCK_TAGS:
                if field == "content":
                    self.fields[field].append("\n")
                self.visible_text.append("\n")
            del self._stack[index:]
            break

    def handle_startendtag(
        self, tag: str, attrs: List[Tuple[str, Optional[str]]]
    ) -> None:
        """处理自闭合标签，避免正文边界受其影响。

        Args:
            tag: HTML 标签名。
            attrs: 标签属性列表。
        """
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        """只读取可见文本，忽略脚本与样式。

        Args:
            data: HTML 文本节点。
        """
        if self._stack and self._stack[-1][2]:
            return
        text = re.sub(r"\s+", " ", data)
        self.visible_text.append(text)
        field = self._stack[-1][1] if self._stack else ""
        if field:
            self.fields[field].append(text)

    def _add_image(self, attributes: Dict[str, Optional[str]]) -> None:
        """优先保留正文懒加载图片地址，跳过内嵌占位图。"""
        value = attributes.get("data-src") or attributes.get("src") or ""
        image_url = _normalize_image_url(self.source_url, value)
        if not image_url or image_url in self._seen_images:
            return
        self._seen_images.add(image_url)
        self.images.append([image_url])


def _publication_date(page: str, visible_date: str, cgi_data: str) -> str:
    """从文章时间节点或明确的发布时间变量读取日期。"""
    date_match = re.search(
        r"(\d{4})[-年/](\d{1,2})[-月/](\d{1,2})日?", visible_date
    )
    if date_match:
        try:
            return datetime(*map(int, date_match.groups())).strftime("%Y-%m-%d")
        except ValueError:
            pass
    original_timestamp = _property_scalar(cgi_data, "ori_create_time")
    if re.fullmatch(r"\d{10}", original_timestamp):
        try:
            return datetime.fromtimestamp(
                int(original_timestamp), tz=CHINA_TIMEZONE
            ).strftime("%Y-%m-%d")
        except (ValueError, OverflowError, OSError):
            pass
    # publish_time 也出现在关联文章的数据中，只匹配本页明确的 JS 变量。
    for variable in ("ct", "create_time", "oriCreateTime"):
        match = re.search(
            rf"\bvar\s+{variable}\s*=\s*['\"]?(\d{{10}})\b", page
        )
        if match:
            try:
                return datetime.fromtimestamp(
                    int(match.group(1)), tz=CHINA_TIMEZONE
                ).strftime("%Y-%m-%d")
            except (ValueError, OverflowError, OSError):
                continue
    return ""


def parse_article_page(page: str, source_url: str) -> MediaMetadata:
    """将公众号 HTML 转换为图文元数据。

    Args:
        page: 匿名请求返回的完整 HTML。
        source_url: 文章链接，用于解析图片的相对地址。

    Returns:
        标题、公众号及署名、正文、发布时间与正文图片。

    Raises:
        RuntimeError: 页面需要验证、内容已失效或缺少可解析正文。
    """
    parser = _ArticleHTMLParser(source_url)
    parser.feed(page)
    parser.close()
    cgi_data = _assigned_object(page, "window.cgiDataNew")
    content = _clean_text("".join(parser.fields["content"]))
    is_gallery = _property_scalar(cgi_data, "item_show_type") == "8"
    gallery_images = _gallery_images(cgi_data, source_url) if is_gallery else []
    has_standard_content = parser.has_content and bool(content or parser.images)
    has_gallery_content = is_gallery and bool(gallery_images)
    if not (has_standard_content or has_gallery_content):
        visible_text = _clean_text("".join(parser.visible_text))
        if any(
            phrase in visible_text
            for phrase in ("环境异常", "完成验证", "安全验证", "验证码", "访问过于频繁")
        ):
            raise RuntimeError("微信公众号页面需要验证或访问受限，暂时无法匿名解析")
        if any(
            phrase in visible_text
            for phrase in (
                "内容已被删除", "该内容已被发布者删除", "内容已删除",
                "内容无法查看", "链接已过期", "内容不存在",
            )
        ):
            raise RuntimeError("微信公众号文章已删除、失效或无法查看")
        raise RuntimeError("微信公众号页面未返回可解析的正文或图片")

    title = (
        _clean_text("".join(parser.fields["title"]))
        or parser.meta.get("og:title", "")
        or _property_string(cgi_data, "title")
    )
    account = (
        _clean_text("".join(parser.fields["account"]))
        or _property_string(cgi_data, "nick_name")
    )
    byline = parser.meta.get("author") or parser.meta.get("og:article:author", "")
    author = (
        f"{account}（{byline}）"
        if account and byline and account != byline
        else account or byline
    )
    summary = (
        parser.meta.get("description")
        or parser.meta.get("og:description", "")
        or _property_string(cgi_data, "desc")
    )
    return {
        "url": source_url,
        "title": title,
        "author": author,
        "desc": content or _clean_summary(summary),
        "timestamp": _publication_date(
            page, "".join(parser.fields["timestamp"]), cgi_data
        ),
        "image_urls": gallery_images if has_gallery_content else parser.images,
    }
