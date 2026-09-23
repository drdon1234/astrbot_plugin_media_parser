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
            - url: 单一规范链接（可选，缺省时由 ParserManager 使用输入链接补齐）
            - title: 标题（可选）
            - author: 作者（可选）
            - desc: 简介（可选）
            - timestamp: 发布时间（可选）
            - video_urls: 视频URL列表，每个元素是单个媒体的可用URL列表（List[List[str]]），即使只有一条直链也要是列表的列表（可选，缺省时补为空列表）
            - video_cover_urls: 视频封面URL列表，与视频逐项对齐；单个通用封面也使用二维列表（可选）
            - image_urls: 图片URL列表，每个元素是单个媒体的可用URL列表（List[List[str]]），即使只有一条直链也要是列表的列表（可选，缺省时补为空列表）
            - image_headers: dict，图片下载的完整请求头字典（可选，缺省时补为空字典）
            - image_tls_ciphers: str，平台图片请求的 TLS 加密套件列表（可选，缺省时沿用会话配置并保持证书校验）
            - video_headers: dict，视频下载的完整请求头字典（可选，缺省时补为空字典）
            - video_force_download: bool，是否强制下载到缓存目录（可选，默认False）。True=缓存目录不可用或下载失败时跳过该视频；False=由下载决策引擎按目录能力选择 local/direct
            - platform: 内容来源平台名（可选）

            ParserManager 会统一写入 source_url 和 parser_name，平台解析器不得返回这两个字段。

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

