from typing import List
import logging

try:
    from astrbot.api import logger
except ImportError:
    logger = logging.getLogger(__name__)

from .constants import Config
from .downloader.utils import check_cache_dir_available
from .parser.platform import (
    BilibiliParser, DouyinParser, KuaishouParser, WeiboParser,
    XiaohongshuParser, XiaoheiheParser, TwitterParser, YoutubeParser
)

class ConfigManager:
    def __init__(self, config: dict):
        self._config = config
        self._parse_config()

    def _parse_config(self):
        c = self._config
        self.is_auto_pack = c.get("is_auto_pack", True)
        
        ts = c.get("trigger_settings", {})
        self.is_auto_parse = ts.get("is_auto_parse", True)
        self.trigger_keywords = ts.get("trigger_keywords", ["视频解析", "解析视频"])
        
        wl = c.get("whitelist", {})
        self.whitelist_enable = wl.get("enable", False)
        self.whitelist_user = wl.get("user", [])
        self.whitelist_group = wl.get("group", [])
        
        vss = c.get("video_size_settings", {})
        self.max_video_size_mb = vss.get("max_video_size_mb", 0.0)
        lvt = vss.get("large_video_threshold_mb", Config.MAX_LARGE_VIDEO_THRESHOLD_MB)
        self.large_video_threshold_mb = min(lvt, Config.MAX_LARGE_VIDEO_THRESHOLD_MB) if lvt > 0 else 0.0
        
        ds = c.get("download_settings", {})
        self.cache_dir = ds.get("cache_dir", "/app/sharedFolder/video_parser/cache")
        self.pre_download_all_media = ds.get("pre_download_all_media", False)
        self.max_concurrent_downloads = ds.get("max_concurrent_downloads", Config.DOWNLOAD_MANAGER_MAX_CONCURRENT)
        
        if self.pre_download_all_media and not check_cache_dir_available(self.cache_dir):
            logger.warning(f"预下载模式已启用，但缓存目录不可用: {self.cache_dir}，将自动降级为禁用")
            self.pre_download_all_media = False
        
        pes = c.get("parser_enable_settings", {})
        self.enable_bilibili = pes.get("enable_bilibili", True)
        self.enable_douyin = pes.get("enable_douyin", True)
        self.enable_kuaishou = pes.get("enable_kuaishou", True)
        self.enable_weibo = pes.get("enable_weibo", True)
        self.enable_xiaohongshu = pes.get("enable_xiaohongshu", True)
        self.enable_xiaoheihe = pes.get("enable_xiaoheihe", True)
        self.enable_twitter = pes.get("enable_twitter", True)
        self.enable_youtube = pes.get("enable_youtube", True)
        
        ps = c.get("proxy_settings", {})
        self.proxy_addr = ps.get("proxy_addr", "")
        
        self.xiaoheihe_use_video_proxy = ps.get("xiaoheihe", {}).get("video", False)
        
        tw = ps.get("twitter", {})
        self.twitter_use_parse_proxy = tw.get("parse", False)
        self.twitter_use_image_proxy = tw.get("image", False)
        self.twitter_use_video_proxy = tw.get("video", False)
        
        self.youtube_use_proxy = ps.get("youtube", {}).get("use_proxy", True)

        self.debug_mode = c.get("debug", False)
        if self.debug_mode:
            logger.setLevel(logging.DEBUG)
            logger.debug("Debug模式已启用")

    def create_parsers(self) -> List:
        parsers = []
        if self.enable_bilibili: parsers.append(BilibiliParser())
        if self.enable_douyin: parsers.append(DouyinParser())
        if self.enable_kuaishou: parsers.append(KuaishouParser())
        if self.enable_weibo: parsers.append(WeiboParser())
        if self.enable_xiaohongshu: parsers.append(XiaohongshuParser())
        
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
            
        if self.enable_youtube:
            parsers.append(YoutubeParser(
                use_proxy=self.youtube_use_proxy,
                proxy_url=self.proxy_addr if self.proxy_addr else None
            ))
        
        if not parsers:
            raise ValueError("至少需要启用一个视频解析器。请检查配置。")
            
        return parsers