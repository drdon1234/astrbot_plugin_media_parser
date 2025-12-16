# -*- coding: utf-8 -*-
"""
下载工具模块
包含纯工具函数，无HTTP请求，无业务逻辑
"""
import os
import re
from typing import Optional

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


def validate_content_type(
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


def check_json_error_response(
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


def extract_size_from_headers(
    response
) -> Optional[float]:
    """从响应头中提取媒体大小

    Args:
        response: HTTP响应对象（aiohttp.ClientResponse）

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

