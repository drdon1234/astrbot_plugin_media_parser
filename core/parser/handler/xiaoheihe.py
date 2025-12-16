# -*- coding: utf-8 -*-
import asyncio
import re
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse

import aiohttp

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from .base import BaseVideoParser
from ..utils import build_request_headers


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class XiaoheiheParser(BaseVideoParser):
    """小黑盒解析器"""

    def __init__(self):
        """初始化小黑盒解析器"""
        super().__init__("xiaoheihe")
        self.semaphore = asyncio.Semaphore(10)
        self._default_headers = {
            "User-Agent": UA,
            "Referer": "https://www.xiaoheihe.cn/",
            "Accept-Language": "zh-CN,zh;q=0.9"
        }

    def can_parse(self, url: str) -> bool:
        """判断是否可以解析此URL

        Args:
            url: 链接

        Returns:
            如果可以解析返回True，否则返回False
        """
        if not url:
            logger.debug(f"[{self.name}] can_parse: URL为空")
            return False
        url_lower = url.lower()
        if 'api.xiaoheihe.cn/game/share_game_detail' in url_lower:
            logger.debug(f"[{self.name}] can_parse: 匹配小黑盒链接 {url}")
            return True
        if 'www.xiaoheihe.cn' in url_lower:
            logger.debug(f"[{self.name}] can_parse: 匹配小黑盒链接 {url}")
            return True
        logger.debug(f"[{self.name}] can_parse: 无法解析 {url}")
        return False

    def extract_links(self, text: str) -> List[str]:
        """从文本中提取小黑盒链接

        Args:
            text: 输入文本

        Returns:
            小黑盒链接列表
        """
        result_links_set = set()
        
        app_pattern = r'https?://api\.xiaoheihe\.cn/game/share_game_detail[^\s<>"\'()]+'
        app_links = re.findall(app_pattern, text, re.IGNORECASE)
        result_links_set.update(app_links)
        
        web_pattern = r'https?://www\.xiaoheihe\.cn/[^\s<>"\'()]+'
        web_links = re.findall(web_pattern, text, re.IGNORECASE)
        result_links_set.update(web_links)
        
        result = list(result_links_set)
        if result:
            logger.debug(f"[{self.name}] extract_links: 提取到 {len(result)} 个链接: {result[:3]}{'...' if len(result) > 3 else ''}")
        else:
            logger.debug(f"[{self.name}] extract_links: 未提取到链接")
        return result

    async def _get_web_url(
        self,
        url: str,
        session: aiohttp.ClientSession
    ) -> str:
        """将 App 分享链接转换为 Web URL

        Args:
            url: App 分享链接
            session: aiohttp会话

        Returns:
            Web URL，如果转换失败返回原URL
        """
        if 'api.xiaoheihe.cn/game/share_game_detail' in url.lower():
            try:
                async with session.get(
                    url,
                    headers=self._default_headers,
                    allow_redirects=True,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    return str(response.url)
            except Exception:
                return url
        return url

    async def _extract_content_urls(
        self,
        web_url: str,
        session: aiohttp.ClientSession
    ) -> tuple[List[str], List[str]]:
        """从游戏页提取所有视频和图片URL

        Args:
            web_url: Web URL
            session: aiohttp会话

        Returns:
            (视频URL列表, 图片URL列表) 元组
        """
        try:
            async with session.get(
                web_url,
                headers=self._default_headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status != 200:
                    raise RuntimeError(
                        f"无法获取页面内容，状态码: {response.status}"
                    )
                html = await response.text()
        except Exception as e:
            raise RuntimeError(f"无法获取页面内容: {e}")

        video_pattern = r'https?://[^"\'\s<>]+\.m3u8(?:\?[^"\'\s<>]*)?'
        videos = list(set(re.findall(video_pattern, html, re.I)))

        all_images = re.findall(
            r'https?://[^"\'\s<>]+\.(?:jpg|jpeg|png|webp)(?:\?[^"\'\s<>]*)?',
            html, re.I
        )
        
        images = []
        for img in set(all_images):
            img_lower = img.lower()
            if '/thumbnail/' in img_lower:
                continue
            if any(kw in img_lower for kw in [
                'gameimg', 'steam_item_assets', 'screenshot', 'game'
            ]):
                images.append(img)

        return videos, images

    async def parse(
        self,
        session: aiohttp.ClientSession,
        url: str
    ) -> Optional[Dict[str, Any]]:
        """解析单个小黑盒链接

        Args:
            session: aiohttp会话
            url: 小黑盒链接

        Returns:
            解析结果字典，包含标准化的元数据格式

        Raises:
            RuntimeError: 当解析失败时
        """
        logger.debug(f"[{self.name}] parse: 开始解析 {url}")
        async with self.semaphore:
            original_url = url
            
            web_url = await self._get_web_url(url, session)
            if web_url != url:
                logger.debug(f"[{self.name}] parse: App链接转换为Web链接 {url} -> {web_url}")
            
            logger.debug(f"[{self.name}] parse: 提取内容URL")
            videos, images = await self._extract_content_urls(web_url, session)
            logger.debug(f"[{self.name}] parse: 提取到视频{len(videos)}个, 图片{len(images)}张")
            
            if not videos and not images:
                logger.debug(f"[{self.name}] parse: 未找到任何内容 {url}")
                raise RuntimeError(f"未找到任何内容: {url}")
            
            video_urls = [[video] for video in videos] if videos else []
            image_urls = [[img] for img in images] if images else []
            
            referer = "https://store.steampowered.com/"
            image_headers = build_request_headers(
                is_video=False,
                referer=referer
            )
            video_headers = build_request_headers(
                is_video=True,
                referer=referer
            )
            result_dict = {
                "url": original_url,
                "title": "",
                "author": "",
                "desc": "",
                "timestamp": "",
                "video_urls": video_urls,
                "image_urls": image_urls,
                "image_headers": image_headers,
                "video_headers": video_headers,
            }
            logger.debug(f"[{self.name}] parse: 解析完成 {url}, video_count={len(video_urls)}, image_count={len(image_urls)}")
            return result_dict

