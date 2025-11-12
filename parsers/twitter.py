# -*- coding: utf-8 -*-
import asyncio
import re
from typing import Optional, Dict, Any, List

import aiohttp

from .base_parser import BaseVideoParser


class TwitterParser(BaseVideoParser):
    """Twitter/X 视频解析器"""

    def __init__(
        self,
        use_image_proxy: bool = False,
        use_video_proxy: bool = False,
        proxy_url: str = None
    ):
        """初始化Twitter解析器。

        Args:
            use_image_proxy: 是否使用图片代理
            use_video_proxy: 是否使用视频代理
            proxy_url: 代理地址（格式：http://host:port 或 socks5://host:port），图片和视频共用此代理地址
        """
        super().__init__("Twitter/X")
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
        """判断是否可以解析此URL。

        Args:
            url: 视频链接

        Returns:
            如果可以解析返回True，否则返回False
        """
        if not url:
            return False
        url_lower = url.lower()
        if 'twitter.com' in url_lower or 'x.com' in url_lower:
            if re.search(r'/status/(\d+)', url):
                return True
        return False

    def extract_links(self, text: str) -> List[str]:
        """从文本中提取Twitter链接。

        Args:
            text: 输入文本

        Returns:
            Twitter链接列表
        """
        result_links = []
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
                result_links.append(standardized_url)
        return result_links

    def _get_image_proxy(self) -> Optional[str]:
        """获取图片代理地址。

        Returns:
            图片代理地址，如果未启用返回None
        """
        if self.use_image_proxy and self.proxy_url:
            return self.proxy_url
        return None

    def _get_video_proxy(self) -> Optional[str]:
        """获取视频代理地址。

        Returns:
            视频代理地址，如果未启用返回None
        """
        if self.use_video_proxy and self.proxy_url:
            return self.proxy_url
        return None

    async def _fetch_media_info(
        self,
        session: aiohttp.ClientSession,
        tweet_id: str,
        max_retries: int = 3,
        retry_delay: float = 1.0
    ) -> Dict[str, Any]:
        """使用FxTwitter API获取推特媒体直链（带重试机制）。

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

        for attempt in range(max_retries + 1):
            try:
                async with session.get(
                    api_url,
                    headers=self.headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    response.raise_for_status()
                    data = await response.json()
                    media_urls = {
                        'images': [],
                        'videos': [],
                        'text': '',
                        'author': ''
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
                    raise RuntimeError(f"解析失败：HTTP {e.status} {e.message}")
                last_exception = e
            except (aiohttp.ClientError, asyncio.TimeoutError, aiohttp.ServerTimeoutError) as e:
                last_exception = e
            except Exception as e:
                raise RuntimeError(f"解析失败：{str(e)}")

            if attempt < max_retries:
                delay = retry_delay * (2 ** attempt)
                await asyncio.sleep(delay)
            else:
                error_msg = str(last_exception) if last_exception else "未知错误"
                raise RuntimeError(f"解析失败：{error_msg}（已重试{max_retries}次）")


    async def parse(
        self,
        session: aiohttp.ClientSession,
        url: str
    ) -> Optional[Dict[str, Any]]:
        """解析单个Twitter链接。

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
            
            if not images and not videos:
                raise RuntimeError("解析失败：推文不包含图片或视频")
            
            video_urls = []
            video_thumb_urls = []
            image_urls = []
            
            for video_info in videos:
                video_url = video_info.get('url')
                if video_url:
                    video_urls.append(video_url)
                    thumbnail = video_info.get('thumbnail', '')
                    if thumbnail:
                        video_thumb_urls.append(thumbnail)
                    else:
                        video_thumb_urls.append(None)
            
            image_urls = [img for img in images if img]
            
            has_videos = len(video_urls) > 0
            has_images = len(image_urls) > 0
            
            if has_videos and has_images:
                media_urls = video_urls + image_urls
                media_types = ['video'] * len(video_urls) + ['image'] * len(image_urls)
                
                return {
                    "url": url,
                    "media_type": "mixed",
                    "title": text[:100] if text else "Twitter 推文",
                    "author": author,
                    "desc": text,
                    "timestamp": "",  # Twitter API不返回发布时间
                    "media_urls": media_urls,
                    "video_urls": video_urls,  # 单独的视频URL列表
                    "image_urls": image_urls,  # 单独的图片URL列表
                    "thumb_url": video_thumb_urls[0] if video_thumb_urls and video_thumb_urls[0] else (image_urls[0] if image_urls else None),
                    "video_thumb_urls": video_thumb_urls,  # 每个视频的缩略图URL列表
                    "media_types": media_types,  # 每个媒体对应的类型列表
                    "is_twitter_video": True,
                }
            elif has_videos:
                media_urls = video_urls
                
                return {
                    "url": url,
                    "media_type": "video",
                    "title": text[:100] if text else "Twitter 推文",
                    "author": author,
                    "desc": text,
                    "timestamp": "",  # Twitter API不返回发布时间
                    "media_urls": media_urls,
                    "video_urls": video_urls,  # 单独的视频URL列表（用于后续处理）
                    "thumb_url": video_thumb_urls[0] if video_thumb_urls and video_thumb_urls[0] else None,
                    "video_thumb_urls": video_thumb_urls,  # 每个视频的缩略图URL列表
                    "is_twitter_video": True,
                }
            else:
                if not image_urls:
                    raise RuntimeError("解析失败：推文不包含图片")
                
                return {
                    "url": url,
                    "media_type": "gallery",
                    "title": text[:100] if text else "Twitter 推文",
                    "author": author,
                    "desc": text,
                    "timestamp": "",  # Twitter API不返回发布时间
                    "media_urls": image_urls,
                    "image_urls": image_urls,  # 单独的图片URL列表（用于后续处理）
                    "thumb_url": image_urls[0] if image_urls else None,
                    "is_twitter_video": False,
                }
