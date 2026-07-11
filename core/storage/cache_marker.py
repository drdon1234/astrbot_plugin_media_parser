"""媒体文件缓存目录标记与安全清理工具。"""
import os
import shutil
import time
from typing import Iterable, Optional, Tuple

from ..logger import logger

MARKER_FILE_NAME = ".astrbot_media_parser"
EXPIRY_FILE_NAME = ".astrbot_media_parser.expire"
LEGACY_EXPIRY_MIN_GRACE_SECONDS = 3600


def stamp_subdir(directory: str) -> None:
    """在媒体缓存子目录中放置归属标记文件。"""
    if not directory:
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


def mark_subdir_expires_at(directory: str, expires_at: float) -> bool:
    """为已标记的媒体缓存子目录写入持久化过期时间。"""
    if not has_marker(directory):
        return False
    try:
        expiry_file = os.path.join(directory, EXPIRY_FILE_NAME)
        with open(expiry_file, "w", encoding="utf-8") as f:
            f.write(f"{float(expires_at):.6f}\n")
        return True
    except Exception as e:
        logger.warning(f"写入缓存过期标记失败: {directory}, 错误: {e}")
        return False


def mark_files_expire_after(
    file_paths: Iterable[str],
    delay_seconds: int,
    now: Optional[float] = None,
) -> int:
    """按文件列表为对应媒体子目录写入持久化清理时间。

    Returns:
        成功写入过期标记的子目录数量。
    """
    if not file_paths:
        return 0

    try:
        delay = max(0, int(delay_seconds))
    except (TypeError, ValueError):
        delay = 0
    expires_at = (time.time() if now is None else float(now)) + delay

    marked = 0
    seen = set()
    for file_path in file_paths:
        if not file_path:
            continue
        directory = os.path.dirname(os.path.abspath(file_path))
        if not directory or directory in seen:
            continue
        seen.add(directory)
        if mark_subdir_expires_at(directory, expires_at):
            marked += 1
    return marked


def _read_expiry_at(directory: str) -> Optional[float]:
    expiry_file = os.path.join(directory, EXPIRY_FILE_NAME)
    if not os.path.isfile(expiry_file):
        return None
    try:
        with open(expiry_file, "r", encoding="utf-8") as f:
            raw = f.read().strip()
        return float(raw)
    except Exception as e:
        logger.warning(f"读取缓存过期标记失败: {expiry_file}, 错误: {e}")
        return None


def _latest_mtime(directory: str) -> float:
    latest = os.path.getmtime(directory)
    for dirpath, dirnames, filenames in os.walk(directory):
        for name in dirnames + filenames:
            path = os.path.join(dirpath, name)
            try:
                latest = max(latest, os.path.getmtime(path))
            except OSError:
                continue
    return latest


def _legacy_expiry_at(
    directory: str,
    ttl_seconds: Optional[int],
) -> Optional[float]:
    if ttl_seconds is None:
        return None
    try:
        ttl = int(ttl_seconds)
    except (TypeError, ValueError):
        return None
    if ttl <= 0:
        return None

    grace = max(ttl, LEGACY_EXPIRY_MIN_GRACE_SECONDS)
    try:
        return _latest_mtime(directory) + grace
    except OSError:
        return None


def cleanup_expired_marked_in(
    root_dir: str,
    ttl_seconds: Optional[int] = None,
    now: Optional[float] = None,
) -> Tuple[int, int]:
    """清理当前缓存根目录下已过期的插件媒体子目录。

    新版本缓存目录会写入持久化过期时间；旧版本遗留目录没有过期文件时，
    使用最近修改时间加 TTL 兜底，并至少保留一小时，避免误删当前下载。

    Returns:
        (清理的子目录数, 清理的文件总数)
    """
    if not root_dir or not os.path.isdir(root_dir):
        return 0, 0

    cutoff = time.time() if now is None else float(now)
    cleaned_subdirs = 0
    cleaned_files = 0

    for entry in os.listdir(root_dir):
        subdir = os.path.join(root_dir, entry)
        if not os.path.isdir(subdir) or not has_marker(subdir):
            continue

        expiry_at = _read_expiry_at(subdir)
        if expiry_at is None:
            expiry_at = _legacy_expiry_at(subdir, ttl_seconds)
        if expiry_at is None or expiry_at > cutoff:
            continue

        file_count = sum(len(files) for _, _, files in os.walk(subdir))
        try:
            shutil.rmtree(subdir, ignore_errors=True)
            cleaned_subdirs += 1
            cleaned_files += file_count
        except Exception as e:
            logger.warning(f"清理过期缓存子目录失败: {subdir}, 错误: {e}")

    return cleaned_subdirs, cleaned_files


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
