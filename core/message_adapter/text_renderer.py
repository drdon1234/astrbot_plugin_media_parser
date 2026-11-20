"""文本元数据图片渲染器，负责将文本节点绘制为 PNG 图片，并按需分页。"""

from __future__ import annotations

import asyncio
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List

from .font_manager import FONT_DIR, ensure_default_fonts, ensure_emoji_font
from .render_options import (
    DEFAULT_RENDER_FONT_FAMILY, DEFAULT_RENDER_STYLE, normalize_font_family, normalize_style,
)


DEFAULT_RENDER_WIDTH = 960
DEFAULT_RENDER_FONT_SIZE = 24
MIN_RENDER_FONT_SIZE = 16
MAX_RENDER_FONT_SIZE = 42
DEFAULT_LINE_SPACING = 1.55
DEFAULT_PARAGRAPH_SPACING = 0.5
PAGINATED_MAX_HEIGHT = 1800
MAX_RENDER_HEIGHT = 16384
MAX_RENDER_PAGE_PIXELS = 16000000
MAX_RENDER_TOTAL_PIXELS = 32000000
MAX_RENDER_PAGES = 20
PICTURE_GAP = 8
CONTENT_PICTURE_KINDS = ("photo", "gallery")
TEXT_SECTION_SEPARATOR = "-------------------------------------"
NO_LINE_START_CHARS = "，。！？；：、,.!?;:)]}）】》〉」』”’％%…"
NO_LINE_END_CHARS = "([{（【《〈「『“‘"
METADATA_LABELS = frozenset(
    {
        "标题", "作者", "发布时间", "视频大小", "时长", "访问提示", "解析失败",
        "原始链接", "点赞", "时间", "简介/正文", "许可证", "Star", "更新时间", "最近活动",
    }
)


@dataclass(frozen=True)
class PictureBlock:
    """正文配图、完整图集或视频封面，仅接受已下载的本地文件。"""

    paths: tuple[str, ...]
    kind: str = "photo"


@dataclass(frozen=True)
class ShareHeader:
    """分享卡的作者、原始时间与平台署名，空字段自然收起。"""

    platform: str
    author: str = ""
    timestamp: str = ""
    avatar: str = ""


@dataclass
class _LanguageShare:
    """保存语言图例的可见文字、色值与条段长度。"""

    label: str
    color: str
    percent: float


@dataclass
class _TextLine:
    """保存测量后的文字行，使绘制与分页共用同一份尺寸。"""

    text: str
    kind: str
    height: int
    label: str = ""
    keep_with_next: bool = False
    pictures: tuple[str, ...] = ()
    picture_widths: tuple[int, ...] = ()
    picture_crops: tuple[tuple[int, int, int, int] | None, ...] = ()
    shares: tuple[_LanguageShare, ...] = ()
    column_width: int = 0
    tree_folder: bool = False


async def render_text_metadata_images(
    text: str,
    output_path: str,
    *,
    title: str = "媒体解析结果",
    width: int = DEFAULT_RENDER_WIDTH,
    font_size: int = DEFAULT_RENDER_FONT_SIZE,
    style: str = DEFAULT_RENDER_STYLE,
    font_family: str = DEFAULT_RENDER_FONT_FAMILY,
    line_spacing: float = DEFAULT_LINE_SPACING,
    paragraph_spacing: float = DEFAULT_PARAGRAPH_SPACING,
    paginate: bool = False,
    font_proxy_url: str = "",
    timeout_seconds: int = 60,
    blocks: tuple[str | PictureBlock, ...] = (),
    share: ShareHeader | None = None,
    embedded: list[str] | None = None,
    original_keyword: str = "",
) -> List[str]:
    """将文本元数据渲染为本地 PNG 文件。

    Args:
        text: 待渲染的文本内容。
        output_path: 输出图片路径；分页时各页在文件名后追加页码。
        title: 图片顶部标题。
        width: 图片宽度。
        font_size: 正文文字大小。
        style: 图片渲染风格。
        font_family: 图片字体族。
        line_spacing: 行高相对文字高度的倍数，限制在 1.0–3.0。
        paragraph_spacing: 段落空行高度相对行高的倍数，限制在 0–3.0。
        paginate: 是否将超过最大高度的长图按行拆分为多页。
        font_proxy_url: 补全默认字体时使用的代理地址，为空时直接连接。
        blocks: 按原文顺序排列的文本与本地图片。
        share: 分享卡头部，省略时使用文字卡。
        embedded: 按绘制顺序登记的封面、图集与正文配图路径。
        original_keyword: 分享卡引用取原图的实际关键词，留空隐藏提示。
        timeout_seconds: 渲染超时时间（秒）。

    Returns:
        按阅读顺序排列的图片绝对路径。

    Raises:
        RuntimeError: Pillow 不可用、字体加载失败或图片未生成。
        ValueError: 文本为空或排版超过渲染资源预算。
        asyncio.TimeoutError: 渲染超时。
    """
    if not str(text or "").strip() and not blocks and not share:
        raise ValueError("没有可渲染的文本元数据")

    await ensure_render_fonts(font_family, font_proxy_url)
    visible_text = str(text) + str(title) + "".join(part for part in blocks if isinstance(part, str))
    if share:
        visible_text += share.author + share.platform + original_keyword
    if _has_emoji(visible_text) and not await asyncio.to_thread(_emoji_font_is_available):
        await ensure_emoji_font(font_proxy_url)
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    render_timeout = max(10, int(timeout_seconds or 60))
    task = asyncio.create_task(asyncio.to_thread(
        _render_text_metadata_image_sync,
        str(text),
        output,
        str(title or "媒体解析结果"),
        _normalize_width(width),
        _normalize_font_size(font_size),
        normalize_style(style),
        normalize_font_family(font_family),
        PAGINATED_MAX_HEIGHT if paginate else 0,
        _normalize_spacing(line_spacing, DEFAULT_LINE_SPACING, 1.0),
        _normalize_spacing(paragraph_spacing, DEFAULT_PARAGRAPH_SPACING, 0.0),
        blocks, share, embedded, original_keyword,
    ))
    completed = False
    try:
        paths = await asyncio.wait_for(
            asyncio.shield(task),
            timeout=render_timeout,
        )
        if not paths or any(
            not Path(path).is_file() or Path(path).stat().st_size <= 0 for path in paths
        ):
            raise RuntimeError("文本元数据图片未生成有效文件")
        completed = True
        return paths
    except (asyncio.CancelledError, asyncio.TimeoutError):
        # 线程无法随协程取消；观察真实结束，二次取消也不能跳过最终清理。
        completion = asyncio.gather(task, return_exceptions=True)
        while not completion.done():
            try:
                await asyncio.shield(completion)
            except asyncio.CancelledError:
                continue
        raise
    finally:
        if not completed:
            _remove_rendered_pages(output)


