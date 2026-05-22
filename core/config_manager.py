"""配置管理模块，负责默认值处理、类型转换与配置兜底。"""
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from .logger import logger

from .constants import Config
from .downloader.utils import check_cache_dir_available
from .parser.platform import (
    BilibiliParser,
    DouyinParser,
    TikTokParser,
    KuaishouParser,
    WeiboParser,
    XiaohongshuParser,
    XianyuParser,
    ToutiaoParser,
    XiaoheiheParser,
    TwitterParser
)
from .translation.provider_defs import (
    LLM_PROVIDER_DEFAULTS,
    LLM_PROVIDER_OPTIONS,
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

PARSER_OUTPUT_KEYS = (
    "bilibili",
    "douyin",
    "tiktok",
    "kuaishou",
    "weibo",
    "xiaohongshu",
    "xianyu",
    "toutiao",
    "xiaoheihe",
    "twitter",
)

OUTPUT_MODE_DISABLED = "关闭"
OUTPUT_MODE_ALL = "全部发送"
OUTPUT_MODE_TEXT_ONLY = "仅文本"
OUTPUT_MODE_RICH_ONLY = "仅富媒体"

OUTPUT_MODE_FLAGS = {
    OUTPUT_MODE_DISABLED: (False, False),
    OUTPUT_MODE_ALL: (True, True),
    OUTPUT_MODE_TEXT_ONLY: (True, False),
    OUTPUT_MODE_RICH_ONLY: (False, True),
}

PACK_MODE_NONE = "不打包"
PACK_MODE_ALL = "全部打包"
PACK_MODE_CONDITIONAL = "按条件打包"
PACK_MODES = {
    PACK_MODE_NONE,
    PACK_MODE_ALL,
    PACK_MODE_CONDITIONAL,
}
TRANSLATION_TARGET_LANGUAGES = {
    "简体中文",
    "繁体中文",
    "English",
    "日本語",
    "한국어",
    "Español",
    "Français",
    "Deutsch",
    "Русский",
    "Português",
}
TRANSLATION_CONTENT_SCOPES = {
    "仅正文",
    "正文和标题",
}


def _is_docker_environment() -> bool:
    """判断当前是否运行在 Docker 容器内。"""
    return os.path.exists("/.dockerenv")


def _get_astrbot_plugin_cache_dir() -> str:
    """获取默认媒体缓存目录；非 AstrBot 运行时回退到项目 cache 目录。"""
    try:
        from astrbot.core import astrbot_config
        data_dir = str(astrbot_config.get("data_dir") or "").strip()
        if data_dir:
            prefix = os.path.join(
                data_dir,
                "plugin_data",
                Config.PLUGIN_NAME,
            )
            return Config.build_cache_dir(prefix)
    except Exception:
        pass

    try:
        from astrbot.core.utils.io import get_astrbot_data_path
        prefix = os.path.join(
            get_astrbot_data_path(),
            "plugin_data",
            Config.PLUGIN_NAME,
        )
        return Config.build_cache_dir(prefix)
    except Exception:
        pass

    prefix = os.getcwd()
    return Config.build_cache_dir(prefix)


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
    pack_mode: str = PACK_MODE_NONE
    pack_image_threshold: int = 3
    pack_video_threshold: int = 2
    pack_node_threshold: int = 5
    quote_user_message: bool = False
    opening_enabled: bool = True
    opening_content: str = "流媒体解析bot为您服务 ٩( 'ω' )و"
    hot_comment_count: int = 0
    hot_comment_bilibili: bool = True
    hot_comment_weibo: bool = True
    hot_comment_xiaohongshu: bool = True
    parser_outputs: Dict[str, str] = field(default_factory=dict)

    def has_any_output(self) -> bool:
        """至少有一个解析器会发送文本元数据或富媒体。"""
        return any(
            any(OUTPUT_MODE_FLAGS.get(mode, (False, False)))
            for mode in self.parser_outputs.values()
        )

    def should_pack(
        self,
        image_count: int,
        video_count: int,
        node_count: int
    ) -> bool:
        """根据打包模式和实际节点数量判断是否发送消息集合。"""
        if self.pack_mode == PACK_MODE_ALL:
            return True
        if self.pack_mode != PACK_MODE_CONDITIONAL:
            return False

        thresholds = (
            (self.pack_image_threshold, image_count),
            (self.pack_video_threshold, video_count),
            (self.pack_node_threshold, node_count),
        )
        return any(
            threshold > 0 and count >= threshold
            for threshold, count in thresholds
        )

    def _flags_for_mode(self, mode: str) -> Tuple[bool, bool]:
        return OUTPUT_MODE_FLAGS.get(mode, OUTPUT_MODE_FLAGS[OUTPUT_MODE_ALL])

    def output_for_controller(self, controller: Any) -> Tuple[bool, bool]:
        """返回指定解析器的文本/富媒体发送开关。"""
        key = str(controller or "").strip()
        mode = self.parser_outputs.get(key, OUTPUT_MODE_ALL)
        return self._flags_for_mode(mode)

    def controller_has_any_output(self, controller: Any) -> bool:
        """指定解析器是否至少会发送一种输出。"""
        return any(self.output_for_controller(controller))

    def output_for_metadata(
        self,
        metadata: Dict[str, Any]
    ) -> Tuple[bool, bool]:
        """按 metadata 的平台名或解析器名返回有效输出开关。"""
        keys = [
            str(metadata.get("platform") or "").strip(),
            str(metadata.get("parser_name") or "").strip(),
        ]
        seen = set()
        for key in keys:
            if not key or key in seen:
                continue
            seen.add(key)
            if key in self.parser_outputs:
                return self._flags_for_mode(self.parser_outputs[key])
        return OUTPUT_MODE_FLAGS[OUTPUT_MODE_ALL]


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
    cache_dir_available: bool = False
    max_concurrent_downloads: int = Config.DOWNLOAD_MANAGER_MAX_CONCURRENT


@dataclass
class ProxyConfig:
    address: str = ""
    xiaoheihe_use_video_proxy: bool = True
    twitter_use_parse_proxy: bool = False
    twitter_use_image_proxy: bool = True
    twitter_use_video_proxy: bool = True
    tiktok_use_proxy: bool = False


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
class TranslationConfig:
    enabled: bool = False
    content_scope: str = "正文和标题"
    target_language: str = "简体中文"
    llm_provider_source: str = "astrbot"
    astrbot_provider_id: str = ""
    llm_provider: str = "openai_compatible"
    base_url: str = ""
    api_key: str = ""
    model: str = "gpt-5.5"
    temperature: float = 0.0
    max_completion_tokens: int = 4000
    request_timeout_seconds: int = 60
    max_text_chars_per_request: int = 4000


@dataclass
class AdminConfig:
    clean_cache_keyword: str = "清理媒体"
    debug_mode: bool = False


# ── 配置管理器 ──────────────────────────────────────────


class ConfigManager:

    """配置读取门面，向业务层提供类型安全的配置访问。"""
    def __init__(self, config: dict):
        self.bilibili_parser = None
        self._parse_config(config)

    # ── 内部解析 ────────────────────────────────────────

    def _parse_config(self, config: dict):
        """解析原始 dict，填充各领域配置分组。"""

        # --- trigger ---
        trigger_raw = config.get("trigger", {})
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

        # --- parsers/output modes ---
        parsers_raw = config.get("parsers", {})
        self.parser_outputs = self._parse_parser_outputs(parsers_raw)
        self._enable_bilibili = self._parser_enabled("bilibili")
        self._enable_douyin = self._parser_enabled("douyin")
        self._enable_tiktok = self._parser_enabled("tiktok")
        self._enable_kuaishou = self._parser_enabled("kuaishou")
        self._enable_weibo = self._parser_enabled("weibo")
        self._enable_xiaohongshu = self._parser_enabled("xiaohongshu")
        self._enable_xianyu = self._parser_enabled("xianyu")
        self._enable_toutiao = self._parser_enabled("toutiao")
        self._enable_xiaoheihe = self._parser_enabled("xiaoheihe")
        self._enable_twitter = self._parser_enabled("twitter")

        # --- message ---
        message_raw = config.get("message", {})
        if not isinstance(message_raw, dict):
            message_raw = {}
        opening = message_raw.get("opening", {})
        packing = message_raw.get("packing", {})
        text_metadata = message_raw.get("text_metadata", {})
        hot_comments = message_raw.get("hot_comments", {})
        if not isinstance(opening, dict):
            opening = {}
        if not isinstance(packing, dict):
            packing = {}
        if not isinstance(text_metadata, dict):
            text_metadata = {}
        pack_thresholds = packing.get("thresholds", {})
        if not isinstance(pack_thresholds, dict):
            pack_thresholds = {}
        if not isinstance(hot_comments, dict):
            hot_comments = {}

        hot_count = self._parse_non_negative_int(
            hot_comments.get("count", 0), 0
        )
        any_text_output_enabled = any(
            flags[0]
            for flags in (
                OUTPUT_MODE_FLAGS.get(mode, (False, False))
                for mode in self.parser_outputs.values()
            )
        )
        if not any_text_output_enabled:
            hot_count = 0

        self.message = MessageConfig(
            pack_mode=self._parse_pack_mode(
                packing.get("mode", PACK_MODE_NONE)
            ),
            pack_image_threshold=self._parse_non_negative_int(
                pack_thresholds.get("image_count", 3), 3
            ),
            pack_video_threshold=self._parse_non_negative_int(
                pack_thresholds.get("video_count", 2), 2
            ),
            pack_node_threshold=self._parse_non_negative_int(
                pack_thresholds.get("node_count", 5), 5
            ),
            quote_user_message=bool(
                text_metadata.get("quote_user_message", False)
            ),
            opening_enabled=opening.get("enable", True),
            opening_content=opening.get(
                "content", "流媒体解析bot为您服务 ٩( 'ω' )و"
            ),
            hot_comment_count=hot_count,
            hot_comment_bilibili=bool(hot_comments.get("bilibili", True)),
            hot_comment_weibo=bool(hot_comments.get("weibo", True)),
            hot_comment_xiaohongshu=bool(
                hot_comments.get("xiaohongshu", True)
            ),
            parser_outputs=self.parser_outputs,
        )
        if not self.message.has_any_output():
            logger.warning(
                "所有解析器输出均已关闭，插件将不会触发解析。"
            )

        # --- permissions ---
        permissions_raw = config.get("permissions", {})
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
        download_raw = config.get("download", {})

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

        configured_cache_dir = str(download_raw.get("cache_dir", "") or "").strip()
        if _is_docker_environment():
            cache_dir = configured_cache_dir or Config.DEFAULT_CACHE_DIR
        else:
            cache_dir = _get_astrbot_plugin_cache_dir()

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
        relay_raw = config.get("media_relay", {})
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

        # --- translation ---
        translation_raw = config.get("translation", {})
        if not isinstance(translation_raw, dict):
            translation_raw = {}
        translation_llm_raw = translation_raw.get("llm", {})
        if not isinstance(translation_llm_raw, dict):
            translation_llm_raw = {}
        astrbot_provider_raw = translation_llm_raw.get("astrbot_provider", {})
        if not isinstance(astrbot_provider_raw, dict):
            astrbot_provider_raw = {}
        custom_provider_raw = translation_llm_raw.get("custom_provider", {})
        if not isinstance(custom_provider_raw, dict):
            custom_provider_raw = {}

        llm_provider_source = self._normalize_llm_provider_source(
            translation_llm_raw.get(
                "provider_source",
                "AstrBot 内置提供商",
            )
        )
        llm_provider = self._normalize_llm_provider(
            custom_provider_raw.get("provider", "自定义 OpenAI 兼容")
        )
        provider_defaults = LLM_PROVIDER_DEFAULTS.get(
            llm_provider,
            LLM_PROVIDER_DEFAULTS["openai_compatible"],
        )
        base_url = str(
            custom_provider_raw.get("base_url", "") or ""
        ).strip().rstrip("/")
        if not base_url:
            base_url = str(provider_defaults.get("base_url", "") or "").strip().rstrip("/")

        self.translation = TranslationConfig(
            enabled=bool(translation_raw.get("enable", False)),
            content_scope=self._parse_translation_content_scope(
                translation_raw.get("content_scope", "正文和标题")
            ),
            target_language=self._parse_translation_target_language(
                translation_raw.get("target_language", "简体中文")
            ),
            llm_provider_source=llm_provider_source,
            astrbot_provider_id=str(
                astrbot_provider_raw.get("provider_id", "") or ""
            ).strip(),
            llm_provider=llm_provider,
            base_url=base_url,
            api_key=str(custom_provider_raw.get("api_key", "") or "").strip(),
            model=str(
                custom_provider_raw.get("model", "gpt-5.5") or "gpt-5.5"
            ).strip(),
        )

        cache_dir_available = check_cache_dir_available(cache_dir)
        if not cache_dir_available:
            logger.warning(
                f"媒体文件缓存目录不可用: {cache_dir}，"
                "视频将尽量使用直链发送，图片和必须写入缓存的媒体会被跳过。"
            )

        self.download = DownloadConfig(
            max_video_size_mb=max_video_size_mb,
            large_video_threshold_mb=large_video_threshold_mb,
            cache_dir=cache_dir,
            cache_dir_available=cache_dir_available,
            max_concurrent_downloads=max_concurrent,
        )

        # --- bilibili_enhanced ---
        bili = config.get("bilibili_enhanced", {})
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
        cookie_runtime_enabled = bool(use_cookie and cache_dir_available)

        runtime_file_name = "cookie.json"
        cookie_dir = Config.build_runtime_dir(cache_dir, "bilibili")
        cookie_runtime_file = os.path.join(cookie_dir, runtime_file_name)
        if use_cookie:
            try:
                os.makedirs(cookie_dir, exist_ok=True)
            except Exception as e:
                logger.warning(
                    f"B站Cookie运行时目录不可用，将旁路Cookie能力: {e}"
                )
                cookie_runtime_file = ""
                cookie_runtime_enabled = False

        if cookie_feature_requested and not cookie_runtime_enabled:
            logger.warning(
                '检测到已开启"是否携带Cookie解析视频"，但媒体文件缓存目录不可用，'
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

        # --- proxy ---
        proxy_raw = config.get("proxy", {})
        twitter_proxy = proxy_raw.get("twitter", {})
        self.proxy = ProxyConfig(
            address=proxy_raw.get("address", ""),
            xiaoheihe_use_video_proxy=proxy_raw.get("xiaoheihe_video", True),
            twitter_use_parse_proxy=twitter_proxy.get("parse", False),
            twitter_use_image_proxy=twitter_proxy.get("image", True),
            twitter_use_video_proxy=twitter_proxy.get("video", True),
            tiktok_use_proxy=proxy_raw.get("tiktok", False),
        )

        # --- admin ---
        admin_raw = config.get("admin", {})
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

    def _parser_enabled(self, parser_name: str) -> bool:
        return any(
            OUTPUT_MODE_FLAGS.get(
                self.parser_outputs.get(parser_name, OUTPUT_MODE_ALL),
                OUTPUT_MODE_FLAGS[OUTPUT_MODE_ALL],
            )
        )

    def _effective_hot_comment_count(
        self,
        enabled: bool,
        controller: str
    ) -> int:
        text_enabled, _ = self.message.output_for_controller(controller)
        if not text_enabled:
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
            self.message.hot_comment_bilibili,
            "bilibili",
        )
        weibo_hc = self._effective_hot_comment_count(
            self.message.hot_comment_weibo,
            "weibo",
        )
        xhs_hc = self._effective_hot_comment_count(
            self.message.hot_comment_xiaohongshu,
            "xiaohongshu",
        )
        proxy_addr = self.proxy.address or None

        if self._enable_bilibili:
            self.bilibili_parser = BilibiliParser(
                cookie_runtime_enabled=self.bilibili.cookie_runtime_enabled,
                configured_cookie=self.bilibili.cookie,
                max_quality=self.bilibili.max_quality,
                admin_assist_enabled=self.bilibili.enable_admin_assist,
                credential_path=self.bilibili.cookie_runtime_file,
                hot_comment_count=bili_hc,
            )
            parsers.append(self.bilibili_parser)
        if self._enable_douyin:
            parsers.append(DouyinParser())
        if self._enable_tiktok:
            parsers.append(TikTokParser(
                use_proxy=self.proxy.tiktok_use_proxy,
                proxy_url=proxy_addr,
            ))
        if self._enable_kuaishou:
            parsers.append(KuaishouParser())
        if self._enable_weibo:
            parsers.append(WeiboParser(hot_comment_count=weibo_hc))
        if self._enable_xiaohongshu:
            parsers.append(XiaohongshuParser(hot_comment_count=xhs_hc))
        if self._enable_xianyu:
            parsers.append(XianyuParser())
        if self._enable_toutiao:
            _, toutiao_rich_enabled = self.message.output_for_controller(
                "toutiao"
            )
            if toutiao_rich_enabled and self.download.cache_dir_available:
                parsers.append(ToutiaoParser())
            else:
                parsers.append(ToutiaoParser(article_image_refreshes=1))
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
    def _parse_parser_outputs(values) -> Dict[str, str]:
        if not isinstance(values, dict):
            values = {}

        normalized: Dict[str, str] = {}
        valid_modes = set(OUTPUT_MODE_FLAGS)
        for key in PARSER_OUTPUT_KEYS:
            raw_mode = values.get(key, OUTPUT_MODE_ALL)
            mode = str(raw_mode or OUTPUT_MODE_ALL).strip()
            if mode not in valid_modes:
                mode = OUTPUT_MODE_ALL
            normalized[key] = mode
        return normalized

    @staticmethod
    def _parse_pack_mode(value) -> str:
        mode = str(value or "").strip()
        if mode in PACK_MODES:
            return mode
        return PACK_MODE_NONE

    @staticmethod
    def _parse_translation_target_language(value) -> str:
        language = str(value or "").strip()
        if language in TRANSLATION_TARGET_LANGUAGES:
            return language
        return "简体中文"

    @staticmethod
    def _parse_translation_content_scope(value) -> str:
        scope = str(value or "").strip()
        if scope in TRANSLATION_CONTENT_SCOPES:
            return scope
        return "正文和标题"

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

    @staticmethod
    def _normalize_llm_provider_source(value: Any) -> str:
        text = str(value or "").strip() or "AstrBot 内置提供商"
        mapping = {
            "AstrBot 内置提供商": "astrbot",
            "AstrBot": "astrbot",
            "astrbot": "astrbot",
            "插件自定义提供商": "custom",
            "自定义提供商": "custom",
            "custom": "custom",
        }
        return mapping.get(text, "astrbot")

    @staticmethod
    def _normalize_llm_provider(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return "openai_compatible"
        if text in LLM_PROVIDER_DEFAULTS:
            return text
        if text in LLM_PROVIDER_OPTIONS:
            return LLM_PROVIDER_OPTIONS[text]
        lowered = text.lower()
        for label, key in LLM_PROVIDER_OPTIONS.items():
            if lowered == label.lower() or lowered == key.lower():
                return key
        return "openai_compatible"
