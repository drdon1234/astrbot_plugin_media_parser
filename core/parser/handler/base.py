# -*- coding: utf-8 -*-
"""
基础解析器抽象类
只负责将url解析为元数据表
"""
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List

import aiohttp

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


class BaseVideoParser(ABC):
    """视频解析器基类，只负责解析URL返回元数据"""

    def __init__(self, name: str):
        """初始化视频解析器基类

        Args:
            name: 解析器名称
        """
        self.name = name
        self.logger = logger

    @abstractmethod
    def can_parse(self, url: str) -> bool:
        """判断是否可以解析此URL

        Args:
            url: 视频链接

        Returns:
            如果可以解析返回True，否则返回False
        """
        pass

    @abstractmethod
    def extract_links(self, text: str) -> List[str]:
        """从文本中提取链接

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
        """解析单个视频链接

        Args:
            session: aiohttp会话
            url: 视频链接

        Returns:
            解析结果字典，包含以下字段：
            - url: 原始url（必需）
            - title: 标题（可选）
            - author: 作者（可选）
            - desc: 简介（可选）
            - timestamp: 发布时间（可选）
            - video_urls: 视频URL列表，每个元素是单个媒体的可用URL列表（List[List[str]]），即使只有一条直链也要是列表的列表（必需，可为空列表）
            - image_urls: 图片URL列表，每个元素是单个媒体的可用URL列表（List[List[str]]），即使只有一条直链也要是列表的列表（必需，可为空列表）
            - image_headers: dict，图片下载的完整请求头字典（必需）
            - video_headers: dict，视频下载的完整请求头字典（必需）
            - image_pre_download: bool，是否必须预下载图片（可选，默认False）。True=必须预下载，如果未启用预下载或预下载失败则跳过；False=根据配置选择
            - video_pre_download: bool，是否必须预下载视频（可选，默认False）。True=必须预下载，如果未启用预下载或预下载失败则跳过；False=根据配置选择
            - 其他平台特定字段

        Raises:
            解析失败时直接raise异常，不记录日志
        """
        pass

