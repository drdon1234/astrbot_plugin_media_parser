<div align="center">

![:name](https://count.getloli.com/@astrbot_plugin_media_parser?name=astrbot_plugin_media_parser&theme=minecraft&padding=6&offset=0&align=top&scale=1&pixelated=1&darkmode=auto)

# 流媒体聚合解析器

_✨ 自动解析流媒体平台链接，转换为媒体直链发送 ✨_

[![License](https://img.shields.io/badge/License-AGPLv3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0.html)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-orange.svg)](https://github.com/AstrBotDevs/AstrBot)
[![Version](https://img.shields.io/badge/Version-v6.0.0-green.svg)](https://github.com/drdon1234/astrbot_plugin_media_parser)
[![GitHub](https://img.shields.io/badge/作者-drdon1234-blue)](https://github.com/drdon1234)

</div>

---

## 📺 支持的流媒体平台

<table class="config-table">
<thead>
<tr>
<th>平台</th>
<th>支持的链接类型</th>
<th>可解析的媒体类型</th>
</tr>
</thead>
<tbody>
<tr>
<td class="center"><strong>B站</strong></td>
<td>短链（<code>b23.tv/...</code>）<br>视频av号（<code>www.bilibili.com/video/av...</code>）<br>视频BV号（<code>www.bilibili.com/video/BV...</code>）<br>消息中的独立av号<br>消息中的独立BV号<br>番剧链接（<code>www.bilibili.com/bangumi/play/ep...</code>）<br>番剧链接（<code>www.bilibili.com/bangumi/play/ss...</code>）<br>动态长链（<code>www.bilibili.com/opus/...</code>）<br>动态短链（<code>t.bilibili.com/...</code>）<br>小程序卡片（<code>message.meta.detail_1.qqdocurl</code>）</td>
<td class="center">视频、图片、热评</td>
</tr>
<tr>
<td class="center"><strong>抖音</strong></td>
<td>抖音短链（<code>v.douyin.com/...</code>）<br>抖音视频长链（<code>www.douyin.com/video/...</code>）<br>抖音图集长链（<code>www.douyin.com/note/...</code>）<br>抖音作品分享链接（包含19位作品ID）</td>
<td class="center">视频、图片</td>
</tr>
<tr>
<td class="center"><strong>TikTok</strong></td>
<td>TikTok短链（<code>vm.tiktok.com/...</code>）<br>TikTok短链（<code>vt.tiktok.com/...</code>）<br>TikTok跳转链接（<code>www.tiktok.com/t/...</code>）<br>TikTok视频长链（<code>www.tiktok.com/@.../video/...</code>）<br>TikTok图集长链（<code>www.tiktok.com/@.../photo/...</code>）</td>
<td class="center">视频、图片</td>
</tr>
<tr>
<td class="center"><strong>快手</strong></td>
<td>短链（<code>v.kuaishou.com/...</code>）<br>快手长链（<code>www.kuaishou.com/...</code>）<br>分享页（<code>gifshow.com/...</code>）<br>分享页（<code>chenzhongtech.com/...</code>）</td>
<td class="center">视频、图片</td>
</tr>
<tr>
<td class="center"><strong>微博</strong></td>
<td>桌面端博客链接（<code>weibo.com/...</code>）<br>微博状态链接（<code>weibo.cn/status/...</code>）<br>移动端博客链接（<code>m.weibo.cn/detail/...</code>）<br>移动端视频分享链接（<code>video.weibo.com/show?fid=...</code>）<br>视频分享链接重定向（<code>weibo.com/tv/show/...</code>）<br>小程序卡片（<code>message.meta.detail_1.qqdocurl</code>）</td>
<td class="center">视频、图片、热评</td>
</tr>
<tr>
<td class="center"><strong>小红书</strong></td>
<td>短链（<code>xhslink.com/...</code>）<br>笔记长链（<code>www.xiaohongshu.com/explore/...</code>）<br>笔记长链（<code>www.xiaohongshu.com/discovery/item/...</code>）<br>小程序卡片（<code>message.meta.news.jumpUrl</code>）</td>
<td class="center">视频、图片、热评</td>
</tr>
<tr>
<td class="center"><strong>小黑盒</strong></td>
<td>Web链接（<code>www.xiaoheihe.cn/app/topic/game/...</code>）<br>App分享链接（<code>api.xiaoheihe.cn/game/share_game_detail?...</code>）<br>BBS分享链接（<code>www.xiaoheihe.cn/app/bbs/link/...</code>）<br>BBS分享链接（<code>www.xiaoheihe.cn/v3/bbs/...</code>）<br>Web分享接口（<code>api.xiaoheihe.cn/v3/bbs/app/api/web/share?...</code>）<br>小程序卡片（<code>message.meta.news.jumpUrl</code>）</td>
<td class="center">游戏页详情、帖子视频、图片</td>
</tr>
<tr>
<td class="center"><strong>推特</strong></td>
<td>twitter 链接（<code>twitter.com/.../status/...</code>）<br>x 链接（<code>x.com/.../status/...</code>）</td>
<td class="center">视频、图片、文本</td>
</tr>
</tbody>
</table>

---

## 🚀 快速开始

### 安装

1. **依赖库**：打开 AstrBot WebUI → 控制台 → 安装 Pip 库，输入 `aiohttp`、`cryptography` 并安装
2. **插件**：打开 AstrBot WebUI → 插件市场搜索 `astrbot_plugin_media_parser` 并安装

### 特性

- ✅ 开箱即用，无需配置即可解析大部分平台
- ✅ 自动识别并解析链接
- ✅ 每个平台可独立选择输出模式：全部发送、仅文本、仅富媒体或关闭
- ✅ 可选 B站 Cookie 解锁高画质 + 管理员协助自动续期
- ✅ 媒体中转模式，跨服务器部署无需共享目录

---

## 🧩 解析器与输出模式

在插件配置的 `解析器与输出模式` 中，每个平台都可以独立选择：

- `关闭`：不解析该平台链接
- `全部发送`：发送标题、作者、简介等文本元数据，并发送图片/视频
- `仅文本`：只发送文本元数据，不下载或发送图片/视频
- `仅富媒体`：只发送图片/视频，不发送文本元数据；热评不会获取或展示

默认所有平台均为 `全部发送`如只想保留某个平台的链接摘要，可以把该平台设为 `仅文本`；如只想要媒体内容，可以设为 `仅富媒体`

---

## ⚙️ 优化体验

确保 **缓存目录** 可用能显著提升解析成功率和发送体验

> **原因**：消息平台使用直链发送媒体时无法指定 header、referer、cookie 等参数，部分风控严格的平台会返回 403 Forbidden
> **建议**：确保媒体缓存目录可用；Docker 部署时请将缓存目录配置为协议端可访问的共享目录，非 Docker 环境会自动使用 AstrBot 插件数据目录

### 各平台特殊情况

**硬性要求（必须缓存目录可用）**
- **图片**：当前实现图片均下载到缓存后发送，缓存目录不可用时图片会被跳过
- **微博**：视频必须正确携带 referer 参数才能下载，会强制缓存后发送
- **小黑盒**：视频、BBS 媒体和 M3U8 格式需要下载到本地；M3U8 分片会合并后发送
- **B站 Cookie 高画质**：Cookie 解析返回 DASH 音视频流时，需要下载并合并后发送
- **Twitter/X 视频**：视频会强制缓存后发送

**概率风控（建议缓存目录可用）**
- **小红书**：部分媒体有身份验证和时效性，缓存发送更稳定
- **TikTok**：受地区和风控影响较明显，必要时请同时配置代理

**提高性能（可选）**
- **B站**：支持 Range 并发下载提升速度；Cookie 登录后 DASH 音视频流也可独立 Range 加速
- **Twitter/X**：支持 Range 请求，配置缓存目录后可并发下载提升速度

> 💡 Range 下载仅为性能优化，未配置缓存目录时普通视频会尽量退化为直链发送；必须缓存的媒体会被跳过并在文本中说明原因

---

## 🍪 B站 Cookie 与画质增强

配置 Cookie 后可解锁更高画质（如 1080P+、4K），视频通过 DASH 音视频流下载

### 配置方式

1. 在 `B站增强 → 携带Cookie解析` 中开启
2. 填入 B站 Cookie（浏览器 F12 → Network → 任意请求的 Cookie 头）
3. 选择 `最高画质`（实际画质取决于账号会员等级和视频源）
4. **前置条件**：媒体缓存目录必须可用

> **注意**：缓存目录不可用时，插件会自动旁路 B站 Cookie、DASH 下载和管理员协助登录，回退到无 Cookie 直链解析路径

### 管理员协助登录

Cookie 会过期失效开启 `管理员协助登录` 后，当 Cookie 失效时插件会自动私聊管理员，引导通过扫码重新登录：

1. 在 `权限控制 → 管理员ID` 填写你的用户 ID
2. 在 `B站增强 → 管理员协助登录` 中开启
3. Cookie 失效时，插件向管理员私聊发送确认请求
4. 管理员回复确认后，收到登录二维码/链接
5. 扫码完成后 Cookie 自动更新，无需手动替换

> **参数说明**：`回复超时` 控制等待管理员响应的时间（默认 1440 分钟）；`请求冷却` 控制两次协助请求的最小间隔，避免频繁打扰

---

## 🔁 媒体中转模式

当 AstrBot 与消息平台协议端（如 NapCat、Lagrange）**不在同一台机器**或**无法共享文件目录**时，本地下载的媒体文件对协议端不可达媒体中转模式通过 AstrBot 内置 HTTP 服务桥接，将已缓存的本地文件转为可回调的临时 URL 发送

### 适用场景

- AstrBot 和协议端分别部署在不同服务器
- Docker 容器间未挂载共享目录
- 协议端无法通过 `file://` 协议访问 AstrBot 本地文件

### 配置方式

1. 在 `媒体中转 → 启用` 中开启
2. 填写 `AstrBot回调地址`：协议端能访问到 AstrBot 的 HTTP 地址（如 `http://192.168.1.100:6185`）
   - 同机部署可用 `http://localhost:6185`
   - 跨服务器需填公网 IP 或域名
   - 留空时会尝试使用 AstrBot 全局回调地址
3. 设置 `中转缓存有效期`（默认 300 秒），到期后临时链接失效并自动清理缓存

> **注意**：开启媒体中转后，不会强制下载所有媒体，也不会自动切换缓存目录；它只会增强已经成功缓存到本地的媒体文件Token 注册失败时自动回退为本地文件发送

---

## 📝 注意事项

- **B站**：配置有效 Cookie 且缓存目录可用时，视频通过 DASH 流下载（详见上方 Cookie 章节）；转发动态会合并展示原动态数据
- **抖音**：短链会先展开，再按视频或图集分享页读取作品数据
- **TikTok**：页面解析会优先使用系统 `curl`，如目标环境没有 `curl` 会回退普通请求；受地区和风控影响时建议开启代理
- **小红书**：链接有身份验证和时效性，部分分享链接解析结果有水印
- **小黑盒**：BBS 分享和部分视频解析依赖签名能力，需要安装 `cryptography` 库；游戏预览视频下载速度不佳时请启用代理
- **推特**：解析 api 优先使用 fxtwitter 服务，服务不可用时尝试 Guest GraphQL 回退；图片和视频 cdn 大多被墙建议开启代理
- **图片处理**：格式除 ```.jpg```, ```.jpeg```, ```.png``` 外的所有图片会先转换为 ```.png``` 格式再发送
- **其他**：插件会跳过包含 `"原始链接："` 字段的消息，防止重复解析；直播链接会自动跳过

---

## 🙏 鸣谢

- [bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect) - B站解析端点
- [FxEmbed](https://github.com/FxEmbed/FxEmbed) - 推特解析服务
- [ParseHub](https://github.com/z-mio/ParseHub) - 小黑盒 BBS 帖子解析方法
- [tianger-mckz](https://github.com/drdon1234/astrbot_plugin_bilibili_bot/issues/1#issuecomment-3517087034) | [ScryAbu](https://github.com/drdon1234/astrbot_plugin_media_parser/issues/16#issuecomment-3726729850) | [WWWA7](https://github.com/drdon1234/astrbot_plugin_media_parser/pull/17#issue-3799325283) - QQ小程序卡片链接提取方法
- [CSDN 博客](https://blog.csdn.net/qq_53153535/article/details/141297614) - 抖音解析方法

## 🤝 社区贡献与扩展
- 如需解析 YouTube 平台链接，请下载带有 v4.3.1-yt-feature 标签的版本（贡献者：[shangzhimingge](https://github.com/shangzhimingge)）
- 欢迎提交 PR 以添加更多平台解析支持和新功能
