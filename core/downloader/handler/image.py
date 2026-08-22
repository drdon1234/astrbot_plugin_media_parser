"""图片下载处理器，负责格式识别与可选转换。"""

import asyncio
import os
import shutil
from typing import Any, Dict, Optional

import aiohttp

from ...logger import logger

from ..budget import MAX_IMAGE_PIXELS, resolve_max_bytes
from ..utils import generate_cache_file_path
from ..fileio import run_blocking
from ..image_format import (
    DIRECT_IMAGE_FORMATS,
    detect_supported_image_file_format,
    normalized_image_path,
)
from .base import download_media_from_url


async def _remove_downloaded_image(file_path: str) -> None:
    """尽力移除无法使用的已下载图片。"""
    try:
        await run_blocking(os.remove, file_path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning(f"清理无效图片失败: {file_path}, 错误: {exc}")


async def _wait_ffmpeg_conversion(process, input_path: str) -> bool:
    """等待 ffmpeg 完成；超时时终止并回收子进程。"""
    try:
        await asyncio.wait_for(process.communicate(), timeout=30)
    except asyncio.TimeoutError:
        await _terminate_ffmpeg_conversion(process, input_path)
        logger.warning(f"ffmpeg 转换超时: {input_path}")
        return False
    except asyncio.CancelledError:
        await _terminate_ffmpeg_conversion(process, input_path)
        raise

    return process.returncode == 0


async def _terminate_ffmpeg_conversion(process, input_path: str) -> None:
    """终止并回收图片转换子进程。"""
    try:
        if process.returncode is None:
            process.kill()
    except ProcessLookupError:
        pass
    except Exception as e:
        logger.warning(f"终止 ffmpeg 进程失败: {input_path}, 错误: {e}")
    try:
        await process.communicate()
    except Exception as e:
        logger.warning(f"回收 ffmpeg 进程失败: {input_path}, 错误: {e}")


async def _convert_image_to_png(
    input_path: str,
    output_path: str,
    max_bytes: Optional[int] = None,
) -> bool:
    """使用 ffmpeg 将图片转换为 PNG 格式（异步版本）

    Args:
        input_path: 输入图片路径
        output_path: 输出 PNG 路径
        max_bytes: 转换输出的最大字节数

    Returns:
        转换是否成功
    """
    hard_limit = resolve_max_bytes(max_bytes, is_video=False)
    temp_output = f"{output_path}.part.png"
    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-max_pixels",
            str(MAX_IMAGE_PIXELS),
            "-i",
            input_path,
            "-frames:v",
            "1",
            "-c:v",
            "png",
            "-fs",
            str(hard_limit + 1),
            temp_output,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        if await _wait_ffmpeg_conversion(process, input_path):
            output_size = await run_blocking(os.path.getsize, temp_output)
            if output_size <= 0 or output_size > hard_limit:
                logger.warning(
                    "图片转换输出超过下载硬限制: "
                    f"{input_path}, {output_size} > {hard_limit}"
                )
                return False
            os.replace(temp_output, output_path)
            logger.debug(f"图片已转换为 PNG: {output_path}")
            return True
        logger.warning(f"ffmpeg 转换图片失败: {input_path}")
        return False
    except FileNotFoundError:
        logger.warning("ffmpeg 未找到，无法转换图片格式")
        return False
    except Exception as e:
        logger.warning(f"ffmpeg 转换图片异常: {input_path}, 错误: {e}")
        return False
    finally:
        try:
            await run_blocking(os.remove, temp_output)
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.debug(f"清理图片转换临时文件失败: {temp_output}, 错误: {e}")


async def download_image_to_cache(
    session: aiohttp.ClientSession,
    image_url: str,
    cache_dir: str,
    media_id: str,
    index: int = 0,
    headers: dict = None,
    proxy: str = None,
    max_bytes: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """下载图片到缓存目录

    Args:
        session: aiohttp会话
        image_url: 图片URL
        cache_dir: 缓存目录
        media_id: 媒体ID（用于生成缓存文件名）
        index: 图片索引
        headers: 请求头字典
        proxy: 代理地址（可选）

    Returns:
        下载结果字典，包含 file_path、size_mb、status_code；失败时保留错误原因。
    """
    if not cache_dir or not media_id:
        return {
            "file_path": None,
            "size_mb": None,
            "status_code": None,
            "error": "缓存目录不可用，跳过图片下载",
        }

    def file_path_generator(content_type: str, url: str) -> str:
        """生成缓存文件路径"""
        return generate_cache_file_path(
            cache_dir=cache_dir,
            media_id=media_id,
            media_type="image",
            index=index,
            content_type=content_type,
            url=url,
        )

    file_path, size_mb, status_code, error = await download_media_from_url(
        session=session,
        media_url=image_url,
        file_path_generator=file_path_generator,
        is_video=False,
        headers=headers,
        proxy=proxy,
        max_bytes=max_bytes,
    )

    if not file_path:
        return {
            "file_path": None,
            "size_mb": None,
            "status_code": status_code,
            "error": error or "下载失败",
        }

    try:
        image_format = await run_blocking(
            detect_supported_image_file_format,
            file_path,
        )
    except OSError as exc:
        await _remove_downloaded_image(file_path)
        return {
            "file_path": None,
            "size_mb": None,
            "status_code": status_code,
            "error": f"无法读取已下载图片: {exc}",
        }

    if not image_format:
        await _remove_downloaded_image(file_path)
        return {
            "file_path": None,
            "size_mb": None,
            "status_code": status_code,
            "error": "响应内容不是受支持的栅格图片",
        }

    normalized_path = normalized_image_path(file_path, image_format)
    if os.path.normcase(normalized_path) != os.path.normcase(file_path):
        try:
            await run_blocking(os.replace, file_path, normalized_path)
            file_path = normalized_path
        except OSError as exc:
            await _remove_downloaded_image(file_path)
            return {
                "file_path": None,
                "size_mb": None,
                "status_code": status_code,
                "error": f"规范图片文件名失败: {exc}",
            }

    if image_format not in DIRECT_IMAGE_FORMATS:
        base_path = os.path.splitext(file_path)[0]
        png_path = f"{base_path}.png"

        if shutil.which("ffmpeg") is None:
            logger.warning(
                f"ffmpeg 未找到，保留图片原格式发送: {file_path}"
            )
            return {
                "file_path": file_path,
                "size_mb": size_mb,
                "status_code": status_code,
                "error": "ffmpeg未找到，已保留图片原格式",
                "converted_to_png": False,
            }

        if await _convert_image_to_png(file_path, png_path, max_bytes=max_bytes):
            try:
                if os.path.exists(file_path):
                    await run_blocking(os.remove, file_path)
            except Exception as e:
                logger.warning(f"删除原图片文件失败: {e}")
            file_path = png_path
            try:
                size_mb = await run_blocking(os.path.getsize, file_path)
                size_mb /= 1024 * 1024
            except OSError:
                pass
        else:
            logger.warning(f"图片格式转换失败，拒绝发送原格式: {file_path}")
            await _remove_downloaded_image(file_path)
            return {
                "file_path": None,
                "size_mb": None,
                "status_code": status_code,
                "error": "图片格式转换失败，原格式不在直接发送范围内",
                "converted_to_png": False,
            }

    return {
        "file_path": file_path,
        "size_mb": size_mb,
        "status_code": status_code,
        "error": None,
        "converted_to_png": file_path.lower().endswith(".png"),
    }
