# -*- coding: utf-8 -*-
import asyncio
import re
from datetime import datetime
from typing import Optional, Dict, Any, List

import aiohttp

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from .base import BaseVideoParser
from ..utils import build_request_headers


class TwitterParser(BaseVideoParser):
    """Twitter/X 视频解析器"""

    def __init__(
        self,
        use_parse_proxy: bool = False,
        use_image_proxy: bool = False,
        use_video_proxy: bool = False,
        proxy_url: str = None
    ):
        """初始化Twitter解析器

        Args:
            use_parse_proxy: 解析时是否使用代理
            use_image_proxy: 图片下载是否使用代理
            use_video_proxy: 视频下载是否使用代理
            proxy_url: 代理地址（格式：http://host:port 或 socks5://host:port）
        """
        super().__init__("twitter")
        self.use_parse_proxy = use_parse_proxy
        self.use_image_proxy = use_image_proxy
        self.use_video_proxy = use_video_proxy
        self.proxy_url = proxy_url
        self.semaphore = asyncio.Semaphore(10)
        self.headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            ),
            'Accept': 'application/json',
        }

    def can_parse(self, url: str) -> bool:
        """判断是否可以解析此URL

        Args:
            url: 视频链接

        Returns:
            如果可以解析返回True，否则返回False
        """
        if not url:
            logger.debug(f"[{self.name}] can_parse: URL为空")
            return False
        url_lower = url.lower()
        if 'twitter.com' in url_lower or 'x.com' in url_lower:
            if re.search(r'/status/(\d+)', url):
                logger.debug(f"[{self.name}] can_parse: 匹配Twitter链接 {url}")
                return True
        logger.debug(f"[{self.name}] can_parse: 无法解析 {url}")
        return False

    def extract_links(self, text: str) -> List[str]:
        """从文本中提取Twitter链接

        Args:
            text: 输入文本

        Returns:
            Twitter链接列表
        """
        result_links_set = set()
        seen_ids = set()
        pattern = (
            r'https?://(?:twitter\.com|x\.com)/'
            r'[^\s]*?status/(\d+)[^\s<>"\'()]*'
        )
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            tweet_id = match.group(1)
            if tweet_id not in seen_ids:
                seen_ids.add(tweet_id)
                original_url = match.group(0)
                standardized_url = re.sub(
                    r'https?://(?:twitter\.com|x\.com)',
                    'https://x.com',
                    original_url,
                    flags=re.IGNORECASE
                )
                result_links_set.add(standardized_url)
        result = list(result_links_set)
        if result:
            logger.debug(f"[{self.name}] extract_links: 提取到 {len(result)} 个链接: {result[:3]}{'...' if len(result) > 3 else ''}")
        else:
            logger.debug(f"[{self.name}] extract_links: 未提取到链接")
        return result


    async def _fetch_media_info(
        self,
        session: aiohttp.ClientSession,
        tweet_id: str,
        max_retries: int = 3,
        retry_delay: float = 1.0
    ) -> Dict[str, Any]:
        """使用FxTwitter API获取推特媒体直链（带重试机制）

        Args:
            session: aiohttp会话
            tweet_id: 推文ID
            max_retries: 最大重试次数，默认3次
            retry_delay: 重试延迟（秒），默认1秒，使用指数退避

        Returns:
            包含images和videos的字典

        Raises:
            RuntimeError: 所有重试均失败后抛出异常
        """
        api_url = f"https://api.fxtwitter.com/status/{tweet_id}"
        last_exception = None

        proxy = self.proxy_url if self.use_parse_proxy else None
        for attempt in range(max_retries + 1):
            try:
                async with session.get(
                    api_url,
                    headers=self.headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                    proxy=proxy
                ) as response:
                    response.raise_for_status()
                    data = await response.json()
                    media_urls = {
                        'images': [],
                        'videos': [],
                        'text': '',
                        'author': '',
                        'timestamp': ''
                    }
                    if 'tweet' in data:
                        tweet = data['tweet']
                        media_urls['text'] = tweet.get('text', '')
                        author_info = tweet.get('author', {})
                        if isinstance(author_info, dict):
                            author_name = author_info.get('name', '')
                            author_username = (
                                author_info.get('screen_name', '')
                            )
                            if author_name:
                                media_urls['author'] = (
                                    f"{author_name}(@{author_username})"
                                )
                            else:
                                media_urls['author'] = author_username
                        
                        created_at = tweet.get('created_at')
                        if created_at:
                            dt = datetime.strptime(created_at, '%a %b %d %H:%M:%S %z %Y')
                            media_urls['timestamp'] = dt.strftime('%Y-%m-%d')
                        
                        if 'media' in tweet and 'photos' in tweet['media']:
                            for photo in tweet['media']['photos']:
                                media_urls['images'].append(photo.get('url', ''))
                        if 'media' in tweet and 'videos' in tweet['media']:
                            for video in tweet['media']['videos']:
                                media_urls['videos'].append({
                                    'url': video.get('url', ''),
                                    'thumbnail': video.get('thumbnail_url', ''),
                                    'duration': video.get('duration', 0)
                                })
                    return media_urls
            except aiohttp.ClientResponseError as e:
                if e.status < 500:
                    raise RuntimeError(f"HTTP {e.status} {e.message}")
                last_exception = e
            except (aiohttp.ClientError, asyncio.TimeoutError, aiohttp.ServerTimeoutError) as e:
                last_exception = e
            except Exception as e:
                raise RuntimeError(str(e))

            if attempt < max_retries:
                delay = retry_delay * (2 ** attempt)
                await asyncio.sleep(delay)
            else:
                error_msg = str(last_exception) if last_exception else "未知错误"
                raise RuntimeError(f"{error_msg}（已重试{max_retries}次）")


    async def parse(
        self,
        session: aiohttp.ClientSession,
        url: str
    ) -> Optional[Dict[str, Any]]:
        """解析单个Twitter链接

        Args:
            session: aiohttp会话
            url: Twitter链接

        Returns:
            解析结果字典，包含标准化的元数据格式

        Raises:
            RuntimeError: 当解析失败时
        """
        async with self.semaphore:
            tweet_id_match = re.search(r'/status/(\d+)', url)
            if not tweet_id_match:
                raise RuntimeError(f"无法解析此URL: {url}")
            tweet_id = tweet_id_match.group(1)
            media_info = await self._fetch_media_info(session, tweet_id)
            
            images = media_info.get('images', [])
            videos = media_info.get('videos', [])
            text = media_info.get('text', '')
            author = media_info.get('author', '')
            timestamp = media_info.get('timestamp', '')
            
            if not images and not videos:
                raise RuntimeError("推文不包含图片或视频")
            
            video_urls = []
            image_urls = []
            
            for video_info in videos:
                video_url = video_info.get('url')
                if video_url:
                    video_urls.append(video_url)
            
            image_urls = [img for img in images if img]
            
            has_videos = len(video_urls) > 0
            has_images = len(image_urls) > 0
            
            image_headers = build_request_headers(is_video=False)
            video_headers = build_request_headers(is_video=True)
            
            metadata_base = {
                "url": url,
                "title": text[:100] if text else "Twitter 推文",
                "author": author,
                "desc": text,
                "timestamp": timestamp,
                "image_headers": image_headers,
                "video_headers": video_headers,
                "use_image_proxy": self.use_image_proxy,
                "use_video_proxy": self.use_video_proxy,
                "proxy_url": self.proxy_url if (self.use_image_proxy or self.use_video_proxy) else None,
            }
            
            if has_videos and has_images:
                result_dict = {
                    **metadata_base,
                    "video_urls": [[url] for url in video_urls],
                    "image_urls": [[url] for url in image_urls],
                    "is_twitter_video": True,
                }
                logger.debug(f"[{self.name}] parse: 解析完成(视频+图片) {url}, video_count={len(video_urls)}, image_count={len(image_urls)}")
                return result_dict
            elif has_videos:
                result_dict = {
                    **metadata_base,
                    "video_urls": [[url] for url in video_urls],
                    "image_urls": [],
                    "is_twitter_video": True,
                }
                logger.debug(f"[{self.name}] parse: 解析完成(视频) {url}, video_count={len(video_urls)}")
                return result_dict
            else:
                if not image_urls:
                    logger.debug(f"[{self.name}] parse: 推文不包含图片 {url}")
                    raise RuntimeError("推文不包含图片")
                
                result_dict = {
                    **metadata_base,
                    "video_urls": [],
                    "image_urls": [[url] for url in image_urls],
                    "is_twitter_video": False,
                }
                logger.debug(f"[{self.name}] parse: 解析完成(图片) {url}, image_count={len(image_urls)}")
                return result_dict
