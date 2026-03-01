import asyncio
import os
import tempfile
from typing import Optional

import aiohttp

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from ..utils import generate_cache_file_path, get_image_suffix
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
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        
        if process.returncode == 0:
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
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
            
            if process.returncode == 0:
                logger.debug(f"图片已转换为 PNG: {output_path}")
                return True
            else:
                logger.warning(f"ffmpeg 转换图片失败: {input_path}")
                return False
                
    except asyncio.TimeoutError:
        logger.warning(f"ffmpeg 转换超时: {input_path}")
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
) -> Optional[str]:
    """下载图片到缓存目录或临时文件

    Args:
        session: aiohttp会话
        image_url: 图片URL
        cache_dir: 缓存目录（如果提供则下载到缓存目录，否则下载到临时文件）
        media_id: 媒体ID（用于生成缓存文件名）
        index: 图片索引
        headers: 请求头字典
        proxy: 代理地址（可选）

    Returns:
        文件路径，失败时为None
    """
    if cache_dir and media_id:
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
        
        file_path, _ = await download_media_from_url(
            session=session,
            media_url=image_url,
            file_path_generator=file_path_generator,
            is_video=False,
            headers=headers,
            proxy=proxy
        )
    else:
        def generate_temp_file_path(content_type: str, url: str) -> str:
            """生成临时文件路径"""
            suffix = get_image_suffix(content_type, url)
            with tempfile.NamedTemporaryFile(
                delete=False,
                suffix=suffix
            ) as temp_file:
                return os.path.normpath(temp_file.name)
        
        file_path, _ = await download_media_from_url(
            session=session,
            media_url=image_url,
            file_path_generator=generate_temp_file_path,
            is_video=False,
            headers=headers,
            proxy=proxy
        )
    
    if file_path and not _is_supported_image_format(file_path):
        base_path = os.path.splitext(file_path)[0]
        png_path = f"{base_path}.png"
        
        if await _convert_image_to_png(file_path, png_path):
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception as e:
                logger.warning(f"删除原图片文件失败: {e}")
            return png_path
        else:
            logger.warning(f"图片格式转换失败，保留原文件: {file_path}")
    
    return file_path

