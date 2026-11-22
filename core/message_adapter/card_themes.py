"""卡片主题的色板与原生 Pillow 卡面绘制。"""

from __future__ import annotations

import random
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter, ImageOps


@dataclass(frozen=True)
class CardTheme:
    """一套卡片主题使用的表面与文字、边缘颜色。"""

    surface: str
    palette: dict[str, str]

    def draw(
        self, canvas: Image.Image, box: tuple[int, int, int, int], picture: Image.Image | None,
    ) -> None:
        """在正文绘制前铺好当前页的卡面。

        Args:
            canvas: 已填入主题背景色的画布。
            box: 卡面边界。
            picture: 本页用于磨砂取光的素材，无图时为空。
        """
        palette = self.palette
        if self.surface == "glass":
            _draw_glass(canvas, box, palette, _light_field(canvas.size, picture))
        elif self.surface == "paper":
            _draw_paper(canvas, box, palette)
        else:
            left, top, right, bottom = box
            draw = ImageDraw.Draw(canvas)
            draw.rounded_rectangle((left, top + 3, right, bottom + 3), 22, fill=palette["card_shadow"])
            draw.rounded_rectangle(box, 22, fill=palette["card_fill"], outline=palette["card_outline"])


CARD_THEMES = {
    "rose": CardTheme("plain", {
        "background": "#F5F0F0", "card_fill": "#FFFCFC", "card_outline": "#EAE0E2",
        "card_shadow": "#EAE3E5", "title": "#322D35", "accent": "#984D66",
        "muted_text": "#7B707A", "body_text": "#403A43", "rule": "#EDE3E7",
    }),
    "mist": CardTheme("plain", {
        "background": "#EDF0F4", "card_fill": "#FFFFFF", "card_outline": "#DFE4EA",
        "card_shadow": "#DEE3EA", "title": "#1E293B", "accent": "#355A7E",
        "muted_text": "#697586", "body_text": "#354052", "rule": "#E2E7ED",
    }),
    "frost": CardTheme("glass", {
        "background": "#1B2939", "card_fill": "#29394D", "card_outline": "#394C61",
        "card_shadow": "#162130", "title": "#EDF5FC", "accent": "#82C7E8",
        "muted_text": "#A0B0C1", "body_text": "#D4DEE9", "rule": "#405267",
    }),
    "paper": CardTheme("paper", {
        "background": "#E9DAC3", "card_fill": "#FFF3DF", "card_outline": "#DAC9AD",
        "card_shadow": "#C9B69B", "title": "#372B23", "accent": "#785134",
        "muted_text": "#6F5844", "body_text": "#44372C", "rule": "#DBCBB3",
    }),
}


def _mix(first, second, amount):
    """按比例混合两个颜色。"""
    left = ImageColor.getrgb(first) if isinstance(first, str) else first
    right = ImageColor.getrgb(second) if isinstance(second, str) else second
    return tuple(round(a + (b - a) * amount) for a, b in zip(left, right))


