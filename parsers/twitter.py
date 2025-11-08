# -*- coding: utf-8 -*-
import re
import asyncio
import aiohttp
import tempfile
import os
from typing import Optional, Dict, Any, List
from .base_parser import BaseVideoParser


class TwitterParser(BaseVideoParser):
    """Twitter/X 视频解析器"""
    
    def __init__(self, max_video_size_mb: float = 0.0, use_proxy: bool = False, proxy_url: str = None, cache_dir: str = "/app/sharedFolder/video_parser/cache/twitter"):
        """
        初始化 Twitter 解析器
        
        Args:
            max_video_size_mb: 最大允许的视频大小(MB)，0表示不限制
            use_proxy: 是否使用代理
            proxy_url: 代理地址（格式：http://host:port 或 socks5://host:port）
            cache_dir: 视频文件缓存目录
        """
        super().__init__("Twitter/X", max_video_size_mb)
        self.use_proxy = use_proxy
        self.proxy_url = proxy_url
        self.cache_dir = cache_dir
        self.semaphore = asyncio.Semaphore(5)
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json',
        }
        
        # 确保缓存目录存在
        os.makedirs(self.cache_dir, exist_ok=True)
    
    def can_parse(self, url: str) -> bool:
        """判断是否可以解析此URL"""
        if not url:
            return False
        # 支持 twitter.com 和 x.com
        url_lower = url.lower()
        if 'twitter.com' in url_lower or 'x.com' in url_lower:
            # 检查是否是推文链接
            if re.search(r'/status/(\d+)', url):
                return True
        return False
    
    def extract_links(self, text: str) -> List[str]:
        """从文本中提取 Twitter 链接"""
        result_links = []
        seen_ids = set()  # 用于去重：记录已提取的推文ID
        # 匹配 twitter.com 和 x.com 的推文链接
        pattern = r'https?://(?:twitter\.com|x\.com)/[^\s]*?status/(\d+)[^\s<>"\'()]*'
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            tweet_id = match.group(1)
            # 使用推文ID去重
            if tweet_id not in seen_ids:
                seen_ids.add(tweet_id)
                # 保留原始URL格式，但统一使用 x.com 域名
                original_url = match.group(0)
                # 标准化为 x.com 格式（保留用户名部分）
                standardized_url = re.sub(r'https?://(?:twitter\.com|x\.com)', 'https://x.com', original_url, flags=re.IGNORECASE)
                result_links.append(standardized_url)
        return result_links
    
    def _get_proxy(self) -> Optional[str]:
        """获取代理地址"""
        if self.use_proxy and self.proxy_url:
            return self.proxy_url
        return None
    
    async def _fetch_media_info(self, session: aiohttp.ClientSession, tweet_id: str) -> Dict[str, Any]:
        """
        使用 FxTwitter API 获取推特媒体直链
        
        Args:
            session: aiohttp 会话
            tweet_id: 推文ID
            
        Returns:
            包含 images 和 videos 的字典
        """
        api_url = f"https://api.fxtwitter.com/status/{tweet_id}"
        
        try:
            proxy = self._get_proxy()
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
                    'author': ''
                }
                
                # 解析媒体
                if 'tweet' in data:
                    tweet = data['tweet']
                    
                    # 获取文本内容
                    media_urls['text'] = tweet.get('text', '')
                    
                    # 获取作者信息
                    author_info = tweet.get('author', {})
                    if isinstance(author_info, dict):
                        author_name = author_info.get('name', '')
                        author_username = author_info.get('screen_name', '')
                        media_urls['author'] = f"{author_name}(@{author_username})" if author_name else author_username
                    
                    # 获取图片
                    if 'media' in tweet and 'photos' in tweet['media']:
                        for photo in tweet['media']['photos']:
                            media_urls['images'].append(photo.get('url', ''))
                    
                    # 获取视频
                    if 'media' in tweet and 'videos' in tweet['media']:
                        for video in tweet['media']['videos']:
                            media_urls['videos'].append({
                                'url': video.get('url', ''),
                                'thumbnail': video.get('thumbnail_url', ''),
                                'duration': video.get('duration', 0)
                            })
                
                return media_urls
        except Exception as e:
            raise RuntimeError(f"获取推文媒体信息失败: {str(e)}")
    
    async def _download_media_to_file(self, session: aiohttp.ClientSession, url: str, tweet_id: str, index: int, is_video: bool = False) -> Optional[str]:
        """
        下载媒体文件到指定位置
        
        Args:
            session: aiohttp 会话
            url: 媒体URL
            tweet_id: 推文ID
            index: 媒体索引（同一推文可能有多个媒体）
            is_video: 是否为视频
            
        Returns:
            文件路径，失败返回 None
        """
        try:
            proxy = self._get_proxy()
            async with session.get(
                url,
                headers=self.headers,
                timeout=aiohttp.ClientTimeout(total=60),
                proxy=proxy
            ) as response:
                response.raise_for_status()
                content = await response.read()
                
                # 确定文件扩展名
                suffix = ".mp4" if is_video else ".jpg"
                
                if is_video:
                    filename = f"{tweet_id}_{index}{suffix}"
                    file_path = os.path.join(self.cache_dir, filename)
                    
                    if os.path.exists(file_path):
                        return os.path.normpath(file_path)
                    
                    with open(file_path, 'wb') as f:
                        f.write(content)
                    
                    return os.path.normpath(file_path)
                else:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                        temp_file.write(content)
                        file_path = os.path.normpath(temp_file.name)
                        return file_path
        except Exception:
            return None
    
    async def parse(self, session: aiohttp.ClientSession, url: str) -> Optional[Dict[str, Any]]:
        """解析单个 Twitter 链接"""
        async with self.semaphore:
            try:
                # 提取推文ID
                tweet_id_match = re.search(r'/status/(\d+)', url)
                if not tweet_id_match:
                    return None
                
                tweet_id = tweet_id_match.group(1)
                
                # 获取媒体信息
                media_info = await self._fetch_media_info(session, tweet_id)
                
                if not media_info.get('images') and not media_info.get('videos'):
                    return None
                
                # 处理视频（下载到缓存目录）
                video_files = []
                if media_info.get('videos'):
                    for idx, video_info in enumerate(media_info['videos']):
                        video_url = video_info.get('url')
                        if video_url:
                            # 检查视频大小
                            if not await self.check_video_size(video_url, session):
                                continue  # 视频过大，跳过
                            
                            # 下载视频到缓存目录
                            video_file = await self._download_media_to_file(session, video_url, tweet_id, idx, is_video=True)
                            if video_file:
                                video_files.append({
                                    'file_path': video_file,
                                    'thumbnail': video_info.get('thumbnail', ''),
                                    'duration': video_info.get('duration', 0)
                                })
                
                # 处理图片（仍然使用临时文件）
                image_files = []
                if media_info.get('images'):
                    for idx, image_url in enumerate(media_info['images']):
                        temp_file = await self._download_media_to_file(session, image_url, tweet_id, idx, is_video=False)
                        if temp_file:
                            image_files.append(temp_file)
                
                # 如果没有成功下载任何媒体，返回 None
                if not video_files and not image_files:
                    return None
                
                # 构建返回结果
                result = {
                    "video_url": url,
                    "title": media_info.get('text', '')[:100] or "Twitter 推文",  # 限制标题长度
                    "author": media_info.get('author', ''),
                    "desc": media_info.get('text', ''),
                }
                
                if video_files:
                    result['video_files'] = video_files
                    result['is_twitter_video'] = True
                elif image_files:
                    result['image_files'] = image_files
                    result['is_twitter_images'] = True
                    result['is_gallery'] = len(image_files) > 1
                
                return result
                
            except Exception:
                return None
    
    def build_media_nodes(self, result: Dict[str, Any], sender_name: str, sender_id: Any, is_auto_pack: bool) -> List:
        """
        构建媒体节点（视频或图片）
        重写基类方法以支持从文件系统加载 Twitter 媒体
        
        Args:
            result: 解析结果
            sender_name: 发送者名称
            sender_id: 发送者ID
            is_auto_pack: 是否打包为Node
            
        Returns:
            List: 媒体节点列表
        """
        nodes = []
        
        # 处理推特视频（从文件）
        if result.get('is_twitter_video') and result.get('video_files'):
            for video_file_info in result['video_files']:
                file_path = video_file_info.get('file_path')
                if file_path:
                    video_node = self._build_video_node_from_file(
                        file_path,
                        sender_name,
                        sender_id,
                        is_auto_pack
                    )
                    if video_node:
                        nodes.append(video_node)
        
        # 处理推特图片（从文件）
        elif result.get('is_twitter_images') and result.get('image_files'):
            gallery_nodes = self._build_gallery_nodes_from_files(
                result['image_files'],
                sender_name,
                sender_id,
                is_auto_pack
            )
            nodes.extend(gallery_nodes)
        
        # 如果不是推特特定内容，回退到基类方法
        elif not result.get('is_twitter_video') and not result.get('is_twitter_images'):
            return super().build_media_nodes(result, sender_name, sender_id, is_auto_pack)
        
        return nodes
