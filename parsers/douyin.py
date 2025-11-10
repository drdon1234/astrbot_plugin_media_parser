# -*- coding: utf-8 -*-
import asyncio
import json
import os
import re
from datetime import datetime
from typing import Optional, Dict, Any, List

import aiohttp

from .base_parser import BaseVideoParser


class DouyinParser(BaseVideoParser):
    """抖音视频解析器"""

    def __init__(self, max_media_size_mb: float = 0.0, large_media_threshold_mb: float = 50.0, cache_dir: str = "/app/sharedFolder/video_parser/cache", pre_download_all_media: bool = False, max_concurrent_downloads: int = 3):
        super().__init__("抖音", max_media_size_mb, large_media_threshold_mb, cache_dir, pre_download_all_media, max_concurrent_downloads)
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 8.0.0; SM-G955U Build/R16NW) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36',
            'Referer': 'https://www.douyin.com/?is_from_mobile_home=1&recommend=1'
        }
        self.semaphore = asyncio.Semaphore(10)

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
        if 'v.douyin.com' in url or 'douyin.com' in url:
            return True
        return False

    def extract_links(self, text: str) -> List[str]:
        """
        从文本中提取抖音链接
        Args:
            text: 输入文本
        Returns:
            List[str]: 抖音链接列表
        """
        result_links = []
        seen_ids = set()
        mobile_pattern = r'https?://v\.douyin\.com/[^\s]+'
        mobile_links = re.findall(mobile_pattern, text)
        result_links.extend(mobile_links)
        note_pattern = r'https?://(?:www\.)?douyin\.com/note/(\d+)'
        note_matches = re.finditer(note_pattern, text)
        for match in note_matches:
            note_id = match.group(1)
            if note_id not in seen_ids:
                seen_ids.add(note_id)
                result_links.append(f"https://www.douyin.com/note/{note_id}")
        video_pattern = r'https?://(?:www\.)?douyin\.com/video/(\d+)'
        video_matches = re.finditer(video_pattern, text)
        for match in video_matches:
            video_id = match.group(1)
            if video_id not in seen_ids:
                seen_ids.add(video_id)
                result_links.append(f"https://www.douyin.com/video/{video_id}")
        web_pattern = r'https?://(?:www\.)?douyin\.com/[^\s]*?(\d{19})[^\s]*'
        web_matches = re.finditer(web_pattern, text)
        for match in web_matches:
            item_id = match.group(1)
            if item_id not in seen_ids:
                seen_ids.add(item_id)
                if '/note/' not in match.group(0) and '/video/' not in match.group(0):
                    standardized_url = f"https://www.douyin.com/video/{item_id}"
                    result_links.append(standardized_url)
        return result_links

    def _extract_media_id(self, url: str) -> str:
        """
        从URL中提取媒体ID
        Args:
            url: 抖音URL
        Returns:
            str: 媒体ID，如果无法提取则返回 "douyin"
        """
        video_id_match = re.search(r'/note/(\d+)', url) or \
                         re.search(r'/video/(\d+)', url) or \
                         re.search(r'(\d{19})', url)
        return video_id_match.group(1) if video_id_match else "douyin"

    def extract_router_data(self, text):
        """
        从HTML中提取ROUTER_DATA
        Args:
            text: HTML文本
        Returns:
            Optional[str]: ROUTER_DATA JSON字符串，如果未找到则返回None
        """
        start_flag = 'window._ROUTER_DATA = '
        start_idx = text.find(start_flag)
        if start_idx == -1:
            return None
        brace_start = text.find('{', start_idx)
        if brace_start == -1:
            return None
        i = brace_start
        stack = []
        while i < len(text):
            if text[i] == '{':
                stack.append('{')
            elif text[i] == '}':
                stack.pop()
                if not stack:
                    return text[brace_start:i+1]
            i += 1
        return None

    async def fetch_video_info(self, session, item_id, is_note=False):
        """
        获取视频/笔记信息
        Args:
            session: aiohttp会话
            item_id: 视频/笔记ID
            is_note: 是否为笔记
        Returns:
            Optional[Dict[str, Any]]: 视频/笔记信息字典，如果解析失败则返回None
        """
        if is_note:
            url = f'https://www.iesdouyin.com/share/note/{item_id}/'
        else:
            url = f'https://www.iesdouyin.com/share/video/{item_id}/'
        try:
            async with session.get(url, headers=self.headers) as response:
                response_text = await response.text()
                json_str = self.extract_router_data(response_text)
                if not json_str:
                    return None
                json_str = json_str.replace('\\u002F', '/').replace('\\/', '/')
                try:
                    json_data = json.loads(json_str)
                except Exception:
                    return None
                loader_data = json_data.get('loaderData', {})
                video_info = None
                for key, v in loader_data.items():
                    if isinstance(v, dict) and 'videoInfoRes' in v:
                        video_info = v['videoInfoRes']
                        break
                    elif isinstance(v, dict) and 'noteDetailRes' in v:
                        video_info = v['noteDetailRes']
                        break
                if not video_info:
                    return None
                if 'item_list' not in video_info or not video_info['item_list']:
                    return None
                item_list = video_info['item_list'][0]
                title = item_list.get('desc', '')
                author_info = item_list.get('author', {})
                nickname = author_info.get('nickname', '')
                unique_id = author_info.get('unique_id', '')
                timestamp = datetime.fromtimestamp(item_list.get('create_time', 0)).strftime('%Y-%m-%d') if item_list.get('create_time') else ''
                images = []
                image_url_lists = []
                raw_images = item_list.get('images') or []
                for idx, img in enumerate(raw_images):
                    if 'url_list' in img and img.get('url_list') and len(img['url_list']) > 0:
                        url_list = img['url_list']
                        valid_urls = []
                        for url_idx, img_url in enumerate(url_list):
                            if img_url and isinstance(img_url, str) and img_url.startswith(('http://', 'https://')):
                                valid_urls.append(img_url)
                        if valid_urls:
                            primary_url = valid_urls[0]
                            images.append(primary_url)
                            image_url_lists.append(valid_urls)
                        else:
                            image_url_lists.append([])
                    else:
                        image_url_lists.append([])
                is_gallery = len(images) > 0
                video_url = None
                thumb_url = None
                if not is_gallery and 'video' in item_list:
                    video_info_item = item_list['video']
                    if 'cover' in video_info_item and video_info_item['cover'].get('url_list'):
                        thumb_url = video_info_item['cover']['url_list'][0]
                    if 'play_addr' in video_info_item and 'uri' in video_info_item['play_addr']:
                        video = video_info_item['play_addr']['uri']
                        if video.endswith('.mp3'):
                            video_url = video
                        elif video.startswith('https://'):
                            video_url = video
                        else:
                            video_url = f'https://www.douyin.com/aweme/v1/play/?video_id={video}'
                elif is_gallery and 'video' in item_list:
                    video_info_item = item_list['video']
                    if 'cover' in video_info_item and video_info_item['cover'].get('url_list'):
                        thumb_url = video_info_item['cover']['url_list'][0]
                if is_gallery and not thumb_url and images:
                    thumb_url = images[0]
                author = nickname
                if unique_id:
                    author = f"{nickname}(uid:{unique_id})" if nickname else f"(uid:{unique_id})"
                return {
                    'title': title,
                    'nickname': nickname,
                    'unique_id': unique_id,
                    'author': author,
                    'timestamp': timestamp,
                    'thumb_url': thumb_url,
                    'video_url': video_url,
                    'images': images,
                    'image_url_lists': image_url_lists,
                    'is_gallery': is_gallery
                }
        except aiohttp.ClientError:
            return None

    async def get_redirected_url(self, session, url):
        """
        获取重定向后的URL
        Args:
            session: aiohttp会话
            url: 原始URL
        Returns:
            str: 重定向后的URL
        """
        async with session.head(url, allow_redirects=True) as response:
            return str(response.url)

    async def get_video_size(self, video_url: str, session: aiohttp.ClientSession, referer: str = None) -> Optional[float]:
        """
        获取视频文件大小(MB)（抖音专用，需要Referer请求头）
        Args:
            video_url: 视频URL
            session: aiohttp会话
            referer: 引用页面URL（可选，默认使用douyin.com）
        Returns:
            Optional[float]: 视频大小(MB)，如果无法获取则返回None
        """
        try:
            headers = self.headers.copy()
            if referer:
                headers["Referer"] = referer
            else:
                headers["Referer"] = 'https://www.douyin.com/'
            async with session.head(video_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
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
            async with session.get(video_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
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

    async def _download_image_to_file(self, session: aiohttp.ClientSession, image_url: str, index: int = 0, referer: str = None) -> Optional[str]:
        """
        下载图片到临时文件（使用基类方法）
        Args:
            session: aiohttp 会话
            image_url: 图片URL
            index: 图片索引
            referer: Referer URL，如果提供则使用，否则使用默认的抖音主页
        Returns:
            Optional[str]: 临时文件路径，失败返回 None
        """
        douyin_headers = {
            'Origin': 'https://www.douyin.com',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Sec-Fetch-Dest': 'image',
            'Sec-Fetch-Mode': 'no-cors',
            'Sec-Fetch-Site': 'same-site',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
        }
        return await super()._download_image_to_file(
            session, image_url, index, douyin_headers, referer, 'https://www.douyin.com/'
        )

    async def _download_image_with_retry(
        self,
        session: aiohttp.ClientSession,
        image_url: str,
        image_id: str,
        index: int = 0,
        referer: str = None,
        max_retries: int = 2,
        retry_delay: float = 0.5,
        to_cache: bool = False
    ) -> Optional[str]:
        """
        下载图片（带重试机制，类似于推特解析器）
        Args:
            session: aiohttp 会话
            image_url: 图片URL
            image_id: 图片ID
            index: 图片索引
            referer: Referer URL，如果提供则使用，否则使用默认的抖音主页
            max_retries: 最大重试次数，默认2次
            retry_delay: 重试延迟（秒），默认0.5秒，使用指数退避
            to_cache: 是否下载到缓存目录，False则下载到临时文件
        Returns:
            Optional[str]: 文件路径，失败返回 None
        """
        import tempfile
        douyin_headers = {
            'Origin': 'https://www.douyin.com',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Sec-Fetch-Dest': 'image',
            'Sec-Fetch-Mode': 'no-cors',
            'Sec-Fetch-Site': 'same-site',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
            'User-Agent': 'Mozilla/5.0 (Linux; Android 8.0.0; SM-G955U Build/R16NW) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36',
            'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
        }
        if referer:
            douyin_headers['Referer'] = referer
        else:
            douyin_headers['Referer'] = 'https://www.douyin.com/'

        for attempt in range(max_retries + 1):
            try:
                async with session.get(
                    image_url,
                    headers=douyin_headers,
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as response:
                    response.raise_for_status()
                    content = await response.read()
                    content_type = response.headers.get('Content-Type', '')
                    suffix = self._get_image_suffix(content_type, image_url)
                    
                    if to_cache:
                        # 下载到缓存目录
                        if not self.cache_dir_available or not self.cache_dir:
                            return None
                        filename = f"{image_id}_{index}{suffix}"
                        file_path = os.path.join(self.cache_dir, filename)
                        if os.path.exists(file_path):
                            return os.path.normpath(file_path)
                        with open(file_path, 'wb') as f:
                            f.write(content)
                        return os.path.normpath(file_path)
                    else:
                        # 下载到临时文件
                        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                            temp_file.write(content)
                            file_path = os.path.normpath(temp_file.name)
                            return file_path
            except (aiohttp.ClientError, asyncio.TimeoutError, aiohttp.ServerTimeoutError) as e:
                if attempt < max_retries:
                    delay = retry_delay * (2 ** attempt)
                    await asyncio.sleep(delay)
                    continue
                return None
            except Exception:
                return None

        return None

    async def _download_large_media_to_cache(self, session: aiohttp.ClientSession, media_url: str, media_id: str, index: int = 0, headers: dict = None, is_video: bool = True, referer: str = None, default_referer: str = None, proxy: str = None) -> Optional[str]:
        """
        下载大媒体到缓存目录（重写基类方法，为图片添加重试逻辑）
        Args:
            session: aiohttp会话
            media_url: 媒体URL
            media_id: 媒体ID
            index: 索引
            headers: 自定义请求头（如果提供，会与默认请求头合并）
            is_video: 是否为视频（True为视频，False为图片）
            referer: Referer URL，如果提供则使用
            default_referer: 默认 Referer URL（如果 referer 未提供）
            proxy: 代理地址（可选）
        Returns:
            Optional[str]: 文件路径或None
        """
        if not self.cache_dir_available or not self.cache_dir:
            return None
        
        # 对于图片，使用带重试的下载方法
        if not is_video:
            referer_url = referer if referer else (default_referer or 'https://www.douyin.com/')
            return await self._download_image_with_retry(
                session,
                media_url,
                media_id,
                index,
                referer_url,
                max_retries=2,
                retry_delay=0.5,
                to_cache=True
            )
        
        # 对于视频，使用基类方法（保持原有逻辑）
        return await super()._download_large_media_to_cache(
            session, media_url, media_id, index, headers, is_video, referer, default_referer, proxy
        )

    async def parse(self, session: aiohttp.ClientSession, url: str) -> Optional[Dict[str, Any]]:
        """
        解析单个抖音链接
        Args:
            session: aiohttp会话
            url: 抖音链接
        Returns:
            Optional[Dict[str, Any]]: 解析结果字典，如果解析失败则返回None
        """
        async with self.semaphore:
            # 跟踪所有下载的文件，以便在异常时清理
            downloaded_files = []
            try:
                redirected_url = await self.get_redirected_url(session, url)
                is_note = '/note/' in redirected_url or '/note/' in url
                note_id = None
                if is_note:
                    note_match = re.search(r'/note/(\d+)', redirected_url)
                    if not note_match:
                        note_match = re.search(r'/note/(\d+)', url)
                    if note_match:
                        note_id = note_match.group(1)
                        result = await self.fetch_video_info(session, note_id, is_note=True)
                    else:
                        return None
                else:
                    video_match = re.search(r'/video/(\d+)', redirected_url)
                    if video_match:
                        video_id = video_match.group(1)
                        result = await self.fetch_video_info(session, video_id, is_note=False)
                    else:
                        match = re.search(r'(\d{19})', redirected_url)
                        if match:
                            item_id = match.group(1)
                            result = await self.fetch_video_info(session, item_id, is_note=False)
                        else:
                            return None
                if not result:
                    return None
                is_gallery = result.get('is_gallery', False)
                images = result.get('images', [])
                image_url_lists = result.get('image_url_lists', [])
                if is_gallery and images:
                    if not self.cache_dir_available:
                        raise RuntimeError("解析失败：本地缓存路径无效")
                    image_files = []
                    if is_note and note_id:
                        display_url = f"https://www.douyin.com/note/{note_id}"
                    else:
                        display_url = url
                    # 优先检查是否开启预下载，避免重复下载
                    if self.pre_download_all_media and self.cache_dir_available:
                        if is_note and note_id:
                            page_referer = f"https://www.douyin.com/note/{note_id}"
                        else:
                            page_referer = url
                        media_items = []
                        for idx, img_url in enumerate(images):
                            if img_url and isinstance(img_url, str) and img_url.startswith(('http://', 'https://')):
                                image_size = await self.get_image_size(img_url, session, headers=self.headers)
                                if self.max_media_size_mb > 0 and image_size is not None:
                                    if image_size > self.max_media_size_mb:
                                        continue
                                image_id = self._extract_media_id(url)
                                # 添加主URL
                                media_items.append({
                                    'url': img_url,
                                    'media_id': image_id,
                                    'index': idx,
                                    'is_video': False,
                                    'headers': self.headers,
                                    'backup_urls': image_url_lists[idx][1:] if idx < len(image_url_lists) and image_url_lists[idx] else [],
                                    'referer': page_referer,
                                    'default_referer': 'https://www.douyin.com/'
                                })
                        if media_items:
                            # 先尝试预下载主URL
                            download_results = await self._pre_download_media(session, media_items, self.headers)
                            # 创建索引到媒体项的映射，以便正确匹配结果
                            index_to_item = {item.get('index', idx): item for idx, item in enumerate(media_items)}
                            # 按照 index 排序，确保按照原始顺序处理
                            download_results_sorted = sorted(download_results, key=lambda x: x.get('index', 0))
                            # 对于失败的下载，尝试备用URL
                            for download_result in download_results_sorted:
                                result_index = download_result.get('index', -1)
                                if download_result.get('success') and download_result.get('file_path'):
                                    file_path = download_result['file_path']
                                    image_files.append(file_path)
                                    downloaded_files.append(file_path)
                                else:
                                    # 主URL下载失败，尝试备用URL（使用带重试的下载方法）
                                    # 使用 index 字段来匹配对应的媒体项
                                    item = index_to_item.get(result_index)
                                    if item:
                                        backup_urls = item.get('backup_urls', [])
                                        if backup_urls:
                                            image_id = self._extract_media_id(url)
                                            cache_path = None
                                            # 尝试每个备用URL，使用带重试的下载方法
                                            for backup_url in backup_urls:
                                                if not backup_url or not isinstance(backup_url, str):
                                                    continue
                                                # 尝试下载到缓存目录
                                                cache_path = await self._download_image_with_retry(
                                                    session,
                                                    backup_url,
                                                    image_id,
                                                    item['index'],
                                                    item.get('referer'),
                                                    max_retries=2,
                                                    retry_delay=0.5,
                                                    to_cache=True
                                                )
                                                if cache_path:
                                                    downloaded_files.append(cache_path)
                                                    break
                                            if cache_path:
                                                image_files.append(cache_path)
                            if image_files:
                                images = []
                    else:
                        # 未开启预下载，使用原有逻辑
                        if is_note and note_id:
                            page_referer = f"https://www.douyin.com/note/{note_id}"
                        else:
                            page_referer = url
                        for idx, primary_url in enumerate(images):
                            if not primary_url or not isinstance(primary_url, str) or not primary_url.startswith(('http://', 'https://')):
                                continue
                            image_size = await self.get_image_size(primary_url, session, headers=self.headers)
                            if self.max_media_size_mb > 0 and image_size is not None:
                                if image_size > self.max_media_size_mb:
                                    continue
                            backup_urls = []
                            if idx < len(image_url_lists) and image_url_lists[idx]:
                                backup_urls = image_url_lists[idx][1:]
                            image_file = None
                            if self.large_media_threshold_mb > 0 and image_size is not None and image_size > self.large_media_threshold_mb:
                                if self.max_media_size_mb <= 0 or image_size <= self.max_media_size_mb:
                                    image_id = self._extract_media_id(url)
                                    # 先尝试主URL下载到缓存（使用与临时文件下载相同的逻辑）
                                    image_file = await self._download_large_media_to_cache(
                                        session,
                                        primary_url,
                                        image_id,
                                        index=idx,
                                        headers=self.headers,
                                        is_video=False,
                                        referer=page_referer,
                                        default_referer='https://www.douyin.com/'
                                    )
                                    # 如果主URL下载到缓存失败，尝试备用URL下载到缓存（使用与临时文件下载相同的逻辑）
                                    if not image_file and backup_urls:
                                        for backup_url in backup_urls:
                                            image_file = await self._download_large_media_to_cache(
                                                session,
                                                backup_url,
                                                image_id,
                                                index=idx,
                                                headers=self.headers,
                                                is_video=False,
                                                referer=page_referer,
                                                default_referer='https://www.douyin.com/'
                                            )
                                            if image_file:
                                                downloaded_files.append(image_file)
                                                break
                            if not image_file:
                                # 如果大图片下载到缓存都失败，降级为下载到临时文件（使用带重试的方法）
                                image_id = self._extract_media_id(url)
                                image_file = await self._download_image_with_retry(
                                    session, primary_url, image_id, idx, page_referer, max_retries=2, retry_delay=0.5, to_cache=False
                                )
                                if not image_file and backup_urls:
                                    for backup_url in backup_urls:
                                        image_file = await self._download_image_with_retry(
                                            session, backup_url, image_id, idx, page_referer, max_retries=2, retry_delay=0.5, to_cache=False
                                        )
                                        if image_file:
                                            downloaded_files.append(image_file)
                                            break
                            if image_file:
                                image_files.append(image_file)
                    if not image_files:
                        # 清理已下载的文件
                        self._cleanup_files_list(downloaded_files)
                        return None
                    parse_result = {
                        "video_url": display_url,
                        "title": result.get('title', ''),
                        "author": result.get('author', result.get('nickname', '')),
                        "timestamp": result.get('timestamp', ''),
                        "thumb_url": result.get('thumb_url'),
                        "images": images,
                        "image_files": image_files,
                        "is_gallery": True
                    }
                    return parse_result
                video_url = result.get('video_url')
                if video_url:
                    page_referer = url if not is_note else (f"https://www.douyin.com/note/{note_id}" if note_id else url)
                    video_size = await self.get_video_size(video_url, session, referer=page_referer)
                    if self.max_media_size_mb > 0 and video_size is not None:
                        if video_size > self.max_media_size_mb:
                            # 清理已下载的文件
                            self._cleanup_files_list(downloaded_files)
                            return None
                    has_large_video = False
                    video_file_path = None
                    if self.large_media_threshold_mb > 0 and video_size is not None and video_size > self.large_media_threshold_mb:
                        if not self.cache_dir_available:
                            # 清理已下载的文件
                            self._cleanup_files_list(downloaded_files)
                            raise RuntimeError("解析失败：本地缓存路径无效")
                        if self.max_media_size_mb <= 0 or video_size <= self.max_media_size_mb:
                            has_large_video = True
                            video_id = self._extract_media_id(url)
                            video_file_path = await self._download_large_media_to_cache(
                                session,
                                video_url,
                                video_id,
                                index=0,
                                headers=self.headers,
                                is_video=True,
                                referer=url
                            )
                            if video_file_path:
                                downloaded_files.append(video_file_path)
                    parse_result = {
                        "video_url": url,
                        "title": result.get('title', ''),
                        "author": result.get('author', result.get('nickname', '')),
                        "timestamp": result.get('timestamp', ''),
                        "thumb_url": result.get('thumb_url'),
                        "direct_url": video_url,
                        "file_size_mb": video_size
                    }
                    if has_large_video:
                        parse_result['force_separate_send'] = True
                        parse_result['has_large_video'] = True
                        if video_file_path:
                            parse_result['video_files'] = [{'file_path': video_file_path}]
                    # 预先下载所有媒体
                    if self.pre_download_all_media and self.cache_dir_available:
                        media_items = []
                        if video_url and not has_large_video:
                            video_id = self._extract_media_id(url)
                            media_items.append({
                                'url': video_url,
                                'media_id': video_id,
                                'index': 0,
                                'is_video': True,
                                'headers': self.headers,
                                'referer': url
                            })
                        if media_items:
                            download_results = await self._pre_download_media(session, media_items, self.headers)
                            for download_result in download_results:
                                if download_result.get('success') and download_result.get('file_path'):
                                    file_path = download_result['file_path']
                                    parse_result['video_files'] = [{'file_path': file_path}]
                                    parse_result['direct_url'] = None
                                    downloaded_files.append(file_path)
                                    break
                    return parse_result
                # 清理已下载的文件
                self._cleanup_files_list(downloaded_files)
                return None
            except Exception:
                # 发生异常，清理所有已下载的文件
                self._cleanup_files_list(downloaded_files)
                return None
    
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
        优先使用下载的图片文件而不是URL，以避免QQ/NapCat无法识别文件类型的问题
        如果解析结果中有 video_files（大视频已下载到缓存目录），优先使用文件方式构建节点
        Args:
            result: 解析结果
            sender_name: 发送者名称
            sender_id: 发送者ID
            is_auto_pack: 是否打包为Node
        Returns:
            List: 媒体节点列表
        """
        nodes = []
        if result.get('video_files'):
            return self._build_video_gallery_nodes_from_files(
                result['video_files'],
                sender_name,
                sender_id,
                is_auto_pack
            )
        if result.get('is_gallery') and result.get('image_files'):
            gallery_nodes = self._build_gallery_nodes_from_files(
                result['image_files'],
                sender_name,
                sender_id,
                is_auto_pack
            )
            nodes.extend(gallery_nodes)
        elif result.get('is_gallery') and result.get('images'):
            gallery_nodes = self._build_gallery_nodes_from_urls(
                result['images'],
                sender_name,
                sender_id,
                is_auto_pack
            )
            nodes.extend(gallery_nodes)
        elif result.get('direct_url'):
            video_node = self._build_video_node_from_url(
                result['direct_url'],
                sender_name,
                sender_id,
                is_auto_pack,
                result.get('thumb_url')
            )
            if video_node:
                nodes.append(video_node)
        return nodes
