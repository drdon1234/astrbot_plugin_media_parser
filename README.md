# 聚合解析流媒体平台链接，转换为媒体直链发送

AstrBot 插件，支持自动解析流媒体平台链接，获取媒体元数据转换为直链发送

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
<td>短链（<code>b23.tv/...</code>）<br>视频av号（<code>www.bilibili.com/video/av...</code>）<br>视频BV号（<code>www.bilibili.com/video/BV...</code>）<br>动态长链（<code>www.bilibili.com/opus/...</code>）<br>动态短链（<code>t.bilibili.com/...</code>）<br>小程序卡片（<code>message.meta.detail_1.qqdocurl</code>）</td>
<td class="center">视频、图片</td>
</tr>
<tr>
<td class="center"><strong>抖音</strong></td>
<td>短链（<code>v.douyin.com/...</code>）<br>视频长链（<code>www.douyin.com/video/...</code>）<br>图集长链（<code>www.douyin.com/note/...</code>）</td>
<td class="center">视频、图片</td>
</tr>
<tr>
<td class="center"><strong>快手</strong></td>
<td>短链（<code>v.kuaishou.com/...</code>）<br>视频长链（<code>www.kuaishou.com/short-video/...</code>）</td>
<td class="center">视频、图片</td>
</tr>
<tr>
<td class="center"><strong>微博</strong></td>
<td>桌面端博客链接（<code>weibo.com/...</code>）<br>移动端博客链接（<code>m.weibo.cn/detail/...</code>）<br>移动端视频分享链接（<code>video.weibo.com/show?fid=...</code>）<br>视频分享链接重定向（<code>weibo.com/tv/show/...</code>）<br>小程序卡片（<code>message.meta.detail_1.qqdocurl</code>）</td>
<td class="center">视频、图片</td>
</tr>
<tr>
<td class="center"><strong>小红书</strong></td>
<td>短链（<code>xhslink.com/...</code>）<br>笔记长链（<code>www.xiaohongshu.com/explore/...</code>）<br>笔记长链（<code>www.xiaohongshu.com/discovery/item/...</code>）<br>小程序卡片（<code>message.meta.news.jumpUrl</code>）</td>
<td class="center">视频、图片</td>
</tr>
<tr>
<td class="center"><strong>小黑盒</strong></td>
<td>Web链接（<code>www.xiaoheihe.cn/app/topic/game/...</code>）<br>App分享链接（<code>api.xiaoheihe.cn/game/share_game_detail?...</code>）<br>小程序卡片（<code>message.meta.news.jumpUrl</code>）</td>
<td class="center">游戏页详情</td>
</tr>
<tr>
<td class="center"><strong>推特</strong></td>
<td>twitter 链接（<code>twitter.com/.../status/...</code>）<br>x 链接（<code>x.com/.../status/...</code>）</td>
<td class="center">视频、图片</td>
</tr>
</tbody>
</table>

---

## 🚀 快速开始

### 安装

1. **依赖库**：打开 AstrBot WebUI → 控制台 → 安装 Pip 库，输入 `aiohttp` 并安装
2. **插件**：打开 AstrBot WebUI → 插件市场搜索 `astrbot_plugin_media_parser` 并安装

### 特性

- ✅ 开箱即用，无需配置即可解析大部分平台
- ✅ 自动识别并解析链接
- ✅ 可选 B站 Cookie 解锁高画质 + 管理员协助自动续期
- ✅ 媒体中转模式，跨服务器部署无需共享目录

---

## ⚙️ 优化体验

配置 **缓存目录** 和打开 **预下载模式** 可显著提升解析成功率和发送体验。

> **原因**：消息平台使用直链发送媒体时无法指定 header、referer、cookie 等参数，部分风控严格的平台会返回 403 Forbidden。  
> **建议**：同时配置缓存目录和开启预下载模式。

### 各平台特殊情况

**硬性要求（必须预下载）**
- **微博**：所有视频必须正确携带 referer 参数才能下载
- **小黑盒**：M3U8 格式必须将音视频分片下载到本地再合并

**概率风控（建议预下载）**
- **小红书**：部分媒体使用 URL 发送有概率风控

**提高性能（可选）**
- **B站**：支持 Range 并发下载提升速度；Cookie 登录后 DASH 音视频流也可独立 Range 加速
- **Twitter/X**：支持 Range 请求，配置缓存目录后可并发下载提升速度

> 💡 Range 下载仅为性能优化，未配置缓存目录时会自动退化为单文件下载模式

---

