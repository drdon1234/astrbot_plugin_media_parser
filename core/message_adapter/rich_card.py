"""将一条解析结果中的可见文字、封面与已下载配图组织为有序画稿。"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Awaitable, Callable

from astrbot.api.message_components import Plain, Video

from ..config_manager import TextMetadataConfig
from ..metadata_visibility import text_metadata_field_enabled
from ..types import LinkBuildMeta, MediaMetadata
from .download_list import DownloadList, download_blocks
from .node_builder import TEXT_SECTION_SEPARATOR, build_text_node
from .text_renderer import CommentBlock, PictureBlock, ShareHeader


@lru_cache(maxsize=1)
def _platform_labels() -> dict[str, str]:
    """复用配置面板的平台名称，让新平台自动获得相同的显示名称。"""
    schema = Path(__file__).resolve().parents[2] / "_conf_schema.json"
    choices = json.loads(schema.read_text(encoding="utf-8"))["parsers"]["items"]["text_platforms"]
    return dict(zip(choices["options"], choices["labels"]))


@dataclass
class CardContent:
    """记录画稿与对应消息节点，仅在整张图绘制成功后替换节点。"""

    blocks: list[str | PictureBlock | DownloadList | CommentBlock]
    header: ShareHeader | None
    consumed: set[int]


def comment_node_blocks(link: LinkBuildMeta, metadata: MediaMetadata) -> dict[int, CommentBlock | None]:
    """把同一评论区的消息分片还原为一个画稿块。"""
    nodes = link.get("comment_nodes", [])
    if not nodes:
        return {}
    items = tuple(item for item in metadata.get("hot_comments") or () if isinstance(item, dict))
    if not items:
        return {}
    title = "分集弹幕" if metadata.get("hot_comments_label") == "分集弹幕" else "热评"
    return {id(node): CommentBlock(title, items) if i == 0 else None for i, node in enumerate(nodes)}


def text_card_blocks(groups: list, comments: dict[int, CommentBlock | None]) -> list[str | CommentBlock]:
    """按链接及翻译的发送顺序保留正文、评论和组间分隔。"""
    blocks = []
    for nodes in groups:
        group = []
        for node in nodes:
            if id(node) in comments:
                block = comments[id(node)]
                if block:
                    group.append(block)
            elif isinstance(node, Plain) and node.text.strip():
                if group and isinstance(group[-1], str):
                    group[-1] += "\n" + node.text.strip()
                else:
                    group.append(node.text.strip())
        if group:
            if blocks:
                blocks.append(TEXT_SECTION_SEPARATOR)
            blocks.extend(group)
    return blocks


async def build_card_content(
    metadata: MediaMetadata,
    link: LinkBuildMeta,
    settings: TextMetadataConfig,
    max_video_mb: float,
    translations: list,
    fetch_picture: Callable[[list[str]], Awaitable[str]],
) -> CardContent:
    """从已启用的输出字段和本地媒体构造单条链接的图片内容。"""
    share = settings.render_layout == "分享卡"
    blocks: list[str | PictureBlock | DownloadList | CommentBlock] = []
    header = None
    if share:
        author = (
            (metadata.get("author_name") or metadata.get("author") or "")
            if text_metadata_field_enabled(metadata, "author")
            else ""
        )
        timestamp = (
            (metadata.get("published_at") or metadata.get("timestamp") or "")
            if text_metadata_field_enabled(metadata, "timestamp")
            else ""
        )
        avatar_url = metadata.get("author_avatar_url")
        avatar = await fetch_picture([avatar_url]) if avatar_url and author else ""
        platform = metadata.get("platform") or metadata.get("parser_name") or "分享"
        label = (
            "bilibili"
            if platform == "bilibili"
            else _platform_labels().get(platform, platform)
        )
        header = ShareHeader(label, author, timestamp, avatar)

    files = metadata.get("file_paths") or []
    offset = len(metadata.get("video_urls") or [])
    readme_start = metadata.get("readme_image_start", len(metadata.get("image_urls") or []))
    pictures = {
        id(node): files[offset + index]
        for index, node in link["image_nodes"].items()
        if index < readme_start and offset + index < len(files) and files[offset + index]
    }
    inline = {id(node) for node in link["inline_images"]}
    consumed = set()
    video_card = False
    if share and settings.share_cover and metadata.get("_enable_rich_media", True):
        covers = metadata.get("video_cover_urls") or []
        has_video = any(isinstance(node, Video) for node in link["link_nodes"])
        video_modes = metadata.get("video_modes") or []
        cover_kinds = {}
        for index, candidates in enumerate(covers):
            if not candidates:
                continue
            preview = has_video and (
                len(covers) == 1 or index >= len(video_modes) or video_modes[index] != "skip"
            )
            key = tuple(candidates)
            if preview or key not in cover_kinds:
                cover_kinds[key] = "video_preview" if preview else "cover"
        for candidates, kind in cover_kinds.items():
            cover = await fetch_picture(list(candidates))
            if cover:
                blocks.append(PictureBlock((cover,), kind))
        converted = metadata.get("converted_cover_count", 0)
        for index, node in link["image_nodes"].items():
            if index < converted and id(node) in pictures:
                blocks.append(PictureBlock((pictures[id(node)],), "cover"))
                consumed.add(id(node))
        video_card = bool(blocks)
        if video_card and text_metadata_field_enabled(metadata, "title") and metadata.get("title"):
            blocks.insert(0, f"标题：{metadata['title']}")
        if not inline:
            gallery = [path for key, path in pictures.items() if key not in consumed]
            if gallery:
                blocks.append(PictureBlock(tuple(gallery), "gallery"))
            consumed.update(pictures)

    base = {id(node) for node in link["base_nodes"]}
    separate = settings.render_separate_sections
    sections = {id(node) for node in link["section_starts"]}
    comments = comment_node_blocks(link, metadata)
    for node in link["link_nodes"]:
        key = id(node)
        if (
            separate
            and key not in base
            and key in sections
            and node is not link["metadata_text_node"]
        ):
            break
        if key in comments:
            if comments[key]:
                blocks.append(comments[key])
        elif key in inline:
            if key not in pictures:
                raise ValueError("正文配图缺少可绘制的本地文件")
            blocks.append(PictureBlock((pictures[key],)))
        elif isinstance(node, Plain):
            if share and key in base:
                if node is link["base_nodes"][0]:
                    body = build_text_node(
                        metadata,
                        max_video_mb,
                        include_description=not inline,
                        share_body=True,
                        video_card=video_card,
                    )
                    if body:
                        desc = str(metadata.get("desc") or "").strip()
                        parts = (
                            download_blocks(desc) if desc and not inline
                            and text_metadata_field_enabled(metadata, "description") else []
                        )
                        if any(isinstance(part, DownloadList) for part in parts):
                            before, _, after = body.text.rpartition(desc)
                            blocks.extend([before, *parts, after])
                        else:
                            blocks.append(body.text)
            else:
                blocks.append(node.text)
        else:
            continue
        consumed.add(key)

    if not separate:
        blocks.extend(node.text for node in translations if isinstance(node, Plain))
    return CardContent(blocks, header, consumed)
