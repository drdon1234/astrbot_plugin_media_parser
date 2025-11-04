# -*- coding: utf-8 -*-
import aiohttp
import asyncio
import re
import json
from datetime import datetime
from typing import Optional, Dict, Any, List
from ..base_parser import BaseVideoParser


class DouyinParser(BaseVideoParser):
    """抖音视频解析器"""
    
    def __init__(self, max_video_size_mb: float = 0.0):
        super().__init__("抖音", max_video_size_mb)
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 8.0.0; SM-G955U Build/R16NW) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36',
            'Referer': 'https://www.douyin.com/?is_from_mobile_home=1&recommend=1'
        }
        self.semaphore = asyncio.Semaphore(10)

    def can_parse(self, url: str) -> bool:
        """判断是否可以解析此URL"""
        if not url:
            return False
        # 检查是否为抖音短链
        if 'v.douyin.com' in url or 'douyin.com' in url:
            return True
        return False

    def extract_links(self, text: str) -> List[str]:
        """从文本中提取抖音链接"""
        result_links = []
        mobile_pattern = r'https?://v\.douyin\.com/[^\s]+'
        mobile_links = re.findall(mobile_pattern, text)
        result_links.extend(mobile_links)
        web_pattern = r'https?://(?:www\.)?douyin\.com/[^\s]*?(\d{19})[^\s]*'
        web_matches = re.finditer(web_pattern, text)
        for match in web_matches:
            video_id = match.group(1)
            standardized_url = f"https://www.douyin.com/video/{video_id}"
            result_links.append(standardized_url)
        return result_links

    def extract_router_data(self, text):
        """从HTML中提取ROUTER_DATA"""
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

    async def fetch_video_info(self, session, video_id):
        """获取视频信息"""
        url = f'https://www.iesdouyin.com/share/video/{video_id}/'
        try:
            async with session.get(url, headers=self.headers) as response:
                response_text = await response.text()
                json_str = self.extract_router_data(response_text)
                if not json_str:
                    print('未找到 _ROUTER_DATA')
                    return None
                json_str = json_str.replace('\\u002F', '/').replace('\\/', '/')
                try:
                    json_data = json.loads(json_str)
                except Exception as e:
                    print('JSON解析失败', e)
                    return None
                loader_data = json_data.get('loaderData', {})
                video_info = None
                for v in loader_data.values():
                    if isinstance(v, dict) and 'videoInfoRes' in v:
                        video_info = v['videoInfoRes']
                        break
                if not video_info or 'item_list' not in video_info or not video_info['item_list']:
                    print('未找到视频信息')
                    return None
                item_list = video_info['item_list'][0]
                title = item_list['desc']
                nickname = item_list['author']['nickname']
                timestamp = datetime.fromtimestamp(item_list['create_time']).strftime('%Y-%m-%d')
                thumb_url = item_list['video']['cover']['url_list'][0]
                video = item_list['video']['play_addr']['uri']
                if video.endswith('.mp3'):
                    video_url = video
                elif video.startswith('https://'):
                    video_url = video
                else:
                    video_url = f'https://www.douyin.com/aweme/v1/play/?video_id={video}'
                images = [img['url_list'][0] for img in (item_list.get('images') or []) if 'url_list' in img]
                is_gallery = len(images) > 0
                return {
                    'title': title,
                    'nickname': nickname,
                    'timestamp': timestamp,
                    'thumb_url': thumb_url,
                    'video_url': video_url,
                    'images': images,
                    'is_gallery': is_gallery
                }
        except aiohttp.ClientError as e:
            print(f'请求错误：{e}')
            return None

    async def get_redirected_url(self, session, url):
        """获取重定向后的URL"""
        async with session.head(url, allow_redirects=True) as response:
            return str(response.url)

    async def parse(self, session: aiohttp.ClientSession, url: str) -> Optional[Dict[str, Any]]:
        """解析单个抖音链接"""
        async with self.semaphore:
            try:
                redirected_url = await self.get_redirected_url(session, url)
                match = re.search(r'(\d+)', redirected_url)
                if match:
                    video_id = match.group(1)
                    result = await self.fetch_video_info(session, video_id)
                    if result:
                        # 检查视频大小（如果不是图片集）
                        if not result.get('is_gallery') and result.get('video_url'):
                            if not await self.check_video_size(result['video_url'], session):
                                return None  # 视频过大，跳过
                        # 转换为统一格式
                        return {
                            "video_url": url,
                            "title": result.get('title', ''),
                            "author": result.get('nickname', ''),
                            "timestamp": result.get('timestamp', ''),
                            "thumb_url": result.get('thumb_url'),
                            "direct_url": result.get('video_url'),
                            "images": result.get('images', []),
                            "is_gallery": result.get('is_gallery', False)
                        }
                return None
            except Exception as e:
                print(f"解析抖音链接失败 {url}: {e}", flush=True)
                return None

