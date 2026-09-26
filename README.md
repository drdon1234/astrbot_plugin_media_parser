<div align="center">

![:name](https://count.getloli.com/@astrbot_plugin_media_parser?name=astrbot_plugin_media_parser&theme=minecraft&padding=6&offset=0&align=top&scale=1&pixelated=1&darkmode=auto)

# 流媒体聚合解析器

_✨ 自动解析流媒体平台链接，发送视频、音频、图片与文本 ✨_

[![License](https://img.shields.io/badge/License-AGPLv3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0.html)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-orange.svg)](https://github.com/AstrBotDevs/AstrBot)
[![Version](https://img.shields.io/badge/Version-v1.8.2-green.svg)](https://github.com/drdon1234/astrbot_plugin_media_parser)
[![GitHub](https://img.shields.io/badge/作者-drdon1234-blue)](https://github.com/drdon1234)

</div>

---

## 📺 支持的平台

| 平台 | 支持能力 | 备注 |
|------|---------|------|
| **B站** | 视频 / 图片 / 文本 / 热评 | 短链 / 视频 / 番剧 / 动态 / QQ小程序卡片 |
| **抖音** | 视频 / 图片 / 文本 / 热评 | 短链 / 视频 / 图集 |
| **快手** | 视频 / 图片 / 文本 | 短链 / 视频 / 图集 |
| **AcFun** | 视频 / 图片 / 文本 / 热评 | 视频（含多 P） / 番剧 / 文章 |
| **网易云音乐** | 音频 / 图片 / 文本 / 热评 | 单曲 |
| **喜马拉雅** | 音频 / 图片 / 文本 / 热评 | 单集 |
| **微博** | 视频 / 图片 / 文本 / 热评 | 微博正文 / 视频 / QQ小程序卡片 |
| **小红书** | 视频 / 图片 / 文本 / 热评 | 短链 / 笔记 / QQ小程序卡片 |
| **闲鱼** | 视频 / 图片 / 文本 / 热评 | 短链 / 商品页 |
| **今日头条** | 视频 / 图片 / 文本 / 热评 | 短链 / 文章 / 视频 / 微头条 / QQ小程序卡片 |
| **小黑盒** | 视频 / 图片 / 文本 / 热评 | 游戏详情 / BBS 分享 / QQ小程序卡片 |
| **雪球** | 视频 / 图片 / 文本 / 热评 | 普通帖 / 长文 / 转发帖 |
| **微信** | 视频 / 图片 / 文本 | 公众号文章 / 视频号 |
| **知乎** | 图片 / 文本 / 热评 | 回答 / 专栏文章 |
| **百度贴吧** | 视频 / 图片 / 文本 / 热评 | 电脑端帖子 / 手机端帖子 |
| **NGA** | 图片 / 文本 / 热评 | 帖子 |
| **虎扑** | 视频 / 图片 / 文本 / 热评 | 电脑端帖子 / 手机端帖子 |
| **豆瓣** | 视频 / 图片 / 文本 / 热评 | 条目 / 长短评 / 小组话题 / 日记 / 广播 / 豆瓣阅读 |
| **V2EX** | 图片 / 文本 / 热评 | 主题 |
| **稀土掘金** | 图片 / 文本 / 热评 | 文章 |
| **CSDN** | 图片 / 文本 / 热评 | 博客文章 |
| **博客园** | 图片 / 文本 | 博客文章 |
| **Gitee** | 文本 / 热评 | 仓库 / Issue |
| **TikTok** | 视频 / 图片 / 文本 / 热评 | 短链 / 视频 / 图集 |
| **YouTube** | 视频 / 文本 / 热评 | 视频 / Shorts |
| **Steam** | 视频 / 图片 / 文本 / 热评 | 商店游戏页 |
| **Twitter/X** | 视频 / 图片 / 文本 | 推文 |
| **Pixiv** | 图片 / 文本 / 热评 | 插画 / 漫画 / 多页作品 |
| **GitHub** | 文本 | 公开仓库 |
| **GitLab** | 文本 / 热评 | 公开项目 |

---

## 🚀 快速开始

1. 打开 AstrBot 4.x WebUI → 插件市场搜索 `astrbot_plugin_media_parser` 并安装
2. 依赖库会根据 `requirements.txt` 自动安装

### 特性

- 开箱即用，无需配置即可解析大部分平台
- 自动识别并解析链接
- 每个平台可独立选择输出模式：全部发送、仅文本、仅富媒体或关闭
- 可选附带热评，每个平台可独立开关
- 可选大模型翻译正文和标题，支持 AstrBot 内置 AI、自定义 OpenAI 兼容接口或 Ollama
- 支持消息聚合策略：不聚合、全部聚合或按条件聚合
- 可选将文本元数据、热评和翻译统一渲染为一张图片发送
- 音频可选择以语音或原始文件发送
- 配置 `引用链接归档命令` 后，可将引用链接的解析结果和已下载媒体导出为 ZIP 文件
- 可选 B站 Cookie 解锁高画质 + 管理员协助自动续期
- 媒体中转模式，跨服务器部署无需共享目录

---

## 💬 热评

热评默认关闭。在 `消息输出 → 附加内容：热评` 中将 `热评条数` 设为大于 0，并开启对应平台开关；该平台的输出模式需为 `全部发送` 或 `仅文本`。

热评也包含普通评论和游戏评价，实际条数可能少于设置值；获取失败不影响正文和媒体发送。

---

## 🎵 音频发送

网易云音乐支持单曲，喜马拉雅支持单集音频。仅有试听时会标注，无可用音频时仍保留图文信息；不展开歌单、专辑，也不解锁付费内容。

在 `消息输出 → 富媒体展示 → 音频发送方式` 中选择 `语音`（默认）或 `文件`。语音转换可能需要 ffmpeg，文件方式保留原始音频。音频需先缓存，大小默认上限 30 MB，填 0 时仍保留 128 MB 安全上限。

---

## 🖼️ 文本元数据图片

在 `消息输出 → 文本元数据` 中开启 `将文本元数据渲染为图片` 后，每次解析会把标题、作者、时间、原始链接、简介/正文、热评和翻译等文本节点合并为一张图片发送；渲染失败时自动保留原文本发送。

可选择 `清新便签`、`科技感`、`专业严肃` 或 `温和卡片` 样式，以及字体和字号（有效范围 16–42）。默认字体缺失时会从 GitHub 自动下载；也可以通过 `ASTRBOT_MEDIA_PARSER_FONT` 环境变量指定字体文件，详见[字体资源说明](resource/font/README.md)。

---

## ⚙️ 缓存目录

确保 **缓存目录** 可用能显著提升解析成功率。部分平台的媒体 CDN 有防盗链或鉴权，直链发送会被拒绝，需要先下载到本地再发送。

非 Docker 环境自动使用 AstrBot 插件数据目录；Docker 环境可在 `下载与缓存 → Docker 共享缓存目录（可选）` 中设置路径，留空时使用 `/app/sharedFolder/video_parser/cache`。

> Docker 部署时请将缓存目录配置为协议端可访问的共享目录，无法共享时可开启媒体中转模式

**必须缓存目录可用的场景**：

- 所有图片及独立音频（均下载后发送）
- B站 Cookie 高画质、YouTube 自适应流（音视频需本地合并）
- AcFun、雪球等平台的 HLS 视频
- 微博、小黑盒、微信视频号、Steam、Twitter/X 视频

**建议缓存目录可用的场景**：

- 抖音、快手、小红书（部分媒体有鉴权和时效性）
- TikTok（受地区和风控影响，必要时请同时配置代理）

缓存目录不可用时，必须缓存的媒体会被跳过并说明原因。

---

## 🍪 B站 Cookie 与画质增强

配置 Cookie 后可解锁更高画质（如 1080P+、4K）。

### 配置方式

1. 在 `B站增强 → 携带 Cookie 解析` 中开启
2. 填入 B站 Cookie（浏览器 F12 → Network → 任意 B站请求的 Cookie 头）
3. 选择 `最高画质`（实际画质取决于账号会员等级和视频源）
4. 媒体缓存目录和 ffmpeg 必须可用

> 缓存目录不可用时会自动回退到无 Cookie 解析路径

### 管理员协助登录

Cookie 会过期失效。开启 `管理员协助登录` 后，Cookie 失效时插件会自动私聊管理员引导扫码重新登录：

1. 在 `权限控制 → 管理员 ID` 填写你的用户 ID，并先私聊一次机器人
2. 在 `B站增强 → 管理员协助登录 → 启用` 中开启
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

> 媒体中转仍需要缓存目录可用，链接过期后失效

---

## 📝 注意事项

- **TikTok / YouTube**：受地区和风控影响较明显，必要时请开启代理
- **小黑盒 / Steam**：游戏预览视频下载速度不佳（Steam CDN）时建议启用代理
- **Twitter/X**：图片和视频 CDN 大多需要代理环境
- **Pixiv**：登录或年龄限制作品需填写包含 `PHPSESSID` 的 Cookie；受地区限制时需同时代理解析请求和图片下载
- **微信视频号**：短链需填写 `微信设置 → 腾讯元宝 Cookie`；公众号图文无需 Cookie，公众号内嵌视频暂不支持
- **代理**：需先在 `代理设置` 中填写代理地址，各平台代理开关才会生效
- **图片格式**：非 JPG/PNG 图片会尝试用 ffmpeg 转换，动图只保留首帧；缺少 ffmpeg 时保留原格式
- 私密、删除、付费或风控内容可能无法解析；插件会跳过机器人自身消息以防重复解析，直播链接会自动跳过

---

## 🙏 鸣谢

- [bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect) - B站解析端点
- [FxEmbed](https://github.com/FxEmbed/FxEmbed) - Twitter/X 解析服务
- [ParseHub](https://github.com/z-mio/ParseHub) - 小黑盒 BBS 帖子解析方法
- [tianger-mckz](https://github.com/drdon1234/astrbot_plugin_bilibili_bot/issues/1#issuecomment-3517087034) | [ScryAbu](https://github.com/drdon1234/astrbot_plugin_media_parser/issues/16#issuecomment-3726729850) | [WWWA7](https://github.com/drdon1234/astrbot_plugin_media_parser/pull/17#issue-3799325283) - QQ小程序卡片链接提取方法
- [CSDN 博客](https://blog.csdn.net/qq_53153535/article/details/141297614) - 抖音解析方法
- [astrbot_plugin_media_parser_yaya](https://github.com/xiaoxi2760/astrbot_plugin_media_parser_yaya) - 抖音备用解析方式与小红书无水印解析方式的参考实现
- [astrbot_plugin_parser](https://github.com/Zhalslar/astrbot_plugin_parser) - 微信视频号解析方案，参考腾讯元宝换取播放令牌与视频号预览接口的实现
- [RSSHub](https://github.com/DIYgod/RSSHub) - 知乎专栏 `x-zse-96` 签名算法及 NGA 客户端接口请求方式参考
- [Johnserf-Seed/f2](https://github.com/Johnserf-Seed/f2) - 抖音 `a_bogus` 签名实现来源；移植部分遵循 [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0)

## 🤝 社区贡献与扩展

- 欢迎提交 PR 以添加更多平台解析支持和新功能
- 版本变化见[更新日志](CHANGELOG.md)，平台解析范围与开发资料见[文档索引](docs/README.md)

## 🤝 AI 协作规范

使用 AI 协作修改本项目时，以仓库根目录的 `AGENTS.md` 为完整规范来源。建议将下列内容作为每次任务的前提：

> 请先完整阅读 `AGENTS.md`，再阅读与本任务相关的其他文档、现有实现和测试，确认需求、修改范围和边界后再开始修改。保留工作区已有改动，复用现有实现与数据契约，保持原项目的代码风格和实现模式一致，不引入临时绕过、重复实现或无明确需求的重构。只处理明确要求的内容，完成后运行相关检查并说明验证结果和未验证部分。
