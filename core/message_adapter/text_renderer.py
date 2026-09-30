"""文本元数据图片渲染器，负责将文本节点绘制为 PNG 图片，并按需分页。"""

from __future__ import annotations

import asyncio
import os
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
TEXT_SECTION_SEPARATOR = "-------------------------------------"
NO_LINE_START_CHARS = "，。！？；：、,.!?;:)]}）】》"


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
        asyncio.TimeoutError: 渲染超时。
    """
    if not str(text or "").strip():
        raise ValueError("没有可渲染的文本元数据")

    await ensure_default_fonts(font_proxy_url)
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        paths = await asyncio.wait_for(
            asyncio.to_thread(
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
            ),
            timeout=max(10, int(timeout_seconds or 60)),
        )
    except (asyncio.CancelledError, asyncio.TimeoutError):
        _remove_rendered_pages(output)
        raise
    if not paths or any(
        not Path(path).is_file() or Path(path).stat().st_size <= 0 for path in paths
    ):
        _remove_rendered_pages(output)
        raise RuntimeError("文本元数据图片未生成有效文件")
    return paths


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


def _paginate_lines(
    lines: List[str],
    heights: List[int],
    capacity: int,
) -> List[List[str]]:
    """按可用高度拆分正文行，页首不保留空行或分隔线。"""
    pages: List[List[str]] = []
    current: List[str] = []
    used = 0
    for line, height in zip(lines, heights):
        if current and used + height > capacity:
            pages.append(current)
            current = []
            used = 0
        if not current and line in ("", TEXT_SECTION_SEPARATOR):
            continue
        current.append(line)
        used += height
    if current:
        pages.append(current)
    for page in pages:
        while len(page) > 1 and page[-1] in ("", TEXT_SECTION_SEPARATOR):
            page.pop()
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
    regular_font = _load_font(ImageFont, font_size, font_family=font_family)
    title_font = _load_font(
        ImageFont,
        max(font_size + 4, int(font_size * 1.35)),
        True,
        font_family,
    )
    label_font = _load_font(
        ImageFont,
        max(font_size, int(font_size * 1.05)),
        True,
        font_family,
    )

    probe = Image.new("RGB", (width, 200), palette["background"])
    probe_draw = ImageDraw.Draw(probe)
    margin = max(34, width // 12)
    content_width = width - margin * 2
    title_lines = _wrap_text(
        probe_draw,
        title.strip() or "媒体解析结果",
        title_font,
        content_width,
    )
    body_lines = _wrap_text_multiline(
        probe_draw,
        text,
        regular_font,
        content_width - 42,
    )
    title_line_height = _line_height(probe_draw, title_font, 1.35)
    body_line_height = _line_height(probe_draw, regular_font, line_spacing)
    blank_line_height = round(body_line_height * paragraph_spacing)
    label_line_height = _line_height(probe_draw, label_font, 1.4)
    separator_height = max(body_line_height // 2, label_line_height // 2)
    line_heights = [
        _row_height(line, body_line_height, blank_line_height, separator_height)
        for line in body_lines
    ]

    page_font = _load_font(
        ImageFont,
        max(MIN_RENDER_FONT_SIZE, font_size - 4),
        font_family=font_family,
    )
    top_padding = 46
    title_gap = 30
    card_top = top_padding + len(title_lines) * title_line_height + title_gap
    # 分页时底部留白需容纳页码；卡片上下内边距与底部留白不计入正文行高度。
    paged_bottom = max(46, _line_height(probe_draw, page_font, 1.2) + 24)
    page_capacity = max_height - card_top - 34 - 34 - paged_bottom
    if max_height > 0 and page_capacity >= body_line_height:
        pages = _paginate_lines(body_lines, line_heights, page_capacity)
    else:
        pages = [body_lines]
    bottom_space = paged_bottom if len(pages) > 1 else 46

    output.parent.mkdir(parents=True, exist_ok=True)
    written: List[str] = []
    try:
        for page_index, page_lines in enumerate(pages, start=1):
            page_heights = [
                _row_height(line, body_line_height, blank_line_height, separator_height)
                for line in page_lines
            ]
            card_bottom = card_top + 34 + sum(page_heights) + 34
            height = card_bottom + bottom_space
            image = Image.new("RGB", (width, height), palette["background"])
            draw = ImageDraw.Draw(image)
            _draw_background(draw, width, height, palette["background_dot"])
            _draw_page(
                draw,
                page_lines,
                title_lines=title_lines,
                width=width,
                margin=margin,
                top_padding=top_padding,
                card_top=card_top,
                card_bottom=card_bottom,
                title_font=title_font,
                label_font=label_font,
                regular_font=regular_font,
                title_line_height=title_line_height,
                body_line_height=body_line_height,
                blank_line_height=blank_line_height,
                separator_height=separator_height,
                palette=palette,
            )
            if len(pages) > 1:
                page_label = f"{page_index} / {len(pages)}"
                draw.text(
                    (
                        (width - _text_width(draw, page_label, page_font)) / 2,
                        card_bottom + 14,
                    ),
                    page_label,
                    font=page_font,
                    fill=palette["label_text"],
                )
            page_path = _page_path(output, page_index, len(pages))
            image.save(page_path, format="PNG")
            written.append(str(page_path))
    except (OSError, ValueError):
        for path in written:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return written


def _draw_page(
    draw: Any,
    body_lines: List[str],
    *,
    title_lines: List[str],
    width: int,
    margin: int,
    top_padding: int,
    card_top: int,
    card_bottom: int,
    title_font: object,
    label_font: object,
    regular_font: object,
    title_line_height: int,
    body_line_height: int,
    blank_line_height: int,
    separator_height: int,
    palette: dict[str, str],
) -> None:
    """绘制单页标题、便签卡片与正文行。"""
    for index, line in enumerate(title_lines):
        line_width = _text_width(draw, line, title_font)
        draw.text(
            ((width - line_width) / 2, top_padding + index * title_line_height),
            line,
            font=title_font,
            fill=palette["title"],
        )

    card_x0 = margin
    card_x1 = width - margin
    draw.rounded_rectangle(
        (card_x0 + 5, card_top + 5, card_x1 + 5, card_bottom + 5),
        radius=16,
        fill=palette["card_shadow"],
    )
    draw.rounded_rectangle(
        (card_x0, card_top, card_x1, card_bottom),
        radius=16,
        fill=palette["card_fill"],
        outline=palette["card_outline"],
        width=2,
    )

    tape_width = min(150, max(100, width // 6))
    tape_x0 = (width - tape_width) // 2
    draw.rectangle(
        (tape_x0, card_top - 13, tape_x0 + tape_width, card_top + 12),
        fill=palette["tape_fill"],
    )

    body_x = card_x0 + 28
    body_y = card_top + 24
    for line in body_lines:
        if line == TEXT_SECTION_SEPARATOR:
            rule_y = body_y + max(8, body_line_height // 2)
            draw.line(
                (body_x, rule_y, card_x1 - 28, rule_y),
                fill=palette["rule"],
                width=2,
            )
            body_y += separator_height
            continue
        if not line:
            body_y += blank_line_height
            continue

        label, value = _split_label(line)
        if label and value:
            draw.text(
                (body_x, body_y),
                label,
                font=label_font,
                fill=palette["label_text"],
            )
            label_width = _text_width(draw, label, label_font)
            draw.text(
                (body_x + label_width, body_y),
                value,
                font=regular_font,
                fill=palette["body_text"],
            )
        else:
            draw.text(
                (body_x, body_y),
                line,
                font=regular_font,
                fill=palette["body_text"],
            )
        body_y += body_line_height


def _draw_background(
    draw: object,
    width: int,
    height: int,
    dot_color: str,
) -> None:
    """绘制低对比度便签背景。"""
    for y in range(7, height, 14):
        for x in range(7, width, 14):
            draw.ellipse((x - 1, y - 1, x + 1, y + 1), fill=dot_color)


def _wrap_text_multiline(
    draw: object,
    text: str,
    font: object,
    max_width: int,
) -> List[str]:
    """按原始换行拆分并对每行进行中文安全换行。"""
    lines: List[str] = []
    raw_lines = (
        str(text or "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .split("\n")
    )
    for raw_line in raw_lines:
        if raw_line == "":
            lines.append("")
            continue
        wrapped = _wrap_text(draw, raw_line, font, max_width)
        lines.extend(wrapped)
    return lines or ["（无文本内容）"]


def _wrap_text(draw: object, text: str, font: object, max_width: int) -> List[str]:
    value = str(text or "").strip()
    if not value:
        return [""]
    lines: List[str] = []
    current = ""
    for char in value:
        candidate = current + char
        if not current or _text_width(draw, candidate, font) <= max_width:
            current = candidate
            continue
        if char in NO_LINE_START_CHARS:
            current = candidate
            continue
        lines.append(current.rstrip())
        current = char.lstrip()
    if current:
        lines.append(current.rstrip())
    return lines or [value]


def _split_label(line: str) -> tuple[str, str]:
    """拆分常见的字段标签，让图片中的元数据更易扫描。"""
    for separator in ("：", ":"):
        if separator in line:
            index = line.find(separator) + len(separator)
            label = line[:index].strip()
            value = line[index:]
            if 0 < len(label) <= 12 and value:
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


def _row_height(
    line: str,
    body_line_height: int,
    blank_line_height: int,
    separator_height: int,
) -> int:
    """返回正文行、段落空行或分隔线占用的高度。"""
    if line == TEXT_SECTION_SEPARATOR:
        return separator_height
    return body_line_height if line else blank_line_height


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
            "background": "#fdeef4",
            "background_dot": "#f4c1d2",
            "card_fill": "#f9ded8",
            "card_outline": "#f4b7bd",
            "card_shadow": "#efb9b6",
            "tape_fill": "#fde6b6",
            "title": "#ff6389",
            "label_text": "#176c72",
            "body_text": "#51413f",
            "rule": "#e4aeb0",
        },
        "tech": {
            "background": "#07111f",
            "background_dot": "#173a60",
            "card_fill": "#0d1828",
            "card_outline": "#2d9cff",
            "card_shadow": "#050b14",
            "tape_fill": "#38e7ff",
            "title": "#6ee7ff",
            "label_text": "#7cffc6",
            "body_text": "#d7e7f7",
            "rule": "#2b78d6",
        },
        "serious": {
            "background": "#eef1f5",
            "background_dot": "#cfd6df",
            "card_fill": "#ffffff",
            "card_outline": "#a8b2bf",
            "card_shadow": "#c8ced6",
            "tape_fill": "#d8dee6",
            "title": "#1f2937",
            "label_text": "#0f4c81",
            "body_text": "#334155",
            "rule": "#d9e2ec",
        },
        "card": {
            "background": "#f4f7f2",
            "background_dot": "#dfe7e2",
            "card_fill": "#ffffff",
            "card_outline": "#dfe7e2",
            "card_shadow": "#d5dae3",
            "tape_fill": "#dbe7f5",
            "title": "#22313d",
            "label_text": "#356f78",
            "body_text": "#3e4a57",
            "rule": "#a8b2bf",
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
        "C:/Windows/Fonts/simhei.ttf",
        str(FONT_DIR / "NotoSansCJKsc-Bold.otf")
        if bold
        else str(FONT_DIR / "NotoSansCJKsc-Regular.otf"),
    ]
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
