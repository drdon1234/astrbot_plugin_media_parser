"""文本元数据图片渲染器，负责将文本节点绘制为 PNG 图片，并按需分页。"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List

from .font_manager import FONT_DIR, ensure_default_fonts


DEFAULT_RENDER_WIDTH = 960
DEFAULT_RENDER_FONT_SIZE = 24
DEFAULT_RENDER_STYLE = "fresh"
DEFAULT_RENDER_FONT_FAMILY = "noto_sans"
MIN_RENDER_FONT_SIZE = 16
MAX_RENDER_FONT_SIZE = 42
DEFAULT_LINE_SPACING = 1.55
DEFAULT_PARAGRAPH_SPACING = 1.0
PAGINATED_MAX_HEIGHT = 1800
MAX_RENDER_HEIGHT = 16384
MAX_RENDER_PAGE_PIXELS = 16000000
MAX_RENDER_TOTAL_PIXELS = 32000000
MAX_RENDER_PAGES = 20
TEXT_SECTION_SEPARATOR = "-------------------------------------"
NO_LINE_START_CHARS = "，。！？；：、,.!?;:)]}）】》〉」』”’％%…"
NO_LINE_END_CHARS = "([{（【《〈「『“‘"
METADATA_LABELS = frozenset(
    {
        "标题", "作者", "发布时间", "视频大小", "时长", "访问提示", "解析失败",
        "原始链接", "点赞", "时间", "简介/正文",
    }
)


@dataclass
class _TextLine:
    """保存测量后的文字行，使绘制与分页共用同一份尺寸。"""

    text: str
    kind: str
    height: int
    label: str = ""
    keep_with_next: bool = False


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
        timeout_seconds: 渲染超时时间（秒）。

    Returns:
        按阅读顺序排列的图片绝对路径。

    Raises:
        RuntimeError: Pillow 不可用、字体加载失败或图片未生成。
        ValueError: 文本为空或排版超过渲染资源预算。
        asyncio.TimeoutError: 渲染超时。
    """
    if not str(text or "").strip():
        raise ValueError("没有可渲染的文本元数据")

    await ensure_render_fonts(font_family, font_proxy_url)
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
        _normalize_style(style),
        _normalize_font_family(font_family),
        PAGINATED_MAX_HEIGHT if paginate else 0,
        _normalize_spacing(line_spacing, DEFAULT_LINE_SPACING, 1.0),
        _normalize_spacing(paragraph_spacing, DEFAULT_PARAGRAPH_SPACING, 0.0),
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
    with Image.new("RGB", (width, 1), palette["background"]) as probe:
        probe_draw = ImageDraw.Draw(probe)
        margin = max(24, width // 30)
        padding = max(28, width // 20)
        body_x = margin + padding
        content_width = width - body_x * 2
        lines = _layout_text(
            probe_draw, text, fonts, content_width, line_spacing, paragraph_spacing,
        )
        page_font = fonts["meta"]
        header = _wrap_text(
            probe_draw, title.strip() or "媒体解析结果", page_font, content_width - 24,
        )
        header_height = _line_height(probe_draw, page_font, 1.5)
    body_top = margin + padding + len(header) * header_height + 24
    footer_height = header_height + 28
    capacity = max_height - body_top - padding - footer_height - margin
    pages = _paginate_lines(lines, capacity) if max_height > 0 else [lines]
    if len(pages) == 1:
        footer_height = 0
    heights = [
        body_top + sum(line.height for line in page) + padding + footer_height + margin
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
            card_bottom = body_top + sum(line.height for line in page_lines) + padding
            height = card_bottom + footer_height + margin
            page_path = _page_path(output, page_index, len(pages))
            with Image.new("RGB", (width, height), palette["background"]) as image:
                draw = ImageDraw.Draw(image)
                _draw_page(
                    draw, page_lines, header=header, fonts=fonts, width=width,
                    margin=margin, padding=padding, body_top=body_top,
                    card_bottom=card_bottom, header_height=header_height, palette=palette,
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
    draw.rounded_rectangle(
        (body_x, header_y + 2, body_x + 4, header_y + 20),
        radius=2, fill=palette["accent"],
    )
    for index, line in enumerate(header):
        _draw_text(
            draw, body_x + 18, header_y + index * header_height,
            line, fonts["meta"], palette["muted_text"],
        )
    body_y = body_top
    for line in body_lines:
        if line.kind == "separator":
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
    left = draw.textbbox((0, 0), text, font=font, anchor="lt")[0]
    draw.text((x - left, y), text, font=font, fill=color, anchor="lt")


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
    for raw_line in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        value = raw_line.strip()
        if not value:
            if lines and lines[-1].kind not in ("blank", "separator"):
                lines.append(_TextLine(
                    "", "blank", round(body_height * paragraph_spacing),
                    keep_with_next=lines[-1].keep_with_next,
                ))
            continue
        if value == TEXT_SECTION_SEPARATOR:
            if lines and lines[-1].kind == "blank":
                lines.pop()
            lines.append(_TextLine("", "separator", body_height, keep_with_next=True))
            continue
        label, content = _split_label(value)
        kind = "body"
        if label.rstrip("：:") == "标题":
            kind, value, label = "title", content.strip(), ""
        elif value in ("简介/正文：", "简介/正文:", "翻译") or re.fullmatch(
            r"(?:热评|分集弹幕|翻译)[（(].+?[）)][：:]?", value,
        ):
            kind, label = "section", ""
            value = value.rstrip("：:")
        elif re.match(r"^\[\d+\]\s+", value):
            kind, label = "author", ""
        elif label:
            kind, value = "meta", content
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
            bbox = draw.textbbox((0, 0), line, font=font)
            actual_height = bbox[3] - bbox[1]
            if index == 0 and label:
                label_bbox = draw.textbbox((0, 0), label, font=fonts["label"])
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
    start = 0
    while start < len(value):
        available = (
            first_line_width if not lines and first_line_width is not None else max_width
        )
        end = start + 1
        while end < len(value) and _text_width(draw, value[start:end + 1], font) <= available:
            end += 1
        if end < len(value):
            boundary = end
            # 能在一个单词前断行时保留单词，超长英文词或链接按字形拆分。
            if (
                value[end - 1].isascii() and value[end].isascii()
                and value[end - 1].isalnum() and value[end].isalnum()
            ):
                space = value.rfind(" ", start, end)
                if space > start:
                    boundary = space + 1
            while boundary > start + 1 and (
                value[boundary] in NO_LINE_START_CHARS
                or value[boundary - 1] in NO_LINE_END_CHARS
            ):
                boundary -= 1
            end = boundary
        lines.append(value[start:end].rstrip())
        start = end
        while start < len(value) and value[start] == " ":
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
    bbox = draw.textbbox((0, 0), str(text or ""), font=font)
    return max(1, int(bbox[2] - bbox[0]))


def _line_height(draw: object, font: object, factor: float) -> int:
    bbox = draw.textbbox((0, 0), "媒体解析Ag", font=font)
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


def _normalize_style(value: object) -> str:
    text = str(value or "").strip()
    lowered = text.casefold()
    aliases = {
        "清新便签": "fresh",
        "清新": "fresh",
        "便签": "fresh",
        "粉色便签": "fresh",
        "fresh": "fresh",
        "bilinote": "fresh",
        "科技感": "tech",
        "科技": "tech",
        "tech": "tech",
        "technology": "tech",
        "专业严肃": "serious",
        "严肃": "serious",
        "专业": "serious",
        "serious": "serious",
        "professional": "serious",
        "温和卡片": "card",
        "卡片": "card",
        "card": "card",
        "soft_card": "card",
    }
    return aliases.get(text, aliases.get(lowered, DEFAULT_RENDER_STYLE))


def _normalize_font_family(value: object) -> str:
    text = str(value or "").strip()
    lowered = text.casefold()
    aliases = {
        "默认黑体": "noto_sans",
        "黑体": "noto_sans",
        "思源黑体": "noto_sans",
        "noto_sans": "noto_sans",
        "noto sans": "noto_sans",
        "default": "noto_sans",
        "专业宋体": "noto_serif",
        "宋体": "noto_serif",
        "思源宋体": "noto_serif",
        "noto_serif": "noto_serif",
        "noto serif": "noto_serif",
        "serif": "noto_serif",
        "清新文楷": "lxgw_wenkai",
        "文楷": "lxgw_wenkai",
        "霞鹜文楷": "lxgw_wenkai",
        "lxgw_wenkai": "lxgw_wenkai",
        "lxgw wenkai": "lxgw_wenkai",
        "wenkai": "lxgw_wenkai",
        "标题手札": "zcool_xiaowei",
        "站酷小薇": "zcool_xiaowei",
        "zcool_xiaowei": "zcool_xiaowei",
        "zcool xiaowei": "zcool_xiaowei",
        "xiaowei": "zcool_xiaowei",
        "科技窄体": "zcool_qingke",
        "站酷庆科黄油体": "zcool_qingke",
        "zcool_qingke": "zcool_qingke",
        "zcool qingke": "zcool_qingke",
        "qingke": "zcool_qingke",
    }
    return aliases.get(text, aliases.get(lowered, DEFAULT_RENDER_FONT_FAMILY))


def _style_palette(style: str) -> dict[str, str]:
    """返回渲染风格对应的颜色方案。"""
    palettes = {
        "fresh": {
            "background": "#F5F0F0",
            "card_fill": "#FFFCFC",
            "card_outline": "#EAE0E2",
            "card_shadow": "#EAE3E5",
            "title": "#322D35",
            "accent": "#A65770",
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
            "muted_text": "#727C71",
            "body_text": "#3F4A42",
            "rule": "#E4E8DF",
        },
    }
    return palettes.get(_normalize_style(style), palettes[DEFAULT_RENDER_STYLE])


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
    family = _normalize_font_family(font_family)
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
