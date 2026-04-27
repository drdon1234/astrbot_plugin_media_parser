"""解析管理器，维护解析器列表并按链接匹配。"""
import asyncio
from typing import List, Dict, Any, Optional, Tuple

import aiohttp

from ..logger import logger

from .platform.base import BaseVideoParser
from .router import LinkRouter
from .utils import SkipParse


class ParserManager:

    """解析器管理器，按链接选择并调用具体平台解析器。"""
    def __init__(self, parsers: List[BaseVideoParser]):
        """初始化解析器管理器

        Args:
            parsers: 解析器列表

        Raises:
            ValueError: parsers参数为空时
        """
        if not parsers:
            raise ValueError("parsers 参数不能为空")
        self.parsers = parsers
        self.link_router = LinkRouter(parsers)

    @staticmethod
    def _resolve_platform_name(
        parser: BaseVideoParser,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """按解析结果归一平台名。"""
        explicit = (metadata or {}).get("platform")
        return explicit or parser.name

    def _normalize_metadata(
        self,
        url: str,
        parser: BaseVideoParser,
        metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """补齐解析结果的统一字段。"""
        platform = self._resolve_platform_name(parser, metadata)
        metadata["platform"] = platform
        metadata.setdefault("parser_name", parser.name)
        metadata.setdefault("source_url", url)
        metadata.setdefault("video_urls", [])
        metadata.setdefault("image_urls", [])
        metadata.setdefault("image_headers", {})
        metadata.setdefault("video_headers", {})
        return metadata

    def find_parser(self, url: str) -> Optional[BaseVideoParser]:
        """根据URL查找合适的解析器

        Args:
            url: 视频链接

        Returns:
            匹配的解析器实例，未找到时为None
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

    async def parse_text(
        self,
        text: str,
        session: aiohttp.ClientSession,
        links_with_parser: Optional[List[Tuple[str, BaseVideoParser]]] = None
    ) -> List[Dict[str, Any]]:
        """解析文本中的所有链接

        Args:
            text: 输入文本
            session: aiohttp会话
            links_with_parser: 预先提取好的链接与解析器列表（可选）

        Returns:
            解析结果字典列表（元数据列表）
        """
        if links_with_parser is None:
            links_with_parser = self.extract_all_links(text)
        if not links_with_parser:
            logger.debug("未提取到任何可解析链接")
            return []
        unique_links = {link: parser for link, parser in links_with_parser}
        logger.debug(f"需要解析 {len(unique_links)} 个链接")
        tasks = [
            parser.parse(session, url)
            for url, parser in unique_links.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        metadata_list = []
        link_items = list(unique_links.items())
        for i, result in enumerate(results):
            url, parser = link_items[i]
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, Exception):
                if isinstance(result, SkipParse):
                    logger.debug(f"跳过解析: {url}, 原因: {result}")
                    continue
                logger.error(f"解析URL失败: {url}, 错误: {result}")
                platform = self._resolve_platform_name(parser)
                metadata_list.append({
                    'url': url,
                    'source_url': url,
                    'error': str(result),
                    'video_urls': [],
                    'image_urls': [],
                    'image_headers': {},
                    'video_headers': {},
                    'platform': platform,
                    'parser_name': parser.name,
                    'has_valid_media': False
                })
            elif isinstance(result, BaseException):
                raise result
            elif result:
                metadata_list.append(
                    self._normalize_metadata(url, parser, result)
                )
        logger.debug(f"解析完成，获得 {len(metadata_list)} 条元数据")
        return metadata_list

