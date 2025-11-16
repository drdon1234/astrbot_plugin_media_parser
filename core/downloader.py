# -*- coding: utf-8 -*-
"""
下载模块
负责视频大小检查方法和下载相关方法
"""
import asyncio
import os
import re
import tempfile
import time
from typing import Dict, Any, List, Optional, Tuple

import aiohttp

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from .file_manager import get_image_suffix, get_video_suffix
from .constants import Config

_DOWNLOAD_CHUNK_SIZE = 1024 * 1024
_EMPTY_CONTENT_TYPE_CHECK_SIZE = 64


def _build_request_headers(
    is_video: bool = False,
    referer: str = None,
    default_referer: str = None,
    custom_headers: dict = None
) -> dict:
    """构建请求头

    Args:
        is_video: 是否为视频（True为视频，False为图片）
        referer: Referer URL，如果提供则使用
        default_referer: 默认Referer URL（如果referer未提供）
        custom_headers: 自定义请求头（如果提供，会与默认请求头合并）

    Returns:
        请求头字典
    """
    if custom_headers and 'Referer' in custom_headers:
        referer_url = custom_headers['Referer']
    else:
        referer_url = referer if referer else (default_referer or '')
    
    if is_video:
        headers = {
            'User-Agent': Config.USER_AGENT_DESKTOP,
            'Accept': '*/*',
            'Accept-Language': Config.DEFAULT_ACCEPT_LANGUAGE,
        }
    else:
        headers = {
            'User-Agent': Config.USER_AGENT_DESKTOP,
            'Accept': (
                'image/avif,image/webp,image/apng,image/svg+xml,'
                'image/*,*/*;q=0.8'
            ),
            'Accept-Language': Config.DEFAULT_ACCEPT_LANGUAGE,
        }
    
    if referer_url:
        headers['Referer'] = referer_url
    
    if custom_headers:
        headers.update(custom_headers)
    
    return headers


def _validate_content_type(
    content_type: str,
    is_video: bool = False
) -> bool:
    """验证Content-Type是否为有效的媒体类型

    Args:
        content_type: Content-Type值（已转换为小写）
        is_video: 是否为视频（True为视频，False为图片）

    Returns:
        如果为有效媒体类型返回True，否则返回False
    """
    if 'application/json' in content_type or 'text/' in content_type:
        return False
    
    if is_video:
        return (content_type.startswith('video/') or 
                'mp4' in content_type or 
                'octet-stream' in content_type or
                not content_type)
    else:
        return (content_type.startswith('image/') or not content_type)


async def _check_json_error_response(
    content_preview: bytes,
    media_url: str
) -> bool:
    """检查内容预览是否为JSON错误响应

    Args:
        content_preview: 内容预览（前64字节）
        media_url: 媒体URL（用于日志）

    Returns:
        如果是JSON错误响应返回True，否则返回False
    """
    if not content_preview or not content_preview.startswith(b'{'):
        return False
    
    try:
        content_preview_str = content_preview.decode('utf-8', errors='ignore')
        if 'error_code' in content_preview_str or 'error_response' in content_preview_str:
            logger.warning(f"媒体URL包含错误响应（Content-Type为空）: {media_url}")
            return True
    except UnicodeDecodeError:
        pass
    
    return False


async def _validate_media_response(
    response: aiohttp.ClientResponse,
    media_url: str,
    is_video: bool = False,
    allow_read_content: bool = True
) -> Tuple[bool, Optional[bytes]]:
    """验证响应是否为有效的媒体响应

    Args:
        response: HTTP响应对象
        media_url: 媒体URL（用于日志）
        is_video: 是否为视频（True为视频，False为图片）
        allow_read_content: 是否允许读取内容（HEAD请求时为False）

    Returns:
        (is_valid, content_preview) 元组，is_valid表示是否为有效媒体，
        content_preview为已读取的内容预览（如果Content-Type为空且允许读取）
    """
    if response.status != 200:
        if response.status == 403:
            logger.warning(f"媒体URL访问被拒绝(403 Forbidden): {media_url}")
        return False, None
    
    content_type = response.headers.get('Content-Type', '').lower()
    
    if 'application/json' in content_type or 'text/' in content_type:
        logger.warning(f"媒体URL包含错误响应（非媒体Content-Type）: {media_url}")
        return False, None
    
    if not content_type:
        if not allow_read_content:
            raise aiohttp.ClientError("Content-Type为空，需要GET请求验证")
        
        content_preview = await response.content.read(_EMPTY_CONTENT_TYPE_CHECK_SIZE)
        if not content_preview:
            return False, None
        
        if await _check_json_error_response(content_preview, media_url):
            return False, None
        
        return True, content_preview
    
    if not _validate_content_type(content_type, is_video):
        return False, None
    
    return True, None


