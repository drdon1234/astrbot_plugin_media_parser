"""文本元数据图片渲染器，负责将文本节点绘制为 PNG 图片。"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import List

from .font_manager import FONT_DIR, ensure_default_fonts


DEFAULT_RENDER_WIDTH = 960
DEFAULT_RENDER_FONT_SIZE = 24
DEFAULT_RENDER_STYLE = "fresh"
DEFAULT_RENDER_FONT_FAMILY = "noto_sans"
MIN_RENDER_FONT_SIZE = 16
MAX_RENDER_FONT_SIZE = 42
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
    timeout_seconds: int = 60,
    paginate_images: bool = False,
    resource_proxy_url: str | None = None,
    line_spacing: float = 1.6,
    paragraph_spacing: float = 1.4,
) -> List[str]:
    """将文本元数据渲染为本地 PNG 文件。

    Args:
        text: 待渲染的文本内容。
        output_path: 输出图片路径。
        title: 图片顶部标题。
        width: 图片宽度。
        font_size: 正文文字大小。
        style: 图片渲染风格。
        font_family: 图片字体族。
        timeout_seconds: 渲染超时时间（秒）。
        paginate_images: 是否按最大高度自动分页，与样式及内容分组独立。
        resource_proxy_url: 下载字体等静态资源时使用的代理地址，为空时直接连接。
        line_spacing: 行高系数。
        paragraph_spacing: 空行高度相对于字号的倍数。

    Returns:
        按阅读顺序排列的图片绝对路径。

    Raises:
        RuntimeError: Pillow 不可用、字体加载失败或图片未生成。
        asyncio.TimeoutError: 渲染超时。
    """
    if not str(text or "").strip():
        raise ValueError("没有可渲染的文本元数据")

    await ensure_default_fonts(proxy_url=resource_proxy_url)
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    task = asyncio.create_task(
        asyncio.to_thread(
            _render_text_metadata_image_sync,
            str(text),
            output,
            str(title or "媒体解析结果"),
            _normalize_width(width),
            _normalize_font_size(font_size),
            _normalize_style(style),
            _normalize_font_family(font_family),
            paginate_images,
            line_spacing,
            paragraph_spacing,
        )
    )
    try:
        return await asyncio.wait_for(
            asyncio.shield(task), timeout=max(10, int(timeout_seconds or 60))
        )
    except (asyncio.CancelledError, asyncio.TimeoutError):
        try:
            await task
        finally:
            for page in output.parent.glob(f"{output.stem}_*.png"):
                page.unlink(missing_ok=True)
        raise


def _render_text_metadata_image_sync(
    text: str,
    output: Path,
    title: str,
    width: int,
    font_size: int,
    style: str,
    font_family: str,
    paginate_images: bool = False,
    line_spacing: float = 1.6,
    paragraph_spacing: float = 1.4,
) -> List[str]:
    """按行绘制卡片，并在启用分页时限制单页画布高度。"""
    from PIL import Image, ImageDraw, ImageFont

    palette = _style_palette(style)
    regular = _load_font(ImageFont, font_size, font_family=font_family)
    heading = _load_font(ImageFont, font_size + 6, True, font_family)
    small = _load_font(ImageFont, max(16, font_size - 4), font_family=font_family)
    probe = ImageDraw.Draw(Image.new("RGB", (width, 1)))
    margin = max(32, width // 16)
    body_width = width - 2 * margin - 56
    max_height = min(1800, width * 2)
    body_top = 126
    body_limit = max_height - body_top - 88 if paginate_images else float("inf")
    pages = []
    rows = []
    used = 0
    # 先测量各行高度，再按分页结果创建画布。
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        value = raw.strip()
        is_heading = bool(re.match(r"^(热评|\d{2,}  |标题：|简介/正文：|翻译)", value))
        is_meta = value.startswith("赞 ")
        font = heading if is_heading else small if is_meta else regular
        color = palette["label_text"] if is_heading or is_meta else palette["body_text"]
        if value == TEXT_SECTION_SEPARATOR:
            block = [("", regular, color, 24, True)]
        elif not value:
            block = [("", regular, color, round(font_size * paragraph_spacing), False)]
        else:
            block = [
                (line, font, color, _line_height(probe, font, line_spacing), False)
                for line in _wrap_text(probe, value, font, body_width)
            ]
        # 短段落尽量保持完整，超长段落仅在行与行之间分页。
        block_height = sum(row[3] for row in block)
        if rows and used + block_height > body_limit and block_height <= body_limit:
            pages.append((rows, used))
            rows, used = [], 0
        for row in block:
            if rows and used + row[3] > body_limit:
                pages.append((rows, used))
                rows, used = [], 0
            rows.append(row)
            used += row[3]
    if rows:
        pages.append((rows, used))

    outputs = []
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        for index, (rows, used) in enumerate(pages, 1):
            height = body_top + used + 88
            image = Image.new("RGB", (width, height), palette["background"])
            draw = ImageDraw.Draw(image)
            _draw_background(draw, width, height, palette["background_dot"])
            draw.rounded_rectangle(
                (margin, 36, margin + 6, 76), radius=3, fill=palette["title"]
            )
            label = _wrap_text(draw, title, heading, width - 2 * margin - 24)[0]
            draw.text((margin + 22, 39), label, font=heading, fill=palette["title"])
            draw.rounded_rectangle(
                (margin, 102, width - margin, height - 56),
                radius=18,
                fill=palette["card_fill"],
                outline=palette["card_outline"],
            )
            y = body_top
            for line, font, color, row_height, rule in rows:
                if rule:
                    draw.line(
                        (margin + 28, y + 12, width - margin - 28, y + 12),
                        fill=palette["rule"],
                    )
                else:
                    label, value = _split_label(line)
                    if label and value and font is regular:
                        draw.text((margin + 28, y), label, font=font, fill=palette["label_text"])
                        draw.text(
                            (margin + 28 + _text_width(draw, label, font), y),
                            value, font=font, fill=color,
                        )
                    else:
                        draw.text((margin + 28, y), line, font=font, fill=color)
                y += row_height
            footer = f"{index:02d} / {len(pages):02d}"
            draw.text(
                (width - margin - _text_width(draw, footer, small), height - 39),
                footer,
                font=small,
                fill=palette["label_text"],
            )
            page = output.with_name(f"{output.stem}_{index:03d}.png")
            outputs.append(str(page))
            image.save(page, format="PNG")
            image.close()
    except (OSError, ValueError, RuntimeError):
        for path in outputs:
            Path(path).unlink(missing_ok=True)
        raise
    return outputs


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
        if char in NO_LINE_START_CHARS and len(current) > 1:
            lines.append(current[:-1].rstrip())
            current = current[-1] + char
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
    bbox = draw.textbbox((0, 0), str(text or ""), font=font)  # type: ignore[attr-defined]
    return max(1, int(bbox[2] - bbox[0]))


def _line_height(draw: object, font: object, factor: float) -> int:
    bbox = draw.textbbox((0, 0), "媒体解析Ag", font=font)  # type: ignore[attr-defined]
    return max(1, int((bbox[3] - bbox[1]) * factor))


def _normalize_width(value: object) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed = DEFAULT_RENDER_WIDTH
    return max(640, min(1600, parsed))


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
            return image_font.truetype(candidate, size)  # type: ignore[attr-defined]
        except (OSError, ValueError):
            continue
    raise RuntimeError(
        "未找到可用中文字体，请安装 Noto Sans CJK、微软雅黑或配置 "
        "ASTRBOT_MEDIA_PARSER_FONT"
    )
