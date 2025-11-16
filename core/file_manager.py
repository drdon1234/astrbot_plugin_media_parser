# -*- coding: utf-8 -*-
"""
文件管理模块
负责缓存目录检查等文件处理相关的方法
"""
import os
from typing import List, Optional

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


def check_cache_dir_available(cache_dir: str) -> bool:
    """检查缓存目录是否可用（可写）

    Args:
        cache_dir: 缓存目录路径

    Returns:
        如果目录可用返回True，否则返回False
    """
    if not cache_dir:
        return False
    try:
        os.makedirs(cache_dir, exist_ok=True)
        test_file = os.path.join(cache_dir, ".test_write")
        try:
            with open(test_file, 'w') as f:
                f.write("test")
            os.unlink(test_file)
            return True
        except Exception as e:
            logger.warning(f"检查缓存目录写入权限失败: {e}")
            return False
    except Exception as e:
        logger.warning(f"检查缓存目录可用性失败: {e}")
        return False


def get_image_suffix(content_type: str = None, url: str = None) -> str:
    """根据Content-Type或URL确定图片文件扩展名

    Args:
        content_type: HTTP Content-Type头
        url: 图片URL

    Returns:
        文件扩展名（.jpg, .png, .webp, .gif），默认返回.jpg
    """
    if content_type:
        if 'jpeg' in content_type or 'jpg' in content_type:
            return '.jpg'
        elif 'png' in content_type:
            return '.png'
        elif 'webp' in content_type:
            return '.webp'
        elif 'gif' in content_type:
            return '.gif'

    if url:
        url_lower = url.lower()
        if '.jpg' in url_lower or '.jpeg' in url_lower:
            return '.jpg'
        elif '.png' in url_lower:
            return '.png'
        elif '.webp' in url_lower:
            return '.webp'
        elif '.gif' in url_lower:
            return '.gif'

    return '.jpg'


def get_video_suffix(content_type: str = None, url: str = None) -> str:
    """根据Content-Type或URL确定视频文件扩展名

    Args:
        content_type: HTTP Content-Type头
        url: 视频URL

    Returns:
        文件扩展名（.mp4, .mkv, .mov, .avi, .flv, .f4v, .webm, .wmv），默认返回.mp4
    """
    if content_type:
        content_type_lower = content_type.lower()
        if 'mp4' in content_type_lower:
            return '.mp4'
        elif 'matroska' in content_type_lower or 'mkv' in content_type_lower:
            return '.mkv'
        elif 'quicktime' in content_type_lower or 'mov' in content_type_lower:
            return '.mov'
        elif 'avi' in content_type_lower or 'x-msvideo' in content_type_lower:
            return '.avi'
        elif 'x-flv' in content_type_lower or 'flv' in content_type_lower or 'f4v' in content_type_lower:
            if 'f4v' in content_type_lower:
                return '.f4v'
            return '.flv'
        elif 'webm' in content_type_lower:
            return '.webm'
        elif 'wmv' in content_type_lower or 'x-ms-wmv' in content_type_lower:
            return '.wmv'
        elif content_type_lower.startswith('video/'):
            if '/mp4' in content_type_lower:
                return '.mp4'
            elif '/webm' in content_type_lower:
                return '.webm'
            elif '/quicktime' in content_type_lower or '/mov' in content_type_lower:
                return '.mov'
            elif '/flv' in content_type_lower or '/f4v' in content_type_lower:
                if '/f4v' in content_type_lower:
                    return '.f4v'
                return '.flv'
            elif '/avi' in content_type_lower:
                return '.avi'
            elif '/wmv' in content_type_lower:
                return '.wmv'
            elif '/matroska' in content_type_lower or '/mkv' in content_type_lower:
                return '.mkv'

    if url:
        url_lower = url.lower()
        if '.mp4' in url_lower:
            return '.mp4'
        elif '.mkv' in url_lower:
            return '.mkv'
        elif '.mov' in url_lower:
            return '.mov'
        elif '.avi' in url_lower:
            return '.avi'
        elif '.f4v' in url_lower:
            return '.f4v'
        elif '.flv' in url_lower:
            return '.flv'
        elif '.webm' in url_lower:
            return '.webm'
        elif '.wmv' in url_lower:
            return '.wmv'

    return '.mp4'


def cleanup_files(file_paths: List[str]) -> None:
    """清理文件列表

    Args:
        file_paths: 文件路径列表
    """
    for file_path in file_paths:
        if file_path and os.path.exists(file_path):
            try:
                os.unlink(file_path)
            except Exception as e:
                logger.warning(f"清理文件失败: {file_path}, 错误: {e}")

