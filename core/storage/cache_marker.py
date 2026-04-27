"""媒体文件缓存目录标记与安全清理工具。"""
import os
import shutil
from typing import Tuple

from ..logger import logger

MARKER_FILE_NAME = ".astrbot_media_parser"
_STAMP_SUBDIR_ENABLED = True


def set_stamp_subdir_enabled(enabled: bool) -> None:
    """控制是否写入缓存归属标记文件。"""
    global _STAMP_SUBDIR_ENABLED
    _STAMP_SUBDIR_ENABLED = bool(enabled)


def stamp_subdir(directory: str) -> None:
    """在媒体缓存子目录中放置归属标记文件。"""
    if not _STAMP_SUBDIR_ENABLED or not directory:
        return
    try:
        os.makedirs(directory, exist_ok=True)
        marker = os.path.join(directory, MARKER_FILE_NAME)
        if not os.path.isfile(marker):
            with open(marker, "w", encoding="utf-8") as f:
                f.write("")
    except Exception as e:
        logger.warning(f"写入缓存标记文件失败: {directory}, 错误: {e}")


def has_marker(directory: str) -> bool:
    """检查目录是否包含本插件的缓存标记文件。"""
    if not directory or not os.path.isdir(directory):
        return False
    return os.path.isfile(os.path.join(directory, MARKER_FILE_NAME))


def cleanup_marked_in(root_dir: str) -> Tuple[int, int]:
    """清理当前媒体文件缓存目录下由本插件标记的媒体子目录。

    只删除 root_dir 的直接子目录中包含标记文件的条目，
    不删除 root_dir 本身，也不触碰没有标记的内容。

    Returns:
        (清理的子目录数, 清理的文件总数)
    """
    if not root_dir or not os.path.isdir(root_dir):
        return 0, 0

    cleaned_subdirs = 0
    cleaned_files = 0

    for entry in os.listdir(root_dir):
        subdir = os.path.join(root_dir, entry)
        if not os.path.isdir(subdir) or not has_marker(subdir):
            continue

        file_count = sum(len(files) for _, _, files in os.walk(subdir))
        try:
            shutil.rmtree(subdir, ignore_errors=True)
            cleaned_subdirs += 1
            cleaned_files += file_count
        except Exception as e:
            logger.warning(f"清理缓存子目录失败: {subdir}, 错误: {e}")

    return cleaned_subdirs, cleaned_files
