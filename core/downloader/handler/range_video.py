import asyncio
import os
from typing import Dict, Any, Optional, Tuple

import aiohttp

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from ..utils import generate_cache_file_path
from ...constants import Config


async def _get_file_size(
    session: aiohttp.ClientSession,
    url: str,
    headers: dict = None,
    proxy: str = None
) -> Optional[int]:
    """获取文件大小

    Args:
        session: aiohttp会话
        url: 文件URL
        headers: 请求头字典
        proxy: 代理地址（可选）

    Returns:
        文件大小（字节），失败时为None
    """
    try:
        request_headers = headers or {}
        timeout = aiohttp.ClientTimeout(total=Config.VIDEO_SIZE_CHECK_TIMEOUT)
        
        async with session.head(
            url,
            headers=request_headers,
            timeout=timeout,
            proxy=proxy
        ) as response:
            if response.status == 200:
                content_length = response.headers.get('Content-Length')
                if content_length:
                    return int(content_length)
            
            request_headers['Range'] = 'bytes=0-0'
            async with session.get(
                url,
                headers=request_headers,
                timeout=timeout,
                proxy=proxy
            ) as get_response:
                if get_response.status in (200, 206):
                    content_range = get_response.headers.get('Content-Range')
                    if content_range:
                        match = content_range.split('/')
                        if len(match) > 1:
                            return int(match[1])
                    content_length = get_response.headers.get('Content-Length')
                    if content_length:
                        return int(content_length)
    except Exception as e:
        logger.debug(f"获取文件大小失败: {url}, 错误: {e}")
    
    return None


async def _download_range(
    session: aiohttp.ClientSession,
    url: str,
    start: int,
    end: int,
    headers: dict = None,
    proxy: str = None,
    chunk_index: int = 0
) -> Optional[bytes]:
    """下载指定范围的字节

    Args:
        session: aiohttp会话
        url: 文件URL
        start: 起始字节位置
        end: 结束字节位置（包含）
        headers: 请求头字典
        proxy: 代理地址（可选）
        chunk_index: chunk索引（用于日志）

    Returns:
        下载的字节数据，失败时为None
    """
    try:
        request_headers = (headers or {}).copy()
        request_headers['Range'] = f'bytes={start}-{end}'
        
        timeout = aiohttp.ClientTimeout(total=Config.VIDEO_DOWNLOAD_TIMEOUT)
        
        async with session.get(
            url,
            headers=request_headers,
            timeout=timeout,
            proxy=proxy
        ) as response:
            if response.status in (200, 206):
                return await response.read()
            else:
                logger.warning(
                    f"Range下载失败: chunk={chunk_index}, "
                    f"status={response.status}, range={start}-{end}"
                )
    except Exception as e:
        logger.warning(f"Range下载异常: chunk={chunk_index}, range={start}-{end}, 错误: {e}")
    
    return None


async def download_video_to_cache(
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
    """使用并发Range下载视频到缓存目录

    Args:
        session: aiohttp会话
        video_url: 视频URL
        cache_dir: 缓存目录路径
        media_id: 媒体ID
        index: 索引
        headers: 请求头字典
        proxy: 代理地址（可选）
        chunk_size: chunk大小（字节），默认2MB
        max_concurrent: 最大并发数，默认64

    Returns:
        包含file_path和size_mb的字典，失败时为None（会降级为normal_video）
    """
    if not cache_dir:
        return None
    
    try:
        file_size = await _get_file_size(session, video_url, headers, proxy)
        if file_size is None:
            logger.warning(f"无法获取文件大小，降级为normal_video: {video_url}")
            from .normal_video import download_video_to_cache as normal_download
            return await normal_download(
                session, video_url, cache_dir, media_id, index, headers, proxy
            )
        
        num_chunks = (file_size + chunk_size - 1) // chunk_size
        
        if num_chunks <= 1:
            logger.debug(f"文件太小，使用normal_video: {video_url}, size={file_size}")
            from .normal_video import download_video_to_cache as normal_download
            return await normal_download(
                session, video_url, cache_dir, media_id, index, headers, proxy
            )
        
        logger.debug(
            f"开始Range下载: {video_url}, "
            f"size={file_size}, chunks={num_chunks}, "
            f"concurrent={max_concurrent}"
        )
        
        file_path = generate_cache_file_path(
            cache_dir=cache_dir,
            media_id=media_id,
            media_type='video',
            index=index,
            url=video_url
        )
        
        temp_chunks = []
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def download_chunk(chunk_idx: int) -> Tuple[int, Optional[bytes]]:
            """下载单个chunk"""
            async with semaphore:
                start = chunk_idx * chunk_size
                end = min(start + chunk_size - 1, file_size - 1)
                data = await _download_range(
                    session, video_url, start, end, headers, proxy, chunk_idx
                )
                return chunk_idx, data
        
        tasks = [download_chunk(i) for i in range(num_chunks)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        chunks_data = {}
        failed_chunks = []
        
        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"Chunk下载异常: {result}")
                failed_chunks.append(None)
            elif isinstance(result, tuple) and len(result) == 2:
                chunk_idx, data = result
                if data is not None:
                    chunks_data[chunk_idx] = data
                else:
                    failed_chunks.append(chunk_idx)
            else:
                failed_chunks.append(None)
        
        if failed_chunks:
            logger.warning(
                f"部分chunks下载失败 ({len(failed_chunks)}/{num_chunks})，"
                f"降级为normal_video: {video_url}"
            )
            from .normal_video import download_video_to_cache as normal_download
            return await normal_download(
                session, video_url, cache_dir, media_id, index, headers, proxy
            )
        
        try:
            with open(file_path, 'wb') as f:
                for i in range(num_chunks):
                    if i in chunks_data:
                        f.write(chunks_data[i])
                    else:
                        logger.error(f"缺少chunk {i}，降级为normal_video")
                        if os.path.exists(file_path):
                            os.remove(file_path)
                        from .normal_video import download_video_to_cache as normal_download
                        return await normal_download(
                            session, video_url, cache_dir, media_id, index, headers, proxy
                        )
            
            actual_size = os.path.getsize(file_path)
            size_mb = actual_size / (1024 * 1024)
            
            logger.debug(
                f"Range下载完成: {video_url}, "
                f"file={file_path}, size={size_mb:.2f}MB"
            )
            
            return {
                'file_path': os.path.normpath(file_path),
                'size_mb': size_mb
            }
        except Exception as e:
            logger.warning(f"合并chunks失败: {video_url}, 错误: {e}")
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception:
                    pass
            from .normal_video import download_video_to_cache as normal_download
            return await normal_download(
                session, video_url, cache_dir, media_id, index, headers, proxy
            )
    
    except Exception as e:
        logger.warning(f"Range下载失败，降级为normal_video: {video_url}, 错误: {e}")
        from .normal_video import download_video_to_cache as normal_download
        return await normal_download(
            session, video_url, cache_dir, media_id, index, headers, proxy
        )

