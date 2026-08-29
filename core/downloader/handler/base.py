"""下载处理器基类与通用下载辅助函数。"""

import asyncio
import os
import re
import uuid
from typing import Optional, Callable, Dict, Any, Tuple

import aiohttp

from ...logger import logger

from ...storage import cleanup_file
from ...constants import Config
from ..utils import extract_size_from_headers
from ..validator import validate_media_response
from ..budget import ByteBudget, DownloadLimitExceeded, resolve_max_bytes
from ..fileio import run_blocking


def _is_retryable_exception(exc: BaseException) -> bool:
    """判断异常是否适合短暂重试。"""
    if isinstance(exc, asyncio.CancelledError):
        return False
    retryable_types = (
        aiohttp.ClientConnectionError,
        aiohttp.ServerDisconnectedError,
        aiohttp.ServerTimeoutError,
        aiohttp.ClientOSError,
        asyncio.TimeoutError,
    )
    if isinstance(exc, retryable_types):
        return True
    if isinstance(exc, aiohttp.ClientResponseError):
        return exc.status in {408, 425, 429, 500, 502, 503, 504}
    return False


async def _sleep_before_retry(attempt: int) -> None:
    """按指数退避等待下一次重试。"""
    delay = Config.DOWNLOAD_RETRY_BASE_DELAY * (2 ** max(0, attempt - 1))
    await asyncio.sleep(delay)


def _format_download_error(exc: BaseException) -> str:
    """将下载异常格式化为用户可读的短文本。"""
    if isinstance(exc, aiohttp.ClientResponseError):
        return f"HTTP {exc.status}: {exc.message}"
    if isinstance(exc, asyncio.TimeoutError):
        return "请求超时"
    text = str(exc).strip()
    return text or type(exc).__name__


def _status_code_from_exception(exc: BaseException) -> Optional[int]:
    """从 aiohttp 异常中提取 HTTP 状态码。"""
    if isinstance(exc, aiohttp.ClientResponseError):
        return exc.status
    return None


async def _get_file_size(
    session: aiohttp.ClientSession, url: str, headers: dict = None, proxy: str = None
) -> Optional[int]:
    """用一次 0-0 探测确认服务端真正支持 Range，并返回总大小。"""
    try:
        request_headers = (headers or {}).copy()
        request_headers["Range"] = "bytes=0-0"
        timeout = aiohttp.ClientTimeout(total=Config.VIDEO_SIZE_CHECK_TIMEOUT)
        response = await session.get(
            url,
            headers=request_headers,
            timeout=timeout,
            proxy=proxy,
            allow_redirects=True,
        )
        async with response:
            # 200 表示服务端忽略 Range。绝不能读取正文或启动并发分片。
            if response.status != 206:
                logger.debug(
                    f"Range探测未返回206，跳过Range模式: {url}, "
                    f"status={response.status}"
                )
                return None
            parsed = _parse_content_range(response.headers.get("Content-Range"))
            if not parsed:
                return None
            start, end, total = parsed
            if start != 0 or end != 0 or total <= 1:
                return None
            return total
    except Exception as e:
        logger.debug(f"获取文件大小失败: {url}, 错误: {e}")

    return None


def _parse_content_range(value: Optional[str]) -> Optional[Tuple[int, int, int]]:
    """严格解析 ``Content-Range: bytes start-end/total``。"""
    match = re.fullmatch(
        r"\s*bytes\s+(\d+)-(\d+)/(\d+)\s*",
        value or "",
        re.IGNORECASE,
    )
    if not match:
        return None
    start, end, total = (int(part) for part in match.groups())
    if start > end or end >= total:
        return None
    return start, end, total


