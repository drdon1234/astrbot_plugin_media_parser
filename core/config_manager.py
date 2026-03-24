"""配置管理模块，负责默认值处理、类型转换与配置兜底。"""
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any, List

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


# ── 配置分组 dataclass ──────────────────────────────────


@dataclass
class TriggerConfig:
    auto_parse: bool = True
    keywords: List[str] = field(default_factory=lambda: ["视频解析", "解析视频"])
    reply_trigger: bool = False

    def has_keyword(self, text: str) -> bool:
        for kw in self.keywords:
            if kw in text:
                return True
        return False

    def should_parse(self, message_str: str) -> bool:
        if self.auto_parse:
            return True
        return self.has_keyword(message_str)


@dataclass
class MessageConfig:
    auto_pack: bool = False
    opening_enabled: bool = True
    opening_content: str = "流媒体解析bot为您服务 ٩( 'ω' )و"
    text_metadata: bool = True
    hot_comment_count: int = 0
    hot_comment_bilibili: bool = True
    hot_comment_weibo: bool = True
    hot_comment_xiaohongshu: bool = True


@dataclass
class PermissionConfig:
    admin_id: str = ""
    whitelist_enable: bool = False
    whitelist_user: List[str] = field(default_factory=list)
    whitelist_group: List[str] = field(default_factory=list)
    blacklist_enable: bool = False
    blacklist_user: List[str] = field(default_factory=list)
    blacklist_group: List[str] = field(default_factory=list)

    def check(self, is_private: bool, sender_id: Any, group_id: Any) -> bool:
        """检查用户或群组是否有权限使用解析"""
        sender_id_str = str(sender_id or "").strip()
        group_id_str = "" if is_private else str(group_id or "").strip()

        if self.admin_id and sender_id_str == self.admin_id:
            return True

        allowed = None
        if self.whitelist_enable and sender_id_str in self.whitelist_user:
            allowed = True
        elif self.blacklist_enable and sender_id_str in self.blacklist_user:
            allowed = False
        elif self.whitelist_enable and group_id_str and group_id_str in self.whitelist_group:
            allowed = True
        elif self.blacklist_enable and group_id_str and group_id_str in self.blacklist_group:
            allowed = False

        if allowed is None:
            allowed = not self.whitelist_enable

        return allowed


@dataclass
class DownloadConfig:
    max_video_size_mb: float = 1000.0
    large_video_threshold_mb: float = Config.DEFAULT_LARGE_VIDEO_THRESHOLD_MB
    cache_dir: str = ""
    pre_download_all_media: bool = False
    max_concurrent_downloads: int = Config.DOWNLOAD_MANAGER_MAX_CONCURRENT


@dataclass
class ProxyConfig:
    address: str = ""
    xiaoheihe_use_video_proxy: bool = True
    twitter_use_parse_proxy: bool = False
    twitter_use_image_proxy: bool = True
    twitter_use_video_proxy: bool = True


@dataclass
class BilibiliEnhancedConfig:
    use_cookie: bool = False
    cookie: str = ""
    max_quality: int = 0
    cookie_feature_requested: bool = False
    cookie_runtime_enabled: bool = False
    cookie_runtime_file: str = ""
    enable_admin_assist: bool = False
    admin_reply_timeout_minutes: int = 1440
    admin_request_cooldown_minutes: int = 1440


@dataclass
class MediaRelayConfig:
    enabled: bool = False
    callback_api_base: str = ""
    file_token_ttl: int = 300


@dataclass
class AdminConfig:
    clean_cache_keyword: str = "清理媒体"
    debug_mode: bool = False


# ── 配置管理器 ──────────────────────────────────────────