def _extract_size_from_headers(
    response: aiohttp.ClientResponse
) -> Optional[float]:
    """从响应头中提取媒体大小

    Args:
        response: HTTP响应对象

    Returns:
        媒体大小(MB)，如果无法获取返回None
    """
    content_range = response.headers.get("Content-Range")
    if content_range:
        match = re.search(r'/\s*(\d+)', content_range)
        if match:
            size_bytes = int(match.group(1))
            return size_bytes / (1024 * 1024)
    
    content_length = response.headers.get("Content-Length")
    if content_length:
        size_bytes = int(content_length)
        return size_bytes / (1024 * 1024)
    
    return None


async def _get_media_size_from_response(
    session: aiohttp.ClientSession,
    media_url: str,
    headers: dict = None,
    proxy: str = None,
    is_video: bool = True
) -> Optional[float]:
    """获取媒体大小并验证是否为有效媒体（仅通过header判断）

    Args:
        session: aiohttp会话
        media_url: 媒体URL
        headers: 请求头（可选）
        proxy: 代理地址（可选）
        is_video: 是否为视频（True为视频，False为图片）

    Returns:
        媒体大小(MB)，如果无效或无法获取返回None
    """
    try:
        request_headers = headers or {}
        timeout = aiohttp.ClientTimeout(total=Config.VIDEO_SIZE_CHECK_TIMEOUT)
        
        try:
            async with session.head(
                media_url,
                headers=request_headers,
                timeout=timeout,
                proxy=proxy,
                allow_redirects=True
            ) as response:
                if response.status == 403:
                    logger.warning(f"媒体URL访问被拒绝(403 Forbidden): {media_url}")
                    response._access_denied = True
                
                is_valid, _ = await _validate_media_response(
                    response, media_url, is_video, allow_read_content=False
                )
                if not is_valid:
                    return None
                
                return _extract_size_from_headers(response)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            async with session.get(
                media_url,
                headers=request_headers,
                timeout=timeout,
                proxy=proxy,
                allow_redirects=True
            ) as response:
                if response.status == 403:
                    logger.warning(f"媒体URL访问被拒绝(403 Forbidden): {media_url}")
                    response._access_denied = True
                
                is_valid, _ = await _validate_media_response(
                    response, media_url, is_video, allow_read_content=True
                )
                if not is_valid:
                    return None
                
                return _extract_size_from_headers(response)
    except Exception as e:
        logger.warning(f"获取媒体大小失败: {media_url}, 错误: {e}")
    return None


async def get_video_size(
    session: aiohttp.ClientSession,
    video_url: str,
    headers: dict = None,
    proxy: str = None
) -> Tuple[Optional[float], Optional[int]]:
    """获取视频文件大小

    Args:
        session: aiohttp会话
        video_url: 视频URL
        headers: 请求头（可选）
        proxy: 代理地址（可选）

    Returns:
        (size_mb, status_code) 元组，size_mb为视频大小(MB)，如果无法获取返回None，
        status_code为HTTP状态码（如果是403等特殊状态码），否则为None
    """
    try:
        request_headers = headers or {}
        timeout = aiohttp.ClientTimeout(total=Config.VIDEO_SIZE_CHECK_TIMEOUT)
        
        try:
            async with session.head(
                video_url,
                headers=request_headers,
                timeout=timeout,
                proxy=proxy,
                allow_redirects=True
            ) as response:
                if response.status == 403:
                    logger.warning(f"视频URL访问被拒绝(403 Forbidden): {video_url}")
                    return None, 403
                size = _extract_size_from_headers(response)
                if size is not None:
                    return size, None
                is_valid, _ = await _validate_media_response(
                    response, video_url, is_video=True, allow_read_content=False
                )
                if not is_valid:
                    return None, None
                return size, None
        except (aiohttp.ClientError, asyncio.TimeoutError):
            async with session.get(
                video_url,
                headers=request_headers,
                timeout=timeout,
                proxy=proxy,
                allow_redirects=True
            ) as response:
                if response.status == 403:
                    logger.warning(f"视频URL访问被拒绝(403 Forbidden): {video_url}")
                    return None, 403
                is_valid, _ = await _validate_media_response(
                    response, video_url, is_video=True, allow_read_content=True
                )
                if not is_valid:
                    return None, None
                size = _extract_size_from_headers(response)
                return size, None
    except Exception as e:
        if '403' in str(e) or 'Forbidden' in str(e):
            return None, 403
        return None, None


