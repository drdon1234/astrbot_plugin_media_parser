"""将解析结果及已下载媒体整理为可发送的 ZIP 归档。"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from ..logger import logger


_INVALID_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_DEFAULT_ROOT_NAME = "media_parser"


def _safe_name(value: Any, fallback: str) -> str:
    """将标题等外部文本转换为跨平台安全的相对路径名称。"""
    text = _INVALID_NAME_CHARS.sub("_", str(value or "").strip())
    text = text.strip(" .")
    if not text or text in {".", ".."}:
        return fallback
    return text[:80]


def _node_text(node: Any) -> str:
    value = getattr(node, "text", None)
    return value if isinstance(value, str) else ""


def _iter_media(
    metadata: Mapping[str, Any],
) -> Iterable[tuple[str, int, str, Optional[str], str]]:
    """按节点展示顺序返回媒体类型、序号、URL、本地路径和模式。"""
    file_paths = metadata.get("file_paths") or []
    video_urls = metadata.get("video_urls") or []
    image_urls = metadata.get("image_urls") or []
    video_modes = metadata.get("video_modes") or []
    image_modes = metadata.get("image_modes") or []
    video_count = len(video_urls)

    for index, url_list in enumerate(video_urls):
        url = url_list[0] if isinstance(url_list, list) and url_list else ""
        path = file_paths[index] if index < len(file_paths) else None
        mode = video_modes[index] if index < len(video_modes) else "skip"
        yield "video", index, url, path, mode

    for index, url_list in enumerate(image_urls):
        position = video_count + index
        url = url_list[0] if isinstance(url_list, list) and url_list else ""
        path = file_paths[position] if position < len(file_paths) else None
        mode = image_modes[index] if index < len(image_modes) else "skip"
        yield "image", index, url, path, mode


def _media_name(kind: str, index: int, source_path: str) -> str:
    suffix = Path(source_path).suffix.lower() or ".bin"
    return f"{kind}_{index + 1:03d}{suffix}"


def build_zip_archive(
    metadata_list: Sequence[Mapping[str, Any]],
    link_metadata: Sequence[Mapping[str, Any]],
    *,
    should_pack: bool,
    translation_nodes: Optional[Sequence[Sequence[Any]]] = None,
    output_dir: str = "",
) -> str:
    """创建 ZIP 文件并返回其路径。

    每条链接都拥有一个目录，其中的 ``metadata.txt`` 与该链接的媒体
    文件处于同一父目录。消息打包模式开启时，所有链接目录再置于同一
    个顶层目录中，以保留消息集合的层级关系。
    """
    parent = output_dir if output_dir and os.path.isdir(output_dir) else None
    workspace = tempfile.mkdtemp(prefix="media_parser_zip_", dir=parent)
    root = Path(workspace) / (_DEFAULT_ROOT_NAME if should_pack else "results")
    root.mkdir(parents=True, exist_ok=True)
    archive_path = Path(workspace) / "media_parser.zip"

    try:
        for link_number, link_meta in enumerate(link_metadata, start=1):
            metadata_index = int(link_meta.get("metadata_index", link_number - 1))
            metadata = (
                metadata_list[metadata_index]
                if 0 <= metadata_index < len(metadata_list)
                else {}
            )
            title = metadata.get("title") or metadata.get("platform")
            link_dir = root / f"{link_number:03d}_{_safe_name(title, 'link')}"
            link_dir.mkdir(parents=True, exist_ok=True)

            text_lines = [
                text for node in (link_meta.get("link_nodes") or [])
                if (text := _node_text(node))
            ]
            if translation_nodes and 0 <= metadata_index < len(translation_nodes):
                text_lines.extend(
                    text for node in (translation_nodes[metadata_index] or [])
                    if (text := _node_text(node))
                )

            copied_count = 0
            missing_media = []
            for kind, index, url, source_path, mode in _iter_media(metadata):
                if source_path and os.path.isfile(source_path):
                    target_name = _media_name(kind, index, source_path)
                    shutil.copy2(source_path, link_dir / target_name)
                    copied_count += 1
                elif mode in {"local", "direct"} and url:
                    missing_media.append(url)

            if missing_media:
                text_lines.append(
                    "未打包媒体（本地文件不可用）："
                    + "、".join(missing_media)
                )
            if not text_lines:
                text_lines.append(
                    f"原始链接：{metadata.get('url', '')}"
                )
            if copied_count == 0 and not missing_media:
                text_lines.append("媒体文件：无可用本地文件")

            (link_dir / "metadata.txt").write_text(
                "\n".join(text_lines) + "\n",
                encoding="utf-8",
            )

        with zipfile.ZipFile(
            archive_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            for file_path in root.rglob("*"):
                if file_path.is_file():
                    archive.write(
                        file_path,
                        file_path.relative_to(workspace).as_posix(),
                    )
        return str(archive_path)
    except Exception:
        shutil.rmtree(workspace, ignore_errors=True)
        raise


def cleanup_zip_archive(archive_path: str) -> None:
    """清理归档文件及其临时目录。"""
    if not archive_path:
        return
    workspace = os.path.dirname(os.path.abspath(archive_path))
    try:
        shutil.rmtree(workspace, ignore_errors=True)
    except Exception as exc:
        logger.warning(f"清理ZIP临时目录失败: {workspace}, 错误: {exc}")
