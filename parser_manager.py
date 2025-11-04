# -*- coding: utf-8 -*-
"""
解析器管理器
统一管理和调度所有视频解析器
"""
from typing import List, Dict, Any, Optional, Tuple
import asyncio
import aiohttp
from .base_parser import BaseVideoParser
from .parsers import BilibiliParser, DouyinParser


class ParserManager:
    """解析器管理器"""
    
    def __init__(self, parsers: List[BaseVideoParser] = None):
        """
        初始化解析器管理器
        
        Args:
            parsers: 解析器列表，如果为None则使用默认的解析器
        """
        if parsers is None:
            # 使用默认解析器
            self.parsers: List[BaseVideoParser] = [
                BilibiliParser(),
                DouyinParser()
            ]
        else:
            self.parsers = parsers
    
    def register_parser(self, parser: BaseVideoParser):
        """
        注册新的解析器
        
        Args:
            parser: 继承自BaseVideoParser的解析器实例
        """
        if parser not in self.parsers:
            self.parsers.append(parser)
    
    def find_parser(self, url: str) -> Optional[BaseVideoParser]:
        """
        根据URL查找合适的解析器
        
        Args:
            url: 视频链接
            
        Returns:
            能解析该URL的解析器，如果找不到则返回None
        """
        for parser in self.parsers:
            if parser.can_parse(url):
                return parser
        return None
    
    def extract_all_links(self, text: str) -> List[Tuple[str, BaseVideoParser]]:
        """
        从文本中提取所有可解析的链接，并返回链接和对应的解析器
        
        Args:
            text: 输入文本
            
        Returns:
            List[Tuple[str, BaseVideoParser]]: (链接, 解析器) 的列表
        """
        links_with_parser = []
        for parser in self.parsers:
            links = parser.extract_links(text)
            for link in links:
                links_with_parser.append((link, parser))
        return links_with_parser
    
    def _deduplicate_links(self, links_with_parser: List[Tuple[str, BaseVideoParser]]) -> Dict[str, BaseVideoParser]:
        """
        对链接进行去重，保留第一个出现的链接
        
        Args:
            links_with_parser: (链接, 解析器) 的列表
            
        Returns:
            Dict[str, BaseVideoParser]: 去重后的链接字典
        """
        unique_links = {}
        for link, parser in links_with_parser:
            if link not in unique_links:
                unique_links[link] = parser
        return unique_links
    
    async def parse_url(self, url: str, session: aiohttp.ClientSession) -> Optional[Dict[str, Any]]:
        """
        解析单个URL
        
        Args:
            url: 视频链接
            session: aiohttp会话
            
        Returns:
            解析结果，如果无法解析则返回None
        """
        parser = self.find_parser(url)
        if parser is None:
            return None
        return await parser.parse(session, url)
    
    async def parse_text(self, text: str, session: aiohttp.ClientSession) -> List[Dict[str, Any]]:
        """
        解析文本中的所有链接
        
        Args:
            text: 输入文本
            session: aiohttp会话
            
        Returns:
            解析结果列表
        """
        links_with_parser = self.extract_all_links(text)
        if not links_with_parser:
            return []
        
        # 去重
        unique_links = self._deduplicate_links(links_with_parser)
        
        # 并发解析
        tasks = [parser.parse(session, url) for url, parser in unique_links.items()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 过滤异常和None
        return [result for result in results if result and not isinstance(result, Exception)]
    
    async def build_nodes(self, event, is_auto_pack: bool) -> Optional[List]:
        """
        构建消息节点
        
        Args:
            event: AstrMessageEvent事件对象
            is_auto_pack: 是否打包为Node
            
        Returns:
            节点列表，如果没有可解析的链接则返回None
        """
        try:
            input_text = event.message_str
            links_with_parser = self.extract_all_links(input_text)
            if not links_with_parser:
                return None
            
            # 去重
            unique_links = self._deduplicate_links(links_with_parser)
            
            nodes = []
            sender_name = "视频解析bot"
            platform = event.get_platform_name()
            sender_id = event.get_self_id()
            if platform not in ("wechatpadpro", "webchat", "gewechat"):
                try:
                    sender_id = int(sender_id)
                except (ValueError, TypeError):
                    sender_id = 10000
            
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                tasks = [parser.parse(session, url) for url, parser in unique_links.items()]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                # 使用第一个解析器实例来构建节点（所有解析器都继承自BaseVideoParser，方法相同）
                parser_instance = next(iter(unique_links.values())) if unique_links else None
                
                for result in results:
                    if result and not isinstance(result, Exception) and parser_instance:
                        # 使用基类方法构建文本节点
                        text_node = parser_instance.build_text_node(result, sender_name, sender_id, is_auto_pack)
                        if text_node:
                            nodes.append(text_node)
                        
                        # 使用基类方法构建媒体节点
                        media_nodes = parser_instance.build_media_nodes(result, sender_name, sender_id, is_auto_pack)
                        nodes.extend(media_nodes)
            
            if not nodes:
                return None
            return nodes
        except Exception:
            return None

