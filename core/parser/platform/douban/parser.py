"""豆瓣解析器，读取公开条目、社区图文、评论及有限集合预览。"""

import asyncio
import html
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

import aiohttp

from ....logger import logger

from ....constants import Config
from ....types import MediaMetadata
from ..base import BaseVideoParser
from .content import (
    Node, first, html_comments, image_candidates, images_in, json_comment,
    meta, parse_html, structured_data, text_of, video_url,
)
from .reading import parse_reading
from .web import HEADERS, DoubanWeb


CONTENT_HOSTS = frozenset({
    'douban.com', 'www.douban.com', 'm.douban.com', 'movie.douban.com',
    'book.douban.com', 'music.douban.com', 'read.douban.com',
})
SHORT_HOSTS = frozenset({'dou.bz', 'www.dou.bz', 'doubanurl.cn', 'www.doubanurl.cn'})
URL_RE = re.compile(
    r'(?<![A-Za-z0-9_.:/@%?=&#-])(?:https?://|douban://|//)?'
    r'(?:(?:(?:www|m|movie|book|music|read)\.)?douban\.com|(?:www\.)?(?:dou\.bz|doubanurl\.cn))'
    r'(?::[0-9]+)?/[^\s<>"\'()，。！？；：、（）【】《》「」,;!]+', re.IGNORECASE,
)
API_BASE = 'https://m.douban.com/rexxar/api/v2'
MAX_COMMENT_PAGES = 5
MAX_COLLECTION_ITEMS = 24
UNAVAILABLE_FLAGS = ('is_private', 'is_deleted', 'is_hidden', 'is_censoring', 'is_disabled')


def _route(url: str, depth: int = 0) -> Tuple[str, str, str]:
    """仅接受受支持主机上含明确内容身份的链接。"""
    if not isinstance(url, str) or not url.strip() or depth > 2:
        return '', '', ''
    value = html.unescape(url.strip())
    if value.startswith('//'):
        value = 'https:' + value
    elif '://' not in value:
        value = 'https://' + value
    try:
        parts = urlsplit(value)
        host = (parts.hostname or '').lower()
        if (parts.username or parts.password or parts.port not in {None, 80, 443}
                or any(ord(char) < 32 for char in value)):
            return '', '', ''
        if parts.scheme == 'douban' and host == 'douban.com':
            return _route('https://m.douban.com' + parts.path + ('?' + parts.query if parts.query else ''), depth + 1)
        if parts.scheme not in {'http', 'https'} or host not in CONTENT_HOSTS | SHORT_HOSTS:
            return '', '', ''
    except ValueError:
        return '', '', ''
    path = parts.path.rstrip('/')
    if host in SHORT_HOSTS:
        return ('short', path, urlunsplit(('https', host, parts.path, parts.query, ''))) if path else ('', '', '')
    if path == '/doubanapp/dispatch':
        values = parse_qs(parts.query).get('uri', [])
        if len(values) != 1:
            return '', '', ''
        target = values[0]
        return _route('https://m.douban.com' + target if target.startswith('/') and not target.startswith('//') else target, depth + 1)
    if path.startswith('/doubanapp/dispatch/'):
        return _route('https://m.douban.com/' + path[len('/doubanapp/dispatch/'):], depth + 1)
    patterns = []
    if host == 'read.douban.com':
        patterns = [
            ('reading', r'/(?:ebook|column|review)/(\d+)'),
            ('reading', r'/reader/ebook/(\d+)(?:/toc/\d+)?'),
            ('reading', r'/reader/column/(\d+)(?:/chapter/\d+)?'),
        ]
    elif host in {'movie.douban.com', 'book.douban.com', 'music.douban.com'}:
        patterns = [('subject', r'/subject/(\d+)'), ('review', r'/review/(\d+)'), ('comment', r'/comment/(\d+)')]
        if host == 'movie.douban.com':
            patterns += [('trailer', r'/trailer/(\d+)'), ('photo', r'/photos/photo/(\d+)')]
        if host == 'book.douban.com':
            patterns += [('discussion', r'/subject/\d+/discussion/(\d+)')]
    else:
        patterns = [
            ('topic', r'/group/topic/(\d+)'), ('note', r'/note/(\d+)'),
            ('status', r'/(?:people/[^/]+/)?status/(\d+)'),
            ('doulist', r'/doulist/(\d+)'), ('event', r'/event/(\d+)'),
            ('game', r'/game/(\d+)'), ('drama', r'/location/drama/(\d+)'),
            ('review', r'/(?:location/drama/)?review/(\d+)'),
            ('photo', r'/online/\d+/photo/(\d+)'),
            ('album', r'/online/\d+/album/(\d+)'),
        ]
        mobile = re.fullmatch(r'/(movie|tv|book|music)/(subject/)?(\d+)', path) if host == 'm.douban.com' else None
        if mobile:
            service = 'movie' if mobile.group(1) == 'tv' else mobile.group(1)
            return _route(f'https://{service}.douban.com/subject/{mobile.group(3)}/', depth + 1)
    for kind, pattern in patterns:
        match = re.fullmatch(pattern, path)
        if not match:
            continue
        identity = match.group(1)
        if not 0 < len(identity) <= 20 or int(identity) == 0:
            return '', '', ''
        if kind in {'topic', 'note', 'status'}:
            canonical = f'https://m.douban.com/{"group/topic" if kind == "topic" else kind}/{identity}/'
        else:
            canonical = urlunsplit(('https', 'www.douban.com' if host == 'douban.com' else host, path + '/', parts.query if kind == 'comment' else '', ''))
        return kind, identity, canonical
    return '', '', ''