async def ensure_render_fonts(
    font_family: str = DEFAULT_RENDER_FONT_FAMILY,
    proxy_url: str = "",
) -> None:
    """优先使用有效本地字体，仅缺少可用字体时补全默认资源。

    Args:
        font_family: 文本图片配置的字体族。
        proxy_url: 补全默认字体时使用的代理地址。
    """
    if await asyncio.to_thread(_render_fonts_are_available, font_family):
        return
    await ensure_default_fonts(proxy_url)
    if not await asyncio.to_thread(_render_fonts_are_available, font_family):
        raise RuntimeError("默认字体补全后仍无法加载文本图片字体")


def _render_fonts_are_available(font_family: str) -> bool:
    """以实际字体加载同时确认普通和强调文字可以离线绘制。"""
    try:
        from PIL import ImageFont
    except ImportError as exc:
        raise RuntimeError("缺少 Pillow，无法渲染文本元数据图片") from exc
    try:
        _load_font(ImageFont, DEFAULT_RENDER_FONT_SIZE, font_family=font_family)
        _load_font(ImageFont, DEFAULT_RENDER_FONT_SIZE, True, font_family)
    except RuntimeError:
        return False
    return True


def _page_path(output: Path, index: int, total: int) -> Path:
    """返回单页输出路径，单页时沿用原路径。"""
    if total <= 1:
        return output
    return output.with_name(f"{output.stem}_p{index:02d}{output.suffix}")


def _remove_rendered_pages(output: Path) -> None:
    """删除当前输出路径已生成的单页或分页图片。"""
    for path in (output, *output.parent.glob(f"{output.stem}_p*{output.suffix}")):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _paginate_lines(lines: List[_TextLine], capacity: int) -> List[List[_TextLine]]:
    """按测量高度分页，让标题与紧随的内容尽量留在同一页。"""
    pages: List[List[_TextLine]] = []
    current: List[_TextLine] = []
    used = 0
    for index, line in enumerate(lines):
        required = line.height
        following = index
        while lines[following].keep_with_next and following + 1 < len(lines):
            following += 1
            required += lines[following].height
        # 特别长的标题允许逐行跨页；最后一行仍与下一条内容同行分页。
        if required > capacity:
            required = line.height
        if current and used + required > capacity:
            while current and current[-1].kind in ("blank", "separator"):
                current.pop()
            pages.append(current)
            current = []
            used = 0
        if not current and line.kind in ("blank", "separator"):
            continue
        current.append(line)
        used += line.height
    if current:
        while len(current) > 1 and current[-1].kind in ("blank", "separator"):
            current.pop()
        pages.append(current)
    return pages or [lines]


def _open_picture(path: str):
    """打开已下载图片，并将异常尺寸归入可恢复的渲染失败。"""
    from PIL import Image

    try:
        source = Image.open(path)
    except Image.DecompressionBombError as exc:
        raise ValueError("原图超过解码像素上限") from exc
    if source.width * source.height > MAX_RENDER_TOTAL_PIXELS:
        source.close()
        raise ValueError("原图超过解码像素上限")
    return source


