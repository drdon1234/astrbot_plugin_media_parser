"""独立音频下载处理器，校验音频格式并以硬限额写入缓存。"""

import asyncio
import os
from typing import Any, Dict, Optional

import aiohttp

from ...logger import logger

from ...constants import Config
from ...storage import cleanup_file
from ..budget import ByteBudget, DownloadLimitExceeded
from ..utils import generate_cache_file_path
from .base import (
    _format_download_error,
    _is_retryable_exception,
    _sleep_before_retry,
    download_media_stream,
)


_MAX_AUDIO_BYTES = 128 * 1024 * 1024
_AUDIO_CONTENT_TYPES = {
    "audio/mpeg": "audio/mpeg",
    "audio/mp3": "audio/mpeg",
    "audio/x-mp3": "audio/mpeg",
    "audio/mp4": "audio/mp4",
    "audio/m4a": "audio/mp4",
    "audio/x-m4a": "audio/mp4",
    "audio/aac": "audio/aac",
    "audio/aacp": "audio/aac",
    "audio/x-aac": "audio/aac",
    "audio/flac": "audio/flac",
    "audio/x-flac": "audio/flac",
    "audio/ogg": "audio/ogg",
    "application/ogg": "audio/ogg",
    "audio/opus": "audio/ogg",
    "audio/wav": "audio/wav",
    "audio/wave": "audio/wav",
    "audio/x-wav": "audio/wav",
    "audio/vnd.wave": "audio/wav",
}
_GENERIC_CONTENT_TYPES = frozenset({
    "", "application/octet-stream", "binary/octet-stream", "application/x-binary",
})


def _detect_audio_type(preview: bytes, declared_type: str) -> Optional[str]:
    """使用文件签名确定音频类型，拒绝 MIME 冲突和非音频容器。"""
    detected = None
    if preview.startswith(b"ID3") and len(preview) >= 10:
        detected = "audio/mpeg"
    elif preview.startswith(b"fLaC"):
        detected = "audio/flac"
    elif preview.startswith(b"RIFF") and preview[8:12] == b"WAVE":
        detected = "audio/wav"
    elif preview.startswith(b"OggS") and any(
        marker in preview for marker in (b"OpusHead", b"\x01vorbis", b"\x7fFLAC")
    ):
        detected = "audio/ogg"
    elif len(preview) >= 12 and preview[4:8] == b"ftyp":
        if declared_type == "audio/mp4" or b"M4A " in preview[8:64]:
            detected = "audio/mp4"
    elif len(preview) >= 4 and preview[0] == 0xFF:
        if preview[1] & 0xF6 == 0xF0:
            detected = "audio/aac"
        elif (
            preview[1] & 0xE0 == 0xE0
            and preview[1] & 0x18 != 0x08
            and preview[1] & 0x06 == 0x02
            and preview[2] & 0xF0 not in (0, 0xF0)
            and preview[2] & 0x0C != 0x0C
        ):
            detected = "audio/mpeg"
    if declared_type and detected != declared_type:
        return None
    return detected


async def download_audio_to_cache(
    session: aiohttp.ClientSession,
    audio_url: str,
    cache_dir: str,
    media_id: str,
    index: int = 0,
    headers: Optional[Dict[str, str]] = None,
    proxy: Optional[str] = None,
    max_bytes: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """将完整音频下载到缓存，失败时返回状态和可读原因。

    Args:
        session: HTTP 会话。
        audio_url: 单个音频候选地址。
        cache_dir: 缓存根目录。
        media_id: 本次媒体目录标识。
        index: 音频索引。
        headers: 源站要求的请求头。
        proxy: 本次请求的代理地址。
        max_bytes: 单个音频的配置限额；未设置时仍受安全上限约束。

    Returns:
        包含文件路径、大小、状态码或失败原因的下载结果。
    """
    if not cache_dir:
        return None
    configured_limit = int(max_bytes) if max_bytes and max_bytes > 0 else None
    hard_limit = min(configured_limit or _MAX_AUDIO_BYTES, _MAX_AUDIO_BYTES)
    limit_source = (
        "configured"
        if configured_limit is not None and configured_limit <= _MAX_AUDIO_BYTES
        else "safety"
    )
    last_status = None
    last_error: Optional[BaseException] = None
    for attempt in range(1, Config.DOWNLOAD_RETRY_ATTEMPTS + 1):
        file_path = None
        try:
            request_headers = {
                key: value for key, value in (headers or {}).items()
                if key.lower() != "range"
            }
            response = await session.get(
                audio_url,
                headers=request_headers,
                proxy=proxy,
                timeout=aiohttp.ClientTimeout(total=Config.VIDEO_DOWNLOAD_TIMEOUT),
                allow_redirects=True,
            )
            async with response:
                last_status = response.status
                response.raise_for_status()
                if response.status != 200:
                    raise ValueError("音频下载未返回完整 HTTP 200 响应")
                declared_size = response.content_length
                if declared_size is not None and declared_size > hard_limit:
                    raise DownloadLimitExceeded(
                        "音频声明大小超过下载限制",
                        limit_source=limit_source,
                        observed_bytes=declared_size,
                    )
                content_type = response.headers.get("Content-Type", "")
                content_type = content_type.split(";", 1)[0].strip().lower()
                declared_type = _AUDIO_CONTENT_TYPES.get(content_type, "")
                if not declared_type and content_type not in _GENERIC_CONTENT_TYPES:
                    raise ValueError("响应不是受支持的音频类型")
                try:
                    preview = await response.content.readexactly(512)
                except asyncio.IncompleteReadError as error:
                    preview = error.partial
                actual_type = _detect_audio_type(preview, declared_type)
                if not actual_type:
                    raise ValueError("响应内容不是受支持的音频或与声明类型不符")
                file_path = generate_cache_file_path(
                    cache_dir, media_id, "audio", index, actual_type, audio_url
                )
                downloaded = await download_media_stream(
                    response,
                    file_path,
                    content_preview=preview,
                    budget=ByteBudget(hard_limit, limit_source=limit_source),
                )
                if not downloaded:
                    raise OSError("写入音频文件失败")
                return {
                    "file_path": file_path,
                    "size_mb": os.path.getsize(file_path) / (1024 * 1024),
                    "status_code": last_status,
                }
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError,
                DownloadLimitExceeded) as error:
            if file_path:
                cleanup_file(file_path)
            last_error = error
            if isinstance(error, aiohttp.ClientResponseError):
                last_status = error.status
            if attempt < Config.DOWNLOAD_RETRY_ATTEMPTS and _is_retryable_exception(error):
                await _sleep_before_retry(attempt)
                continue
            logger.warning(f"音频下载失败: {audio_url}, 错误: {_format_download_error(error)}")
            break
    limit_error = last_error if isinstance(last_error, DownloadLimitExceeded) else None
    return {
        "file_path": None,
        "size_mb": (
            limit_error.observed_bytes / (1024 * 1024)
            if limit_error and limit_error.observed_bytes is not None else None
        ),
        "status_code": last_status,
        "error": _format_download_error(last_error) if last_error else "音频下载失败",
        "limit_source": limit_error.limit_source if limit_error else None,
    }
