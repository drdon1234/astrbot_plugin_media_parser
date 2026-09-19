"""公众号文章页面提取，读取服务端返回的正文、图片和元数据。"""

import re
from datetime import datetime, timedelta, timezone
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


def _clean_text(text: str) -> str:
    """整理段落空白，保留正文中的换行。"""
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


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
        try:
            image_url = urljoin(self.source_url, value.strip()) if value.strip() else ""
            parsed = urlparse(image_url)
        except ValueError:
            return
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return
        if parsed.username or parsed.password or image_url in self._seen_images:
            return
        self._seen_images.add(image_url)
        self.images.append([image_url])


def _publication_date(page: str, visible_date: str) -> str:
    """从文章时间节点或明确的发布时间变量读取日期。"""
    date_match = re.search(
        r"(\d{4})[-年/](\d{1,2})[-月/](\d{1,2})日?", visible_date
    )
    if date_match:
        try:
            return datetime(*map(int, date_match.groups())).strftime("%Y-%m-%d")
        except ValueError:
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
    content = _clean_text("".join(parser.fields["content"]))
    if not parser.has_content or not (content or parser.images):
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

    title = _clean_text("".join(parser.fields["title"])) or parser.meta.get(
        "og:title", ""
    )
    account = _clean_text("".join(parser.fields["account"]))
    byline = parser.meta.get("author") or parser.meta.get("og:article:author", "")
    author = (
        f"{account}（{byline}）"
        if account and byline and account != byline
        else account or byline
    )
    summary = parser.meta.get("description") or parser.meta.get("og:description", "")
    summary = re.sub(r"\\x0[dDaA]|\\[nr]", "\n", summary)
    return {
        "url": source_url,
        "title": title,
        "author": author,
        "desc": content or _clean_text(summary),
        "timestamp": _publication_date(page, "".join(parser.fields["timestamp"])),
        "image_urls": parser.images,
    }
