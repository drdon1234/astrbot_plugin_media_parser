"""下载前校验逻辑，确保元数据与链接可用。"""
import asyncio
from typing import Optional, Tuple

import aiohttp

from ..logger import logger

from .utils import (
    validate_content_type,
    check_json_error_response,
    extract_size_from_headers,
    strip_media_prefixes
)
from ..constants import Config

_CONTENT_PREVIEW_CHECK_SIZE = 512
_GENERIC_VIDEO_CONTENT_TYPES = (
    "application/octet-stream",
    "binary/octet-stream",
    "application/x-binary",
)


def _with_range_header(headers: dict = None, range_value: str = "bytes=0-511") -> dict:
    """复制请求头并补充 Range，避免验证阶段拉取完整媒体。"""
    request_headers = (headers or {}).copy()
    request_headers.setdefault("Range", range_value)
    return request_headers


def _is_generic_video_content_type(content_type: str) -> bool:
    """判断视频响应是否只有泛型 Content-Type，需要读取内容预览。"""
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    return (
        not normalized
        or normalized in _GENERIC_VIDEO_CONTENT_TYPES
        or "octet-stream" in normalized
    )


def _has_known_video_signature(content_preview: bytes) -> bool:
    """识别常见视频容器签名，避免误杀泛型二进制媒体。"""
    if not content_preview:
        return False
    head = content_preview[:64]
    return (
        b"ftyp" in head[:16] or
        head.startswith(b"\x1a\x45\xdf\xa3") or
        head.startswith(b"FLV") or
        (head.startswith(b"RIFF") and b"AVI" in head[:16]) or
        (
            len(content_preview) > 188 and
            content_preview[0] == 0x47 and
            content_preview[188] == 0x47
        )
    )


def _is_obvious_non_media_preview(
    content_preview: bytes,
    media_url: str
) -> bool:
    """识别泛型响应里明显不是媒体的 HTML/JSON/纯文本内容。"""
    if not content_preview:
        return True

    if _has_known_video_signature(content_preview):
        return False

    stripped = content_preview.lstrip(b"\xef\xbb\xbf\r\n\t ")
    lowered = stripped[:128].lower()
    if stripped.startswith((b"{", b"[")):
        logger.warning(f"媒体URL包含JSON响应（非媒体内容）: {media_url}")
        return True

    if (
        lowered.startswith((b"<!doctype", b"<html", b"<body", b"<?xml", b"<"))
        or b"<html" in lowered
    ):
        logger.warning(f"媒体URL包含HTML响应（非媒体内容）: {media_url}")
        return True

    text_like = all(
        byte in b"\r\n\t" or 32 <= byte <= 126
        for byte in stripped
    )
    if text_like and any(
        marker in lowered
        for marker in (
            b"error",
            b"forbidden",
            b"access denied",
            b"not found",
            b"not media",
            b"gateway",
            b"timeout",
            b"unauthorized",
        )
    ):
        logger.warning(f"媒体URL包含文本错误响应（非媒体内容）: {media_url}")
        return True

    return False


async def validate_media_response(
    response: aiohttp.ClientResponse,
    media_url: str,
    is_video: bool = False,
    allow_read_content: bool = True
) -> Tuple[bool, Optional[bytes]]:
    """验证响应是否为有效的媒体响应

    Args:
        response: HTTP响应对象
        media_url: 媒体URL（用于日志）
        is_video: 是否为视频（True为视频，False为图片）
        allow_read_content: 是否允许读取内容（HEAD请求时为False）

    Returns:
        (is_valid, content_preview) 元组，is_valid表示是否为有效媒体，
        content_preview为已读取的内容预览（需要内容探测时返回）
    """
    if response.status not in (200, 206):
        if response.status == 403:
            logger.warning(f"媒体URL访问被拒绝(403 Forbidden): {media_url}")
        return False, None

    content_type = response.headers.get('Content-Type', '').lower()

    if 'application/json' in content_type or 'text/' in content_type:
        logger.warning(f"媒体URL包含错误响应（非媒体Content-Type）: {media_url}")
        return False, None
    
    if not content_type:
        if not allow_read_content:
            raise aiohttp.ClientError("Content-Type为空，需要GET请求验证")

        content_preview = await response.content.read(_CONTENT_PREVIEW_CHECK_SIZE)
        if not content_preview:
            return False, None

        if (
            check_json_error_response(content_preview, media_url) or
            _is_obvious_non_media_preview(content_preview, media_url)
        ):
            return False, None

        return True, content_preview

    if not validate_content_type(content_type, is_video):
        return False, None

    if is_video and _is_generic_video_content_type(content_type):
        if not allow_read_content:
            raise aiohttp.ClientError("泛型Content-Type需要GET请求验证")

        content_preview = await response.content.read(_CONTENT_PREVIEW_CHECK_SIZE)
        if not content_preview:
            return False, None

        if _is_obvious_non_media_preview(content_preview, media_url):
            return False, None

        return True, content_preview

    return True, None


