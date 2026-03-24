"""日志初始化模块，导出全局可复用日志实例。"""
try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger("astrbot_plugin_media_parser")
