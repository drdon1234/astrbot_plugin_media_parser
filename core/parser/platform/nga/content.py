"""NGA 正文转换，整理 HTML 和 BBCode 并提取正文配图。"""

import re
from html import escape, unescape
from html.parser import HTMLParser
from typing import Any, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit


BLOCK_TAGS = {
    "article", "blockquote", "div", "h1", "h2", "h3", "h4", "h5", "h6",
    "hr", "li", "ol", "p", "pre", "section", "table", "tr", "ul",
}
IGNORED_TAGS = {"script", "style", "template"}
EMOTICON_PATH_RE = re.compile(
    r"/(?:smile|smiles|smilies|emotion|emotions|emoticon|emoticons)/", re.IGNORECASE
)
FORMAT_TAG_RE = re.compile(
    r"\[/?(?:b|i|u|s|del|color|size|font|align|center|left|right|pid|uid|tid)"
    r"(?:=[^\]]*)?\]",
    re.IGNORECASE,
)


def _http_url(value: str, base: str = "") -> str:
    """只接受完整 HTTP 地址或指定来源下可明确补全的路径。"""
    value = unescape(value.strip())
    if not value or re.search(r"[\s\x00-\x1f\x7f]", value):
        return ""
    if value.startswith("//"):
        value = "https:" + value
    elif base and value.startswith(("./mon_", "mon_", "/")):
        value = urljoin(base.rstrip("/") + "/", value)
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname or parsed.username or parsed.password
        ):
            return ""
        parsed.port
    except ValueError:
        return ""
    return value


def _bbcode_to_html(content: str) -> str:
    """将有阅读语义的 BBCode 转成可统一处理的 HTML。"""
    content = re.sub(
        r"\[img(?:=[^\]]*)?\](.*?)\[/img\]",
        lambda match: '<img src="' + escape(match.group(1).strip(), quote=True) + '">',
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    content = re.sub(
        r"\[url=([^\]]+)\](.*?)\[/url\]",
        lambda match: (
            '<a href="' + escape(match.group(1).strip().strip('\"\''), quote=True)
            + '">' + match.group(2) + "</a>"
        ),
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    content = re.sub(
        r"\[url\](.*?)\[/url\]",
        lambda match: (
            '<a href="' + escape(match.group(1).strip(), quote=True)
            + '">' + escape(match.group(1).strip()) + "</a>"
        ),
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    content = re.sub(
        r"\[s:([^\]]+)\]",
        lambda match: "[表情：" + escape(match.group(1).rsplit(":", 1)[-1]) + "]",
        content,
        flags=re.IGNORECASE,
    )
    content = re.sub(
        r"\[(collapse|fold)(?:=([^\]]*))?\]",
        lambda match: "<div>折叠内容" + (
            "（" + escape(match.group(2)) + "）" if match.group(2) else ""
        ) + "：<br>",
        content,
        flags=re.IGNORECASE,
    )
    content = re.sub(r"\[/(?:collapse|fold)\]", "</div>", content, flags=re.IGNORECASE)
    content = re.sub(
        r"\[quote(?:=([^\]]*))?\]",
        lambda match: "<blockquote>" + (
            escape(match.group(1)) + "：<br>" if match.group(1) else ""
        ),
        content,
        flags=re.IGNORECASE,
    )
    content = re.sub(r"\[/quote\]", "</blockquote>", content, flags=re.IGNORECASE)
    content = re.sub(r"\[list(?:=[^\]]*)?\]", "<ul>", content, flags=re.IGNORECASE)
    content = re.sub(r"\[/list\]", "</ul>", content, flags=re.IGNORECASE)
    content = content.replace("[*]", "<br>- ")
    content = re.sub(r"\[/?code\]", "<br>", content, flags=re.IGNORECASE)
    content = re.sub(
        r"\[(/?)h\]", lambda match: "<" + match.group(1) + "h3>",
        content, flags=re.IGNORECASE,
    )
    return FORMAT_TAG_RE.sub("", content)


class _ContentParser(HTMLParser):
    """按正文顺序读取文本、链接和配图。"""

    def __init__(self, attach_prefix: str) -> None:
        """初始化正文缓冲及图片去重集合。"""
        super().__init__(convert_charrefs=True)
        self.attach_prefix = attach_prefix
        self.parts: List[str] = []
        self.images: List[List[str]] = []
        self.image_seen = set()
        self.ignored: List[str] = []
        self.links: List[Tuple[str, int]] = []

    def _append_image(self, value: str) -> None:
        """补全附件链接并按首次出现顺序保存图片。"""
        url = _http_url(value, self.attach_prefix)
        if url and url not in self.image_seen:
            self.image_seen.add(url)
            self.images.append([url])

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        """读取 HTML 起始标签。

        Args:
            tag: 标签名。
            attrs: 标签属性。
        """
        if tag in IGNORED_TAGS:
            self.ignored.append(tag)
            return
        if self.ignored:
            return
        attributes = dict(attrs)
        if tag in BLOCK_TAGS or tag == "br":
            self.parts.append("\n")
        if tag == "blockquote":
            self.parts.append("引用：\n")
        elif tag == "li":
            self.parts.append("- ")
        elif tag == "a":
            self.links.append((
                _http_url(attributes.get("href") or "", "https://bbs.nga.cn/"),
                len(self.parts),
            ))
        elif tag == "img":
            source = attributes.get("src") or ""
            markers = " ".join(attributes.get(key) or "" for key in ("class", "id"))
            if EMOTICON_PATH_RE.search(source) or re.search(
                r"\b(?:smile|emotion)\w*", markers, re.IGNORECASE
            ):
                label = (attributes.get("alt") or attributes.get("title") or "").strip()
                self.parts.append("[表情" + ("：" + label if label else "") + "]")
            else:
                self._append_image(source)
                self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        """读取 HTML 结束标签并补充可读链接。

        Args:
            tag: 标签名。
        """
        if self.ignored:
            if tag == self.ignored[-1]:
                self.ignored.pop()
            return
        if tag == "a" and self.links:
            url, start = self.links.pop()
            label = "".join(self.parts[start:]).strip()
            if url and label != url:
                self.parts.append("（" + url + "）" if label else url)
        if tag in BLOCK_TAGS:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        """读取普通文本并忽略脚本、样式内容。

        Args:
            data: 原始文本片段。
        """
        if not self.ignored:
            self.parts.append(data)


def parse_content(
    content: str, attach_prefix: str, attachments: Any = None
) -> Tuple[str, List[List[str]]]:
    """将 NGA 混合正文转换为可发送的文字及图片候选组。

    Args:
        content: 首帖的 HTML 与 BBCode 正文。
        attach_prefix: 接口返回的附件地址前缀。
        attachments: 首帖的附件字段。

    Returns:
        保留基本阅读层次的文字与按出现顺序去重的图片候选组。
    """
    parser = _ContentParser(attach_prefix)
    parser.feed(_bbcode_to_html(content))
    parser.close()
    if isinstance(attachments, list):
        for attachment in attachments:
            if not isinstance(attachment, dict) or attachment.get("type") != "img":
                continue
            value = attachment.get("attachurl")
            if isinstance(value, str):
                parser._append_image(value)
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in "".join(parser.parts).splitlines()]
    text = "\n".join(line for line in lines if line)
    return text, parser.images
