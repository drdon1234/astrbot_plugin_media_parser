# -*- coding: utf-8 -*-
"""
基础解析器抽象类
所有视频解析器都应继承此类并实现必要的方法
"""
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
import aiohttp
from astrbot.api.message_components import Node, Nodes


class BaseVideoParser(ABC):
    """视频解析器基类"""
    
    def __init__(self, name: str, max_video_size_mb: float = 0.0):
        """
        初始化解析器
        
        Args:
            name: 解析器名称（用于显示）
            max_video_size_mb: 最大允许的视频大小(MB)，0表示不限制
        """
        self.name = name
        self.max_video_size_mb = max_video_size_mb
        self.semaphore = None  # 子类可以设置信号量来控制并发
    
    @abstractmethod
    def can_parse(self, url: str) -> bool:
        """
        判断是否可以解析此URL
        
        Args:
            url: 待检测的URL
            
        Returns:
            bool: 是否可以解析
        """
        pass
    
    @abstractmethod
    def extract_links(self, text: str) -> List[str]:
        """
        从文本中提取该解析器可以处理的链接
        
        Args:
            text: 输入文本
            
        Returns:
            List[str]: 提取到的链接列表
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
            Optional[Dict[str, Any]]: 解析结果，包含：
                - video_url: 原始视频页面URL
                - direct_url: 视频直链（如果有）
                - title: 视频标题
                - author: 作者信息
                - desc: 视频描述（可选）
                - thumb_url: 封面图URL（可选）
                - images: 图片列表（如果是图片集，可选）
                - is_gallery: 是否为图片集（可选）
            如果解析失败，返回None
        """
        pass
    
    async def get_video_size(self, video_url: str, session: aiohttp.ClientSession) -> Optional[float]:
        """
        获取视频文件大小(MB)
        
        Args:
            video_url: 视频URL
            session: aiohttp会话
            
        Returns:
            Optional[float]: 视频大小(MB)，如果无法获取则返回None
        """
        try:
            async with session.head(video_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                content_length = resp.headers.get("Content-Length")
                if content_length:
                    size_bytes = int(content_length)
                    size_mb = size_bytes / (1024 * 1024)
                    return size_mb
        except Exception:
            pass
        return None
    
    async def check_video_size(self, video_url: str, session: aiohttp.ClientSession) -> bool:
        """
        检查视频大小是否在允许范围内
        
        Args:
            video_url: 视频URL
            session: aiohttp会话
            
        Returns:
            bool: 如果视频大小在允许范围内或无法获取大小，返回True；否则返回False
        """
        if self.max_video_size_mb <= 0:
            return True
        video_size = await self.get_video_size(video_url, session)
        if video_size is None:
            return True  # 无法获取大小时，允许通过
        return video_size <= self.max_video_size_mb
    
    def build_text_node(self, result: Dict[str, Any], sender_name: str, sender_id: Any, is_auto_pack: bool):
        """
        构建文本节点（标题、作者等信息）
        
        Args:
            result: 解析结果
            sender_name: 发送者名称
            sender_id: 发送者ID
            is_auto_pack: 是否打包为Node
            
        Returns:
            Node或Plain: 文本节点
        """
        from astrbot.api.message_components import Plain
        
        # 构建文本内容
        text_parts = []
        if result.get('title'):
            text_parts.append(f"标题：{result['title']}")
        if result.get('author'):
            text_parts.append(f"作者：{result['author']}")
        if result.get('desc'):
            text_parts.append(f"简介：{result['desc']}")
        if result.get('timestamp'):
            text_parts.append(f"发布时间：{result['timestamp']}")
        
        desc_text = "\n".join(text_parts)
        
        if is_auto_pack:
            return Node(
                name=sender_name,
                uin=sender_id,
                content=[Plain(desc_text)]
            )
        else:
            return Plain(desc_text)
    
    def build_media_nodes(self, result: Dict[str, Any], sender_name: str, sender_id: Any, is_auto_pack: bool) -> List:
        """
        构建媒体节点（视频或图片）
        
        Args:
            result: 解析结果
            sender_name: 发送者名称
            sender_id: 发送者ID
            is_auto_pack: 是否打包为Node
            
        Returns:
            List: 媒体节点列表
        """
        from astrbot.api.message_components import Video, Image
        
        nodes = []
        
        # 处理图片集
        if result.get('is_gallery') and result.get('images'):
            if is_auto_pack:
                gallery_node_content = []
                for image_url in result['images']:
                    image_node = Node(
                        name=sender_name,
                        uin=sender_id,
                        content=[Image.fromURL(image_url)]
                    )
                    gallery_node_content.append(image_node)
                parent_gallery_node = Node(
                    name=sender_name,
                    uin=sender_id,
                    content=gallery_node_content
                )
                nodes.append(parent_gallery_node)
            else:
                for image_url in result['images']:
                    nodes.append(Image.fromURL(image_url))
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