## 🍪 B站 Cookie 与画质增强

配置 Cookie 后可解锁更高画质（如 1080P+、4K），视频通过 DASH 音视频流下载。

### 配置方式

1. 在 `B站增强 → 携带Cookie解析` 中开启
2. 填入 B站 Cookie（浏览器 F12 → Network → 任意请求的 Cookie 头）
3. 选择 `最高画质`（实际画质取决于账号会员等级和视频源）
4. **前置条件**：必须同时开启 `预下载所有媒体`

### 管理员协助登录

Cookie 会过期失效。开启 `管理员协助登录` 后，当 Cookie 失效时插件会自动私聊管理员，引导通过扫码重新登录：

1. 在 `权限控制 → 管理员ID` 填写你的用户 ID
2. 在 `B站增强 → 管理员协助登录` 中开启
3. Cookie 失效时，插件向管理员私聊发送确认请求
4. 管理员回复确认后，收到登录二维码/链接
5. 扫码完成后 Cookie 自动更新，无需手动替换

> **参数说明**：`回复超时` 控制等待管理员响应的时间（默认 1440 分钟）；`请求冷却` 控制两次协助请求的最小间隔，避免频繁打扰

---

## 🔁 媒体中转模式

当 AstrBot 与消息平台协议端（如 NapCat、Lagrange）**不在同一台机器**或**无法共享文件目录**时，本地下载的媒体文件对协议端不可达。媒体中转模式通过 AstrBot 内置 HTTP 服务桥接，将本地文件转为可回调的临时 URL 发送。

### 适用场景

- AstrBot 和协议端分别部署在不同服务器
- Docker 容器间未挂载共享目录
- 协议端无法通过 `file://` 协议访问 AstrBot 本地文件

### 配置方式

1. 在 `媒体中转 → 启用` 中开启
2. 填写 `AstrBot回调地址`：协议端能访问到 AstrBot 的 HTTP 地址（如 `http://192.168.1.100:6185`）
   - 同机部署可用 `http://localhost:6185`
   - 跨服务器需填公网 IP 或域名
3. 设置 `中转缓存有效期`（默认 300 秒），到期后临时链接失效并自动清理缓存

> **注意**：开启媒体中转后，`预下载` 会被强制启用，缓存目录自动切换为系统临时目录，不受手动配置的 `缓存目录` 影响。Token 注册失败时自动回退为原始直链模式。

---

## 📝 注意事项

- **B站**：配置有效 Cookie 后视频通过 DASH 流下载（详见上方 Cookie 章节）；转发动态会使用 ```"转发动态数据（原始动态数据）"``` 组织文本格式解析结果
- **小红书**：链接有身份验证和时效性，分享链接解析结果有水印
- **小黑盒**：不携带 token 只能解析游戏页详情，游戏预览视频下载速度不佳时请启用代理
- **推特**：解析 api 使用 fxtwitter 服务可直连，图片 cdn 大多被墙建议开启代理，视频 cdn ~~可直连~~ 近期大多被墙建议开启代理
- **图片处理**：格式除 ```.jpg```, ```.jpeg```, ```.png``` 外的所有图片会先转换为 ```.png``` 格式再发送
- **热评获取**：支持获取 B站、微博、小红书 的热门评论，默认关闭，需在配置文件中自行启用
- **黑名单 / 白名单**：优先级：个人白名单 > 个人黑名单 > 群组白名单 > 群组黑名单
- **其他**：插件会跳过包含 `"原始链接："` 字段的消息，防止重复解析

---

## 🙏 鸣谢

- [bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect) - B站解析端点
- [FxEmbed](https://github.com/FxEmbed/FxEmbed) - 推特解析服务
- [tianger-mckz](https://github.com/drdon1234/astrbot_plugin_bilibili_bot/issues/1#issuecomment-3517087034) | [ScryAbu](https://github.com/drdon1234/astrbot_plugin_media_parser/issues/16#issuecomment-3726729850) | [WWWA7](https://github.com/drdon1234/astrbot_plugin_media_parser/pull/17#issue-3799325283) - QQ小程序卡片链接提取方法
- [CSDN 博客](https://blog.csdn.net/qq_53153535/article/details/141297614) - 抖音解析方法

## 🤝 社区贡献与扩展
- 如需解析 YouTube 平台链接，请下载带有 v4.3.1-yt-feature 标签的版本（贡献者：[shangzhimingge](https://github.com/shangzhimingge)）
- 欢迎提交 PR 以添加更多平台解析支持和新功能
