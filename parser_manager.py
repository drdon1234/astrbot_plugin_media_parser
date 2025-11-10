# -*- coding: utf-8 -*-
from typing import List, Dict, Any, Optional, Tuple
import asyncio
import aiohttp
import os
from .parsers.base_parser import BaseVideoParser


class ParserManager:

    def __init__(self, parsers: List[BaseVideoParser]):
        """
        初始化插件
        Args:
            parsers: 解析器列表
        """
        if not parsers:
            raise ValueError("parsers 参数不能为空")
        self.parsers = parsers

    def register_parser(self, parser: BaseVideoParser):
        """
        注册新的解析器
        Args:
            parser: 解析器实例
        Returns:
            Any: 返回值
        """
        if parser not in self.parsers:
            self.parsers.append(parser)

    def find_parser(self, url: str) -> Optional[BaseVideoParser]:
        """
        根据URL查找合适的解析器
        Args:
            url: 视频链接
        Returns:
            Optional[BaseVideoParser]: 解析器或None
        """
        for parser in self.parsers:
            if parser.can_parse(url):
                return parser
        return None

    def extract_all_links(self, text: str) -> List[Tuple[str, BaseVideoParser]]:
        """
        从文本中提取所有可解析的链接
        Args:
            text: 输入文本
        Returns:
            List: 列表
        """
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

    def _deduplicate_links(self, links_with_parser: List[Tuple[str, BaseVideoParser]]) -> Dict[str, BaseVideoParser]:
        """
        对链接进行去重
        Args:
            links_with_parser: 链接和解析器的列表
        Returns:
            Dict[str, BaseVideoParser]: 链接和解析器的字典
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
            Optional: 返回值
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
            List: 列表
        """
        links_with_parser = self.extract_all_links(text)
        if not links_with_parser:
            return []
        unique_links = self._deduplicate_links(links_with_parser)
        tasks = [parser.parse(session, url) for url, parser in unique_links.items()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [result for result in results if result and not isinstance(result, Exception)]

    async def build_nodes(self, event, is_auto_pack: bool) -> Optional[tuple]:
        """
        构建消息节点
        Args:
            event: 消息事件对象
            is_auto_pack: 是否打包为Node
        Returns:
            Optional[tuple]: 元组或None
        """
        try:
            input_text = event.message_str
            links_with_parser = self.extract_all_links(input_text)
            if not links_with_parser:
                return None
            unique_links = self._deduplicate_links(links_with_parser)
            all_link_nodes = []
            link_metadata = []
            normal_link_count = 0
            temp_files = []
            video_files = []
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
                url_parser_pairs = list(unique_links.items())
                tasks = [parser.parse(session, url) for url, parser in url_parser_pairs]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for (url, parser_instance), result in zip(url_parser_pairs, results):
                    if isinstance(result, Exception) or not result:
                        from astrbot.api.message_components import Plain
                        if isinstance(result, Exception):
                            error_msg = str(result)
                            if error_msg.startswith("解析失败："):
                                failure_reason = error_msg.replace("解析失败：", "", 1)
                            else:
                                failure_reason = error_msg
                            if "本地缓存路径无效" in failure_reason or "cache_dir" in failure_reason.lower():
                                failure_reason = "本地缓存路径无效"
                        else:
                            failure_reason = "未知错误"
                        failure_text = f"解析失败：{failure_reason}\n原始链接：{url}"
                        link_nodes = [Plain(failure_text)]
                        all_link_nodes.append(link_nodes)
                        link_metadata.append({
                            'link_nodes': link_nodes,
                            'is_large_video': False,
                            'is_normal': True
                        })
                        normal_link_count += 1
                        continue
                    link_has_large_video = result.get('force_separate_send', False) or result.get('has_large_video', False)
                    if result.get('image_files'):
                        temp_files.extend(result['image_files'])
                    if result.get('video_files'):
                        for video_file_info in result['video_files']:
                            file_path = video_file_info.get('file_path')
                            if file_path:
                                video_files.append(file_path)
                    link_nodes = []
                    text_node = parser_instance.build_text_node(result, sender_name, sender_id, False)
                    if text_node:
                        link_nodes.append(text_node)
                    media_nodes = parser_instance.build_media_nodes(result, sender_name, sender_id, False)
                    link_nodes.extend(media_nodes)
                    link_video_files = []
                    if result.get('video_files'):
                        for video_file_info in result['video_files']:
                            file_path = video_file_info.get('file_path')
                            if file_path:
                                link_video_files.append(file_path)
                    all_link_nodes.append(link_nodes)
                    link_metadata.append({
                        'link_nodes': link_nodes,
                        'is_large_video': link_has_large_video,
                        'is_normal': not link_has_large_video,
                        'video_files': link_video_files
                    })
                    if not link_has_large_video:
                        normal_link_count += 1
            if not all_link_nodes:
                # 如果没有节点，清理已收集的文件
                self._cleanup_files_list(temp_files + video_files)
                return None
            return (all_link_nodes, link_metadata, temp_files, video_files, normal_link_count)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError):
            # 发生异常，清理已收集的文件
            self._cleanup_files_list(temp_files + video_files)
            return None
        except Exception:
            # 发生异常，清理已收集的文件
            self._cleanup_files_list(temp_files + video_files)
            return None
    
    def _cleanup_files_list(self, file_paths: list):
        """
        清理文件列表
        Args:
            file_paths: 文件路径列表
        """
        for file_path in file_paths:
            if file_path and os.path.exists(file_path):
                try:
                    os.unlink(file_path)
                except Exception:
                    pass
