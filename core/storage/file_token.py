"""文件 Token 服务集成，将已下载媒体注册为可回调的临时 URL。"""
import os
from typing import Any, Dict, List, Optional

from ..logger import logger


async def register_files_with_token_service(
    metadata: Dict[str, Any],
    callback_api_base: str,
    file_token_ttl: int,
) -> None:
    """将已下载的媒体文件注册到 AstrBot 文件 Token 服务。

    无论注册是否成功，都会设置 use_file_token_service 标志，
    确保节点构建时不会回退到 fromFileSystem（临时目录下的文件
    对消息平台不可达）。注册失败时回退到原始直链。
    """
    metadata['use_file_token_service'] = True

    file_paths = metadata.get('file_paths', [])
    if not file_paths or metadata.get('error'):
        return

    try:
        from astrbot.core import file_token_service, astrbot_config
    except ImportError:
        logger.warning(
            "无法导入astrbot.core的file_token_service，"
            "文件Token服务不可用，将回退为直链模式"
        )
        return

    if not callback_api_base:
        callback_api_base = str(
            astrbot_config.get("callback_api_base") or ""
        ).strip().rstrip("/")
    if not callback_api_base:
        logger.warning(
            "文件Token服务模式已启用，但未配置回调地址"
            "（插件配置 callback_api_base 或 AstrBot 全局 callback_api_base 均为空），"
            "将回退为直链模式"
        )
        return

    file_token_urls: List[Optional[str]] = []
    for fp in file_paths:
        if fp and os.path.exists(fp):
            try:
                token = await file_token_service.register_file(
                    fp, timeout=file_token_ttl
                )
                url = f"{callback_api_base}/api/file/{token}"
                file_token_urls.append(url)
                logger.debug(f"已注册文件到Token服务: {fp} -> {url}")
            except Exception as e:
                logger.warning(f"注册文件到Token服务失败: {fp}, 错误: {e}")
                file_token_urls.append(None)
        else:
            file_token_urls.append(None)

    metadata['file_token_urls'] = file_token_urls