def _draw_paper(canvas, box, palette):
    """让细纤维与小幅明暗变化落在纸面以内。"""
    width, height = canvas.size
    left, top, right, bottom = box
    rng = random.Random(713)
    shadow = Image.new("L", canvas.size)
    ImageDraw.Draw(shadow).rounded_rectangle((left + 1, top + 3, right + 1, bottom + 3), 5, fill=180)
    canvas.paste(_mix(palette["card_shadow"], palette["body_text"], .11),
                 (0, 0, width, height), shadow.filter(ImageFilter.GaussianBlur(2)))
    mask = Image.new("L", canvas.size)
    ImageDraw.Draw(mask).rounded_rectangle(box, 5, fill=255)
    grain = Image.frombytes("L", canvas.size, rng.randbytes(width * height))
    fibers = ImageDraw.Draw(grain)
    for _ in range(width * height // 170):
        x, y = rng.randrange(width), rng.randrange(height)
        fibers.line((x, y, x + rng.randrange(3, 12), y + rng.randrange(-2, 3)), fill=rng.choice((12, 244)))
    coarse_size = (max(1, width // 13), max(1, height // 13))
    coarse = Image.frombytes("L", coarse_size, rng.randbytes(coarse_size[0] * coarse_size[1]))
    grain = Image.blend(grain, coarse.resize(canvas.size, Image.Resampling.BICUBIC), .07)
    base = _mix(palette["card_fill"], palette["background"], .27)
    sheet = ImageOps.colorize(grain, _mix(base, palette["body_text"], .08), _mix(base, "white", .07))
    canvas.paste(sheet, (0, 0), mask)
    ImageDraw.Draw(canvas).rounded_rectangle(box, 5, outline=_mix(base, palette["body_text"], .16))


def _light_field(size, picture):
    """取当前页首图的明暗，无图时生成稳定的柔光底景。"""
    width, height = size
    side = 144
    if picture is None:
        field = Image.frombytes("L", (6, 6), random.Random(415).randbytes(36))
        field = field.resize((side, side), Image.Resampling.BICUBIC)
    else:
        grayscale = picture.convert("L")
        if picture.getexif().get(274, 1) != 1:
            grayscale = ImageOps.exif_transpose(grayscale)
        field = ImageOps.fit(grayscale, (side, side), Image.Resampling.LANCZOS)
    field = ImageOps.autocontrast(field.filter(ImageFilter.GaussianBlur(5)), cutoff=2)
    lighting = Image.new("L", (side, (height * side + width - 1) // width))
    reflected = ImageOps.flip(field)
    for row, top in enumerate(range(0, lighting.height, side)):
        lighting.paste(reflected if row % 2 else field, (0, top))
    return lighting


def _draw_glass(canvas, box, palette, lighting):
    """用同一底景绘制透光阅读面、折射内缘和随光源变化的边光。"""
    width, height = canvas.size
    left, top, right, bottom = box
    size = (right - left, bottom - top)
    radius = round(width * .032)
    low = _mix(palette["background"], palette["accent"], .06)
    high = _mix(palette["accent"], "white", .18)
    scene = ImageOps.colorize(lighting, low, high).resize(canvas.size, Image.Resampling.BICUBIC)
    canvas.paste(scene)

    def refract(inset, blur, tint):
        inset = min(inset, (min(size) - 1) // 2)
        sample = scene.crop((left + inset, top + inset, right - inset, bottom - inset))
        sample = sample.resize(size, Image.Resampling.BICUBIC).filter(ImageFilter.GaussianBlur(blur))
        return Image.blend(sample, Image.new("RGB", size, palette["card_fill"]), tint)

    face = refract(12, width * .035, .78)
    bevel = refract(38, 5, .43)
    scene.close()
    mask = Image.new("L", size)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=255)
    shadow = Image.new("L", canvas.size)
    shadow.paste(mask, (left + 1, top + 6))
    shadow = shadow.filter(ImageFilter.GaussianBlur(9)).point(lambda value: value * .22)
    canvas.paste(_mix(palette["background"], "#08131A", .78), (0, 0, width, height), shadow)
    inside = Image.new("L", size)
    ImageDraw.Draw(inside).rounded_rectangle((4, 4, size[0] - 5, size[1] - 5), radius - 4, fill=255)
    bevel.paste(face, (0, 0), inside.filter(ImageFilter.GaussianBlur(1.8)))
    canvas.paste(bevel, (left, top), mask)
    del face, bevel, inside, shadow
    light = lighting.resize(canvas.size, Image.Resampling.BICUBIC).crop(box)
    inset_mask = Image.new("L", size)
    inset_mask.paste(mask, (1, 2))
    glint = ImageChops.multiply(ImageChops.subtract(mask, inset_mask), light.point(lambda value: 60 + .56 * value))
    canvas.paste(_mix(palette["card_fill"], "white", .82), box, glint)

    # 按条带叠加磨砂颗粒，长图无需再分配两幅整页 RGB 图。
    rng = random.Random(713)
    for y in range(0, height, 128):
        rows = min(128, height - y)
        grain = Image.frombytes("L", (width, rows), rng.randbytes(width * rows))
        strip = canvas.crop((0, y, width, y + rows))
        canvas.paste(Image.blend(strip, grain.convert("RGB"), .014), (0, y))
