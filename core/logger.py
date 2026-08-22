"""日志初始化模块，导出全局可复用日志实例。"""

import logging


_PLUGIN_TAG = "astrbot_plugin_media_parser"


class _PluginTagFilter(logging.Filter):
    """补齐 AstrBot formatter 使用的 plugin_tag 字段。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "plugin_tag", None):
            record.plugin_tag = _PLUGIN_TAG
        return True


def _configure_plugin_logger(base_logger):
    # AstrBot logger 已携带插件上下文；创建 getChild 会丢失 plugin_tag。
    plugin_logger = base_logger

    if isinstance(plugin_logger, logging.LoggerAdapter):
        extra = dict(plugin_logger.extra or {})
        extra.setdefault("plugin_tag", _PLUGIN_TAG)
        plugin_logger.extra = extra

    add_filter = getattr(plugin_logger, "addFilter", None)
    if callable(add_filter):
        add_filter(_PluginTagFilter())
    else:
        underlying_logger = getattr(plugin_logger, "logger", None)
        underlying_add_filter = getattr(underlying_logger, "addFilter", None)
        if callable(underlying_add_filter):
            underlying_add_filter(_PluginTagFilter())
    return plugin_logger


try:
    from astrbot.api import logger as _astrbot_logger

    logger = _configure_plugin_logger(_astrbot_logger)
except ImportError:
    logger = logging.getLogger("astrbot_plugin_media_parser")
    logger.addFilter(_PluginTagFilter())
