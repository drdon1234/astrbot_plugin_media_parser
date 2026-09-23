<div align="center">

![:name](https://count.getloli.com/@astrbot_plugin_media_parser?name=astrbot_plugin_media_parser&theme=minecraft&padding=6&offset=0&align=top&scale=1&pixelated=1&darkmode=auto)

# 流媒体聚合解析器

_✨ 自动解析流媒体平台链接，发送视频、音频、图片与文本 ✨_

[![License](https://img.shields.io/badge/License-AGPLv3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0.html)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-orange.svg)](https://github.com/AstrBotDevs/AstrBot)
[![Version](https://img.shields.io/badge/Version-v1.8.1-green.svg)](https://github.com/drdon1234/astrbot_plugin_media_parser)
[![GitHub](https://img.shields.io/badge/作者-drdon1234-blue)](https://github.com/drdon1234)

</div>

---

## 📺 支持的平台

| 平台 | 支持能力 | 备注 |
|------|---------|------|
| **B站** | 视频 / 图片 / 文本 / 热评 | 视频、图文 |
| **抖音** | 视频 / 图片 / 文本 / 热评 | 视频、图集 |
| **快手** | 视频 / 图片 / 文本 | 视频、图集 |
| **AcFun** | 视频 / 图片 / 文本 / 热评 | 视频、图文 |
| **网易云音乐** | 音频 / 图片 / 文本 / 热评 | 音频、封面 |
| **喜马拉雅** | 音频 / 图片 / 文本 / 热评 | 音频、封面 |
| **微博** | 视频 / 图片 / 文本 / 热评 | 视频、图文 |
| **小红书** | 视频 / 图片 / 文本 / 热评 | 视频、图文 |
| **闲鱼** | 视频 / 图片 / 文本 / 热评 | 视频、图文 |
| **今日头条** | 视频 / 图片 / 文本 / 热评 | 视频、图文 |
| **小黑盒** | 视频 / 图片 / 文本 / 热评 | 视频、图文 |
| **雪球** | 视频 / 图片 / 文本 / 热评 | 视频、图文 |
| **微信** | 视频 / 图片 / 文本 | 视频、图文 |
| **知乎** | 图片 / 文本 / 热评 | 图文 |
| **百度贴吧** | 视频 / 图片 / 文本 / 热评 | 视频、图文 |
| **NGA** | 图片 / 文本 / 热评 | 图文 |
| **虎扑** | 视频 / 图片 / 文本 / 热评 | 视频、图文 |
| **豆瓣** | 视频 / 图片 / 文本 / 热评 | 视频、图文 |
| **V2EX** | 图片 / 文本 / 热评 | 图文 |
| **稀土掘金** | 图片 / 文本 / 热评 | 图文 |
| **CSDN** | 图片 / 文本 / 热评 | 图文 |
| **博客园** | 图片 / 文本 | 图文 |
| **Gitee** | 文本 / 热评 | 文本 |
| **TikTok** | 视频 / 图片 / 文本 / 热评 | 视频、图集 |
| **YouTube** | 视频 / 文本 / 热评 | 视频 |
| **Steam** | 视频 / 图片 / 文本 / 热评 | 视频、图文 |
| **Twitter/X** | 视频 / 图片 / 文本 | 视频、图文 |
| **Pixiv** | 图片 / 文本 / 热评 | 图片、图集 |
| **GitHub** | 文本 | 文本 |
| **GitLab** | 文本 / 热评 | 文本 |

---

## 🚀 快速开始

1. 打开 AstrBot 4.x WebUI → 插件市场搜索 `astrbot_plugin_media_parser` 并安装
2. 依赖库会根据 `requirements.txt` 自动安装，向机器人发送支持平台的分享链接即可解析

### 特性

- 开箱即用，无需配置即可解析大部分平台
- 自动识别并解析链接
- 每个平台可独立选择输出模式：全部发送、仅文本、仅富媒体或关闭
- 可选大模型翻译正文和标题，支持 AstrBot 内置 AI、自定义 OpenAI 兼容接口或 Ollama
- 支持消息聚合策略：不聚合、全部聚合或按条件聚合
- 可选将文本元数据、热评和翻译合并为一张图片发送
- 音频可选择语音或原始文件，独立发送
- 配置 `引用链接归档命令` 后，可将引用链接的解析结果和已下载媒体导出为 ZIP 文件
- 可选 B站 Cookie 获取更高画质，支持管理员协助更新
- 媒体中转模式，跨服务器部署无需共享目录

热评默认关闭。在 `消息输出 → 附加内容：热评` 中将 `热评条数` 设为大于 0，并开启对应平台开关；该平台需选择 `全部发送` 或 `仅文本`。热评也包含普通评论和游戏评价，实际条数可能少于设置值，获取失败不影响正文和媒体。

### 音频发送

网易云音乐支持单曲，喜马拉雅支持单集音频。仅有试听时会标注，无可用音频时仍保留图文信息；不展开歌单、专辑或解锁付费内容。

在 `消息输出 → 富媒体展示 → 音频发送方式` 中选择 `语音`（默认）或 `文件`。语音转换可能需要 ffmpeg，文件方式保留原始音频；两种方式均受聊天平台的时长和大小限制。音频需先缓存，大小默认上限 30 MB，填 0 时仍保留 128 MB 安全上限。

### 文本元数据图片

在 `消息输出 → 文本元数据` 中开启 `将文本元数据渲染为图片`，可选择样式、字体和字号。默认字体缺失时会从 GitHub 自动下载，字体或渲染失败时保留原文本；自定义字体见[字体资源说明](resource/font/README.md)。

---

## ⚙️ 缓存目录

确保 **缓存目录** 可用能显著提升媒体发送成功率。部分平台的媒体 CDN 有防盗链或鉴权，需要先下载到本地再发送。

非 Docker 环境自动使用 AstrBot 插件数据目录。Docker 环境可在 `下载与缓存 → Docker 共享缓存目录（可选）` 中设置路径，留空时使用 `/app/sharedFolder/video_parser/cache`。

> Docker 部署时请使用协议端可访问的共享目录；无法共享目录时，可开启下方的媒体中转模式

**必须缓存目录可用的场景**：

- 所有图片及独立音频（均下载后发送）
- B站高画质、YouTube 自适应流等需要本地处理的音视频
- AcFun、雪球等平台返回的 HLS 视频
- 微博、小黑盒、微信视频号、Steam 和 Twitter/X 的视频

**建议缓存目录可用的场景**：

- 抖音、快手、小红书等平台的普通视频（媒体地址可能有鉴权和时效限制）
- TikTok（受地区和风控影响，必要时同时配置代理）

缓存目录不可用时，必须缓存的媒体会被跳过并说明原因，部分普通视频仍可尝试直链。音视频合并、视频截帧及图片格式转换需要 AstrBot 运行环境中有可用的 ffmpeg。

---

## 🍪 B站 Cookie 与画质增强

配置 Cookie 后可获取更高画质（如 1080P+、4K）。

### 配置方式

1. 在 `B站增强 → 携带 Cookie 解析` 中开启
2. 填入 B站 Cookie（浏览器 F12 → Network → 任意 B站请求的 Cookie 头）
3. 选择 `最高画质`（实际画质取决于账号会员等级和视频源）
4. 确保媒体缓存目录和 ffmpeg 可用

> 缓存目录不可用时会自动回退到无 Cookie 解析路径

### 管理员协助登录

Cookie 会过期失效。开启 `管理员协助登录` 后，插件可私聊管理员引导扫码重新登录：

1. 在 `权限控制 → 管理员 ID` 填写你的用户 ID，并先私聊一次机器人
2. 在 `B站增强 → 管理员协助登录 → 启用` 中开启
3. Cookie 失效时向管理员发送确认请求，扫码后 Cookie 自动更新

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
3. 设置 `中转缓存有效期`（默认 300 秒，协议端拉取较慢时可适当延长）

媒体中转仍需要本地缓存可用，只转换已缓存文件的发送地址；过期后链接失效。

---

## 📝 注意事项

- **网络代理**：TikTok、YouTube、Twitter/X、Pixiv 等平台可能需要代理；请先填写 HTTP/HTTPS 代理地址，再开启对应平台的解析或下载开关
- **微信视频号**：短链需填写 `微信设置 → 腾讯元宝 Cookie`，失效后手动更新；带有效播放令牌的预览链接可直接解析。公众号图文无需 Cookie，公众号内嵌视频暂不支持
- **Pixiv**：登录或年龄限制作品需填写包含 `PHPSESSID` 的完整 Cookie；受地区限制时同时代理解析和图片下载
- **访问权限**：私密、删除、付费或风控内容可能无法解析，不支持直播，也不保证展开整帖评论或完整集合；分享链接中的必要参数请保留
- **图片格式**：非 JPG/PNG 图片会尝试用 ffmpeg 转为 PNG，GIF 等动图只保留首帧；缺少 ffmpeg 时保留原格式，显示效果取决于消息平台
- **发送限制**：仅支持文本的平台请选择 `全部发送` 或 `仅文本`；媒体格式、文件大小和语音时长仍受聊天平台限制

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
- 参与开发或使用 AI 修改项目前，请先阅读[协作规范](AGENTS.md)
