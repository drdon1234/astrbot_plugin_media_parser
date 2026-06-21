"""视频封面截取处理器。"""
import asyncio
import os
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from ...logger import logger
from ...storage import cleanup_file
from ..utils import generate_cache_file_path, strip_media_prefixes


VIDEO_COVER_TIMEOUT = 45


def _build_ffmpeg_headers(headers: Optional[Dict[str, Any]]) -> str:
    """将 HTTP 头转换成 ffmpeg -headers 可接受的格式。"""
    if not isinstance(headers, dict):
        return ""

    lines = []
    skipped = {"host", "content-length", "connection"}
    for key, value in headers.items():
        name = str(key or "").strip()
        if not name or name.lower() in skipped or value is None:
            continue
        lines.append(f"{name}: {value}")
    return "\r\n".join(lines) + ("\r\n" if lines else "")


async def _terminate_ffmpeg_process(process, label: str) -> None:
    """取消或超时时终止并回收 ffmpeg 子进程。"""
    try:
        if process.returncode is None:
            process.kill()
    except ProcessLookupError:
        pass
    except Exception as e:
        logger.warning(f"终止 ffmpeg 截帧进程失败: {label}, 错误: {e}")
    try:
        await process.communicate()
    except Exception as e:
        logger.warning(f"回收 ffmpeg 截帧进程失败: {label}, 错误: {e}")


async def _run_ffmpeg_cover_extract(
    source_url: str,
    output_path: str,
    headers: Optional[Dict[str, Any]] = None,
    proxy: str = None,
) -> Tuple[bool, str]:
    """执行 ffmpeg 首帧截取。"""
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if source_url.startswith(("http://", "https://")):
        if proxy:
            args.extend(["-http_proxy", proxy])
        if isinstance(headers, dict):
            user_agent = str(headers.get("User-Agent") or "").strip()
            referer = str(headers.get("Referer") or "").strip()
            if user_agent:
                args.extend(["-user_agent", user_agent])
            if referer:
                args.extend(["-referer", referer])
        header_blob = _build_ffmpeg_headers(headers)
        if header_blob:
            args.extend(["-headers", header_blob])

    args.extend([
        "-i",
        source_url,
        "-frames:v",
        "1",
        "-an",
        "-update",
        "1",
        output_path,
    ])

    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return False, "ffmpeg未找到，无法截取视频封面"
    except Exception as e:
        return False, f"启动ffmpeg截帧失败: {e}"

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=VIDEO_COVER_TIMEOUT,
        )
    except asyncio.TimeoutError:
        await _terminate_ffmpeg_process(process, source_url)
        return False, "ffmpeg截取视频封面超时"
    except asyncio.CancelledError:
        await _terminate_ffmpeg_process(process, source_url)
        raise

    if process.returncode == 0 and os.path.exists(output_path):
        return True, ""

    detail = (stderr or stdout or b"").decode("utf-8", errors="ignore").strip()
    if detail:
        detail = detail.splitlines()[-1]
    return False, detail or f"ffmpeg截帧失败(退出码 {process.returncode})"


async def extract_video_cover_to_cache(
    session: aiohttp.ClientSession,
    video_urls: List[str],
    cache_dir: str,
    media_id: str,
    index: int = 0,
    headers: dict = None,
    proxy: str = None,
) -> Optional[Dict[str, Any]]:
    """从视频候选 URL 中截取首帧并写入图片缓存。"""
    del session

    if not cache_dir or not media_id:
        return {
            "file_path": None,
            "size_mb": None,
            "status_code": None,
            "error": "缓存目录不可用，无法截取视频封面",
        }

    candidates = [
        strip_media_prefixes(url)
        for url in (video_urls or [])
        if isinstance(url, str) and strip_media_prefixes(url)
    ]
    if not candidates:
        return {
            "file_path": None,
            "size_mb": None,
            "status_code": None,
            "error": "未找到可截取封面的视频URL",
        }

    last_error = "截取视频封面失败"
    output_path = generate_cache_file_path(
        cache_dir=cache_dir,
        media_id=media_id,
        media_type="image",
        index=index,
        content_type="image/jpeg",
        url="cover.jpg",
    )

    for candidate in candidates:
        cleanup_file(output_path)
        success, error = await _run_ffmpeg_cover_extract(
            source_url=candidate,
            output_path=output_path,
            headers=headers,
            proxy=proxy,
        )
        if success:
            try:
                size_mb = os.path.getsize(output_path) / (1024 * 1024)
            except OSError:
                size_mb = None
            return {
                "file_path": os.path.normpath(output_path),
                "size_mb": size_mb,
                "status_code": None,
                "error": None,
            }
        last_error = error or last_error
        logger.debug(f"截取视频封面失败，尝试下一个候选: {candidate}, 错误: {last_error}")

    cleanup_file(output_path)
    return {
        "file_path": None,
        "size_mb": None,
        "status_code": None,
        "error": last_error,
    }
