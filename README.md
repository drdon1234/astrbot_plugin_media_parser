<div align="center">

![:name](https://count.getloli.com/@astrbot_plugin_media_parser?name=astrbot_plugin_media_parser&theme=minecraft&padding=6&offset=0&align=top&scale=1&pixelated=1&darkmode=auto)

# 流媒体聚合解析器

_自动解析平台分享内容，发送视频、音频、图片与文本。_

[![License](https://img.shields.io/badge/License-AGPLv3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0.html)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-4.x-orange.svg)](https://github.com/AstrBotDevs/AstrBot)
[![Version](https://img.shields.io/badge/Version-v1.8.0-green.svg)](https://github.com/drdon1234/astrbot_plugin_media_parser/releases/latest)
[![GitHub](https://img.shields.io/badge/作者-drdon1234-blue)](https://github.com/drdon1234)

</div>

## 🚀 快速开始

1. 在 AstrBot 4.x 的 WebUI 插件市场搜索 `astrbot_plugin_media_parser` 并安装，Python 依赖会自动安装。
2. 向机器人发送支持平台的分享内容，默认自动识别链接并解析。
3. 在插件配置中按需调整各平台的输出模式：`全部发送`、`仅文本`、`仅富媒体` 或 `关闭`。

多数平台无需额外配置。Docker 或跨服务器部署请先确认下方的[缓存与媒体中转](#-缓存与媒体中转)设置；需要音视频合并、视频截帧或图片格式转换时，请在 AstrBot 运行环境中安装 ffmpeg。

## 📺 支持的平台

| 平台 | 热评 | 备注（支持的媒体类型） |
|------|------|----------------------|
| **B站** | 支持 | 视频 / 图片 / 文本 |
| **抖音** | 支持 | 视频 / 图片 / 文本 |
| **快手** | — | 视频 / 图片 / 文本 |
| **AcFun** | 支持 | 视频 / 图片 / 文本 |
| **网易云音乐** | 支持 | 音频 / 图片 / 文本 |
| **喜马拉雅** | 支持 | 音频 / 图片 / 文本 |
| **微博** | 支持 | 视频 / 图片 / 文本 |
| **小红书** | 支持 | 视频 / 图片 / 文本 |
| **闲鱼** | 支持 | 视频 / 图片 / 文本 |
| **今日头条** | 支持 | 视频 / 图片 / 文本 |
| **小黑盒** | 支持 | 视频 / 图片 / 文本 |
| **雪球** | 支持 | 视频 / 图片 / 文本 |
| **微信** | — | 视频 / 图片 / 文本 |
| **知乎** | 支持 | 图片 / 文本 |
| **百度贴吧** | 支持 | 视频 / 图片 / 文本 |
| **NGA** | 支持 | 图片 / 文本 |
| **虎扑** | 支持 | 视频 / 图片 / 文本 |
| **豆瓣** | 支持 | 视频 / 图片 / 文本 |
| **V2EX** | 支持 | 图片 / 文本 |
| **稀土掘金** | 支持 | 图片 / 文本 |
| **CSDN** | 支持 | 图片 / 文本 |
| **博客园** | — | 图片 / 文本 |
| **Gitee** | 支持 | 文本 |
| **TikTok** | 支持 | 视频 / 图片 / 文本 |
| **YouTube** | 支持 | 视频 / 文本 |
| **Steam** | 支持 | 视频 / 图片 / 文本 |
| **Twitter/X** | — | 视频 / 图片 / 文本 |
| **Pixiv** | 支持 | 图片 / 文本 |
| **GitHub** | — | 文本 |
| **GitLab** | 支持 | 文本 |

表中列出平台可输出的媒体类型，具体内容以来源实际提供为准。热评是配置中的统一名称，包含热门、精选、普通评论及游戏评价，不保证全站热门排序，也不代表该平台所有内容都有评论。

## ⚙️ 常用设置

### 触发与消息输出

- **手动解析**：关闭 `触发设置 → 自动解析链接` 后，消息需同时包含链接和 `手动触发关键词`（默认 `视频解析`、`解析视频`）。开启 `回复触发解析` 后，也可引用含链接的消息并附关键词。
- **文本与媒体**：在 `消息输出` 中选择文本字段、视频是否只发封面，以及消息是否聚合。仅支持文本的平台应选择 `全部发送` 或 `仅文本`。
- **热评**：在 `消息输出 → 附加内容：热评` 中将默认 `0` 的 `热评条数` 改为正数，并开启所需平台开关；平台输出模式必须包含文本。实际条数受公开范围和分页限制，评论获取失败不影响正文与媒体。
- **翻译**：在 `文本翻译` 中启用，可选择 AstrBot 内置 AI 或插件自定义提供商（OpenAI 兼容接口、Ollama 等）。只翻译所选且可见的标题、正文，失败时保留原内容。
- **ZIP 归档**：在 `消息输出 → 导出行为：ZIP 归档` 中设置 `引用链接归档命令`，随后引用含链接的消息并单独发送该命令。默认关闭；归档包含解析详情与成功下载的媒体，需要可用缓存目录及聊天平台的文件发送支持。

### 音频发送

网易云音乐支持单曲，喜马拉雅支持单集音频；不展开歌单或专辑，也不解锁会员、付费内容。仅有试听时会标注试听，无可用音频时仍保留已取得的图文信息和评论。

在 `消息输出 → 富媒体展示 → 音频发送方式` 中选择 `语音`（默认）或 `文件`。语音转换可能需要 ffmpeg，文件方式保留原始音频；两种方式都独立发送，受聊天平台的音质、时长和大小限制。音频必须先缓存，`下载与缓存 → 音频大小上限（MB）` 默认 30 MB，填 `0` 时仍保留 128 MB 安全上限。

### 文本渲染为图片

在 `消息输出 → 文本元数据` 中开启 `将文本元数据渲染为图片`，可将本次解析的可见文本、热评和翻译合并为一张 PNG。支持四种样式、字体选择和 16–42 的字号；字体或渲染失败时保留原文本。

默认字体缺失时会自动从 GitHub 下载，首次加载需能访问 GitHub。自定义字体可通过 `ASTRBOT_MEDIA_PARSER_FONT` 指定；详细说明见[字体资源说明](resource/font/README.md)。

## 📁 缓存与媒体中转

图片、独立音频以及需要下载或合并的视频必须有可写的缓存目录。缓存不可用时，必须缓存的媒体会被跳过；可直接发送的视频仍可尝试直链。

- **非 Docker 部署**：自动使用 AstrBot 插件数据目录，无需填写缓存路径。
- **Docker 部署**：在 `下载与缓存 → Docker 共享缓存目录（可选）` 中填写容器内的共享挂载路径；留空使用 `/app/sharedFolder/video_parser/cache`。协议端需能读取该路径，或使用媒体中转。
- **跨服务器或无法共享目录**：开启 `媒体中转 → 启用`，填写协议端可访问的 `AstrBot 回调地址`，例如 `http://192.168.1.100:6185`；留空时使用 AstrBot 全局回调地址。中转缓存默认有效 300 秒，过期后链接失效。

媒体中转只转换已缓存文件的发送地址，仍需要本地缓存可用。协议端拉取较慢时可适当延长有效期。

## 🍪 需要登录态的功能

### B站高画质与管理员协助登录

在 `B站增强` 中开启 `携带 Cookie 解析`，填写 Cookie 并选择 `最高画质`。实际画质取决于账号权限和视频源，音视频合并需要缓存目录和 ffmpeg；缓存不可用时回退为无 Cookie 解析。

需要协助更新 Cookie 时，在 `权限控制 → 管理员 ID` 中填写用户 ID，先让管理员私聊一次机器人，再开启 `B站增强 → 管理员协助登录 → 启用`。失效后插件会私聊管理员请求确认和扫码；管理员也可私聊发送默认指令 `B站更新Cookie` 主动更新。

### 微信视频号与 Pixiv

- **微信视频号**：短链解析需要 `微信设置 → 腾讯元宝 Cookie`，失效后需手动更新；带有效播放令牌的预览链接可直接解析。公众号图文无需 Cookie，公众号内嵌视频暂不支持。
- **Pixiv**：公开作品可尝试匿名解析；登录或年龄限制内容需要在 `Pixiv 设置 → Pixiv Cookie` 中填写包含 `PHPSESSID` 的完整 Cookie。

## 📝 注意事项

- **访问权限**：私密、删除、登录受限、付费或平台风控内容可能无法解析，插件不会绕过付费权限；不支持直播。帖子通常提取主帖及有限评论，集合内容也可能只展示部分条目，不保证完整展开。
- **代理网络**：部分网络下的海外平台和媒体下载需要代理。在 `代理设置` 中填写 HTTP/HTTPS 代理地址，再开启对应平台的解析或下载开关；只填地址不会覆盖所有请求。
- **分享完整性**：请保留原始分享链接的必要参数，部分内容依赖分享凭据。解析范围与具体限制可查阅[平台解析说明](docs/PARSER_METHOD_MEMO.md)。
- **媒体格式与发送限制**：非 JPG/PNG 图片会尝试经 ffmpeg 转为 PNG，GIF 等动图只保留首帧；缺少 ffmpeg 时保留原格式。消息平台仍可能限制格式、文件大小或语音时长，插件配置不能解除这些限制。

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

## 🤝 文档与贡献

欢迎通过 Issue 反馈问题或提交 PR。用户使用说明见本文，版本变化见[更新日志](CHANGELOG.md)，开发与调研资料见[文档索引](docs/README.md)。参与开发或使用 AI 修改项目前，请先阅读[协作规范](AGENTS.md)。