def _gallery_crop(image) -> tuple[int, int, int, int] | None:
    """收窄图集原图连续的纯白外边，保留文字、透明区域和少量留白。"""
    from PIL import ImageOps

    if image.mode not in ("RGB", "RGBA") or "transparency" in image.info:
        image = image.convert("RGBA")
    w, h = image.size
    white = (255,) * len(image.getbands())
    if any(image.getpixel(point) != white for point in ((0, 0), (w-1, 0), (0, h-1), (w-1, h-1))):
        return None
    if image.mode == "RGBA":
        if image.getchannel("A").getextrema()[0] < 255:
            return None
        image = image.convert("RGB")
    content = ImageOps.invert(image).getbbox()
    if not content:
        return None
    left, top, right, bottom = content
    pad = round(min(w, h) * 0.03)
    box = (max(0, min(w // 20, left - pad)), max(0, min(h // 5, top - pad)),
           min(w, max(w - w // 20, right + pad)), min(h, max(h - h // 5, bottom + pad)))
    return box if box != (0, 0, w, h) else None


def _picture_geometry(path: str, gallery: bool = False) -> tuple[float, tuple[int, int, int, int] | None]:
    """按显示方向测量图片，记录图集预览的裁切范围供绘制复用。"""
    from PIL import ImageOps

    with _open_picture(path) as source:
        width, height = source.size
        orientation = source.getexif().get(274, 1)
        if orientation in (5, 6, 7, 8):
            width, height = height, width
        crop = None
        if gallery:
            crop = _gallery_crop(source if orientation == 1 else ImageOps.exif_transpose(source))
            if crop:
                left, top, right, bottom = crop
                width, height = right - left, bottom - top
    return width / height, crop


def _picture_lines(block: PictureBlock, width: int, max_height: int) -> list[_TextLine]:
    """测量配图或图集，分页和绘制复用每行的实际尺寸。"""
    if not block.paths:
        raise ValueError("图片块缺少本地文件")
    if block.kind != "gallery":
        height = width * 9 // 16 if block.kind == "cover" else round(width / _picture_geometry(block.paths[0])[0])
        height = max(1, min(height, max_height))
        return [_TextLine("", block.kind, height + PICTURE_GAP, pictures=block.paths)]

    lines = []
    start = 0
    while start < len(block.paths):
        # 最后四张分成两对，避免产生单独放大的尾图。
        count = 2 if len(block.paths) - start == 4 else 3
        paths = block.paths[start : start + count]
        ratios, crops = zip(*(_picture_geometry(path, gallery=True) for path in paths))
        available = width - PICTURE_GAP * (len(paths) - 1)
        # 限制极端长宽比的占位；图片自身始终按原比例完整显示。
        weights = [min(3.0, max(0.5, ratio)) for ratio in ratios]
        if len(block.paths) == 4:
            widths = [available // 2, available - available // 2]
            height = max(w / ratio for w, ratio in zip(widths, weights))
        else:
            height = available / sum(weights)
            widths = [round(height * weight) for weight in weights]
            widths[-1] += available - sum(widths)
        height = max(1, min(round(height), max_height))
        lines.append(_TextLine("", "gallery", height + PICTURE_GAP,
                               pictures=paths, picture_widths=tuple(widths), picture_crops=crops))
        start += len(paths)
    return lines


def _paste_pictures(canvas, paths, box, kind: str, widths: tuple[int, ...] = (), crops: tuple = ()) -> None:
    """解码当前行的图片并按用途裁切或等比缩放。"""
    from PIL import Image, ImageDraw, ImageOps

    x, y, width, height = box
    for index, (path, cell) in enumerate(zip(paths, widths or (width,), strict=True)):
        with _open_picture(path) as source:
            photo = ImageOps.exif_transpose(source).convert("RGBA")
            if crops and crops[index]:
                photo = photo.crop(crops[index])
            if kind in ("cover", "avatar"):
                thumb = ImageOps.fit(photo, (cell, height), method=Image.Resampling.LANCZOS)
            else:
                scale = min(cell / photo.width, height / photo.height)
                size = (max(1, round(photo.width * scale)), max(1, round(photo.height * scale)))
                thumb = photo.resize(size, Image.Resampling.LANCZOS)
            left = x + (cell - thumb.width) // 2
            top = y + (height - thumb.height) // 2
            if kind == "avatar":
                mask = Image.new("L", thumb.size)
                ImageDraw.Draw(mask).ellipse((0, 0, cell - 1, height - 1), fill=255)
                canvas.paste(thumb, (left, top), mask)
            else:
                canvas.paste(thumb, (left, top), thumb)
        x += cell + PICTURE_GAP
    if kind == "cover":
        overlay = ImageDraw.Draw(canvas, "RGBA")
        cx, cy = box[0] + width // 2, y + height // 2
        overlay.ellipse((cx-28, cy-28, cx+28, cy+28), fill=(0, 0, 0, 135))
        overlay.polygon(((cx-7, cy-13), (cx-7, cy+13), (cx+14, cy)), fill="white")


def _render_text_metadata_image_sync(
    text: str,
    output: Path,
    title: str,
    width: int,
    font_size: int,
    style: str,
    font_family: str,
    max_height: int = 0,
    line_spacing: float = DEFAULT_LINE_SPACING,
    paragraph_spacing: float = DEFAULT_PARAGRAPH_SPACING,
    blocks: tuple[str | PictureBlock, ...] = (),
    share: ShareHeader | None = None,
    embedded: list[str] | None = None,
    original_keyword: str = "",
) -> List[str]:
    """同步绘制文本元数据图片，max_height 大于 0 时按高度分页。"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("缺少 Pillow，无法渲染文本元数据图片") from exc

    palette = _style_palette(style)
    meta_size = max(MIN_RENDER_FONT_SIZE, round(font_size * 0.83))
    fonts = {
        "body": _load_font(ImageFont, font_size, font_family=font_family),
        "title": _load_font(ImageFont, round(font_size * 1.5), True, font_family),
        "section": _load_font(ImageFont, font_size, True, font_family),
        "author": _load_font(ImageFont, font_size, True, font_family),
        "meta": _load_font(ImageFont, meta_size, font_family=font_family),
        "label": _load_font(ImageFont, meta_size, True, font_family),
    }
    if _has_emoji(text + title + "".join(b for b in blocks if isinstance(b, str)) + (share.author + original_keyword if share else "")):
        fonts = {
            key: _FallbackFont(font, _load_emoji_font(ImageFont, font.size))
            for key, font in fonts.items()
        }
    with Image.new("RGB", (width, 1), palette["background"]) as probe:
        probe_draw = ImageDraw.Draw(probe)
        margin = max(24, width // 30)
        padding = max(28, width // 20)
        # 末行已包含行距，分享卡底部留白随字号调整。
        bottom_padding = min(padding, max(12, round(meta_size * 0.8))) if share else padding
        body_x = margin + padding
        content_width = width - body_x * 2
        page_font = fonts["meta"]
        header_text = share.platform if share else title.strip() or "媒体解析结果"
        header_width = content_width // 3 if share else content_width - 24
        header = _wrap_text(probe_draw, header_text, page_font, header_width)
        header_height = _line_height(probe_draw, page_font, 1.5)
        profile = []
        profile_height = 0
        if share:
            name_width = content_width - header_width - 16 - (76 if share.author else 0)
            for value, role in ((share.author, "section"), (share.timestamp, "meta")):
                if value:
                    font = fonts[role]
                    row_height = _line_height(probe_draw, font, 1.45)
                    for part in _wrap_text(probe_draw, value, font, name_width):
                        profile.append((part, role, row_height))
                        profile_height += row_height
            if share.author:
                profile_height = max(60, profile_height)
        body_top = margin + padding + max(profile_height, len(header) * header_height) + 24
        footer_height = header_height + 28
        capacity = max_height - body_top - bottom_padding - footer_height - margin
        if max_height and capacity <= PICTURE_GAP:
            raise ValueError("卡片头部超过分页高度")
        lines = []
        for block in blocks or ((text,) if text else ()):
            if isinstance(block, PictureBlock):
                if block.kind in CONTENT_PICTURE_KINDS and lines and lines[-1].text:
                    # 文字行自身已有行距，再补足图前留白；独立空行可在分页边缘自然移除。
                    lines.append(_TextLine("", "blank", round(font_size * 2 / 3), keep_with_next=True))
                lines.extend(_picture_lines(block, content_width, capacity - PICTURE_GAP if max_height else 2400))
            elif block.strip():
                if lines and lines[-1].kind in CONTENT_PICTURE_KINDS:
                    lines.append(_TextLine("", "blank", font_size, keep_with_next=True))
                lines.extend(_layout_text(
                    probe_draw, block, fonts, content_width, line_spacing, paragraph_spacing,
                ))
        if share and original_keyword and any(line.pictures for line in lines):
            hint = f"引用本消息并发送「{original_keyword}」，即可获取原图。"
            hint_rows = _wrap_text(probe_draw, hint, page_font, content_width)
            hint_line_height = _line_height(probe_draw, page_font, 1.25)
            hint_height = meta_size + len(hint_rows) * hint_line_height
            while lines[-1].kind in ("blank", "separator"):
                lines.pop()
            # 让提示与末段内容同页；末行若是满页长图，按剩余高度等比缩放。
            last = lines[-1]
            if last.pictures and max_height and capacity - hint_height > PICTURE_GAP:
                last.height = min(last.height, capacity - hint_height)
            last.keep_with_next = True
            lines.append(_TextLine("", "separator", meta_size, keep_with_next=True))
            lines.extend(_TextLine(row, "meta", hint_line_height, keep_with_next=i < len(hint_rows) - 1)
                         for i, row in enumerate(hint_rows))
    if blocks and not max_height:
        # 图文长图超过单页硬限制时分页，避免因一张超长图退回逐段发送原图。
        limit = min(MAX_RENDER_HEIGHT, MAX_RENDER_PAGE_PIXELS // width)
        if body_top + sum(line.height for line in lines) + bottom_padding + margin > limit:
            max_height = limit
            capacity = limit - body_top - bottom_padding - footer_height - margin
    pages = _paginate_lines(lines, capacity) if max_height > 0 else [lines]
    if len(pages) == 1:
        footer_height = 0
    heights = [
        body_top + sum(line.height for line in page) + bottom_padding + footer_height + margin
        for page in pages
    ]
    height_limit = min(max_height, MAX_RENDER_HEIGHT) if max_height > 0 else MAX_RENDER_HEIGHT
    if (
        len(pages) > MAX_RENDER_PAGES
        or any(height > height_limit or width * height > MAX_RENDER_PAGE_PIXELS for height in heights)
        or width * sum(heights) > MAX_RENDER_TOTAL_PIXELS
    ):
        raise ValueError("文本图片超出高度、页数或像素预算上限，请使用原文本")

    output.parent.mkdir(parents=True, exist_ok=True)
    written: List[str] = []
    completed = False
    try:
        for page_index, page_lines in enumerate(pages, start=1):
            height = heights[page_index - 1]
            card_bottom = height - footer_height - margin
            page_path = _page_path(output, page_index, len(pages))
            with Image.new("RGB", (width, height), palette["background"]) as image:
                draw = ImageDraw.Draw(image)
                _draw_page(
                    draw, page_lines, header=header, fonts=fonts, width=width,
                    margin=margin, padding=padding, body_top=body_top,
                    card_bottom=card_bottom, header_height=header_height, palette=palette,
                    canvas=image, share=share, profile=profile, embedded=embedded,
                )
                if len(pages) > 1:
                    page_label = f"{page_index:02d} / {len(pages):02d}"
                    _draw_text(
                        draw, (width - _text_width(draw, page_label, page_font)) / 2,
                        card_bottom + 18, page_label, page_font, palette["muted_text"],
                    )
                image.save(page_path, format="PNG")
            written.append(str(page_path))
        completed = True
    finally:
        if not completed:
            _remove_rendered_pages(output)
    return written


def _draw_page(
    draw: Any,
    body_lines: List[_TextLine],
    *,
    header: List[str],
    fonts: dict[str, object],
    width: int,
    margin: int,
    padding: int,
    body_top: int,
    card_bottom: int,
    header_height: int,
    palette: dict[str, str],
    canvas: Any = None,
    profile: tuple | list = (),
    share: ShareHeader | None = None,
    embedded: list[str] | None = None,
) -> None:
    """绘制有清楚阅读层级的单页卡片。"""
    draw.rounded_rectangle(
        (margin, margin + 3, width - margin, card_bottom + 3),
        radius=22,
        fill=palette["card_shadow"],
    )
    draw.rounded_rectangle(
        (margin, margin, width - margin, card_bottom),
        radius=22,
        fill=palette["card_fill"],
        outline=palette["card_outline"],
        width=1,
    )
    body_x = margin + padding
    header_y = margin + padding
    author_color = palette["accent"]
    if share:
        dark = palette["background"] == "#0D1420"
        if share.platform == "bilibili":
            author_color = "#73D2F6" if dark else "#087DA5"
        elif share.platform == "抖音":
            author_color = "#FF8CA5" if dark else "#B82A55"
        if share.author:
            draw.ellipse((body_x, header_y, body_x + 60, header_y + 60), fill=author_color)
            initial = _text_clusters(share.author)[0]
            initial_x = body_x + (60 - _text_width(draw, initial, fonts["section"])) / 2
            _draw_text(draw, initial_x, header_y + 14, initial, fonts["section"], palette["card_fill"])
            if share.avatar:
                try:
                    _paste_pictures(canvas, (share.avatar,), (body_x, header_y, 60, 60), "avatar")
                except (OSError, ValueError):
                    pass
        top = header_y
        for value, role, row_height in profile:
            _draw_text(draw, body_x + (76 if share.author else 0), top, value, fonts[role],
                       author_color if role == "section" else palette["muted_text"])
            top += row_height
    else:
        draw.rounded_rectangle(
            (body_x, header_y + 2, body_x + 4, header_y + 20),
            radius=2, fill=palette["accent"],
        )
    for index, line in enumerate(header):
        _draw_text(
            draw, (width - body_x - _text_width(draw, line, fonts["meta"]) if share else body_x + 18),
            header_y + index * header_height,
            line, fonts["meta"], palette["muted_text"],
        )
    body_y = body_top
    for line in body_lines:
        if line.pictures:
            _paste_pictures(canvas, line.pictures,
                            (body_x, body_y, width - 2 * body_x, line.height - PICTURE_GAP),
                            line.kind, line.picture_widths, line.picture_crops)
            if embedded is not None:
                embedded.extend(line.pictures)
        elif line.kind == "language_bar":
            bar_width = width - body_x * 2
            bar_height = max(7, fonts["meta"].size // 2)
            draw.rounded_rectangle((body_x, body_y, body_x + bar_width - 1, body_y + bar_height),
                                   radius=bar_height // 2, fill=palette["rule"])
            total = max(100, sum(item.percent for item in line.shares))
            left, accumulated = 0, 0.0
            for item in line.shares:
                accumulated += item.percent
                right = min(bar_width, round(accumulated / total * bar_width))
                if right > left:
                    draw.rectangle((body_x + left, body_y, body_x + right - 1, body_y + bar_height),
                                   fill=item.color)
                left = right
        elif line.kind == "language_legend":
            card_luminance = _relative_luminance(palette["card_fill"])
            for index, item in enumerate(line.shares):
                x = body_x + index * line.column_width
                radius = max(3, fonts["meta"].size // 5)
                top = body_y + fonts["meta"].size // 3
                dot_luminance = _relative_luminance(item.color)
                outline = None
                if dot_luminance <= card_luminance and (card_luminance + 0.05) / (dot_luminance + 0.05) < 3:
                    outline = palette["muted_text"]
                draw.ellipse((x, top, x + 2 * radius, top + 2 * radius), fill=item.color, outline=outline)
                _draw_text(draw, x + 2 * radius + 8, body_y, item.label, fonts["meta"], palette["body_text"])
        elif line.kind == "tree":
            draw.rectangle((body_x, body_y, width - body_x, body_y + line.height - 1), fill=palette["background"])
            step = line.column_width
            center = body_y + line.height // 2
            for level, edge in enumerate(line.label):
                x = body_x + 14 + level * step
                if edge != " ":
                    bottom = center if edge == "└" else body_y + line.height
                    draw.line((x, body_y, x, bottom), fill=palette["muted_text"])
                if edge in "├└":
                    draw.line((x, center, x + step // 2, center), fill=palette["muted_text"])
            color = palette["accent"] if line.tree_folder else palette["body_text"]
            font = fonts["label"] if line.tree_folder else fonts["meta"]
            _draw_text(draw, body_x + 14 + len(line.label) * step, body_y + 5, line.text, font, color)
        elif line.kind == "separator":
            rule_y = body_y + line.height // 2
            draw.line(
                (body_x, rule_y, width - body_x, rule_y),
                fill=palette["rule"], width=1,
            )
        elif line.kind != "blank":
            font = fonts[line.kind]
            color = (
                palette["muted_text"] if line.kind == "meta" else palette["body_text"]
            )
            if line.kind == "title":
                color = palette["title"]
            elif line.kind in ("section", "author"):
                color = palette["accent"]
            text_x = body_x
            if line.label:
                _draw_text(
                    draw, text_x, body_y, line.label,
                    fonts["label"], palette["muted_text"],
                )
                text_x += _text_width(draw, line.label, fonts["label"])
            _draw_text(draw, text_x, body_y, line.text, font, color)
        body_y += line.height


def _draw_text(draw: Any, x: float, y: float, text: str, font: object, color: str) -> None:
    """按字形实际边界对齐，避免字体上边距或负左边距导致溢出。"""
    for value, run_font in _font_runs(text, font):
        bbox = draw.textbbox((0, 0), value, font=run_font, anchor="lt")
        draw.text((x - bbox[0], y), value, font=run_font, fill=color, anchor="lt")
        x += bbox[2] - bbox[0]


class _FallbackFont:
    """保存正文和表情字体，让测量与绘制共用分段规则。"""

    def __init__(self, primary: object, emoji: object) -> None:
        self.primary = primary
        self.size = primary.size
        self.emoji = emoji
        self.coverage: dict[str, bool] = {}
        missing = emoji.getmask("\U0010ffff")
        self.missing = (missing.size, bytes(missing))

    def _supports_emoji(self, text: str) -> bool:
        """检查表情字体字形，避免原本可显示的符号被替换成缺字符。"""
        for char in text:
            if char in "\ufe0e\ufe0f\u200d" or 0xE0020 <= ord(char) <= 0xE007F:
                continue
            if char not in self.coverage:
                glyph = self.emoji.getmask(char)
                self.coverage[char] = (glyph.size, bytes(glyph)) != self.missing
            if not self.coverage[char]:
                return False
        return True


def _has_emoji(text: str) -> bool:
    """识别表情符号、旗帜、键帽和表情显示选择符。"""
    return bool(re.search(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u20E3]", text))


def _text_clusters(text: str) -> List[str]:
    """保留组合附加符、肤色、连接符序列及成对地区指示符。"""
    clusters: List[str] = []
    for char in text:
        code = ord(char)
        if clusters and (
            unicodedata.combining(char) or char in "\ufe0e\ufe0f\u20e3\u200d"
            or clusters[-1].endswith("\u200d")
            or 0x1F3FB <= code <= 0x1F3FF or 0xE0020 <= code <= 0xE007F
            or (0x1F1E6 <= code <= 0x1F1FF and len(clusters[-1]) == 1
                and 0x1F1E6 <= ord(clusters[-1]) <= 0x1F1FF)
        ):
            clusters[-1] += char
        else:
            clusters.append(char)
    return clusters


def _font_runs(text: str, font: object) -> List[tuple[str, object]]:
    """按完整表情序列选择字体，相邻同字体文字合并以保留字距。"""
    if not isinstance(font, _FallbackFont):
        return [(text, font)]
    runs: List[tuple[str, object]] = []
    for cluster in _text_clusters(text):
        selected = (
            font.emoji if _has_emoji(cluster) and "\ufe0e" not in cluster
            and font._supports_emoji(cluster) else font.primary
        )
        if runs and runs[-1][1] is selected:
            runs[-1] = (runs[-1][0] + cluster, selected)
        else:
            runs.append((cluster, selected))
    return runs


def _text_bbox(draw: Any, text: str, font: object) -> tuple[int, int, int, int]:
    """测量与实际绘制一致的混排宽度和可见高度。"""
    boxes = [draw.textbbox((0, 0), value, font=run_font, anchor="lt")
             for value, run_font in _font_runs(text, font)]
    return (0, 0, sum(box[2] - box[0] for box in boxes),
            max((box[3] - box[1] for box in boxes), default=0))


def _clean_inline_markdown(text: str) -> str:
    """去除成对强调标记，保留代码字面量和链接目标。"""
    # 代码、转义符和 URL 作为独立词元保留，不把其中的星号或下划线当成强调。
    tokens: dict[str, str] = {}

    def protect(match: re.Match[str]) -> str:
        key = f"\x00{len(tokens)}\x00"
        value = match[0]
        tokens[key] = value
        if value.startswith("\\"):
            tokens[key] = value[1:]
        return key

    def protect_code(match: re.Match[str]) -> str:
        key = f"\x00{len(tokens)}\x00"
        tokens[key] = match[2]
        return key

    text = re.sub(r"(`+)(.*?)\1", protect_code, text)
    text = re.sub(r"\\[\\`*_{}\[\]()#+.!~\-]", protect, text)
    text = re.sub(r"!?\[([^]\n]+)\]\((https?://[^\s)]+)\)",
                  lambda m: m[1] if m[1] == m[2] else f"{m[1]}（{m[2]}）", text)
    # 成对强调的右边界可能紧随链接，用非贪婪匹配保留边界标记。
    text = re.sub(r"https?://[^\s<>（）]+?(?=\*\*(?:\s|$)|[\s<>（）]|$)", protect, text)
    for _ in range(2):
        previous = text
        for pattern in (
            r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)",
            r"~~(?=\S)(.+?)(?<=\S)~~", r"(?<!\*)\*(?=\S)(.+?)(?<=\S)\*(?!\*)",
            r"(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)",
        ):
            text = re.sub(pattern, r"\1", text)
        if text == previous:
            break
    return re.sub(r"\x00\d+\x00", lambda m: tokens.get(m[0], m[0]), text)


def _load_emoji_font(image_font: Any, size: int) -> object:
    """优先加载可缩放的单色表情字体，避免位图字体字号限制。"""
    for path in (
        str(FONT_DIR / "NotoEmoji.ttf"),
        "/usr/share/fonts/truetype/noto/NotoEmoji-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoEmoji.ttf",
        "C:/Windows/Fonts/seguiemj.ttf",
    ):
        if not os.path.isfile(path):
            continue
        try:
            font = image_font.truetype(path, size)
            glyph, missing = font.getmask("💫"), font.getmask("\U0010ffff")
            if glyph.size != missing.size or bytes(glyph) != bytes(missing):
                return font
        except (OSError, ValueError):
            continue
    raise RuntimeError("未找到可用表情字体，请补全 Noto Emoji 字体")


def _emoji_font_is_available() -> bool:
    """确认本地表情字体可在正文和标题字号下绘制。"""
    from PIL import ImageFont

    try:
        _load_emoji_font(ImageFont, MIN_RENDER_FONT_SIZE)
        _load_emoji_font(ImageFont, round(MAX_RENDER_FONT_SIZE * 1.5))
    except RuntimeError:
        return False
    return True


def _relative_luminance(color: str) -> float:
    """计算十六进制 sRGB 颜色的相对亮度，用于判断图例圆点的对比度。"""
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    red, green, blue = [
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _language_lines(draw: Any, value: str, fonts: dict[str, object], width: int) -> List[_TextLine]:
    """从完整的语言占比行排出色条与等宽图例列；异常格式沿用原文。"""
    colors = {"Python": "#3572A5", "Vue": "#41B883", "TypeScript": "#3178C6",
              "JavaScript": "#D7B632", "HTML": "#E36B45", "CSS": "#8064AA",
              "Java": "#B07219", "Go": "#00A5C6", "Shell": "#78A953"}
    fallback = ("#A67796", "#647E9C", "#AC8057", "#568D87", "#8377A6", "#9B9056")
    shares = []
    for index, token in enumerate(value.removeprefix("语言占比：").split(" · ")):
        match = re.fullmatch(r"(.+?) ([<>]?)(\d{1,3}(?:\.\d{1,2})?)%", token)
        if not match:
            return []
        percent = float(match[3])
        if percent > 100:
            return []
        shares.append(_LanguageShare(token, colors.get(match[1], fallback[index % len(fallback)]), percent))
    font = fonts["meta"]
    widest = max(_text_width(draw, item.label, font) for item in shares) + font.size + 20
    if widest > width:
        return []
    columns = min(3, max(1, width // widest))
    height = _line_height(draw, font, 1.6)
    lines = [_TextLine("语言占比", "section", _line_height(draw, fonts["section"], 1.6), keep_with_next=True),
             _TextLine("", "language_bar", height, shares=tuple(shares), keep_with_next=True)]
    for index in range(0, len(shares), columns):
        lines.append(_TextLine("", "language_legend", height, shares=tuple(shares[index:index + columns]),
                               column_width=width // columns))
    return lines


def _layout_text(
    draw: Any,
    text: str,
    fonts: dict[str, object],
    max_width: int,
    line_spacing: float,
    paragraph_spacing: float,
) -> List[_TextLine]:
    """按标题、辅助信息、正文与评论角色测量每一行。"""
    lines: List[_TextLine] = []
    body_height = _line_height(draw, fonts["body"], line_spacing)
    fence = ""
    tree = False
    for raw_line in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        value = raw_line.strip()
        marker = re.match(r"^(`{3,}|~{3,})(.*)$", value)
        if marker:
            if not fence:
                fence = marker[1]
                tree = marker[2].strip() == "tree"
                continue
            if marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = ""
                tree = False
                continue
        if not value:
            if lines and lines[-1].kind not in ("blank", "separator"):
                lines.append(_TextLine(
                    "", "blank", round(body_height * paragraph_spacing),
                    keep_with_next=lines[-1].keep_with_next,
                ))
            continue
        if not fence and value == TEXT_SECTION_SEPARATOR:
            if lines and lines[-1].kind == "blank":
                lines.pop()
            lines.append(_TextLine("", "separator", body_height, keep_with_next=True))
            continue
        if not fence and value.startswith("语言占比："):
            language_lines = _language_lines(draw, value, fonts, max_width)
            if language_lines:
                lines.extend(language_lines)
                continue
        if tree:
            # 保留空格前缀，最后一个父节点的子项没有左侧竖线。
            node = re.fullmatch(r"((?:│   |    )*)([├└])── (.+)", raw_line)
            if node:
                guides = node[1][::4] + node[2]
                folder = node[3].endswith("/")
                font = fonts["label"] if folder else fonts["meta"]
                step = font.size + 4
                available = max_width - 28 - len(guides) * step
                if available > font.size:
                    parts = _wrap_text(draw, node[3], font, available)
                    if lines and lines[-1].kind == "tree" and len(lines[-1].label) < len(guides):
                        lines[-1].keep_with_next = True
                    height = _line_height(draw, font, 1.4) + 4
                    for index, part in enumerate(parts):
                        lines.append(_TextLine(part, "tree", height, guides, column_width=step,
                                               tree_folder=folder, keep_with_next=index + 1 < len(parts)))
                        guides = guides.replace("├", "│").replace("└", " ")
                    continue
        label, content = _split_label(value) if not fence else ("", "")
        kind = "body"
        heading = re.match(r"^#{1,6}\s+(.+?)(?:\s+#+)?$", value) if not fence else None
        if heading:
            kind, value, label = "section", heading[1], ""
        elif label.rstrip("：:") == "标题":
            kind, value, label = "title", content.strip(), ""
        elif not fence and (value in ("简介/正文：", "简介/正文:", "仓库简介：", "翻译") or re.fullmatch(
            r"(?:热评|分集弹幕|翻译)[（(].+?[）)][：:]?", value,
        )):
            kind, label = "section", ""
            value = value.rstrip("：:")
        elif not fence and re.match(r"^\[\d+\]\s+", value):
            kind, label = "author", ""
        elif label:
            kind, value = "meta", content
        if not fence:
            value = _clean_inline_markdown(value)
        font = fonts[kind]
        first_width = max_width - (
            _text_width(draw, label, fonts["label"]) if label else 0
        )
        wrapped = _wrap_text(draw, value, font, max_width, first_line_width=first_width)
        factor = line_spacing if kind in ("body", "meta") else 1.5
        height = _line_height(draw, font, factor)
        keep_with_next = (
            kind in ("title", "section", "author") or label.rstrip("：:") == "点赞"
        )
        for index, line in enumerate(wrapped):
            bbox = _text_bbox(draw, line, font)
            actual_height = bbox[3] - bbox[1]
            if index == 0 and label:
                label_bbox = _text_bbox(draw, label, fonts["label"])
                actual_height = max(actual_height, label_bbox[3] - label_bbox[1])
            lines.append(_TextLine(
                line, kind, max(height, actual_height + 2),
                label if index == 0 else "",
                keep_with_next,
            ))
        if kind in ("title", "section"):
            lines[-1].height += max(10, body_height // 3)
    while lines and lines[-1].kind in ("blank", "separator"):
        lines.pop()
    return lines or [_TextLine("（无文本内容）", "body", body_height)]


def _wrap_text(
    draw: object, text: str, font: object, max_width: int,
    *, first_line_width: int | None = None,
) -> List[str]:
    """按实际字形宽度换行，优先保留英文单词并避免中文标点悬挂。"""
    value = str(text or "").strip()
    if not value:
        return [""]
    lines: List[str] = []
    units = _text_clusters(value)
    start = 0
    while start < len(units):
        available = (
            first_line_width if not lines and first_line_width is not None else max_width
        )
        end = start + 1
        while end < len(units) and _text_width(draw, "".join(units[start:end + 1]), font) <= available:
            end += 1
        if end < len(units):
            boundary = end
            # 能在一个单词前断行时保留单词，超长英文词或链接按字形拆分。
            if (
                units[end - 1].isascii() and units[end].isascii()
                and units[end - 1].isalnum() and units[end].isalnum()
            ):
                space = next((i for i in range(end - 1, start, -1) if units[i] == " "), -1)
                if space > start:
                    boundary = space + 1
            while boundary > start + 1 and (
                units[boundary] in NO_LINE_START_CHARS
                or units[boundary - 1] in NO_LINE_END_CHARS
            ):
                boundary -= 1
            end = boundary
        lines.append("".join(units[start:end]).rstrip())
        start = end
        while start < len(units) and units[start] == " ":
            start += 1
    return lines or [value]


def _split_label(line: str) -> tuple[str, str]:
    """拆分常见的字段标签，让图片中的元数据更易扫描。"""
    for separator in ("：", ":"):
        if separator in line:
            index = line.find(separator) + len(separator)
            label = line[:index].strip()
            value = line[index:]
            if label.rstrip("：:") in METADATA_LABELS and value:
                return label, value
            break
    return "", ""


def _text_width(draw: object, text: str, font: object) -> int:
    bbox = _text_bbox(draw, str(text or ""), font)
    return max(1, int(bbox[2] - bbox[0]))


def _line_height(draw: object, font: object, factor: float) -> int:
    bbox = _text_bbox(draw, "媒体解析Ag", font)
    return max(1, int((bbox[3] - bbox[1]) * factor))


def _normalize_width(value: object) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed = DEFAULT_RENDER_WIDTH
    return max(640, min(1600, parsed))


def _normalize_spacing(value: object, default: float, minimum: float) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed = default
    if parsed != parsed:
        parsed = default
    return max(minimum, min(3.0, parsed))


def _normalize_font_size(value: object) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed = DEFAULT_RENDER_FONT_SIZE
    return max(MIN_RENDER_FONT_SIZE, min(MAX_RENDER_FONT_SIZE, parsed))


def _style_palette(style: str) -> dict[str, str]:
    """返回渲染风格对应的颜色方案。"""
    palettes = {
        "fresh": {
            "background": "#F5F0F0",
            "card_fill": "#FFFCFC",
            "card_outline": "#EAE0E2",
            "card_shadow": "#EAE3E5",
            "title": "#322D35",
            "accent": "#984D66",
            "muted_text": "#7B707A",
            "body_text": "#403A43",
            "rule": "#EDE3E7",
        },
        "tech": {
            "background": "#0D1420",
            "card_fill": "#162131",
            "card_outline": "#28384B",
            "card_shadow": "#080F19",
            "title": "#EDF5FC",
            "accent": "#82C7E8",
            "muted_text": "#A0B0C1",
            "body_text": "#D4DEE9",
            "rule": "#2C3C4F",
        },
        "serious": {
            "background": "#EDF0F4",
            "card_fill": "#FFFFFF",
            "card_outline": "#DFE4EA",
            "card_shadow": "#DEE3EA",
            "title": "#1E293B",
            "accent": "#355A7E",
            "muted_text": "#697586",
            "body_text": "#354052",
            "rule": "#E2E7ED",
        },
        "card": {
            "background": "#F0F2ED",
            "card_fill": "#FDFDF9",
            "card_outline": "#E0E5DA",
            "card_shadow": "#E0E5DB",
            "title": "#2C3831",
            "accent": "#53745F",
            "muted_text": "#616E62",
            "body_text": "#3F4A42",
            "rule": "#E4E8DF",
        },
    }
    return palettes.get(normalize_style(style), palettes[DEFAULT_RENDER_STYLE])


def _load_font(
    image_font: object,
    size: int,
    bold: bool = False,
    font_family: str = DEFAULT_RENDER_FONT_FAMILY,
) -> object:
    """按常见部署环境尝试加载中文字体，找不到时交由上层回退文本。"""
    configured_path = str(
        os.environ.get("ASTRBOT_MEDIA_PARSER_FONT", "") or ""
    ).strip()
    family = normalize_font_family(font_family)
    family_paths = {
        "noto_sans": (
            str(FONT_DIR / "NotoSansCJKsc-Bold.otf")
            if bold
            else str(FONT_DIR / "NotoSansCJKsc-Regular.otf"),
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
            if bold
            else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"
            if bold
            else "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        ),
        "noto_serif": (
            "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc"
            if bold
            else "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSerifCJK-Bold.ttc"
            if bold
            else "/usr/share/fonts/truetype/noto/NotoSerifCJK-Regular.ttc",
            "C:/Windows/Fonts/simsun.ttc",
        ),
        "lxgw_wenkai": (
            "/usr/share/fonts/truetype/lxgw/LXGWWenKai-Medium.ttf"
            if bold
            else "/usr/share/fonts/truetype/lxgw/LXGWWenKai-Regular.ttf",
        ),
        "zcool_xiaowei": (
            "/usr/share/fonts/truetype/zcool/ZCOOLXiaoWei-Regular.ttf",
        ),
        "zcool_qingke": (
            "/usr/share/fonts/truetype/zcool/ZCOOLQingKeHuangYou-Regular.ttf",
        ),
    }
    candidates = [
        configured_path,
        *family_paths.get(family, ()),
    ]
    if bold:
        # 没有专用粗体时沿用相同字体族的常规字体，不因强调文字强制联网。
        candidates.extend(
            candidate.replace("-Bold", "-Regular").replace("-Medium", "-Regular").replace("msyhbd", "msyh")
            for candidate in family_paths.get(family, ())
        )
    if family != "noto_sans":
        candidates.extend(family_paths["noto_sans"])
        if bold:
            candidates.extend(
                candidate.replace("-Bold", "-Regular").replace("msyhbd", "msyh")
                for candidate in family_paths["noto_sans"]
            )
    candidates.append("C:/Windows/Fonts/simhei.ttf")
    for candidate in candidates:
        if not candidate or not os.path.isfile(candidate):
            continue
        try:
            return image_font.truetype(candidate, size)
        except (OSError, ValueError):
            continue
    raise RuntimeError(
        "未找到可用中文字体，请安装 Noto Sans CJK、微软雅黑或配置 "
        "ASTRBOT_MEDIA_PARSER_FONT"
    )
