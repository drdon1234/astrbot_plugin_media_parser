"""提取豆瓣页面正文、媒体和评论，保持内容区域与推荐信息分离。"""

import html
import json
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse


class Node:
    """保存豆瓣页面所需的轻量元素树。

    Attributes:
        tag: 元素标签。
        attrs: 元素属性。
        children: 子元素与文本。
    """

    def __init__(self, tag: str = '', attrs: Optional[Dict[str, str]] = None) -> None:
        """初始化页面元素。

        Args:
            tag: 元素标签，根元素可为空。
            attrs: 元素属性，缺省时使用空字典。
        """
        self.tag = tag
        self.attrs = attrs or {}
        self.children: List[Any] = []

    def find_all(self, tag: str = '', cls: str = '', id: str = '',
                 attr: str = '', value: str = '') -> List['Node']:
        """查找符合条件的后代元素。

        Args:
            tag: 标签名，为空时不限。
            cls: 样式类名，为空时不限。
            id: 元素标识，为空时不限。
            attr: 必须存在的属性名，为空时不限。
            value: 属性值，为空时只检查属性是否存在。

        Returns:
            按页面顺序排列的匹配元素。
        """
        result = []
        for child in self.children:
            if not isinstance(child, Node):
                continue
            if ((not tag or child.tag == tag)
                    and (not cls or cls in child.attrs.get('class', '').split())
                    and (not id or child.attrs.get('id') == id)
                    and (not attr or attr in child.attrs and (not value or child.attrs[attr] == value))):
                result.append(child)
            result.extend(child.find_all(tag, cls, id, attr, value))
        return result

    def find(self, tag: str = '', cls: str = '', id: str = '',
             attr: str = '', value: str = '') -> Optional['Node']:
        """返回第一个匹配的后代元素。

        Args:
            tag: 标签名，为空时不限。
            cls: 样式类名，为空时不限。
            id: 元素标识，为空时不限。
            attr: 必须存在的属性名，为空时不限。
            value: 属性值，为空时只检查属性是否存在。

        Returns:
            匹配元素，无匹配时返回空值。
        """
        return next(iter(self.find_all(tag, cls, id, attr, value)), None)

    def text(self) -> str:
        """读取正文文本，保留段落并忽略脚本。

        Returns:
            清理空白后的元素文本。
        """
        if self.tag in {'script', 'style', 'noscript'}:
            return ''
        parts = []
        for child in self.children:
            if isinstance(child, str):
                parts.append(re.sub(r'\s+', ' ', child))
            else:
                value = child.text()
                if child.tag in {'p', 'div', 'br', 'li', 'h1', 'h2', 'h3', 'blockquote', 'tr'}:
                    value = '\n' + value + '\n'
                parts.append(value)
        return re.sub(r'\n\s*\n+', '\n\n', ''.join(parts)).strip()


class _TreeParser(HTMLParser):
    """处理页面元素与省略的列表结束标签。"""

    def __init__(self) -> None:
        """初始化元素树与未闭合标签栈。"""
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list) -> None:
        """接收开始标签并加入元素树。

        Args:
            tag: HTML 标签名称。
            attrs: HTMLParser 提供的属性键值对。
        """
        if tag in {'li', 'p', 'dt', 'dd', 'tr', 'td', 'th'} and self.stack[-1].tag == tag:
            self.stack.pop()
        node = Node(tag, {key: value or '' for key, value in attrs})
        self.stack[-1].children.append(node)
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        """接收自闭合标签并保持当前层级。

        Args:
            tag: HTML 标签名称。
            attrs: HTMLParser 提供的属性键值对。
        """
        self.handle_starttag(tag, attrs)
        if self.stack[-1].tag == tag:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        """接收结束标签并关闭匹配的元素层级。

        Args:
            tag: 待关闭的 HTML 标签名称。
        """
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        """将文本加入当前元素。

        Args:
            data: HTMLParser 解码后的文本。
        """
        self.stack[-1].children.append(data)


def parse_html(value: str) -> Node:
    """解析网页或正文片段为平台内部元素树。

    Args:
        value: HTML 网页或片段。

    Returns:
        包含完整输入的根元素。
    """
    parser = _TreeParser()
    parser.feed(value)
    return parser.root


def text_of(node: Optional[Node]) -> str:
    """读取可缺省元素的文本。

    Args:
        node: 待提取元素。

    Returns:
        元素文本，空元素返回空字符串。
    """
    return node.text() if node else ''


