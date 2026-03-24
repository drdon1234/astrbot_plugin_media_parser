"""下载处理器基类与通用下载辅助函数。"""
import asyncio
import os
from typing import Optional, Callable, Dict, Any, Tuple

import aiohttp

from ...logger import logger

from ...storage import cleanup_file
from ..utils import extract_size_from_headers
from ..validator import validate_media_response
from ...constants import Config


async def _get_file_size(
    session: aiohttp.ClientSession,
    url: str,
    headers: dict = None,
    proxy: str = None
) -> Optional[int]:
    """获取文件大小（字节），失败时返回 None。"""
    try:
        request_headers = (headers or {}).copy()
        timeout = aiohttp.ClientTimeout(total=Config.VIDEO_SIZE_CHECK_TIMEOUT)

        async with session.head(
            url,
            headers=request_headers,
            timeout=timeout,
            proxy=proxy
        ) as response:
            if response.status == 200:
                content_length = response.headers.get("Content-Length")
                if content_length:
                    return int(content_length)

        request_headers["Range"] = "bytes=0-0"
        async with session.get(
            url,
            headers=request_headers,
            timeout=timeout,
            proxy=proxy
        ) as get_response:
            if get_response.status in (200, 206):
                content_range = get_response.headers.get("Content-Range")
                if content_range:
                    parts = content_range.split("/")
                    if len(parts) > 1:
                        return int(parts[1])
                content_length = get_response.headers.get("Content-Length")
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
    """下载指定字节范围的数据，失败返回 None。"""
    try:
        request_headers = (headers or {}).copy()
        request_headers["Range"] = f"bytes={start}-{end}"

        timeout = aiohttp.ClientTimeout(total=Config.VIDEO_DOWNLOAD_TIMEOUT)
        async with session.get(
            url,
            headers=request_headers,
            timeout=timeout,
            proxy=proxy
        ) as response:
            if response.status in (200, 206):
                return await response.read()
            logger.warning(
                f"Range下载失败: chunk={chunk_index}, "
                f"status={response.status}, range={start}-{end}"
            )
    except Exception as e:
        logger.warning(
            f"Range下载异常: chunk={chunk_index}, range={start}-{end}, 错误: {e}"
        )
    return None


async def range_download_file(
    session: aiohttp.ClientSession,
    url: str,
    output_path: str,
    headers: dict = None,
    proxy: str = None,
    chunk_size: int = Config.RANGE_DOWNLOAD_CHUNK_SIZE,
    max_concurrent: int = Config.RANGE_DOWNLOAD_MAX_CONCURRENT
) -> Optional[Dict[str, Any]]:
    """使用并发 Range 下载单个 URL 到指定文件路径。"""
    if not output_path:
        return None

    file_size = await _get_file_size(session, url, headers, proxy)
    if file_size is None:
        logger.debug(f"Range下载无法获取文件大小: {url}")
        return None

    num_chunks = (file_size + chunk_size - 1) // chunk_size
    if num_chunks <= 1:
        logger.debug(f"Range下载文件分片数不足，跳过Range模式: {url}, size={file_size}")
        return None

    logger.debug(
        f"开始Range下载: {url}, "
        f"size={file_size}, chunks={num_chunks}, concurrent={max_concurrent}"
    )

    try:
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "wb") as output_file:
            output_file.truncate(file_size)
    except Exception as e:
        logger.warning(f"创建Range目标文件失败: {output_path}, 错误: {e}")
        cleanup_file(output_path)
        return None

    semaphore = asyncio.Semaphore(max_concurrent)
    write_lock = asyncio.Lock()

    async def download_chunk(chunk_idx: int, output_file) -> Tuple[int, bool]:
        """并发下载单个分片并写入目标文件的正确偏移。"""
        async with semaphore:
            start = chunk_idx * chunk_size
            end = min(start + chunk_size - 1, file_size - 1)
            data = await _download_range(
                session, url, start, end, headers, proxy, chunk_idx
            )
            if data is None:
                return chunk_idx, False

            expected_size = end - start + 1
            if len(data) != expected_size:
                logger.warning(
                    f"Range分片长度异常: chunk={chunk_idx}, "
                    f"expected={expected_size}, actual={len(data)}"
                )
                return chunk_idx, False

            try:
                async with write_lock:
                    output_file.seek(start)
                    output_file.write(data)
                return chunk_idx, True
            except Exception as write_error:
                logger.warning(
                    f"写入Range分片失败: chunk={chunk_idx}, 错误: {write_error}"
                )
                return chunk_idx, False

    try:
        with open(output_path, "r+b") as output_file:
            tasks = [download_chunk(i, output_file) for i in range(num_chunks)]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            output_file.flush()
    except Exception as e:
        logger.warning(f"Range下载写入流程失败: {url}, 错误: {e}")
        cleanup_file(output_path)
        return None

    failed_chunks = []
    for result in results:
        if isinstance(result, Exception):
            logger.warning(f"Chunk下载异常: {result}")
            failed_chunks.append(None)
        elif isinstance(result, tuple) and len(result) == 2:
            chunk_idx, success = result
            if not success:
                failed_chunks.append(chunk_idx)
        else:
            failed_chunks.append(None)

    if failed_chunks:
        logger.warning(
            f"部分chunks下载失败 ({len(failed_chunks)}/{num_chunks})，"
            f"放弃Range结果: {url}"
        )
        cleanup_file(output_path)
        return None

    try:
        actual_size = os.path.getsize(output_path)
    except Exception as e:
        logger.warning(f"读取Range下载文件大小失败: {output_path}, 错误: {e}")
        cleanup_file(output_path)
        return None

    if actual_size != file_size:
        logger.warning(
            f"Range下载文件大小异常: {url}, "
            f"expected={file_size}, actual={actual_size}"
        )
        cleanup_file(output_path)
        return None

    size_mb = actual_size / (1024 * 1024)
    logger.debug(
        f"Range下载完成: {url}, file={output_path}, size={size_mb:.2f}MB"
    )
    return {
        "file_path": os.path.normpath(output_path),
        "size_mb": size_mb
    }


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
                async for chunk in response.content.iter_chunked(Config.STREAM_DOWNLOAD_CHUNK_SIZE):
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

