from typing import List

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from .constants import Config
from .downloader.utils import check_cache_dir_available
from .parser.platform import (
    BilibiliParser,
    DouyinParser,
    KuaishouParser,
    WeiboParser,
    XiaohongshuParser,
    XiaoheiheParser,
    TwitterParser
)


class ConfigManager:

    def __init__(self, config: dict):
        """初始化配置管理器

        Args:
            config: 原始配置字典

        Raises:
            ValueError: 没有启用任何解析器时
        """
        self._config = config
        self._parse_config()

    def _parse_config(self):
        """解析配置"""
        self.is_auto_pack = self._config.get("is_auto_pack", True)
        
        trigger_settings = self._config.get("trigger_settings", {})
        self.is_auto_parse = trigger_settings.get("is_auto_parse", True)
        self.trigger_keywords = trigger_settings.get(
            "trigger_keywords",
            ["视频解析", "解析视频"]
        )
        
        whitelist = self._config.get("whitelist", {})
        self.whitelist_enable = whitelist.get("enable", False)
        self.whitelist_user = whitelist.get("user", [])
        self.whitelist_group = whitelist.get("group", [])
        
        video_size_settings = self._config.get("video_size_settings", {})
        self.max_video_size_mb = video_size_settings.get("max_video_size_mb", 0.0)
        large_video_threshold_mb = video_size_settings.get(
            "large_video_threshold_mb",
            Config.MAX_LARGE_VIDEO_THRESHOLD_MB
        )
        if large_video_threshold_mb > 0:
            large_video_threshold_mb = min(
                large_video_threshold_mb,
                Config.MAX_LARGE_VIDEO_THRESHOLD_MB
            )
        self.large_video_threshold_mb = large_video_threshold_mb
        
        download_settings = self._config.get("download_settings", {})
        self.cache_dir = download_settings.get(
            "cache_dir",
            "/app/sharedFolder/video_parser/cache"
        )
        self.pre_download_all_media = download_settings.get(
            "pre_download_all_media",
            False
        )
        self.max_concurrent_downloads = download_settings.get(
            "max_concurrent_downloads",
            Config.DOWNLOAD_MANAGER_MAX_CONCURRENT
        )
        
        if self.pre_download_all_media:
            if not check_cache_dir_available(self.cache_dir):
                logger.warning(
                    f"预下载模式已启用，但缓存目录不可用: {self.cache_dir}，"
                    f"将自动降级为禁用预下载模式"
                )
                self.pre_download_all_media = False
        
        parser_enable_settings = self._config.get("parser_enable_settings", {})
        self.enable_bilibili = parser_enable_settings.get("enable_bilibili", True)
        self.enable_douyin = parser_enable_settings.get("enable_douyin", True)
        self.enable_kuaishou = parser_enable_settings.get(
            "enable_kuaishou",
            True
        )
        self.enable_weibo = parser_enable_settings.get(
            "enable_weibo",
            True
        )
        self.enable_xiaohongshu = parser_enable_settings.get(
            "enable_xiaohongshu",
            True
        )
        self.enable_xiaoheihe = parser_enable_settings.get(
            "enable_xiaoheihe",
            True
        )
        self.enable_twitter = parser_enable_settings.get("enable_twitter", True)
        
        proxy_settings = self._config.get("proxy_settings", {})
        self.proxy_addr = proxy_settings.get("proxy_addr", "")
        
        xiaoheihe_proxy = proxy_settings.get("xiaoheihe", {})
        self.xiaoheihe_use_video_proxy = xiaoheihe_proxy.get("video", False)
        
        twitter_proxy = proxy_settings.get("twitter", {})
        self.twitter_use_parse_proxy = twitter_proxy.get("parse", False)
        self.twitter_use_image_proxy = twitter_proxy.get("image", False)
        self.twitter_use_video_proxy = twitter_proxy.get("video", False)
        
        self.debug_mode = self._config.get("debug", False)
        if self.debug_mode:
            import logging
            logger.setLevel(logging.DEBUG)
            logger.debug("Debug模式已启用")

    def create_parsers(self) -> List:
        """创建解析器列表

        Returns:
            解析器列表

        Raises:
            ValueError: 没有启用任何解析器时
        """
        parsers = []
        
        if self.enable_bilibili:
            parsers.append(BilibiliParser())
        if self.enable_douyin:
            parsers.append(DouyinParser())
        if self.enable_kuaishou:
            parsers.append(KuaishouParser())
        if self.enable_weibo:
            parsers.append(WeiboParser())
        if self.enable_xiaohongshu:
            parsers.append(XiaohongshuParser())
        if self.enable_xiaoheihe:
            parsers.append(XiaoheiheParser(
                use_video_proxy=self.xiaoheihe_use_video_proxy,
                proxy_url=self.proxy_addr if self.proxy_addr else None
            ))
        if self.enable_twitter:
            parsers.append(TwitterParser(
                use_parse_proxy=self.twitter_use_parse_proxy,
                use_image_proxy=self.twitter_use_image_proxy,
                use_video_proxy=self.twitter_use_video_proxy,
                proxy_url=self.proxy_addr if self.proxy_addr else None
            ))
        
        if not parsers:
            raise ValueError(
                "至少需要启用一个视频解析器。"
                "请检查配置中的 parser_enable_settings 设置。"
            )
        
        return parsers