async def get_video_size(
    session: aiohttp.ClientSession,
    video_url: str,
    headers: dict = None,
    proxy: str = None
) -> Tuple[Optional[float], Optional[int]]:
    """获取视频文件大小

    Args:
        session: aiohttp会话
        video_url: 视频URL
        headers: 请求头（可选）
        proxy: 代理地址（可选）

    Returns:
        (size_mb, status_code) 元组，size_mb为视频大小(MB)，无法获取时为None，
        status_code为最近一次成功获得的HTTP状态码，异常时为None
    """
    video_url = strip_media_prefixes(video_url)

    logger.debug(f"检查视频大小: {video_url}")
    try:
        request_headers = headers or {}
        timeout = aiohttp.ClientTimeout(total=Config.VIDEO_SIZE_CHECK_TIMEOUT)

        try:
            async with session.head(
                video_url,
                headers=request_headers,
                timeout=timeout,
                proxy=proxy,
                allow_redirects=True
            ) as response:
                if response.status >= 400:
                    raise aiohttp.ClientError(
                        f"HEAD不支持媒体探测: HTTP {response.status}"
                    )
                is_valid, _ = await validate_media_response(
                    response, video_url, is_video=True, allow_read_content=False
                )
                if not is_valid:
                    return None, response.status
                size = extract_size_from_headers(response)
                if size is not None:
                    logger.debug(f"视频大小(HEAD): {size:.2f}MB, {video_url}")
                    return size, response.status
                return size, response.status
        except (aiohttp.ClientError, asyncio.TimeoutError):
            get_headers = _with_range_header(request_headers)
            async with session.get(
                video_url,
                headers=get_headers,
                timeout=timeout,
                proxy=proxy,
                allow_redirects=True
            ) as response:
                if response.status == 403:
                    logger.warning(f"视频URL访问被拒绝(403 Forbidden): {video_url}")
                    return None, 403
                if response.status >= 400:
                    return None, response.status
                is_valid, _ = await validate_media_response(
                    response, video_url, is_video=True, allow_read_content=True
                )
                if not is_valid:
                    return None, response.status
                size = extract_size_from_headers(response)
                return size, response.status
    except asyncio.CancelledError:
        raise
    except Exception as e:
        if '403' in str(e) or 'Forbidden' in str(e):
            return None, 403
        return None, None


async def validate_media_url(
    session: aiohttp.ClientSession,
    media_url: str,
    headers: dict = None,
    proxy: str = None,
    is_video: bool = True
) -> Tuple[bool, Optional[int]]:
    """验证媒体URL是否有效

    Args:
        session: aiohttp会话
        media_url: 媒体URL
        headers: 请求头（可选）
        proxy: 代理地址（可选）
        is_video: 是否为视频（True为视频，False为图片）

    Returns:
        (is_valid, status_code) 元组，is_valid表示媒体URL是否有效，
        status_code为最近一次成功获得的HTTP状态码，异常时为None
    """
    media_url = strip_media_prefixes(media_url)

    logger.debug(f"验证媒体URL: {media_url}, is_video={is_video}")
    try:
        request_headers = headers or {}
        timeout = aiohttp.ClientTimeout(total=Config.VIDEO_SIZE_CHECK_TIMEOUT)

        try:
            async with session.head(
                media_url,
                headers=request_headers,
                timeout=timeout,
                proxy=proxy,
                allow_redirects=True
            ) as response:
                if response.status >= 400:
                    raise aiohttp.ClientError(
                        f"HEAD不支持媒体探测: HTTP {response.status}"
                    )
                is_valid, _ = await validate_media_response(
                    response, media_url, is_video, allow_read_content=False
                )
                logger.debug(f"媒体验证: valid={is_valid}, {media_url}")
                return is_valid, response.status
        except (aiohttp.ClientError, asyncio.TimeoutError):
            get_headers = _with_range_header(request_headers)
            async with session.get(
                media_url,
                headers=get_headers,
                timeout=timeout,
                proxy=proxy,
                allow_redirects=True
            ) as response:
                if response.status == 403:
                    return False, 403
                is_valid, _ = await validate_media_response(
                    response, media_url, is_video, allow_read_content=True
                )
                return is_valid, response.status
    except asyncio.CancelledError:
        raise
    except Exception as e:
        if '403' in str(e) or 'Forbidden' in str(e):
            return False, 403
        return False, None

