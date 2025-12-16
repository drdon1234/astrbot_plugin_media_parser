# -*- coding: utf-8 -*-
"""
媒体处理器模块
包含不同类型的媒体处理逻辑

注意：所有媒体下载应通过 router.download_media 进行，不要直接调用底层处理器
"""

from .normal_video import pre_download_videos, pre_download_media
from .m3u8 import M3U8Handler

__all__ = [
    'pre_download_videos',
    'pre_download_media',
    'M3U8Handler'
]

