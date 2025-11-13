# -*- coding: utf-8 -*-
"""
下载管理器
负责管理下载流程，检查配置项，确定使用网络直链还是本地文件
"""
from typing import Dict, Any, List, Optional
import asyncio

import aiohttp

from astrbot.api import logger

from .downloader import (
    get_video_size,
    download_media_to_cache,
    download_image_to_file,
    pre_download_media
)
from .file_manager import (
    check_cache_dir_available,
    move_temp_file_to_cache,
    cleanup_files
)


class DownloadManager:
    """下载管理器，负责管理视频下载流程。"""

    def __init__(
        self,
        max_video_size_mb: float = 0.0,
        large_video_threshold_mb: float = 50.0,
        cache_dir: str = "/app/sharedFolder/video_parser/cache",
        pre_download_all_media: bool = False,
        max_concurrent_downloads: int = 3
    ):
        """初始化下载管理器。

        Args:
            max_video_size_mb: 最大允许的视频大小(MB)，0表示不限制
            large_video_threshold_mb: 大视频阈值(MB)，超过此大小将单独发送
            cache_dir: 视频缓存目录
            pre_download_all_media: 是否预先下载所有媒体到本地
            max_concurrent_downloads: 最大并发下载数
        """
        self.max_video_size_mb = max_video_size_mb
        if large_video_threshold_mb > 0:
            self.large_video_threshold_mb = min(large_video_threshold_mb, 100.0)
        else:
            self.large_video_threshold_mb = 0.0
        self.cache_dir = cache_dir
        self.pre_download_all_media = pre_download_all_media
        self.max_concurrent_downloads = max_concurrent_downloads
        self.cache_dir_available = check_cache_dir_available(cache_dir)
        if self.cache_dir_available and cache_dir:
            import os
            os.makedirs(cache_dir, exist_ok=True)

    def _determine_media_type(
        self,
        metadata: Dict[str, Any],
        idx: int,
        media_url: str
    ) -> bool:
        """判断指定索引的媒体是否为视频。

        Args:
            metadata: 元数据字典
            idx: 媒体索引
            media_url: 媒体URL

        Returns:
            如果是视频返回True，否则返回False
        """
        media_type = metadata.get('media_type', 'video')
        media_types_list = metadata.get('media_types', [])

        if media_type == 'mixed':
            if idx < len(media_types_list):
                return media_types_list[idx] == 'video'
            else:
                video_urls = metadata.get('video_urls', [])
                return media_url in video_urls
        elif media_type == 'video':
            return True
        elif media_type == 'gallery':
            return idx == 0 and '.mp4' in str(media_url).lower()
        else:
            return False

    def _build_media_items(
        self,
        metadata: Dict[str, Any],
        media_urls: List[str],
        media_id: str,
        headers: dict = None,
        referer: str = None,
        proxy: str = None
    ) -> List[Dict[str, Any]]:
        """构建媒体项列表。

        Args:
            metadata: 元数据字典
            media_urls: 媒体URL列表
            media_id: 媒体ID
            headers: 请求头（可选）
            referer: Referer URL（可选）
            proxy: 代理地址（可选）

        Returns:
            媒体项列表
        """
        media_items = []
        for idx, media_url in enumerate(media_urls):
            is_video = self._determine_media_type(metadata, idx, media_url)
            media_items.append({
                'url': media_url,
                'media_id': media_id,
                'index': idx,
                'is_video': is_video,
                'headers': headers,
                'referer': referer,
                'default_referer': referer,
                'proxy': proxy
            })
        return media_items

    def _process_download_results(
        self,
        download_results: List[Dict[str, Any]]
    ) -> List[Optional[str]]:
        """处理下载结果，转换为文件路径列表。

        Args:
            download_results: 下载结果列表

        Returns:
            文件路径列表，失败的项目为None
        """
        file_paths = []
        for result in download_results:
            if result.get('success') and result.get('file_path'):
                file_paths.append(result['file_path'])
            else:
                file_paths.append(None)
        return file_paths

    async def process_metadata(
        self,
        session: aiohttp.ClientSession,
        metadata: Dict[str, Any],
        headers: dict = None,
        referer: str = None,
        proxy: str = None
    ) -> Dict[str, Any]:
        """处理元数据，检查视频大小，确定使用网络直链还是本地文件。

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

        media_type = metadata.get('media_type', 'video')
        url = metadata.get('url', '')
        media_urls = metadata.get('media_urls', [])

        if not media_urls:
            return metadata

        if self.pre_download_all_media and self.cache_dir_available:
            media_id = self._generate_media_id(url)
            media_items = self._build_media_items(
                metadata,
                media_urls,
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

            file_paths = self._process_download_results(download_results)
            metadata['file_paths'] = file_paths
            metadata['use_local_files'] = True

            if media_type == 'gallery':
                metadata['video_sizes'] = []
                metadata['max_video_size_mb'] = None
                metadata['total_video_size_mb'] = 0.0
                metadata['video_count'] = 0
                metadata['exceeds_max_size'] = False
                metadata['is_large_media'] = False
            else:
                if media_type == 'mixed':
                    video_urls = metadata.get('video_urls', [])
                elif media_type == 'video':
                    video_urls = metadata.get('video_urls', media_urls)
                else:
                    video_urls = []

                if video_urls:
                    async def get_video_size_task(video_url: str) -> Optional[float]:
                        try:
                            size = await get_video_size(session, video_url, headers)
                            return size
                        except Exception:
                            return None
                    
                    tasks = [
                        get_video_size_task(video_url)
                        for video_url in video_urls
                    ]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    
                    video_sizes = []
                    for result in results:
                        if isinstance(result, Exception):
                            video_sizes.append(None)
                        elif isinstance(result, (int, float)) or result is None:
                            video_sizes.append(result)
                        else:
                            video_sizes.append(None)

                    valid_sizes = [s for s in video_sizes if s is not None]
                    video_count = len(video_urls)
                    max_video_size = max(valid_sizes) if valid_sizes else None
                    total_video_size = sum(valid_sizes) if valid_sizes else 0.0

                    metadata['video_sizes'] = video_sizes
                    metadata['max_video_size_mb'] = max_video_size
                    metadata['total_video_size_mb'] = total_video_size
                    metadata['video_count'] = video_count
                else:
                    metadata['video_sizes'] = []
                    metadata['max_video_size_mb'] = None
                    metadata['total_video_size_mb'] = 0.0
                    metadata['video_count'] = 0
                
                metadata['exceeds_max_size'] = False
                metadata['is_large_media'] = False

            return metadata

        if media_type == 'gallery':
            metadata['video_sizes'] = []
            metadata['max_video_size_mb'] = None
            metadata['total_video_size_mb'] = 0.0
            metadata['video_count'] = 0
            metadata['exceeds_max_size'] = False
            metadata['file_paths'] = [None] * len(media_urls)
            metadata['use_local_files'] = False
            metadata['is_large_media'] = False
            return metadata
        
        if media_type == 'mixed':
            video_urls = metadata.get('video_urls', [])
        elif media_type == 'video':
            video_urls = metadata.get('video_urls', media_urls)
        else:
            video_urls = []

        if not video_urls:
            metadata['video_sizes'] = []
            metadata['max_video_size_mb'] = None
            metadata['total_video_size_mb'] = 0.0
            metadata['video_count'] = 0
            metadata['exceeds_max_size'] = False
            metadata['file_paths'] = [None] * len(media_urls)
            metadata['use_local_files'] = False
            metadata['is_large_media'] = False
            return metadata

        async def get_video_size_task(video_url: str) -> Optional[float]:
            """获取单个视频的大小。

            Args:
                video_url: 视频URL

            Returns:
                视频大小(MB)，如果无法获取返回None
            """
            try:
                size = await get_video_size(session, video_url, headers)
                return size
            except Exception:
                return None
        
        tasks = [
            get_video_size_task(video_url)
            for video_url in video_urls
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        video_sizes = []
        for result in results:
            if isinstance(result, Exception):
                video_sizes.append(None)
            elif isinstance(result, (int, float)) or result is None:
                video_sizes.append(result)
            else:
                video_sizes.append(None)

        valid_sizes = [s for s in video_sizes if s is not None]
        video_count = len(video_urls)
        max_video_size = max(valid_sizes) if valid_sizes else None
        total_video_size = sum(valid_sizes) if valid_sizes else 0.0

        metadata['video_sizes'] = video_sizes
        metadata['max_video_size_mb'] = max_video_size
        metadata['total_video_size_mb'] = total_video_size
        metadata['video_count'] = video_count

        if self.max_video_size_mb > 0 and max_video_size is not None:
            if max_video_size > self.max_video_size_mb:
                logger.warning(
                    f"视频大小超过限制: {max_video_size:.2f}MB > {self.max_video_size_mb}MB, "
                    f"URL: {url}"
                )
                metadata['exceeds_max_size'] = True
                return metadata

        metadata['exceeds_max_size'] = False

        needs_download = False
        file_paths = []

        if self.large_video_threshold_mb > 0 and max_video_size is not None:
            if max_video_size > self.large_video_threshold_mb:
                needs_download = True

        if metadata.get('is_twitter_video'):
            needs_download = True

        if needs_download and self.cache_dir_available:
            media_id = self._generate_media_id(url)
            media_items = self._build_media_items(
                metadata,
                media_urls,
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
            
            file_paths = self._process_download_results(download_results)
            
            metadata['file_paths'] = file_paths
            metadata['use_local_files'] = True
            metadata['is_large_media'] = True
        else:
            metadata['file_paths'] = [None] * len(media_urls)
            metadata['use_local_files'] = False
            metadata['is_large_media'] = False

        return metadata

    def _generate_media_id(self, url: str) -> str:
        """根据URL生成媒体ID。

        Args:
            url: 原始URL

        Returns:
            媒体ID
        """
        import hashlib
        import re
        from urllib.parse import urlparse

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
        """处理元数据列表。

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

