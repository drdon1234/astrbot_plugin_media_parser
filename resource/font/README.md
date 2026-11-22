# 字体资源目录

此目录保留字体许可证与资源说明。字体文件按需下载到 AstrBot 数据目录下的 `plugin_data/astrbot_plugin_media_parser/font/`。AstrBot 更新插件会替换整个插件目录，字体放在插件数据目录可在更新后继续使用，无需重新下载；脱离 AstrBot 本地调试时回退到此目录。

## 字体选择

仅启用文本图片渲染时准备字体，默认选择思源宋体。插件先尝试加载自定义字体或所选字体族的本地文件。缺少专用粗体时可沿用同字体族的常规字体；本地字体有效时无需联网补全默认字体，离线也能渲染。

可通过 `ASTRBOT_MEDIA_PARSER_FONT` 环境变量指定优先使用的字体文件路径。未指定或该文件不可用时，使用面板选定的字体族。

正文或卡片标题含 Emoji 时，按完整表情序列切换至表情字体；宽度测量、换行和绘制采用相同规则，旗帜、肤色和连接符序列不会从中间断行。优先使用数据目录中的 `NotoEmoji.ttf`、系统 Noto Emoji 或 Windows Segoe UI Emoji；本地均不可用时，从 Google Fonts 固定提交自动下载约 2 MB 的单色 Noto Emoji，校验大小与 SHA256 后存入同一持久化字体目录，下载沿用字体代理设置。字体遵循 [SIL 开放字体许可证](LICENSE-NotoEmoji.txt)。表情以单色绘制，避免彩色位图字体的固定字号限制；补全失败时沿用原文本回退机制。

复杂组合表情的合字效果依赖 Pillow 的 Raqm 排版支持；缺少该支持的环境（例如部分 Windows Pillow 构建）可能将肤色、旗帜、职业等组合显示为多个符号，单个 `💫` 等表情不受此限制。

图片正文将 Markdown ATX 标题显示为小标题，去除成对强调、删除线和行内代码的格式标记，保留可读正文与链接目标；代码块内容、转义标记、普通话题标签和 URL 不按强调语法清理。此处理仅影响图片显示，不改写解析元数据或原文本消息。

## 按需补全的字体文件

| 面板选项 | 持久化目录中的文件 | 许可证 |
| --- | --- | --- |
| 思源黑体 | `NotoSansCJKsc-Regular.otf`、`NotoSansCJKsc-Bold.otf` | [Noto Sans CJK](LICENSE-NotoSansCJK.txt) |
| 思源宋体 | `NotoSerifSC-VF.ttf`（含常规与粗体字重） | [Noto Serif SC](LICENSE-NotoSerifSC.txt) |
| 霞鹜文楷 | `LXGWWenKai-Regular.ttf` | [霞鹜文楷](LICENSE-LXGWWenKai.txt) |

本地缺少所选字体时，插件从固定版本地址下载到临时文件，校验大小和 SHA256 后原子替换。离线部署也可将表中的文件预先放入字体目录。需要代理时，在「代理设置 → 使用代理的用途」中勾选字体资源下载。补全失败时插件继续加载，当前卡片回退为原文本。
