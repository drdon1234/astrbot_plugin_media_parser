"""卡片主题、字体选项及其配置名称。"""

DEFAULT_RENDER_STYLE = "rose"
DEFAULT_RENDER_FONT_FAMILY = "noto_serif"

# 每组首项为配置面板的显示名称，其余名称用于读取已保存的选择。
STYLE_NAMES = {
    "rose": ("玫瑰便签", "fresh", "浅粉底 · 玫瑰色", "清新便签", "清新", "便签", "粉色便签", "bilinote"),
    "mist": ("雾灰简约", "serious", "浅灰底 · 钢蓝色", "专业严肃", "严肃", "专业", "professional"),
    "frost": ("深蓝磨砂", "tech", "深蓝底 · 冰蓝色", "科技感", "科技", "technology"),
    "paper": ("浅棕细纹纸", "card", "米白底 · 灰绿色", "温和卡片", "卡片", "soft_card"),
}

FONT_NAMES = {
    "noto_sans": (
        "思源黑体", "黑体 · 简洁清晰", "默认黑体", "黑体", "noto sans", "default",
        "zcool_qingke", "站酷庆科黄油体 · 瘦长圆润", "科技窄体", "站酷庆科黄油体", "zcool qingke", "qingke",
    ),
    "noto_serif": (
        "思源宋体", "宋体 · 横细竖粗", "专业宋体", "宋体", "noto serif", "serif",
        "zcool_xiaowei", "站酷小薇体 · 装饰宋体", "标题手札", "站酷小薇", "zcool xiaowei", "xiaowei",
    ),
    "lxgw_wenkai": ("霞鹜文楷", "霞鹜文楷 · 手写楷体", "清新文楷", "文楷", "lxgw wenkai", "wenkai"),
}

STYLE_IDS = {name: key for key, names in STYLE_NAMES.items() for name in (key, *names)}
FONT_IDS = {name: key for key, names in FONT_NAMES.items() for name in (key, *names)}


def normalize_style(value: object) -> str:
    """返回渲染使用的主题标识。"""
    return STYLE_IDS.get(str(value or "").strip().casefold(), DEFAULT_RENDER_STYLE)


def normalize_font_family(value: object) -> str:
    """返回渲染使用的字体族标识。"""
    return FONT_IDS.get(str(value or "").strip().casefold(), DEFAULT_RENDER_FONT_FAMILY)