async def _download_range(
    session: aiohttp.ClientSession,
    url: str,
    start: int,
    end: int,
    headers: dict = None,
    proxy: str = None,
    chunk_index: int = 0,
    total_size: Optional[int] = None,
) -> Optional[bytes]:
    """下载指定字节范围的数据，失败返回 None。"""
    try:
        request_headers = (headers or {}).copy()
        request_headers["Range"] = f"bytes={start}-{end}"

        timeout = aiohttp.ClientTimeout(total=Config.VIDEO_DOWNLOAD_TIMEOUT)
        response = await session.get(
            url,
            headers=request_headers,
            timeout=timeout,
            proxy=proxy,
            allow_redirects=True,
        )
        async with response:
            if response.status == 206:
                parsed = _parse_content_range(response.headers.get("Content-Range"))
                if (
                    not parsed
                    or parsed[0] != start
                    or parsed[1] != end
                    or (total_size is not None and parsed[2] != total_size)
                ):
                    logger.warning(
                        f"Range响应范围异常: chunk={chunk_index}, "
                        f"expected={start}-{end}, "
                        f"actual={response.headers.get('Content-Range')}"
                    )
                    return None
                expected = end - start + 1
                chunks = []
                received = 0
                async for data in response.content.iter_chunked(
                    min(Config.STREAM_DOWNLOAD_CHUNK_SIZE, expected + 1)
                ):
                    received += len(data)
                    if received > expected:
                        return None
                    chunks.append(data)
                return b"".join(chunks) if received == expected else None
            logger.warning(
                f"Range下载失败: chunk={chunk_index}, "
                f"status={response.status}, range={start}-{end}"
            )
    except Exception as e:
        logger.warning(
            f"Range下载异常: chunk={chunk_index}, range={start}-{end}, 错误: {e}"
        )
    return None


def _temporary_path(path: str) -> str:
    return f"{path}.{uuid.uuid4().hex}.part"


