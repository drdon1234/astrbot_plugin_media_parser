# -*- coding: utf-8 -*-
"""
下载管理器
负责管理下载流程，检查配置项，确定使用网络直链还是本地文件
"""
import asyncio
import hashlib
import os
import re
from typing import Dict, Any, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp

from astrbot.api import logger

from .downloader import (
    get_video_size,
    download_media_to_cache,
    pre_download_media,
    validate_media_url,
    download_image_to_file
)
from .file_manager import (
    check_cache_dir_available,
    cleanup_files
)
from .constants import Config


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
            large_video_threshold_mb: 大视频阈值(MB)，超过此大小将单独发送
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

    async def _download_one_image(
        self,
        session: aiohttp.ClientSession,
        url_list: List[str],
        img_idx: int,
        headers: dict = None,
        referer: str = None,
        default_referer: str = None,
        proxy: str = None
    ) -> Optional[str]:
        """下载单个图片，遍历URL列表，每个URL只尝试一次

        Args:
            session: aiohttp会话
            url_list: 图片URL列表
            img_idx: 图片索引
            headers: 请求头（可选）
            referer: Referer URL（可选）
            default_referer: 默认Referer URL（可选）
            proxy: 代理地址（可选）

        Returns:
            临时文件路径，失败返回None
        """
        if not url_list or not isinstance(url_list, list):
            return None
        
        for url in url_list:
            temp_path = await download_image_to_file(
                session,
                url,
                index=img_idx,
                headers=headers,
                referer=referer,
                default_referer=default_referer,
                proxy=proxy
            )
            if temp_path:
                return temp_path
        
        return None

    async def _download_images(
        self,
        session: aiohttp.ClientSession,
        image_urls: List[List[str]],
        has_valid_images: bool,
        headers: dict = None,
        referer: str = None,
        default_referer: str = None,
        proxy: str = None
    ) -> Tuple[List[Optional[str]], int]:
        """下载所有图片到临时文件

        Args:
            session: aiohttp会话
            image_urls: 图片URL列表（二维列表）
            has_valid_images: 是否有有效的图片
            headers: 请求头（可选）
            referer: Referer URL（可选）
            default_referer: 默认Referer URL（可选）
            proxy: 代理地址（可选）

        Returns:
            (image_file_paths, failed_image_count) 元组
        """
        image_file_paths = []
        failed_image_count = 0

        if image_urls and has_valid_images:
            tasks = [
                self._download_one_image(
                    session, url_list, idx,
                    headers, referer, default_referer, proxy
                )
                for idx, url_list in enumerate(image_urls)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

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

    def _build_media_items(
        self,
        metadata: Dict[str, Any],
        media_id: str,
        headers: dict = None,
        referer: str = None,
        proxy: str = None
    ) -> List[Dict[str, Any]]:
        """构建媒体项列表

        Args:
            metadata: 元数据字典
            media_id: 媒体ID
            headers: 请求头（可选）
            referer: Referer URL（可选）
            proxy: 代理地址（可选）

        Returns:
            媒体项列表，每个项包含url_list（URL列表）、media_id、index、is_video等字段
        """
        media_items = []
        video_urls = metadata.get('video_urls', [])
        image_urls = metadata.get('image_urls', [])
        
        idx = 0
        for url_list in video_urls:
            if url_list and isinstance(url_list, list):
                media_items.append({
                    'url_list': url_list,
                    'media_id': media_id,
                    'index': idx,
                    'is_video': True,
                    'headers': headers,
                    'referer': referer,
                    'default_referer': referer,
                    'proxy': proxy
                })
                idx += 1
        
        for url_list in image_urls:
            if url_list and isinstance(url_list, list):
                media_items.append({
                    'url_list': url_list,
                    'media_id': media_id,
                    'index': idx,
                    'is_video': False,
                    'headers': headers,
                    'referer': referer,
                    'default_referer': referer,
                    'proxy': proxy
                })
                idx += 1
        
        return media_items

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
        file_paths = []
        failed_video_count = 0
        failed_image_count = 0
        
        result_idx = 0
        for url_list in video_urls:
            if result_idx < len(download_results):
                result = download_results[result_idx]
                if result.get('success') and result.get('file_path'):
                    file_paths.append(result['file_path'])
                else:
                    file_paths.append(None)
                    failed_video_count += 1
                result_idx += 1
            else:
                file_paths.append(None)
                failed_video_count += 1
        
        for url_list in image_urls:
            if result_idx < len(download_results):
                result = download_results[result_idx]
                if result.get('success') and result.get('file_path'):
                    file_paths.append(result['file_path'])
                else:
                    file_paths.append(None)
                    failed_image_count += 1
                result_idx += 1
            else:
                file_paths.append(None)
                failed_image_count += 1
        
        return file_paths, failed_video_count, failed_image_count

    async def process_metadata(
        self,
        session: aiohttp.ClientSession,
        metadata: Dict[str, Any],
        headers: dict = None,
        referer: str = None,
        proxy: str = None
    ) -> Dict[str, Any]:
        """处理元数据，检查视频大小，确定使用网络直链还是本地文件

        Args:
            session: aiohttp会话
            metadata: 解析后的元数据
            headers: 请求头（可选）
            referer: Referer URL（可选）
            proxy: 代理地址（可选）

        Returns:
            处理后的元数据，包含视频大小信息和文件路径信息
        """
        if not metadata:
            return metadata

        url = metadata.get('url', '')
        video_urls = metadata.get('video_urls', [])
        image_urls = metadata.get('image_urls', [])

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
            async def get_video_size_task(url_list: List[str]) -> Tuple[Optional[float], Optional[int]]:
                """获取视频大小，尝试第一个URL"""
                if not url_list:
                    return None, None
                try:
                    return await get_video_size(session, url_list[0], headers, proxy)
                except Exception:
                    return None, None
            
            tasks = [
                get_video_size_task(url_list)
                for url_list in video_urls
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
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
            media_id = self._generate_media_id(url)
            media_items = self._build_media_items(
                metadata,
                media_id,
                headers,
                referer,
                proxy
            )

            download_results = await pre_download_media(
                session,
                media_items,
                self.cache_dir,
                self.max_concurrent_downloads
            )
            
            file_paths, failed_video_count, failed_image_count = self._process_download_results(
                download_results, video_urls, image_urls
            )
            
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

        async def get_video_size_task(url_list: List[str]) -> Tuple[Optional[float], Optional[int]]:
            """获取单个视频的大小，尝试第一个URL"""
            if not url_list:
                return None, None
            try:
                return await get_video_size(session, url_list[0], headers, proxy)
            except Exception:
                return None, None
        
        video_sizes = []
        video_has_access_denied = False
        if video_urls:
            tasks = [
                get_video_size_task(url_list)
                for url_list in video_urls
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
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
                    return await validate_media_url(
                        session, url_list[0], headers, proxy, is_video=False
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
            media_id = self._generate_media_id(url)
            video_media_items = []
            idx = 0
            for url_list in video_urls:
                if url_list and isinstance(url_list, list):
                    video_media_items.append({
                        'url_list': url_list,
                        'media_id': media_id,
                        'index': idx,
                        'is_video': True,
                        'headers': headers,
                        'referer': referer,
                        'default_referer': referer,
                        'proxy': proxy
                    })
                    idx += 1
            
            download_results = await pre_download_media(
                session,
                video_media_items,
                self.cache_dir,
                self.max_concurrent_downloads
            )
            
            video_file_paths = []
            failed_video_count = 0
            for result in download_results:
                if result.get('success') and result.get('file_path'):
                    video_file_paths.append(result['file_path'])
                else:
                    video_file_paths.append(None)
                    failed_video_count += 1
            
            while len(video_file_paths) < len(video_urls):
                video_file_paths.append(None)
                failed_video_count += 1
            
            page_url = metadata.get('page_url')
            default_referer = page_url if page_url else referer
            
            image_file_paths, failed_image_count = await self._download_images(
                session, image_urls, has_valid_images,
                headers, referer, default_referer, proxy
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
            page_url = metadata.get('page_url')
            default_referer = page_url if page_url else referer
            
            image_file_paths, failed_image_count = await self._download_images(
                session, image_urls, has_valid_images,
                headers, referer, default_referer, proxy
            )
            
            # 在这个分支中只处理图片，不处理视频（视频使用直链）
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

    def _generate_media_id(self, url: str) -> str:
        """根据URL生成媒体ID

        Args:
            url: 原始URL

        Returns:
            媒体ID
        """
        parsed = urlparse(url)
        path = parsed.path.strip('/')
        
        id_match = re.search(r'/(\d+)', path)
        if id_match:
            return id_match.group(1)
        
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        return url_hash

    async def process_metadata_list(
        self,
        session: aiohttp.ClientSession,
        metadata_list: List[Dict[str, Any]],
        headers: dict = None,
        referer: str = None,
        proxy: str = None
    ) -> List[Dict[str, Any]]:
        """处理元数据列表

        Args:
            session: aiohttp会话
            metadata_list: 解析后的元数据列表
            headers: 请求头（可选）
            referer: Referer URL（可选）
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
                    headers,
                    referer,
                    proxy
                )
                processed_metadata.append(processed)
            except Exception as e:
                logger.exception(f"处理元数据失败: {metadata.get('url', '')}, 错误: {e}")
                metadata['error'] = str(e)
                processed_metadata.append(metadata)
        return processed_metadata

