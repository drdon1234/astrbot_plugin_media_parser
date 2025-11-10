# -*- coding: utf-8 -*-
import asyncio
import os
import re
from typing import Optional, Dict, Any, List

import aiohttp
import tempfile

from .base_parser import BaseVideoParser


class TwitterParser(BaseVideoParser):
    """Twitter/X 视频解析器"""

    def __init__(self, max_media_size_mb: float = 0.0, large_media_threshold_mb: float = 50.0, use_image_proxy: bool = False, use_video_proxy: bool = False, proxy_url: str = None, cache_dir: str = "/app/sharedFolder/video_parser/cache", pre_download_all_media: bool = False, max_concurrent_downloads: int = 3):
        """
        初始化 Twitter 解析器
        Args:
            max_media_size_mb: 最大允许的媒体大小(MB)，超过此大小的媒体将被跳过，0表示不限制
            large_media_threshold_mb: 大媒体阈值(MB)，超过此大小的媒体将单独发送，0表示不启用，最大不超过100MB
            use_image_proxy: 是否使用图片代理
            use_video_proxy: 是否使用视频代理
            proxy_url: 代理地址（格式：http://host:port 或 socks5://host:port），图片和视频共用此代理地址
            cache_dir: 媒体文件缓存目录（通用缓存目录，用于Twitter视频和所有大媒体）
            pre_download_all_media: 是否预先下载所有媒体到本地
            max_concurrent_downloads: 最大并发下载数
        """
        super().__init__("Twitter/X", max_media_size_mb, large_media_threshold_mb, cache_dir, pre_download_all_media, max_concurrent_downloads)
        self.use_image_proxy = use_image_proxy
        self.use_video_proxy = use_video_proxy
        self.proxy_url = proxy_url
        self.semaphore = asyncio.Semaphore(5)
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json',
        }

    def can_parse(self, url: str) -> bool:
        """
        判断是否可以解析此URL
        Args:
            url: 视频链接
        Returns:
            bool: 如果可以解析返回True，否则返回False
        """
        if not url:
            return False
        url_lower = url.lower()
        if 'twitter.com' in url_lower or 'x.com' in url_lower:
            if re.search(r'/status/(\d+)', url):
                return True
        return False

    def extract_links(self, text: str) -> List[str]:
        """
        从文本中提取 Twitter 链接
        Args:
            text: 输入文本
        Returns:
            List[str]: Twitter链接列表
        """
        result_links = []
        seen_ids = set()
        pattern = r'https?://(?:twitter\.com|x\.com)/[^\s]*?status/(\d+)[^\s<>"\'()]*'
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            tweet_id = match.group(1)
            if tweet_id not in seen_ids:
                seen_ids.add(tweet_id)
                original_url = match.group(0)
                standardized_url = re.sub(r'https?://(?:twitter\.com|x\.com)', 'https://x.com', original_url, flags=re.IGNORECASE)
                result_links.append(standardized_url)
        return result_links

    def _get_image_proxy(self) -> Optional[str]:
        """获取图片代理地址"""
        if self.use_image_proxy and self.proxy_url:
            return self.proxy_url
        return None

    def _get_video_proxy(self) -> Optional[str]:
        """获取视频代理地址"""
        if self.use_video_proxy and self.proxy_url:
            return self.proxy_url
        return None

    async def _fetch_media_info(self, session: aiohttp.ClientSession, tweet_id: str, max_retries: int = 3, retry_delay: float = 1.0) -> Dict[str, Any]:
        """
        使用 FxTwitter API 获取推特媒体直链（带重试机制）
        Args:
            session: aiohttp 会话
            tweet_id: 推文ID
            max_retries: 最大重试次数，默认3次
            retry_delay: 重试延迟（秒），默认1秒，使用指数退避
        Returns:
            包含 images 和 videos 的字典
        Raises:
            RuntimeError: 所有重试均失败后抛出异常
        """
        api_url = f"https://api.fxtwitter.com/status/{tweet_id}"
        # fxtwitter接口不需要传入代理
        last_exception = None

        for attempt in range(max_retries + 1):
            try:
                async with session.get(
                    api_url,
                    headers=self.headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    response.raise_for_status()
                    data = await response.json()
                    media_urls = {
                        'images': [],
                        'videos': [],
                        'text': '',
                        'author': ''
                    }
                    if 'tweet' in data:
                        tweet = data['tweet']
                        media_urls['text'] = tweet.get('text', '')
                        author_info = tweet.get('author', {})
                        if isinstance(author_info, dict):
                            author_name = author_info.get('name', '')
                            author_username = author_info.get('screen_name', '')
                            media_urls['author'] = f"{author_name}(@{author_username})" if author_name else author_username
                        if 'media' in tweet and 'photos' in tweet['media']:
                            for photo in tweet['media']['photos']:
                                media_urls['images'].append(photo.get('url', ''))
                        if 'media' in tweet and 'videos' in tweet['media']:
                            for video in tweet['media']['videos']:
                                media_urls['videos'].append({
                                    'url': video.get('url', ''),
                                    'thumbnail': video.get('thumbnail_url', ''),
                                    'duration': video.get('duration', 0)
                                })
                    return media_urls
            except aiohttp.ClientResponseError as e:
                if e.status < 500:
                    raise RuntimeError(f"解析失败：HTTP {e.status} {e.message}")
                last_exception = e
            except (aiohttp.ClientError, asyncio.TimeoutError, aiohttp.ServerTimeoutError) as e:
                last_exception = e
            except Exception as e:
                raise RuntimeError(f"解析失败：{str(e)}")

            if attempt < max_retries:
                delay = retry_delay * (2 ** attempt)
                await asyncio.sleep(delay)
            else:
                error_msg = str(last_exception) if last_exception else "未知错误"
                raise RuntimeError(f"解析失败：{error_msg}（已重试{max_retries}次）")

    async def get_video_size(self, video_url: str, session: aiohttp.ClientSession, referer: str = None) -> Optional[float]:
        """
        获取视频文件大小(MB)（Twitter专用，需要Referer请求头）
        Args:
            video_url: 视频URL
            session: aiohttp会话
            referer: 引用页面URL（可选，默认使用 x.com）
        Returns:
            Optional[float]: 视频大小(MB)，如果无法获取则返回None
        """
        try:
            proxy = self._get_video_proxy()
            headers = self.headers.copy()
            if referer:
                headers['Referer'] = referer
            else:
                headers['Referer'] = 'https://x.com/'
            async with session.head(video_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10), proxy=proxy) as resp:
                content_range = resp.headers.get("Content-Range")
                if content_range:
                    match = re.search(r'/\s*(\d+)', content_range)
                    if match:
                        size_bytes = int(match.group(1))
                        size_mb = size_bytes / (1024 * 1024)
                        return size_mb
                content_length = resp.headers.get("Content-Length")
                if content_length:
                    size_bytes = int(content_length)
                    size_mb = size_bytes / (1024 * 1024)
                    return size_mb
            headers["Range"] = "bytes=0-1"
            async with session.get(video_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10), proxy=proxy) as resp:
                content_range = resp.headers.get("Content-Range")
                if content_range:
                    match = re.search(r'/\s*(\d+)', content_range)
                    if match:
                        size_bytes = int(match.group(1))
                        size_mb = size_bytes / (1024 * 1024)
                        return size_mb
                content_length = resp.headers.get("Content-Length")
                if content_length:
                    size_bytes = int(content_length)
                    size_mb = size_bytes / (1024 * 1024)
                    return size_mb
        except Exception:
            pass
        return None

    async def get_image_size(self, image_url: str, session: aiohttp.ClientSession, headers: dict = None) -> Optional[float]:
        """
        获取图片文件大小（Twitter专用，支持proxy）
        Args:
            image_url: 图片URL
            session: aiohttp会话
            headers: 请求头（可选）
        Returns:
            Optional[float]: 图片大小(MB)，如果无法获取则返回None
        """
        try:
            proxy = self._get_image_proxy()
            request_headers = headers or self.headers.copy()
            async with session.head(image_url, headers=request_headers, timeout=aiohttp.ClientTimeout(total=10), proxy=proxy) as resp:
                content_range = resp.headers.get("Content-Range")
                if content_range:
                    match = re.search(r'/\s*(\d+)', content_range)
                    if match:
                        size_bytes = int(match.group(1))
                        size_mb = size_bytes / (1024 * 1024)
                        return size_mb
                content_length = resp.headers.get("Content-Length")
                if content_length:
                    size_bytes = int(content_length)
                    size_mb = size_bytes / (1024 * 1024)
                    return size_mb
        except Exception:
            pass
        return None

    async def _download_media_to_file(self, session: aiohttp.ClientSession, url: str, tweet_id: str, index: int, is_video: bool = False, max_retries: int = 2, retry_delay: float = 0.5, referer: str = None) -> Optional[str]:
        """
        下载媒体文件到指定位置（带重试机制）
        Args:
            session: aiohttp 会话
            url: 媒体URL
            tweet_id: 推文ID
            index: 媒体索引（同一推文可能有多个媒体）
            is_video: 是否为视频
            max_retries: 最大重试次数，默认2次
            retry_delay: 重试延迟（秒），默认0.5秒，使用指数退避
            referer: Referer URL，如果提供则使用，否则使用默认的 Twitter/X 主页
        Returns:
            文件路径，失败返回 None
        """
        # 根据媒体类型选择对应的代理
        proxy = self._get_video_proxy() if is_video else self._get_image_proxy()
        suffix = ".mp4" if is_video else ".jpg"
        download_headers = self.headers.copy()
        if referer:
            download_headers['Referer'] = referer
        else:
            download_headers['Referer'] = f'https://x.com/status/{tweet_id}'

        for attempt in range(max_retries + 1):
            try:
                async with session.get(
                    url,
                    headers=download_headers,
                    timeout=aiohttp.ClientTimeout(total=60),
                    proxy=proxy
                ) as response:
                    response.raise_for_status()
                    content = await response.read()

                    if is_video:
                        filename = f"{tweet_id}_{index}{suffix}"
                        file_path = os.path.join(self.cache_dir, filename)
                        if os.path.exists(file_path):
                            return os.path.normpath(file_path)
                        with open(file_path, 'wb') as f:
                            f.write(content)
                        return os.path.normpath(file_path)
                    else:
                        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                            temp_file.write(content)
                            file_path = os.path.normpath(temp_file.name)
                            return file_path
            except (aiohttp.ClientError, asyncio.TimeoutError, aiohttp.ServerTimeoutError):
                if attempt < max_retries:
                    delay = retry_delay * (2 ** attempt)
                    await asyncio.sleep(delay)
                    continue
                return None
            except Exception:
                return None

        return None

    async def parse(self, session: aiohttp.ClientSession, url: str) -> Optional[Dict[str, Any]]:
        """解析单个 Twitter 链接"""
        async with self.semaphore:
            # 跟踪所有下载的文件，以便在异常时清理
            downloaded_files = []
            try:
                tweet_id_match = re.search(r'/status/(\d+)', url)
                if not tweet_id_match:
                    return None
                tweet_id = tweet_id_match.group(1)
                media_info = await self._fetch_media_info(session, tweet_id)
                if not media_info.get('images') and not media_info.get('videos'):
                    raise RuntimeError("解析失败：推文不包含图片或视频")
                video_files = []
                has_large_video = False
                if media_info.get('videos'):
                    # 如果启用预先下载，使用预先下载方法
                    if self.pre_download_all_media and self.cache_dir_available:
                        media_items = []
                        for idx, video_info in enumerate(media_info['videos']):
                            video_url = video_info.get('url')
                            if video_url:
                                video_size = await self.get_video_size(video_url, session, referer=url)
                                if self.max_media_size_mb > 0 and video_size is not None:
                                    if video_size > self.max_media_size_mb:
                                        continue
                                exceeds_large_threshold = False
                                if self.large_media_threshold_mb > 0 and video_size is not None and video_size > self.large_media_threshold_mb:
                                    if self.max_media_size_mb <= 0 or video_size <= self.max_media_size_mb:
                                        exceeds_large_threshold = True
                                        has_large_video = True
                                media_items.append({
                                    'url': video_url,
                                    'media_id': tweet_id,
                                    'index': idx,
                                    'is_video': True,
                                    'headers': self.headers,
                                    'video_info': video_info,
                                    'exceeds_large_threshold': exceeds_large_threshold,
                                    'file_size_mb': video_size
                                })
                        if media_items:
                            download_results = await self._pre_download_media(session, media_items, self.headers)
                            for idx, download_result in enumerate(download_results):
                                if download_result.get('success') and download_result.get('file_path'):
                                    file_path = download_result['file_path']
                                    item = media_items[idx]
                                    video_files.append({
                                        'file_path': file_path,
                                        'thumbnail': item.get('video_info', {}).get('thumbnail', ''),
                                        'duration': item.get('video_info', {}).get('duration', 0),
                                        'exceeds_large_threshold': item.get('exceeds_large_threshold', False),
                                        'file_size_mb': item.get('file_size_mb')
                                    })
                                    downloaded_files.append(file_path)
                                    if item.get('exceeds_large_threshold'):
                                        has_large_video = True
                    else:
                        # 原有逻辑：Twitter 视频必须下载到缓存目录
                        for idx, video_info in enumerate(media_info['videos']):
                            video_url = video_info.get('url')
                            if video_url:
                                video_size = await self.get_video_size(video_url, session, referer=url)
                                if self.max_media_size_mb > 0 and video_size is not None:
                                    if video_size > self.max_media_size_mb:
                                        continue
                                exceeds_large_threshold = False
                                if self.large_media_threshold_mb > 0 and video_size is not None and video_size > self.large_media_threshold_mb:
                                    if not self.cache_dir_available:
                                        raise RuntimeError("解析失败：本地缓存路径无效")
                                    if self.max_media_size_mb <= 0 or video_size <= self.max_media_size_mb:
                                        exceeds_large_threshold = True
                                        has_large_video = True
                                video_file = await self._download_media_to_file(session, video_url, tweet_id, idx, is_video=True, referer=url)
                                if video_file:
                                    video_files.append({
                                        'file_path': video_file,
                                        'thumbnail': video_info.get('thumbnail', ''),
                                        'duration': video_info.get('duration', 0),
                                        'exceeds_large_threshold': exceeds_large_threshold,
                                        'file_size_mb': video_size
                                    })
                                    downloaded_files.append(video_file)
                image_files = []
                if media_info.get('images'):
                    if not self.cache_dir_available:
                        raise RuntimeError("解析失败：本地缓存路径无效")
                    # 如果启用预先下载，使用预先下载方法
                    if self.pre_download_all_media and self.cache_dir_available:
                        media_items = []
                        for idx, image_url in enumerate(media_info['images']):
                            image_size = await self.get_image_size(image_url, session, headers=self.headers)
                            if self.max_media_size_mb > 0 and image_size is not None:
                                if image_size > self.max_media_size_mb:
                                    continue
                            media_items.append({
                                'url': image_url,
                                'media_id': tweet_id,
                                'index': idx,
                                'is_video': False,
                                'headers': self.headers,
                                'referer': url,
                                'default_referer': 'https://x.com/',
                                'proxy': self._get_image_proxy()
                            })
                        if media_items:
                            download_results = await self._pre_download_media(session, media_items, self.headers)
                            for download_result in download_results:
                                if download_result.get('success') and download_result.get('file_path'):
                                    file_path = download_result['file_path']
                                    image_files.append(file_path)
                                    downloaded_files.append(file_path)
                    else:
                        # 原有逻辑：大图片下载到缓存，小图片下载到临时文件
                        for idx, image_url in enumerate(media_info['images']):
                            image_size = await self.get_image_size(image_url, session, headers=self.headers)
                            if self.max_media_size_mb > 0 and image_size is not None:
                                if image_size > self.max_media_size_mb:
                                    continue
                            temp_file = None
                            if self.large_media_threshold_mb > 0 and image_size is not None and image_size > self.large_media_threshold_mb:
                                if self.max_media_size_mb <= 0 or image_size <= self.max_media_size_mb:
                                    temp_file = await self._download_large_media_to_cache(
                                        session,
                                        image_url,
                                        tweet_id,
                                        index=idx,
                                        headers=self.headers,
                                        is_video=False,
                                        referer=url,
                                        default_referer='https://x.com/',
                                        proxy=self._get_image_proxy()
                                    )
                            if not temp_file:
                                temp_file = await self._download_media_to_file(session, image_url, tweet_id, idx, is_video=False, referer=url)
                            if temp_file:
                                image_files.append(temp_file)
                                downloaded_files.append(temp_file)
                if not video_files and not image_files:
                    # 清理已下载的文件
                    self._cleanup_files_list(downloaded_files)
                    raise RuntimeError("解析失败：媒体文件下载失败")
                result = {
                    "video_url": url,
                    "title": media_info.get('text', '')[:100] or "Twitter 推文",
                    "author": media_info.get('author', ''),
                    "desc": media_info.get('text', ''),
                }
                if video_files:
                    result['video_files'] = video_files
                    result['is_twitter_video'] = True
                    result['has_large_video'] = has_large_video
                    if has_large_video:
                        result['force_separate_send'] = True
                    max_video_size = None
                    for video_file_info in video_files:
                        video_size = video_file_info.get('file_size_mb')
                        if video_size is not None:
                            if max_video_size is None or video_size > max_video_size:
                                max_video_size = video_size
                    if max_video_size is not None:
                        result['file_size_mb'] = max_video_size
                if image_files:
                    result['image_files'] = image_files
                    result['is_twitter_images'] = True
                    if len(image_files) > 1:
                        result['is_gallery'] = True
                return result
            except RuntimeError:
                # RuntimeError 可能是业务逻辑错误，清理已下载的文件
                self._cleanup_files_list(downloaded_files)
                raise
            except Exception as e:
                # 发生异常，清理所有已下载的文件
                self._cleanup_files_list(downloaded_files)
                error_msg = str(e) if str(e) else "未知错误"
                raise RuntimeError(f"解析失败：{error_msg}")
    
    def _cleanup_files_list(self, file_paths: list):
        """
        清理文件列表
        Args:
            file_paths: 文件路径列表
        """
        for file_path in file_paths:
            if file_path and os.path.exists(file_path):
                try:
                    os.unlink(file_path)
                except Exception:
                    pass

    def build_media_nodes(self, result: Dict[str, Any], sender_name: str, sender_id: Any, is_auto_pack: bool) -> List:
        """
        构建媒体节点（视频或图片）
        重构后：
        - 纯图片图集：返回 Image 对象列表（扁平化）
        - 视频图集混合：全部单独发送（返回 Video 和 Image 对象列表，扁平化）
        Args:
            result: 解析结果
            sender_name: 发送者名称
            sender_id: 发送者ID
            is_auto_pack: 是否打包为Node（已废弃，统一扁平化返回）
        Returns:
            List: 媒体节点列表（Image 或 Video 对象）
        """
        from astrbot.api.message_components import Image
        has_video = result.get('is_twitter_video') and result.get('video_files')
        has_images = result.get('is_twitter_images') and result.get('image_files')
        if not has_video and not has_images:
            return super().build_media_nodes(result, sender_name, sender_id, is_auto_pack)
        nodes = []
        if has_video:
            for video_file_info in result['video_files']:
                file_path = video_file_info.get('file_path')
                if file_path:
                    video_node = self._build_video_node_from_file(
                        file_path,
                        sender_name,
                        sender_id,
                        False
                    )
                    if video_node:
                        nodes.append(video_node)
            if has_images:
                for image_path in result['image_files']:
                    if image_path:
                        image_path = os.path.normpath(image_path)
                        if os.path.exists(image_path):
                            try:
                                nodes.append(Image.fromFileSystem(image_path))
                            except Exception:
                                pass
        elif has_images:
            for image_path in result['image_files']:
                if image_path:
                    image_path = os.path.normpath(image_path)
                    if os.path.exists(image_path):
                        try:
                            nodes.append(Image.fromFileSystem(image_path))
                        except Exception:
                            pass
        return nodes
