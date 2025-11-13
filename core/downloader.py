# -*- coding: utf-8 -*-
"""
下载模块
负责视频大小检查方法和下载相关方法
"""
import asyncio
import os
import re
import tempfile
from typing import Dict, Any, List, Optional

import aiohttp

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from .file_manager import get_image_suffix, move_temp_file_to_cache


async def _get_media_size_from_response(
    session: aiohttp.ClientSession,
    media_url: str,
    headers: dict = None
) -> Optional[float]:
    """从HTTP响应中提取视频大小（通用函数）。

    Args:
        session: aiohttp会话
        media_url: 视频URL
        headers: 请求头（可选）

    Returns:
        视频大小(MB)，如果无法获取返回None
    """
    try:
        request_headers = headers or {}
        async with session.head(
            media_url,
            headers=request_headers,
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
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
    except Exception as e:
        logger.warning(f"获取视频大小失败: {media_url}, 错误: {e}")
    return None


async def get_video_size(
    session: aiohttp.ClientSession,
    video_url: str,
    headers: dict = None
) -> Optional[float]:
    """获取视频文件大小。

    Args:
        session: aiohttp会话
        video_url: 视频URL
        headers: 请求头（可选）

    Returns:
        视频大小(MB)，如果无法获取返回None
    """
    return await _get_media_size_from_response(session, video_url, headers)


async def download_image_to_file(
    session: aiohttp.ClientSession,
    image_url: str,
    index: int = 0,
    headers: dict = None,
    referer: str = None,
    default_referer: str = None
) -> Optional[str]:
    """下载图片到临时文件。

    Args:
        session: aiohttp会话
        image_url: 图片URL
        index: 图片索引
        headers: 自定义请求头（如果提供，会与默认请求头合并）
        referer: Referer URL，如果提供则使用
        default_referer: 默认Referer URL（如果referer未提供）

    Returns:
        临时文件路径，失败返回None
    """
    try:
        referer_url = referer if referer else (default_referer or '')
        default_headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            ),
            'Accept': (
                'image/avif,image/webp,image/apng,image/svg+xml,'
                'image/*,*/*;q=0.8'
            ),
            'Accept-Language': (
                'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7'
            ),
        }
        if referer_url:
            default_headers['Referer'] = referer_url

        if headers:
            default_headers.update(headers)

        async with session.get(
            image_url,
            headers=default_headers,
            timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            response.raise_for_status()
            content = await response.read()
            content_type = response.headers.get('Content-Type', '')
            suffix = get_image_suffix(content_type, image_url)

            with tempfile.NamedTemporaryFile(
                delete=False,
                suffix=suffix
            ) as temp_file:
                temp_file.write(content)
                file_path = os.path.normpath(temp_file.name)
                return file_path
    except Exception as e:
        logger.warning(f"下载图片到临时文件失败: {image_url}, 错误: {e}")
        return None


async def download_media_to_cache(
    session: aiohttp.ClientSession,
    media_url: str,
    cache_dir: str,
    media_id: str,
    index: int = 0,
    is_video: bool = True,
    headers: dict = None,
    referer: str = None,
    default_referer: str = None,
    proxy: str = None
) -> Optional[str]:
    """下载媒体到缓存目录。

    Args:
        session: aiohttp会话
        media_url: 媒体URL
        cache_dir: 缓存目录路径
        media_id: 媒体ID
        index: 索引
        is_video: 是否为视频（True为视频，False为图片）
        headers: 自定义请求头（如果提供，会与默认请求头合并）
        referer: Referer URL，如果提供则使用
        default_referer: 默认Referer URL（如果referer未提供）
        proxy: 代理地址（可选）

    Returns:
        文件路径，失败返回None
    """
    if not cache_dir:
        return None
    try:
        if not is_video:
            referer_url = (
                referer if referer else (default_referer or '')
            )
            default_headers = {
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/120.0.0.0 Safari/537.36'
                ),
                'Accept': (
                    'image/avif,image/webp,image/apng,image/svg+xml,'
                    'image/*,*/*;q=0.8'
                ),
                'Accept-Language': (
                    'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7'
                ),
            }
            if referer_url:
                default_headers['Referer'] = referer_url

            if headers:
                default_headers.update(headers)

            async with session.get(
                media_url,
                headers=default_headers,
                timeout=aiohttp.ClientTimeout(total=30),
                proxy=proxy
            ) as response:
                response.raise_for_status()
                content = await response.read()
                content_type = response.headers.get('Content-Type', '')
                suffix = get_image_suffix(content_type, media_url)
                filename = f"{media_id}_{index}{suffix}"
                file_path = os.path.join(cache_dir, filename)
                
                os.makedirs(cache_dir, exist_ok=True)
                
                if os.path.exists(file_path):
                    return os.path.normpath(file_path)
                with open(file_path, 'wb') as f:
                    f.write(content)
                return os.path.normpath(file_path)
        else:
            default_headers = {
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/120.0.0.0 Safari/537.36'
                ),
                'Accept': '*/*',
                'Accept-Language': (
                    'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7'
                ),
            }

            if referer:
                default_headers['Referer'] = referer
            elif headers and 'Referer' in headers:
                default_headers['Referer'] = headers['Referer']

            if headers:
                default_headers.update(headers)

            async with session.get(
                media_url,
                headers=default_headers,
                timeout=aiohttp.ClientTimeout(total=300),
                proxy=proxy
            ) as response:
                response.raise_for_status()
                suffix = ".mp4"
                filename = f"{media_id}_{index}{suffix}"
                file_path = os.path.join(cache_dir, filename)
                
                os.makedirs(cache_dir, exist_ok=True)
                
                if os.path.exists(file_path):
                    return os.path.normpath(file_path)
                content = await response.read()
                with open(file_path, 'wb') as f:
                    f.write(content)
                return os.path.normpath(file_path)
    except Exception as e:
        logger.warning(f"下载媒体到缓存目录失败: {media_url}, 错误: {e}")
        return None


