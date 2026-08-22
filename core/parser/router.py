"""链接路由器，负责文本提链与解析器选择。"""

from typing import List, Tuple

from ..logger import logger

from .platform.base import BaseVideoParser
from .utils import is_live_url


class LinkRouter:
    """链接路由器，负责抽取文本链接并定位可用解析器。"""

    def __init__(self, parsers: List[BaseVideoParser]):
        """初始化链接清洗分流器；允许空列表作为停用状态。"""
        self.parsers = list(parsers or [])

    def extract_links_with_parser(self, text: str) -> List[Tuple[str, BaseVideoParser]]:
        """从文本中提取所有可解析的链接，并匹配对应的解析器

        Args:
            text: 输入文本

        Returns:
            包含(链接, 解析器)元组的列表。原文可定位项按出现位置排序，
            解析器规范化后无法定位的链接按提取顺序保留在其后。
        """
        links_with_position = []
        normalized_link_order = 0
        for parser in self.parsers:
            try:
                links = parser.extract_links(text)
            except Exception:
                logger.exception(f"解析器 {parser.name} 提取链接失败，已跳过")
                continue
            if links is None:
                continue
            if not isinstance(links, list):
                logger.error(
                    f"解析器 {parser.name} 的 extract_links 返回了无效类型 "
                    f"{type(links).__name__}，已跳过"
                )
                continue
            if links:
                logger.debug(f"解析器 {parser.name} 提取到 {len(links)} 个链接")
            for link in links:
                if not isinstance(link, str) or not link.strip():
                    logger.warning(f"解析器 {parser.name} 返回了无效链接项，已跳过")
                    continue
                link = link.strip()
                if is_live_url(link):
                    logger.debug(f"提取到直播域名链接，跳过: {link}")
                    continue
                position = text.find(link)
                if position == -1:
                    # 平台解析器可能把移动端或分享链接规范化为标准链接。
                    # 这类链接无法再从原文精确定位，但不能因此丢失。
                    position = len(text) + normalized_link_order
                    normalized_link_order += 1
                links_with_position.append((position, link, parser))

        links_with_position.sort(key=lambda x: x[0])

        seen_links = set()
        links_with_parser = []
        for position, link, parser in links_with_position:
            if link not in seen_links:
                seen_links.add(link)
                links_with_parser.append((link, parser))

        if links_with_parser:
            logger.debug(
                f"链接提取完成，共 {len(links_with_parser)} 个唯一链接: {[link for link, _ in links_with_parser]}"
            )
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
            try:
                can_parse = parser.can_parse(url)
            except Exception:
                logger.exception(f"解析器 {parser.name} 判断链接支持状态失败，已跳过")
                continue
            if can_parse:
                logger.debug(f"找到匹配的解析器: {parser.name} for {url}")
                return parser
        logger.debug(f"未找到可以解析该URL的解析器: {url}")
        raise ValueError(f"找不到可以解析该URL的解析器: {url}")
