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
    """检查缓存目录是否可用（可写）。

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
    """根据Content-Type或URL确定图片文件扩展名。

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


def cleanup_files(file_paths: List[str]) -> None:
    """清理文件列表。

    Args:
        file_paths: 文件路径列表
    """
    for file_path in file_paths:
        if file_path and os.path.exists(file_path):
            try:
                os.unlink(file_path)
            except Exception as e:
                logger.warning(f"清理文件失败: {file_path}, 错误: {e}")


def move_temp_file_to_cache(
    temp_file_path: str,
    cache_dir: str,
    media_id: str,
    index: int
) -> Optional[str]:
    """将临时文件移动到缓存目录。

    Args:
        temp_file_path: 临时文件路径
        cache_dir: 缓存目录路径
        media_id: 媒体ID
        index: 索引

    Returns:
        缓存文件路径，失败返回None
    """
    if not cache_dir or not temp_file_path:
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
            except Exception:
                pass
        return None

    if not os.path.exists(temp_file_path):
        return None

    cache_path = None
    try:
        content = None
        try:
            with open(temp_file_path, 'rb') as f:
                content = f.read()
        except Exception:
            if os.path.exists(temp_file_path):
                try:
                    os.unlink(temp_file_path)
                except Exception:
                    pass
            return None

        if not content:
            if os.path.exists(temp_file_path):
                try:
                    os.unlink(temp_file_path)
                except Exception:
                    pass
            return None

        if content.startswith(b'\xff\xd8'):
            suffix = '.jpg'
        elif content.startswith(b'\x89PNG'):
            suffix = '.png'
        elif content.startswith(b'RIFF') and b'WEBP' in content[:12]:
            suffix = '.webp'
        elif content.startswith(b'GIF'):
            suffix = '.gif'
        else:
            suffix = get_image_suffix(url=temp_file_path)

        os.makedirs(cache_dir, exist_ok=True)

        cache_filename = f"{media_id}_{index}{suffix}"
        cache_path = os.path.join(cache_dir, cache_filename)

        try:
            with open(cache_path, 'wb') as f:
                f.write(content)
        except Exception:
            if os.path.exists(temp_file_path):
                try:
                    os.unlink(temp_file_path)
                except Exception:
                    pass
            return None

        if os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
            except Exception:
                pass

        return os.path.normpath(cache_path)
    except Exception as e:
        logger.warning(f"移动临时文件到缓存目录失败: {e}")
        if os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
            except Exception:
                pass
        if cache_path and os.path.exists(cache_path):
            try:
                os.unlink(cache_path)
            except Exception:
                pass
        return None

