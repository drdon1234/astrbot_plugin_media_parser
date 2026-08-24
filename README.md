<div align="center">

![:name](https://count.getloli.com/@astrbot_plugin_media_parser?name=astrbot_plugin_media_parser&theme=minecraft&padding=6&offset=0&align=top&scale=1&pixelated=1&darkmode=auto)

# 流媒体聚合解析器

_✨ 自动解析流媒体平台链接，转换为媒体直链发送 ✨_

[![License](https://img.shields.io/badge/License-AGPLv3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0.html)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-orange.svg)](https://github.com/AstrBotDevs/AstrBot)
[![Version](https://img.shields.io/badge/Version-v1.1.0-green.svg)](https://github.com/drdon1234/astrbot_plugin_media_parser)
[![GitHub](https://img.shields.io/badge/作者-drdon1234-blue)](https://github.com/drdon1234)

</div>

---

## 📺 支持的平台

| 平台 | 支持能力 | 备注 |
|------|---------|------|
| **B站** | 视频 / 图片 / 文本 / 热评 | 支持短链、视频、番剧、动态、QQ小程序卡片 |
| **抖音** | 视频 / 图片 / 文本 | 支持短链、视频、图集 |
| **TikTok** | 视频 / 图片 / 文本 | 支持短链、视频、图集 |
| **快手** | 视频 / 图片 / 文本 | 支持短链、作品链接 |
| **微博** | 视频 / 图片 / 文本 / 热评 | 支持博客、视频分享、QQ小程序卡片 |
| **小红书** | 视频 / 图片 / 文本 / 热评 | 支持短链、笔记、QQ小程序卡片 |
| **闲鱼** | 视频 / 图片 / 文本 | 支持短链、商品页 |
| **今日头条** | 视频 / 图片 / 文本 | 支持短链、文章、视频、微头条、QQ小程序卡片 |
| **小黑盒** | 视频 / 图片 / 文本 | 支持游戏详情、BBS 分享、QQ小程序卡片 |
| **Steam** | 视频 / 图片 / 文本 | |
| **Twitter/X** | 视频 / 图片 / 文本 | |
| **Pixiv** | 图片 / 文本 | 支持插画、漫画多页解析 |

---

## 🚀 快速开始

1. 打开 AstrBot WebUI → 插件市场搜索 `astrbot_plugin_media_parser` 并安装
2. 依赖库会根据 `requirements.txt` 自动安装

### 特性

- 开箱即用，无需配置即可解析大部分平台
- 自动识别并解析链接
- 每个平台可独立选择输出模式：全部发送、仅文本、仅富媒体或关闭
- 可选大模型翻译正文和标题，支持 AstrBot 内置 AI 或自定义 OpenAI 兼容接口
- 支持消息聚合策略：不聚合、全部聚合或按条件聚合
- 可选将多条链接的文本元数据、热评和翻译统一渲染为一张图片发送
- 图片渲染支持清新便签、科技感、专业严肃、温和卡片，以及字体族和字号配置
- 可将引用链接的解析结果和已下载媒体导出为 ZIP 文件
- 可选 B站 Cookie 解锁高画质 + 管理员协助自动续期
- 媒体中转模式，跨服务器部署无需共享目录

---

## 🖼️ 文本元数据图片

在 `消息输出 → 文本元数据` 中开启 `将文本元数据渲染为图片` 后，每次解析会把标题、作者、时间、原始链接、简介/正文、热评和翻译节点合并为一张 PNG 图片发送。原文本节点只会在图片成功生成后移除；Pillow、字体或发送链路不可用时会自动回退为原文本。

可在同一配置项下选择 `清新便签`、`科技感`、`专业严肃` 或 `温和卡片` 样式，字体大小有效范围为 16–42。中文字体默认使用插件内置的 Noto Sans CJK；也可以通过 `ASTRBOT_MEDIA_PARSER_FONT` 环境变量指定字体文件。该功能依赖 `requirements.txt` 中的 Pillow。

启用媒体中转后，渲染图片也会注册为文件 Token，并按 `媒体中转 → 中转缓存有效期` 延迟清理。

---

## ⚙️ 缓存目录

确保 **缓存目录** 可用能显著提升解析成功率。部分平台的媒体 CDN 有防盗链或鉴权，直链发送会被拒绝，需要先下载到本地再发送。

> Docker 部署时请将缓存目录配置为协议端可访问的共享目录

**必须缓存目录可用的场景**：

- 所有图片（当前实现均下载后发送）
- B站 Cookie 高画质（DASH 音视频流需本地合并）
- 微博视频、小黑盒视频/BBS 媒体、Twitter/X 视频

**建议缓存目录可用的场景**：

- TikTok（受地区和风控影响，必要时请同时配置代理）
- 小红书（部分媒体有鉴权和时效性）
- Pixiv（图片必须缓存；受地区限制时需开启代理）

未配置缓存目录时，必须缓存的媒体会被跳过并说明原因。

---

## 🍪 B站 Cookie 与画质增强

配置 Cookie 后可解锁更高画质（如 1080P+、4K）。

### 配置方式

1. 在 `B站增强 → 携带 Cookie 解析` 中开启
2. 填入 B站 Cookie（浏览器 F12 → Network → 任意请求的 Cookie 头）
3. 选择 `最高画质`（实际画质取决于账号会员等级和视频源）
4. 媒体缓存目录必须可用

> 缓存目录不可用时会自动回退到无 Cookie 解析路径

### 管理员协助登录

Cookie 会过期失效。开启 `管理员协助登录` 后，Cookie 失效时插件会自动私聊管理员引导扫码重新登录：

1. 在 `权限控制 → 管理员 ID` 填写你的用户 ID
2. 在 `B站增强 → 管理员协助登录` 中开启
3. Cookie 失效时自动向管理员发送确认请求，扫码后 Cookie 自动更新

也可以在管理员私聊发送 `主动更新 Cookie 指令`（默认 `B站更新Cookie`）立即发起更新。

---

## 🔁 媒体中转模式

当 AstrBot 与消息平台协议端**不在同一台机器**或**无法共享文件目录**时，本地下载的媒体文件对协议端不可达。媒体中转模式通过 AstrBot HTTP 服务将本地文件转为临时 URL 发送。

### 适用场景

- AstrBot 和协议端分别部署在不同服务器
- Docker 容器间未挂载共享目录

### 配置方式

1. 在 `媒体中转 → 启用` 中开启
2. 填写 `AstrBot 回调地址`：协议端能访问到 AstrBot 的 HTTP 地址（如 `http://192.168.1.100:6185`），留空时尝试使用 AstrBot 全局回调地址
3. 设置 `中转缓存有效期`（默认 300 秒）

---

## 📝 注意事项

- **TikTok**：受地区和风控影响较明显，必要时请开启代理
- **小黑盒**：游戏预览视频下载速度不佳（Steam CDN）时建议启用代理
- **Twitter/X**：图片和视频 CDN 大多需要代理环境
- **Pixiv**：受地区限制时需同时代理解析请求和图片下载
- **图片格式**：非 JPG/PNG 图片会尝试用 ffmpeg 转换；缺少 ffmpeg 时保留原格式
- 插件会跳过机器人自身消息以防重复解析；直播链接会自动跳过

---

## 🙏 鸣谢

- [bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect) - B站解析端点
- [FxEmbed](https://github.com/FxEmbed/FxEmbed) - Twitter/X 解析服务
- [ParseHub](https://github.com/z-mio/ParseHub) - 小黑盒 BBS 帖子解析方法
- [tianger-mckz](https://github.com/drdon1234/astrbot_plugin_bilibili_bot/issues/1#issuecomment-3517087034) | [ScryAbu](https://github.com/drdon1234/astrbot_plugin_media_parser/issues/16#issuecomment-3726729850) | [WWWA7](https://github.com/drdon1234/astrbot_plugin_media_parser/pull/17#issue-3799325283) - QQ小程序卡片链接提取方法
- [CSDN 博客](https://blog.csdn.net/qq_53153535/article/details/141297614) - 抖音解析方法
- [astrbot_plugin_media_parser_yaya](https://github.com/xiaoxi2760/astrbot_plugin_media_parser_yaya) - 抖音备用解析方式与小红书无水印解析方式的参考实现
- [Johnserf-Seed/f2](https://github.com/Johnserf-Seed/f2) - 抖音 `a_bogus` 签名实现来源；移植部分遵循 Apache-2.0，完整文本见 `LICENSES/Apache-2.0.txt`

## 🤝 社区贡献与扩展

- 如需体验 YouTube 平台链接解析，请下载 [v0.4.1 贡献者预览版](https://github.com/drdon1234/astrbot_plugin_media_parser/releases/tag/v0.4.1)（贡献者：[shangzhimingge](https://github.com/shangzhimingge)）
- 欢迎提交 PR 以添加更多平台解析支持和新功能

## 🤝 协作与修改规范

本节用于引导使用 AI 协作的开发者。向 AI 提出代码、配置或文档修改请求时，请先明确要求 AI 完整阅读仓库根目录的 `AGENTS.md`，再阅读与任务相关的 `README.md`、`CHANGELOG.md`、`docs/` 文档、现有实现和测试；AI 完成这些阅读并确认任务要求、修改范围和边界后，才能编辑文件。

可以直接把以下要求作为任务前提交给 AI：

> 请先完整阅读 `AGENTS.md`，再阅读与本任务相关的其他文档、现有实现和测试，确认需求、修改范围和边界后再开始修改。保留工作区已有改动，保持原项目的代码风格与实现模式一致，只处理明确要求的内容，并在完成后运行相关检查、说明验证结果和未验证部分。

修改前应先检查现有实现和工作区状态，保留已有改动，只在明确请求范围内工作。修改时保持原项目的代码风格、命名、导入顺序、异常处理、日志语言、模块边界和实现模式，优先复用现有辅助函数、配置 dataclass 与 `MediaMetadata` 契约；不得为了方便引入临时绕过、重复实现、无明确需求的兼容层或架构重构。

README 只记录稳定且对使用者有帮助的平台能力、配置前提、使用方式和已知限制。常规版本更新、内部实现细节、维护记录以及用户无需感知或无需手动处理的变更，不放入 README，应按内容归入 `CHANGELOG.md`、`docs/` 或提交记录。完成修改后应执行与改动直接相关的检查，并说明验证结果和未验证部分；需求边界不明确时不得擅自扩大修改范围。
