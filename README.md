# 聚合解析流媒体平台链接，转换为媒体直链发送

<style>
table.config-table {
  border-collapse: collapse;
  width: 100%;
  border: 1px solid #ddd;
}
table.config-table th,
table.config-table td {
  border: 1px solid #ddd;
  padding: 8px;
}
table.config-table thead tr {
  background-color: #f5f5f5;
}
table.config-table th {
  text-align: center;
}
table.config-table td.center {
  text-align: center;
}
</style>

## 使用说明

### 🎉 开箱即用

- ✅ 无需配置任何 cookie
- ✅ 自动识别并解析链接，获取媒体元数据
- ✅ 自动下载并发送 100MB 以下的媒体（推特媒体除外）

### ⚙️ 手动配置（可选）

- **视频大小限制**：限制可解析的视频大小，减少服务器负载
- **缓存目录**：（同时启用推特解析器）配置后推特视频可正常发送
- **预下载模式**：预先下载媒体到本地，提高发送成功率并减少下载时间（需先配置缓存目录）
- **最大并发下载数**：限制单个解析器并发下载数量，减少服务器负载
- **推特代理**：配置后推特图片和少量被墙视频可正常发送

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
<td>短链（<code>b23.tv/...</code>）<br>视频av号（<code>www.bilibili.com/video/av...</code>）<br>视频BV号（<code>www.bilibili.com/video/BV...</code>）<br>动态长链（<code>www.bilibili.com/opus/...</code>）<br>动态短链（<code>t.bilibili.com/...</code>）</td>
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
<td class="center"><strong>小红书</strong></td>
<td>短链（<code>xhslink.com/...</code>）<br>笔记长链（<code>www.xiaohongshu.com/explore/...</code>）<br>笔记长链（<code>www.xiaohongshu.com/discovery/item/...</code>）</td>
<td class="center">视频、图片</td>
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

## 配置文档

### **使用前请核对并根据需要修改配置文件**：

- 打开 AstrBot WebUI → `插件` → 找到本插件 → `插件配置` → 根据需要进行设置

### 基础配置

<table class="config-table">
<thead>
<tr>
<th>配置项</th>
<th>类型</th>
<th>默认值</th>
<th>说明</th>
</tr>
</thead>
<tbody>
<tr>
<td>是否将解析结果打包为消息集合</td>
<td class="center"><code>bool</code></td>
<td class="center"><code>true</code></td>
<td>在微信平台使用时需要禁用此项</td>
</tr>
</tbody>
</table>

### 触发设置

<table class="config-table">
<thead>
<tr>
<th>配置项</th>
<th>类型</th>
<th>默认值</th>
<th>说明</th>
</tr>
</thead>
<tbody>
<tr>
<td>是否自动解析视频链接</td>
<td class="center"><code>bool</code></td>
<td class="center"><code>true</code></td>
<td>-</td>
</tr>
<tr>
<td>手动触发解析的关键词列表</td>
<td class="center"><code>list</code></td>
<td class="center"><code>["视频解析", "解析视频"]</code></td>
<td>当禁用自动解析时，消息头使用这些关键词才会触发解析</td>
</tr>
</tbody>
</table>

### 视频大小设置

<table class="config-table">
<thead>
<tr>
<th>配置项</th>
<th>类型</th>
<th>默认值</th>
<th>说明</th>
</tr>
</thead>
<tbody>
<tr>
<td>最大允许发送的视频大小(MB)</td>
<td class="center"><code>float</code></td>
<td class="center"><code>0.0</code></td>
<td>超过此大小的视频将被跳过，不下载也不发送。设置为0表示不限制</td>
</tr>
<tr>
<td>大视频阈值(MB)</td>
<td class="center"><code>float</code></td>
<td class="center"><code>100.0</code></td>
<td>当视频大小超过此阈值时，将下载到缓存目录。视频会单独发送而不包含在转发消息集合中。不能超过消息适配器100MB硬性阈值。设置为0表示不限制</td>
</tr>
</tbody>
</table>

### 下载和缓存设置

