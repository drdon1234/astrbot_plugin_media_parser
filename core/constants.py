"""常量与默认配置定义。"""
import os


class Config:
    """配置常量类，包含下载、解析等功能的配置参数"""
    
    DEFAULT_TIMEOUT = 30
    VIDEO_SIZE_CHECK_TIMEOUT = 10
    IMAGE_DOWNLOAD_TIMEOUT = 10
    VIDEO_DOWNLOAD_TIMEOUT = 300
    TIKTOK_CURL_CONNECT_TIMEOUT = 10
    TIKTOK_CURL_MAX_TIME = 30
    
    DEFAULT_LARGE_VIDEO_THRESHOLD_MB = 100.0
    MAX_LARGE_VIDEO_THRESHOLD_MB = 100.0
    DOWNLOAD_RETRY_ATTEMPTS = 3
    DOWNLOAD_RETRY_BASE_DELAY = 0.5
    
    STREAM_DOWNLOAD_CHUNK_SIZE = 2 * 1024 * 1024
    
    RANGE_DOWNLOAD_CHUNK_SIZE = 2 * 1024 * 1024
    RANGE_DOWNLOAD_MAX_CONCURRENT = 64
    
    M3U8_MAX_CONCURRENT_SEGMENTS = 10
    
    DOWNLOAD_MANAGER_MAX_CONCURRENT = 5
    PARSER_MAX_CONCURRENT = 10
    
    PLUGIN_NAME = "astrbot_plugin_media_parser"
    CACHE_DIR_NAME = "cache"
    RUNTIME_DIR_NAME = "runtime_manager"
    DEFAULT_CACHE_DIR = "/app/sharedFolder/video_parser/cache"

    @staticmethod
    def build_cache_dir(prefix: str) -> str:
        """基于运行环境前缀生成统一的媒体缓存目录。"""
        return os.path.abspath(os.path.join(prefix, Config.CACHE_DIR_NAME))

    @staticmethod
    def build_runtime_dir(cache_dir: str, *parts: str) -> str:
        """基于媒体缓存目录生成统一的运行时文件目录。"""
        return os.path.abspath(
            os.path.join(cache_dir, Config.RUNTIME_DIR_NAME, *parts)
        )