async def validate_media_url(
    session: aiohttp.ClientSession,
    media_url: str,
    headers: dict = None,
    proxy: str = None,
    is_video: bool = True
) -> Tuple[bool, Optional[int]]:
    """验证媒体URL是否有效

    Args:
        session: aiohttp会话
        media_url: 媒体URL
        headers: 请求头（可选）
        proxy: 代理地址（可选）
        is_video: 是否为视频（True为视频，False为图片）

    Returns:
        (is_valid, status_code) 元组，is_valid表示媒体URL是否有效，
        status_code为HTTP状态码（如果是403等特殊状态码），否则为None
    """
    try:
        request_headers = headers or {}
        timeout = aiohttp.ClientTimeout(total=Config.VIDEO_SIZE_CHECK_TIMEOUT)
        
        try:
            async with session.head(
                media_url,
                headers=request_headers,
                timeout=timeout,
                proxy=proxy,
                allow_redirects=True
            ) as response:
                if response.status == 403:
                    return False, 403
                is_valid, _ = await _validate_media_response(
                    response, media_url, is_video, allow_read_content=False
                )
                return is_valid, None
        except (aiohttp.ClientError, asyncio.TimeoutError):
            async with session.get(
                media_url,
                headers=request_headers,
                timeout=timeout,
                proxy=proxy,
                allow_redirects=True
            ) as response:
                if response.status == 403:
                    return False, 403
                is_valid, _ = await _validate_media_response(
                    response, media_url, is_video, allow_read_content=True
                )
                return is_valid, None
    except Exception as e:
        if '403' in str(e) or 'Forbidden' in str(e):
            return False, 403
        return False, None


async def _download_media_stream(
    response: aiohttp.ClientResponse,
    file_path: str,
    content_preview: Optional[bytes] = None
) -> bool:
    """下载媒体流到文件

    Args:
        response: HTTP响应对象
        file_path: 文件路径
        content_preview: 已读取的内容预览（如果Content-Type为空）

    Returns:
        下载是否成功
    """
    try:
        file_dir = os.path.dirname(file_path)
        if file_dir:
            os.makedirs(file_dir, exist_ok=True)
        
        with open(file_path, 'wb') as f:
            if content_preview:
                f.write(content_preview)
            async for chunk in response.content.iter_chunked(_DOWNLOAD_CHUNK_SIZE):
                f.write(chunk)
            f.flush()
        return True
    except Exception as e:
        logger.warning(f"下载媒体流失败: {file_path}, 错误: {e}")
        if os.path.exists(file_path):
            try:
                os.unlink(file_path)
            except Exception:
                pass
        return False


