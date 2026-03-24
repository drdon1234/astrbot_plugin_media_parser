"""文件清理工具，负责临时文件与空目录回收。"""
import os
import shutil
from typing import List

from ..logger import logger
from .cache_registry import MARKER_FILE_NAME


def cleanup_file(file_path: str) -> bool:
    """清理单个文件

    Args:
        file_path: 文件路径

    Returns:
        是否成功
    """
    if not file_path or not os.path.exists(file_path):
        return True
    
    try:
        if os.path.isfile(file_path):
            os.unlink(file_path)
            _try_remove_empty_parent(file_path)
            return True
        else:
            logger.warning(f"路径不是文件: {file_path}")
            return False
    except Exception as e:
        logger.warning(f"清理文件失败: {file_path}, 错误: {e}")
        return False


def _try_remove_empty_parent(file_path: str) -> None:
    """尝试删除文件所在的空父目录。

    若目录仅剩插件标记文件，先清除标记再移除目录，
    避免留下只含 .astrbot_media_parser 的空壳子目录。
    """
    parent = os.path.dirname(file_path)
    if not parent:
        return
    try:
        remaining = os.listdir(parent)
        if not remaining:
            os.rmdir(parent)
        elif remaining == [MARKER_FILE_NAME]:
            os.unlink(os.path.join(parent, MARKER_FILE_NAME))
            os.rmdir(parent)
    except OSError:
        pass


def cleanup_files(file_paths: List[str]) -> None:
    """清理文件列表

    Args:
        file_paths: 文件路径列表
    """
    if file_paths:
        logger.debug(f"开始清理 {len(file_paths)} 个文件")
    for file_path in file_paths:
        cleanup_file(file_path)


def cleanup_directory(dir_path: str, ignore_errors: bool = True) -> bool:
    """清理目录及其所有内容

    Args:
        dir_path: 目录路径
        ignore_errors: 是否忽略错误（默认True，与shutil.rmtree行为一致）

    Returns:
        是否成功
    """
    if not dir_path or not os.path.exists(dir_path):
        return True
    
    try:
        if os.path.isdir(dir_path):
            shutil.rmtree(dir_path, ignore_errors=ignore_errors)
            return True
        else:
            logger.warning(f"路径不是目录: {dir_path}")
            return False
    except Exception as e:
        if ignore_errors:
            logger.warning(f"清理目录失败: {dir_path}, 错误: {e}")
            return False
        else:
            raise
