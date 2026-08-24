"""文件 Token 服务集成，将已下载媒体注册为可回调的临时 URL。"""
import os
from typing import Any, Dict, List, Optional

from ..logger import logger


async def register_file_with_token_service(
    file_path: str,
    callback_api_base: str,
    file_token_ttl: int,
) -> Optional[str]:
    """将单个本地文件注册为可回调的临时 URL。"""
    if not file_path or not os.path.isfile(file_path):
        return None

    try:
        from astrbot.core import file_token_service, astrbot_config
    except ImportError:
        logger.warning(
            "无法导入astrbot.core的file_token_service，"
            "文件Token服务不可用，将回退为本地文件发送"
        )
        return None

    callback_api_base = str(callback_api_base or "").strip().rstrip("/")
    if not callback_api_base:
        callback_api_base = str(
            astrbot_config.get("callback_api_base") or ""
        ).strip().rstrip("/")
    if not callback_api_base:
        logger.warning(
            "文件Token服务已启用但没有可用回调地址，将回退为本地文件发送"
        )
        return None

    try:
        token = await file_token_service.register_file(
            file_path,
            timeout=file_token_ttl,
        )
    except Exception as exc:
        logger.warning(f"注册文件到Token服务失败: {file_path}, 错误: {exc}")
        return None

    url = f"{callback_api_base}/api/file/{token}"
    logger.debug(f"已注册文件到Token服务: {file_path}")
    return url


async def register_files_with_token_service(
    metadata: Dict[str, Any],
    callback_api_base: str,
    file_token_ttl: int,
) -> None:
    """将已下载的媒体文件注册到 AstrBot 文件 Token 服务。

    Token 服务只增强已经缓存到本地文件的媒体。注册失败不会改变解析结果，
    节点构建时会回退为本地文件发送。
    """
    metadata['use_file_token_service'] = False
    metadata['file_token_urls'] = []

    file_paths = metadata.get('file_paths', [])
    if not file_paths or metadata.get('error'):
        return

    local_modes = list(metadata.get('video_modes') or []) + list(
        metadata.get('image_modes') or []
    )
    if not any(
        fp and os.path.exists(fp) and idx < len(local_modes)
        and local_modes[idx] == "local"
        for idx, fp in enumerate(file_paths)
    ):
        return

    file_token_urls: List[Optional[str]] = []
    for idx, fp in enumerate(file_paths):
        is_local = idx < len(local_modes) and local_modes[idx] == "local"
        if is_local and fp and os.path.exists(fp):
            file_token_urls.append(
                await register_file_with_token_service(
                    fp,
                    callback_api_base,
                    file_token_ttl,
                )
            )
        else:
            file_token_urls.append(None)

    metadata['file_token_urls'] = file_token_urls
    metadata['use_file_token_service'] = any(
        url is not None for url in file_token_urls
    )
