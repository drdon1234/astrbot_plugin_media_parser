"""平台解析器抽象基类，定义统一接口与结果规范。"""
from abc import ABC, abstractmethod
from typing import Optional, List

import aiohttp

from ...logger import logger
from ...types import MediaMetadata


class BaseVideoParser(ABC):

    """平台解析器抽象基类，定义统一解析接口和结果结构。"""
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
            是否可以解析
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
    ) -> Optional[MediaMetadata]:
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
            - video_force_download: bool，是否强制下载到缓存目录（可选，默认False）。True=缓存目录不可用或下载失败时跳过该视频；False=由下载决策引擎按目录能力选择 local/direct
            - video_force_downloads: List[bool]，逐视频强制写入缓存标记（可选）
            - platform: 平台名
            - 其他平台特定字段

        Raises:
            解析失败时直接raise异常，不记录日志
        """
        pass

    def _add_range_prefix_to_video_urls(self, video_urls: List[List[str]]) -> List[List[str]]:
        """为视频URL列表添加 range: 前缀
        
        Args:
            video_urls: 视频URL列表（二维列表）
            
        Returns:
            添加了 range: 前缀的视频URL列表
        """
        if not video_urls:
            return video_urls
        
        result = []
        for url_list in video_urls:
            if url_list and isinstance(url_list, list):
                prefixed_list = []
                for url in url_list:
                    if not url:
                        prefixed_list.append(url)
                        continue

                    if url.startswith('dash:'):
                        payload = url[5:]
                        parts = payload.split('||', 1)
                        prefixed_parts = []
                        for part in parts:
                            if part and not (
                                part.startswith('range:') or
                                part.startswith('m3u8:') or
                                part.startswith('dash:')
                            ):
                                prefixed_parts.append(f'range:{part}')
                            else:
                                prefixed_parts.append(part)
                        prefixed_list.append(f"dash:{'||'.join(prefixed_parts)}")
                    elif url.startswith('range:') or url.startswith('m3u8:'):
                        prefixed_list.append(url)
                    else:
                        prefixed_list.append(f'range:{url}')
                result.append(prefixed_list)
            else:
                result.append(url_list)
        
        return result

