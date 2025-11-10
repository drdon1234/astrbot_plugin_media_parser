# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
import aiohttp
import os
import re
import asyncio


class BaseVideoParser(ABC):

    def __init__(self, name: str, max_media_size_mb: float = 0.0, large_media_threshold_mb: float = 50.0, cache_dir: str = "/app/sharedFolder/video_parser/cache", pre_download_all_media: bool = False, max_concurrent_downloads: int = 3):
        """
        初始化插件
        Args:
            name: 解析器名称
            max_media_size_mb: 最大允许的媒体大小(MB)
            large_media_threshold_mb: 大媒体阈值(MB)
            cache_dir: 媒体缓存目录
            pre_download_all_media: 是否预先下载所有媒体到本地
            max_concurrent_downloads: 最大并发下载数
        """
        self.name = name
        self.max_media_size_mb = max_media_size_mb
        self.cache_dir = cache_dir
        self.pre_download_all_media = pre_download_all_media
        self.max_concurrent_downloads = max_concurrent_downloads
        self.semaphore = None
        if large_media_threshold_mb > 0:
            self.large_media_threshold_mb = min(large_media_threshold_mb, 100.0)
        else:
            self.large_media_threshold_mb = 0.0
        self.cache_dir_available = self._check_cache_dir_available(cache_dir)
        if self.cache_dir_available and cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    @abstractmethod
    def can_parse(self, url: str) -> bool:
        """
        判断是否可以解析此URL
        Args:
            url: 视频链接
        Returns:
            bool: 布尔值
        """
        pass

    @abstractmethod
    def extract_links(self, text: str) -> List[str]:
        """
        从文本中提取链接
        Args:
            text: 输入文本
        Returns:
            List[str]: 字符串列表
        """
        pass

    @abstractmethod
    async def parse(self, session: aiohttp.ClientSession, url: str) -> Optional[Dict[str, Any]]:
        """
        解析单个视频链接
        Args:
            session: aiohttp会话
            url: 视频链接
        Returns:
            Optional: 返回值
        """
        pass

    async def get_video_size(self, video_url: str, session: aiohttp.ClientSession) -> Optional[float]:
        """
        获取视频文件大小
        Args:
            video_url: 视频URL
            session: aiohttp会话
        Returns:
            Optional[float]: 浮点数或None
        """
        try:
            async with session.head(video_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                content_range = resp.headers.get("Content-Range")
                if content_range:
                    match = re.search(r'/\s*(\d+)', content_range)
                    if match:
                        size_bytes = int(match.group(1))
                        size_mb = size_bytes / (1024 * 1024)
                        return size_mb
                content_length = resp.headers.get("Content-Length")
                if content_length:
                    size_bytes = int(content_length)
                    size_mb = size_bytes / (1024 * 1024)
                    return size_mb
        except Exception:
            pass
        return None

    def _check_cache_dir_available(self, cache_dir: str) -> bool:
        """
        检查缓存目录是否可用（可写）
        Args:
            cache_dir: 缓存目录路径
        Returns:
            bool: 如果目录可用返回True，否则返回False
        """
        if not cache_dir:
            return False
        try:
            os.makedirs(cache_dir, exist_ok=True)
            test_file = os.path.join(cache_dir, ".test_write")
            try:
                with open(test_file, 'w') as f:
                    f.write("test")
                os.unlink(test_file)
                return True
            except Exception:
                return False
        except Exception:
            return False

    async def get_image_size(self, image_url: str, session: aiohttp.ClientSession, headers: dict = None) -> Optional[float]:
        """
        获取图片文件大小
        Args:
            image_url: 图片URL
            session: aiohttp会话
            headers: 请求头（可选）
        Returns:
            Optional[float]: 图片大小(MB)，如果无法获取则返回None
        """
        try:
            request_headers = headers or {}
            async with session.head(image_url, headers=request_headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                content_range = resp.headers.get("Content-Range")
                if content_range:
                    match = re.search(r'/\s*(\d+)', content_range)
                    if match:
                        size_bytes = int(match.group(1))
                        size_mb = size_bytes / (1024 * 1024)
                        return size_mb
                content_length = resp.headers.get("Content-Length")
                if content_length:
                    size_bytes = int(content_length)
                    size_mb = size_bytes / (1024 * 1024)
                    return size_mb
        except Exception:
            pass
        return None

    async def check_media_size(self, media_url: str, session: aiohttp.ClientSession, is_video: bool = True, headers: dict = None) -> bool:
        """
        检查媒体大小是否在允许范围内
        Args:
            media_url: 媒体URL
            session: aiohttp会话
            is_video: 是否为视频（True为视频，False为图片）
            headers: 请求头（可选）
        Returns:
            bool: 如果大小在允许范围内返回True，否则返回False
        """
        if self.max_media_size_mb <= 0:
            return True
        if is_video:
            media_size = await self.get_video_size(media_url, session)
        else:
            media_size = await self.get_image_size(media_url, session, headers)
        if media_size is None:
            return True
        return media_size <= self.max_media_size_mb

    async def check_video_size(self, video_url: str, session: aiohttp.ClientSession) -> bool:
        """
        检查视频大小是否在允许范围内（兼容旧接口）
        Args:
            video_url: 视频URL
            session: aiohttp会话
        Returns:
            bool: 布尔值
        """
        return await self.check_media_size(video_url, session, is_video=True)

    @staticmethod
    def _get_image_suffix(content_type: str = None, url: str = None) -> str:
        """
        根据 Content-Type 或 URL 确定图片文件扩展名
        Args:
            content_type: HTTP Content-Type 头
            url: 图片URL
        Returns:
            str: 文件扩展名（.jpg, .png, .webp, .gif）
        """
        if content_type:
            if 'jpeg' in content_type or 'jpg' in content_type:
                return '.jpg'
            elif 'png' in content_type:
                return '.png'
            elif 'webp' in content_type:
                return '.webp'
            elif 'gif' in content_type:
                return '.gif'
        
        if url:
            url_lower = url.lower()
            if '.jpg' in url_lower or '.jpeg' in url_lower:
                return '.jpg'
            elif '.png' in url_lower:
                return '.png'
            elif '.webp' in url_lower:
                return '.webp'
            elif '.gif' in url_lower:
                return '.gif'
        
        return '.jpg'  # 默认

    async def _download_image_to_file(
        self,
        session: aiohttp.ClientSession,
        image_url: str,
        index: int = 0,
        headers: dict = None,
        referer: str = None,
        default_referer: str = None
    ) -> Optional[str]:
        """
        下载图片到临时文件（通用方法）
        Args:
            session: aiohttp 会话
            image_url: 图片URL
            index: 图片索引
            headers: 自定义请求头（如果提供，会与默认请求头合并）
            referer: Referer URL，如果提供则使用
            default_referer: 默认 Referer URL（如果 referer 未提供）
        Returns:
            Optional[str]: 临时文件路径，失败返回 None
        """
        import tempfile
        try:
            referer_url = referer if referer else (default_referer or '')
            default_headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
                'Accept-Language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
            }
            if referer_url:
                default_headers['Referer'] = referer_url
            
            # 合并自定义请求头
            if headers:
                default_headers.update(headers)
            
            async with session.get(
                image_url,
                headers=default_headers,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                response.raise_for_status()
                content = await response.read()
                content_type = response.headers.get('Content-Type', '')
                suffix = self._get_image_suffix(content_type, image_url)
                
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                    temp_file.write(content)
                    file_path = os.path.normpath(temp_file.name)
                    return file_path
        except Exception:
            return None

    def _move_temp_file_to_cache(
        self,
        temp_file_path: str,
        media_id: str,
        index: int
    ) -> Optional[str]:
        """
        将临时文件移动到缓存目录
        Args:
            temp_file_path: 临时文件路径
            media_id: 媒体ID
            index: 索引
        Returns:
            Optional[str]: 缓存文件路径或None
        """
        if not self.cache_dir_available or not self.cache_dir or not temp_file_path:
            # 如果缓存目录不可用，确保删除临时文件
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.unlink(temp_file_path)
                except Exception:
                    pass
            return None
        cache_path = None
        temp_file_existed = os.path.exists(temp_file_path)
        try:
            if not temp_file_existed:
                return None
            
            # 读取临时文件内容
            content = None
            try:
                with open(temp_file_path, 'rb') as f:
                    content = f.read()
            except Exception:
                # 读取失败，删除临时文件
                if temp_file_existed:
                    try:
                        os.unlink(temp_file_path)
                    except Exception:
                        pass
                return None
            
            # 确定文件扩展名
            suffix = self._get_image_suffix(url=temp_file_path)
            if not content:
                # 内容为空，删除临时文件
                if temp_file_existed:
                    try:
                        os.unlink(temp_file_path)
                    except Exception:
                        pass
                return None
            
            # 根据文件内容确定扩展名（更准确）
            if content.startswith(b'\xff\xd8'):
                suffix = '.jpg'
            elif content.startswith(b'\x89PNG'):
                suffix = '.png'
            elif content.startswith(b'RIFF') and b'WEBP' in content[:12]:
                suffix = '.webp'
            elif content.startswith(b'GIF'):
                suffix = '.gif'
            
            # 保存到缓存目录
            cache_filename = f"{media_id}_{index}{suffix}"
            cache_path = os.path.join(self.cache_dir, cache_filename)
            try:
                with open(cache_path, 'wb') as f:
                    f.write(content)
            except Exception:
                # 写入缓存失败，删除临时文件
                if temp_file_existed:
                    try:
                        os.unlink(temp_file_path)
                    except Exception:
                        pass
                return None
            
            # 缓存文件写入成功，删除临时文件
            if temp_file_existed:
                try:
                    os.unlink(temp_file_path)
                except Exception:
                    # 如果删除失败，至少确保缓存文件已创建
                    pass
            
            return os.path.normpath(cache_path)
        except Exception:
            # 发生异常，确保清理临时文件
            if temp_file_existed and os.path.exists(temp_file_path):
                try:
                    os.unlink(temp_file_path)
                except Exception:
                    pass
            # 如果缓存文件已创建但发生异常，也删除缓存文件
            if cache_path and os.path.exists(cache_path):
                try:
                    os.unlink(cache_path)
                except Exception:
                    pass
            return None

    async def _retry_download_with_backup_urls(
        self,
        session: aiohttp.ClientSession,
        primary_result: Dict[str, Any],
        backup_urls: List[str],
        media_id: str,
        index: int,
        headers: dict = None,
        referer: str = None,
        default_referer: str = None
    ) -> Optional[str]:
        """
        使用备用URL重试下载（用于预下载失败时）
        Args:
            session: aiohttp会话
            primary_result: 主URL下载结果（包含 success 和 file_path）
            backup_urls: 备用URL列表
            media_id: 媒体ID
            index: 索引
            headers: 请求头
            referer: Referer URL
            default_referer: 默认 Referer URL
        Returns:
            Optional[str]: 缓存文件路径或None（注意：如果返回临时文件路径，调用者必须负责清理）
        """
        # 如果主URL下载成功，直接返回
        if primary_result.get('success') and primary_result.get('file_path'):
            return primary_result['file_path']
        
        # 尝试备用URL
        if not backup_urls:
            return None
        
        temp_files_to_cleanup = []
        try:
            for backup_url in backup_urls:
                if not backup_url or not isinstance(backup_url, str):
                    continue
                
                # 下载到临时文件
                temp_file = await self._download_image_to_file(
                    session, backup_url, index, headers, referer, default_referer
                )
                
                if temp_file:
                    # 移动到缓存目录（该方法会处理临时文件清理）
                    cache_path = self._move_temp_file_to_cache(temp_file, media_id, index)
                    if cache_path:
                        # 清理之前失败的临时文件
                        for tf in temp_files_to_cleanup:
                            if tf and os.path.exists(tf):
                                try:
                                    os.unlink(tf)
                                except Exception:
                                    pass
                        return cache_path
                    # 如果移动到缓存失败，记录临时文件以便后续清理
                    temp_files_to_cleanup.append(temp_file)
            
            # 所有备用URL都失败，清理所有临时文件
            for temp_file in temp_files_to_cleanup:
                if temp_file and os.path.exists(temp_file):
                    try:
                        os.unlink(temp_file)
                    except Exception:
                        pass
            return None
        except Exception:
            # 发生异常，清理所有临时文件
            for temp_file in temp_files_to_cleanup:
                if temp_file and os.path.exists(temp_file):
                    try:
                        os.unlink(temp_file)
                    except Exception:
                        pass
            return None

    async def _download_large_media_to_cache(self, session: aiohttp.ClientSession, media_url: str, media_id: str, index: int = 0, headers: dict = None, is_video: bool = True, referer: str = None, default_referer: str = None, proxy: str = None) -> Optional[str]:
        """
        下载大媒体到缓存目录
        Args:
            session: aiohttp会话
            media_url: 媒体URL
            media_id: 媒体ID
            index: 索引
            headers: 自定义请求头（如果提供，会与默认请求头合并）
            is_video: 是否为视频（True为视频，False为图片）
            referer: Referer URL，如果提供则使用
            default_referer: 默认 Referer URL（如果 referer 未提供）
            proxy: 代理地址（可选）
        Returns:
            Optional[str]: 文件路径或None
        """
        if not self.cache_dir_available or not self.cache_dir:
            return None
        try:
            # 对于图片，使用与_download_image_to_file完全相同的逻辑
            if not is_video:
                referer_url = referer if referer else (default_referer or '')
                default_headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
                    'Accept-Language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
                }
                if referer_url:
                    default_headers['Referer'] = referer_url
                
                # 合并自定义请求头
                if headers:
                    default_headers.update(headers)
                
                async with session.get(
                    media_url,
                    headers=default_headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                    proxy=proxy
                ) as response:
                    response.raise_for_status()
                    content = await response.read()
                    content_type = response.headers.get('Content-Type', '')
                    suffix = self._get_image_suffix(content_type, media_url)
                    filename = f"{media_id}_{index}{suffix}"
                    file_path = os.path.join(self.cache_dir, filename)
                    if os.path.exists(file_path):
                        return os.path.normpath(file_path)
                    with open(file_path, 'wb') as f:
                        f.write(content)
                    return os.path.normpath(file_path)
            else:
                # 视频下载逻辑（保持原有逻辑）
                default_headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': '*/*',
                    'Accept-Language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
                }
                
                # 设置Referer（优先使用参数，其次从headers中获取）
                if referer:
                    default_headers['Referer'] = referer
                elif headers and 'Referer' in headers:
                    default_headers['Referer'] = headers['Referer']
                
                # 合并自定义请求头（自定义headers会覆盖默认值）
                if headers:
                    default_headers.update(headers)
                
                async with session.get(
                    media_url,
                    headers=default_headers,
                    timeout=aiohttp.ClientTimeout(total=300),
                    proxy=proxy
                ) as response:
                    response.raise_for_status()
                    suffix = ".mp4"
                    filename = f"{media_id}_{index}{suffix}"
                    file_path = os.path.join(self.cache_dir, filename)
                    if os.path.exists(file_path):
                        return os.path.normpath(file_path)
                    content = await response.read()
                    with open(file_path, 'wb') as f:
                        f.write(content)
                    return os.path.normpath(file_path)
        except Exception:
            return None

    async def _download_large_video_to_cache(self, session: aiohttp.ClientSession, video_url: str, video_id: str, index: int = 0, headers: dict = None) -> Optional[str]:
        """
        下载大视频到缓存目录（兼容旧接口）
        Args:
            session: aiohttp会话
            video_url: 视频URL
            video_id: 视频ID
            index: 索引
            headers: 请求头
        Returns:
            Optional[str]: 字符串或None
        """
        return await self._download_large_media_to_cache(session, video_url, video_id, index, headers, is_video=True)

    def build_text_node(self, result: Dict[str, Any], sender_name: str, sender_id: Any, is_auto_pack: bool):
        """
        构建文本节点
        Args:
            result: 解析结果
            sender_name: 发送者名称
            sender_id: 发送者ID
            is_auto_pack: 是否打包为Node
        Returns:
            Any: 返回值
        """
        from astrbot.api.message_components import Plain
        text_parts = []
        if result.get('title'):
            text_parts.append(f"标题：{result['title']}")
        if result.get('author'):
            text_parts.append(f"作者：{result['author']}")
        if result.get('desc'):
            text_parts.append(f"简介：{result['desc']}")
        if result.get('timestamp'):
            text_parts.append(f"发布时间：{result['timestamp']}")
        if result.get('file_size_mb') is not None:
            file_size_mb = result.get('file_size_mb')
            text_parts.append(f"媒体大小：{file_size_mb:.2f} MB")
        if result.get('video_url'):
            text_parts.append(f"原始链接：{result['video_url']}")
        if not text_parts:
            return None
        desc_text = "\n".join(text_parts)
        return Plain(desc_text)

    def _build_gallery_nodes_from_files(self, image_files: List[str], sender_name: str, sender_id: Any, is_auto_pack: bool) -> List:
        """
        从文件路径列表构建图集节点
        Args:
            image_files: 图片文件路径列表
            sender_name: 发送者名称
            sender_id: 发送者ID
            is_auto_pack: 是否打包为Node
        Returns:
            List: 列表
        """
        from astrbot.api.message_components import Image
        if not image_files or not isinstance(image_files, list):
            return []
        images = []
        for image_path in image_files:
            if not image_path:
                continue
            image_path = os.path.normpath(image_path)
            if not os.path.exists(image_path):
                continue
            try:
                images.append(Image.fromFileSystem(image_path))
            except Exception:
                if os.path.exists(image_path):
                    try:
                        os.unlink(image_path)
                    except Exception:
                        pass
                continue
        return images

    def _build_gallery_nodes_from_urls(self, images: List[str], sender_name: str, sender_id: Any, is_auto_pack: bool) -> List:
        """
        从URL列表构建图集节点
        Args:
            images: 图片URL列表
            sender_name: 发送者名称
            sender_id: 发送者ID
            is_auto_pack: 是否打包为Node
        Returns:
            List: 列表
        """
        from astrbot.api.message_components import Image
        if not images or not isinstance(images, list):
            return []
        valid_images = [img for img in images if img and isinstance(img, str) and img.startswith(('http://', 'https://'))]
        if not valid_images:
            return []
        images_list = []
        for image_url in valid_images:
            try:
                images_list.append(Image.fromURL(image_url))
            except Exception:
                continue
        return images_list

    def _build_video_node_from_url(self, video_url: str, sender_name: str, sender_id: Any, is_auto_pack: bool, cover: Optional[str] = None) -> Optional[Any]:
        """
        从URL构建视频节点
        Args:
            video_url: 视频URL
            sender_name: 发送者名称
            sender_id: 发送者ID
            is_auto_pack: 是否打包为Node
            cover: 封面图URL
        Returns:
            Optional[Any]: 任意类型或None
        """
        from astrbot.api.message_components import Video
        if not video_url:
            return None
        try:
            if cover:
                return Video.fromURL(video_url, cover=cover)
            else:
                return Video.fromURL(video_url)
        except Exception:
            return None

    def _build_video_node_from_file(self, video_file_path: str, sender_name: str, sender_id: Any, is_auto_pack: bool) -> Optional[Any]:
        """
        从文件路径构建视频节点
        Args:
            video_file_path: 视频文件路径
            sender_name: 发送者名称
            sender_id: 发送者ID
            is_auto_pack: 是否打包为Node
        Returns:
            Optional[Any]: 任意类型或None
        """
        from astrbot.api.message_components import Video
        if not video_file_path:
            return None
        video_file_path = os.path.normpath(video_file_path)
        if not os.path.exists(video_file_path):
            return None
        try:
            return Video.fromFileSystem(video_file_path)
        except Exception:
            return None

    def _build_video_gallery_nodes_from_files(self, video_files: List[Dict[str, Any]], sender_name: str, sender_id: Any, is_auto_pack: bool) -> List:
        """
        从视频文件信息列表构建视频图集节点
        Args:
            video_files: 视频文件列表
            sender_name: 发送者名称
            sender_id: 发送者ID
            is_auto_pack: 是否打包为Node
        Returns:
            List: 列表
        """
        from astrbot.api.message_components import Video
        if not video_files or not isinstance(video_files, list):
            return []
        videos = []
        for video_file_info in video_files:
            file_path = video_file_info.get('file_path') if isinstance(video_file_info, dict) else video_file_info
            if not file_path:
                continue
            file_path = os.path.normpath(file_path)
            if not os.path.exists(file_path):
                continue
            try:
                videos.append(Video.fromFileSystem(file_path))
            except Exception:
                if os.path.exists(file_path):
                    try:
                        os.unlink(file_path)
                    except Exception:
                        pass
                continue
        return videos

    def build_media_nodes(self, result: Dict[str, Any], sender_name: str, sender_id: Any, is_auto_pack: bool) -> List:
        """
        构建媒体节点
        Args:
            result: 解析结果
            sender_name: 发送者名称
            sender_id: 发送者ID
            is_auto_pack: 是否打包为Node
        Returns:
            List: 列表
        """
        nodes = []
        # 优先处理本地文件（image_files 或 video_files）
        if result.get('is_gallery') and result.get('image_files'):
            gallery_nodes = self._build_gallery_nodes_from_files(
                result['image_files'],
                sender_name,
                sender_id,
                is_auto_pack
            )
            nodes.extend(gallery_nodes)
        elif result.get('is_gallery') and result.get('images'):
            gallery_nodes = self._build_gallery_nodes_from_urls(
                result['images'],
                sender_name,
                sender_id,
                is_auto_pack
            )
            nodes.extend(gallery_nodes)
        elif result.get('video_files'):
            video_nodes = self._build_video_gallery_nodes_from_files(
                result['video_files'],
                sender_name,
                sender_id,
                is_auto_pack
            )
            nodes.extend(video_nodes)
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

    async def _pre_download_media(self, session: aiohttp.ClientSession, media_items: List[Dict[str, Any]], headers: dict = None) -> List[Dict[str, Any]]:
        """
        预先下载所有媒体到本地
        Args:
            session: aiohttp会话
            media_items: 媒体项列表，每个项包含 {'url': str, 'media_id': str, 'index': int, 'is_video': bool, 'headers': dict}
            headers: 默认请求头
        Returns:
            List[Dict[str, Any]]: 下载结果列表，每个项包含 {'url': str, 'file_path': str, 'success': bool}
        """
        if not self.pre_download_all_media or not self.cache_dir_available:
            return []
        
        semaphore = asyncio.Semaphore(self.max_concurrent_downloads)
        
        async def download_one(item: Dict[str, Any]) -> Dict[str, Any]:
            async with semaphore:
                try:
                    url = item.get('url')
                    media_id = item.get('media_id', 'media')
                    index = item.get('index', 0)
                    is_video = item.get('is_video', True)
                    item_headers = item.get('headers') or headers
                    item_referer = item.get('referer')
                    item_default_referer = item.get('default_referer')
                    item_proxy = item.get('proxy')
                    
                    if not url:
                        return {'url': url, 'file_path': None, 'success': False, 'index': index}
                    
                    file_path = await self._download_large_media_to_cache(
                        session, url, media_id, index, item_headers, is_video, item_referer, item_default_referer, item_proxy
                    )
                    return {'url': url, 'file_path': file_path, 'success': file_path is not None, 'index': index}
                except Exception as e:
                    # 捕获所有异常，返回失败结果而不是抛出异常
                    url = item.get('url', '')
                    index = item.get('index', 0)
                    return {'url': url, 'file_path': None, 'success': False, 'index': index, 'error': str(e)}
        
        tasks = [download_one(item) for item in media_items]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 处理结果：将异常转换为失败结果，确保结果列表长度与 media_items 一致
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                # 如果是异常，创建一个失败结果
                item = media_items[i] if i < len(media_items) else {}
                processed_results.append({
                    'url': item.get('url', ''),
                    'file_path': None,
                    'success': False,
                    'index': item.get('index', i),
                    'error': str(result)
                })
            elif isinstance(result, dict):
                processed_results.append(result)
            else:
                # 未知类型，创建失败结果
                item = media_items[i] if i < len(media_items) else {}
                processed_results.append({
                    'url': item.get('url', ''),
                    'file_path': None,
                    'success': False,
                    'index': item.get('index', i),
                    'error': 'Unknown error'
                })
        
        return processed_results
