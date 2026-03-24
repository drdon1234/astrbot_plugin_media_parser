"""下载通用工具函数集合。"""
import os
import re
from typing import Optional, List, Dict, Any

from ..logger import logger
from ..storage import stamp_subdir


def validate_content_type(
    content_type: str,
    is_video: bool = False
) -> bool:
    """验证Content-Type是否为有效的媒体类型

    Args:
        content_type: Content-Type值（已转换为小写）
        is_video: 是否为视频（True为视频，False为图片）

    Returns:
        是否为有效媒体类型
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
        是否为JSON错误响应
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
        媒体大小(MB)，无法获取时为None
    """
    content_range = response.headers.get("Content-Range")
    if content_range:
        match = re.search(r'/\s*(\d+)', content_range)
        if match:
            try:
                size_bytes = int(match.group(1))
                return size_bytes / (1024 * 1024)
            except (ValueError, TypeError):
                pass
    
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            size_bytes = int(content_length)
            return size_bytes / (1024 * 1024)
        except (ValueError, TypeError):
            pass
    
    return None


def check_cache_dir_available(cache_dir: str) -> bool:
    """检查缓存目录是否可用（可写）

    Args:
        cache_dir: 缓存目录路径

    Returns:
        目录是否可用
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


_IMAGE_CONTENT_TYPE_MAP = {
    'jpeg': '.jpg', 'jpg': '.jpg', 'png': '.png',
    'webp': '.webp', 'gif': '.gif',
}

_IMAGE_EXT_LIST = ['.jpg', '.jpeg', '.png', '.webp', '.gif']


def get_image_suffix(content_type: str = None, url: str = None) -> str:
    """根据Content-Type或URL确定图片文件扩展名

    Args:
        content_type: HTTP Content-Type头
        url: 图片URL

    Returns:
        文件扩展名（.jpg, .png, .webp, .gif），默认返回.jpg
    """
    if content_type:
        ct_lower = content_type.lower()
        for key, ext in _IMAGE_CONTENT_TYPE_MAP.items():
            if key in ct_lower:
                return ext

    if url:
        url_lower = url.lower()
        for ext in _IMAGE_EXT_LIST:
            if ext in url_lower:
                return '.jpg' if ext == '.jpeg' else ext

    return '.jpg'


_VIDEO_CONTENT_TYPE_MAP = [
    ('f4v', '.f4v'), ('mp4', '.mp4'), ('matroska', '.mkv'), ('mkv', '.mkv'),
    ('quicktime', '.mov'), ('mov', '.mov'), ('x-msvideo', '.avi'), ('avi', '.avi'),
    ('x-flv', '.flv'), ('flv', '.flv'), ('webm', '.webm'),
    ('x-ms-wmv', '.wmv'), ('wmv', '.wmv'),
]

_VIDEO_EXT_LIST = ['.mp4', '.mkv', '.mov', '.avi', '.f4v', '.flv', '.webm', '.wmv']


def get_video_suffix(content_type: str = None, url: str = None) -> str:
    """根据Content-Type或URL确定视频文件扩展名

    Args:
        content_type: HTTP Content-Type头
        url: 视频URL

    Returns:
        文件扩展名（.mp4, .mkv, .mov, .avi, .flv, .f4v, .webm, .wmv），默认返回.mp4
    """
    if content_type:
        ct_lower = content_type.lower()
        for key, ext in _VIDEO_CONTENT_TYPE_MAP:
            if key in ct_lower:
                return ext

    if url:
        url_lower = url.lower()
        for ext in _VIDEO_EXT_LIST:
            if ext in url_lower:
                return ext

    return '.mp4'


def strip_media_prefixes(url: str) -> str:
    """剥离媒体URL前缀，返回可直接访问的URL。

    处理顺序：dash -> m3u8 -> range
    """
    if not url:
        return ""

    stripped = url
    if stripped.startswith('dash:'):
        stripped = stripped[5:].split('||', 1)[0]
    if stripped.startswith('m3u8:'):
        stripped = stripped[5:]
    if stripped.startswith('range:'):
        stripped = stripped[6:]
    return stripped


def process_gather_results(
    results: List[Any],
    items: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """处理 asyncio.gather 返回的下载结果，统一错误处理逻辑
    
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


def generate_cache_file_path(
    cache_dir: str,
    media_id: str,
    media_type: str,
    index: int,
    content_type: str = None,
    url: str = None
) -> str:
    """生成缓存文件路径
    
    Args:
        cache_dir: 缓存目录路径
        media_id: 媒体ID
        media_type: 媒体类型，'video' 或 'image'
        index: 媒体索引
        content_type: HTTP Content-Type头（可选）
        url: 媒体URL（可选）
        
    Returns:
        缓存文件路径（已标准化）
    """
    if media_type == 'video':
        suffix = get_video_suffix(content_type, url)
        filename = f"video_{index}{suffix}"
    else:
        suffix = get_image_suffix(content_type, url)
        filename = f"image_{index}{suffix}"
    
    cache_subdir = os.path.join(cache_dir, media_id)
    os.makedirs(cache_subdir, exist_ok=True)
    stamp_subdir(cache_subdir)
    return os.path.normpath(os.path.join(cache_subdir, filename))

