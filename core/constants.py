# -*- coding: utf-8 -*-
"""
常量配置模块
包含所有配置常量，避免魔法数字和硬编码值
"""


class Config:
    """配置常量类。"""
    
    DEFAULT_TIMEOUT = 30
    VIDEO_SIZE_CHECK_TIMEOUT = 10
    IMAGE_DOWNLOAD_TIMEOUT = 30
    VIDEO_DOWNLOAD_TIMEOUT = 300
    
    MAX_RETRIES = 3
    
    DEFAULT_MAX_CONCURRENT_DOWNLOADS = 3
    MAX_MAX_CONCURRENT_DOWNLOADS = 10
    RECOMMENDED_MAX_CONCURRENT_DOWNLOADS_MIN = 3
    RECOMMENDED_MAX_CONCURRENT_DOWNLOADS_MAX = 5
    
    DEFAULT_LARGE_VIDEO_THRESHOLD_MB = 50.0
    MAX_LARGE_VIDEO_THRESHOLD_MB = 100.0
    
    MAX_MEDIA_ID_LENGTH = 50
    MAX_FILENAME_LENGTH = 100
    
    PARSER_SEMAPHORE_LIMIT = 10
    TWITTER_PARSER_SEMAPHORE_LIMIT = 5
    
    USER_AGENT_DESKTOP = (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    )
    
    USER_AGENT_MOBILE = (
        'Mozilla/5.0 (Linux; Android 8.0.0; SM-G955U Build/R16NW) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/116.0.0.0 Mobile Safari/537.36'
    )
    
    DEFAULT_ACCEPT_LANGUAGE = (
        'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7'
    )

