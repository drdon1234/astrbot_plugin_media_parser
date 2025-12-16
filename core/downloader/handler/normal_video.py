# -*- coding: utf-8 -*-
"""
普通视频处理模块
负责下载普通视频到缓存目录
"""
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

from ..utils import get_video_suffix
from .base import download_media_from_url


def _process_download_results(
    results: List[Any],
    items: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """处理下载结果，统一错误处理逻辑
    
    Args:
        results: asyncio.gather 返回的结果列表（可能包含异常）
        items: 原始媒体项列表
        
    Returns:
        处理后的结果列表，每个项包含url、file_path、success、index等字段
    """
    processed_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            item = items[i] if i < len(items) else {}
            url_list = item.get('url_list', [])
            processed_results.append({
                'url': url_list[0] if url_list else None,
                'file_path': None,
                'success': False,
                'index': item.get('index', i),
                'error': str(result)
            })
        elif isinstance(result, dict):
            processed_results.append(result)
        else:
            item = items[i] if i < len(items) else {}
            url_list = item.get('url_list', [])
            processed_results.append({
                'url': url_list[0] if url_list else None,
                'file_path': None,
                'success': False,
                'index': item.get('index', i),
                'error': 'Unknown error'
            })
    return processed_results


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
        包含file_path和size_mb的字典，失败返回None
    """
    if not cache_dir:
        return None
    
    def generate_cache_file_path(content_type: str, url: str) -> str:
        """生成缓存文件路径"""
        suffix = get_video_suffix(content_type, url)
        cache_subdir = os.path.join(cache_dir, media_id)
        os.makedirs(cache_subdir, exist_ok=True)
        filename = f"video_{index}{suffix}"
        file_path = os.path.join(cache_subdir, filename)
        return file_path
    
    file_path, size_mb = await download_media_from_url(
        session=session,
        media_url=video_url,
        file_path_generator=generate_cache_file_path,
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


async def pre_download_videos(
    session: aiohttp.ClientSession,
    video_items: List[Dict[str, Any]],
    cache_dir: str,
    max_concurrent: int = 3
) -> List[Dict[str, Any]]:
    """预先下载所有视频到本地（支持普通视频和m3u8）

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

    from ..router import download_media

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
                    result = await download_media(
                        session,
                        url,
                        media_type=None,
                        cache_dir=cache_dir,
                        media_id=media_id,
                        index=index,
                        headers=item_headers,
                        proxy=item_proxy
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
                logger.warning(f"预下载视频失败: {url_list[0] if url_list else 'unknown'}, 错误: {e}")
                return {
                    'url': url_list[0] if url_list else None,
                    'file_path': None,
                    'success': False,
                    'index': index,
                    'error': str(e)
                }

    tasks = [download_one(item) for item in video_items]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return _process_download_results(results, video_items)


async def pre_download_media(
    session: aiohttp.ClientSession,
    media_items: List[Dict[str, Any]],
    cache_dir: str,
    max_concurrent: int = 3
) -> List[Dict[str, Any]]:
    """预先下载所有媒体到本地（支持视频和图片混合）
    
    此函数用于向后兼容，实际会根据媒体类型使用相应的下载器

    Args:
        session: aiohttp会话
        media_items: 媒体项列表，每个项包含url_list（URL列表）、media_id、index、
            headers、proxy等字段
        cache_dir: 缓存目录路径
        max_concurrent: 最大并发下载数

    Returns:
        下载结果列表，每个项包含url（第一个URL）、file_path、success、index等字段
    """
    if not cache_dir or not media_items:
        return []

    from ..router import download_media

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
                    result = await download_media(
                        session,
                        url,
                        media_type=None,
                        cache_dir=cache_dir,
                        media_id=media_id,
                        index=index,
                        headers=item_headers,
                        proxy=item_proxy
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
                logger.warning(f"预下载媒体失败: {url_list[0] if url_list else 'unknown'}, 错误: {e}")
                return {
                    'url': url_list[0] if url_list else None,
                    'file_path': None,
                    'success': False,
                    'index': index,
                    'error': str(e)
                }

    tasks = [download_one(item) for item in media_items]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return _process_download_results(results, media_items)

