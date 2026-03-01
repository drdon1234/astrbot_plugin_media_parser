from typing import List, Tuple

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from .platform.base import BaseVideoParser
from .utils import is_live_url


class LinkRouter:

    def __init__(self, parsers: List[BaseVideoParser]):
        """初始化链接清洗分流器

        Args:
            parsers: 解析器列表

        Raises:
            ValueError: 当parsers参数为空时
        """
        if not parsers:
            raise ValueError("parsers 参数不能为空")
        self.parsers = parsers

    def extract_links_with_parser(
        self,
        text: str
    ) -> List[Tuple[str, BaseVideoParser]]:
        """从文本中提取所有可解析的链接，并匹配对应的解析器

        Args:
            text: 输入文本

        Returns:
            包含(链接, 解析器)元组的列表，按在文本中出现的位置排序
        """
        if "原始链接：" in text:
            logger.debug("检测到'原始链接：'标记，跳过链接提取")
            return []

        links_with_position = []
        for parser in self.parsers:
            links = parser.extract_links(text)
            if links:
                logger.debug(f"解析器 {parser.name} 提取到 {len(links)} 个链接")
            for link in links:
                if is_live_url(link):
                    logger.debug(f"提取到直播域名链接，跳过: {link}")
                    continue
                position = text.find(link)
                if position != -1:
                    links_with_position.append((position, link, parser))
        
        links_with_position.sort(key=lambda x: x[0])
        
        seen_links = set()
        links_with_parser = []
        for position, link, parser in links_with_position:
            if link not in seen_links:
                seen_links.add(link)
                links_with_parser.append((link, parser))
        
        if links_with_parser:
            logger.debug(f"链接提取完成，共 {len(links_with_parser)} 个唯一链接: {[link for link, _ in links_with_parser]}")
        else:
            logger.debug("未提取到任何可解析链接")
        
        return links_with_parser

    def find_parser(self, url: str) -> BaseVideoParser:
        """根据URL查找合适的解析器

        Args:
            url: 视频链接

        Returns:
            匹配的解析器实例

        Raises:
            ValueError: 当找不到匹配的解析器时
        """
        logger.debug(f"查找URL的解析器: {url}")
        if is_live_url(url):
            logger.debug(f"检测到直播域名链接，跳过解析: {url}")
            raise ValueError(f"直播域名链接不解析: {url}")
        for parser in self.parsers:
            if parser.can_parse(url):
                logger.debug(f"找到匹配的解析器: {parser.name} for {url}")
                return parser
        logger.debug(f"未找到可以解析该URL的解析器: {url}")
        raise ValueError(f"找不到可以解析该URL的解析器: {url}")

