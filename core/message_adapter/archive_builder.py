"""将完整解析结果和本地媒体整理为独立 ZIP 归档。"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from ..logger import logger
from ..storage.cache_marker import (
    EXPIRY_FILE_NAME,
    MARKER_FILE_NAME,
    stamp_subdir,
)


_INVALID_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_ARCHIVE_ROOT_NAME = "media_parser"
_WORKSPACE_PREFIX = "media_parser_zip_"
_ARCHIVE_FILE_NAME = "media_parser.zip"
_DISK_SPACE_RESERVE_BYTES = 16 * 1024 * 1024


class ArchiveSizeLimitError(ValueError):
    """待归档媒体超过管理员配置的请求级总预算。"""


def _safe_name(value: Any, fallback: str) -> str:
    text = _INVALID_NAME_CHARS.sub("_", str(value or "").strip())
    text = text.strip(" .")
    if not text or text in {".", ".."}:
        return fallback
    return text[:80]


def _iter_media(
    metadata: Mapping[str, Any],
) -> Iterable[tuple[str, int, str, Optional[str]]]:
    file_paths = metadata.get("file_paths") or []
    video_urls = metadata.get("video_urls") or []
    image_urls = metadata.get("image_urls") or []
    video_count = len(video_urls)

    for index, url_list in enumerate(video_urls):
        url = url_list[0] if isinstance(url_list, list) and url_list else ""
        path = file_paths[index] if index < len(file_paths) else None
        yield "video", index, str(url or ""), path

    for index, url_list in enumerate(image_urls):
        position = video_count + index
        url = url_list[0] if isinstance(url_list, list) and url_list else ""
        path = file_paths[position] if position < len(file_paths) else None
        yield "image", index, str(url or ""), path


def _media_name(kind: str, index: int, source_path: str) -> str:
    suffix = Path(source_path).suffix.lower() or ".bin"
    if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
        suffix = ".bin"
    return f"{kind}_{index + 1:03d}{suffix}"


def _append_field(lines: list[str], label: str, value: Any) -> None:
    text = str(value or "").strip()
    if text:
        lines.append(f"{label}：{text}")


def _format_hot_comments(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, Mapping):
            author = str(
                item.get("username") or item.get("author") or item.get("name") or ""
            ).strip()
            content = str(
                item.get("content") or item.get("text") or item.get("message") or ""
            ).strip()
            if content:
                attributes = []
                for key, label in (
                    ("uid", "UID"),
                    ("likes", "点赞"),
                    ("time", "时间"),
                ):
                    text = str(item.get(key) or "").strip()
                    if text:
                        attributes.append(f"{label}={text}")
                suffix = f"（{'，'.join(attributes)}）" if attributes else ""
                result.append(f"{author + '：' if author else ''}{content}{suffix}")
        else:
            text = str(item or "").strip()
            if text:
                result.append(text)
    return result


def format_archive_metadata(
    metadata: Mapping[str, Any],
    translated_metadata: Optional[Mapping[str, Any]] = None,
    missing_media: Sequence[str] = (),
) -> str:
    """生成与聊天节点无关的归档元数据文本。"""
    lines: list[str] = []
    _append_field(lines, "平台", metadata.get("platform"))
    _append_field(lines, "标题", metadata.get("title"))
    _append_field(lines, "作者", metadata.get("author"))
    _append_field(lines, "发布时间", metadata.get("timestamp"))
    _append_field(lines, "简介", metadata.get("desc"))
    _append_field(lines, "原始链接", metadata.get("url") or metadata.get("source_url"))
    _append_field(lines, "访问状态", metadata.get("access_status"))
    _append_field(lines, "访问提示", metadata.get("access_message"))
    _append_field(lines, "解析错误", metadata.get("error"))
    _append_field(lines, "最大视频大小(MB)", metadata.get("max_video_size_mb"))
    _append_field(lines, "有效视频数", metadata.get("valid_video_count"))
    _append_field(lines, "有效图片数", metadata.get("valid_image_count"))
    _append_field(lines, "失败视频数", metadata.get("failed_video_count"))
    _append_field(lines, "失败图片数", metadata.get("failed_image_count"))
    warnings = [
        str(value).strip()
        for value in (metadata.get("image_warnings") or [])
        if str(value or "").strip()
    ]
    if warnings:
        lines.append("图片处理警告：")
        lines.extend(f"- {warning}" for warning in warnings)

    comments = _format_hot_comments(metadata.get("hot_comments"))
    if comments:
        lines.append("热评：")
        lines.extend(f"- {comment}" for comment in comments)

    translated_fields = (
        translated_metadata.get("_translated_fields")
        if isinstance(translated_metadata, Mapping)
        else None
    )
    if isinstance(translated_fields, Mapping) and translated_fields:
        lines.append("翻译：")
        for key, label in (("title", "标题"), ("desc", "简介")):
            _append_field(lines, f"- {label}", translated_fields.get(key))

    clean_missing = [str(url).strip() for url in missing_media if str(url).strip()]
    if clean_missing:
        lines.append("未归档媒体：")
        lines.extend(f"- {url}" for url in clean_missing)

    if not lines:
        lines.append("没有可用的解析元数据")
    return "\n".join(lines) + "\n"


def _safe_json_value(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _safe_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json_value(item) for item in value]
    return str(value)


def _build_archive_details(
    metadata: Mapping[str, Any],
    translated_metadata: Optional[Mapping[str, Any]],
    media: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """输出白名单化详情，不落盘 Cookie、请求头、Token 或本地路径。"""
    safe_fields = (
        "platform",
        "title",
        "author",
        "timestamp",
        "desc",
        "url",
        "source_url",
        "access_status",
        "access_message",
        "error",
        "has_valid_media",
        "valid_video_count",
        "valid_image_count",
        "failed_video_count",
        "failed_image_count",
        "max_video_size_mb",
        "exceeds_max_size",
        "image_warnings",
        "hot_comments",
    )
    details = {
        key: _safe_json_value(metadata.get(key))
        for key in safe_fields
        if metadata.get(key) not in (None, "", [], {})
    }
    details["media"] = [_safe_json_value(item) for item in media]

    translated_fields = (
        translated_metadata.get("_translated_fields")
        if isinstance(translated_metadata, Mapping)
        else None
    )
    if isinstance(translated_fields, Mapping) and translated_fields:
        details["translation"] = _safe_json_value(translated_fields)
        language = translated_metadata.get("translation_target_language")
        if language:
            details["translation_target_language"] = str(language)
    return details


def _create_workspace(output_dir: str) -> str:
    if output_dir and os.path.isdir(output_dir):
        try:
            return tempfile.mkdtemp(prefix=_WORKSPACE_PREFIX, dir=output_dir)
        except OSError as exc:
            logger.warning(f"归档缓存目录不可写，已回退系统临时目录: {exc}")
    return tempfile.mkdtemp(prefix=_WORKSPACE_PREFIX)


def build_zip_archive(
    metadata_list: Sequence[Mapping[str, Any]],
    *,
    translated_metadata_list: Optional[Sequence[Mapping[str, Any]]] = None,
    output_dir: str = "",
    max_total_bytes: int = 1024 * 1024 * 1024,
) -> str:
    """创建稳定布局的 ZIP，并直接写入源媒体以避免额外复制。"""
    try:
        byte_limit = max(1, int(max_total_bytes))
    except (TypeError, ValueError) as exc:
        raise ValueError("归档总大小上限无效") from exc

    source_total_bytes = 0
    for metadata in metadata_list:
        for _, _, _, source_path in _iter_media(metadata):
            if not source_path or not os.path.isfile(source_path):
                continue
            try:
                source_total_bytes += os.path.getsize(source_path)
            except OSError as exc:
                raise OSError(f"无法读取待归档媒体大小: {source_path}") from exc
            if source_total_bytes > byte_limit:
                raise ArchiveSizeLimitError(
                    f"媒体总大小超过归档上限 {byte_limit / 1024 / 1024:.1f} MB"
                )

    workspace = _create_workspace(output_dir)
    stamp_subdir(workspace)
    archive_path = Path(workspace) / _ARCHIVE_FILE_NAME

    try:
        free_bytes = shutil.disk_usage(workspace).free
        required_bytes = source_total_bytes + _DISK_SPACE_RESERVE_BYTES
        if free_bytes < required_bytes:
            raise OSError(
                "归档磁盘空间不足："
                f"至少需要 {required_bytes / 1024 / 1024:.1f} MB 可用空间"
            )
        with zipfile.ZipFile(
            archive_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            allowZip64=True,
        ) as archive:
            for index, metadata in enumerate(metadata_list):
                link_number = index + 1
                title = metadata.get("title") or metadata.get("platform")
                link_dir = (
                    f"{_ARCHIVE_ROOT_NAME}/"
                    f"{link_number:03d}_{_safe_name(title, 'link')}"
                )

                missing_media: list[str] = []
                media_details: list[dict[str, Any]] = []
                for kind, media_index, url, source_path in _iter_media(metadata):
                    modes = metadata.get(f"{kind}_modes") or []
                    reasons = metadata.get(f"{kind}_skip_reasons") or []
                    mode = modes[media_index] if media_index < len(modes) else ""
                    reason = (
                        reasons[media_index] if media_index < len(reasons) else None
                    )
                    detail: dict[str, Any] = {
                        "type": kind,
                        "index": media_index + 1,
                        "mode": mode,
                    }
                    if kind == "video":
                        sizes = metadata.get("video_sizes") or []
                        if media_index < len(sizes) and sizes[media_index] is not None:
                            detail["size_mb"] = sizes[media_index]
                    if source_path and os.path.isfile(source_path):
                        archive_name = _media_name(kind, media_index, source_path)
                        archive.write(
                            source_path,
                            f"{link_dir}/{archive_name}",
                            compress_type=zipfile.ZIP_STORED,
                        )
                        detail["status"] = "archived"
                        detail["archive_name"] = archive_name
                    elif url:
                        missing_media.append(url)
                        detail["status"] = "not_archived"
                        detail["source_url"] = url
                    else:
                        detail["status"] = "not_archived"
                    if reason:
                        detail["reason"] = str(reason)
                    media_details.append(detail)

                translated = None
                if translated_metadata_list and index < len(translated_metadata_list):
                    translated = translated_metadata_list[index]
                archive.writestr(
                    f"{link_dir}/metadata.txt",
                    format_archive_metadata(metadata, translated, missing_media),
                )
                details = _build_archive_details(
                    metadata,
                    translated,
                    media_details,
                )
                archive.writestr(
                    f"{link_dir}/details.json",
                    json.dumps(details, ensure_ascii=False, indent=2) + "\n",
                )
        return str(archive_path)
    except BaseException:
        try:
            shutil.rmtree(workspace)
        except OSError as cleanup_error:
            logger.warning(f"清理失败的ZIP工作目录失败: {cleanup_error}")
        raise


def cleanup_zip_archive(archive_path: str) -> None:
    """只清理由本模块创建的归档工作目录。"""
    if not archive_path:
        return
    path = Path(archive_path).resolve()
    workspace = path.parent
    if path.name != _ARCHIVE_FILE_NAME or not workspace.name.startswith(
        _WORKSPACE_PREFIX
    ):
        logger.warning(f"拒绝清理非归档工作目录路径: {path}")
        return
    try:
        shutil.rmtree(workspace)
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning(f"清理ZIP临时目录失败: {workspace}, 错误: {exc}")


def cleanup_expired_zip_workspaces(now: Optional[float] = None) -> tuple[int, int]:
    """回收系统临时目录中的过期归档，覆盖热重载时被取消的延迟任务。"""
    root = Path(tempfile.gettempdir()).resolve()
    cutoff = time.time() if now is None else float(now)
    cleaned = 0
    failed = 0
    try:
        entries = list(root.iterdir())
    except OSError as exc:
        logger.warning(f"扫描系统临时归档目录失败: {exc}")
        return 0, 1

    for workspace in entries:
        if not workspace.is_dir() or not workspace.name.startswith(_WORKSPACE_PREFIX):
            continue
        if workspace.parent.resolve() != root:
            continue
        marker = workspace / MARKER_FILE_NAME
        expiry = workspace / EXPIRY_FILE_NAME
        if not marker.is_file() or not expiry.is_file():
            continue
        try:
            expires_at = float(expiry.read_text(encoding="utf-8").strip())
            if expires_at > cutoff:
                continue
            shutil.rmtree(workspace)
            if workspace.exists():
                raise OSError("目录删除后仍然存在")
            cleaned += 1
        except (OSError, ValueError) as exc:
            failed += 1
            logger.warning(f"清理过期ZIP工作目录失败: {workspace}, 错误: {exc}")
    return cleaned, failed
