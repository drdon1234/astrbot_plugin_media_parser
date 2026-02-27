import asyncio
import os
from typing import Dict, Any, Optional

import aiohttp
import yt_dlp
import imageio_ffmpeg

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


async def download_ytdlp_to_cache(
    session: aiohttp.ClientSession,
    video_url: str,
    cache_dir: str,
    media_id: str,
    index: int = 0,
    headers: dict = None,
    proxy: str = None
) -> Optional[Dict[str, Any]]:
    if not cache_dir:
        return None

    try:
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ffmpeg_exe = "ffmpeg"

    cache_dir = os.path.normpath(cache_dir)
    cache_subdir = os.path.join(cache_dir, media_id)
    
    if not os.path.exists(cache_subdir):
        os.makedirs(cache_subdir, exist_ok=True)
        
    outtmpl = os.path.join(os.path.abspath(cache_subdir), f"video_{index}.%(ext)s")
    
    opts = {
        "outtmpl": outtmpl,
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        # 即使没有 Cookie，使用 Android 客户端伪装也能提高普通视频的下载成功率
        "extractor_args": {'youtube': {'player_client': ['android']}},
        "noplaylist": True,
        "quiet": True,
        "ffmpeg_location": ffmpeg_exe
    }
    if proxy:
        opts["proxy"] = proxy
    
    def _download():
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            return ydl.prepare_filename(info)

    try:
        logger.debug(f"开始使用yt-dlp下载: {video_url} -> {outtmpl}")
        final_path = await asyncio.get_running_loop().run_in_executor(None, _download)
        
        if final_path:
            final_path = os.path.normpath(final_path)
        
        if not os.path.exists(final_path):
            base = os.path.splitext(final_path)[0]
            for ext in ['.mp4', '.mkv', '.webm']:
                candidate = base + ext
                if os.path.exists(candidate):
                    final_path = candidate
                    break

        if os.path.exists(final_path):
            size_mb = os.path.getsize(final_path) / (1024 * 1024)
            logger.debug(f"yt-dlp下载完成: {final_path}, size: {size_mb:.2f}MB")
            return {
                'file_path': final_path,
                'size_mb': size_mb
            }
        
        logger.error(f"yt-dlp 下载似已完成但文件不存在: {final_path}")
        return None
    except Exception as e:
        logger.warning(f"yt-dlp下载失败: {video_url}, 错误: {e}")
        return None