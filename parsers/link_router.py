# -*- coding: utf-8 -*-
"""
链接清洗分流器
用于从文本中匹配可解析的链接并确定链接该传入什么解析器
"""
from typing import List, Tuple

from .base_parser import BaseVideoParser


class LinkRouter:
    """链接清洗分流器，负责从文本中提取链接并匹配解析器"""

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
            return []

        links_with_position = []
        for parser in self.parsers:
            links = parser.extract_links(text)
            for link in links:
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
        for parser in self.parsers:
            if parser.can_parse(url):
                return parser
        raise ValueError(f"找不到可以解析该URL的解析器: {url}")