def _date(value: str) -> str:
    """保留页面明确显示的日期或时间精度。"""
    match = re.search(r'\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?', value)
    return match.group(0) if match else ''


def _author(root: Optional[Node]) -> str:
    """读取当前内容区域的作者链接，不搜索页面推荐区。"""
    if not root:
        return ''
    return next((node.text() for node in root.find_all('a') if '/people/' in node.attrs.get('href', '') and node.text()), '')


def _append_images(metadata: MediaMetadata, groups: List[List[str]]) -> None:
    """按资源首选地址去重，保持正文图片顺序。"""
    images = metadata.setdefault('image_urls', [])
    seen = {url for group in images for url in group}
    for group in groups:
        if group and not any(url in seen for url in group):
            images.append(group)
            seen.update(group)


def _videos(root: Optional[Node]) -> Tuple[List[List[str]], List[List[str]]]:
    """提取正文原生视频及其逐项封面，不跟随外站播放器。"""
    videos, covers = [], []
    if not root:
        return videos, covers
    for node in root.find_all('video'):
        urls = list(dict.fromkeys(value for value in [video_url(node.attrs.get('src'))] + [video_url(item.attrs.get('src')) for item in node.find_all('source')] if value))
        if urls:
            videos.append(urls)
            covers.append(image_candidates(node.attrs.get('poster')))
    return videos, covers


