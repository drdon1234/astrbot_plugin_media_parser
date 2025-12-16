# -*- coding: utf-8 -*-
from typing import Optional, Dict, Any, List

import aiohttp

from .base import BaseVideoParser


class ExampleParser(BaseVideoParser):
    """示例解析器

    这个类展示了如何实现一个新的视频解析器。
    可以复制此文件并修改以实现新的解析器。
    """

    def __init__(self):
        """初始化示例解析器"""
        super().__init__("示例平台")

    def can_parse(self, url: str) -> bool:
        """判断是否可以解析此URL

        在此方法中实现URL识别逻辑，例如：检查URL是否包含特定域名。

        Args:
            url: 视频链接

        Returns:
            如果可以解析返回True，否则返回False
        """
        if not url:
            return False
        return False

    def extract_links(self, text: str) -> List[str]:
        """从文本中提取该解析器可以处理的链接

        在此方法中实现链接提取逻辑，可以使用正则表达式匹配链接模式。

        Args:
            text: 输入文本

        Returns:
            提取到的链接列表
        """
        result_links = []
        return result_links

    async def parse(
        self,
        session: aiohttp.ClientSession,
        url: str
    ) -> Optional[Dict[str, Any]]:
        """解析单个视频链接

        在此方法中实现具体的解析逻辑：
        1. 获取视频信息（标题、作者、描述等）
        2. 获取视频直链
        3. 返回统一格式的字典

        返回的字典应包含以下字段（根据实际情况选择）：
        - url: 原始url（必需）
        - title: 标题（可选）
        - author: 作者（可选）
        - desc: 简介（可选）
        - timestamp: 发布时间（可选）
        - video_urls: 视频URL列表，每个元素是单个媒体的可用URL列表（List[List[str]]），即使只有一条直链也要是列表的列表（必需，可为空列表）
        - image_urls: 图片URL列表，每个元素是单个媒体的可用URL列表（List[List[str]]），即使只有一条直链也要是列表的列表（必需，可为空列表）
        - 其他平台特定字段

        Args:
            session: aiohttp会话
            url: 视频链接

        Returns:
            解析结果字典，如果解析失败返回None

        Raises:
            RuntimeError: 当解析失败时
        """
        try:
            return None
        except Exception as e:
            raise RuntimeError(str(e))
