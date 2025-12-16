# -*- coding: utf-8 -*-
import re
from typing import Optional, Dict, Any, Literal

import aiohttp

from .handler.image import download_image_to_file
from .handler.normal_video import download_video_to_cache
from .handler.m3u8 import M3U8Handler


def detect_media_type(url: str) -> Literal['m3u8', 'image', 'video']:
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
    media_type: Optional[Literal['m3u8', 'image', 'video']] = None,
    cache_dir: Optional[str] = None,
    media_id: Optional[str] = None,
    index: int = 0,
    headers: dict = None,
    proxy: str = None,
    m3u8_handler: Optional[M3U8Handler] = None,
    use_ffmpeg: bool = True
) -> Optional[Dict[str, Any]]:
    if media_type is None:
        media_type = detect_media_type(media_url)
    
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
            m3u8_url=media_url,
            cache_dir=cache_dir,
            media_id=media_id or 'media',
            index=index,
            use_ffmpeg=use_ffmpeg
        )
    
    elif media_type == 'image':
        file_path = await download_image_to_file(
            session=session,
            image_url=media_url,
            index=index,
            headers=headers,
            proxy=proxy,
            cache_dir=cache_dir,
            media_id=media_id
        )
        if file_path:
            return {'file_path': file_path, 'size_mb': None}
        return None
    
    else:
        if not cache_dir:
            return None
        
        return await download_video_to_cache(
            session=session,
            video_url=media_url,
            cache_dir=cache_dir,
            media_id=media_id or 'media',
            index=index,
            headers=headers,
            proxy=proxy
        )
