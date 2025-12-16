# -*- coding: utf-8 -*-
"""
下载管理器
负责管理下载流程，检查配置项，确定使用网络直链还是本地文件
"""
import asyncio
import hashlib
import os
import re
import time
from typing import Dict, Any, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from .utils import check_cache_dir_available
from .validator import get_video_size, validate_media_url
from .handler import (
    pre_download_videos,
    pre_download_media
)
from .router import download_media
from ..file_cleaner import cleanup_files
from ..constants import Config


class DownloadManager:
    """下载管理器，负责管理视频下载流程"""

    def __init__(
        self,
        max_video_size_mb: float = 0.0,
        large_video_threshold_mb: float = Config.DEFAULT_LARGE_VIDEO_THRESHOLD_MB,
        cache_dir: str = "/app/sharedFolder/video_parser/cache",
        pre_download_all_media: bool = False,
        max_concurrent_downloads: int = 3
    ):
        """初始化下载管理器

        Args:
            max_video_size_mb: 最大允许的视频大小(MB)，0表示不限制
            large_video_threshold_mb: 大视频阈值(MB)，超过此大小将单独发送。
                当设置为0时，所有视频都使用直链，不进行本地下载（与max_video_size_mb=0时的行为类似）
            cache_dir: 视频缓存目录
            pre_download_all_media: 是否预先下载所有媒体到本地
            max_concurrent_downloads: 最大并发下载数
        """
        self.max_video_size_mb = max_video_size_mb
        if large_video_threshold_mb > 0:
            self.large_video_threshold_mb = min(
                large_video_threshold_mb,
                Config.MAX_LARGE_VIDEO_THRESHOLD_MB
            )
        else:
            self.large_video_threshold_mb = 0.0
        self.cache_dir = cache_dir
        self.pre_download_all_media = pre_download_all_media
        self.max_concurrent_downloads = max_concurrent_downloads
        self.cache_dir_available = check_cache_dir_available(cache_dir)
        if self.cache_dir_available and cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
        
        self._active_sessions: List[aiohttp.ClientSession] = []
        self._active_tasks: List[asyncio.Task] = []
        self._shutting_down = False

    def _get_effective_proxy(
        self,
        metadata: Dict[str, Any],
        proxy_addr: str = None,
        use_proxy_flag: bool = False
    ) -> Optional[str]:
        """获取有效的代理地址
        
        优先级：元数据中的 proxy_url > 函数参数 proxy_addr
        
        Args:
            metadata: 元数据字典
            proxy_addr: 函数参数中的代理地址（可选）
            use_proxy_flag: 是否使用代理的标志（如 use_image_proxy, use_video_proxy）
            
        Returns:
            有效的代理地址，如果不需要代理返回None
        """
        if not use_proxy_flag:
            return None
        return metadata.get('proxy_url') or proxy_addr

    async def _download_one_image(
        self,
        session: aiohttp.ClientSession,
        url_list: List[str],
        img_idx: int,
        metadata: Dict[str, Any],
        proxy_addr: str = None
    ) -> Optional[str]:
        """下载单个图片，遍历URL列表，每个URL只尝试一次

        Args:
            session: aiohttp会话
            url_list: 图片URL列表
            img_idx: 图片索引
            metadata: 元数据字典（用于获取 header 参数）
            proxy_addr: 代理地址（可选）

        Returns:
            临时文件路径，失败返回None
        """
        if not url_list or not isinstance(url_list, list):
            return None
        
        headers = metadata.get('image_headers', {})
        use_image_proxy = metadata.get('use_image_proxy', False)
        proxy = self._get_effective_proxy(metadata, proxy_addr, use_image_proxy)
        
        for url in url_list:
            result = await download_media(
                session,
                url,
                media_type=None,
                cache_dir=None,
                media_id='image',
                index=img_idx,
                headers=headers,
                proxy=proxy
            )
            if result and result.get('file_path'):
                return result.get('file_path')
        
        return None

    async def _download_images(
        self,
        session: aiohttp.ClientSession,
        image_urls: List[List[str]],
        has_valid_images: bool,
        metadata: Dict[str, Any],
        proxy_addr: str = None
    ) -> Tuple[List[Optional[str]], int]:
        """下载所有图片到临时文件

        Args:
            session: aiohttp会话
            image_urls: 图片URL列表（二维列表）
            has_valid_images: 是否有有效的图片
            metadata: 元数据字典（用于获取 header 参数）
            proxy_addr: 代理地址（可选）

        Returns:
            (image_file_paths, failed_image_count) 元组
        """
        image_file_paths = []
        failed_image_count = 0

        if image_urls and has_valid_images:
            if self._shutting_down:
                return image_file_paths, len(image_urls)
            
            coros = [
                self._download_one_image(
                    session, url_list, idx, metadata, proxy_addr
                )
                for idx, url_list in enumerate(image_urls)
            ]
            tasks = [asyncio.create_task(coro) for coro in coros]
            self._active_tasks.extend(tasks)
            
            try:
                results = await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                for task in tasks:
                    if task in self._active_tasks:
                        self._active_tasks.remove(task)

            for result in results:
                if isinstance(result, Exception):
                    image_file_paths.append(None)
                    failed_image_count += 1
                elif isinstance(result, str) and result:
                    image_file_paths.append(result)
                else:
                    image_file_paths.append(None)
                    failed_image_count += 1
        else:
            if image_urls:
                failed_image_count = len(image_urls)

        return image_file_paths, failed_image_count


    async def _get_video_size_task(
        self,
        session: aiohttp.ClientSession,
        url_list: List[str],
        metadata: Dict[str, Any],
        proxy_addr: str = None
    ) -> Tuple[Optional[float], Optional[int]]:
        """获取视频大小任务（异步函数）
        
        Args:
            session: aiohttp会话
            url_list: 视频URL列表
            metadata: 元数据字典（用于获取 header 参数）
            proxy_addr: 代理地址（可选）
            
        Returns:
            (size_mb, status_code) 元组
        """
        if not url_list:
            return None, None
        try:
            headers = metadata.get('video_headers', {})
            use_video_proxy = metadata.get('use_video_proxy', False)
            proxy = self._get_effective_proxy(metadata, proxy_addr, use_video_proxy)
            return await get_video_size(session, url_list[0], headers, proxy)
        except Exception:
            return None, None

    def _build_media_items(
        self,
        metadata: Dict[str, Any],
        media_id: str,
        proxy_addr: str = None
    ) -> List[Dict[str, Any]]:
        """构建媒体项列表

        Args:
            metadata: 元数据字典（应包含 image_headers, video_headers 字段）
            media_id: 媒体ID
            proxy_addr: 代理地址（可选，优先级低于元数据中的 proxy_url）

        Returns:
            媒体项列表，每个项包含url_list（URL列表）、media_id、index、is_video、headers等字段
        """
        media_items = []
        video_urls = metadata.get('video_urls', [])
        image_urls = metadata.get('image_urls', [])
        
        use_image_proxy = metadata.get('use_image_proxy', False)
        use_video_proxy = metadata.get('use_video_proxy', False)
        effective_proxy_addr = self._get_effective_proxy(metadata, proxy_addr, True) or proxy_addr
        
        image_headers = metadata.get('image_headers', {})
        video_headers = metadata.get('video_headers', {})
        
        idx = 0
        for url_list in video_urls:
            if url_list and isinstance(url_list, list):
                item_proxy = effective_proxy_addr if use_video_proxy else None
                media_items.append({
                    'url_list': url_list,
                    'media_id': media_id,
                    'index': idx,
                    'is_video': True,
                    'headers': video_headers,
                    'proxy': item_proxy
                })
                idx += 1
        
        for url_list in image_urls:
            if url_list and isinstance(url_list, list):
                item_proxy = effective_proxy_addr if use_image_proxy else None
                media_items.append({
                    'url_list': url_list,
                    'media_id': media_id,
                    'index': idx,
                    'is_video': False,
                    'headers': image_headers,
                    'proxy': item_proxy
                })
                idx += 1
        
        return media_items

    def _process_single_type_results(
        self,
        download_results: List[Dict[str, Any]],
        expected_count: int,
        start_idx: int = 0
    ) -> Tuple[List[Optional[str]], int]:
        """处理单一类型的下载结果（视频或图片）

        Args:
            download_results: 下载结果列表
            expected_count: 期望的结果数量
            start_idx: 开始索引（用于处理部分结果）

        Returns:
            (file_paths, failed_count) 元组
        """
        file_paths = []
        failed_count = 0
        
        for idx in range(expected_count):
            result_idx = start_idx + idx
            if result_idx < len(download_results):
                result = download_results[result_idx]
                if result.get('success') and result.get('file_path'):
                    file_paths.append(result['file_path'])
                else:
                    file_paths.append(None)
                    failed_count += 1
            else:
                file_paths.append(None)
                failed_count += 1
        
        return file_paths, failed_count

    def _process_download_results(
        self,
        download_results: List[Dict[str, Any]],
        video_urls: List[List[str]],
        image_urls: List[List[str]]
    ) -> Tuple[List[Optional[str]], int, int]:
        """处理下载结果，构建文件路径列表并统计失败数量

        Args:
            download_results: 下载结果列表
            video_urls: 视频URL列表（二维列表）
            image_urls: 图片URL列表（二维列表）

        Returns:
            (file_paths, failed_video_count, failed_image_count) 元组
        """
        video_file_paths, failed_video_count = self._process_single_type_results(
            download_results, len(video_urls), start_idx=0
        )
        image_file_paths, failed_image_count = self._process_single_type_results(
            download_results, len(image_urls), start_idx=len(video_urls)
        )
        
        return video_file_paths + image_file_paths, failed_video_count, failed_image_count

    async def process_metadata(
        self,
        session: aiohttp.ClientSession,
        metadata: Dict[str, Any],
        proxy_addr: str = None
    ) -> Dict[str, Any]:
        """处理元数据，检查视频大小，确定使用网络直链还是本地文件

        Args:
            session: aiohttp会话
            metadata: 解析后的元数据（应包含 image_headers, video_headers 字段）
            proxy_addr: 代理地址（可选，用于 Twitter 等需要代理的平台）

        Returns:
            处理后的元数据，包含视频大小信息和文件路径信息
        """
        if self._shutting_down:
            return metadata
        
        if session not in self._active_sessions:
            self._active_sessions.append(session)
        
        if not metadata:
            return metadata

        url = metadata.get('url', '')
        video_urls = metadata.get('video_urls', [])
        image_urls = metadata.get('image_urls', [])
        
        if 'image_headers' not in metadata:
            metadata['image_headers'] = {}
        if 'video_headers' not in metadata:
            metadata['video_headers'] = {}
        
        image_pre_download = metadata.get('image_pre_download', False)
        video_pre_download = metadata.get('video_pre_download', False)
        
        logger.debug(
            f"处理元数据: {url}, "
            f"image_pre_download={image_pre_download}, "
            f"video_pre_download={video_pre_download}, "
            f"pre_download_all_media={self.pre_download_all_media}"
        )
        
        if image_pre_download and not self.pre_download_all_media:
            logger.debug(f"图片要求预下载但未启用预下载，跳过所有图片: {url}")
            image_urls = []
            metadata['image_urls'] = []
        
        if video_pre_download and not self.pre_download_all_media:
            logger.debug(f"视频要求预下载但未启用预下载，跳过所有视频: {url}")
            video_urls = []
            metadata['video_urls'] = []

        if not video_urls and not image_urls:
            metadata['has_valid_media'] = False
            metadata['video_count'] = 0
            metadata['image_count'] = 0
            metadata['failed_video_count'] = 0
            metadata['failed_image_count'] = 0
            metadata['file_paths'] = []
            return metadata

        video_count = len(video_urls)
        image_count = len(image_urls)
        
        pre_check_video_sizes = None
        if video_urls and self.max_video_size_mb > 0:
            logger.debug(f"开始检查视频大小: {url}, 视频数量: {len(video_urls)}")
            
            if self._shutting_down:
                video_sizes = [None] * len(video_urls)
            else:
                coros = [
                    self._get_video_size_task(session, url_list, metadata, proxy_addr)
                    for url_list in video_urls
                ]
                tasks = [asyncio.create_task(coro) for coro in coros]
                self._active_tasks.extend(tasks)
                
                try:
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                finally:
                    for task in tasks:
                        if task in self._active_tasks:
                            self._active_tasks.remove(task)
                
                video_sizes = []
                for result in results:
                    if isinstance(result, Exception):
                        video_sizes.append(None)
                    elif isinstance(result, tuple) and len(result) == 2:
                        size, _ = result
                        video_sizes.append(size)
                    elif isinstance(result, (int, float)) or result is None:
                        video_sizes.append(result)
                    else:
                        video_sizes.append(None)
            
            valid_sizes = [s for s in video_sizes if s is not None]
            if valid_sizes:
                max_video_size = max(valid_sizes)
                if max_video_size > self.max_video_size_mb:
                    logger.warning(
                        f"视频大小超过限制: {max_video_size:.2f}MB > {self.max_video_size_mb}MB, "
                        f"URL: {url}，跳过下载"
                    )
                    metadata['exceeds_max_size'] = True
                    metadata['has_valid_media'] = False
                    metadata['video_sizes'] = video_sizes
                    metadata['max_video_size_mb'] = max_video_size
                    metadata['total_video_size_mb'] = sum(valid_sizes) if valid_sizes else 0.0
                    metadata['video_count'] = video_count
                    metadata['image_count'] = image_count
                    metadata['failed_video_count'] = video_count
                    metadata['failed_image_count'] = image_count
                    metadata['file_paths'] = []
                    metadata['use_local_files'] = False
                    metadata['is_large_media'] = False
                    return metadata
                pre_check_video_sizes = video_sizes
        
        if self.pre_download_all_media and self.cache_dir_available:
            logger.debug(f"开始预下载所有媒体: {url}, 视频: {len(video_urls)}, 图片: {len(image_urls)}")
            media_id = self._generate_media_id(url, metadata)
            media_items = self._build_media_items(
                metadata,
                media_id,
                proxy_addr
            )
            logger.debug(f"构建了 {len(media_items)} 个媒体项")

            download_results = await pre_download_media(
                session,
                media_items,
                self.cache_dir,
                self.max_concurrent_downloads
            )
            logger.debug(f"预下载完成: {url}, 成功: {sum(1 for r in download_results if r.get('success'))}/{len(download_results)}")
            
            file_paths, failed_video_count, failed_image_count = self._process_download_results(
                download_results, video_urls, image_urls
            )
            
            original_video_count = len(video_urls)
            original_image_count = len(image_urls)
            
            if video_pre_download:
                video_results = download_results[:original_video_count] if original_video_count > 0 else []
                all_video_failed = all(not result.get('success') for result in video_results) if video_results else False
                if all_video_failed and original_video_count > 0:
                    logger.debug(f"视频要求预下载但全部失败，跳过所有视频: {url}")
                    video_urls = []
                    metadata['video_urls'] = []
                    for idx in range(original_video_count):
                        if idx < len(file_paths):
                            file_paths[idx] = None
                    failed_video_count = original_video_count
            
            if image_pre_download:
                image_results = download_results[original_video_count:] if original_video_count < len(download_results) else []
                all_image_failed = all(not result.get('success') for result in image_results) if image_results else False
                if all_image_failed and original_image_count > 0:
                    logger.debug(f"图片要求预下载但全部失败，跳过所有图片: {url}")
                    image_urls = []
                    metadata['image_urls'] = []
                    for idx in range(original_image_count):
                        file_idx = original_video_count + idx
                        if file_idx < len(file_paths):
                            file_paths[file_idx] = None
                    failed_image_count = original_image_count
            
            metadata['file_paths'] = file_paths
            metadata['failed_video_count'] = failed_video_count
            metadata['failed_image_count'] = failed_image_count
            
            if video_urls:
                video_sizes = []
                for idx, result in enumerate(download_results[:len(video_urls)]):
                    if result.get('success') and result.get('size_mb') is not None:
                        video_sizes.append(result.get('size_mb'))
                    elif pre_check_video_sizes and idx < len(pre_check_video_sizes):
                        video_sizes.append(pre_check_video_sizes[idx])
                    else:
                        video_sizes.append(None)
                
                valid_sizes = [s for s in video_sizes if s is not None]
                max_video_size = max(valid_sizes) if valid_sizes else None
                total_video_size = sum(valid_sizes) if valid_sizes else 0.0
                
                metadata['video_sizes'] = video_sizes
                metadata['max_video_size_mb'] = max_video_size
                metadata['total_video_size_mb'] = total_video_size
                
                if self.max_video_size_mb > 0 and max_video_size is not None:
                    if max_video_size > self.max_video_size_mb:
                        logger.warning(
                            f"视频大小超过限制: {max_video_size:.2f}MB > {self.max_video_size_mb}MB, "
                            f"URL: {url}"
                        )
                        cleanup_files(file_paths)
                        metadata['exceeds_max_size'] = True
                        metadata['has_valid_media'] = False
                        metadata['use_local_files'] = False
                        metadata['file_paths'] = []
                        return metadata
            else:
                metadata['video_sizes'] = []
                metadata['max_video_size_mb'] = None
                metadata['total_video_size_mb'] = 0.0
            
            has_valid_media = any(
                result.get('success') and result.get('file_path')
                for result in download_results
            )
            
            metadata['has_valid_media'] = has_valid_media
            metadata['use_local_files'] = has_valid_media
            metadata['video_count'] = video_count
            metadata['image_count'] = image_count
            metadata['exceeds_max_size'] = False
            metadata['is_large_media'] = False
            
            return metadata

        logger.debug(f"使用直链模式处理媒体: {url}, 视频: {len(video_urls)}, 图片: {len(image_urls)}")
        video_sizes = []
        video_has_access_denied = False
        if video_urls:
            if self._shutting_down:
                video_sizes = [None] * len(video_urls)
            else:
                coros = [
                    self._get_video_size_task(session, url_list, metadata, proxy_addr)
                    for url_list in video_urls
                ]
                tasks = [asyncio.create_task(coro) for coro in coros]
                self._active_tasks.extend(tasks)
                
                try:
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                finally:
                    for task in tasks:
                        if task in self._active_tasks:
                            self._active_tasks.remove(task)
                
                for result in results:
                    if isinstance(result, Exception):
                        video_sizes.append(None)
                        if '403' in str(result) or 'Forbidden' in str(result):
                            video_has_access_denied = True
                    elif isinstance(result, tuple) and len(result) == 2:
                        size, status_code = result
                        video_sizes.append(size)
                        if status_code == 403:
                            video_has_access_denied = True
                    elif isinstance(result, (int, float)) or result is None:
                        video_sizes.append(result)
                    else:
                        video_sizes.append(None)
        else:
            video_sizes = []

        valid_sizes = [s for s in video_sizes if s is not None]
        max_video_size = max(valid_sizes) if valid_sizes else None
        total_video_size = sum(valid_sizes) if valid_sizes else 0.0

        metadata['video_sizes'] = video_sizes
        metadata['max_video_size_mb'] = max_video_size
        metadata['total_video_size_mb'] = total_video_size
        metadata['video_count'] = video_count
        metadata['image_count'] = image_count

        has_valid_videos = len(valid_sizes) > 0
        
        has_valid_images = False
        has_access_denied = False
        if image_urls:
            async def validate_image_task(url_list: List[str]) -> Tuple[bool, Optional[int]]:
                """验证图片URL列表，尝试第一个URL"""
                if not url_list:
                    return False, None
                try:
                    image_headers = metadata.get('image_headers', {})
                    use_image_proxy = metadata.get('use_image_proxy', False)
                    image_proxy = self._get_effective_proxy(metadata, proxy_addr, use_image_proxy)
                    return await validate_media_url(
                        session, url_list[0], image_headers, image_proxy, is_video=False
                    )
                except Exception:
                    return False, None
            
            tasks = [validate_image_task(url_list) for url_list in image_urls]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    continue
                if isinstance(r, tuple) and len(r) == 2:
                    is_valid, status_code = r
                    if is_valid:
                        has_valid_images = True
                    elif status_code == 403:
                        has_access_denied = True
                elif isinstance(r, bool) and r:
                    has_valid_images = True
        
        has_valid_media = has_valid_videos or has_valid_images
        metadata['has_valid_media'] = has_valid_media
        metadata['has_access_denied'] = has_access_denied or video_has_access_denied
        
        if not has_valid_media:
            metadata['exceeds_max_size'] = False
            metadata['file_paths'] = []
            metadata['use_local_files'] = False
            metadata['is_large_media'] = False
            metadata['failed_video_count'] = video_count
            metadata['failed_image_count'] = image_count
            return metadata

        if self.max_video_size_mb > 0 and max_video_size is not None:
            if max_video_size > self.max_video_size_mb:
                logger.warning(
                    f"视频大小超过限制: {max_video_size:.2f}MB > {self.max_video_size_mb}MB, "
                    f"URL: {url}"
                )
                metadata['exceeds_max_size'] = True
                metadata['has_valid_media'] = False
                metadata['max_video_size_mb'] = max_video_size
                metadata['failed_video_count'] = video_count
                metadata['failed_image_count'] = image_count
                return metadata

        metadata['exceeds_max_size'] = False

        needs_download = False
        if self.large_video_threshold_mb > 0 and max_video_size is not None:
            if max_video_size > self.large_video_threshold_mb:
                needs_download = True

        if needs_download and self.cache_dir_available:
            logger.debug(f"大视频需要下载到缓存: {url}, 视频数量: {len(video_urls)}")
            media_id = self._generate_media_id(url, metadata)
            all_media_items = self._build_media_items(
                metadata,
                media_id,
                proxy_addr
            )
            video_media_items = [item for item in all_media_items if item.get('is_video')]
            
            download_results = await pre_download_videos(
                session,
                video_media_items,
                self.cache_dir,
                self.max_concurrent_downloads
            )
            logger.debug(f"大视频下载完成: {url}, 成功: {sum(1 for r in download_results if r.get('success'))}/{len(download_results)}")
            
            video_file_paths, failed_video_count = self._process_single_type_results(
                download_results, len(video_urls), start_idx=0
            )
            
            image_file_paths, failed_image_count = await self._download_images(
                session, image_urls, has_valid_images,
                metadata, proxy_addr
            )
            
            file_paths = video_file_paths + image_file_paths
            
            if video_urls and self.max_video_size_mb > 0:
                download_video_sizes = []
                for idx, result in enumerate(download_results[:len(video_urls)]):
                    if result.get('success') and result.get('size_mb') is not None:
                        download_video_sizes.append(result.get('size_mb'))
                    elif idx < len(video_sizes):
                        download_video_sizes.append(video_sizes[idx])
                    else:
                        download_video_sizes.append(None)
                
                valid_download_sizes = [s for s in download_video_sizes if s is not None]
                if valid_download_sizes:
                    actual_max_video_size = max(valid_download_sizes)
                    if actual_max_video_size > self.max_video_size_mb:
                        logger.warning(
                            f"视频大小超过限制: "
                            f"{actual_max_video_size:.2f}MB > {self.max_video_size_mb}MB, "
                            f"URL: {url}，清理已下载的文件"
                        )
                        cleanup_files(file_paths)
                        metadata['exceeds_max_size'] = True
                        metadata['has_valid_media'] = False
                        metadata['use_local_files'] = False
                        metadata['file_paths'] = []
                        metadata['is_large_media'] = False
                        metadata['video_sizes'] = download_video_sizes
                        metadata['max_video_size_mb'] = actual_max_video_size
                        metadata['failed_video_count'] = video_count
                        metadata['failed_image_count'] = image_count
                        return metadata
                    metadata['video_sizes'] = download_video_sizes
                    metadata['max_video_size_mb'] = actual_max_video_size
                    metadata['total_video_size_mb'] = sum(valid_download_sizes)
            
            has_valid_video_downloads = any(
                result.get('success') and result.get('file_path')
                for result in download_results
            )
            has_valid_image_downloads = any(fp for fp in image_file_paths if fp)
            has_valid_media = has_valid_video_downloads or has_valid_image_downloads
            
            metadata['file_paths'] = file_paths
            metadata['use_local_files'] = has_valid_media
            metadata['is_large_media'] = True
            metadata['failed_video_count'] = failed_video_count
            metadata['failed_image_count'] = failed_image_count
        else:
            image_file_paths, failed_image_count = await self._download_images(
                session, image_urls, has_valid_images,
                metadata, proxy_addr
            )
            
            file_paths = image_file_paths
            
            has_successful_downloads = any(fp for fp in image_file_paths if fp)
            
            metadata['file_paths'] = file_paths
            metadata['use_local_files'] = has_successful_downloads
            metadata['is_large_media'] = False
            failed_video_count = (
                sum(1 for size in video_sizes if size is None)
                if video_sizes else 0
            )
            metadata['failed_video_count'] = failed_video_count
            metadata['failed_image_count'] = failed_image_count
            
            has_valid_media = has_valid_videos or has_successful_downloads
            metadata['has_valid_media'] = has_valid_media

        return metadata

    def _generate_media_id(self, url: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """根据URL生成媒体目录名，格式：{platform}_{url_hash}_{timestamp}

        Args:
            url: 原始URL
            metadata: 元数据字典（可选），应包含platform字段

        Returns:
            媒体目录名
        """
        platform = 'unknown'
        if metadata and 'platform' in metadata:
            platform = metadata.get('platform')
        else:
            logger.warning(
                f"metadata中缺少platform字段，URL: {url}，"
                f"将使用'unknown'作为平台标识"
            )
        
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        timestamp = int(time.time())
        return f"{platform}_{url_hash}_{timestamp}"

    async def process_metadata_list(
        self,
        session: aiohttp.ClientSession,
        metadata_list: List[Dict[str, Any]],
        proxy: str = None
    ) -> List[Dict[str, Any]]:
        """处理元数据列表

        Args:
            session: aiohttp会话
            metadata_list: 解析后的元数据列表
            proxy: 代理地址（可选）

        Returns:
            处理后的元数据列表
        """
        processed_metadata = []
        for metadata in metadata_list:
            try:
                processed = await self.process_metadata(
                    session,
                    metadata,
                    proxy
                )
                processed_metadata.append(processed)
            except Exception as e:
                logger.exception(f"处理元数据失败: {metadata.get('url', '')}, 错误: {e}")
                metadata['error'] = str(e)
                processed_metadata.append(metadata)
        return processed_metadata

    async def shutdown(self):
        """关闭所有活动的下载任务和会话
        
        终止所有正在进行的下载任务，关闭所有活动的 aiohttp 会话
        """
        self._shutting_down = True
        
        for session in self._active_sessions:
            if not session.closed:
                await session.close()
        self._active_sessions.clear()
        
        for task in self._active_tasks:
            if not task.done():
                task.cancel()
        
        if self._active_tasks:
            await asyncio.gather(*self._active_tasks, return_exceptions=True)
        self._active_tasks.clear()

