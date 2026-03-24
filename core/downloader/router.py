"""下载路由工具，根据链接特征识别媒体类型。"""
import re
from typing import Optional, Dict, Any, Literal

import aiohttp

from ..logger import logger
from .handler.image import download_image_to_cache
from .handler.normal_video import download_video_to_cache
from .handler.range_downloader import download_video_with_range_to_cache
from .handler.dash import download_dash_to_cache
from .handler.m3u8 import M3U8Handler


def detect_media_type(url: str) -> Literal['m3u8', 'image', 'video']:
    """检测媒体类型

    Args:
        url: 媒体URL

    Returns:
        媒体类型：'m3u8'、'image' 或 'video'
    """
    if not url:
        return 'video'
    
    url_lower = url.lower()
    url_path = url_lower.split('?')[0].split('#')[0]
    
    if '.m3u8' in url_lower:
        return 'm3u8'
    
    media_types = {
        'image': ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg'],
        'video': ['mp4', 'mkv', 'mov', 'avi', 'flv', 'f4v', 'webm', 'wmv', 'm4v']
    }
    
    for media_type, extensions in media_types.items():
        for ext in extensions:
            if url_lower.endswith(f'.{ext}') or f'.{ext}?' in url_lower:
                return media_type
            if url_path.endswith(ext):
                if len(url_path) == len(ext) or not url_path[-(len(ext) + 1)].isalpha():
                    return media_type
    
    image_patterns = [
        r'[._!-](jpg|jpeg|png|gif|webp|bmp|svg)(_|\d|$)',
    ]
    
    video_patterns = [
        r'[._!-](mp4|mkv|mov|avi|flv|f4v|webm|wmv|m4v|3gp|ts)(_|\d|$)',
    ]
    
    for pattern in image_patterns:
        if re.search(pattern, url_lower):
            return 'image'
    
    for pattern in video_patterns:
        if re.search(pattern, url_lower):
            return 'video'
    
    return 'video'


async def download_media(
    session: aiohttp.ClientSession,
    media_url: str,
    media_type: Optional[Literal['dash', 'm3u8', 'image', 'video']] = None,
    cache_dir: Optional[str] = None,
    media_id: Optional[str] = None,
    index: int = 0,
    headers: dict = None,
    proxy: str = None,
    m3u8_handler: Optional[M3U8Handler] = None,
    use_ffmpeg: bool = True
) -> Optional[Dict[str, Any]]:
    """下载媒体文件

    Args:
        session: aiohttp会话
        media_url: 媒体URL
        media_type: 媒体类型（可选，如果不提供会自动检测）
        cache_dir: 缓存目录
        media_id: 媒体ID
        index: 媒体索引
        headers: 请求头字典
        proxy: 代理地址（可选）
        m3u8_handler: M3U8处理器（可选）
        use_ffmpeg: 是否使用ffmpeg（仅用于M3U8）

    Returns:
        下载结果字典，包含file_path和size_mb字段，失败时为None
    """
    logger.debug(
        f"下载路由: url={media_url[:80]}..., type={media_type}, "
        f"cache_dir={'有' if cache_dir else '无'}"
    )

    actual_url = media_url
    dash_video_url = ""
    dash_audio_url = ""

    if media_url.startswith('dash:'):
        payload = media_url[5:]
        parts = payload.split('||', 1)
        dash_video_url = parts[0].strip()
        dash_audio_url = parts[1].strip() if len(parts) > 1 else ""
        actual_url = dash_video_url
        media_type = 'dash'

    if media_url.startswith('m3u8:'):
        actual_url = media_url[5:]
        media_type = 'm3u8'
    elif media_type is None:
        media_type = detect_media_type(media_url)

    if media_type == 'dash':
        if not cache_dir or not dash_video_url:
            return None
        return await download_dash_to_cache(
            session=session,
            video_url=dash_video_url,
            audio_url=dash_audio_url,
            cache_dir=cache_dir,
            media_id=media_id or 'media',
            index=index,
            headers=headers,
            proxy=proxy
        )
    
    if media_type == 'm3u8':
        if not cache_dir:
            return None
        
        if m3u8_handler is None:
            m3u8_handler = M3U8Handler(
                session=session,
                headers=headers,
                proxy=proxy
            )
        
        return await m3u8_handler.download_m3u8_to_cache(
            m3u8_url=actual_url,
            cache_dir=cache_dir,
            media_id=media_id or 'media',
            index=index,
            use_ffmpeg=use_ffmpeg
        )
    
    elif media_type == 'image':
        file_path = await download_image_to_cache(
            session=session,
            image_url=actual_url,
            cache_dir=cache_dir or '',
            media_id=media_id or 'image',
            index=index,
            headers=headers,
            proxy=proxy
        )
        if file_path:
            return {'file_path': file_path, 'size_mb': None}
        return None
    
    else:
        if not cache_dir:
            return None
        
        use_range_download = False
        
        if actual_url.startswith('range:'):
            actual_url = actual_url[6:]
            use_range_download = True
        
        if use_range_download:
            return await download_video_with_range_to_cache(
                session=session,
                video_url=actual_url,
                cache_dir=cache_dir,
                media_id=media_id or 'media',
                index=index,
                headers=headers,
                proxy=proxy
            )
        else:
            return await download_video_to_cache(
                session=session,
                video_url=actual_url,
                cache_dir=cache_dir,
                media_id=media_id or 'media',
                index=index,
                headers=headers,
                proxy=proxy
            )
