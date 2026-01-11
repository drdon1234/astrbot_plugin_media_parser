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

- ✅ 无需配置任何 cookie
- ✅ 自动识别并解析链接

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
- **B站、Twitter/X**：支持 Range 请求，配置缓存目录后可并发下载提升速度

> 💡 Range 下载仅为性能优化，未配置缓存目录时会自动退化为单文件下载模式

---

## 📝 注意事项

- **B站**：转发动态会使用 ```"转发动态数据（原始动态数据）"``` 组织文本格式解析结果
- **小红书**：链接有身份验证和时效性，分享链接解析结果有水印
- **小黑盒**：不携带 token 只能解析游戏页详情，游戏预览视频下载速度不佳时请启用代理
- **推特**：解析 api 使用 fxtwitter 服务可直连，图片 cdn 大多被墙建议开启代理，视频 cdn ~~可直连~~ 近期大多被墙建议开启代理
- **图片处理** 格式除 ```.jpg```, ```.jpeg```, ```.png``` 外的所有图片会先转换为 ```.png``` 格式再发送
- **其他**：插件会跳过包含 `"原始链接："` 字段的消息，防止重复解析

---

## 🙏 鸣谢

- [bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect) - B站解析端点
- [FxEmbed](https://github.com/FxEmbed/FxEmbed) - 推特解析服务
- [tianger-mckz](https://github.com/drdon1234/astrbot_plugin_bilibili_bot/issues/1#issuecomment-3517087034) | [ScryAbu](https://github.com/drdon1234/astrbot_plugin_media_parser/issues/16#issuecomment-3726729850) | [WWWA7](https://github.com/drdon1234/astrbot_plugin_media_parser/pull/17#issue-3799325283) - QQ小程序卡片链接提取方法
- [CSDN 博客](https://blog.csdn.net/qq_53153535/article/details/141297614) - 抖音解析方法
