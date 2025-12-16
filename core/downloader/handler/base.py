# -*- coding: utf-8 -*-
"""
基础下载处理器
包含通用下载逻辑
"""
import os
from typing import Optional, Callable, Dict, Any, Tuple

import aiohttp

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from ...file_cleaner import cleanup_file
from ..utils import extract_size_from_headers
from ..validator import validate_media_response
from ...constants import Config

_DOWNLOAD_CHUNK_SIZE = 1024 * 1024


async def download_media_stream(
    response: aiohttp.ClientResponse,
    file_path: str,
    content_preview: Optional[bytes] = None,
    is_video: bool = True
) -> bool:
    """下载媒体流到文件

    Args:
        response: HTTP响应对象
        file_path: 文件路径
        content_preview: 已读取的内容预览（如果Content-Type为空）
        is_video: 是否为视频（True为视频使用流式下载，False为图片使用完整下载）

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
            
            if is_video:
                async for chunk in response.content.iter_chunked(_DOWNLOAD_CHUNK_SIZE):
                    f.write(chunk)
            else:
                content = await response.read()
                f.write(content)
            
            f.flush()
        return True
    except Exception as e:
        logger.warning(f"下载媒体流失败: {file_path}, 错误: {e}")
        cleanup_file(file_path)
        return False


async def download_media_from_url(
    session: aiohttp.ClientSession,
    media_url: str,
    file_path_generator: Callable[[str, str], str],
    is_video: bool = True,
    headers: dict = None,
    proxy: str = None
) -> Tuple[Optional[str], Optional[float]]:
    """通用媒体下载函数，封装公共的下载逻辑

    Args:
        session: aiohttp会话
        media_url: 媒体URL
        file_path_generator: 文件路径生成函数，接受 (content_type, media_url) 参数，返回文件路径
        is_video: 是否为视频（True为视频，False为图片）
        headers: 请求头字典
        proxy: 代理地址（可选）

    Returns:
        (file_path, size_mb) 元组，失败返回 (None, None)
    """
    try:
        request_headers = headers or {}
        
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
            
            is_valid, content_preview = await validate_media_response(
                response, media_url, is_video=is_video, allow_read_content=True
            )
            if not is_valid:
                return None, None
            
            content_type = response.headers.get('Content-Type', '')
            size_mb = extract_size_from_headers(response)
            
            file_path = file_path_generator(content_type, media_url)
            
            if await download_media_stream(response, file_path, content_preview, is_video=is_video):
                if size_mb is None:
                    try:
                        file_size_bytes = os.path.getsize(file_path)
                        size_mb = file_size_bytes / (1024 * 1024)
                    except Exception:
                        pass
                return os.path.normpath(file_path), size_mb
            return None, None
    except Exception as e:
        logger.warning(f"下载媒体失败: {media_url}, 错误: {e}")
        return None, None

