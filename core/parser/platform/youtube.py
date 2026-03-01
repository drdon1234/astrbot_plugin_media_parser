import asyncio
import re
from typing import Optional, Dict, Any, List

import yt_dlp
import aiohttp

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from .base import BaseVideoParser
from ...constants import Config


class YoutubeParser(BaseVideoParser):
    def __init__(
        self,
        use_proxy: bool = False,
        proxy_url: str = None
    ):
        super().__init__("youtube")
        self.use_proxy = use_proxy
        self.proxy_url = proxy_url
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)

    def can_parse(self, url: str) -> bool:
        if not url: 
            return False
        url_lower = url.lower()
        return 'youtube.com' in url_lower or 'youtu.be' in url_lower

    def extract_links(self, text: str) -> List[str]:
        pattern = r'https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)[a-zA-Z0-9_-]+'
        return list(set(re.findall(pattern, text)))

    async def parse(
        self,
        session: aiohttp.ClientSession,
        url: str
    ) -> Optional[Dict[str, Any]]:
        async with self.semaphore:
            opts = {
                "quiet": True,
                "no_warnings": True,
                "nocheckcertificate": True,
                "extract_flat": True, 
                "skip_download": True,
            }
            
            if self.use_proxy and self.proxy_url:
                opts["proxy"] = self.proxy_url

            def _extract():
                with yt_dlp.YoutubeDL(opts) as ydl:
                    return ydl.extract_info(url, download=False)

            try:
                logger.debug(f"[{self.name}] parse: 开始解析 {url}")
                info = await asyncio.get_running_loop().run_in_executor(None, _extract)
            except Exception as e:
                err_str = str(e)
                # 专门捕获年龄限制错误，返回更友好的提示
                if "Sign in to confirm your age" in err_str:
                    logger.warning(f"[{self.name}] 视频有年龄限制: {url}")
                    raise RuntimeError("该视频有年龄限制，无法免登录解析。")
                
                logger.error(f"yt-dlp 解析错误: {e}")
                raise RuntimeError(f"yt-dlp解析失败: {e}")

            if not info:
                raise RuntimeError("无法获取YouTube视频信息")

            title = info.get('title', 'YouTube Video')
            author = info.get('uploader', '')
            desc = info.get('description', '')
            timestamp = ""
            upload_date = info.get('upload_date')
            if upload_date and len(upload_date) == 8:
                timestamp = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"

            return {
                "url": url,
                "title": title,
                "author": author,
                "desc": desc[:200] + "..." if desc and len(desc) > 200 else desc,
                "timestamp": timestamp,
                "video_urls": [[f"ytdlp:{url}"]],
                "image_urls": [],
                "image_headers": {},
                "video_headers": {},
                "video_force_download": True,
                "use_video_proxy": self.use_proxy,
                "proxy_url": self.proxy_url if self.use_proxy else None
            }