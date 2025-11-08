# -*- coding: utf-8 -*-
import aiohttp
import asyncio
import re
import json
import tempfile
import os
from datetime import datetime
from typing import Optional, Dict, Any, List
from .base_parser import BaseVideoParser


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
        seen_ids = set()
        
        mobile_pattern = r'https?://v\.douyin\.com/[^\s]+'
        mobile_links = re.findall(mobile_pattern, text)
        result_links.extend(mobile_links)
        
        note_pattern = r'https?://(?:www\.)?douyin\.com/note/(\d+)'
        note_matches = re.finditer(note_pattern, text)
        for match in note_matches:
            note_id = match.group(1)
            if note_id not in seen_ids:
                seen_ids.add(note_id)
                result_links.append(f"https://www.douyin.com/note/{note_id}")
        
        video_pattern = r'https?://(?:www\.)?douyin\.com/video/(\d+)'
        video_matches = re.finditer(video_pattern, text)
        for match in video_matches:
            video_id = match.group(1)
            if video_id not in seen_ids:
                seen_ids.add(video_id)
                result_links.append(f"https://www.douyin.com/video/{video_id}")
        
        web_pattern = r'https?://(?:www\.)?douyin\.com/[^\s]*?(\d{19})[^\s]*'
        web_matches = re.finditer(web_pattern, text)
        for match in web_matches:
            item_id = match.group(1)
            if item_id not in seen_ids:
                seen_ids.add(item_id)
                if '/note/' not in match.group(0) and '/video/' not in match.group(0):
                    standardized_url = f"https://www.douyin.com/video/{item_id}"
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

    async def fetch_video_info(self, session, item_id, is_note=False):
        """获取视频/笔记信息"""
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
                json_str = json_str.replace('\\u002F', '/').replace('\\/', '/')
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
                if 'item_list' not in video_info or not video_info['item_list']:
                    return None
                item_list = video_info['item_list'][0]
                title = item_list.get('desc', '')
                author_info = item_list.get('author', {})
                nickname = author_info.get('nickname', '')
                unique_id = author_info.get('unique_id', '')
                timestamp = datetime.fromtimestamp(item_list.get('create_time', 0)).strftime('%Y-%m-%d') if item_list.get('create_time') else ''
                
                images = []  # 每个图片的主URL（第一个有效URL）
                image_url_lists = []  # 每个图片的所有URL列表，用于备用下载
                raw_images = item_list.get('images') or []
                
                for idx, img in enumerate(raw_images):
                    if 'url_list' in img and img.get('url_list') and len(img['url_list']) > 0:
                        url_list = img['url_list']
                        
                        # 提取所有有效的URL
                        valid_urls = []
                        for url_idx, img_url in enumerate(url_list):
                            if img_url and isinstance(img_url, str) and img_url.startswith(('http://', 'https://')):
                                valid_urls.append(img_url)
                        
                        if valid_urls:
                            # 使用第一个有效URL作为主URL
                            primary_url = valid_urls[0]
                            images.append(primary_url)
                            # 保存所有URL作为备用
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
                    if 'cover' in video_info_item and video_info_item['cover'].get('url_list'):
                        thumb_url = video_info_item['cover']['url_list'][0]
                    if 'play_addr' in video_info_item and 'uri' in video_info_item['play_addr']:
                        video = video_info_item['play_addr']['uri']
                        if video.endswith('.mp3'):
                            video_url = video
                        elif video.startswith('https://'):
                            video_url = video
                        else:
                            video_url = f'https://www.douyin.com/aweme/v1/play/?video_id={video}'
                elif is_gallery and 'video' in item_list:
                    video_info_item = item_list['video']
                    if 'cover' in video_info_item and video_info_item['cover'].get('url_list'):
                        thumb_url = video_info_item['cover']['url_list'][0]
                
                if is_gallery and not thumb_url and images:
                    thumb_url = images[0]
                
                author = nickname
                if unique_id:
                    author = f"{nickname}(uid:{unique_id})" if nickname else f"(uid:{unique_id})"
                
                return {
                    'title': title,
                    'nickname': nickname,
                    'unique_id': unique_id,
                    'author': author,
                    'timestamp': timestamp,
                    'thumb_url': thumb_url,
                    'video_url': video_url,
                    'images': images,  # 主URL列表
                    'image_url_lists': image_url_lists,  # 每个图片的所有URL列表（用于备用下载）
                    'is_gallery': is_gallery
                }
        except aiohttp.ClientError:
            return None

    async def get_redirected_url(self, session, url):
        """获取重定向后的URL"""
        async with session.head(url, allow_redirects=True) as response:
            return str(response.url)
    
    async def _download_image_to_file(self, session: aiohttp.ClientSession, image_url: str, image_index: int = 0, referer: str = None) -> Optional[str]:
        """
        下载图片到临时文件
        
        Args:
            session: aiohttp 会话
            image_url: 图片URL
            image_index: 图片索引
            referer: Referer URL，如果提供则使用，否则使用默认的抖音主页
            
        Returns:
            临时文件路径，失败返回 None
        """
        try:
            # 为图片下载添加完整的请求头，包括 Referer 以避免 403 错误
            # 使用真实的浏览器请求头，Referer 优先使用提供的referer，否则使用抖音主页
            referer_url = referer if referer else 'https://www.douyin.com/'
            image_headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Referer': referer_url,
                'Origin': 'https://www.douyin.com',
                'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
                'Accept-Language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Sec-Fetch-Dest': 'image',
                'Sec-Fetch-Mode': 'no-cors',
                'Sec-Fetch-Site': 'same-site',
                'Cache-Control': 'no-cache',
                'Pragma': 'no-cache',
            }
            async with session.get(
                image_url,
                headers=image_headers,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                response.raise_for_status()
                content = await response.read()
                
                # 从URL或Content-Type确定文件扩展名
                content_type = response.headers.get('Content-Type', '')
                
                if 'jpeg' in content_type or 'jpg' in content_type:
                    suffix = '.jpg'
                elif 'png' in content_type:
                    suffix = '.png'
                elif 'webp' in content_type:
                    suffix = '.webp'
                elif 'gif' in content_type:
                    suffix = '.gif'
                else:
                    # 从URL推断
                    if '.jpg' in image_url.lower() or '.jpeg' in image_url.lower():
                        suffix = '.jpg'
                    elif '.png' in image_url.lower():
                        suffix = '.png'
                    elif '.webp' in image_url.lower():
                        suffix = '.webp'
                    elif '.gif' in image_url.lower():
                        suffix = '.gif'
                    else:
                        suffix = '.jpg'  # 默认使用jpg
                
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                    temp_file.write(content)
                    file_path = os.path.normpath(temp_file.name)
                    return file_path
        except Exception:
            return None

    async def parse(self, session: aiohttp.ClientSession, url: str) -> Optional[Dict[str, Any]]:
        """解析单个抖音链接"""
        async with self.semaphore:
            try:
                redirected_url = await self.get_redirected_url(session, url)
                
                is_note = '/note/' in redirected_url or '/note/' in url
                note_id = None
                
                if is_note:
                    note_match = re.search(r'/note/(\d+)', redirected_url)
                    if not note_match:
                        note_match = re.search(r'/note/(\d+)', url)
                    if note_match:
                        note_id = note_match.group(1)
                        result = await self.fetch_video_info(session, note_id, is_note=True)
                    else:
                        return None
                else:
                    video_match = re.search(r'/video/(\d+)', redirected_url)
                    if video_match:
                        video_id = video_match.group(1)
                        result = await self.fetch_video_info(session, video_id, is_note=False)
                    else:
                        match = re.search(r'(\d{19})', redirected_url)
                        if match:
                            item_id = match.group(1)
                            result = await self.fetch_video_info(session, item_id, is_note=False)
                        else:
                            return None
                
                if not result:
                    return None
                
                is_gallery = result.get('is_gallery', False)
                images = result.get('images', [])  # 主URL列表
                image_url_lists = result.get('image_url_lists', [])  # 每个图片的所有URL列表
                
                if is_gallery and images:
                    # 下载图片到临时文件
                    image_files = []
                    
                    # 获取原始页面URL作为Referer
                    if is_note and note_id:
                        page_referer = f"https://www.douyin.com/note/{note_id}"
                    else:
                        page_referer = url
                    
                    # 对每张图片，先尝试主URL，如果失败则尝试备用URL
                    for idx, primary_url in enumerate(images):
                        if not primary_url or not isinstance(primary_url, str) or not primary_url.startswith(('http://', 'https://')):
                            continue
                        
                        # 获取该图片的所有备用URL
                        backup_urls = []
                        if idx < len(image_url_lists) and image_url_lists[idx]:
                            backup_urls = image_url_lists[idx][1:]  # 跳过第一个（主URL）
                        
                        # 尝试下载：先主URL，后备用URL
                        # 尝试主URL
                        image_file = await self._download_image_to_file(session, primary_url, image_index=idx, referer=page_referer)
                        
                        # 如果主URL失败，尝试备用URL
                        if not image_file and backup_urls:
                            for backup_url in backup_urls:
                                image_file = await self._download_image_to_file(session, backup_url, image_index=idx, referer=page_referer)
                                if image_file:
                                    break
                        
                        if image_file:
                            image_files.append(image_file)
                    
                    if not image_files:
                        return None
                    
                    if is_note and note_id:
                        display_url = f"https://www.douyin.com/note/{note_id}"
                    else:
                        display_url = url
                    
                    return {
                        "video_url": display_url,
                        "title": result.get('title', ''),
                        "author": result.get('author', result.get('nickname', '')),
                        "timestamp": result.get('timestamp', ''),
                        "thumb_url": result.get('thumb_url'),
                        "images": images,  # 保留原始URL用于显示
                        "image_files": image_files,  # 临时文件路径
                        "is_gallery": True
                    }
                
                video_url = result.get('video_url')
                if video_url:
                    if not await self.check_video_size(video_url, session):
                        return None
                    return {
                        "video_url": url,
                        "title": result.get('title', ''),
                        "author": result.get('author', result.get('nickname', '')),
                        "timestamp": result.get('timestamp', ''),
                        "thumb_url": result.get('thumb_url'),
                        "direct_url": video_url
                    }
                
                return None
            except Exception:
                return None
    
    def build_media_nodes(self, result: Dict[str, Any], sender_name: str, sender_id: Any, is_auto_pack: bool) -> List:
        """
        构建媒体节点（视频或图片）
        优先使用下载的图片文件而不是URL，以避免QQ/NapCat无法识别文件类型的问题
        
        Args:
            result: 解析结果
            sender_name: 发送者名称
            sender_id: 发送者ID
            is_auto_pack: 是否打包为Node
            
        Returns:
            List: 媒体节点列表
        """
        nodes = []
        
        # 处理图片集（优先使用下载的文件）
        if result.get('is_gallery') and result.get('image_files'):
            gallery_nodes = self._build_gallery_nodes_from_files(
                result['image_files'],
                sender_name,
                sender_id,
                is_auto_pack
            )
            nodes.extend(gallery_nodes)
        # 如果没有下载的文件，回退到使用URL（兼容旧逻辑）
        elif result.get('is_gallery') and result.get('images'):
            gallery_nodes = self._build_gallery_nodes_from_urls(
                result['images'],
                sender_name,
                sender_id,
                is_auto_pack
            )
            nodes.extend(gallery_nodes)
        # 处理视频
        elif result.get('direct_url'):
            video_node = self._build_video_node_from_url(
                result['direct_url'],
                sender_name,
                sender_id,
                is_auto_pack,
                result.get('thumb_url')
            )
            if video_node:
                nodes.append(video_node)
        
        return nodes

