# -*- coding: utf-8 -*-
import aiohttp
import asyncio
import re
import json
import tempfile
import os
from urllib.parse import urlparse
from datetime import datetime
from typing import Optional, Dict, Any, List
from .base_parser import BaseVideoParser


MOBILE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) '
                  'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Accept-Encoding': 'gzip, deflate',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1'
}


class KuaishouParser(BaseVideoParser):
    """快手视频解析器"""
    
    def __init__(self, max_video_size_mb: float = 0.0):
        super().__init__("快手", max_video_size_mb)
        self.headers = MOBILE_HEADERS
        self.semaphore = asyncio.Semaphore(10)
    
    def can_parse(self, url: str) -> bool:
        """判断是否可以解析此URL"""
        if not url:
            return False
        url_lower = url.lower()
        if 'kuaishou.com' in url_lower or 'kspkg.com' in url_lower:
            return True
        return False
    
    def extract_links(self, text: str) -> List[str]:
        """从文本中提取快手链接"""
        result_links = []
        short_pattern = r'https?://v\.kuaishou\.com/[^\s]+'
        short_links = re.findall(short_pattern, text)
        result_links.extend(short_links)
        
        long_pattern = r'https?://(?:www\.)?kuaishou\.com/[^\s]+'
        long_links = re.findall(long_pattern, text)
        result_links.extend(long_links)
        
        return result_links
    
    def _min_mp4(self, url: str) -> str:
        """处理MP4 URL，提取最小格式"""
        pu = urlparse(url)
        domain = pu.netloc
        filename = pu.path.split('/')[-1].split('?')[0]
        path_wo_file = '/'.join(pu.path.split('/')[1:-1])
        return f"https://{domain}/{path_wo_file}/{filename}"
    
    def _extract_upload_time(self, url: str) -> Optional[str]:
        """从URL中提取上传时间"""
        try:
            match = re.search(r'/(\d{4})/(\d{2})/(\d{2})/', url)
            if match:
                year, month, day = match.groups()
                return f"{year}-{month}-{day}"
            match = re.search(r'_(\d{11,13})_', url)
            if match:
                timestamp = int(match.group(1))
                if len(match.group(1)) == 13:
                    timestamp = timestamp // 1000
                dt = datetime.fromtimestamp(timestamp)
                return dt.strftime('%Y-%m-%d')
        except Exception:
            pass
        return None
    
    def _extract_metadata(self, html: str) -> Dict[str, Optional[str]]:
        """提取用户名、UID、标题"""
        metadata = {'userName': None, 'userId': None, 'caption': None}
        
        json_match = re.search(r'window\.INIT_STATE\s*=\s*({.*?});', html, re.DOTALL)
        if not json_match:
            json_match = re.search(r'window\.__APOLLO_STATE__\s*=\s*({.*?});', html, re.DOTALL)
        
        if json_match:
            try:
                json_str = json_match.group(1)
                
                user_match = re.search(r'"userName"\s*:\s*"([^"]+)"', json_str)
                if user_match:
                    metadata['userName'] = user_match.group(1)
                
                uid_match = re.search(r'"userId"\s*:\s*["\']?(\d+)["\']?', json_str)
                if uid_match:
                    metadata['userId'] = uid_match.group(1)
                
                caption_match = re.search(r'"caption"\s*:\s*"([^"]*(?:\\.[^"]*)*)"', json_str)
                if caption_match:
                    raw_caption = caption_match.group(1)
                    try:
                        test_json = f'{{"text":"{raw_caption}"}}'
                        parsed = json.loads(test_json)
                        metadata['caption'] = parsed['text']
                    except Exception:
                        metadata['caption'] = raw_caption
            except Exception:
                pass
        
        if not metadata['caption']:
            title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE)
            if title_match:
                metadata['caption'] = title_match.group(1).strip()
        
        return metadata
    
    def _extract_album_image_url(self, html: str) -> Optional[str]:
        """提取图集图片URL"""
        match = re.search(r'<img\s+class="image"\s+src="([^"]+)"', html)
        if match:
            return match.group(1).split('?')[0]
        match = re.search(r'src="(https?://[^"]*?/upic/[^"]*?\.jpg)', html)
        if match:
            return match.group(1)
        return None
    
    def _build_album(self, cdns: List[str], music_path: Optional[str], img_paths: List[str]) -> Dict[str, Any]:
        """
        构建图集数据，支持多个CDN
        
        Args:
            cdns: CDN列表
            music_path: 音乐路径
            img_paths: 图片路径列表
            
        Returns:
            包含 images（主URL列表）和 image_url_lists（每个图片的所有CDN URL列表）的字典
        """
        # 清理CDN，去除协议前缀
        cleaned_cdns = [re.sub(r'https?://', '', cdn) for cdn in cdns if cdn]
        if not cleaned_cdns:
            return None
        
        cleaned_paths = [p.strip('"') for p in img_paths if p.strip('"')]
        if not cleaned_paths:
            return None
        
        # 为每个图片生成所有CDN的URL
        images = []  # 每个图片的主URL（第一个CDN）
        image_url_lists = []  # 每个图片的所有CDN URL列表，用于备用下载
        
        for img_path in cleaned_paths:
            # 为当前图片生成所有CDN的URL
            url_list = []
            for cdn in cleaned_cdns:
                url = f"https://{cdn}{img_path}"
                url_list.append(url)
            
            if url_list:
                # 第一个URL作为主URL
                images.append(url_list[0])
                # 保存所有URL作为备用
                image_url_lists.append(url_list)
        
        # 去重主URL列表（保留第一个出现的）
        # 注意：去重时确保 images 和 image_url_lists 的索引一一对应
        seen = set()
        uniq_images = []
        uniq_image_url_lists = []
        for idx, img_url in enumerate(images):
            if img_url not in seen:
                seen.add(img_url)
                uniq_images.append(img_url)
                # 确保 image_url_lists[idx] 的第一个URL就是 img_url
                url_list = image_url_lists[idx].copy() if image_url_lists[idx] else []
                # 如果 url_list 的第一个URL不是 img_url，调整顺序
                if url_list and url_list[0] != img_url:
                    if img_url in url_list:
                        url_list.remove(img_url)
                    url_list.insert(0, img_url)
                uniq_image_url_lists.append(url_list)
        
        # 处理背景音乐
        bgm = None
        if music_path and cleaned_cdns:
            cleaned_music = music_path.strip('"')
            bgm = f"https://{cleaned_cdns[0]}{cleaned_music}"
        
        return {
            'type': 'album',
            'bgm': bgm,
            'images': uniq_images,  # 主URL列表
            'image_url_lists': uniq_image_url_lists  # 每个图片的所有CDN URL列表
        }
    
    def _parse_album(self, html: str) -> Optional[Dict[str, Any]]:
        """
        解析图集，提取所有CDN
        
        Returns:
            包含 images 和 image_url_lists 的字典，如果解析失败返回 None
        """
        # 提取所有CDN（可能有多个）
        cdn_matches = re.findall(r'"cdnList"\s*:\s*\[.*?"cdn"\s*:\s*"([^"]+)"', html, re.DOTALL)
        if not cdn_matches:
            cdn_matches = re.findall(r'"cdn"\s*:\s*\["([^"]+)"', html)
        if not cdn_matches:
            cdn_matches = re.findall(r'"cdn"\s*:\s*"([^"]+)"', html)
        if not cdn_matches:
            return None
        
        # 保留所有CDN，而不是只取第一个
        cdns = list(set(cdn_matches))  # 去重但保留所有CDN
        
        img_paths = re.findall(r'"/ufile/atlas/[^"]+?\.jpg"', html)
        if not img_paths:
            return None
        
        m = re.search(r'"music"\s*:\s*"(/ufile/atlas/[^"]+?\.m4a)"', html)
        music_path = m.group(1) if m else None
        
        return self._build_album(cdns, music_path, img_paths)
    
    def _parse_video(self, html: str) -> Optional[str]:
        """解析视频URL"""
        m = re.search(r'"(url|srcNoMark|photoUrl|videoUrl)"\s*:\s*"(https?://[^"]+?\.mp4[^"]*)"', html)
        if not m:
            m = re.search(r'"url"\s*:\s*"(https?://[^"]+?\.mp4[^"]*)"', html)
        if m:
            return self._min_mp4(m.group(2))
        return None
    
    async def _download_image_to_file(self, session: aiohttp.ClientSession, image_url: str, image_index: int = 0) -> Optional[str]:
        """
        下载图片到临时文件，支持备用CDN重试
        
        Args:
            session: aiohttp 会话
            image_url: 图片URL
            image_index: 图片索引
            
        Returns:
            临时文件路径，失败返回 None
        """
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Referer': 'https://www.kuaishou.com/',
                'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
                'Accept-Language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
            }
            async with session.get(
                image_url,
                headers=headers,
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
        """解析单个快手链接"""
        async with self.semaphore:
            try:
                is_short = 'v.kuaishou.com' in urlparse(url).netloc
                
                if is_short:
                    async with session.get(url, headers=self.headers, allow_redirects=False) as r1:
                        if r1.status != 302:
                            return None
                        loc = r1.headers.get('Location')
                        if not loc:
                            return None
                    async with session.get(loc, headers=self.headers) as r2:
                        if r2.status != 200:
                            return None
                        html = await r2.text()
                else:
                    async with session.get(url, headers=self.headers) as r:
                        if r.status != 200:
                            return None
                        html = await r.text()
                
                metadata = self._extract_metadata(html)
                
                userName = metadata.get('userName', '')
                userId = metadata.get('userId', '')
                
                if userName and userId:
                    author = f"{userName}(uid:{userId})"
                elif userName:
                    author = userName
                elif userId:
                    author = f"(uid:{userId})"
                else:
                    author = ""
                
                title = metadata.get('caption', '') or "快手视频"
                if len(title) > 100:
                    title = title[:100]
                
                video_url = self._parse_video(html)
                if video_url:
                    if not await self.check_video_size(video_url, session):
                        return None
                    upload_time = self._extract_upload_time(video_url)
                    return {
                        "video_url": url,
                        "title": title,
                        "author": author,
                        "timestamp": upload_time or "",
                        "direct_url": video_url
                    }
                
                album = self._parse_album(html)
                if album:
                    images = album.get('images', [])
                    image_url_lists = album.get('image_url_lists', [])
                    
                    if images:
                        # 下载图片到临时文件
                        image_files = []
                        
                        # 对每张图片，按CDN顺序尝试下载，避免重复访问和重复下载
                        for idx, primary_url in enumerate(images):
                            if not primary_url or not isinstance(primary_url, str) or not primary_url.startswith(('http://', 'https://')):
                                continue
                            
                            # 获取该图片的所有CDN URL列表
                            # _build_album 已确保 image_url_lists[idx][0] == images[idx] (primary_url)
                            all_urls = []
                            if idx < len(image_url_lists) and image_url_lists[idx]:
                                all_urls = image_url_lists[idx]
                            else:
                                # 如果索引不匹配，使用 primary_url 作为唯一URL
                                all_urls = [primary_url]
                            
                            # 按顺序尝试所有CDN URL，每个URL只尝试一次
                            # 第一个成功即停止，避免重复下载同一张图片
                            image_file = None
                            for url in all_urls:
                                image_file = await self._download_image_to_file(session, url, image_index=idx)
                                if image_file:
                                    break  # 下载成功，停止尝试其他CDN
                            
                            if image_file:
                                image_files.append(image_file)
                        
                        if not image_files:
                            return None
                        
                        image_url = self._extract_album_image_url(html)
                        upload_time = self._extract_upload_time(image_url) if image_url else None
                        return {
                            "video_url": url,
                            "title": title or "快手图集",
                            "author": author,
                            "timestamp": upload_time or "",
                            "images": images,  # 保留原始URL用于显示
                            "image_files": image_files,  # 临时文件路径
                            "is_gallery": True
                        }
                
                json_match = re.search(r'<script[^>]*>window\.rawData\s*=\s*({.*?});?</script>', html, re.DOTALL)
                if json_match:
                    try:
                        data = json.loads(json_match.group(1))
                        if 'video' in data:
                            vurl = data['video'].get('url') or data['video'].get('srcNoMark')
                            if vurl and '.mp4' in vurl:
                                video_url = self._min_mp4(vurl)
                                if not await self.check_video_size(video_url, session):
                                    return None
                                upload_time = self._extract_upload_time(video_url)
                                return {
                                    "video_url": url,
                                    "title": title,
                                    "author": author,
                                    "timestamp": upload_time or "",
                                    "direct_url": video_url
                                }
                        elif 'photo' in data and data.get('type') == 1:
                            cdn_raw = data['photo'].get('cdn', ['p3.a.yximgs.com'])
                            # 支持多个CDN
                            if isinstance(cdn_raw, list):
                                cdns = cdn_raw if len(cdn_raw) > 0 else ['p3.a.yximgs.com']
                            elif isinstance(cdn_raw, str):
                                cdns = [cdn_raw]
                            else:
                                cdns = ['p3.a.yximgs.com']
                            
                            music = data['photo'].get('music')
                            img_list = data['photo'].get('list', [])
                            album_data = self._build_album(cdns, music, img_list)
                            
                            if album_data:
                                images = album_data.get('images', [])
                                image_url_lists = album_data.get('image_url_lists', [])
                                
                                if images:
                                    # 下载图片到临时文件
                                    image_files = []
                                    
                                    # 对每张图片，按CDN顺序尝试下载，避免重复访问和重复下载
                                    for idx, primary_url in enumerate(images):
                                        if not primary_url or not isinstance(primary_url, str) or not primary_url.startswith(('http://', 'https://')):
                                            continue
                                        
                                        # 获取该图片的所有CDN URL列表
                                        # _build_album 已确保 image_url_lists[idx][0] == images[idx] (primary_url)
                                        all_urls = []
                                        if idx < len(image_url_lists) and image_url_lists[idx]:
                                            all_urls = image_url_lists[idx]
                                        else:
                                            # 如果索引不匹配，使用 primary_url 作为唯一URL
                                            all_urls = [primary_url]
                                        
                                        # 按顺序尝试所有CDN URL，每个URL只尝试一次
                                        # 第一个成功即停止，避免重复下载同一张图片
                                        image_file = None
                                        for url in all_urls:
                                            image_file = await self._download_image_to_file(session, url, image_index=idx)
                                            if image_file:
                                                break  # 下载成功，停止尝试其他CDN
                                        
                                        if image_file:
                                            image_files.append(image_file)
                                    
                                    if not image_files:
                                        return None
                                    
                                    image_url = self._extract_album_image_url(html)
                                    upload_time = self._extract_upload_time(image_url) if image_url else None
                                    return {
                                        "video_url": url,
                                        "title": title or "快手图集",
                                        "author": author,
                                        "timestamp": upload_time or "",
                                        "images": images,  # 保留原始URL用于显示
                                        "image_files": image_files,  # 临时文件路径
                                        "is_gallery": True
                                    }
                    except json.JSONDecodeError:
                        pass
                
                if metadata.get('userName') or metadata.get('userId') or metadata.get('caption'):
                    return {
                        "video_url": url,
                        "title": title,
                        "author": author,
                        "timestamp": "",
                        "direct_url": None
                    }
                
                return None
            except Exception:
                return None
    
    def build_media_nodes(self, result: Dict[str, Any], sender_name: str, sender_id: Any, is_auto_pack: bool) -> List:
        """
        构建媒体节点（视频或图片）
        对于图集，优先使用下载的图片文件，避免发送时下载失败
        使用下载的图片文件而不是URL，以避免QQ/NapCat无法识别文件类型的问题
        
        Args:
            result: 解析结果
            sender_name: 发送者名称
            sender_id: 发送者ID
            is_auto_pack: 是否打包为Node
            
        Returns:
            List: 媒体节点列表
        """
        from astrbot.api.message_components import Video, Image, Node
        
        nodes = []
        
        # 处理图片集（优先使用下载的文件）
        if result.get('is_gallery') and result.get('image_files'):
            image_files = result['image_files']
            if isinstance(image_files, list) and len(image_files) > 0:
                if is_auto_pack:
                    gallery_node_content = []
                    for image_path in image_files:
                        if image_path:
                            image_path = os.path.normpath(image_path)
                            if os.path.exists(image_path):
                                try:
                                    image_node_content = Image.fromFileSystem(image_path)
                                    image_node = Node(
                                        name=sender_name,
                                        uin=sender_id,
                                        content=[image_node_content]
                                    )
                                    gallery_node_content.append(image_node)
                                except Exception:
                                    # 如果加载失败，清理临时文件
                                    if os.path.exists(image_path):
                                        try:
                                            os.unlink(image_path)
                                        except Exception:
                                            pass
                                    continue
                    if gallery_node_content:
                        # 仅在图片数量 > 1 时创建父节点
                        if len(gallery_node_content) > 1:
                            parent_gallery_node = Node(
                                name=sender_name,
                                uin=sender_id,
                                content=gallery_node_content
                            )
                            nodes.append(parent_gallery_node)
                        else:
                            nodes.extend(gallery_node_content)
                else:
                    for image_path in image_files:
                        if image_path:
                            image_path = os.path.normpath(image_path)
                            if os.path.exists(image_path):
                                try:
                                    nodes.append(Image.fromFileSystem(image_path))
                                except Exception:
                                    # 如果加载失败，清理临时文件
                                    if os.path.exists(image_path):
                                        try:
                                            os.unlink(image_path)
                                        except Exception:
                                            pass
                                    continue
        # 如果没有下载的文件，回退到使用URL（兼容旧逻辑）
        elif result.get('is_gallery') and result.get('images'):
            images = result['images']
            if isinstance(images, list) and len(images) > 0:
                valid_images = [img for img in images if img and isinstance(img, str) and img.startswith(('http://', 'https://'))]
                if valid_images:
                    if is_auto_pack:
                        gallery_node_content = []
                        for image_url in valid_images:
                            try:
                                image_node = Node(
                                    name=sender_name,
                                    uin=sender_id,
                                    content=[Image.fromURL(image_url)]
                                )
                                gallery_node_content.append(image_node)
                            except Exception:
                                continue
                        if gallery_node_content:
                            # 仅在图片数量 > 1 时创建父节点
                            if len(gallery_node_content) > 1:
                                parent_gallery_node = Node(
                                    name=sender_name,
                                    uin=sender_id,
                                    content=gallery_node_content
                                )
                                nodes.append(parent_gallery_node)
                            else:
                                nodes.extend(gallery_node_content)
                    else:
                        for image_url in valid_images:
                            try:
                                nodes.append(Image.fromURL(image_url))
                            except Exception:
                                continue
        # 处理视频
        elif result.get('direct_url'):
            if is_auto_pack:
                video_node = Node(
                    name=sender_name,
                    uin=sender_id,
                    content=[Video.fromURL(result['direct_url'])]
                )
            else:
                cover = result.get('thumb_url')
                video_node = Video.fromURL(result['direct_url'], cover=cover) if cover else Video.fromURL(result['direct_url'])
            nodes.append(video_node)
        
        return nodes