async def download_image_to_file(
    session: aiohttp.ClientSession,
    image_url: str,
    index: int = 0,
    headers: dict = None,
    referer: str = None,
    default_referer: str = None,
    proxy: str = None
) -> Optional[str]:
    """下载图片到临时文件

    Args:
        session: aiohttp会话
        image_url: 图片URL
        index: 图片索引
        headers: 自定义请求头（如果提供，会与默认请求头合并）
        referer: Referer URL，如果提供则使用
        default_referer: 默认Referer URL（如果referer未提供）
        proxy: 代理地址（可选）

    Returns:
        临时文件路径，失败返回None
    """
    try:
        request_headers = _build_request_headers(
            is_video=False,
            referer=referer,
            default_referer=default_referer,
            custom_headers=headers
        )
        
        async with session.get(
            image_url,
            headers=request_headers,
            timeout=aiohttp.ClientTimeout(total=Config.IMAGE_DOWNLOAD_TIMEOUT),
            proxy=proxy
        ) as response:
            response.raise_for_status()
            
            is_valid, content_preview = await _validate_media_response(
                response, image_url, is_video=False, allow_read_content=True
            )
            if not is_valid:
                return None
            
            content_type = response.headers.get('Content-Type', '')
            suffix = get_image_suffix(content_type, image_url)
            
            with tempfile.NamedTemporaryFile(
                delete=False,
                suffix=suffix
            ) as temp_file:
                temp_file_path = os.path.normpath(temp_file.name)
            
            if await _download_media_stream(response, temp_file_path, content_preview):
                return temp_file_path
            return None
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
) -> Optional[Dict[str, Any]]:
    """下载媒体到缓存目录

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
        包含file_path和size_mb的字典，失败返回None
    """
    if not cache_dir:
        return None
    
    try:
        request_headers = _build_request_headers(
            is_video=is_video,
            referer=referer,
            default_referer=default_referer,
            custom_headers=headers
        )
        
        timeout = aiohttp.ClientTimeout(
            total=Config.VIDEO_DOWNLOAD_TIMEOUT if is_video else Config.IMAGE_DOWNLOAD_TIMEOUT
        )
        
        async with session.get(
            media_url,
            headers=request_headers,
            timeout=timeout,
            proxy=proxy
        ) as response:
            response.raise_for_status()
            
            is_valid, content_preview = await _validate_media_response(
                response, media_url, is_video=is_video, allow_read_content=True
            )
            if not is_valid:
                return None
            
            content_type = response.headers.get('Content-Type', '')
            
            size_mb = _extract_size_from_headers(response)
            
            if is_video:
                suffix = get_video_suffix(content_type, media_url)
            else:
                suffix = get_image_suffix(content_type, media_url)
            
            timestamp = int(time.time() * 1000)
            filename = f"{media_id}_{index}_{timestamp}{suffix}"
            file_path = os.path.join(cache_dir, filename)
            
            os.makedirs(cache_dir, exist_ok=True)
            
            if await _download_media_stream(response, file_path, content_preview):
                if size_mb is None:
                    try:
                        file_size_bytes = os.path.getsize(file_path)
                        size_mb = file_size_bytes / (1024 * 1024)
                    except Exception:
                        pass
                return {
                    'file_path': os.path.normpath(file_path),
                    'size_mb': size_mb
                }
            return None
    except Exception as e:
        logger.warning(f"下载媒体到缓存目录失败: {media_url}, 错误: {e}")
        return None


async def pre_download_media(
    session: aiohttp.ClientSession,
    media_items: List[Dict[str, Any]],
    cache_dir: str,
    max_concurrent: int = 3
) -> List[Dict[str, Any]]:
    """预先下载所有媒体到本地

    Args:
        session: aiohttp会话
        media_items: 媒体项列表，每个项包含url_list（URL列表）、media_id、index、
            is_video、headers、referer、default_referer、proxy等字段
        cache_dir: 缓存目录路径
        max_concurrent: 最大并发下载数

    Returns:
        下载结果列表，每个项包含url（第一个URL）、file_path、success、index等字段
    """
    if not cache_dir or not media_items:
        return []

    semaphore = asyncio.Semaphore(max_concurrent)

    async def download_one(item: Dict[str, Any]) -> Dict[str, Any]:
        async with semaphore:
            try:
                url_list = item.get('url_list', [])
                media_id = item.get('media_id', 'media')
                index = item.get('index', 0)
                is_video = item.get('is_video', True)
                item_headers = item.get('headers')
                item_referer = item.get('referer')
                item_default_referer = item.get('default_referer')
                item_proxy = item.get('proxy')

                if not url_list or not isinstance(url_list, list):
                    return {
                        'url': url_list[0] if url_list else None,
                        'file_path': None,
                        'success': False,
                        'index': index
                    }

                for url in url_list:
                    result = await download_media_to_cache(
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
                    if result:
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

    processed_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            item = media_items[i] if i < len(media_items) else {}
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
            item = media_items[i] if i < len(media_items) else {}
            url_list = item.get('url_list', [])
            processed_results.append({
                'url': url_list[0] if url_list else None,
                'file_path': None,
                'success': False,
                'index': item.get('index', i),
                'error': 'Unknown error'
            })

    return processed_results
