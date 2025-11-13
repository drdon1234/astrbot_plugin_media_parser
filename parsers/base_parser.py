# -*- coding: utf-8 -*-
"""
基础解析器抽象类
只负责将url解析为元数据表
"""
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List

import aiohttp


class BaseVideoParser(ABC):
    """视频解析器基类，只负责解析URL返回元数据。"""

    def __init__(self, name: str):
        """初始化视频解析器基类。

        Args:
            name: 解析器名称
        """
        self.name = name

    @abstractmethod
    def can_parse(self, url: str) -> bool:
        """判断是否可以解析此URL。

        Args:
            url: 视频链接

        Returns:
            如果可以解析返回True，否则返回False
        """
        pass

    @abstractmethod
    def extract_links(self, text: str) -> List[str]:
        """从文本中提取链接。

        Args:
            text: 输入文本

        Returns:
            提取到的链接列表
        """
        pass

    @abstractmethod
    async def parse(
        self,
        session: aiohttp.ClientSession,
        url: str
    ) -> Optional[Dict[str, Any]]:
        """解析单个视频链接。

        Args:
            session: aiohttp会话
            url: 视频链接

        Returns:
            解析结果字典，包含以下字段：
            - url: 原始url（必需）
            - media_type: 媒体类型: "video", "image", "gallery"（必需）
            - title: 标题（可选）
            - author: 作者（可选）
            - desc: 简介（可选）
            - timestamp: 发布时间（可选）
            - media_urls: 媒体直链列表（必需）
            - 其他平台特定字段（如thumb_url, image_url_lists等）

        Raises:
            解析失败时直接raise异常，不记录日志
        """
        pass
