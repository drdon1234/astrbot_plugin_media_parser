# -*- coding: utf-8 -*-
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.message_components import Nodes
from astrbot.core.star.filter.event_message_type import EventMessageType
from .parser_manager import ParserManager
from .parsers import BilibiliParser, DouyinParser
import re


@register("astrbot_plugin_video_parser", "drdon1234", "统一视频链接解析插件，支持B站、抖音等平台", "1.0.0")
class VideoParserPlugin(Star):
    """统一视频解析插件"""
    
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.is_auto_parse = config.get("is_auto_parse", True)
        self.is_auto_pack = config.get("is_auto_pack", True)
        max_video_size_mb = config.get("max_video_size_mb", 0.0)
        
        # 初始化解析器
        parsers = []
        # B站解析器
        if config.get("enable_bilibili", True):
            parsers.append(BilibiliParser(max_video_size_mb=max_video_size_mb))
        # 抖音解析器
        if config.get("enable_douyin", True):
            parsers.append(DouyinParser(max_video_size_mb=max_video_size_mb))
        
        # 创建解析器管理器
        self.parser_manager = ParserManager(parsers)
        
        # 触发关键词（用于手动触发解析）
        self.trigger_keywords = config.get("trigger_keywords", ["视频解析", "解析视频"])

    async def terminate(self):
        """插件终止时的清理工作"""
        pass

    def _should_parse(self, message_str: str) -> bool:
        """
        判断是否应该解析消息
        
        Args:
            message_str: 消息文本
            
        Returns:
            bool: 是否应该解析
        """
        # 如果启用了自动解析
        if self.is_auto_parse:
            return True
        
        # 检查是否包含触发关键词
        for keyword in self.trigger_keywords:
            if keyword in message_str:
                return True
        
        # 检查是否包含特定平台的触发词
        if bool(re.search(r'.?B站解析|b站解析|bilibili解析', message_str)):
            return True
        if bool(re.search(r'.?抖音解析', message_str)):
            return True
        
        return False

    @filter.event_message_type(EventMessageType.ALL)
    async def auto_parse(self, event: AstrMessageEvent):
        """自动解析消息中的视频链接"""
        if not self._should_parse(event.message_str):
            return
        
        nodes = await self.parser_manager.build_nodes(event, self.is_auto_pack)
        if nodes is None:
            return
        
        await event.send(event.plain_result("视频解析bot为您服务 ٩( 'ω' )و"))
        if self.is_auto_pack:
            await event.send(event.chain_result([Nodes(nodes)]))
        else:
            for node in nodes:
                await event.send(event.chain_result([node]))

