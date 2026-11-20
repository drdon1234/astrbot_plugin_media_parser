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
from .node_builder import build_text_node
from .text_renderer import PictureBlock, ShareHeader


@lru_cache(maxsize=1)
def _platform_labels() -> dict[str, str]:
    """复用配置面板的平台名称，让新平台自动获得相同的显示名称。"""
    schema = Path(__file__).resolve().parents[2] / "_conf_schema.json"
    choices = json.loads(schema.read_text(encoding="utf-8"))["parsers"]["items"]["text_platforms"]
    return dict(zip(choices["options"], choices["labels"]))


@dataclass
class CardContent:
    """记录画稿与对应消息节点，仅在整张图绘制成功后替换节点。"""

    blocks: list[str | PictureBlock]
    header: ShareHeader | None
    consumed: set[int]


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
    blocks: list[str | PictureBlock] = []
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
    if share and settings.share_cover and metadata.get("_enable_rich_media", True):
        covers = metadata.get("video_cover_urls") or []
        has_video = any(isinstance(node, Video) for node in link["link_nodes"])
        video_modes = metadata.get("video_modes") or []
        for index, candidates in enumerate(covers):
            # 单组封面可能由多个视频共用；独立封面只为未能输出的视频补位。
            if has_video and (
                len(covers) == 1 or index >= len(video_modes) or video_modes[index] != "skip"
            ):
                continue
            cover = await fetch_picture(candidates)
            if cover:
                blocks.append(PictureBlock((cover,), "cover"))
        converted = metadata.get("converted_cover_count", 0)
        for index, node in link["image_nodes"].items():
            if index < converted and id(node) in pictures:
                blocks.append(PictureBlock((pictures[id(node)],), "cover"))
                consumed.add(id(node))
        if not inline:
            gallery = [path for key, path in pictures.items() if key not in consumed]
            if gallery:
                blocks.append(PictureBlock(tuple(gallery), "gallery"))
            consumed.update(pictures)

    base = {id(node) for node in link["base_nodes"]}
    separate = settings.render_separate_sections
    sections = {id(node) for node in link["section_starts"]}
    for node in link["link_nodes"]:
        key = id(node)
        if (
            separate
            and key not in base
            and key in sections
            and node is not link["metadata_text_node"]
        ):
            break
        if key in inline:
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
                    )
                    if body:
                        blocks.append(body.text)
            else:
                blocks.append(node.text)
        else:
            continue
        consumed.add(key)

    if not separate:
        blocks.extend(node.text for node in translations if isinstance(node, Plain))
    return CardContent(blocks, header, consumed)