def _prepare_range_file(path: str, size: int) -> None:
    output_dir = os.path.dirname(path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(path, "wb") as output_file:
        output_file.truncate(size)


def _write_range_chunk(path: str, offset: int, data: bytes) -> None:
    with open(path, "r+b") as output_file:
        output_file.seek(offset)
        output_file.write(data)


def _replace_file(source: str, destination: str) -> None:
    os.replace(source, destination)


async def range_download_file(
    session: aiohttp.ClientSession,
    url: str,
    output_path: str,
    headers: dict = None,
    proxy: str = None,
    chunk_size: int = Config.RANGE_DOWNLOAD_CHUNK_SIZE,
    max_concurrent: int = Config.RANGE_DOWNLOAD_MAX_CONCURRENT,
    max_bytes: Optional[int] = None,
    budget: Optional[ByteBudget] = None,
) -> Optional[Dict[str, Any]]:
    """使用并发 Range 下载单个 URL 到指定文件路径。"""
    if not output_path:
        return None
    try:
        chunk_size = max(64 * 1024, int(chunk_size))
        max_concurrent = min(16, max(1, int(max_concurrent)))
    except (TypeError, ValueError):
        chunk_size = Config.RANGE_DOWNLOAD_CHUNK_SIZE
        max_concurrent = min(16, Config.RANGE_DOWNLOAD_MAX_CONCURRENT)

    file_size = await _get_file_size(session, url, headers, proxy)
    if file_size is None:
        logger.debug(f"Range下载无法获取文件大小: {url}")
        return None

    active_budget = budget or ByteBudget(resolve_max_bytes(max_bytes, is_video=True))
    try:
        await active_budget.consume(file_size)
    except DownloadLimitExceeded as e:
        logger.warning(f"Range下载已拒绝: {url}, 错误: {e}")
        return None
    budget_reserved = file_size

    num_chunks = (file_size + chunk_size - 1) // chunk_size
    if num_chunks <= 1:
        logger.debug(f"Range下载文件分片数不足，跳过Range模式: {url}, size={file_size}")
        await active_budget.release(budget_reserved)
        return None

    logger.debug(
        f"开始Range下载: {url}, "
        f"size={file_size}, chunks={num_chunks}, concurrent={max_concurrent}"
    )

    temp_path = _temporary_path(output_path)
    try:
        await run_blocking(_prepare_range_file, temp_path, file_size)
    except Exception as e:
        logger.warning(f"创建Range目标文件失败: {output_path}, 错误: {e}")
        cleanup_file(temp_path)
        await active_budget.release(budget_reserved)
        return None

    semaphore = asyncio.Semaphore(max_concurrent)

    async def download_chunk(chunk_idx: int) -> Tuple[int, bool]:
        """并发下载单个分片并写入目标文件的正确偏移。"""
        async with semaphore:
            start = chunk_idx * chunk_size
            end = min(start + chunk_size - 1, file_size - 1)
            data = await _download_range(
                session,
                url,
                start,
                end,
                headers,
                proxy,
                chunk_idx,
                file_size,
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
                await run_blocking(_write_range_chunk, temp_path, start, data)
                return chunk_idx, True
            except Exception as write_error:
                logger.warning(
                    f"写入Range分片失败: chunk={chunk_idx}, 错误: {write_error}"
                )
                return chunk_idx, False

    tasks = [asyncio.create_task(download_chunk(i)) for i in range(num_chunks)]
    try:
        results = await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        cleanup_file(temp_path)
        await active_budget.release(budget_reserved)
        raise
    except Exception as e:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.warning(f"Range下载写入流程失败: {url}, 错误: {e}")
        cleanup_file(temp_path)
        await active_budget.release(budget_reserved)
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
        cleanup_file(temp_path)
        await active_budget.release(budget_reserved)
        return None

    try:
        actual_size = await run_blocking(os.path.getsize, temp_path)
    except Exception as e:
        logger.warning(f"读取Range下载文件大小失败: {output_path}, 错误: {e}")
        cleanup_file(temp_path)
        await active_budget.release(budget_reserved)
        return None

    if actual_size != file_size:
        logger.warning(
            f"Range下载文件大小异常: {url}, expected={file_size}, actual={actual_size}"
        )
        cleanup_file(temp_path)
        await active_budget.release(budget_reserved)
        return None

    try:
        _replace_file(temp_path, output_path)
    except asyncio.CancelledError:
        cleanup_file(temp_path)
        await active_budget.release(budget_reserved)
        raise
    except Exception as e:
        logger.warning(f"提交Range下载文件失败: {output_path}, 错误: {e}")
        cleanup_file(temp_path)
        await active_budget.release(budget_reserved)
        return None

    size_mb = actual_size / (1024 * 1024)
    logger.debug(f"Range下载完成: {url}, file={output_path}, size={size_mb:.2f}MB")
    return {"file_path": os.path.normpath(output_path), "size_mb": size_mb}


async def download_media_stream(
    response: aiohttp.ClientResponse,
    file_path: str,
    content_preview: Optional[bytes] = None,
    is_video: bool = True,
    max_bytes: Optional[int] = None,
    budget: Optional[ByteBudget] = None,
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
    active_budget = budget or ByteBudget(
        resolve_max_bytes(max_bytes, is_video=is_video)
    )
    temp_path = _temporary_path(file_path)
    written = 0
    output_file = None
    try:
        file_dir = os.path.dirname(file_path)
        if file_dir:
            await run_blocking(os.makedirs, file_dir, exist_ok=True)
        output_file = await run_blocking(open, temp_path, "wb")

        async def write_chunk(chunk: bytes) -> None:
            nonlocal written
            if not chunk:
                return
            await active_budget.consume(len(chunk))
            written += len(chunk)
            await run_blocking(output_file.write, chunk)

        await write_chunk(content_preview or b"")
        async for chunk in response.content.iter_chunked(
            Config.STREAM_DOWNLOAD_CHUNK_SIZE
        ):
            await write_chunk(chunk)

        await run_blocking(output_file.flush)
        await run_blocking(os.fsync, output_file.fileno())
        await run_blocking(output_file.close)
        output_file = None
        _replace_file(temp_path, file_path)
        return True
    except (asyncio.CancelledError, DownloadLimitExceeded):
        if output_file is not None:
            await run_blocking(output_file.close)
        cleanup_file(temp_path)
        await active_budget.release(written)
        raise
    except Exception as e:
        logger.warning(f"下载媒体流失败: {file_path}, 错误: {e}")
        if output_file is not None:
            try:
                await run_blocking(output_file.close)
            except Exception:
                pass
        cleanup_file(temp_path)
        await active_budget.release(written)
        return False


async def download_media_from_url(
    session: aiohttp.ClientSession,
    media_url: str,
    file_path_generator: Callable[[str, str], str],
    is_video: bool = True,
    headers: dict = None,
    proxy: str = None,
    retry_enabled: bool = True,
    max_bytes: Optional[int] = None,
    budget: Optional[ByteBudget] = None,
) -> Tuple[Optional[str], Optional[float], Optional[int], Optional[str]]:
    """通用媒体下载函数，封装公共的下载逻辑

    Args:
        session: aiohttp会话
        media_url: 媒体URL
        file_path_generator: 文件路径生成函数，接受 (content_type, media_url) 参数，返回文件路径
        is_video: 是否为视频（True为视频，False为图片）
        headers: 请求头字典
        proxy: 代理地址（可选）

    Returns:
        (file_path, size_mb, status_code, error) 元组；
        成功时 error 为 None，失败时尽量保留 HTTP 状态码与错误文本。
    """
    attempts = Config.DOWNLOAD_RETRY_ATTEMPTS if retry_enabled else 1
    last_error = None
    last_status_code = None
    for attempt in range(1, attempts + 1):
        try:
            request_headers = (headers or {}).copy()
            request_headers.pop("Range", None)
            timeout = aiohttp.ClientTimeout(
                total=Config.VIDEO_DOWNLOAD_TIMEOUT
                if is_video
                else Config.IMAGE_DOWNLOAD_TIMEOUT
            )
            response = await session.get(
                media_url,
                headers=request_headers,
                timeout=timeout,
                proxy=proxy,
                allow_redirects=True,
            )
            async with response:
                last_status_code = response.status
                response.raise_for_status()
                if response.status != 200:
                    return (
                        None,
                        None,
                        response.status,
                        "普通媒体下载未返回完整HTTP 200响应",
                    )
                is_valid, content_preview = await validate_media_response(
                    response, media_url, is_video=is_video, allow_read_content=True
                )
                if not is_valid:
                    return (None, None, response.status, "响应不是有效媒体")

                content_type = response.headers.get("Content-Type", "")
                size_mb = extract_size_from_headers(response)
                file_path = file_path_generator(content_type, media_url)

                hard_limit = resolve_max_bytes(max_bytes, is_video=is_video)
                declared_length = response.headers.get("Content-Length")
                if declared_length:
                    try:
                        if int(declared_length) > hard_limit:
                            raise DownloadLimitExceeded("响应声明大小超过下载硬限制")
                    except ValueError:
                        pass

                if await download_media_stream(
                    response,
                    file_path,
                    content_preview,
                    is_video=is_video,
                    max_bytes=hard_limit,
                    budget=budget,
                ):
                    if size_mb is None:
                        try:
                            file_size_bytes = os.path.getsize(file_path)
                            size_mb = file_size_bytes / (1024 * 1024)
                        except Exception:
                            pass
                    return os.path.normpath(file_path), size_mb, response.status, None
                return None, None, response.status, "写入媒体文件失败"
        except asyncio.CancelledError:
            raise
        except Exception as e:
            last_error = e
            last_status_code = _status_code_from_exception(e) or last_status_code
            if attempt < attempts and _is_retryable_exception(e):
                logger.debug(
                    f"下载媒体失败，将重试({attempt}/{attempts}): "
                    f"{media_url}, 错误: {_format_download_error(e)}"
                )
                await _sleep_before_retry(attempt)
                continue
            logger.warning(
                f"下载媒体失败: {media_url}, 错误: {_format_download_error(e)}"
            )
            break
    if last_error:
        logger.debug(f"最终下载错误: {_format_download_error(last_error)}")
    return (
        None,
        None,
        last_status_code,
        _format_download_error(last_error) if last_error else "下载失败",
    )
