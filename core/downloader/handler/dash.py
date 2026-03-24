"""DASH 下载处理器，负责音视频流下载与合并。"""
import asyncio
import os
from typing import Dict, Any, Optional

import aiohttp

from ...logger import logger
from ...storage import cleanup_file, stamp_subdir
from .base import download_media_from_url


async def _download_stream_normal(
    session: aiohttp.ClientSession,
    media_url: str,
    output_path: str,
    headers: dict = None,
    proxy: str = None
) -> Optional[Dict[str, Any]]:
    """普通流式下载。"""

    def file_path_generator(content_type: str, url: str) -> str:
        """根据内容类型与链接生成目标文件路径。"""
        return output_path

    file_path, size_mb = await download_media_from_url(
        session=session,
        media_url=media_url,
        file_path_generator=file_path_generator,
        is_video=True,
        headers=headers,
        proxy=proxy
    )
    if not file_path:
        return None

    if size_mb is None:
        try:
            size_mb = os.path.getsize(file_path) / (1024 * 1024)
        except Exception:
            size_mb = None

    return {
        "file_path": os.path.normpath(file_path),
        "size_mb": size_mb
    }


async def _download_stream(
    session: aiohttp.ClientSession,
    media_url: str,
    output_path: str,
    headers: dict = None,
    proxy: str = None
) -> Optional[Dict[str, Any]]:
    """根据 range: 前缀决定是否走 Range 下载。"""
    actual_url = media_url
    use_range = False
    if media_url.startswith("range:"):
        actual_url = media_url[6:]
        use_range = True

    if use_range:
        try:
            from .base import range_download_file
            range_result = await range_download_file(
                session=session,
                url=actual_url,
                output_path=output_path,
                headers=headers,
                proxy=proxy
            )
            if range_result:
                return range_result
            logger.debug(f"DASH子流Range下载失败，降级普通下载: {actual_url}")
        except Exception as e:
            logger.warning(f"DASH子流Range下载异常，降级普通下载: {actual_url}, 错误: {e}")

    return await _download_stream_normal(
        session=session,
        media_url=actual_url,
        output_path=output_path,
        headers=headers,
        proxy=proxy
    )


async def _merge_dash_streams(
    video_path: str,
    audio_path: str,
    output_path: str
) -> bool:
    """使用 ffmpeg 异步合并 DASH 音视频。"""
    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", video_path, "-i", audio_path,
            "-c", "copy", "-map", "0:v:0", "-map", "1:a:0",
            output_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await process.communicate()
        if process.returncode == 0:
            return True

        error_output = (
            stderr.decode("utf-8", errors="ignore").strip()
            if stderr else
            ""
        )
        logger.warning(
            f"DASH ffmpeg 合并失败(退出码 {process.returncode}): "
            f"{error_output[:200]}"
        )
        return False
    except FileNotFoundError:
        logger.warning("ffmpeg 未找到，DASH 合并将降级为仅视频")
        return False
    except Exception as e:
        logger.warning(f"DASH ffmpeg 合并异常: {e}")
        return False


def _replace_as_output(src_path: str, output_path: str) -> bool:
    """将源文件移动为最终输出文件。"""
    if not src_path or not os.path.exists(src_path):
        return False
    try:
        src_norm = os.path.normcase(os.path.abspath(src_path))
        dst_norm = os.path.normcase(os.path.abspath(output_path))
        if src_norm != dst_norm:
            cleanup_file(output_path)
            os.replace(src_path, output_path)
        return True
    except Exception as e:
        logger.warning(f"移动DASH视频输出失败: {src_path} -> {output_path}, 错误: {e}")
        return False


async def download_dash_to_cache(
    session: aiohttp.ClientSession,
    video_url: str,
    audio_url: str,
    cache_dir: str,
    media_id: str,
    index: int = 0,
    headers: dict = None,
    proxy: str = None
) -> Optional[Dict[str, Any]]:
    """下载 DASH 视频并合并到缓存目录。"""
    if not cache_dir or not video_url:
        return None

    logger.debug(
        f"开始DASH下载: video={video_url[:60]}..., "
        f"audio={'有' if audio_url else '无'}, index={index}"
    )

    cache_subdir = os.path.normpath(os.path.join(cache_dir, media_id))
    os.makedirs(cache_subdir, exist_ok=True)
    stamp_subdir(cache_subdir)
    video_temp_path = os.path.normpath(
        os.path.join(cache_subdir, f"video_{index}_video.m4s")
    )
    audio_temp_path = os.path.normpath(
        os.path.join(
            cache_subdir,
            f"video_{index}_audio.m4s"
        )
    )
    output_path = os.path.normpath(
        os.path.join(
            cache_subdir,
            f"video_{index}.mp4"
        )
    )

    video_result = None
    audio_result = None
    try:
        if audio_url:
            video_task = _download_stream(
                session=session,
                media_url=video_url,
                output_path=video_temp_path,
                headers=headers,
                proxy=proxy
            )
            audio_task = _download_stream(
                session=session,
                media_url=audio_url,
                output_path=audio_temp_path,
                headers=headers,
                proxy=proxy
            )
            video_result, audio_result = await asyncio.gather(video_task, audio_task)
        else:
            video_result = await _download_stream(
                session=session,
                media_url=video_url,
                output_path=video_temp_path,
                headers=headers,
                proxy=proxy
            )

        if not video_result or not video_result.get("file_path"):
            cleanup_file(video_temp_path)
            cleanup_file(audio_temp_path)
            return None

        video_file_path = video_result["file_path"]
        audio_file_path = audio_result["file_path"] if audio_result else None

        if audio_file_path and os.path.exists(audio_file_path):
            merge_ok = await _merge_dash_streams(
                video_path=video_file_path,
                audio_path=audio_file_path,
                output_path=output_path
            )
            if merge_ok and os.path.exists(output_path):
                cleanup_file(video_file_path)
                cleanup_file(audio_file_path)
                final_path = output_path
            else:
                logger.warning("DASH 合并失败，降级为仅视频输出")
                cleanup_file(audio_file_path)
                if not _replace_as_output(video_file_path, output_path):
                    return None
                final_path = output_path
        else:
            if not _replace_as_output(video_file_path, output_path):
                return None
            final_path = output_path

        if not os.path.exists(final_path):
            return None

        try:
            size_mb = os.path.getsize(final_path) / (1024 * 1024)
        except Exception:
            size_mb = None

        logger.debug(f"DASH下载完成: {final_path}, {size_mb}MB")
        return {
            "file_path": os.path.normpath(final_path),
            "size_mb": size_mb
        }
    except Exception as e:
        logger.warning(f"DASH 下载失败: video={video_url}, 错误: {e}")
        cleanup_file(video_temp_path)
        cleanup_file(audio_temp_path)
        cleanup_file(output_path)
        return None

