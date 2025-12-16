# -*- coding: utf-8 -*-
"""
图片处理模块
负责下载图片到文件（支持缓存目录或临时文件）
"""
import os
import tempfile
from typing import Optional

import aiohttp

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from ..utils import get_image_suffix
from .base import download_media_from_url


async def download_image_to_file(
    session: aiohttp.ClientSession,
    image_url: str,
    index: int = 0,
    headers: dict = None,
    proxy: str = None,
    cache_dir: Optional[str] = None,
    media_id: Optional[str] = None
) -> Optional[str]:
    """下载图片到文件

    Args:
        session: aiohttp会话
        image_url: 图片URL
        index: 图片索引
        headers: 请求头字典
        proxy: 代理地址（可选）
        cache_dir: 缓存目录（可选，如果提供则下载到缓存目录，否则下载到临时文件）
        media_id: 媒体ID（可选，用于生成缓存文件名）

    Returns:
        文件路径，失败返回None
    """
    if cache_dir and media_id:
        def generate_cache_file_path(content_type: str, url: str) -> str:
            """生成缓存文件路径"""
            suffix = get_image_suffix(content_type, url)
            cache_subdir = os.path.join(cache_dir, media_id)
            os.makedirs(cache_subdir, exist_ok=True)
            filename = f"image_{index}{suffix}"
            return os.path.normpath(os.path.join(cache_subdir, filename))
        
        file_path, _ = await download_media_from_url(
            session=session,
            media_url=image_url,
            file_path_generator=generate_cache_file_path,
            is_video=False,
            headers=headers,
            proxy=proxy
        )
    else:
        def generate_temp_file_path(content_type: str, url: str) -> str:
            """生成临时文件路径"""
            suffix = get_image_suffix(content_type, url)
            with tempfile.NamedTemporaryFile(
                delete=False,
                suffix=suffix
            ) as temp_file:
                return os.path.normpath(temp_file.name)
        
        file_path, _ = await download_media_from_url(
            session=session,
            media_url=image_url,
            file_path_generator=generate_temp_file_path,
            is_video=False,
            headers=headers,
            proxy=proxy
        )
    
    return file_path