async def pre_download_media(
    session: aiohttp.ClientSession,
    media_items: List[Dict[str, Any]],
    cache_dir: str,
    max_concurrent: int = 3
) -> List[Dict[str, Any]]:
    """预先下载所有媒体到本地。

    Args:
        session: aiohttp会话
        media_items: 媒体项列表，每个项包含url、media_id、index、
            is_video、headers、referer、default_referer、proxy等字段
        cache_dir: 缓存目录路径
        max_concurrent: 最大并发下载数

    Returns:
        下载结果列表，每个项包含url、file_path、success、index等字段
    """
    if not cache_dir or not media_items:
        return []

    semaphore = asyncio.Semaphore(max_concurrent)

    async def download_one(item: Dict[str, Any]) -> Dict[str, Any]:
        async with semaphore:
            try:
                url = item.get('url')
                media_id = item.get('media_id', 'media')
                index = item.get('index', 0)
                is_video = item.get('is_video', True)
                item_headers = item.get('headers')
                item_referer = item.get('referer')
                item_default_referer = item.get('default_referer')
                item_proxy = item.get('proxy')

                if not url:
                    return {
                        'url': url,
                        'file_path': None,
                        'success': False,
                        'index': index
                    }

                file_path = await download_media_to_cache(
                    session,
                    url,
                    cache_dir,
                    media_id,
                    index,
                    is_video,
                    item_headers,
                    item_referer,
                    item_default_referer,
                    item_proxy
                )
                return {
                    'url': url,
                    'file_path': file_path,
                    'success': file_path is not None,
                    'index': index
                }
            except Exception as e:
                url = item.get('url', '')
                index = item.get('index', 0)
                logger.warning(f"预下载媒体失败: {url}, 错误: {e}")
                return {
                    'url': url,
                    'file_path': None,
                    'success': False,
                    'index': index,
                    'error': str(e)
                }

    tasks = [download_one(item) for item in media_items]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    processed_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            item = media_items[i] if i < len(media_items) else {}
            processed_results.append({
                'url': item.get('url', ''),
                'file_path': None,
                'success': False,
                'index': item.get('index', i),
                'error': str(result)
            })
        elif isinstance(result, dict):
            processed_results.append(result)
        else:
            item = media_items[i] if i < len(media_items) else {}
            processed_results.append({
                'url': item.get('url', ''),
                'file_path': None,
                'success': False,
                'index': item.get('index', i),
                'error': 'Unknown error'
            })

    return processed_results