def first(root: Node, *classes: str) -> Optional[Node]:
    """按优先级查找正文样式类。

    Args:
        root: 查找范围。
        classes: 按优先级排列的样式类。

    Returns:
        第一个匹配元素，无匹配时返回空值。
    """
    return next((node for cls in classes if (node := root.find(cls=cls))), None)


def meta(root: Node, key: str) -> str:
    """读取页面明确提供的元信息。

    Args:
        root: 页面根元素。
        key: 元信息名称。

    Returns:
        元信息内容，缺失时返回空字符串。
    """
    node = root.find('meta', attr='property', value=key) or root.find('meta', attr='name', value=key)
    return node.attrs.get('content', '').strip() if node else ''


def structured_data(root: Node) -> Dict[str, Any]:
    """读取 JSON-LD 标识与辅助字段，不以摘要替代全文。

    Args:
        root: 页面根元素。

    Returns:
        首个有效的结构化数据对象。
    """
    for node in root.find_all('script', attr='type', value='application/ld+json'):
        try:
            data = json.loads(''.join(child for child in node.children if isinstance(child, str)))
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def media_url(value: Any) -> str:
    """只接受豆瓣及阅读媒体域的 HTTP 地址，保留签名查询参数。

    Args:
        value: 页面或接口提供的媒体地址。

    Returns:
        通过校验的地址，不可信地址返回空字符串。
    """
    if not isinstance(value, str):
        return ''
    url = html.unescape(value.strip())
    if url.startswith('//'):
        url = 'https:' + url
    if re.search(r'[\x00-\x20\\]', url):
        return ''
    try:
        parsed = urlparse(url)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
                or parsed.username or parsed.password or parsed.port not in {None, 80, 443}):
            return ''
        if not any(parsed.hostname == domain or parsed.hostname.endswith('.' + domain)
                   for domain in ('douban.com', 'doubanio.com', 'arkread.com')):
            return ''
    except ValueError:
        return ''
    return url


def image_candidates(value: Any) -> List[str]:
    """按接口给出的原图、大图和常规图顺序保留有效候选。

    Args:
        value: 图片地址或接口图片对象。

    Returns:
        去重后的可信图片地址。
    """
    if isinstance(value, str):
        return [url] if (url := media_url(value)) else []
    if not isinstance(value, dict):
        return []
    if isinstance(value.get('image'), dict):
        value = value['image']
    result = []
    for key in ('raw', 'large', 'normal', 'url'):
        item = value.get(key)
        url = media_url(item.get('url') if isinstance(item, dict) else item)
        if url and url not in result:
            result.append(url)
    return result


def images_in(root: Optional[Node]) -> List[List[str]]:
    """只从指定正文区域提取图片，排除空地址和重复资源。

    Args:
        root: 正文范围。

    Returns:
        按页面顺序排列的图片候选组。
    """
    result = []
    seen = set()
    if not root:
        return result
    for node in root.find_all('img'):
        urls = list(dict.fromkeys(url for key in ('data-original', 'data-src', 'src')
                                  if (url := media_url(node.attrs.get(key)))))
        if urls and urls[0] not in seen:
            seen.add(urls[0])
            result.append(urls)
    return result


def video_url(value: Any) -> str:
    """仅将实际视频播放文件交给下载器。

    Args:
        value: 页面或接口提供的播放地址。

    Returns:
        视频文件地址，播放列表加下载器所需的标记，无效时为空。
    """
    url = media_url(value)
    path = urlparse(url).path.lower() if url else ''
    if path.endswith('.m3u8'):
        return 'm3u8:' + url
    return url if path.endswith(('.mp4', '.webm', '.m4v', '.mov')) else ''