class ConfigManager:

    """配置读取门面，向业务层提供类型安全的配置访问。"""
    def __init__(self, config: dict):
        self._config = config
        self.bilibili_parser = None
        self._parse_config()

    # ── 内部解析 ────────────────────────────────────────

    def _parse_config(self):
        """解析原始 dict，填充各领域配置分组。"""

        # --- trigger ---
        trigger_raw = self._config.get("trigger", {})
        self.trigger = TriggerConfig(
            auto_parse=trigger_raw.get("auto_parse", True),
            keywords=trigger_raw.get("keywords", ["视频解析", "解析视频"]),
            reply_trigger=bool(trigger_raw.get("reply_trigger", False)),
        )
        if (
            not self.trigger.auto_parse
            and not self.trigger.keywords
            and not self.trigger.reply_trigger
        ):
            logger.warning(
                "自动解析已关闭且未配置任何触发关键词，"
                "回复触发也已禁用，解析功能将完全不可用"
            )

        # --- message ---
        message_raw = self._config.get("message", {})
        opening = message_raw.get("opening", {})
        hot_comments = message_raw.get("hot_comments", {})
        if not isinstance(hot_comments, dict):
            hot_comments = {}

        text_metadata_enabled = message_raw.get("text_metadata", True)
        hot_count = self._parse_non_negative_int(
            hot_comments.get("count", 0), 0
        )
        if not text_metadata_enabled:
            hot_count = 0

        self.message = MessageConfig(
            auto_pack=message_raw.get("auto_pack", False),
            opening_enabled=opening.get("enable", True),
            opening_content=opening.get(
                "content", "流媒体解析bot为您服务 ٩( 'ω' )و"
            ),
            text_metadata=text_metadata_enabled,
            hot_comment_count=hot_count,
            hot_comment_bilibili=bool(hot_comments.get("bilibili", True)),
            hot_comment_weibo=bool(hot_comments.get("weibo", True)),
            hot_comment_xiaohongshu=bool(
                hot_comments.get("xiaohongshu", True)
            ),
        )

        # --- permissions ---
        permissions_raw = self._config.get("permissions", {})
        whitelist = permissions_raw.get("whitelist", {})
        blacklist = permissions_raw.get("blacklist", {})
        admin_id = str(permissions_raw.get("admin_id", "") or "").strip()
        wl_user = self._normalize_id_list(whitelist.get("user", []))
        if admin_id and admin_id not in wl_user:
            wl_user.append(admin_id)

        self.permission = PermissionConfig(
            admin_id=admin_id,
            whitelist_enable=whitelist.get("enable", False),
            whitelist_user=wl_user,
            whitelist_group=self._normalize_id_list(
                whitelist.get("group", [])
            ),
            blacklist_enable=blacklist.get("enable", False),
            blacklist_user=self._normalize_id_list(
                blacklist.get("user", [])
            ),
            blacklist_group=self._normalize_id_list(
                blacklist.get("group", [])
            ),
        )

        # --- download ---
        download_raw = self._config.get("download", {})

        max_video_size_mb = self._parse_non_negative_float(
            download_raw.get("max_video_size_mb", 1000.0), 1000.0
        )
        large_video_threshold_mb = self._parse_non_negative_float(
            download_raw.get(
                "large_video_threshold_mb",
                Config.MAX_LARGE_VIDEO_THRESHOLD_MB
            ),
            Config.MAX_LARGE_VIDEO_THRESHOLD_MB
        )
        if large_video_threshold_mb > 0:
            large_video_threshold_mb = min(
                large_video_threshold_mb,
                Config.MAX_LARGE_VIDEO_THRESHOLD_MB
            )

        configured_cache_dir = str(
            download_raw.get("cache_dir", "") or ""
        ).strip()
        if (
            not configured_cache_dir
            or configured_cache_dir == Config.DEFAULT_CACHE_DIR
        ):
            if os.path.exists('/.dockerenv'):
                cache_dir = Config.DEFAULT_CACHE_DIR
            else:
                cache_dir = os.path.join(
                    tempfile.gettempdir(), "astrbot_media_parser_cache"
                )
        else:
            cache_dir = configured_cache_dir

        pre_download = download_raw.get("pre_download", False)
        max_concurrent = min(
            self._parse_positive_int(
                download_raw.get(
                    "max_concurrent",
                    Config.DOWNLOAD_MANAGER_MAX_CONCURRENT
                ),
                Config.DOWNLOAD_MANAGER_MAX_CONCURRENT
            ),
            20
        )

        # --- media_relay ---
        relay_raw = self._config.get("media_relay", {})
        self.relay = MediaRelayConfig(
            enabled=relay_raw.get("enable", False),
            callback_api_base=str(
                relay_raw.get("callback_url", "") or ""
            ).strip().rstrip("/"),
            file_token_ttl=max(
                30,
                self._parse_positive_int(relay_raw.get("ttl", 300), 300)
            ),
        )

        if self.relay.enabled:
            cache_dir = os.path.join(
                tempfile.gettempdir(),
                "astrbot_media_parser_relay_cache"
            )
            pre_download = True
            logger.info(
                f"媒体中转模式已启用，缓存目录: {cache_dir}，"
                f"预下载已强制开启"
            )

        if pre_download:
            if not check_cache_dir_available(cache_dir):
                logger.warning(
                    f"预下载模式已启用，但缓存目录不可用: {cache_dir}，"
                    f"将自动降级为禁用预下载模式"
                )
                pre_download = False

        self.download = DownloadConfig(
            max_video_size_mb=max_video_size_mb,
            large_video_threshold_mb=large_video_threshold_mb,
            cache_dir=cache_dir,
            pre_download_all_media=pre_download,
            max_concurrent_downloads=max_concurrent,
        )

        # --- bilibili_enhanced ---
        bili = self._config.get("bilibili_enhanced", {})
        if not isinstance(bili, dict):
            bili = {}

        use_cookie = bool(bili.get("use_cookie", False))
        if use_cookie:
            cookie = str(bili.get("cookie", "") or "").strip()
            max_quality_label = str(
                bili.get("max_quality", "不限制") or "不限制"
            ).strip()
            max_quality = BILIBILI_QUALITY_MAP.get(max_quality_label, 0)
            admin_assist_raw = bili.get("admin_assist", {})
            if not isinstance(admin_assist_raw, dict):
                admin_assist_raw = {}
            enable_admin_assist = bool(
                admin_assist_raw.get("enable", False)
            )
            admin_reply_timeout = self._parse_positive_int(
                admin_assist_raw.get("reply_timeout_minutes", 1440), 1440
            )
            admin_request_cooldown = self._parse_positive_int(
                admin_assist_raw.get("request_cooldown_minutes", 1440), 1440
            )
        else:
            cookie = ""
            max_quality = 0
            enable_admin_assist = False
            admin_reply_timeout = 1440
            admin_request_cooldown = 1440

        cookie_feature_requested = use_cookie
        cookie_runtime_enabled = bool(use_cookie and pre_download)

        runtime_file_name = "cookie.json"
        core_dir = os.path.dirname(os.path.abspath(__file__))
        cookie_dir = os.path.join(
            core_dir, "parser", "runtime_manager", "bilibili"
        )
        cookie_runtime_file = os.path.join(cookie_dir, runtime_file_name)
        if use_cookie:
            try:
                os.makedirs(cookie_dir, exist_ok=True)
            except Exception as e:
                logger.warning(
                    f"B站Cookie运行时目录不可用，将回退到缓存目录保存: {e}"
                )
                fallback = os.path.join(
                    cache_dir, "runtime_manager", "bilibili"
                )
                cookie_runtime_file = os.path.join(
                    fallback, runtime_file_name
                )

        if cookie_feature_requested and not cookie_runtime_enabled:
            logger.warning(
                '检测到已开启"是否携带Cookie解析视频"，但预下载未启用或不可用，'
                "将旁路B站Cookie与协助登录流程，直接使用无Cookie直链模式。"
            )

        self.bilibili = BilibiliEnhancedConfig(
            use_cookie=use_cookie,
            cookie=cookie,
            max_quality=max_quality,
            cookie_feature_requested=cookie_feature_requested,
            cookie_runtime_enabled=cookie_runtime_enabled,
            cookie_runtime_file=cookie_runtime_file,
            enable_admin_assist=enable_admin_assist,
            admin_reply_timeout_minutes=admin_reply_timeout,
            admin_request_cooldown_minutes=admin_request_cooldown,
        )

        # --- parsers ---
        parsers_raw = self._config.get("parsers", {})
        self._enable_bilibili = parsers_raw.get("bilibili", True)
        self._enable_douyin = parsers_raw.get("douyin", True)
        self._enable_kuaishou = parsers_raw.get("kuaishou", True)
        self._enable_weibo = parsers_raw.get("weibo", True)
        self._enable_xiaohongshu = parsers_raw.get("xiaohongshu", True)
        self._enable_xiaoheihe = parsers_raw.get("xiaoheihe", True)
        self._enable_twitter = parsers_raw.get("twitter", True)

        # --- proxy ---
        proxy_raw = self._config.get("proxy", {})
        twitter_proxy = proxy_raw.get("twitter", {})
        self.proxy = ProxyConfig(
            address=proxy_raw.get("address", ""),
            xiaoheihe_use_video_proxy=proxy_raw.get("xiaoheihe_video", True),
            twitter_use_parse_proxy=twitter_proxy.get("parse", False),
            twitter_use_image_proxy=twitter_proxy.get("image", True),
            twitter_use_video_proxy=twitter_proxy.get("video", True),
        )

        # --- admin ---
        admin_raw = self._config.get("admin", {})
        self.admin = AdminConfig(
            clean_cache_keyword=str(
                admin_raw.get("clean_cache_keyword", "清理媒体") or "清理媒体"
            ).strip(),
            debug_mode=admin_raw.get("debug", False),
        )
        if self.admin.debug_mode:
            import logging
            logger.setLevel(logging.DEBUG)
            logger.debug("Debug模式已启用")

    # ── 工厂方法 ────────────────────────────────────────

    def _effective_hot_comment_count(self, enabled: bool) -> int:
        if not self.message.text_metadata:
            return 0
        if not enabled:
            return 0
        return self.message.hot_comment_count

    def create_parsers(self) -> List:
        """根据配置创建并返回解析器列表。

        Raises:
            ValueError: 没有启用任何解析器时
        """
        parsers = []
        bili_hc = self._effective_hot_comment_count(
            self.message.hot_comment_bilibili
        )
        weibo_hc = self._effective_hot_comment_count(
            self.message.hot_comment_weibo
        )
        xhs_hc = self._effective_hot_comment_count(
            self.message.hot_comment_xiaohongshu
        )
        proxy_addr = self.proxy.address or None

        if self._enable_bilibili:
            self.bilibili_parser = BilibiliParser(
                cookie_runtime_enabled=self.bilibili.cookie_runtime_enabled,
                configured_cookie=self.bilibili.cookie,
                max_quality=self.bilibili.max_quality,
                admin_assist_enabled=self.bilibili.enable_admin_assist,
                admin_reply_timeout_minutes=self.bilibili.admin_reply_timeout_minutes,
                admin_request_cooldown_minutes=self.bilibili.admin_request_cooldown_minutes,
                credential_path=self.bilibili.cookie_runtime_file,
                hot_comment_count=bili_hc,
            )
            parsers.append(self.bilibili_parser)
        if self._enable_douyin:
            parsers.append(DouyinParser())
        if self._enable_kuaishou:
            parsers.append(KuaishouParser())
        if self._enable_weibo:
            parsers.append(WeiboParser(hot_comment_count=weibo_hc))
        if self._enable_xiaohongshu:
            parsers.append(XiaohongshuParser(hot_comment_count=xhs_hc))
        if self._enable_xiaoheihe:
            parsers.append(XiaoheiheParser(
                use_video_proxy=self.proxy.xiaoheihe_use_video_proxy,
                proxy_url=proxy_addr,
            ))
        if self._enable_twitter:
            parsers.append(TwitterParser(
                use_parse_proxy=self.proxy.twitter_use_parse_proxy,
                use_image_proxy=self.proxy.twitter_use_image_proxy,
                use_video_proxy=self.proxy.twitter_use_video_proxy,
                proxy_url=proxy_addr,
            ))

        if not parsers:
            raise ValueError(
                "至少需要启用一个视频解析器。"
                "请检查配置中的 parsers 设置。"
            )

        return parsers

    # ── 静态辅助 ────────────────────────────────────────

    @staticmethod
    def _parse_positive_int(value, default: int) -> int:
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return max(1, int(default))

    @staticmethod
    def _parse_non_negative_float(value, default: float) -> float:
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return max(0.0, float(default))

    @staticmethod
    def _parse_non_negative_int(value, default: int) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return max(0, int(default))

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
