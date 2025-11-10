# 聚合解析流媒体平台链接，转换为媒体直链发送

> 开箱即用，可手动配置以优化使用体验

## 功能特性

- 自动识别会话中的视频或图片链接，并解析成直链发送  
- 支持多平台并行解析与批量处理  
- 提供消息打包为集合的返回方式（可配置开关）  

---

## 支持的流媒体平台

<table border="1" cellpadding="5" cellspacing="0">
<thead>
<tr>
<th>平台</th>
<th>可解析的媒体类型</th>
</tr>
</thead>
<tbody>
<tr>
<td>B站</td>
<td>视频（UGC/PGC）</td>
</tr>
<tr>
<td>抖音</td>
<td>视频、图片集</td>
</tr>
<tr>
<td>快手</td>
<td>视频、图片集</td>
</tr>
<tr>
<td>推特</td>
<td>视频、图片集</td>
</tr>
</tbody>
</table>

---

## 安装

### 依赖库安装（重要）

使用前请先安装依赖库：`aiohttp`

- 打开 “AstrBot WebUI” -> “控制台” -> “安装 Pip 库”  
- 在库名栏输入 `aiohttp` 并点击安装  

### 插件安装

1) 通过 插件市场 安装  
- 打开 “AstrBot WebUI” -> “插件市场” -> “右上角 Search”  
- 搜索与本项目相关的关键词，找到插件后点击安装  
- 推荐通过唯一标识符搜索：`astrbot_plugin_video_parser`  

2) 通过 GitHub 仓库链接 安装  
- 打开 “AstrBot WebUI” -> “插件市场” -> “右下角 ‘+’ 按钮”  
- 输入以下地址并点击安装：  
  https://github.com/drdon1234/astrbot_plugin_video_parser

---

## 使用说明

### 🎉 开箱即用

下载插件后，**无需配置** 即可：

- ✅ **自动解析** B站、抖音、快手、推特链接
- ✅ **下载并发送** 全部 100MB 以下的 B站、抖音、快手媒体
- ✅ **下载并发送** 大部分直连 CDN 的推特视频（图片需配置代理）

## 配置文档

### 基础配置

<table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse;">
<thead>
<tr style="background-color: #f0f0f0;">
<th>配置项</th>
<th>类型</th>
<th>默认值</th>
<th>说明</th>
</tr>
</thead>
<tbody>
<tr>
<td>是否将解析结果打包为消息集合</td>
<td>bool</td>
<td>true</td>
<td>在微信平台使用时需要禁用此项</td>
</tr>
</tbody>
</table>

### 触发设置

<table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse;">
<thead>
<tr style="background-color: #f0f0f0;">
<th>配置项</th>
<th>类型</th>
<th>默认值</th>
<th>说明</th>
</tr>
</thead>
<tbody>
<tr>
<td>是否自动解析视频链接</td>
<td>bool</td>
<td>true</td>
<td>-</td>
</tr>
<tr>
<td>手动触发解析的关键词列表</td>
<td>list</td>
<td>["视频解析", "解析视频"]</td>
<td>当禁用自动解析时，消息头使用这些关键词才会触发解析</td>
</tr>
</tbody>
</table>

### 媒体大小设置

<table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse;">
<thead>
<tr style="background-color: #f0f0f0;">
<th>配置项</th>
<th>类型</th>
<th>默认值</th>
<th>说明</th>
</tr>
</thead>
<tbody>
<tr>
<td>最大允许发送的媒体大小(MB)</td>
<td>float</td>
<td>0.0</td>
<td>超过此大小的媒体（视频和图片）将被跳过，不下载也不发送。设置为0表示不限制</td>
</tr>
<tr>
<td>大媒体阈值(MB)</td>
<td>float</td>
<td>100.0</td>
<td>当媒体大小（视频和图片）超过此阈值时，将下载到缓存目录。媒体会单独发送而不包含在转发消息集合中。不能超过消息适配器100MB硬性阈值。设置为0表示不限制</td>
</tr>
</tbody>
</table>

### 下载和缓存设置

<table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse;">
<thead>
<tr style="background-color: #f0f0f0;">
<th>配置项</th>
<th>类型</th>
<th>默认值</th>
<th>说明</th>
</tr>
</thead>
<tbody>
<tr>
<td>媒体文件缓存目录</td>
<td>string</td>
<td>"/app/sharedFolder/video_parser/cache"</td>
<td>用于推特视频和所有超过阈值的大媒体（视频和图片）</td>
</tr>
<tr>
<td>是否预先下载所有媒体到本地</td>
<td>bool</td>
<td>false</td>
<td>启用后，所有媒体文件将并发下载到缓存目录，发送时使用本地路径。可以提高发送成功率，减少总下载时间，但会短时间增加磁盘占用</td>
</tr>
<tr>
<td>最大并发下载数</td>
<td>int</td>
<td>3</td>
<td>当启用预先下载所有媒体到本地时，同时下载的媒体文件数量上限。建议值：3-5</td>
</tr>
</tbody>
</table>

### 解析器启用设置

<table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse;">
<thead>
<tr style="background-color: #f0f0f0;">
<th>配置项</th>
<th>类型</th>
<th>默认值</th>
<th>说明</th>
</tr>
</thead>
<tbody>
<tr>
<td>是否启用B站解析器</td>
<td>bool</td>
<td>true</td>
<td>-</td>
</tr>
<tr>
<td>是否启用抖音解析器</td>
<td>bool</td>
<td>true</td>
<td>-</td>
</tr>
<tr>
<td>是否启用快手解析器</td>
<td>bool</td>
<td>true</td>
<td>-</td>
</tr>
<tr>
<td>是否启用推特解析器</td>
<td>bool</td>
<td>true</td>
<td>-</td>
</tr>
</tbody>
</table>

### 推特代理设置

<table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse;">
<thead>
<tr style="background-color: #f0f0f0;">
<th>配置项</th>
<th>类型</th>
<th>默认值</th>
<th>说明</th>
</tr>
</thead>
<tbody>
<tr>
<td>推特图片下载是否使用代理</td>
<td>bool</td>
<td>false</td>
<td>启用后，推特图片下载将通过代理进行。图片CDN大多被墙，建议开启以提升成功率</td>
</tr>
<tr>
<td>推特视频下载是否使用代理</td>
<td>bool</td>
<td>false</td>
<td>启用后，推特视频下载将通过代理进行。视频CDN几乎不受影响，通常无需开启以节约流量</td>
</tr>
<tr>
<td>推特代理地址</td>
<td>string</td>
<td>""</td>
<td>代理地址格式：http://host:port 或 socks5://host:port。图片和视频共用此代理地址。fxtwitter API接口不需要代理，会自动直连</td>
</tr>
</tbody>
</table>

---

## 已知问题

- 微信无法正确推送视频消息（疑似消息平台问题）  

---

## 鸣谢

- B站解析端点参考自：GitHub 项目 bilibili-API-collect  
  https://github.com/SocialSisterYi/bilibili-API-collect
- 抖音解析方法参考自：CSDN 博客文章  
  https://blog.csdn.net/qq_53153535/article/details/141297614
- 快手解析方法：自主实现
- 推特解析使用免费第三方服务：fxtwitter（GitHub 项目 FxEmbed）  
  https://github.com/FxEmbed/FxEmbed
