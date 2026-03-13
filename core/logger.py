try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger("astrbot_plugin_media_parser")
