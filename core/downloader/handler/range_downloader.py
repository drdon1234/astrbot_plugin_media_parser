from typing import Dict, Any, Optional

import aiohttp

from ...logger import logger
from ...constants import Config
from ..utils import generate_cache_file_path
from .base import range_download_file


async def download_video_with_range_to_cache(
    session: aiohttp.ClientSession,
    video_url: str,
    cache_dir: str,
    media_id: str,
    index: int = 0,
    headers: dict = None,
    proxy: str = None,
    chunk_size: int = Config.RANGE_DOWNLOAD_CHUNK_SIZE,
    max_concurrent: int = Config.RANGE_DOWNLOAD_MAX_CONCURRENT
) -> Optional[Dict[str, Any]]:
    """Range 下载封装：先并发 Range，失败时降级 normal_video。"""
    if not cache_dir:
        return None

    file_path = generate_cache_file_path(
        cache_dir=cache_dir,
        media_id=media_id,
        media_type="video",
        index=index,
        url=video_url
    )

    try:
        result = await range_download_file(
            session=session,
            url=video_url,
            output_path=file_path,
            headers=headers,
            proxy=proxy,
            chunk_size=chunk_size,
            max_concurrent=max_concurrent
        )
    except Exception as e:
        logger.warning(f"Range下载异常，降级为normal_video: {video_url}, 错误: {e}")
        result = None

    if result:
        return result

    logger.debug(f"Range下载不可用，降级为normal_video: {video_url}")
    from .normal_video import download_video_to_cache as normal_download
    return await normal_download(
        session=session,
        video_url=video_url,
        cache_dir=cache_dir,
        media_id=media_id,
        index=index,
        headers=headers,
        proxy=proxy
    )