def json_comment(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """规范化实际可见的评论，保留纯图片评论和一层引用。

    Args:
        item: 接口评论对象。

    Returns:
        统一评论字段，删除、禁用和空评论返回空值。
    """
    if item.get('is_deleted') or item.get('is_disabled') or item.get('is_censoring'):
        return None
    identity = str(item.get('id') or '')
    if not identity:
        return None
    message = _json_comment_message(item)
    if not message:
        return None
    reference = item.get('ref_comment')
    if isinstance(reference, dict) and not any(reference.get(key)
                                             for key in ('is_deleted', 'is_disabled', 'is_censoring')):
        reference_message = _json_comment_message(reference)
        author = reference.get('author')
        author = author if isinstance(author, dict) else {}
        if reference_message:
            message = f'引用 {author.get("name") or ""}: {reference_message}\n{message}'
    author = item.get('author')
    author = author if isinstance(author, dict) else {}
    comment = {
        'id': identity, 'username': str(author.get('name') or ''),
        'uid': str(author.get('id') or ''), 'message': message.strip(),
        'time': str(item.get('create_time') or ''),
    }
    if item.get('vote_count') is not None:
        try:
            comment['likes'] = max(0, int(item['vote_count']))
        except (TypeError, ValueError, OverflowError):
            pass
    return comment


def _json_comment_message(item: Dict[str, Any]) -> str:
    """保留评论实际文字与图片链接，过滤异常字段类型。"""
    value = item.get('text') or item.get('content')
    message = value.strip() if isinstance(value, str) else ''
    photos = item.get('photos')
    seen = set()
    for photo in photos if isinstance(photos, list) else []:
        urls = image_candidates(photo)
        if urls and urls[0] not in seen:
            seen.add(urls[0])
            message += '\n[图片] ' + urls[0]
    return message.strip()


def _comment_content(node: Node) -> Node:
    """复制回复正文，排除作者、引用及操作按钮区域。"""
    excluded = {'reply-quote', 'author', 'comment-info', 'user-info', 'avatar',
                'pic', 'op-lnks', 'group_banned', 'comment-vote', 'digg'}
    result = Node(node.tag, node.attrs.copy())
    for child in node.children:
        if isinstance(child, str):
            result.children.append(child)
        elif not excluded.intersection(child.attrs.get('class', '').split()):
            result.children.append(_comment_content(child))
    return result


def _comment_message(node: Node) -> str:
    """完整正文优先于摘要，并将一层引用与本条回复分开。"""
    content = _comment_content(node)
    body = first(content, 'comment-content', 'reply-content', 'comment-text')
    if body:
        message = text_of(first(body, 'all', 'short') or body)
    else:
        paragraphs = content.find_all('p')
        message = '\n\n'.join(text_of(first(item, 'all', 'short') or item)
                              for item in paragraphs).strip()
        if not paragraphs:
            message = text_of(first(content, 'all', 'short', 'comment'))
    for urls in images_in(content):
        message += '\n[图片] ' + urls[0]
    message = message.strip()
    if not message:
        return ''
    quote = node.find(cls='reply-quote')
    if quote:
        reference = text_of(first(quote, 'all', 'short'))
        author = next((link for link in quote.find_all('a')
                       if '/people/' in link.attrs.get('href', '') and link.text()), None)
        if reference:
            message = f'引用 {text_of(author)}: {reference}\n{message}'
    return message


def html_comments(root: Node) -> List[Dict[str, Any]]:
    """提取短评和普通回复，热门区优先且按身份去重。

    Args:
        root: 评论所在页面或区域。

    Returns:
        统一评论字段，缺少点赞信息时不填充点赞数。
    """
    popular = root.find(id='popular-bd') or root.find(cls='popular-bd')
    nodes = ((popular.find_all(cls='comment-item') + popular.find_all(cls='reply-item'))
             if popular else []) + root.find_all(cls='comment-item') + root.find_all(cls='reply-item')
    result = []
    seen = set()
    for node in nodes:
        identity = node.attrs.get('data-cid') or node.attrs.get('id', '')
        if not identity or identity in seen:
            continue
        message = _comment_message(node)
        if not message:
            continue
        author = first(node, 'author', 'comment-info', 'user-info') or node
        user = next((link for link in author.find_all('a')
                     if '/people/' in link.attrs.get('href', '') and link.text()), None)
        clock = first(author, 'comment-time', 'pubtime', 'pubdate', 'pub-date', 'time')
        time = (clock.attrs.get('title') or clock.text()) if clock else ''
        if not time:
            match = re.search(r'\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}:\d{2})?', author.text())
            time = match.group(0) if match else ''
        user_url = user.attrs.get('href', '') if user else ''
        uid = re.search(r'/people/([^/]+)', user_url)
        comment = {
            'id': identity, 'username': text_of(user),
            'uid': uid.group(1) if uid else '', 'message': message, 'time': time,
        }
        vote = first(node, 'votes', 'vote-count', 'comment-vote-count', 'vote-count-num', 'comment-vote', 'digg')
        if not vote:
            vote = node.find(attr='data-count')
        number = re.search(r'\d+', (vote.attrs.get('data-count') or vote.text())) if vote else None
        if number:
            comment['likes'] = int(number.group(0))
        seen.add(identity)
        result.append(comment)
    return result
