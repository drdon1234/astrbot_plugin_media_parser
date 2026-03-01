import asyncio
import os
import time
from typing import Dict, Any, List, Optional, Tuple

import aiohttp

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from ...constants import Config
from ..utils import process_gather_results, generate_cache_file_path
from .base import download_media_from_url


async def download_video_to_cache(
    session: aiohttp.ClientSession,
    video_url: str,
    cache_dir: str,
    media_id: str,
    index: int = 0,
    headers: dict = None,
    proxy: str = None
) -> Optional[Dict[str, Any]]:
    """下载视频到缓存目录

    Args:
        session: aiohttp会话
        video_url: 视频URL
        cache_dir: 缓存目录路径
        media_id: 媒体ID
        index: 索引
        headers: 请求头字典
        proxy: 代理地址（可选）

    Returns:
        包含file_path和size_mb的字典，失败时为None
    """
    if not cache_dir:
        return None
    
    def file_path_generator(content_type: str, url: str) -> str:
        """生成缓存文件路径"""
        return generate_cache_file_path(
            cache_dir=cache_dir,
            media_id=media_id,
            media_type='video',
            index=index,
            content_type=content_type,
            url=url
        )
    
    file_path, size_mb = await download_media_from_url(
        session=session,
        media_url=video_url,
        file_path_generator=file_path_generator,
        is_video=True,
        headers=headers,
        proxy=proxy
    )
    
    if file_path:
        return {
            'file_path': file_path,
            'size_mb': size_mb
        }
    return None


async def batch_download_videos(
    session: aiohttp.ClientSession,
    video_items: List[Dict[str, Any]],
    cache_dir: str,
    max_concurrent: int = None
) -> List[Dict[str, Any]]:
    """批量下载普通视频到缓存目录

    Args:
        session: aiohttp会话
        video_items: 视频项列表，每个项包含url_list（URL列表）、media_id、index、
            headers、proxy等字段
        cache_dir: 缓存目录路径
        max_concurrent: 最大并发下载数

    Returns:
        下载结果列表，每个项包含url（第一个URL）、file_path、success、index等字段
    """
    if not cache_dir or not video_items:
        return []

    if max_concurrent is None:
        max_concurrent = Config.DOWNLOAD_MANAGER_MAX_CONCURRENT
    semaphore = asyncio.Semaphore(max_concurrent)

    async def download_one(item: Dict[str, Any]) -> Dict[str, Any]:
        async with semaphore:
            try:
                url_list = item.get('url_list', [])
                media_id = item.get('media_id', 'media')
                index = item.get('index', 0)
                item_headers = item.get('headers', {})
                item_proxy = item.get('proxy')

                if not url_list or not isinstance(url_list, list):
                    return {
                        'url': url_list[0] if url_list else None,
                        'file_path': None,
                        'success': False,
                        'index': index
                    }

                for url in url_list:
                    result = await download_video_to_cache(
                        session,
                        url,
                        cache_dir,
                        media_id,
                        index,
                        item_headers,
                        item_proxy
                    )
                    if result and result.get('file_path'):
                        return {
                            'url': url_list[0],
                            'file_path': result.get('file_path'),
                            'size_mb': result.get('size_mb'),
                            'success': True,
                            'index': index
                        }
                
                return {
                    'url': url_list[0] if url_list else None,
                    'file_path': None,
                    'size_mb': None,
                    'success': False,
                    'index': index
                }
            except Exception as e:
                url_list = item.get('url_list', [])
                index = item.get('index', 0)
                logger.warning(f"批量下载视频失败: {url_list[0] if url_list else 'unknown'}, 错误: {e}")
                return {
                    'url': url_list[0] if url_list else None,
                    'file_path': None,
                    'success': False,
                    'index': index,
                    'error': str(e)
                }

    tasks = [download_one(item) for item in video_items]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return process_gather_results(results, video_items)
