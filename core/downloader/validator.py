import asyncio
from typing import Optional, Tuple

import aiohttp

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from .utils import (
    validate_content_type,
    check_json_error_response,
    extract_size_from_headers
)
from ..constants import Config

_EMPTY_CONTENT_TYPE_CHECK_SIZE = 64


async def validate_media_response(
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
        
        if check_json_error_response(content_preview, media_url):
            return False, None
        
        return True, content_preview
    
    if not validate_content_type(content_type, is_video):
        return False, None
    
    return True, None


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
        (size_mb, status_code) 元组，size_mb为视频大小(MB)，无法获取时为None，
        status_code为HTTP状态码（如果是403等特殊状态码），否则为None
    """
    if video_url.startswith('m3u8:'):
        video_url = video_url[5:]
    elif video_url.startswith('range:'):
        video_url = video_url[6:]
    
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
                size = extract_size_from_headers(response)
                if size is not None:
                    return size, None
                is_valid, _ = await validate_media_response(
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
                is_valid, _ = await validate_media_response(
                    response, video_url, is_video=True, allow_read_content=True
                )
                if not is_valid:
                    return None, None
                size = extract_size_from_headers(response)
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
    if media_url.startswith('m3u8:'):
        media_url = media_url[5:]
    elif media_url.startswith('range:'):
        media_url = media_url[6:]
    
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
                is_valid, _ = await validate_media_response(
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
                is_valid, _ = await validate_media_response(
                    response, media_url, is_video, allow_read_content=True
                )
                return is_valid, None
    except Exception as e:
        if '403' in str(e) or 'Forbidden' in str(e):
            return False, 403
        return False, None