<table class="config-table">
<thead>
<tr>
<th>配置项</th>
<th>类型</th>
<th>默认值</th>
<th>说明</th>
</tr>
</thead>
<tbody>
<tr>
<td>媒体文件缓存目录</td>
<td class="center"><code>string</code></td>
<td class="center"><code>"/app/sharedFolder/video_parser/cache"</code></td>
<td>用于推特视频和所有超过阈值的大媒体（视频和图片）</td>
</tr>
<tr>
<td>是否预先下载所有媒体到本地</td>
<td class="center"><code>bool</code></td>
<td class="center"><code>false</code></td>
<td>启用后，所有媒体文件将并发下载到缓存目录，发送时使用本地路径。可以提高发送成功率，减少总下载时间，但会短时间增加磁盘占用</td>
</tr>
<tr>
<td>最大并发下载数</td>
<td class="center"><code>int</code></td>
<td class="center"><code>3</code></td>
<td>当启用预先下载所有媒体到本地时，同时下载的媒体文件数量上限。建议值：3-5</td>
</tr>
</tbody>
</table>

### 解析器启用设置

<table class="config-table">
<thead>
<tr>
<th>配置项</th>
<th>类型</th>
<th>默认值</th>
<th>说明</th>
</tr>
</thead>
<tbody>
<tr>
<td>是否启用B站解析器</td>
<td class="center"><code>bool</code></td>
<td class="center"><code>true</code></td>
<td>-</td>
</tr>
<tr>
<td>是否启用抖音解析器</td>
<td class="center"><code>bool</code></td>
<td class="center"><code>true</code></td>
<td>-</td>
</tr>
<tr>
<td>是否启用快手解析器</td>
<td class="center"><code>bool</code></td>
<td class="center"><code>true</code></td>
<td>-</td>
</tr>
<tr>
<td>是否启用小红书解析器</td>
<td class="center"><code>bool</code></td>
<td class="center"><code>true</code></td>
<td>-</td>
</tr>
<tr>
<td>是否启用推特解析器</td>
<td class="center"><code>bool</code></td>
<td class="center"><code>true</code></td>
<td>-</td>
</tr>
</tbody>
</table>

### 推特代理设置

<table class="config-table">
<thead>
<tr>
<th>配置项</th>
<th>类型</th>
<th>默认值</th>
<th>说明</th>
</tr>
</thead>
<tbody>
<tr>
<td>推特图片下载是否使用代理</td>
<td class="center"><code>bool</code></td>
<td class="center"><code>false</code></td>
<td>启用后，推特图片下载将通过代理进行。图片CDN大多被墙，建议开启以提升成功率</td>
</tr>
<tr>
<td>推特视频下载是否使用代理</td>
<td class="center"><code>bool</code></td>
<td class="center"><code>false</code></td>
<td>启用后，推特视频下载将通过代理进行。视频CDN几乎不受影响，通常无需开启以节约流量</td>
</tr>
<tr>
<td>推特代理地址</td>
<td class="center"><code>string</code></td>
<td class="center"><code>""</code></td>
<td>代理地址格式：<code>http://host:port</code> 或 <code>socks5://host:port</code>。图片和视频共用此代理地址。fxtwitter API接口不需要代理，会自动直连</td>
</tr>
</tbody>
</table>

---

## 注意事项

- 小红书的所有链接均有身份验证和时效性，在有效期内发送完整链接才能成功解析
- 小红书分享长短链均有水印，explore 类型链接无水印
- 推特链接无法解析第三方网站关联媒体

---

## 鸣谢

- **B站解析端点**参考自：GitHub 项目 [bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect)
- **QQ小程序卡片链接提取方法**参考自：GitHub 用户 [tianger-mckz](https://github.com/tianger-mckz)  
  详见：[issue #1](https://github.com/drdon1234/astrbot_plugin_bilibili_bot/issues/1#issuecomment-3517087034)
- **抖音解析方法**参考自：CSDN 博客文章  
  [文章链接](https://blog.csdn.net/qq_53153535/article/details/141297614)
- **推特解析**使用免费第三方服务：fxtwitter（GitHub 项目 [FxEmbed](https://github.com/FxEmbed/FxEmbed)）
