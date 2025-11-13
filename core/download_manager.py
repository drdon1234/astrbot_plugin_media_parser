# -*- coding: utf-8 -*-
"""
下载管理器
负责管理下载流程，检查配置项，确定使用网络直链还是本地文件
"""
from typing import Dict, Any, List, Optional, Tuple
import asyncio
import os

import aiohttp

from astrbot.api import logger

from .downloader import (
    get_video_size,
    download_media_to_cache,
    pre_download_media,
    validate_media_url
)
from .file_manager import (
    check_cache_dir_available,
    cleanup_files
)


class DownloadManager:
    """下载管理器，负责管理视频下载流程"""

    def __init__(
        self,
        max_video_size_mb: float = 0.0,
        large_video_threshold_mb: float = 50.0,
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
        """判断指定索引的媒体是否为视频

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
        """构建媒体项列表

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
        """处理下载结果，转换为文件路径列表

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

    def _check_has_valid_media(
        self,
        download_results: List[Dict[str, Any]],
        media_urls: List[str],
        metadata: Dict[str, Any]
    ) -> bool:
        """检查下载结果中是否有有效媒体

        Args:
            download_results: 下载结果列表
            media_urls: 媒体URL列表
            metadata: 元数据字典

        Returns:
            如果有有效媒体返回True，否则返回False
        """
        if not download_results:
            return False
        
        for result in download_results:
            if result.get('success') and result.get('file_path'):
                file_path = result['file_path']
                if file_path and os.path.exists(file_path):
                    return True
        
        return False

    def _extract_sizes_from_download_results(
        self,
        download_results: List[Dict[str, Any]],
        target_urls: List[str],
        all_media_urls: List[str]
    ) -> List[Optional[float]]:
        """从下载结果中提取指定URL的大小

        Args:
            download_results: 下载结果列表
            target_urls: 目标URL列表（要提取大小的URL）
            all_media_urls: 所有媒体URL列表（未使用，保留兼容性）

        Returns:
            大小列表（MB），对应target_urls的顺序
        """
        url_to_size = {}
        for result in download_results:
            url = result.get('url')
            if url and result.get('success'):
                size_mb = result.get('size_mb')
                if size_mb is not None:
                    url_to_size[url] = size_mb
                else:
                    file_path = result.get('file_path')
                    if file_path and os.path.exists(file_path):
                        try:
                            file_size_bytes = os.path.getsize(file_path)
                            size_mb = file_size_bytes / (1024 * 1024)
                            url_to_size[url] = size_mb
                        except Exception:
                            pass
        
        sizes = []
        for url in target_urls:
            size = url_to_size.get(url)
            sizes.append(size)
        
        return sizes

    async def _validate_media_urls_with_results(
        self,
        session: aiohttp.ClientSession,
        media_urls: List[str],
        headers: dict = None,
        referer: str = None,
        proxy: str = None,
        metadata: Dict[str, Any] = None,
        media_type: str = None
    ) -> Dict[str, bool]:
        """验证媒体URL列表，返回每个URL的验证结果

        Args:
            session: aiohttp会话
            media_urls: 媒体URL列表
            headers: 请求头（可选）
            referer: Referer URL（可选）
            proxy: 代理地址（可选）
            metadata: 元数据字典（可选）
            media_type: 媒体类型（可选）

        Returns:
            字典，key为URL，value为是否有效
        """
        if not media_urls:
            return {}
        
        if metadata and media_type:
            media_type = metadata.get('media_type', media_type or 'video')
        else:
            media_type = media_type or 'video'
        
        if media_type == 'gallery':
            is_video = False
        elif media_type == 'mixed':
            video_urls = metadata.get('video_urls', []) if metadata else []
            image_urls = metadata.get('image_urls', []) if metadata else []
            
            async def validate_media_task(media_url: str, is_video: bool) -> Tuple[str, bool]:
                try:
                    is_valid = await validate_media_url(
                        session, media_url, headers, proxy, is_video=is_video
                    )
                    return media_url, is_valid
                except Exception:
                    return media_url, False
            
            tasks = []
            for media_url in media_urls:
                if media_url in video_urls:
                    tasks.append(validate_media_task(media_url, True))
                elif media_url in image_urls:
                    tasks.append(validate_media_task(media_url, False))
                else:
                    tasks.append(validate_media_task(media_url, True))
            
            if not tasks:
                return {url: False for url in media_urls}
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            url_to_valid = {}
            for result in results:
                if isinstance(result, Exception):
                    continue
                elif isinstance(result, tuple) and len(result) == 2:
                    url, is_valid = result
                    url_to_valid[url] = is_valid
            
            for url in media_urls:
                if url not in url_to_valid:
                    url_to_valid[url] = False
            
            return url_to_valid
        elif media_type == 'video':
            is_video = True
        else:
            is_video = False
        
        async def validate_media_task(media_url: str) -> Tuple[str, bool]:
            try:
                is_valid = await validate_media_url(
                    session, media_url, headers, proxy, is_video=is_video
                )
                return media_url, is_valid
            except Exception:
                return media_url, False
        
        tasks = [validate_media_task(media_url) for media_url in media_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        url_to_valid = {}
        for result in results:
            if isinstance(result, Exception):
                continue
            elif isinstance(result, tuple) and len(result) == 2:
                url, is_valid = result
                url_to_valid[url] = is_valid
        
        for url in media_urls:
            if url not in url_to_valid:
                url_to_valid[url] = False
        
        return url_to_valid

    async def _validate_all_media_urls(
        self,
        session: aiohttp.ClientSession,
        media_urls: List[str],
        headers: dict = None,
        referer: str = None,
        proxy: str = None,
        metadata: Dict[str, Any] = None,
        media_type: str = None
    ) -> bool:
        """验证所有媒体URL是否有效

        Args:
            session: aiohttp会话
            media_urls: 媒体URL列表
            headers: 请求头（可选）
            referer: Referer URL（可选）
            proxy: 代理地址（可选）
            metadata: 元数据字典（可选）
            media_type: 媒体类型（可选）

        Returns:
            如果至少有一个有效媒体返回True，否则返回False
        """
        url_to_valid = await self._validate_media_urls_with_results(
            session, media_urls, headers, referer, proxy, metadata, media_type
        )
        
        for is_valid in url_to_valid.values():
            if is_valid:
                return True
        
        return False

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

        media_type = metadata.get('media_type', 'video')
        url = metadata.get('url', '')
        media_urls = metadata.get('media_urls', [])

        if not media_urls:
            metadata['has_valid_media'] = False
            return metadata

        if self.pre_download_all_media and self.cache_dir_available:
            pre_check_video_sizes = None
            if media_type in ('video', 'mixed'):
                if media_type == 'mixed':
                    video_urls = metadata.get('video_urls', [])
                elif media_type == 'video':
                    video_urls = metadata.get('video_urls', media_urls)
                else:
                    video_urls = []
                
                if video_urls and self.max_video_size_mb > 0:
                    async def get_video_size_task(video_url: str) -> Optional[float]:
                        try:
                            size = await get_video_size(session, video_url, headers, proxy)
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
                            metadata['video_count'] = len(video_urls)
                            metadata['file_paths'] = [None] * len(media_urls)
                            metadata['use_local_files'] = False
                            metadata['is_large_media'] = False
                            return metadata
                        pre_check_video_sizes = video_sizes
            
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

            url_to_result = {result.get('url'): result for result in download_results}
            
            file_paths = []
            for url in media_urls:
                if url in url_to_result:
                    result = url_to_result[url]
                    if result.get('success') and result.get('file_path'):
                        file_paths.append(result['file_path'])
                    else:
                        file_paths.append(None)
                else:
                    file_paths.append(None)
            
            metadata['file_paths'] = file_paths

            if media_type == 'gallery':
                has_valid_media = self._check_has_valid_media(
                    download_results, media_urls, metadata
                )
                metadata['has_valid_media'] = has_valid_media
                metadata['use_local_files'] = has_valid_media
                metadata['video_sizes'] = []
                metadata['max_video_size_mb'] = None
                metadata['total_video_size_mb'] = 0.0
                metadata['video_count'] = 0
                metadata['exceeds_max_size'] = False
                metadata['is_large_media'] = False
            else:
                if media_type == 'mixed':
                    video_urls = metadata.get('video_urls', [])
                    image_urls = metadata.get('image_urls', [])
                elif media_type == 'video':
                    video_urls = metadata.get('video_urls', media_urls)
                    image_urls = []
                else:
                    video_urls = []
                    image_urls = []

                has_valid_videos = False
                has_valid_images = False

                if video_urls:
                    download_video_sizes = self._extract_sizes_from_download_results(
                        download_results, video_urls, media_urls
                    )
                    
                    if pre_check_video_sizes is not None:
                        video_sizes = []
                        for i, download_size in enumerate(download_video_sizes):
                            if download_size is not None:
                                video_sizes.append(download_size)
                            elif i < len(pre_check_video_sizes):
                                video_sizes.append(pre_check_video_sizes[i])
                            else:
                                video_sizes.append(None)
                    else:
                        video_sizes = download_video_sizes
                    
                    valid_sizes = [s for s in video_sizes if s is not None]
                    has_valid_videos = len(valid_sizes) > 0
                    video_count = len(video_urls)
                    max_video_size = max(valid_sizes) if valid_sizes else None
                    total_video_size = sum(valid_sizes) if valid_sizes else 0.0

                    metadata['video_sizes'] = video_sizes
                    metadata['max_video_size_mb'] = max_video_size
                    metadata['total_video_size_mb'] = total_video_size
                    metadata['video_count'] = video_count
                    
                    need_check = True
                    if pre_check_video_sizes is not None:
                        pre_valid_sizes = [s for s in pre_check_video_sizes if s is not None]
                        if pre_valid_sizes:
                            pre_max_size = max(pre_valid_sizes)
                            if pre_max_size <= self.max_video_size_mb:
                                if all(s is not None for s in video_sizes):
                                    need_check = False
                    
                    if need_check and self.max_video_size_mb > 0 and max_video_size is not None:
                        if max_video_size > self.max_video_size_mb:
                            logger.warning(
                                f"视频大小超过限制: {max_video_size:.2f}MB > {self.max_video_size_mb}MB, "
                                f"URL: {url}"
                            )
                            cleanup_files(file_paths)
                            metadata['exceeds_max_size'] = True
                            metadata['has_valid_media'] = False
                            metadata['use_local_files'] = False
                            metadata['file_paths'] = [None] * len(media_urls)
                            metadata['is_large_media'] = False
                            metadata['max_video_size_mb'] = max_video_size
                            return metadata
                else:
                    metadata['video_sizes'] = []
                    metadata['max_video_size_mb'] = None
                    metadata['total_video_size_mb'] = 0.0
                    metadata['video_count'] = 0
                
                if image_urls:
                    has_valid_images = self._check_has_valid_media(
                        download_results, image_urls, metadata
                    )
                
                has_valid_media = self._check_has_valid_media(
                    download_results, media_urls, metadata
                ) or has_valid_videos or has_valid_images
                
                metadata['has_valid_media'] = has_valid_media
                metadata['use_local_files'] = has_valid_media
                metadata['exceeds_max_size'] = False
                metadata['is_large_media'] = False
            
            return metadata

        if media_type == 'gallery':
            has_valid_media = await self._validate_all_media_urls(
                session, media_urls, headers, referer, proxy, metadata, 'gallery'
            )
            metadata['has_valid_media'] = has_valid_media
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
            image_urls = metadata.get('image_urls', [])
        elif media_type == 'video':
            video_urls = metadata.get('video_urls', media_urls)
            image_urls = []
        else:
            video_urls = []
            image_urls = []

        if not video_urls and not image_urls:
            has_valid_media = await self._validate_all_media_urls(
                session, media_urls, headers, referer, proxy, metadata, media_type
            )
            metadata['has_valid_media'] = has_valid_media
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
            """获取单个视频的大小

            Args:
                video_url: 视频URL

            Returns:
                视频大小(MB)，如果无法获取返回None
            """
            try:
                size = await get_video_size(session, video_url, headers, proxy)
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

        has_valid_videos = len(valid_sizes) > 0
        
        if image_urls:
            has_valid_images = await self._validate_all_media_urls(
                session, image_urls, headers, referer, proxy, metadata, 'image'
            )
            has_valid_media = has_valid_videos or has_valid_images
        else:
            has_valid_media = has_valid_videos
        
        metadata['has_valid_media'] = has_valid_media
        
        if not has_valid_media:
            metadata['exceeds_max_size'] = False
            metadata['file_paths'] = [None] * len(media_urls)
            metadata['use_local_files'] = False
            metadata['is_large_media'] = False
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
            
            if video_urls and self.max_video_size_mb > 0:
                download_video_sizes = self._extract_sizes_from_download_results(
                    download_results, video_urls, media_urls
                )
                valid_download_sizes = [s for s in download_video_sizes if s is not None]
                if valid_download_sizes:
                    actual_max_video_size = max(valid_download_sizes)
                    if actual_max_video_size > self.max_video_size_mb:
                        logger.warning(
                            f"视频大小超过限制: {actual_max_video_size:.2f}MB > {self.max_video_size_mb}MB, "
                            f"URL: {url}，清理已下载的文件"
                        )
                        cleanup_files(file_paths)
                        metadata['exceeds_max_size'] = True
                        metadata['has_valid_media'] = False
                        metadata['use_local_files'] = False
                        metadata['file_paths'] = [None] * len(media_urls)
                        metadata['is_large_media'] = False
                        metadata['video_sizes'] = download_video_sizes
                        metadata['max_video_size_mb'] = actual_max_video_size
                        return metadata
                    metadata['video_sizes'] = download_video_sizes
                    metadata['max_video_size_mb'] = actual_max_video_size
                    metadata['total_video_size_mb'] = sum(valid_download_sizes)
            
            has_valid_media = self._check_has_valid_media(
                download_results, media_urls, metadata
            )
            
            metadata['file_paths'] = file_paths
            metadata['use_local_files'] = True
            metadata['is_large_media'] = True
        else:
            metadata['file_paths'] = [None] * len(media_urls)
            metadata['use_local_files'] = False
            metadata['is_large_media'] = False
        
        metadata['has_valid_media'] = has_valid_media

        return metadata

    def _generate_media_id(self, url: str) -> str:
        """根据URL生成媒体ID

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

