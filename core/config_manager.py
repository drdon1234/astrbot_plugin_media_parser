"""配置管理模块，负责默认值处理、类型转换与配置兜底。"""

import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

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
    SteamParser,
    TwitterParser,
    PixivParser,
    XueqiuParser,
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
    "steam",
    "twitter",
    "pixiv",
    "xueqiu",
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

AGGREGATION_MODE_NONE = "不聚合"
AGGREGATION_MODE_ALL = "全部聚合"
AGGREGATION_MODE_CONDITIONAL = "按条件聚合"
AGGREGATION_MODES = {
    AGGREGATION_MODE_NONE,
    AGGREGATION_MODE_ALL,
    AGGREGATION_MODE_CONDITIONAL,
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

TEXT_RENDER_STYLE_ALIASES = {
    "清新便签": "fresh",
    "清新": "fresh",
    "便签": "fresh",
    "粉色便签": "fresh",
    "fresh": "fresh",
    "bilinote": "fresh",
    "科技感": "tech",
    "科技": "tech",
    "tech": "tech",
    "technology": "tech",
    "专业严肃": "serious",
    "严肃": "serious",
    "专业": "serious",
    "serious": "serious",
    "professional": "serious",
    "温和卡片": "card",
    "卡片": "card",
    "card": "card",
    "soft_card": "card",
}

TEXT_RENDER_FONT_ALIASES = {
    "默认黑体": "noto_sans",
    "黑体": "noto_sans",
    "思源黑体": "noto_sans",
    "noto_sans": "noto_sans",
    "noto sans": "noto_sans",
    "default": "noto_sans",
    "专业宋体": "noto_serif",
    "宋体": "noto_serif",
    "思源宋体": "noto_serif",
    "noto_serif": "noto_serif",
    "noto serif": "noto_serif",
    "serif": "noto_serif",
    "清新文楷": "lxgw_wenkai",
    "文楷": "lxgw_wenkai",
    "霞鹜文楷": "lxgw_wenkai",
    "lxgw_wenkai": "lxgw_wenkai",
    "lxgw wenkai": "lxgw_wenkai",
    "wenkai": "lxgw_wenkai",
    "标题手札": "zcool_xiaowei",
    "站酷小薇": "zcool_xiaowei",
    "zcool_xiaowei": "zcool_xiaowei",
    "zcool xiaowei": "zcool_xiaowei",
    "xiaowei": "zcool_xiaowei",
    "科技窄体": "zcool_qingke",
    "站酷庆科黄油体": "zcool_qingke",
    "zcool_qingke": "zcool_qingke",
    "zcool qingke": "zcool_qingke",
    "qingke": "zcool_qingke",
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
class ParserOutputConfig:
    modes: Dict[str, str] = field(default_factory=dict)

    def has_any_output(self) -> bool:
        """至少有一个解析器会发送文本元数据或富媒体。"""
        return any(
            any(OUTPUT_MODE_FLAGS.get(mode, (False, False)))
            for mode in self.modes.values()
        )

    def has_any_text_output(self) -> bool:
        """至少有一个解析器会发送文本元数据。"""
        return any(
            OUTPUT_MODE_FLAGS.get(mode, (False, False))[0]
            for mode in self.modes.values()
        )

    def _flags_for_mode(self, mode: str) -> Tuple[bool, bool]:
        return OUTPUT_MODE_FLAGS.get(mode, OUTPUT_MODE_FLAGS[OUTPUT_MODE_DISABLED])

    def output_for_controller(self, controller: Any) -> Tuple[bool, bool]:
        """返回指定解析器的文本/富媒体发送开关。"""
        key = str(controller or "").strip()
        mode = self.modes.get(key, OUTPUT_MODE_ALL)
        return self._flags_for_mode(mode)

    def controller_has_any_output(self, controller: Any) -> bool:
        """指定解析器是否至少会发送一种输出。"""
        return any(self.output_for_controller(controller))

    def output_for_metadata(self, metadata: Dict[str, Any]) -> Tuple[bool, bool]:
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
            if key in self.modes:
                return self._flags_for_mode(self.modes[key])
        return OUTPUT_MODE_FLAGS[OUTPUT_MODE_ALL]


@dataclass
class OpeningMessageConfig:
    enabled: bool = True
    content: str = Config.DEFAULT_OPENING_CONTENT
    archive_content: str = Config.DEFAULT_ARCHIVE_OPENING_CONTENT


@dataclass
class AggregationConfig:
    mode: str = AGGREGATION_MODE_NONE
    image_threshold: int = 3
    video_threshold: int = 2
    node_threshold: int = 5

    def should_aggregate_nodes(
        self,
        image_count: int,
        video_count: int,
        node_count: int,
    ) -> bool:
        """根据聚合模式和实际节点数量判断是否发送合并转发消息。"""
        if self.mode == AGGREGATION_MODE_ALL:
            return True
        if self.mode != AGGREGATION_MODE_CONDITIONAL:
            return False

        thresholds = (
            (self.image_threshold, image_count),
            (self.video_threshold, video_count),
            (self.node_threshold, node_count),
        )
        return any(
            threshold > 0 and count >= threshold for threshold, count in thresholds
        )


@dataclass
class ArchiveConfig:
    command: str = ""
    max_total_size_mb: float = 1024.0


@dataclass
class MediaDisplayConfig:
    video_cover_only: bool = False


@dataclass
class TextMetadataConfig:
    show_title: bool = True
    show_author: bool = True
    show_timestamp: bool = True
    show_original_link: bool = True
    show_description: bool = True
    quote_user_message: bool = False
    render_to_image: bool = False
    render_style: str = "fresh"
    render_font_family: str = "noto_sans"
    render_font_size: int = 24

    def visibility(self) -> Dict[str, bool]:
        """返回写入 metadata 的稳定字段名与展示开关。"""
        return {
            "title": self.show_title,
            "author": self.show_author,
            "timestamp": self.show_timestamp,
            "original_link": self.show_original_link,
            "description": self.show_description,
        }


@dataclass
class HotCommentConfig:
    count: int = 0
    bilibili: bool = True
    weibo: bool = True
    xiaohongshu: bool = True


@dataclass
class MessageConfig:
    opening: OpeningMessageConfig = field(default_factory=OpeningMessageConfig)
    aggregation: AggregationConfig = field(default_factory=AggregationConfig)
    archive: ArchiveConfig = field(default_factory=ArchiveConfig)
    media_display: MediaDisplayConfig = field(default_factory=MediaDisplayConfig)
    text_metadata: TextMetadataConfig = field(default_factory=TextMetadataConfig)
    hot_comments: HotCommentConfig = field(default_factory=HotCommentConfig)


@dataclass
class PermissionConfig:
    configuration_valid: bool = True
    admin_id: str = ""
    whitelist_enable: bool = False
    whitelist_user: List[str] = field(default_factory=list)
    whitelist_group: List[str] = field(default_factory=list)
    blacklist_enable: bool = False
    blacklist_user: List[str] = field(default_factory=list)
    blacklist_group: List[str] = field(default_factory=list)

    def check(self, is_private: bool, sender_id: Any, group_id: Any) -> bool:
        """检查用户或群组是否有权限使用解析"""
        if not self.configuration_valid:
            return False
        sender_id_str = str(sender_id or "").strip()
        group_id_str = "" if is_private else str(group_id or "").strip()

        if self.admin_id and sender_id_str == self.admin_id:
            return True

        allowed = None
        if self.whitelist_enable and sender_id_str in self.whitelist_user:
            allowed = True
        elif self.blacklist_enable and sender_id_str in self.blacklist_user:
            allowed = False
        elif (
            self.whitelist_enable
            and group_id_str
            and group_id_str in self.whitelist_group
        ):
            allowed = True
        elif (
            self.blacklist_enable
            and group_id_str
            and group_id_str in self.blacklist_group
        ):
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
class ParseRateLimitRuleConfig:
    max_count: int = 0
    window_seconds: int = 3600

    @property
    def enabled(self) -> bool:
        return self.max_count > 0 and self.window_seconds > 0


@dataclass
class ParseRateLimitConfig:
    same_link: ParseRateLimitRuleConfig = field(
        default_factory=ParseRateLimitRuleConfig
    )
    same_user: ParseRateLimitRuleConfig = field(
        default_factory=ParseRateLimitRuleConfig
    )
    record_file: str = ""

    @property
    def enabled(self) -> bool:
        return self.same_link.enabled or self.same_user.enabled

    @property
    def retention_seconds(self) -> int:
        windows = [
            rule.window_seconds
            for rule in (self.same_link, self.same_user)
            if rule.enabled
        ]
        return max(windows) if windows else 0


@dataclass
class ProxyConfig:
    address: str = ""
    xiaoheihe_use_video_proxy: bool = True
    steam_use_parse_proxy: bool = False
    steam_use_image_proxy: bool = True
    steam_use_video_proxy: bool = True
    twitter_use_parse_proxy: bool = False
    twitter_use_image_proxy: bool = True
    twitter_use_video_proxy: bool = True
    tiktok_use_proxy: bool = False
    pixiv_use_proxy: bool = False


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
    admin_cookie_update_command: str = "B站更新Cookie"


@dataclass
class PixivConfig:
    cookie: str = ""


@dataclass
class SteamConfig:
    use_xiaoheihe: bool = False


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
        if not isinstance(config, dict):
            logger.warning("插件根配置不是对象，已安全关闭解析并拒绝所有消息")
            config = {
                "trigger": None,
                "parsers": None,
                "permissions": None,
            }
        self._migrate_message_config(config)
        self._parse_config(config)

    # ── 内部解析 ────────────────────────────────────────

    def _parse_config(self, config: dict):
        """解析原始 dict，填充各领域配置分组。"""

        # --- trigger ---
        trigger_config_valid = "trigger" not in config or isinstance(
            config.get("trigger"), dict
        )
        if trigger_config_valid:
            trigger_raw = self._as_dict(config.get("trigger"))
        else:
            logger.warning("trigger 配置存在但不是对象，已安全关闭全部解析触发")
            trigger_raw = {
                "auto_parse": False,
                "keywords": [],
                "reply_trigger": False,
            }
        self.trigger = TriggerConfig(
            auto_parse=self._parse_bool(
                trigger_raw.get("auto_parse", True),
                True,
                "trigger.auto_parse",
            ),
            keywords=self._normalize_string_list(
                trigger_raw.get("keywords", ["视频解析", "解析视频"])
            ),
            reply_trigger=self._parse_bool(
                trigger_raw.get("reply_trigger", False),
                False,
                "trigger.reply_trigger",
            ),
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
        if "parsers" not in config:
            parsers_raw = {}
        elif isinstance(config.get("parsers"), dict):
            parsers_raw = config["parsers"]
        else:
            logger.warning("parsers 配置存在但不是对象，已安全关闭全部解析器")
            parsers_raw = {key: OUTPUT_MODE_DISABLED for key in PARSER_OUTPUT_KEYS}
        self.parser_output = ParserOutputConfig(
            modes=self._parse_parser_outputs(parsers_raw)
        )
        self._enable_bilibili = self._parser_enabled("bilibili")
        self._enable_douyin = self._parser_enabled("douyin")
        self._enable_tiktok = self._parser_enabled("tiktok")
        self._enable_kuaishou = self._parser_enabled("kuaishou")
        self._enable_weibo = self._parser_enabled("weibo")
        self._enable_xiaohongshu = self._parser_enabled("xiaohongshu")
        self._enable_xianyu = self._parser_enabled("xianyu")
        self._enable_toutiao = self._parser_enabled("toutiao")
        self._enable_xiaoheihe = self._parser_enabled("xiaoheihe")
        self._enable_steam = self._parser_enabled("steam")
        self._enable_twitter = self._parser_enabled("twitter")
        self._enable_pixiv = self._parser_enabled("pixiv")
        self._enable_xueqiu = self._parser_enabled("xueqiu")

        # --- message ---
        message_raw = self._as_dict(config.get("message"))
        opening = self._as_dict(message_raw.get("opening"))
        aggregation = self._as_dict(message_raw.get("packing"))
        archive = self._as_dict(message_raw.get("archive"))
        text_metadata = self._as_dict(message_raw.get("text_metadata"))
        media_display = self._as_dict(message_raw.get("media_display"))
        hot_comments = self._as_dict(message_raw.get("hot_comments"))
        aggregation_thresholds = self._as_dict(aggregation.get("thresholds"))

        hot_count = self._parse_non_negative_int(hot_comments.get("count", 0), 0)
        any_text_output_enabled = self.parser_output.has_any_text_output()
        if not any_text_output_enabled:
            hot_count = 0

        self.message = MessageConfig(
            opening=OpeningMessageConfig(
                enabled=self._parse_bool(
                    opening.get("enable", True),
                    True,
                    "message.opening.enable",
                ),
                content=str(
                    opening.get(
                        "content",
                        Config.DEFAULT_OPENING_CONTENT,
                    )
                    or Config.DEFAULT_OPENING_CONTENT
                ),
                archive_content=str(
                    opening.get(
                        "archive_content",
                        Config.DEFAULT_ARCHIVE_OPENING_CONTENT,
                    )
                    or Config.DEFAULT_ARCHIVE_OPENING_CONTENT
                ),
            ),
            aggregation=AggregationConfig(
                mode=self._parse_aggregation_mode(
                    aggregation.get("mode", AGGREGATION_MODE_NONE)
                ),
                image_threshold=self._parse_non_negative_int(
                    aggregation_thresholds.get("image_count", 3), 3
                ),
                video_threshold=self._parse_non_negative_int(
                    aggregation_thresholds.get("video_count", 2), 2
                ),
                node_threshold=self._parse_non_negative_int(
                    aggregation_thresholds.get("node_count", 5), 5
                ),
            ),
            archive=ArchiveConfig(
                command=str(archive.get("command", "") or "").strip(),
                max_total_size_mb=min(
                    4096.0,
                    max(
                        1.0,
                        self._parse_non_negative_float(
                            archive.get("max_total_size_mb", 1024.0),
                            1024.0,
                        ),
                    ),
                ),
            ),
            media_display=MediaDisplayConfig(
                video_cover_only=self._parse_bool(
                    media_display.get("video_cover_only", False),
                    False,
                    "message.media_display.video_cover_only",
                ),
            ),
            text_metadata=TextMetadataConfig(
                show_title=self._parse_bool(
                    text_metadata.get("show_title", True),
                    True,
                    "message.text_metadata.show_title",
                ),
                show_author=self._parse_bool(
                    text_metadata.get("show_author", True),
                    True,
                    "message.text_metadata.show_author",
                ),
                show_timestamp=self._parse_bool(
                    text_metadata.get("show_timestamp", True),
                    True,
                    "message.text_metadata.show_timestamp",
                ),
                show_original_link=self._parse_bool(
                    text_metadata.get("show_original_link", True),
                    True,
                    "message.text_metadata.show_original_link",
                ),
                show_description=self._parse_bool(
                    text_metadata.get("show_description", True),
                    True,
                    "message.text_metadata.show_description",
                ),
                quote_user_message=self._parse_bool(
                    text_metadata.get("quote_user_message", False),
                    False,
                    "message.text_metadata.quote_user_message",
                ),
                render_to_image=self._parse_bool(
                    text_metadata.get("render_to_image", False),
                    False,
                    "message.text_metadata.render_to_image",
                ),
                render_style=self._parse_text_render_style(
                    text_metadata.get("render_style", "清新便签")
                ),
                render_font_family=self._parse_text_render_font_family(
                    text_metadata.get("render_font_family", "默认黑体")
                ),
                render_font_size=min(
                    42,
                    max(
                        16,
                        self._parse_non_negative_int(
                            text_metadata.get("render_font_size", 24),
                            24,
                        ),
                    ),
                ),
            ),
            hot_comments=HotCommentConfig(
                count=hot_count,
                bilibili=self._parse_bool(
                    hot_comments.get("bilibili", True),
                    True,
                    "message.hot_comments.bilibili",
                ),
                weibo=self._parse_bool(
                    hot_comments.get("weibo", True),
                    True,
                    "message.hot_comments.weibo",
                ),
                xiaohongshu=self._parse_bool(
                    hot_comments.get("xiaohongshu", True),
                    True,
                    "message.hot_comments.xiaohongshu",
                ),
            ),
        )
        if not self.parser_output.has_any_output():
            logger.warning("所有解析器输出均已关闭，插件将不会触发解析。")

        # --- permissions ---
        permission_config_valid = "permissions" not in config or isinstance(
            config.get("permissions"), dict
        )
        permissions_raw = self._as_dict(config.get("permissions"))
        for subsection_name in ("whitelist", "blacklist"):
            if subsection_name in permissions_raw and not isinstance(
                permissions_raw.get(subsection_name), dict
            ):
                permission_config_valid = False
        whitelist = self._as_dict(permissions_raw.get("whitelist"))
        blacklist = self._as_dict(permissions_raw.get("blacklist"))
        for section in (whitelist, blacklist):
            if "enable" in section and self._coerce_bool(section.get("enable")) is None:
                permission_config_valid = False
            for list_name in ("user", "group"):
                if list_name in section and not isinstance(
                    section.get(list_name), list
                ):
                    permission_config_valid = False
        if not permission_config_valid:
            logger.warning(
                "permissions 配置结构或开关值无效，已拒绝所有消息；请修复配置后重载插件"
            )
        admin_id = str(permissions_raw.get("admin_id", "") or "").strip()
        wl_user = self._normalize_id_list(whitelist.get("user", []))
        if admin_id and admin_id not in wl_user:
            wl_user.append(admin_id)

        self.permission = PermissionConfig(
            configuration_valid=permission_config_valid,
            admin_id=admin_id,
            whitelist_enable=self._parse_bool(
                whitelist.get("enable", False),
                False,
                "permissions.whitelist.enable",
            ),
            whitelist_user=wl_user,
            whitelist_group=self._normalize_id_list(whitelist.get("group", [])),
            blacklist_enable=self._parse_bool(
                blacklist.get("enable", False),
                False,
                "permissions.blacklist.enable",
            ),
            blacklist_user=self._normalize_id_list(blacklist.get("user", [])),
            blacklist_group=self._normalize_id_list(blacklist.get("group", [])),
        )

        # --- download ---
        download_raw = self._as_dict(config.get("download"))

        max_video_size_mb = self._parse_non_negative_float(
            download_raw.get("max_video_size_mb", 1000.0), 1000.0
        )
        large_video_threshold_mb = self._parse_non_negative_float(
            download_raw.get(
                "large_video_threshold_mb", Config.MAX_LARGE_VIDEO_THRESHOLD_MB
            ),
            Config.MAX_LARGE_VIDEO_THRESHOLD_MB,
        )
        if large_video_threshold_mb > 0:
            large_video_threshold_mb = min(
                large_video_threshold_mb, Config.MAX_LARGE_VIDEO_THRESHOLD_MB
            )

        configured_cache_dir = str(download_raw.get("cache_dir", "") or "").strip()
        if _is_docker_environment():
            cache_dir = configured_cache_dir or Config.DEFAULT_CACHE_DIR
        else:
            cache_dir = _get_astrbot_plugin_cache_dir()

        max_concurrent = min(
            self._parse_positive_int(
                download_raw.get(
                    "max_concurrent", Config.DOWNLOAD_MANAGER_MAX_CONCURRENT
                ),
                Config.DOWNLOAD_MANAGER_MAX_CONCURRENT,
            ),
            20,
        )

        # --- media_relay ---
        relay_raw = self._as_dict(config.get("media_relay"))
        self.relay = MediaRelayConfig(
            enabled=self._parse_bool(
                relay_raw.get("enable", False),
                False,
                "media_relay.enable",
            ),
            callback_api_base=str(relay_raw.get("callback_url", "") or "")
            .strip()
            .rstrip("/"),
            file_token_ttl=max(
                30, self._parse_positive_int(relay_raw.get("ttl", 300), 300)
            ),
        )

        # --- translation ---
        translation_raw = self._as_dict(config.get("translation"))
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
        base_url = (
            str(custom_provider_raw.get("base_url", "") or "").strip().rstrip("/")
        )
        if not base_url:
            base_url = (
                str(provider_defaults.get("base_url", "") or "").strip().rstrip("/")
            )

        self.translation = TranslationConfig(
            enabled=self._parse_bool(
                translation_raw.get("enable", False),
                False,
                "translation.enable",
            ),
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
            model=str(custom_provider_raw.get("model", "gpt-5.5") or "gpt-5.5").strip(),
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

        # --- parse_rate_limit ---
        rate_limit_raw = self._as_dict(config.get("parse_rate_limit"))
        self.parse_rate_limit = ParseRateLimitConfig(
            same_link=self._parse_rate_limit_rule(rate_limit_raw.get("same_link", {})),
            same_user=self._parse_rate_limit_rule(rate_limit_raw.get("same_user", {})),
            record_file=os.path.join(
                Config.build_runtime_dir(cache_dir, "parse_records"),
                "records.json",
            )
            if cache_dir
            else "",
        )

        # --- bilibili_enhanced ---
        bili = config.get("bilibili_enhanced", {})
        if not isinstance(bili, dict):
            bili = {}

        use_cookie = self._parse_bool(
            bili.get("use_cookie", False),
            False,
            "bilibili_enhanced.use_cookie",
        )
        if use_cookie:
            cookie = str(bili.get("cookie", "") or "").strip()
            max_quality_label = str(
                bili.get("max_quality", "不限制") or "不限制"
            ).strip()
            if max_quality_label in BILIBILI_QUALITY_MAP:
                max_quality = BILIBILI_QUALITY_MAP[max_quality_label]
            else:
                max_quality = BILIBILI_QUALITY_MAP["720P"]
                logger.warning(
                    f"无效的B站最大画质配置 {max_quality_label!r}，"
                    "已按安全默认值720P处理"
                )
            admin_assist_raw = bili.get("admin_assist", {})
            if not isinstance(admin_assist_raw, dict):
                admin_assist_raw = {}
            enable_admin_assist = self._parse_bool(
                admin_assist_raw.get("enable", False),
                False,
                "bilibili_enhanced.admin_assist.enable",
            )
            admin_reply_timeout = self._parse_positive_int(
                admin_assist_raw.get("reply_timeout_minutes", 1440), 1440
            )
            admin_request_cooldown = self._parse_positive_int(
                admin_assist_raw.get("request_cooldown_minutes", 1440), 1440
            )
            admin_cookie_update_command = str(
                admin_assist_raw.get("command", "B站更新Cookie") or ""
            ).strip()
        else:
            cookie = ""
            max_quality = 0
            enable_admin_assist = False
            admin_reply_timeout = 1440
            admin_request_cooldown = 1440
            admin_cookie_update_command = "B站更新Cookie"

        cookie_feature_requested = use_cookie
        cookie_runtime_enabled = bool(use_cookie and cache_dir_available)

        runtime_file_name = "cookie.json"
        cookie_dir = Config.build_runtime_dir(cache_dir, "bilibili")
        cookie_runtime_file = os.path.join(cookie_dir, runtime_file_name)
        if use_cookie:
            try:
                os.makedirs(cookie_dir, exist_ok=True)
            except Exception as e:
                logger.warning(f"B站Cookie运行时目录不可用，将旁路Cookie能力: {e}")
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
            admin_cookie_update_command=admin_cookie_update_command,
        )

        # --- pixiv ---
        pixiv_raw = config.get("pixiv", {})
        if not isinstance(pixiv_raw, dict):
            pixiv_raw = {}
        self.pixiv = PixivConfig(
            cookie=str(pixiv_raw.get("cookie", "") or "").strip(),
        )

        # --- steam ---
        steam_raw = config.get("steam", {})
        if not isinstance(steam_raw, dict):
            steam_raw = {}
        self.steam = SteamConfig(
            use_xiaoheihe=self._parse_bool(
                steam_raw.get("use_xiaoheihe", False),
                False,
                "steam.use_xiaoheihe",
            ),
        )

        # --- proxy ---
        proxy_raw = self._as_dict(config.get("proxy"))
        steam_proxy = self._as_dict(proxy_raw.get("steam"))
        twitter_proxy = self._as_dict(proxy_raw.get("twitter"))
        self.proxy = ProxyConfig(
            address=str(proxy_raw.get("address", "") or "").strip(),
            xiaoheihe_use_video_proxy=self._parse_bool(
                proxy_raw.get("xiaoheihe_video", True),
                True,
                "proxy.xiaoheihe_video",
            ),
            steam_use_parse_proxy=self._parse_bool(
                steam_proxy.get("parse", False),
                False,
                "proxy.steam.parse",
            ),
            steam_use_image_proxy=self._parse_bool(
                steam_proxy.get("image", True),
                True,
                "proxy.steam.image",
            ),
            steam_use_video_proxy=self._parse_bool(
                steam_proxy.get("video", True),
                True,
                "proxy.steam.video",
            ),
            twitter_use_parse_proxy=self._parse_bool(
                twitter_proxy.get("parse", False),
                False,
                "proxy.twitter.parse",
            ),
            twitter_use_image_proxy=self._parse_bool(
                twitter_proxy.get("image", True),
                True,
                "proxy.twitter.image",
            ),
            twitter_use_video_proxy=self._parse_bool(
                twitter_proxy.get("video", True),
                True,
                "proxy.twitter.video",
            ),
            tiktok_use_proxy=self._parse_bool(
                proxy_raw.get("tiktok", False),
                False,
                "proxy.tiktok",
            ),
            pixiv_use_proxy=self._parse_bool(
                proxy_raw.get("pixiv", False),
                False,
                "proxy.pixiv",
            ),
        )

        # --- admin ---
        admin_raw = self._as_dict(config.get("admin"))
        self.admin = AdminConfig(
            clean_cache_keyword=str(
                admin_raw.get("clean_cache_keyword", "清理媒体") or "清理媒体"
            ).strip(),
            debug_mode=self._parse_bool(
                admin_raw.get("debug", False),
                False,
                "admin.debug",
            ),
        )
        import logging

        logger.setLevel(logging.DEBUG if self.admin.debug_mode else logging.NOTSET)
        if self.admin.debug_mode:
            logger.debug("Debug模式已启用")

        if (
            self.message.archive.command
            and self.message.archive.command == self.admin.clean_cache_keyword
        ):
            logger.warning(
                "引用链接归档命令与清缓存命令冲突，已禁用归档命令；请配置两个不同命令"
            )
            self.message.archive.command = ""

    # ── 工厂方法 ────────────────────────────────────────

    def _parser_enabled(self, parser_name: str) -> bool:
        return self.parser_output.controller_has_any_output(parser_name)

    def _effective_hot_comment_count(self, enabled: bool, controller: str) -> int:
        text_enabled, _ = self.parser_output.output_for_controller(controller)
        if not text_enabled:
            return 0
        if not enabled:
            return 0
        return self.message.hot_comments.count

    def create_parsers(self) -> List:
        """根据配置创建并返回解析器列表。"""
        parsers = []
        bili_hc = self._effective_hot_comment_count(
            self.message.hot_comments.bilibili,
            "bilibili",
        )
        weibo_hc = self._effective_hot_comment_count(
            self.message.hot_comments.weibo,
            "weibo",
        )
        xhs_hc = self._effective_hot_comment_count(
            self.message.hot_comments.xiaohongshu,
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
            parsers.append(
                TikTokParser(
                    use_proxy=self.proxy.tiktok_use_proxy,
                    proxy_url=proxy_addr,
                )
            )
        if self._enable_kuaishou:
            parsers.append(KuaishouParser())
        if self._enable_weibo:
            parsers.append(WeiboParser(hot_comment_count=weibo_hc))
        if self._enable_xiaohongshu:
            parsers.append(XiaohongshuParser(hot_comment_count=xhs_hc))
        if self._enable_xianyu:
            parsers.append(XianyuParser())
        if self._enable_toutiao:
            _, toutiao_rich_enabled = self.parser_output.output_for_controller(
                "toutiao"
            )
            if toutiao_rich_enabled and self.download.cache_dir_available:
                parsers.append(ToutiaoParser())
            else:
                parsers.append(ToutiaoParser(article_image_refreshes=1))
        if self._enable_xiaoheihe:
            parsers.append(
                XiaoheiheParser(
                    use_video_proxy=self.proxy.xiaoheihe_use_video_proxy,
                    proxy_url=proxy_addr,
                )
            )
        if self._enable_steam:
            parsers.append(
                SteamParser(
                    use_xiaoheihe=self.steam.use_xiaoheihe,
                    use_parse_proxy=self.proxy.steam_use_parse_proxy,
                    use_image_proxy=self.proxy.steam_use_image_proxy,
                    use_video_proxy=self.proxy.steam_use_video_proxy,
                    xiaoheihe_use_video_proxy=self.proxy.xiaoheihe_use_video_proxy,
                    proxy_url=proxy_addr,
                )
            )
        if self._enable_twitter:
            parsers.append(
                TwitterParser(
                    use_parse_proxy=self.proxy.twitter_use_parse_proxy,
                    use_image_proxy=self.proxy.twitter_use_image_proxy,
                    use_video_proxy=self.proxy.twitter_use_video_proxy,
                    proxy_url=proxy_addr,
                )
            )
        if self._enable_pixiv:
            parsers.append(
                PixivParser(
                    cookie=self.pixiv.cookie,
                    proxy=proxy_addr if self.proxy.pixiv_use_proxy else None,
                )
            )
        if self._enable_xueqiu:
            parsers.append(XueqiuParser())

        return parsers

    # ── 静态辅助 ────────────────────────────────────────

    @staticmethod
    def _parse_parser_outputs(values) -> Dict[str, str]:
        if not isinstance(values, dict):
            values = {}

        normalized: Dict[str, str] = {}
        valid_modes = set(OUTPUT_MODE_FLAGS)
        for key in PARSER_OUTPUT_KEYS:
            if key not in values:
                normalized[key] = OUTPUT_MODE_ALL
                continue
            raw_mode = values.get(key)
            mode = str(raw_mode).strip() if raw_mode is not None else ""
            if mode not in valid_modes:
                logger.warning(f"解析器 {key} 的输出模式 {raw_mode!r} 无效，已安全关闭")
                mode = OUTPUT_MODE_DISABLED
            normalized[key] = mode
        return normalized

    @staticmethod
    def _parse_aggregation_mode(value) -> str:
        mode = str(value or "").strip()
        if mode in AGGREGATION_MODES:
            return mode
        if mode:
            logger.warning(f"无效的消息聚合模式 {mode!r}，已禁用聚合")
        return AGGREGATION_MODE_NONE

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
    def _parse_text_render_style(value: Any) -> str:
        text = str(value or "").strip()
        lowered = text.casefold()
        return TEXT_RENDER_STYLE_ALIASES.get(
            text,
            TEXT_RENDER_STYLE_ALIASES.get(lowered, "fresh"),
        )

    @staticmethod
    def _parse_text_render_font_family(value: Any) -> str:
        text = str(value or "").strip()
        lowered = text.casefold()
        return TEXT_RENDER_FONT_ALIASES.get(
            text,
            TEXT_RENDER_FONT_ALIASES.get(lowered, "noto_sans"),
        )

    @staticmethod
    def _parse_positive_int(value, default: int) -> int:
        try:
            return max(1, int(value))
        except (OverflowError, TypeError, ValueError):
            return max(1, int(default))

    @staticmethod
    def _parse_non_negative_float(value, default: float) -> float:
        try:
            parsed = float(value)
            if not math.isfinite(parsed):
                raise ValueError("数值必须有限")
            return max(0.0, parsed)
        except (TypeError, ValueError):
            return max(0.0, float(default))

    @staticmethod
    def _parse_non_negative_int(value, default: int) -> int:
        try:
            return max(0, int(value))
        except (OverflowError, TypeError, ValueError):
            return max(0, int(default))

    @staticmethod
    def _coerce_bool(value: Any) -> Optional[bool]:
        """把明确的布尔表示归一化；无法识别时返回 ``None``。"""
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        return None

    @classmethod
    def _parse_bool(cls, value: Any, default: bool, field_name: str) -> bool:
        """严格解析布尔配置，避免 ``bool("false")`` 被当成开启。"""
        parsed = cls._coerce_bool(value)
        if parsed is not None:
            return parsed
        logger.warning(
            f"布尔配置 {field_name} 的值 {value!r} 无效，"
            f"已安全按关闭处理（字段缺省值为 {default!r}）"
        )
        return False

    @classmethod
    def _parse_rate_limit_rule(cls, value) -> ParseRateLimitRuleConfig:
        if not isinstance(value, dict):
            value = {}
        return ParseRateLimitRuleConfig(
            max_count=cls._parse_non_negative_int(value.get("max_count", 0), 0),
            window_seconds=cls._parse_non_negative_int(
                value.get("window_seconds", 3600),
                3600,
            ),
        )

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
    def _normalize_string_list(values: Any) -> List[str]:
        """仅保留非空字符串，避免空关键词全量命中或类型异常。"""
        if not isinstance(values, list):
            return []
        normalized: List[str] = []
        seen = set()
        for value in values:
            if not isinstance(value, str):
                continue
            text = value.strip()
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return normalized

    @staticmethod
    def _as_dict(value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @classmethod
    def _migrate_message_config(cls, config: Dict[str, Any]) -> None:
        """在保留持久化键的前提下迁移旧模式值和归档命令。"""
        message = cls._as_dict(config.get("message"))
        packing = cls._as_dict(message.get("packing"))
        if not packing:
            return

        changed = False
        legacy_mode_map = {
            "不打包": AGGREGATION_MODE_NONE,
            "全部打包": AGGREGATION_MODE_ALL,
            "按条件打包": AGGREGATION_MODE_CONDITIONAL,
        }
        current_mode = str(packing.get("mode", "") or "").strip()
        migrated_mode = legacy_mode_map.get(current_mode)
        if migrated_mode:
            packing["mode"] = migrated_mode
            changed = True

        archive = cls._as_dict(message.get("archive"))
        legacy_command = str(packing.get("zip_command", "") or "").strip()
        if legacy_command:
            if not str(archive.get("command", "") or "").strip():
                archive["command"] = legacy_command
            packing["zip_command"] = ""
            changed = True
        message["packing"] = packing
        message["archive"] = archive
        config["message"] = message

        if not changed:
            return
        save_config = getattr(config, "save_config", None)
        if callable(save_config):
            try:
                save_config()
            except Exception as exc:
                logger.warning(f"保存归档配置迁移结果失败: {exc}")

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