class DoubanParser(BaseVideoParser):
    """解析豆瓣公开详情，评论按站点顺序读取并设置分页上限。"""

    def __init__(self, hot_comment_count: int = 0) -> None:
        """初始化评论数量与解析并发限制。"""
        super().__init__('douban')
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)
        try:
            self.hot_comment_count = max(0, int(hot_comment_count))
        except (TypeError, ValueError, OverflowError):
            self.hot_comment_count = 0

    def can_parse(self, url: str) -> bool:
        """判断链接是否指向支持的豆瓣公开内容。

        Args:
            url: 待判断链接。

        Returns:
            链接是否具有明确的内容类型与身份。
        """
        return bool(_route(url)[0])

    def extract_links(self, text: str) -> List[str]:
        """提取豆瓣分享链接并按内容身份去重。

        Args:
            text: 消息文本。

        Returns:
            按出现顺序排列的规范链接。
        """
        result = []
        seen = set()
        for match in URL_RE.finditer(text or ''):
            kind, identity, url = _route(match.group(0).rstrip('.,!?)]}>，。！？；：）】》」'))
            key = (kind, identity, urlsplit(url).hostname, urlsplit(url).path)
            if kind and key not in seen:
                seen.add(key)
                result.append(url)
        return result

    async def parse(self, session: aiohttp.ClientSession, url: str) -> Optional[MediaMetadata]:
        """读取豆瓣正文与可选评论，并准备公开媒体请求上下文。

        Args:
            session: 调用方网络会话。
            url: 支持的豆瓣内容链接。

        Returns:
            统一媒体元数据。

        Raises:
            ValueError: 链接或响应身份不符合预期。
            RuntimeError: 内容不可公开读取。
        """
        kind, identity, canonical = _route(url)
        if not kind:
            raise ValueError('不支持的豆瓣内容链接')
        async with self.semaphore, DoubanWeb(session) as web:
            if kind == 'short':
                kind, identity, canonical = _route(await web.resolve_url(canonical))
                if not kind or kind == 'short':
                    raise ValueError('豆瓣短链接未指向受支持的内容')
            if kind == 'reading':
                metadata = await parse_reading(web, canonical, self.hot_comment_count)
            elif kind in {'topic', 'note', 'status'}:
                metadata = await self._community(web, kind, identity, canonical)
            else:
                final_url, source = await web.get_page(canonical)
                if _route(final_url) != (kind, identity, canonical):
                    raise ValueError('豆瓣页面跳转后的内容身份不一致')
                root = parse_html(source)
                self._check_identity(root, kind, identity, canonical)
                metadata = self._html_metadata(root, kind, canonical)
                await self._html_comment_metadata(web, root, metadata, kind, identity)
            metadata['platform'] = '豆瓣'
            metadata.setdefault('url', canonical)
            metadata['image_headers'] = {**HEADERS, 'Referer': metadata['url']}
            metadata['video_headers'] = {**HEADERS, 'Referer': metadata['url']}
            if not any(metadata.get(key) for key in ('desc', 'image_urls', 'video_urls')):
                raise RuntimeError('豆瓣页面未包含可读取的公开正文或媒体')
            await web.prepare_media(metadata)
            return metadata

    @staticmethod
    def _check_identity(root: Node, kind: str, identity: str, url: str) -> None:
        """已提供规范身份的页面必须与请求一致，避免返回登录或其他详情。"""
        if kind == 'album':
            album_link = root.find('a', id='pho-num')
            share = root.find('a', attr='data-url', value=url)
            if not ((album_link and _route(urljoin(url, album_link.attrs.get('href', ''))) == (kind, identity, url))
                    or (root.find(id=f'Album-{identity}') and share)):
                raise ValueError('豆瓣公共相册身份与请求不一致')
            return
        data = structured_data(root)
        values = [meta(root, 'og:url')]
        review = root.find(cls='review-content')
        if review:
            values.append(review.attrs.get('data-url', ''))
        if data.get('url') and isinstance(data['url'], str):
            values.append(data['url'])
        for value in values:
            if not value:
                continue
            actual_kind, actual_id, actual_url = _route(urljoin(url, value))
            if not actual_kind or actual_kind != kind or actual_id != identity or urlsplit(actual_url).hostname != urlsplit(url).hostname:
                raise ValueError('豆瓣页面身份与请求内容不一致')

    async def _community(self, web: DoubanWeb, kind: str, identity: str, url: str) -> MediaMetadata:
        """读取小组、日记与广播的完整公开正文和附件。"""
        endpoint = f'{"group/topic" if kind == "topic" else kind}/{identity}'
        data = await web.get_json(f'{API_BASE}/{endpoint}', referer=url)
        if str(data.get('id') or '') != identity:
            raise ValueError('豆瓣接口未返回请求的内容')
        if any(data.get(key) for key in UNAVAILABLE_FLAGS):
            raise RuntimeError('该豆瓣内容不允许公开读取')
        metadata = self._community_metadata(data, kind, url)
        if self.hot_comment_count:
            try:
                metadata['hot_comments'] = await self._json_comments(web, endpoint, url)
            except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, ValueError) as exc:
                logger.warning(f'豆瓣评论获取失败，保留正文：{exc}')
        return metadata

    def _community_metadata(self, data: Dict[str, Any], kind: str, url: str) -> MediaMetadata:
        """只使用内容全文字段，合并卡片、原图和一层公开转发。"""
        author = data.get('author') if isinstance(data.get('author'), dict) else {}
        content = data.get('content') if kind != 'status' else data.get('text')
        root = parse_html(content) if kind != 'status' and isinstance(content, str) else Node()
        metadata: MediaMetadata = {
            'url': url, 'title': str(data.get('title') or ''), 'author': str(author.get('name') or ''),
            'desc': content.strip() if isinstance(content, str) and (kind == 'status' or not re.search(r'<[A-Za-z][^>]*>', content)) else root.text(),
            'timestamp': str(data.get('create_time') or ''), 'image_urls': [],
        }
        photos = data.get('photos') or data.get('images') or []
        if isinstance(photos, list):
            _append_images(metadata, [image_candidates(photo) for photo in photos])
        _append_images(metadata, images_in(root))
        card = data.get('card')
        if isinstance(card, dict):
            card_text = '\n'.join(str(card.get(key) or '') for key in ('title', 'subtitle', 'description') if card.get(key))
            if card_text:
                metadata['desc'] = '\n\n'.join(filter(None, [metadata['desc'], card_text]))
            _append_images(metadata, [image_candidates(card.get('image'))])
        metadata['video_urls'], metadata['video_cover_urls'] = _videos(root)
        entries = list(data['videos']) if isinstance(data.get('videos'), list) else []
        entries += [data.get('video_info'), data.get('video_card')]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            urls = list(dict.fromkeys(value for key in ('video_url', 'play_url', 'url') if (value := video_url(entry.get(key)))))
            if urls:
                metadata['video_urls'].append(urls)
                metadata['video_cover_urls'].append(image_candidates(entry.get('cover_url') or entry.get('cover')))
        reshared = data.get('reshared_status')
        if isinstance(reshared, dict) and not any(reshared.get(key) for key in UNAVAILABLE_FLAGS):
            original = self._community_metadata({**reshared, 'reshared_status': None}, 'status', url)
            metadata['desc'] += f'\n\n转发 {original.get("author", "")}：\n{original.get("desc", "")}'
            _append_images(metadata, original.get('image_urls', []))
            metadata['video_urls'].extend(original.get('video_urls', []))
            metadata['video_cover_urls'].extend(original.get('video_cover_urls', []))
        return metadata

    async def _json_comments(self, web: DoubanWeb, endpoint: str, referer: str) -> List[Dict[str, Any]]:
        """热门数组优先，普通回复补足；按真实返回数量分页并限制请求数。"""
        result, seen = [], set()
        start = 0
        for _ in range(MAX_COMMENT_PAGES):
            count = min(100, max(20, self.hot_comment_count - len(result)))
            try:
                data = await web.get_json(f'{API_BASE}/{endpoint}/comments?start={start}&count={count}', referer=referer)
            except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, ValueError) as exc:
                if not result:
                    raise
                logger.warning(f'豆瓣评论后续分页失败，保留已取得的评论：{exc}')
                break
            comments = data.get('comments') if isinstance(data.get('comments'), list) else []
            popular = data.get('popular_comments') if isinstance(data.get('popular_comments'), list) else []
            added = 0
            for item in popular + comments:
                if not isinstance(item, dict):
                    continue
                comment = json_comment(item)
                if comment and comment['id'] not in seen:
                    seen.add(comment['id'])
                    result.append(comment)
                    added += 1
                    if len(result) >= self.hot_comment_count:
                        return result
            if not comments or not added:
                break
            start += len(comments)
            total = data.get('total')
            if isinstance(total, int) and start >= total:
                break
        return result

    def _html_metadata(self, root: Node, kind: str, url: str) -> MediaMetadata:
        """按具体详情结构提取主体，禁止把页面摘要当正文兜底。"""
        data = structured_data(root)
        metadata: MediaMetadata = {'url': url, 'title': text_of(root.find('h1')) or meta(root, 'og:title'), 'image_urls': []}
        body = None
        if kind in {'subject', 'game', 'drama'}:
            return self._subject(root, metadata, kind, data)
        if kind == 'review':
            body = root.find(cls='review-content')
            metadata['author'] = body.attrs.get('data-author', '') if body else ''
            metadata['timestamp'] = str(data.get('datePublished') or text_of(root.find(cls='main-meta')))
        elif kind == 'comment':
            comments = html_comments(root)
            if not comments:
                raise RuntimeError('豆瓣短评正文不可读取')
            selected = next((comment for comment in comments if comment['id'] == _route(url)[1]), None)
            if not selected:
                raise ValueError('豆瓣短评身份与请求不一致')
            metadata.update(desc=selected['message'], author=selected['username'], timestamp=selected['time'])
            return metadata
        elif kind == 'discussion':
            body = root.find(cls='post-content')
            report = body.find(id='link-report') if body else None
            header = report.find(cls='post-author') if report else None
            metadata['author'] = _author(header)
            metadata['timestamp'] = _date(text_of(header))
            if report:
                body = Node()
                body.children = [node for node in report.children if node is not header]
        elif kind == 'doulist':
            info = root.find(id='doulist-info')
            metadata['author'] = _author(info)
            metadata['timestamp'] = _date(text_of(info))
            items = root.find_all(cls='doulist-item')[:MAX_COLLECTION_ITEMS]
            sections = [text_of(info), f'以下为豆列首批 {len(items)} 项预览。']
            for index, item in enumerate(items, 1):
                title = item.find(cls='title')
                link = title.find('a') if title else None
                sections.append(f'{index}. {text_of(title)}\n{link.attrs.get("href", "") if link else ""}\n{text_of(item.find(cls="abstract"))}\n{text_of(item.find(cls="comment"))}')
                _append_images(metadata, images_in(item))
            metadata['desc'] = '\n\n'.join(sections)
            if not info or not items:
                raise RuntimeError('豆列未返回可读取的公开条目')
            return metadata
        elif kind == 'event':
            body = root.find(id='edesc_s') or root.find(id='link-report')
            info = root.find(id='event-info')
            details = '\n'.join(node.text() for node in info.find_all(cls='event-detail')) if info else ''
            metadata['desc'] = details
            _append_images(metadata, images_in(info))
            poster = root.find('img', id='poster_img')
            if poster:
                _append_images(metadata, [image_candidates(poster.attrs.get('src'))])
        elif kind == 'photo':
            image = root.find(cls='mainphoto')
            _append_images(metadata, images_in(image))
            body = root.find(id='link-report')
            footer = root.find(cls='photo-ft')
            if not footer:
                aside = root.find(cls='aside')
                footer = next((node for node in aside.find_all(cls='mod') if '上传于' in node.text()), None) if aside else None
            metadata['author'] = _author(footer)
            metadata['timestamp'] = _date(text_of(footer))
        elif kind == 'album':
            album = root.find(cls='photolst') or root.find(id='photo_album')
            photos = images_in(album)[:MAX_COLLECTION_ITEMS]
            if not photos:
                raise RuntimeError('公共相册未返回可读取的照片')
            _append_images(metadata, photos)
            metadata['desc'] = f'以下为公共相册首批 {len(photos)} 张照片预览。'
            return metadata
        elif kind == 'trailer':
            body = root.find(cls='stage-cont')
            metadata['video_urls'], metadata['video_cover_urls'] = _videos(body)
            metadata['timestamp'] = _date(text_of(root.find(cls='main-info')))
            if not metadata['video_urls']:
                raise RuntimeError('预告片页面未返回实际播放资源')
            return metadata
        if body:
            metadata['desc'] = '\n\n'.join(filter(None, [metadata.get('desc', ''), body.text()]))
            _append_images(metadata, images_in(body))
            metadata['video_urls'], metadata['video_cover_urls'] = _videos(body)
        return metadata

    @staticmethod
    def _subject(root: Node, metadata: MediaMetadata, kind: str, data: Dict[str, Any]) -> MediaMetadata:
        """读取书影音、游戏和舞台剧介绍及封面，不获取受限作品正文。"""
        info = root.find(id='info')
        body = None
        cover = root.find(id='mainpic')
        extra = ''
        if kind == 'subject':
            report = root.find(id='link-report')
            scope = report or root
            body = scope.find(cls='all') or scope.find(attr='property', value='v:summary') or scope.find(cls='intro')
            if urlsplit(metadata['url']).hostname == 'music.douban.com':
                tracks = root.find(cls='track-list')
                extra = text_of(tracks)
        elif kind == 'game':
            info = root.find(cls='thing-attr')
            body = root.find(id='link-report')
            cover = root.find(cls='item-subject-info')
        else:
            details = root.find(cls='drama-info')
            info = details.find(cls='meta') if details else None
            cover = details.find(cls='pic') if details else None
            article = root.find(cls='article')
            body = next((node for node in article.children if isinstance(node, Node) and node.tag == 'div' and '剧情简介' in text_of(node.find('h2'))), None) if article else None
        metadata['desc'] = '\n\n'.join(filter(None, [text_of(info), text_of(body), extra]))
        if not info and not body:
            raise RuntimeError('豆瓣条目未返回可读取的公开介绍')
        authors = data.get('author')
        if urlsplit(metadata['url']).hostname == 'book.douban.com' and isinstance(authors, list):
            metadata['author'] = ' / '.join(str(item.get('name') or '') for item in authors if isinstance(item, dict))
        _append_images(metadata, images_in(cover))
        if not metadata['image_urls']:
            _append_images(metadata, [image_candidates(meta(root, 'og:image') or data.get('image'))])
        return metadata

    async def _html_comment_metadata(self, web: DoubanWeb, root: Node, metadata: MediaMetadata, kind: str, identity: str) -> None:
        """为已有主体附加热门或普通评论，附属请求失败时保留主体。"""
        if not self.hot_comment_count or kind in {'comment', 'album', 'trailer', 'event'}:
            return
        url = metadata['url']
        try:
            if kind == 'review':
                metadata['hot_comments'] = await self._json_comments(web, f'review/{identity}', url)
                return
            if kind in {'subject', 'game', 'drama', 'doulist'}:
                sort = 'score' if urlsplit(url).hostname == 'book.douban.com' or kind == 'game' else 'new_score'
                if kind == 'subject':
                    suffix = f'comments?sort={sort}&status=P'
                elif kind == 'game':
                    suffix = 'comments?sort=score'
                elif kind == 'drama':
                    suffix = 'comments/?sort=time'
                else:
                    suffix = 'comments/'
                page_url = url + suffix
                root = parse_html(await web.get_text(page_url, referer=url))
            else:
                page_url = url
            result, seen, pages = [], set(), set()
            for _ in range(MAX_COMMENT_PAGES):
                pages.add(page_url)
                added = 0
                for comment in html_comments(root):
                    if comment['id'] not in seen:
                        seen.add(comment['id'])
                        result.append(comment)
                        added += 1
                if len(result) >= self.hot_comment_count or not added:
                    break
                # 图片前后导航也有 rel=next，仅跟随评论分页器中的链接。
                paginator = root.find(cls='paginator')
                next_link = paginator.find('a', attr='rel', value='next') if paginator else None
                if not next_link and paginator:
                    next_node = paginator.find(cls='next')
                    next_link = next_node.find('a') if next_node else None
                if not next_link:
                    break
                next_url = urljoin(page_url, next_link.attrs.get('href', ''))
                previous, following = urlsplit(page_url), urlsplit(next_url)
                if (next_url in pages or previous.hostname != following.hostname
                        or previous.path.rstrip('/') != following.path.rstrip('/')):
                    break
                page_url = next_url
                try:
                    root = parse_html(await web.get_text(page_url, referer=url))
                except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, ValueError) as exc:
                    logger.warning(f'豆瓣评论后续分页失败，保留已取得的评论：{exc}')
                    break
            metadata['hot_comments'] = result[:self.hot_comment_count]
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, ValueError) as exc:
            logger.warning(f'豆瓣评论获取失败，保留正文：{exc}')
