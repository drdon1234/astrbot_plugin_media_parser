"""图片下载处理器，负责格式识别与可选转换。"""
import asyncio
import os
from typing import Any, Dict, Optional

import aiohttp

from ...logger import logger

from ..utils import generate_cache_file_path
from .base import download_media_from_url


def _is_supported_image_format(file_path: str) -> bool:
    """检查图片格式是否为支持的格式（jpg, jpeg, png）
    
    Args:
        file_path: 图片文件路径
        
    Returns:
        是否为支持的格式
    """
    if not file_path or not os.path.exists(file_path):
        return False
    
    ext = os.path.splitext(file_path)[1].lower()
    return ext in ['.jpg', '.jpeg', '.png']


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


async def _convert_image_to_png(input_path: str, output_path: str) -> bool:
    """使用 ffmpeg 将图片转换为 PNG 格式（异步版本）
    
    Args:
        input_path: 输入图片路径
        output_path: 输出 PNG 路径
        
    Returns:
        转换是否成功
    """
    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", input_path,
            output_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        if await _wait_ffmpeg_conversion(process, input_path):
            logger.debug(f"图片已转换为 PNG: {output_path}")
            return True
        else:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", input_path,
                "-c:v", "png",
                output_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            if await _wait_ffmpeg_conversion(process, input_path):
                logger.debug(f"图片已转换为 PNG: {output_path}")
                return True
            else:
                logger.warning(f"ffmpeg 转换图片失败: {input_path}")
                return False
    except FileNotFoundError:
        logger.warning("ffmpeg 未找到，无法转换图片格式")
        return False
    except Exception as e:
        logger.warning(f"ffmpeg 转换图片异常: {input_path}, 错误: {e}")
        return False


async def download_image_to_cache(
    session: aiohttp.ClientSession,
    image_url: str,
    cache_dir: str,
    media_id: str,
    index: int = 0,
    headers: dict = None,
    proxy: str = None
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
            'file_path': None,
            'size_mb': None,
            'status_code': None,
            'error': '缓存目录不可用，跳过图片下载'
        }

    def file_path_generator(content_type: str, url: str) -> str:
        """生成缓存文件路径"""
        return generate_cache_file_path(
            cache_dir=cache_dir,
            media_id=media_id,
            media_type='image',
            index=index,
            content_type=content_type,
            url=url
        )

    file_path, size_mb, status_code, error = await download_media_from_url(
        session=session,
        media_url=image_url,
        file_path_generator=file_path_generator,
        is_video=False,
        headers=headers,
        proxy=proxy
    )
    
    if not file_path:
        return {
            'file_path': None,
            'size_mb': None,
            'status_code': status_code,
            'error': error or '下载失败'
        }

    if file_path and not _is_supported_image_format(file_path):
        base_path = os.path.splitext(file_path)[0]
        png_path = f"{base_path}.png"
        
        if await _convert_image_to_png(file_path, png_path):
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception as e:
                logger.warning(f"删除原图片文件失败: {e}")
            file_path = png_path
        else:
            logger.warning(f"图片格式转换失败，保留原文件: {file_path}")
    
    return {
        'file_path': file_path,
        'size_mb': size_mb,
        'status_code': status_code,
        'error': None
    }

