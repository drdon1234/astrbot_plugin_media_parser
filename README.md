<div align="center">

![:name](https://count.getloli.com/@astrbot_plugin_media_parser?name=astrbot_plugin_media_parser&theme=minecraft&padding=6&offset=0&align=top&scale=1&pixelated=1&darkmode=auto)

# 流媒体聚合解析器

**把聊天里的平台链接，变成可直接阅读和发送的媒体内容。**

视频 · 音频 · 图片 · 图文 · 热评

[![License](https://img.shields.io/badge/License-AGPLv3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0.html)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-orange.svg)](https://github.com/AstrBotDevs/AstrBot)
[![Version](https://img.shields.io/badge/Version-v1.17.0-green.svg)](https://github.com/drdon1234/astrbot_plugin_media_parser)

[快速开始](#-快速开始) · [支持平台](#-支持平台) · [默认行为](#-默认行为) · [高级功能](#-高级功能)

</div>

---

## 🚀 快速开始

1. 在 AstrBot 4.x WebUI 的插件市场搜索 astrbot_plugin_media_parser 并安装。
2. 向机器人发送支持平台的链接，插件默认自动解析。
3. 在插件配置中选择发送文本、富媒体或两者。

大多数平台无需额外配置。首次使用建议先确认缓存目录可用。

## 🌐 支持平台

支持视频、音频、图片、图文和热评等内容。具体能力会因平台和链接类型有所不同。

|  |  |  |  |
| --- | --- | --- | --- |
| B站 | 抖音 | 快手 | AcFun |
| 红果短剧 | 网易云音乐 | 喜马拉雅 | 微博 |
| 小红书 | 闲鱼 | 今日头条 | 小黑盒 |
| 雪球 | 酷安 | 微信 | 知乎 |
| 百度贴吧 | NGA | 虎扑 | 豆瓣 |
| V2EX | 稀土掘金 | CSDN | 博客园 |
| Gitee | TikTok | YouTube | Steam |
| Twitter/X | Pixiv | GitHub | GitLab |

## ⚙️ 默认行为

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| 自动解析 | 开启 | 发送链接后自动处理 |
| 文本与富媒体输出 | 均开启 | 同时发送正文和媒体 |
| 正文配图按原文顺序穿插 | 开启 | 支持的平台按阅读顺序发送 |
| 热评 | 3 条 | 可按平台排除 |
| 文本元数据渲染为图片 | 关闭 | 开启后生成文字卡或分享卡 |
| 卡片版式 | 分享卡 | 可切换为文字卡 |
| 卡片主题 | 玫瑰便签 | 另有雾灰简约、深蓝磨砂、浅棕细纹纸 |
| 卡片字体 | 思源宋体 | 另有思源黑体、霞鹜文楷 |
| 正文、热评与翻译分区渲染 | 关闭 | 默认合并到同一张卡片 |
| 长图自动分页 | 关闭 | 开启后按 1800 像素分页 |

## 🧭 常用配置

### 平台输出模式

在「平台开关与输出模式」中分别选择发送文本和富媒体的平台。

- 两项都选时发送文本和媒体。
- 只选文本时不下载媒体。
- 只选富媒体时不发送正文和热评。
- 开启「有视频时只发富媒体」后，含视频的内容跳过正文。

### 热评

在「消息输出 → 附加内容：热评」中设置条数。填 0 可关闭热评，也可以排除指定平台。实际来源可能是普通评论、精选评论、游戏评价、Issue 讨论或分集弹幕，数量受平台接口和访问权限影响。

### 文本图片

在「消息输出 → 文本元数据」中开启渲染后，可选择文字卡或分享卡，并独立选择主题和字体。

- 两种卡片使用同一套原生 Pillow 渲染逻辑。
- 正文、热评和翻译默认合并到同一张卡片。
- 分享卡可以显示封面和图集。
- 开启分区渲染后，正文、热评和翻译分别出图。
- 卡片中的封面、图集和正文配图可以通过引用关键词取回。

字体资源见[字体资源说明](resource/font/README.md)。

### 正文与配图

开启「正文与配图按原文顺序穿插」后，支持的平台会按原文位置交替发送段落和图片。渲染为卡片时，配图会嵌入正文位置。无法识别位置或图片下载失败时，插件保留原有发送方式。

「图片发送方式」支持保持原方式、多图一条和逐张发送。

### 音频

「音频发送方式」支持语音或原始文件。语音转换、图片格式转换和部分视频处理需要 ffmpeg。

## 🧰 高级功能

<details>
<summary>展开高级配置和运行前提</summary>

### 缓存目录

非 Docker 环境默认使用 AstrBot 插件数据目录。Docker 环境建议配置协议端可以访问的共享缓存目录。图片、独立音频、部分视频流和 B站高画质通常需要缓存。

### B站高画质

在「B站增强」中开启 Cookie 解析并填写 Cookie。高画质、Cookie 续期和部分视频流需要可用缓存目录，管理员协助登录还需要配置管理员 ID。

### 翻译与内容总结

在「文本翻译与内容总结」中选择模型和内容类型。功能默认关闭，模型不可用时保留原始解析结果。

### 仓库 README 截图

GitHub、GitLab 和 Gitee 可发送仓库概况与 README 截图。截图功能需要在 AstrBot 所用的 Python 环境中安装 Playwright Chromium。

    python -m playwright install chromium

### 媒体中转

当 AstrBot 与协议端无法共享文件目录时，可在「媒体中转」中开启临时链接发送。媒体中转仍需要本地缓存目录。

### 引用和归档

分享卡支持引用取回原图和正文链接。配置归档命令后，可以将引用链接的解析结果和已下载媒体导出为 ZIP。

</details>

## 🛠️ 常见限制

- 私密、删除、付费、登录限制和风控内容可能无法解析。
- TikTok、YouTube、Twitter/X、Pixiv、小黑盒和 Steam 的部分请求可能需要代理。
- 微信视频号短链需要配置腾讯元宝 Cookie。
- 语音、视频解密和图片转换可能需要 ffmpeg。
- 机器人会跳过自身消息，直播链接默认跳过。
- 缓存目录不可用时，必须下载到本地的媒体会被跳过，但文本通常仍会发送。

## 📚 更多资料

| 文档 | 内容 |
| --- | --- |
| [更新日志](CHANGELOG.md) | 版本变化 |
| [平台解析备忘](docs/PARSER_METHOD_MEMO.md) | 链接范围和平台边界 |
| [架构说明](docs/ARCHITECTURE.md) | 模块职责和运行流程 |
| [字体资源说明](resource/font/README.md) | 字体来源、许可证和补全方式 |

## 📄 许可证

本项目使用 AGPL-3.0 许可证。字体资源遵循各自许可证，详见[字体资源说明](resource/font/README.md)。
