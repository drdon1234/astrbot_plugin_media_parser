"""缓存目录注册表，跨配置变更追踪本插件使用过的所有缓存路径。

每次启动时将当前 cache_dir 注册到持久化 JSON 文件中；
在插件创建的每个媒体子目录下放置标记文件，用于安全识别归属。
清理时仅删除带有标记的子目录，不会对根目录执行 rmtree。
"""
import json
import os
import shutil
from typing import Dict, List, Tuple

from ..logger import logger

MARKER_FILE_NAME = ".astrbot_media_parser"


def _default_registry_path() -> str:
    """注册表文件存放在系统临时目录下，避免写入源码树。"""
    import tempfile
    registry_dir = os.path.join(
        tempfile.gettempdir(), "astrbot_media_parser_runtime"
    )
    os.makedirs(registry_dir, exist_ok=True)
    return os.path.join(registry_dir, "cache_dirs.json")


def stamp_subdir(directory: str) -> None:
    """在指定目录中放置标记文件，标识该目录由本插件创建。

    供下载器在创建媒体子目录时调用。
    """
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
    """检查目录是否包含本插件的标记文件。"""
    if not directory or not os.path.isdir(directory):
        return False
    return os.path.isfile(os.path.join(directory, MARKER_FILE_NAME))


class CacheRegistry:
    """管理本插件曾使用过的全部缓存目录。"""

    def __init__(self, registry_path: str = None):
        self._path = registry_path or _default_registry_path()
        self._dirs: Dict[str, str] = self._load()

    # ── 持久化 ──────────────────────────────────────────

    def _load(self) -> Dict[str, str]:
        """从 JSON 加载 {abs_path: label} 映射。"""
        if not os.path.isfile(self._path):
            return {}
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"读取缓存注册表失败，将重建: {e}")
        return {}

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._dirs, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存缓存注册表失败: {e}")

    # ── 目录注册 ────────────────────────────────────────

    def register(self, cache_dir: str, label: str = "") -> None:
        """将缓存根目录注册到注册表。

        Args:
            cache_dir: 缓存根目录的绝对路径
            label: 可选标签（如 "pre_download" / "file_token_service"）
        """
        if not cache_dir:
            return
        abs_dir = os.path.abspath(cache_dir)
        is_new = abs_dir not in self._dirs
        if label:
            self._dirs[abs_dir] = label
        elif is_new:
            self._dirs[abs_dir] = ""
        self._save()
        if is_new:
            logger.debug(f"注册缓存目录: {abs_dir} (label={label or '无'})")

    # ── 查询 ────────────────────────────────────────────

    def get_all(self) -> Dict[str, str]:
        """返回全部已注册目录 {abs_path: label}。"""
        return dict(self._dirs)

    # ── 安全清理 ────────────────────────────────────────

    @staticmethod
    def cleanup_marked_in(root_dir: str) -> Tuple[int, int]:
        """清理单个根目录下所有带标记的子目录。

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
            if not os.path.isdir(subdir):
                continue
            if not has_marker(subdir):
                continue

            file_count = sum(len(files) for _, _, files in os.walk(subdir))
            try:
                shutil.rmtree(subdir, ignore_errors=True)
                cleaned_subdirs += 1
                cleaned_files += file_count
            except Exception as e:
                logger.warning(f"清理子目录失败: {subdir}, 错误: {e}")

        return cleaned_subdirs, cleaned_files

    def cleanup_all(self) -> Tuple[int, int, List[str]]:
        """安全清理所有已注册根目录下带标记的子目录。

        Returns:
            (清理的子目录总数, 清理的文件总数, 跳过的根目录列表)
        """
        total_subdirs = 0
        total_files = 0
        skipped: List[str] = []

        for abs_dir in list(self._dirs):
            if not os.path.isdir(abs_dir):
                self._dirs.pop(abs_dir, None)
                continue

            subdirs, files = self.cleanup_marked_in(abs_dir)
            if subdirs > 0:
                total_subdirs += subdirs
                total_files += files
                logger.debug(
                    f"已清理注册目录: {abs_dir} "
                    f"({subdirs} 个子目录, {files} 个文件)"
                )
            else:
                skipped.append(abs_dir)

        self._prune()
        self._save()
        return total_subdirs, total_files, skipped

    def _prune(self) -> None:
        """移除注册表中已不存在的目录条目。"""
        gone = [d for d in self._dirs if not os.path.isdir(d)]
        for d in gone:
            self._dirs.pop(d, None)
