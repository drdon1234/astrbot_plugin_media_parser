# -*- coding: utf-8 -*-
import asyncio
import json
import re
from datetime import datetime
from typing import Optional, Dict, Any, List

import aiohttp

from .base_parser import BaseVideoParser


class DouyinParser(BaseVideoParser):
    """抖音视频解析器"""

    def __init__(self):
        """初始化抖音解析器"""
        super().__init__("抖音")
        self.headers = {
            'User-Agent': (
                'Mozilla/5.0 (Linux; Android 8.0.0; SM-G955U Build/R16NW) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/116.0.0.0 Mobile Safari/537.36'
            ),
            'Referer': (
                'https://www.douyin.com/?is_from_mobile_home=1&recommend=1'
            )
        }
        self.semaphore = asyncio.Semaphore(10)

    def can_parse(self, url: str) -> bool:
        """判断是否可以解析此URL

        Args:
            url: 视频链接

        Returns:
            如果可以解析返回True，否则返回False
        """
        if not url:
            return False
        url_lower = url.lower()
        if 'v.douyin.com' in url_lower or 'douyin.com' in url_lower:
            return True
        return False

    def extract_links(self, text: str) -> List[str]:
        """从文本中提取抖音链接

        Args:
            text: 输入文本

        Returns:
            抖音链接列表
        """
        result_links_set = set()
        seen_ids = set()
        
        mobile_pattern = r'https?://v\.douyin\.com/[^\s]+'
        mobile_links = re.findall(mobile_pattern, text)
        result_links_set.update(mobile_links)
        
        note_pattern = r'https?://(?:www\.)?douyin\.com/note/(\d+)'
        note_matches = re.finditer(note_pattern, text)
        for match in note_matches:
            note_id = match.group(1)
            if note_id not in seen_ids:
                seen_ids.add(note_id)
                result_links_set.add(f"https://www.douyin.com/note/{note_id}")
        
        video_pattern = r'https?://(?:www\.)?douyin\.com/video/(\d+)'
        video_matches = re.finditer(video_pattern, text)
        for match in video_matches:
            video_id = match.group(1)
            if video_id not in seen_ids:
                seen_ids.add(video_id)
                result_links_set.add(f"https://www.douyin.com/video/{video_id}")
        
        web_pattern = r'https?://(?:www\.)?douyin\.com/[^\s]*?(\d{19})[^\s]*'
        web_matches = re.finditer(web_pattern, text)
        for match in web_matches:
            item_id = match.group(1)
            if item_id not in seen_ids:
                matched_url = match.group(0)
                if '/note/' not in matched_url and '/video/' not in matched_url:
                    seen_ids.add(item_id)
                    result_links_set.add(f"https://www.douyin.com/video/{item_id}")
        
        return list(result_links_set)

    def _extract_media_id(self, url: str) -> str:
        """从URL中提取媒体ID

        Args:
            url: 抖音URL

        Returns:
            媒体ID，如果无法提取则返回"douyin"
        """
        video_id_match = (
            re.search(r'/note/(\d+)', url) or
            re.search(r'/video/(\d+)', url) or
            re.search(r'(\d{19})', url)
        )
        return video_id_match.group(1) if video_id_match else "douyin"

    def extract_router_data(self, text: str) -> Optional[str]:
        """从HTML中提取ROUTER_DATA

        Args:
            text: HTML文本

        Returns:
            ROUTER_DATA JSON字符串，如果未找到返回None
        """
        start_flag = 'window._ROUTER_DATA = '
        start_idx = text.find(start_flag)
        if start_idx == -1:
            return None
        brace_start = text.find('{', start_idx)
        if brace_start == -1:
            return None
        i = brace_start
        stack = []
        while i < len(text):
            if text[i] == '{':
                stack.append('{')
            elif text[i] == '}':
                stack.pop()
                if not stack:
                    return text[brace_start:i+1]
            i += 1
        return None

    async def fetch_video_info(
        self,
        session: aiohttp.ClientSession,
        item_id: str,
        is_note: bool = False
    ) -> Optional[Dict[str, Any]]:
        """获取视频/笔记信息

        Args:
            session: aiohttp会话
            item_id: 视频/笔记ID
            is_note: 是否为笔记

        Returns:
            视频/笔记信息字典，如果解析失败返回None
        """
        if is_note:
            url = f'https://www.iesdouyin.com/share/note/{item_id}/'
        else:
            url = f'https://www.iesdouyin.com/share/video/{item_id}/'
        try:
            async with session.get(url, headers=self.headers) as response:
                response_text = await response.text()
                json_str = self.extract_router_data(response_text)
                if not json_str:
                    return None
                json_str = json_str.replace('\\u002F', '/').replace(
                    '\\/',
                    '/'
                )
                try:
                    json_data = json.loads(json_str)
                except Exception:
                    return None
                loader_data = json_data.get('loaderData', {})
                video_info = None
                for key, v in loader_data.items():
                    if isinstance(v, dict) and 'videoInfoRes' in v:
                        video_info = v['videoInfoRes']
                        break
                    elif isinstance(v, dict) and 'noteDetailRes' in v:
                        video_info = v['noteDetailRes']
                        break
                if not video_info:
                    return None
                if ('item_list' not in video_info or
                        not video_info['item_list']):
                    return None
                item_list = video_info['item_list'][0]
                title = item_list.get('desc', '')
                author_info = item_list.get('author', {})
                nickname = author_info.get('nickname', '')
                unique_id = author_info.get('unique_id', '')
                timestamp = ''
                if item_list.get('create_time'):
                    timestamp = datetime.fromtimestamp(
                        item_list.get('create_time', 0)
                    ).strftime('%Y-%m-%d')
                images = []
                image_url_lists = []
                raw_images = item_list.get('images') or []
                for idx, img in enumerate(raw_images):
                    if ('url_list' in img and
                            img.get('url_list') and
                            len(img['url_list']) > 0):
                        url_list = img['url_list']
                        valid_urls = []
                        for url_idx, img_url in enumerate(url_list):
                            if (img_url and
                                    isinstance(img_url, str) and
                                    img_url.startswith(
                                        ('http://', 'https://')
                                    )):
                                valid_urls.append(img_url)
                        if valid_urls:
                            primary_url = valid_urls[0]
                            images.append(primary_url)
                            image_url_lists.append(valid_urls)
                        else:
                            image_url_lists.append([])
                    else:
                        image_url_lists.append([])
                is_gallery = len(images) > 0
                video_url = None
                thumb_url = None
                if not is_gallery and 'video' in item_list:
                    video_info_item = item_list['video']
                    if ('cover' in video_info_item and
                            video_info_item['cover'].get('url_list')):
                        thumb_url = video_info_item['cover']['url_list'][0]
                    if ('play_addr' in video_info_item and
                            'uri' in video_info_item['play_addr']):
                        video = video_info_item['play_addr']['uri']
                        if video.endswith('.mp3'):
                            video_url = video
                        elif video.startswith('https://'):
                            video_url = video
                        else:
                            video_url = (
                                f'https://www.douyin.com/aweme/v1/play/'
                                f'?video_id={video}'
                            )
                elif is_gallery and 'video' in item_list:
                    video_info_item = item_list['video']
                    if ('cover' in video_info_item and
                            video_info_item['cover'].get('url_list')):
                        thumb_url = video_info_item['cover']['url_list'][0]
                if is_gallery and not thumb_url and images:
                    thumb_url = images[0]
                author = nickname
                if unique_id:
                    author = (
                        f"{nickname}(uid:{unique_id})"
                        if nickname
                        else f"(uid:{unique_id})"
                    )
                return {
                    'title': title,
                    'nickname': nickname,
                    'unique_id': unique_id,
                    'author': author,
                    'timestamp': timestamp,
                    'thumb_url': thumb_url,
                    'video_url': video_url,
                    'images': images,
                    'image_url_lists': image_url_lists,
                    'is_gallery': is_gallery
                }
        except aiohttp.ClientError:
            return None

    async def get_redirected_url(
        self,
        session: aiohttp.ClientSession,
        url: str
    ) -> str:
        """获取重定向后的URL

        Args:
            session: aiohttp会话
            url: 原始URL

        Returns:
            重定向后的URL
        """
        async with session.head(url, allow_redirects=True) as response:
            return str(response.url)


    async def parse(
        self,
        session: aiohttp.ClientSession,
        url: str
    ) -> Optional[Dict[str, Any]]:
        """解析单个抖音链接

        Args:
            session: aiohttp会话
            url: 抖音链接

        Returns:
            解析结果字典，包含标准化的元数据格式

        Raises:
            RuntimeError: 当解析失败时
        """
        async with self.semaphore:
            redirected_url = await self.get_redirected_url(session, url)
            is_note = '/note/' in redirected_url or '/note/' in url
            note_id = None
            if is_note:
                note_match = re.search(r'/note/(\d+)', redirected_url)
                if not note_match:
                    note_match = re.search(r'/note/(\d+)', url)
                if note_match:
                    note_id = note_match.group(1)
                    result = await self.fetch_video_info(
                        session,
                        note_id,
                        is_note=True
                    )
                else:
                    raise RuntimeError(f"无法解析此URL: {url}")
            else:
                video_match = re.search(r'/video/(\d+)', redirected_url)
                if video_match:
                    video_id = video_match.group(1)
                    result = await self.fetch_video_info(
                        session,
                        video_id,
                        is_note=False
                    )
                else:
                    match = re.search(r'(\d{19})', redirected_url)
                    if match:
                        item_id = match.group(1)
                        result = await self.fetch_video_info(
                            session,
                            item_id,
                            is_note=False
                        )
                    else:
                        raise RuntimeError(f"无法解析此URL: {url}")
            
            if not result:
                raise RuntimeError(f"无法获取视频信息: {url}")

            is_gallery = result.get('is_gallery', False)
            images = result.get('images', [])
            image_url_lists = result.get('image_url_lists', [])
            video_url = result.get('video_url')
            thumb_url = result.get('thumb_url')
            title = result.get('title', '')
            author = result.get('author', result.get('nickname', ''))
            timestamp = result.get('timestamp', '')
            
            if is_note and note_id:
                display_url = f"https://www.douyin.com/note/{note_id}"
            else:
                display_url = url
            
            if is_gallery:
                media_urls = []
                if images:
                    for idx, primary_url in enumerate(images):
                        media_urls.append(primary_url)
                
                return {
                    "url": display_url,
                    "media_type": "gallery",
                    "title": title,
                    "author": author,
                    "desc": "",
                    "timestamp": timestamp,
                    "media_urls": media_urls,
                    "thumb_url": thumb_url,
                    "image_url_lists": image_url_lists,
                }
            else:
                if not video_url:
                    raise RuntimeError(f"无法获取视频URL: {url}")
                
                return {
                    "url": display_url,
                    "media_type": "video",
                    "title": title,
                    "author": author,
                    "desc": "",
                    "timestamp": timestamp,
                    "media_urls": [video_url],
                    "thumb_url": thumb_url,
                }
