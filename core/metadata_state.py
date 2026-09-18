"""媒体元数据状态派生模块，统一维护下载与发送阶段的汇总字段。"""

import math
from typing import Optional

from .types import MediaMetadata


_SENDABLE_MODES = frozenset({"local", "direct"})


def largest_size_limited_video_mb(metadata: MediaMetadata) -> Optional[float]:
    """返回因大小限制被跳过的视频最大值。"""
    video_sizes = metadata.get("video_sizes")
    if not isinstance(video_sizes, list):
        return None
    size_limit_flags = metadata.get("video_size_limit_flags")
    if not isinstance(size_limit_flags, list):
        return None

    sizes = [
        float(size)
        for size, exceeded in zip(video_sizes, size_limit_flags)
        if exceeded is True
        and isinstance(size, (int, float))
        and not isinstance(size, bool)
        and math.isfinite(float(size))
    ]
    return max(sizes) if sizes else None


def refresh_media_state(metadata: MediaMetadata) -> None:
    """根据逐项模式、大小和文件路径刷新媒体汇总状态。

    Args:
        metadata: 待原地更新的媒体元数据。
    """
    video_urls = metadata.get("video_urls")
    image_urls = metadata.get("image_urls")
    video_count = len(video_urls) if isinstance(video_urls, list) else 0
    image_count = len(image_urls) if isinstance(image_urls, list) else 0

    video_modes = metadata.get("video_modes")
    if not isinstance(video_modes, list):
        video_modes = []
    image_modes = metadata.get("image_modes")
    if not isinstance(image_modes, list):
        image_modes = []
    file_paths = metadata.get("file_paths")
    if not isinstance(file_paths, list):
        file_paths = []
    video_sizes = metadata.get("video_sizes")
    if not isinstance(video_sizes, list):
        video_sizes = []
    size_limit_flags = metadata.get("video_size_limit_flags")
    if not isinstance(size_limit_flags, list):
        size_limit_flags = []

    sendable_video_sizes = [
        float(size)
        for size, mode in zip(video_sizes, video_modes)
        if isinstance(size, (int, float))
        and not isinstance(size, bool)
        and math.isfinite(float(size))
        and mode in _SENDABLE_MODES
    ]

    metadata["video_count"] = video_count
    metadata["image_count"] = image_count
    metadata["failed_video_count"] = sum(
        1 for mode in video_modes if mode == "skip"
    )
    metadata["failed_image_count"] = sum(
        1 for mode in image_modes if mode == "skip"
    )
    has_valid_media = any(
        mode in _SENDABLE_MODES for mode in (*video_modes, *image_modes)
    )
    metadata["has_valid_media"] = has_valid_media
    metadata["use_local_files"] = any(
        mode == "local" and index < len(file_paths) and bool(file_paths[index])
        for index, mode in enumerate(video_modes)
    ) or any(
        mode == "local"
        and video_count + index < len(file_paths)
        and bool(file_paths[video_count + index])
        for index, mode in enumerate(image_modes)
    )
    metadata["largest_video_size_mb"] = (
        max(sendable_video_sizes) if sendable_video_sizes else None
    )
    metadata["total_video_size_mb"] = (
        sum(sendable_video_sizes) if sendable_video_sizes else 0.0
    )
    metadata["exceeds_max_size"] = bool(
        not has_valid_media and any(flag is True for flag in size_limit_flags)
    )
