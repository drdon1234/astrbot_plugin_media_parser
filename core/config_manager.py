import os
import tempfile
from typing import List

from .logger import logger

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


BILIBILI_QUALITY_MAP = {
    "不限制": 0,
    "4K": 120,
    "1080P60": 116,
    "1080P+": 112,
    "1080P": 80,
    "720P": 64,
    "480P": 32,
    "360P": 16,
}


class ConfigManager:

    def __init__(self, config: dict):
        """初始化配置管理器

        Args:
            config: 原始配置字典

        Raises:
            ValueError: 没有启用任何解析器时
        """
        self._config = config
        self.bilibili_parser = None
        self._parse_config()

    def _parse_config(self):
        """解析配置"""
        self.is_auto_pack = self._config.get("is_auto_pack", False)
        
        trigger_settings = self._config.get("trigger_settings", {})
        self.is_auto_parse = trigger_settings.get("is_auto_parse", True)
        self.trigger_keywords = trigger_settings.get(
            "trigger_keywords",
            ["视频解析", "解析视频"]
        )
        
        permissions = self._config.get("permissions", {})
        whitelist = permissions.get("whitelist", {})
        blacklist = permissions.get("blacklist", {})
        self.admin_id = str(
            permissions.get("admin_id", self._config.get("admin_id", "")) or ""
        ).strip()
        
        self.whitelist_enable = whitelist.get("enable", False)
        whitelist_user = whitelist.get("user", [])
        self.whitelist_user = self._normalize_id_list(whitelist_user)
        if self.admin_id and self.admin_id not in self.whitelist_user:
            self.whitelist_user.append(self.admin_id)
        whitelist_group = whitelist.get("group", [])
        self.whitelist_group = self._normalize_id_list(whitelist_group)
        
        self.blacklist_enable = blacklist.get("enable", False)
        blacklist_user = blacklist.get("user", [])
        self.blacklist_user = self._normalize_id_list(blacklist_user)
        blacklist_group = blacklist.get("group", [])
        self.blacklist_group = self._normalize_id_list(blacklist_group)
        
        text_settings = self._config.get("text_settings", {})
        self.enable_opening_msg = text_settings.get("enable_opening_msg", True)
        self.opening_msg_content = text_settings.get(
            "opening_msg_content",
            "流媒体解析bot为您服务 ٩( 'ω' )و"
        )
        self.enable_text_metadata = text_settings.get("enable_text_metadata", True)
        
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
        configured_cache_dir = download_settings.get("cache_dir", "").strip()
        
        if not configured_cache_dir or configured_cache_dir == "/app/sharedFolder/video_parser/cache":
            if os.path.exists('/.dockerenv'):
                self.cache_dir = "/app/sharedFolder/video_parser/cache"
            else:
                self.cache_dir = os.path.join(tempfile.gettempdir(), "astrbot_media_parser_cache")
        else:
            self.cache_dir = configured_cache_dir
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

        cookie_settings = self._config.get("cookie_settings", {})
        bilibili_cookie_settings = {}
        if isinstance(cookie_settings, dict):
            bilibili_cookie_settings = cookie_settings.get("bilibili", {})
        if not isinstance(bilibili_cookie_settings, dict):
            bilibili_cookie_settings = {}
        if not bilibili_cookie_settings:
            legacy_settings = self._config.get("bilibili_cookie_settings", {})
            if isinstance(legacy_settings, dict):
                bilibili_cookie_settings = legacy_settings

        self.bilibili_use_cookie_for_parsing = bool(
            bilibili_cookie_settings.get("use_cookie_for_parsing", False)
        )
        if self.bilibili_use_cookie_for_parsing:
            self.bilibili_cookie = str(
                bilibili_cookie_settings.get("cookie", "") or ""
            ).strip()
            max_quality_label = str(
                bilibili_cookie_settings.get("max_quality", "不限制") or "不限制"
            ).strip()
            self.bilibili_max_quality = BILIBILI_QUALITY_MAP.get(
                max_quality_label,
                0
            )
            self.bilibili_enable_admin_assist_on_expire = bool(
                bilibili_cookie_settings.get("enable_admin_assist_on_expire", False)
            )
            self.bilibili_admin_reply_timeout_minutes = self._parse_positive_int(
                bilibili_cookie_settings.get("admin_reply_timeout_minutes", 1440),
                1440
            )
            self.bilibili_admin_request_cooldown_minutes = self._parse_positive_int(
                bilibili_cookie_settings.get("admin_request_cooldown_minutes", 1440),
                1440
            )
        else:
            self.bilibili_cookie = ""
            self.bilibili_max_quality = 0
            self.bilibili_enable_admin_assist_on_expire = False
            self.bilibili_admin_reply_timeout_minutes = 1440
            self.bilibili_admin_request_cooldown_minutes = 1440

        self.bilibili_cookie_feature_requested = self.bilibili_use_cookie_for_parsing
        self.bilibili_cookie_runtime_enabled = bool(
            self.bilibili_use_cookie_for_parsing and self.pre_download_all_media
        )
        runtime_file_name = "cookie.json"
        core_dir = os.path.dirname(os.path.abspath(__file__))
        cookie_dir = os.path.join(
            core_dir,
            "parser",
            "runtime_manager",
            "bilibili"
        )
        self.bilibili_cookie_runtime_file = os.path.join(
            cookie_dir,
            runtime_file_name
        )
        if self.bilibili_use_cookie_for_parsing:
            try:
                os.makedirs(cookie_dir, exist_ok=True)
            except Exception as e:
                logger.warning(
                    f"B站Cookie运行时目录不可用，将回退到缓存目录保存: {e}"
                )
                fallback_cookie_dir = os.path.join(
                    self.cache_dir,
                    "runtime_manager",
                    "bilibili"
                )
                self.bilibili_cookie_runtime_file = os.path.join(
                    fallback_cookie_dir,
                    runtime_file_name
                )
        if (
            self.bilibili_cookie_feature_requested and
            not self.bilibili_cookie_runtime_enabled
        ):
            logger.warning(
                "检测到已开启“是否携带Cookie解析视频”，但预下载未启用或不可用，"
                "将旁路B站Cookie与协助登录流程，直接使用无Cookie直链模式。"
            )
        
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

    @staticmethod
    def _parse_positive_int(value, default: int) -> int:
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return max(1, int(default))

    @staticmethod
    def _normalize_id_list(values) -> List[str]:
        if not isinstance(values, list):
            return []
        normalized: List[str] = []
        seen = set()
        for value in values:
            if value is None:
                continue
            value_str = str(value).strip()
            if not value_str or value_str in seen:
                continue
            seen.add(value_str)
            normalized.append(value_str)
        return normalized

    def create_parsers(self) -> List:
        """创建解析器列表

        Returns:
            解析器列表

        Raises:
            ValueError: 没有启用任何解析器时
        """
        parsers = []
        
        if self.enable_bilibili:
            self.bilibili_parser = BilibiliParser(
                cookie_runtime_enabled=self.bilibili_cookie_runtime_enabled,
                configured_cookie=self.bilibili_cookie,
                max_quality=self.bilibili_max_quality,
                admin_assist_enabled=self.bilibili_enable_admin_assist_on_expire,
                admin_reply_timeout_minutes=self.bilibili_admin_reply_timeout_minutes,
                admin_request_cooldown_minutes=self.bilibili_admin_request_cooldown_minutes,
                credential_path=self.bilibili_cookie_runtime_file
            )
            parsers.append(self.bilibili_parser)
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

