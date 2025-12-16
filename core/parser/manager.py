# -*- coding: utf-8 -*-
"""
解析器管理器
负责管理和调度解析器
"""
import asyncio
from typing import List, Dict, Any, Optional, Tuple

import aiohttp

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from .handler.base import BaseVideoParser
from .router import LinkRouter


class ParserManager:
    """解析器管理器，负责管理和调度解析器"""

    def __init__(self, parsers: List[BaseVideoParser]):
        """初始化解析器管理器

        Args:
            parsers: 解析器列表

        Raises:
            ValueError: 当parsers参数为空时
        """
        if not parsers:
            raise ValueError("parsers 参数不能为空")
        self.parsers = parsers
        self.logger = logger
        self.link_router = LinkRouter(parsers)

    def register_parser(self, parser: BaseVideoParser):
        """注册新的解析器

        Args:
            parser: 解析器实例
        """
        if parser not in self.parsers:
            self.parsers.append(parser)
            self.link_router = LinkRouter(self.parsers)

    def find_parser(self, url: str) -> Optional[BaseVideoParser]:
        """根据URL查找合适的解析器

        Args:
            url: 视频链接

        Returns:
            匹配的解析器实例，如果未找到返回None
        """
        try:
            return self.link_router.find_parser(url)
        except ValueError:
            return None

    def extract_all_links(
        self,
        text: str
    ) -> List[Tuple[str, BaseVideoParser]]:
        """从文本中提取所有可解析的链接

        Args:
            text: 输入文本

        Returns:
            包含(链接, 解析器)元组的列表，按在文本中出现的位置排序
        """
        return self.link_router.extract_links_with_parser(text)

    def _deduplicate_links(
        self,
        links_with_parser: List[Tuple[str, BaseVideoParser]]
    ) -> Dict[str, BaseVideoParser]:
        """对链接进行去重

        Args:
            links_with_parser: 链接和解析器的列表

        Returns:
            去重后的链接和解析器字典
        """
        unique_links = {}
        for link, parser in links_with_parser:
            if link not in unique_links:
                unique_links[link] = parser
        return unique_links

    async def parse_url(
        self,
        url: str,
        session: aiohttp.ClientSession
    ) -> Optional[Dict[str, Any]]:
        """解析单个URL

        Args:
            url: 视频链接
            session: aiohttp会话

        Returns:
            解析结果字典（元数据），如果无法解析返回None
        """
        parser = self.find_parser(url)
        if parser is None:
            self.logger.debug(f"未找到匹配的解析器: {url}")
            return None
        self.logger.debug(f"使用解析器 {parser.name} 解析URL: {url}")
        try:
            result = await parser.parse(session, url)
            if result:
                if 'platform' not in result:
                    result['platform'] = parser.name
                self.logger.debug(
                    f"解析成功: {url}, "
                    f"视频: {len(result.get('video_urls', []))}, "
                    f"图片: {len(result.get('image_urls', []))}"
                )
            return result
        except Exception as e:
            self.logger.exception(f"解析URL失败: {url}, 错误: {e}")
            return None

    async def parse_text(
        self,
        text: str,
        session: aiohttp.ClientSession
    ) -> List[Dict[str, Any]]:
        """解析文本中的所有链接

        Args:
            text: 输入文本
            session: aiohttp会话

        Returns:
            解析结果字典列表（元数据列表）
        """
        links_with_parser = self.extract_all_links(text)
        if not links_with_parser:
            self.logger.debug("未提取到任何可解析链接")
            return []
        unique_links = self._deduplicate_links(links_with_parser)
        self.logger.debug(f"去重后需要解析 {len(unique_links)} 个链接")
        tasks = [
            parser.parse(session, url)
            for url, parser in unique_links.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        metadata_list = []
        for i, result in enumerate(results):
            url = list(unique_links.keys())[i]
            parser = unique_links[url]
            if isinstance(result, Exception):
                self.logger.exception(f"解析URL失败: {url}, 错误: {result}")
                metadata_list.append({
                    'url': url,
                    'error': str(result),
                    'video_urls': [],
                    'image_urls': [],
                    'image_headers': {},
                    'video_headers': {},
                    'platform': parser.name,
                    'has_valid_media': False
                })
            elif result:
                if 'platform' not in result:
                    result['platform'] = parser.name
                metadata_list.append(result)
        self.logger.debug(f"解析完成，获得 {len(metadata_list)} 条元数据")
        return metadata_list

