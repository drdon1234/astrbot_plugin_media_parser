# 聚合解析流媒体平台链接，转换为媒体直链发送

## 使用说明

### 🎉 开箱即用

- ✅ 无需配置任何 cookie
- ✅ 自动识别并解析链接，获取媒体元数据

### ⚙️ 优化体验
- 配置 **缓存目录** 
- 打开 **预下载模式**  

> **由于消息平台使用直链发送媒体的局限性（无法指定 header、referer、cookie 等参数）：**
> - 部分风控严格的平台会返回 403 Forbidden，此时需要先将媒体下载到本地再发送  
> 
> **已知受影响的平台包括：**
> - 部分小红书媒体（视频/图片）  
> - 全部微博视频

---

## 支持的流媒体平台

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

## 安装

### 依赖库安装（重要）

使用前请先安装依赖库：`aiohttp`

1. 打开 **AstrBot WebUI** → **控制台** → **安装 Pip 库**
2. 在库名栏输入 `aiohttp` 并点击安装

### 插件安装

#### 方式一：通过插件市场安装

1. 打开 **AstrBot WebUI** → **插件市场** → **右上角 Search**
2. 搜索与本项目相关的关键词，找到插件后点击安装
3. 推荐通过唯一标识符搜索：`astrbot_plugin_media_parser`

#### 方式二：通过 GitHub 仓库链接安装

1. 打开 **AstrBot WebUI** → **插件市场** → **右下角 '+' 按钮**
2. 输入以下地址并点击安装：
   ```
   https://github.com/drdon1234/astrbot_plugin_media_parser
   ```

---

## 注意事项

1. **B站**：
- 转发动态会使用"转发动态数据（原始动态数据）"组织文本格式解析结果

2. **微博**：
- 视频直链使用严格风控策略，开启预下载模式保存到本地才能正确发送

3. **小红书**：
- 所有链接均有身份验证和时效性，在有效期内发送完整链接才能成功解析
- 分享链接的解析结果有水印

4. **小黑盒**
- 不携带 token 的状态只能解析游戏页详情（文字，视频，图片）
- 游戏预览视频实际从 Steam CDN 请求音视频分片，下载速度不佳时请启用代理

5. **推特**：
- 解析 API 使用 fxtwitter 服务，无需代理
- 图片 CDN 大多被墙，建议开启代理
- 视频 CDN 通常不受影响，可直连

6. **其他**
- 插件在任何消息中匹配到 ```"原始链接："``` 字段将静默跳过解析
- 这是为了防止多个使用本插件的 Bot 重复解析其他 Bot 发送的文本格式解析结果

---

## 鸣谢

- **B站解析端点** 参考自：GitHub 项目 bilibili-API-collect  
  详见：[bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect)
- **QQ小程序卡片链接提取方法** 参考自：GitHub 用户 tianger-mckz  
  详见：[issue #1](https://github.com/drdon1234/astrbot_plugin_bilibili_bot/issues/1#issuecomment-3517087034)
- **抖音解析方法** 参考自：CSDN 博客文章  
  详见：[文章链接](https://blog.csdn.net/qq_53153535/article/details/141297614)
- **推特解析** 使用免费第三方服务：fxtwitter（GitHub 项目 FxEmbed）  
  详见：[FxEmbed](https://github.com/FxEmbed/FxEmbed)
