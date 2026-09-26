# 平台解析备忘

本文记录当前 30 个平台解析器的链接范围、取数路径、字段来源与处理边界，平台顺序与配置、解析器工厂及本地发现列表一致。内容以代码实现为准；文中的上游接口与页面结构属于平台未公开承诺的协议，历史样本验证不代表其持续可用。使用配置见 [README](../README.md)，公共数据流见 [架构说明](ARCHITECTURE.md)。

## 一、通用约定

### 数据来源

分享链接只提供内容入口，解析所需数据通常来自以下位置：

- 短链重定向后的规范 URL
- 平台 Web 或移动前端调用的公开接口
- HTML 中注入的页面状态
- SSR / rehydration 脚本
- 旧版页面保留的内联 JSON 或媒体字段

### 处理流程

```text
分享链接
  ↓
展开短链 / 清理分享参数
  ↓
识别内容 ID 与内容形态
  ↓
选择对应页面或接口
  ↓
读取结构化数据
  ↓
提取标题、作者、正文、时间、媒体候选与访问状态
  ↓
写入 MediaMetadata，交由下载与发送流程处理
```

### 设计原则

1. **先识别内容形态，再读取字段。** 视频、图集、音频、动态、番剧、帖子与游戏页的数据结构互不相同。
2. **优先使用前端实际使用的结构化数据。** 页面状态和接口 JSON 比基于正则的 HTML 抽取更稳定。
3. **校验内容身份。** 响应中的内容 ID 必须与请求目标一致，防止将其他内容的正文或媒体归入当前链接。
4. **保留上下文与候选。** 媒体地址、访问限制、来源页与请求环境都会影响后续下载，同一媒体的多个地址按优先级保留为一个候选组。
5. **解析阶段不下载媒体。** 媒体探测、缓存与格式转换统一由下载层处理。

### 评论通用规则

评论在配置和消息中统称“热评”，实际来源可以是热门、精选、默认顺序、时间顺序评论或游戏评价，具体排序以各平台说明为准。以下规则适用于所有已接入评论的平台，平台章节不再重复：

- 条数由 `message.hot_comments.count` 统一控制，默认 `0`；各平台开关位于 `message.hot_comments.<平台>`，默认开启。
- 只有条数大于 `0`、平台开关开启且输出模式包含文本时才请求评论；`仅富媒体` 模式不请求评论。
- 评论请求的页数、条数与响应体积均有上限，按评论 ID 去重；评论中的图片、表情等非文本内容以文字标记或链接表示，不加入正文媒体。
- 虎扑、豆瓣、V2EX、稀土掘金、Gitee、GitLab 与 Pixiv 在点赞数未知时省略该字段，消息中显示 `-`，其余平台按接口返回值展示。缩写赞数与相对时间保留平台原文。
- 评论失败时保留正文、媒体及已取得的评论；`asyncio.CancelledError` 继续向上传播。

快手、微信、博客园、Twitter/X 与 GitHub 未接入评论。

## 二、B站

支持能力：视频 / 图片 / 文本 / 热评

### 链接范围

`bilibili.com` 下包含多套独立的内容模型：UGC 视频（BV / AV）、PGC 番剧（`/bangumi/play/ep{id}`、`/bangumi/play/ss{id}` 或 `ep_id` / `season_id` 参数）、动态与 opus（`/opus/{id}`、`t.bilibili.com/{id}`），以及 `b23.tv` 短链。解析前先展开短链并过滤直播入口，再按目标类型进入对应链路。

```text
b23.tv / bilibili.com / t.bilibili.com
  ↓
展开 b23 短链
  ↓
过滤直播入口
  ↓
判断 opus / UGC / PGC
  ↓
进入对应取数链路
```

### UGC 视频

BV / AV 号定位视频主体，分 P 对应的 `cid` 定位具体播放单元。

```text
BV / AV
  ↓
x/web-interface/view
  ↓
x/player/pagelist
  ↓
按 p 参数选择 cid
  ↓
x/player/wbi/playurl（WBI 签名）
```

`view` 提供标题、作者、简介与发布时间，`pagelist` 提供分 P 列表与 `cid`，`playurl` 提供播放结构。DASH 请求使用 `fnval=4048`，MP4 兼容回退使用 `fnval=1`；不使用旧版 `/x/player/playurl`、HTML5 平台参数或 `fnval=0` 的 FLV 回退。播放结构可能是普通直链，也可能是 DASH 音视频分离流；解析阶段只识别并保留，合并由下载层完成。

### PGC 番剧

番剧不复用 UGC 链路。入口为 `ep_id` 或 `season_id`；仅有 `season_id` 时，先确定首个可播放的 episode。

```text
ep_id / season_id
  ↓
season_id -> 首个 ep_id
  ↓
番剧详情
  ↓
pgc/player/web/v2/playurl
  ↓
探测清晰度与播放结构
```

番剧常受会员、试看、地区与付费限制，是否存在媒体地址不足以判断可访问性。解析结果同时保留访问状态、可播放时长与完整时长，用于说明仅获得预览或无法获得完整视频的原因。

### 动态与 opus

动态本身包含作者、正文与发布时间，内部可能承载图片，也可能引用或转发视频。

```text
opus_id
  ↓
动态接口
  ↓
解析 card / inner card / origin
  ├─ 图片动态 -> 提取 pictures
  ├─ 视频动态 -> 取得内嵌视频链接，进入视频链路
  └─ 转发视频 -> 合并外层动态与内层视频信息
```

转发动态同时保留转发者正文与原视频信息，避免只保留其中一侧造成信息缺失。

### 视频封面

封面统一写入 `video_cover_urls`：UGC 取 `x/web-interface/view` 的 `pic`；PGC 优先取目标 episode 的 `cover`，缺失时回退 season 的 `cover`，并统一规范为 HTTPS 候选组。动态、opus 与转发内容重组视频元数据时同步透传该字段，否则“视频仅发送封面”模式会退回为截取首帧。

### Cookie

Cookie 属于增强条件而非前提。配置 Cookie 时，播放接口可能返回更高清晰度或更多可访问内容；未配置时仍按匿名路径解析。

### 评论

评论请求 `x/v2/reply/wbi/main`，依赖 WBI 签名。签名密钥从导航接口的 `wbi_img` 获取并短期缓存，按前端规则生成请求参数，不硬编码固定签名。

## 三、抖音

支持能力：视频 / 图片 / 文本 / 热评

### 链接范围

常见入口为 `v.douyin.com` 短链，展开后的规范目标为 `/video/{id}`、`/note/{id}` 或 `/slides/{id}`，三种形态分别处理。

### 取数流程

```text
v.douyin.com / douyin.com
  ↓
HEAD 展开，失败时改用 GET
  ↓
判断 video、note 或 slides
  ↓
douyin.com/aweme/v1/web/aweme/detail/
（open.douyin.com 来源上下文 + 最小参数）
  ├─ 成功 -> 使用目标作品详情
  └─ 失败 -> a_bogus 签名 + 有界 ttwid 会话
               ├─ 成功 -> 使用目标作品详情
               └─ 失败 -> slidesinfo 或 iesdouyin.com/share/{type}/{id}/
                         ↓
                    读取 window._ROUTER_DATA
```

首选路径以 `open.douyin.com` 来源上下文和作品 ID 等最小参数请求 Web 详情接口，不依赖 Cookie 或签名，响应须再次校验作品 ID。该路径不可用时，改用 `a_bogus` 签名与短生命周期 `ttwid` 会话，在会话失效、响应非 JSON 或目标不匹配时进行有界重试。两条详情路径均失败时，回退到 slidesinfo 接口或移动分享页；分享页体量较小且通常包含 `window._ROUTER_DATA`，作为末级数据源。

### 字段与媒体

- 视频：从 `videoInfoRes` 读取作品信息与播放地址。
- 图文笔记：从 `noteDetailRes` 读取图片列表。
- slides：从 `slidesInfoRes` 或 `slidesinfo` 接口读取混排条目。

平台可能返回完整播放 URL，也可能只返回资源 ID，后者按播放接口格式补全。图文图片结构可能多层嵌套，解析器递归查找常见 URL 字段，并保留同一图片的多个候选。slides 的 `images` 条目可能内嵌分段视频，须先识别其中的视频与封面地址，确认为纯图片条目后才加入图片列表。

### 评论

复用匿名 `ttwid` 与 `a_bogus` 签名请求 `/aweme/v1/web/comment/list/`，按作品 ID 与游标读取平台默认顺序的评论。

## 四、快手

支持能力：视频 / 图片 / 文本

### 链接范围与取数流程

支持 `v.kuaishou.com` 短链及 `kuaishou.com`、`gifshow.com`、`chenzhongtech.com` 域名。短链先跳转到真实页面；`gifshow.com`、`chenzhongtech.com` 等域名的链接改写为 `m.gifshow.com` 移动页，因其 SSR 包含完整的作品数据。

```text
v.kuaishou.com / kuaishou.com / gifshow.com / chenzhongtech.com
  ↓
展开短链
  ↓
必要时改写为 m.gifshow.com
  ↓
获取页面 HTML
  ↓
优先读取 window.INIT_STATE / window.__APOLLO_STATE__
  ↓
状态缺失或字段不完整时，读取旧版内联字段与 rawData
```

### 字段与媒体

结构化状态中的作品主体为 `photo`。图集完整列表通常位于 `photo.ext_params.atlas.list`，补充字段可能位于 `single` 或相近对象。

- 视频：直接读取作品视频地址。
- 图集：优先读取完整图集列表，将 CDN 前缀与图片路径组合为完整地址，去重后保持候选顺序。

`coverUrls` 仅为封面候选，不代表完整图集；只有取得完整图集列表时才视为图集解析成功。页面未提供完整 SSR 状态时，读取 `photoUrl`、`videoUrl`、`srcNoMark`、`window.rawData` 等候选字段，同样须校验目标内容与媒体完整性。

## 五、AcFun

支持能力：视频 / 图片 / 文本 / 热评

### 链接范围

支持 `acfun.cn`、`www.acfun.cn`、`m.acfun.cn` 上的以下入口：

- 视频 `/v/ac{ID}` 与多 P `/v/ac{ID}_{P}`
- 文章 / 动态 `/a/ac{ID}`
- 番剧 `/bangumi/aa{ID}` 与指定集 `/bangumi/aa{ID}_36188_{itemId}`
- 移动分享 `/v/?ac={ID}`（含分 P）

消息中无协议前缀或与中文标点相邻的链接同样可以提取。所有入口统一规范为 HTTPS 桌面链接，移除追踪参数，保留决定内容的分 P 与分集信息；同一作品的不同分 P、同一番剧的不同集分别解析。

移动分享入口可能将多 P 链接重定向到第一 P，因此在请求前完成规范化。视频与文章共用 ac 编号，`/v/` 入口也可能返回文章，按实际页面类型处理。番剧路径中的 `36188` 是官方脚本使用的固定路由段，决定集数的是末尾的 `itemId`。

番剧链接的 `?ac={序号}` 用于选择花絮，规范化时保留。解析器按番剧状态中的 `sidelights` 清单定位花絮，读取对应投稿页并交叉校验清单与投稿的视频 ID，最终返回投稿的规范链接；索引无效或身份不符时直接报错，不回退到同页正片。

### 取数流程

```text
规范链接
  ↓
window.videoInfo / window.articleInfo / window.bangumiData
  ↓
核对作品 ID、当前视频 ID 与指定分 P / itemId
  ├─ 视频 / 番剧 -> currentVideoInfo.ksPlayJson / ksPlayJsonHevc
  └─ 文章 / 动态 -> parts[].content
```

页面请求失败或缺少可解码状态、且链接不含选集信息时，改为请求 `GET /rest/pc-direct/article/info?articleId=...`，响应须同时满足业务码 `result=0` 与 `articleId` 一致。已解码但身份不符的页面直接报错，不以回退掩盖错误；不存在的分 P 或集数即使返回 HTTP 200，也不以第一条视频代替。

页面身份有效但缺少播放数据时，请求 `GET /rest/pc-direct/play/playInfo/ksPlayJson`，携带当前 `videoId`、页面 `mkey`、作品 `resourceId` 与 `resourceType`（投稿为 2，番剧为 1），由接口校验视频归属。该接口只补充播放数据，不覆盖页面中的毫秒时长。

### 字段与媒体

同一视频的流按 H.264 优先、清晰度降序排列，每档保留主地址与备用 CDN，其后追加 HEVC 候选，全部归入同一个 `video_urls` 项，由下载器依次回退。`m3u8Slice` 仅为片头分片，不作为完整视频。HLS 地址添加 `m3u8:` 前缀，由下载器逐项决定本地拼接，不设置作品级强制下载标记。

文章正文按 HTML 结构解析：图片优先使用懒加载原图，同一 `<video>` 下的多个 `<source>` 合并为一个候选组，脚本与样式不计入正文。封面仅在正文没有图片和视频时补充；视频封面单独写入 `video_cover_urls`，不混入图集。

### 评论

仅普通视频投稿接入评论：请求 `/rest/pc-direct/comment/list`（`sourceType=3`），先取 `hotComments`，再以 `rootComments` 补足。番剧与动态尚未接入评论，其页面 ID 不能作为投稿评论的资源 ID 使用。

### 边界

只处理链接指定的单个播放单元或文章，不批量抓取整季或全部分 P，不支持直播、个人空间、独立音频与应用私有协议。登录、地区与内容访问限制仍可能导致解析失败。

## 六、网易云音乐

支持能力：音频 / 图片 / 文本 / 热评

### 链接范围

支持单曲链接：桌面 `/song?id=…`、片段路由 `/#/song?id=…` 与移动 `/m/song?id=…`，按歌曲 ID 去重并输出规范桌面链接。其他主机、异常端口、ID 冲突、歌单与专辑等集合入口以及应用私有协议均不接受。

### 取数流程

1. 匿名请求 `/api/song/detail/`，获取标题、歌手、专辑、封面与时长。
2. 请求 `/api/song/enhance/player/url/v1`，获取标准音质 MP3 地址。
3. 逐首校验歌曲身份、状态、URL、`freeTrialInfo` 与可播放时长，识别试听。

音频候选写入 `audio_urls`，请求头写入 `audio_headers`，不复用视频字段。音频不可用时仍保留歌曲详情，不引入 Cookie、替代音源或外部服务。平台请求不跟随重定向，JSON 响应有体积上限。

### 评论

请求 `/api/v1/resource/hotcomments/R_SO_4_{id}`，按服务端顺序分页读取，最多 3 页。

## 七、喜马拉雅

支持能力：音频 / 图片 / 文本 / 热评

### 链接范围

支持 `www.ximalaya.com`、`m.ximalaya.com` 与裸域名上的 `/sound/{id}` 和 `/{数字主播ID}/sound/{id}`，按单集编号去重并保持消息中的出现顺序，输出规范的 `/sound/{id}` 链接。专辑、评论详情、短链与应用私有协议不接入。

### 取数流程

匿名请求 `https://m.ximalaya.com/tracks/{id}.json`，获取标题、主播、简介、封面、时长与 `play_path_64`、`play_path_32`、`play_path` 音频候选。解析器校验单集身份、清洗 HTML，媒体地址只接受平台 CDN 域名。

候选地址包含 `_preview_` 时标记为试听，不与完整音频混入同一候选组；付费或状态未知的内容即使有可用地址，也不标记为完整音频。接口未返回音频时保留图文信息。所有 JSON 响应有体积上限且不跟随重定向。

### 评论

请求移动接口 `/m-revision/common/track/queryTrackCommentsFirstPage`，按 `trackId` 校验归属，保留首屏顺序并排除楼中楼，最多 20 条。该接口的翻页行为未经验证，因此不构造分页，也不以普通评论接口补足。

## 八、微博

支持能力：视频 / 图片 / 文本 / 热评

### 链接范围

不同 URL 形态对应三套数据源，解析器先判定类型再选择链路：

| 链接形态 | 类型 | 数据源 |
| --- | --- | --- |
| `weibo.com/{uid}/{数字或短 ID}`、`weibo.cn/status/{id}` | 桌面详情 | `weibo.com/ajax/statuses/show` |
| `m.weibo.cn/detail/{id}` | 移动详情 | 页面内联状态 |
| `weibo.com/tv/show/{fid}`、`video.weibo.com/show?fid=…` | 视频组件 | `weibo.com/tv/api/component` |

### 桌面详情

请求 `weibo.com/ajax/statuses/show`，需要访客 Cookie、Referer 与 XSRF 相关请求头。媒体可能分布在混合媒体列表、图片信息表、普通图片列表、页面卡片与视频信息对象中，解析器按优先级扫描这些结构，分别识别图片、GIF 转码视频与普通视频。

### 移动详情

移动详情页不调用桌面接口，数据以 `var $render_data = [...][0]` 形式注入 HTML。媒体主要位于 `status` 下的图片列表与页面卡片；正文包含 HTML、表情图片与跳转标签，需清洗后展示。

### 视频组件

视频页通过 `weibo.com/tv/api/component` 的 `Component_Play_Playinfo` 获取播放信息，视频地址来自返回的 URL 集合。该接口提供的作者、标题与正文少于普通微博，缺失字段保持为空。

### 评论

取得微博状态 ID 后请求 `/ajax/statuses/buildComments`，复用访客 Cookie 与请求头。仅读取一次返回列表，在已取得的评论范围内按点赞数排序并截取；该排序不代表全帖评论排名。

## 九、小红书

支持能力：视频 / 图片 / 文本 / 热评

### 链接范围与取数流程

支持 `xhslink.com` / `xhslink.cn` 短链与 `xiaohongshu.com` 笔记页。短链必须先展开到正式笔记页；解析器需兼容移动端与 PC 端两套状态树。

```text
xhslink.com / xhslink.cn / xiaohongshu.com
  ↓
展开短链
  ↓
清理分享参数
  ↓
按移动端或 PC 端选择请求头
  ↓
读取 window.__INITIAL_STATE__
  ├─ 移动端：noteData.data.noteData
  └─ PC 端：note.noteDetailMap[*].note
```

移动端 `discovery/item` 分享链接只移除 `source` 与 `xhsshare` 参数，随后改写为对应的 PC `explore` 页面并保留其余查询参数。PC 链接中的访问参数可能影响页面是否返回完整状态，不做额外删除。

### 字段与媒体

- 视频笔记：优先从 `video.media.stream.h264` 的 `masterUrl` 选择最高质量 H.264 地址；缺少 H.264 时依次回退 H.265、AV1、H.266，并统一协议。PC `explore` 页面通常提供无水印播放地址。
- 图文笔记：从 `imageList` 的 `urlDefault`、`url`、`infoList` 中选择可用图片地址。

正文中的话题标签带有前端标记，解析时转换为可读文本。

### 评论

仅使用页面状态中已下发的评论，按点赞数排序；状态中没有评论时不额外请求评论接口，以避免触发风控。

## 十、闲鱼

支持能力：视频 / 图片 / 文本 / 热评

### 链接范围与取数流程

解析以稳定的 `itemId` 为核心，通过复现 H5 前端的详情接口取数，不依赖页面 HTML。`m.tb.cn` 为中转页，商品入口通常落在 `h5.m.goofish.com/item`，PC 链接为 `www.goofish.com/item`。

```text
m.tb.cn / h5.m.goofish.com / www.goofish.com
  ↓
从短链页提取真实商品 URL
  ↓
归一 itemId
  ↓
向 h5api.m.goofish.com 申请 _m_h5_tk
  ↓
按 H5 MTop 规则签名
  ↓
mtop.taobao.idle.awesome.detail
  ↓
从 itemDO / sellerDO / flowData 提取文本与媒体
```

实现要点：

1. `m.tb.cn` 常返回中转 HTML 而非 HTTP 重定向，需要从脚本中的 `var url = '...'` 提取真实商品页。
2. 原始商品 URL 作为上下文保留，详情请求只依赖 `itemId`，不受 `ut_sk`、`spm` 等易变分享参数影响。
3. 详情接口 `mtop.taobao.idle.awesome.detail` 是前端直接使用的数据源；令牌失效时重新申请 `_m_h5_tk` 并重新签名。

### 字段与媒体

- 标题、正文、价格与发布时间优先读取 `itemDO`。
- 作者优先读取 `flowData.floating` 中的未脱敏昵称，回退 `sellerDO`。
- 图片优先读取 `itemDO.imageInfos`，回退 `flowData.body.sections` 中的图片组件。
- 仅当详情 JSON 明确包含可用播放地址时才输出视频，否则按图集商品处理；同一商品的多个播放地址归入一个视频候选组。

### 评论

商品留言请求 `mtop.taobao.idle.comment.list`（版本 `5.0`），复用 MTop 匿名签名，分页参数按接口实际拼写使用 `roesPerPage`。保留接口顺序与打码昵称。公开列表通常只返回 3 条，即使总数非零或标记存在下一页，也可能无法取得更多留言。

## 十一、今日头条

支持能力：视频 / 图片 / 文本 / 热评

### 链接范围与取数流程

支持 PC 与移动端的文章页、视频页、微头条 `/w/{id}/`、短链 `m.toutiao.com/is/...`，以及通过 `message.meta.news.jumpUrl` 提取的 QQ 小程序卡片（常见落地页为 `m.toutiao.com/w/{id}/`）。所有入口统一归一到移动端页面，因为可复用的结构化状态与视频取数线索都位于移动端页面中。

```text
www.toutiao.com / m.toutiao.com / /w/ / 短链 / 小程序卡片
  ↓
提取内容类型与内容 ID
  ↓
归一到 m.toutiao.com/{article|video|w}/{id}/
  ↓
读取页面中百分号编码的 JSON
  ↓
按文章 / 微头条 / 视频分别提取
```

### 文章

数据位于 `<script>` 中百分号编码的 JSON，经 `urllib.parse.unquote` 解码后得到 `articleInfo`，包含标题、发布时间、来源、作者、正文 HTML 与封面。正文图片以 `<img src>` 形式嵌入 `articleInfo.content`，无需额外请求；正文文本通过去除标签和 HTML 反转义得到。

文章图片 URL 带签名与过期时间，适合解析后立即下载。缓存可用且启用富媒体时，解析器会有限次刷新页面，以新签名的地址补充候选；解析阶段不探测或下载图片本体。

### 微头条

微头条页面同样下发 `articleInfo`，`sessionConfig.articleType`、`pageType` 等字段通常标记为 `weitoutiao`。存在 `playAuthTokenV2` 时沿用视频链路，否则按图文处理。

### 视频

视频地址不在正文中，而是由 `playAuthTokenV2` 间接提供：

```text
articleInfo.playAuthTokenV2
  ↓
base64 JSON
  ↓
GetPlayInfoToken 查询串
  ↓
https://vod.bytedanceapi.com/?...
  ↓
Result.Data.PlayInfoList
```

`PlayInfoList` 中包含多档码率的 `MainPlayUrl`，按码率从高到低排列，作为同一视频的候选列表。

### 评论

请求移动前端使用的 `https://api.toutiaoapi.com/article/v4/tab_comments/`。`group_id` 优先取页面 `sessionConfig.groupId`，其次取 `articleInfo.gid` 等字段；校验响应资源身份后分页读取，每页 20 条、最多 5 页，保留平台排序。文章、视频与微头条共用该链路。

## 十二、小黑盒

支持能力：视频 / 图片 / 文本 / 热评

入口分为 BBS / link 帖子与游戏详情页两类：能提取帖子 `link_id` 时按帖子处理，否则按游戏的 `appid` 与 `game_type` 处理。

### BBS / link 帖子

帖子通过签名接口获取，不解析网页 HTML。

```text
小黑盒 BBS / link 分享
  ↓
提取 link_id
  ↓
生成签名参数
  ↓
获取设备 token
  ↓
/bbs/app/link/tree
  ↓
解析帖子文本与富媒体
```

帖子正文可能是富文本 JSON 数组，混合 HTML、纯文本、图片、视频与 GIF。逐项处理时，文本拼接为正文，图片进入图片候选，视频与 M3U8 作为视频候选，GIF 按资源形态判定为图片或视频。

接口返回的 `link_id`、`linkid` 或 `id` 可能是数字形式的内部别名，而非分享 URL 中的字符串 ID。解析器优先使用返回的 `share_url` 校验规范分享 ID：两者一致时接受该别名，无法对应或指向其他帖子时拒绝响应。

### 游戏详情

游戏分享链接只提取接口所需的 `appid` 与 `game_type`，不请求 Web 详情页。`appid` 为不透明字符串，不转换为整数。

```text
share_game_detail?appid=...
或 /app/topic/game/{game_type}/{appid}
  ↓
/game/get_game_detail/?appid=...
```

分享 ID 不是 Steam 数字 appid 且详情接口返回空结果时，先调用 `game_introduction` 获取对应的 Steam appid，再重新请求详情接口：

```text
game_introduction?steam_appid=...
  └─ 映射 Steam appid、简介、发行时间与厂商
        ↓
/game/get_game_detail/?appid={steam_appid}
  └─ 标题、评分、价格、标签、统计、奖项、截图与预览媒体
```

详情响应中的 `about_the_game`、`screenshots`、`image`、`user_num`、`game_award` 等字段直接用于构建文本与媒体候选，不依赖页面 HTML、Nuxt 注入数据或浏览器执行 JavaScript。`user_num.game_data` 提供当前在线、昨日峰值、全球销量排行与平均游戏时间等统计，接口返回 `-` 时按原值展示。

### 代理

`proxy.xiaoheihe_video` 仅控制直接解析小黑盒时的视频下载，帖子与游戏详情接口不使用代理。Steam 委托小黑盒解析时，详情请求由 `proxy.steam.parse` 控制。

### 评论

- 帖子评论：请求 `/bbs/app/link/tree`（`owner_only=0`），只取每楼首项作为主评论，排除楼中楼。
- PC 游戏评价：请求 `/bbs/app/link/game/comments`（`sort_type=4`，即“有用”排序）。

两条链路复用现有签名与请求代理。主机与手游评价尚未接入。

## 十三、雪球

支持能力：视频 / 图片 / 文本 / 热评

### 链接范围

以帖子 ID 为稳定入口。分享链接形如 `xueqiu.com/{user_id}/{status_id}`，查询串中常带有 `md5__1038` 等 WAF / CDN 参数，只取路径部分，无需展开重定向。

### 取数流程

`xueqiu.com` 的 HTML 页面与同域 `/statuses/*.json` 均受阿里云 WAF 的 JavaScript 挑战保护，普通 HTTP 客户端只能取得 `aliyun_waf` 挑战页；`api.xueqiu.com` 不受该挑战影响，但需要访客令牌。

```text
xueqiu.com/{user_id}/{status_id}
  ↓
提取 status_id（忽略查询串）
  ↓
xueqiu.com/service/csrf?api=/statuses/show.json
  └─ 下发 xq_a_token / u / s 等访客 Cookie
  ↓
api.xueqiu.com/statuses/show.json?id={status_id}
  ↓
校验返回 ID 与请求一致
  ↓
按普通帖 / 长文 / 转发帖提取文本与图片
```

`/service/csrf` 的响应体为 `{}`，其作用在于 `Set-Cookie` 下发的访客令牌；令牌写入会话后即可访问详情接口，无需登录。

详情接口以业务错误码表达错误，HTTP 状态为 400：

- `400016`：令牌缺失或过期，重新申请访客令牌后重试一次。
- `20210`：帖子不存在，不重试。
- 响应体包含 `aliyun_waf`：请求被挑战页拦截，通常由短时间内请求过密引起，按解析失败处理。

### 字段与媒体

- 标题读取 `title`，回退 `rawTitle`、`topic_title`；普通帖没有标题属于正常情况。
- 正文优先读取完整的 `text`，`description` 为截断版本，仅作兜底。
- 作者读取 `user.screen_name`，时间读取毫秒级 `created_at`。
- 图片来自三处：`text` 中的 `<img src>`、逗号分隔的 `pic`，以及 `image_info_list[].filename`（拼接为 `https://xqimg.imedao.com/{filename}`）。长文配图按正文顺序收集，再以其余两处补齐，按原图地址去重。
- `pic` 中的地址常带 `!thumb.jpg`、`!custom.jpg` 等缩略后缀，去除 `!` 之后的部分即为原图；原图排在首位，原始地址作为降级候选。
- `assets.imedao.com` 下的图片为站点表情等静态资源：表情按 `title` / `alt` 还原为 `[捂脸]` 形式的文字，其余 `<img>` 从正文中移除。

转发帖的 `retweeted_status` 是结构相同的帖子对象，外层 `text` 只包含转发语与评论链。解析结果在外层正文后追加“转发原帖：”段落，包含原帖作者、标题与正文；图片按外层在前、原帖在后合并去重。

`video_info` 与 `vod_info` 通常为空，仅当出现可播放 URL 时才输出视频。一条帖子最多包含一个视频，两个字段描述同一视频，收集到的多个地址归入同一候选组。HLS 地址添加 `m3u8:` 前缀并设置 `video_force_download`。现有验证尚未覆盖公开视频帖的完整下载与发送。

图片直链无需 Referer 或 Cookie 即可下载，请求时仍携带帖子页 Referer。

### 评论

复用匿名访客令牌与失效重试逻辑，先请求 `api.xueqiu.com/statuses/comments_excellent.json` 获取精选评论，再以 `comments.json` 的普通评论补足。响应中的评论总数不等于实际取得的数量。

## 十四、微信

支持能力：视频 / 图片 / 文本

公众号与视频号共用 `parsers.wechat` 输出模式，取数链路相互独立：公众号读取匿名文章页，视频号通过带令牌的预览接口获取视频。`wechat/parser.py` 负责链接分流与请求，`wechat/article.py` 负责公众号 HTML 字段提取。

### 公众号文章

支持 `mp.weixin.qq.com/s/{文章标识}` 短链接，以及同时带有 `__biz`、`mid`、`idx`、`sn` 参数的 `mp.weixin.qq.com/s?...` 链接。提链时还原 HTML 实体，并保留文章标识与查询参数的大小写。

```text
mp.weixin.qq.com/s/...
  ↓
匿名 GET 文章页（允许正常重定向）
  ↓
确认 HTTP 状态正常且最终页面不是验证码入口
  ↓
识别普通文章或 item_show_type=8 纯图集
  ├─ 普通文章：从 js_content 按段落提取正文与图片
  └─ 纯图集：从 picture_page_info_list 按顺序提取图片
  ↓
组合标题、公众号署名与发布日期
```

**普通文章**：正文与图片仅从 `js_content` 容器提取，忽略 `script`、`style`、`noscript`、`template`，不将正文外的头像与页面控件计入配图。HTML 实体由标准库 `HTMLParser` 解码，文本整理空白并保留段落换行。图片优先读取 `data-src`，缺失时读取 `src`，相对地址按文章链接补全；只保留不含用户信息的 HTTP / HTTPS 地址，去重后保持顺序，每张图片单独作为一个候选组。

**纯图集**：页面没有 `js_content`，以 `window.cgiDataNew` 顶层的 `item_show_type=8` 作为类型判定依据。解析器使用能识别引号、转义与嵌套括号的扫描逻辑读取 `picture_page_info_list`，只取每个顶层图片对象自身的 `cdn_url`，不在全页搜索同名字段，因此不会混入封面、`watermark_info`、`share_cover`、`original_info`、头像或页面资源。

**文本字段**：

- 标题依次读取 `activity-name`、`og:title` 与 `cgiDataNew.title`。
- 公众号名称优先读取 `js_name`，纯图集缺少该节点时读取 `cgiDataNew.nick_name`；署名读取 `author` 或 `og:article:author`，与公众号名称不同时组合为“公众号（署名）”。
- 正文写入 `desc`；没有正文文本时，以 `description`、`og:description` 或 `cgiDataNew.desc` 摘要补充，摘要中有限的 JavaScript 转义与 HTML 标签还原为纯文本。
- 日期优先读取 `publish_time` 节点，其次读取 `cgiDataNew.ori_create_time`；仍缺失时按 `ct`、`create_time`、`oriCreateTime` 顺序读取本页声明的秒级时间变量，按东八区转换，不使用关联文章对象中的同名字段。

**请求与判定**：页面与图片请求携带桌面 User-Agent 与 `https://mp.weixin.qq.com/` Referer，不携带腾讯元宝 Cookie。普通文章须存在 `js_content` 且至少提取到正文或图片；纯图集须满足 `item_show_type=8` 且图集中含有效图片；不能仅凭标题或摘要判定成功。HTTP 200 仍可能是验证页或错误页：最终 URL 为 `/mp/wappoc_appmsgcaptcha` 时直接报告需要验证；页面缺少有效正文与图片时，再按可见提示区分验证 / 访问频繁、删除 / 失效与普通空页面。正文中出现“验证码”等词语不会导致误判。

### 视频号

支持 `weixin.qq.com/sph/{短链标识}`，以及 `channels.weixin.qq.com/finder-preview/pages/sph?...`、`.../pages/feed?...` 预览长链。方案参考 [Zhalslar/astrbot_plugin_parser](https://github.com/Zhalslar/astrbot_plugin_parser) 的 `ShipinhaoParser`。

```text
视频号短链 / 预览长链
  ↓
读取查询参数 token 与 eid（也接受 exportId）
  ├─ 两者齐全 -> 直接请求预览接口
  └─ 缺少任一项 -> 腾讯元宝 get_parse_result
                    └─ playable_url -> token + eid
  ↓
finder-preview/api/feed/get_feed_info
  ↓
data.feedInfo + data.authorInfo
  ↓
视频直链、封面、作者、发布时间与互动统计
```

**第一步：换取令牌。** POST `https://yuanbao.tencent.com/api/weixin/get_parse_result`，携带元宝 Cookie、元宝站点的 Origin / Referer 与网页客户端请求头，请求体为 `type=video_channel_url`、原始分享链接 `url` 与 `scene=1`。从 `data.playable_url` 的查询参数中提取 `token` 与 `eid`，`eid` 缺失时使用 `data.wx_export_id`。两者均有效时才进入下一步；HTTP 401 表示元宝 Cookie 已失效。

**第二步：获取视频信息。** POST `https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info`，请求体为 `baseReq.generalToken=token` 与 `exportId=eid`，Referer 使用携带同一组令牌的预览页地址，查询参数经 URL 编码构建。元宝 Cookie 不发送给预览接口或媒体 CDN，两次 POST 均不跟随重定向。预览接口会以 HTTP 201 返回成功结果或结构化业务错误，因此任意 2xx 响应都读取 JSON，先检查顶层 `errCode`，再检查 `data.errMsg.type` 并保留其提示文本。

**字段映射**：视频地址按 `data.feedInfo.h264VideoInfo.videoUrl`、`h265VideoInfo.videoUrl`、`videoUrl` 顺序选择；`coverUrl` 仅作为 `video_cover_urls`；标题取 `feedInfo.description`，作者取 `authorInfo.nickname`，日期取 `feedInfo.createtime`，点赞、收藏、评论与转发计数写入 `desc`；编码信息中的 `duration` 由秒换算为 `timelength_ms`。没有可用视频地址时报错，不将封面作为视频或图集结果。视频设置 `video_force_download=True`，下载时携带 `https://channels.weixin.qq.com/` Referer 与对应 User-Agent。

### 配置与边界

`wechat.yuanbao_cookie` 仅用于视频号换取令牌，公众号无需填写；Cookie 失效后需手动更新。已带有效 `token` 与 `eid` 的预览长链可跳过元宝请求。微信解析不提供代理开关；本地调试可通过 `YUANBAO_COOKIE` 环境变量提供元宝 Cookie。

两个分支均使用现有 aiohttp 会话与解析并发限制，单次请求超时 30 秒。视频号视频与公众号图片均依赖可用缓存目录。当前不支持视频号图集、直播与公众号内嵌视频。

## 十五、知乎

支持能力：图片 / 文本 / 热评

### 链接范围

支持指定回答与专栏文章。纯问题页不解析，以免在问题下误选回答。

### 回答

请求 `https://api.zhihu.com/v4/answers/{answer_id}?include=content,author,question`，不携带登录 Cookie，校验回答 ID、问题 ID 与非空正文。正文按 HTML 结构提取文本，`br` 与块级标签转换为换行；图片优先使用懒加载原图属性，并保留知乎图片下载所需的 User-Agent 与 Referer。

### 专栏文章

请求 `https://zhuanlan.zhihu.com/api/articles/{article_id}?ws_qiangzhisafe=0`。请求前先访问 `https://www.zhihu.com/explore` 获取匿名访客 `d_c0`，再根据接口路径、`d_c0` 与 `x-zse-93` 生成 `x-zse-96` 签名，算法参考 [RSSHub](https://github.com/DIYgod/RSSHub) 的知乎专栏实现。访客 Cookie 只保存在解析器实例内并短期缓存，使用独立的 Cookie 会话，不与其他平台的登录态混用。响应包含 `content_need_truncated` 或 `force_login_when_click_read_more` 时直接失败，不将登录摘要作为完整正文。

### 限流与重试

知乎解析器的并发上限为 2。文章接口返回 403 或 429 时，使当前 `d_c0` 缓存失效，等待 1 秒后重新获取访客值并重试一次。匿名接口仍受上游风控与限流影响，不以无限重试规避。

### 评论

回答与专栏评论分别请求 `/api/v4/comment_v5/answers/{id}/root_comment` 与 `/api/v4/comment_v5/articles/{id}/root_comment`，参数为 `order=score`，复用匿名 `d_c0` 与 `x-zse-96` 签名。翻页时按响应给出的下一页游标重新签名，并限定在当前资源路径内。

## 十六、百度贴吧

支持能力：视频 / 图片 / 文本 / 热评

### 链接范围

支持 `tieba.baidu.com`、`tiebac.baidu.com` 与 `wapp.baidu.com` 上的以下帖子入口：`/p/{帖子ID}`、`/mo/q*/m?kz={帖子ID}`、`/f?kz={帖子ID}`、`/f?z={帖子ID}` 与 `/mo/q/movideo/page?thread_id={帖子ID}`，其中 `q*` 表示 `q` 及其路径参数。各端链接统一为 `https://tieba.baidu.com/p/{帖子ID}`，分享参数不参与帖子身份判定。

### 取数流程

通过贴吧客户端接口 `POST https://tieba.baidu.com/c/f/pb/page` 获取首楼内容，无需用户提供 Cookie。解析器校验主题 ID、首楼楼层与接口给出的首楼 ID，防止将回复作为正文；首楼中提取标题、作者、时间、正文、图片、动图与原生视频候选。

首楼包含转发卡片时，根据 `origin_thread_info.tid` 额外获取一次原帖首楼，将原帖说明与媒体附加到本帖结果中，不递归展开多层转发。结果仍使用本帖的标题、作者、时间与链接；原帖不可见时保留原帖链接及读取失败提示。

### 字段与媒体

- 图片按接口提供的原图与 CDN 地址组织候选并保留签名参数，覆盖普通图片、贴图表情、涂鸦与表情商店图片；普通内置表情还原为文字。
- 动图优先提取动态资源，发送时遵循统一的图片转换策略：ffmpeg 可用时转为首帧 PNG，不保证保留动画。
- 原生视频按清晰度保留播放候选，封面单独写入 `video_cover_urls`。
- 语音只保留收听提示，外部视频播放页只保留正文中的链接。

### 评论

使用同一接口并附加 `r=2`、`lz=0` 参数，每页 30 条，按接口返回的热门顺序分页读取本帖回复，最多 20 页。帖子未提供热门排序、接口退回普通正序时，仍输出有效回复并保留接口顺序。首楼与重复回复被排除；转发原帖不单独获取评论。

### 边界

正文范围限于首楼与直接转发的原帖，不抓取全部回帖或楼中楼。网页可能返回 403 或验证页面，这些页面不作为正文；删除、不可见或获取失败的帖子返回明确错误。链接形态参考 [TiebaLite](https://github.com/HuanCheng65/TiebaLite)，富文本字段参考 [open-tbm](https://github.com/n0099/open-tbm)，混排与转发样例参考 [aiotieba](https://github.com/lumina37/aiotieba)，最终以实际响应为准。

## 十七、NGA

支持能力：图片 / 文本 / 热评

### 链接范围

支持 `bbs.nga.cn`、`nga.178.com`、`ngabbs.com` 上带 `tid` 参数的 `read.php` 帖子链接，按帖子 ID 去重。正文与媒体范围限于公开帖子的楼主首帖。

### 取数流程

通过客户端接口 `POST https://ngabbs.com/app_api.php?__lib=post&__act=list` 获取帖子，表单携带 `tid`，请求头为 `X-User-Agent: NGA_skull/6.0.5(iPhone10,3;iOS 12.0.1)`，每次请求设置取值为当前秒级时间戳的 `guestJs` 访客 Cookie。无需用户配置登录 Cookie，也不预先交换 `guest_token`；网页端的访客限制不代表该客户端接口同样要求登录。

响应需校验 HTTP 状态、业务 `code`、帖子 ID 与首帖身份，不将回复或错误说明作为楼主正文。权限不足、帖子删除、接口拒绝或缺少有效首帖时返回明确错误。请求方式参考 [RSSHub NGA forum.ts](https://github.com/DIYgod/RSSHub/blob/75d43dd0868d3a169cb0bc94eabf354d26af5e9c/lib/routes/nga/forum.ts)。

### 字段与媒体

`nga/content.py` 统一处理混合的 HTML 与 BBCode：换行与段落整理为文本行，链接保留可读文字与地址，引用、折叠与列表保留基本层次，内置表情转换为文字标记；正文图片按出现顺序去重，相对附件地址结合 `attachPrefix` 补全。

首楼附件字段的实际拼写为 `attches`，图片项为 `type: "img"` 并提供 `attachurl`，仅补充正文中尚未出现的图片，保留原地址及查询参数。

NGA 通过 `image_tls_ciphers` 指定 `ECDHE+AESGCM:ECDHE+CHACHA20`，以应对已观察到的图片 CDN 握手拒绝。图片下载器在请求级别使用缓存的 SSLContext，保留默认 TLS 版本范围、证书与主机名校验，不修改共享会话。

### 评论

优先使用帖子接口返回的 `hot_post`，保留平台热门顺序；存在可用热门回复时不以普通回复补足。没有可用热门回复时，按 `result` 中的楼层顺序读取普通回复：复用正文请求的第一页，必要时通过同一接口的 `page` 表单参数翻页，包含第一页在内最多 20 页。回复按 `pid` 去重，校验帖子身份与返回页码，首帖不计入评论；保留作者、发布时间与 `vote_good` 赞数。

## 十八、虎扑

支持能力：视频 / 图片 / 文本 / 热评

### 链接范围与取数流程

支持 `bbs.hupu.com/{帖子编号}.html` 与 `m.hupu.com/bbs/{帖子编号}.html`，按帖子编号归一为电脑端链接。使用普通浏览器请求头读取 `__NEXT_DATA__`，校验 `props.pageProps.detail.thread` 的编号与可见状态，只处理主帖内容，不将推荐、头像或评论图片计入主帖媒体。

### 字段与媒体

正文 HTML 转换为文本与媒体候选；原生视频保留完整签名参数及独立封面。赛事战报组件只输出查看原帖的提示，不将组件 JSON 作为正文。缺少实际正文或媒体、状态异常与访问受限时返回明确错误。

### 评论

优先读取亮评 `detail.lights`，不足时以当前首屏的普通回复补足，不翻页。两类回复均保留作者、时间与可获得的赞数。

## 十九、豆瓣

支持能力：视频 / 图片 / 文本 / 热评

### 链接范围

按实体类型路由以下公开内容：影视、图书、音乐、游戏与舞台剧条目，长评与短评，小组话题、日记、广播、图书讨论、豆列、活动、照片、线上相册、预告片，以及豆瓣阅读的作品介绍与评论。`douban://` 应用链接、`/doubanapp/dispatch` 跳转与 `m.douban.com` 条目页会先转换为对应的网页链接。实体身份由响应元数据与内容节点核对；独立短评的规范链接保留原查询参数（如 `_spm_id`）。豆列与相册仅有限展开，并在正文中标明范围。

### 取数流程

- 小组话题、日记与广播使用移动端 Rexxar 公开接口，正文取完整的 `content` / `text`，图片结合 `photos` 等字段提取。
- 其余页面按实际正文区域提取；电影、图书与音乐优先使用展开后的完整简介，长评不以 JSON-LD 中的摘要替代全文。
- 豆瓣阅读单独处理作品介绍、阅读评论以及阅读器页面到作品介绍的映射，不提取付费正文。

权限限制、已删除、校验页与空壳页面不作为正常正文返回。

### 匿名会话与访客校验

每次解析使用独立的匿名会话，共享调用方的连接池并在结束时关闭，不读取用户登录 Cookie。网页端仅自动处理已验证的 `sec.douban.com/c` 固定 SHA-512 校验；计算量、响应大小、重试与跳转次数均有上限，规则变化时明确失败，不执行远端 JavaScript。图片访客校验同样只静态解析已知格式，验证取得真实图片后，将有效期十分钟的 Cookie 按精确 CDN 主机写回下载会话，不在通用媒体请求头中附带 Cookie。

### 评论

- 短评读取公开评论页，小组等实体读取对应评论接口；优先热门列表，缺少热门排序时接受普通评论，热门区与普通区按身份去重。
- 豆瓣阅读评论使用从当前页面脚本中发现的只读查询标识与匿名 CSRF 令牌，不固定历史查询哈希；作品短评、章节讨论与阅读评论回复分别核对归属。
- 纯图片评论保留文字与链接。预告片不借用作品短评作为自身评论。

## 二十、V2EX

支持能力：图片 / 文本 / 热评

### 取数流程

匿名请求 `GET https://www.v2ex.com/api/topics/show.json?id={主题ID}` 读取公开主题，核对返回的主题身份后提取标题、作者、发布时间与主帖正文。图片仅来自主帖内容，不包含头像或回复配图；不读取网页中的附言。请求不携带 Cookie 或 Token；登录限制、删除、限流与无效响应分别按失败处理，不以搜索摘要替代正文。所有响应上限为 8 MiB。

### 评论

匿名旧回复接口 `/api/replies/show.json?topic_id={主题ID}` 按楼层顺序返回评论，不含感谢数；实测分页参数无效，因此只请求一次。开启评论后，另读取主题网页前 5 页（每页最多 100 楼）的 JSON-LD 感谢统计，按回复 ID、作者与时间与接口结果对齐，在已取得范围内按感谢数降序优先展示，其余按楼层顺序补齐。网页不可用或未发现获感谢的回复时，使用接口的楼层顺序。

感谢数写入 `hot_comments.likes`。该排序仅限已读取的范围，不代表官方热评或全帖热度。

## 二十一、稀土掘金

支持能力：图片 / 文本 / 热评

### 取数流程

仅接入 `juejin.cn`（含旧域名 `juejin.im`）上 `/post/{文章ID}` 的公开文章。匿名 `POST https://api.juejin.cn/content_api/v1/article/detail` 读取 `article_info.web_html_content`，校验顶层与文章详情的身份及公开状态后，提取标题、作者与发布时间。HTML 清洗保留代码缩进、列表、表格与链接；仅将正文中的掘金图床图片交给下载器，并保留完整的签名查询参数。

### 评论

请求 `/interact_api/v1/comment/list`（`sort=1`，热门排序），最多 3 页；一级评论不足时，使用内嵌回复及最多 3 次 `/interact_api/v1/reply/list` 请求补齐。按文章与父评论身份校验并去重。

## 二十二、CSDN

支持能力：图片 / 文本 / 热评

### 取数流程

仅接入 `blog.csdn.net/{作者}/article/details/{文章ID}`。从公开 HTML 的 `articleContentId`、`content_views` 与文章元数据读取标题、正文、作者与时间，并核对规范链接。正文排除页面推荐、广告、隐藏内容与代码工具栏，保留代码缩进、段落与正文链接；图片仅取正文中的 CSDN 图床地址。

付费文章仅使用服务端匿名返回的公开预览，正文中注明限制并设置 `is_preview_only`，不请求解锁接口。

### 评论

请求 `POST /phoenix/web/v1/comment/list/{文章ID}?page={页}&size=10&fold=unfold`，最多 5 页，按平台默认顺序读取一级评论及其附带回复，核对文章身份后去重。

## 二十三、博客园

支持能力：图片 / 文本

仅接入 `www.cnblogs.com/{博客}/p/{文章ID}` 及其 `.html` 形式，重定向仅在同一文章的规范路径内有限跟随。正文限定为 `cnblogs_post_body`，标题取 `cb_post_title_url`，时间取页面可见的 `post-date`，作者取身份核验后的 `BlogPosting` 结构化数据。代码块保留缩进，图片仅取正文中的博客园图床地址。

匿名评论接口实测要求登录，返回的评论数也不能证明评论可读，因此未注册评论开关、不发起评论请求。新闻子站、登录内容与搜索摘要不接入。

## 二十四、Gitee

支持能力：文本 / 热评

### 仓库概况

通过官方匿名接口 `GET https://gitee.com/api/v5/repos/{owner}/{repo}` 读取公开仓库概况，展示简介、主要语言、Star / Fork 数、许可证与更新时间，不读取 README 或仓库代码。仓库迁移后，接口可能在 HTTP 200 中直接返回新仓库身份，以一致的 `full_name`、`namespace` 与规范地址为准；组织仓库的 `owner.login` 可能是管理员个人账号，不用于表示所有者。

### Issue 与评论

Issue 使用 `/repos/{owner}/{repo}/issues/{编号}` 及其 `/comments` 接口，编号为区分大小写的字母数字组合。核验议题归属后读取有限数量的普通评论，保留正文、作者、时间与回复关系。仓库概况不附加评论。

## 二十五、TikTok

支持能力：视频 / 图片 / 文本 / 热评

### 取数流程

TikTok 使用独立解析器，取数方式与抖音不同。作品数据主要位于 rehydration 脚本中，普通 HTTP 客户端容易取得防护页或不完整页面，因此优先使用系统 curl 获取页面。

```text
tiktok.com / vm.tiktok.com / vt.tiktok.com
  ↓
优先使用系统 curl 获取页面
  ↓
确认不是防护页
  ↓
读取 __UNIVERSAL_DATA_FOR_REHYDRATION__
  ↓
失败时读取 SIGI_STATE
  ↓
按新旧结构查找 itemStruct
```

新版页面的作品结构位于 `webapp.video-detail.itemInfo.itemStruct`；旧版页面可能使用 `SIGI_STATE`，或将作品结构嵌套在更深层对象中，需递归查找 `itemStruct`、`video`、`imagePost` 等字段。oEmbed 仅用于补充标题、作者等文本，媒体以页面脚本中的作品结构为准。

### 字段与媒体

- 视频：从 `playAddr`、`downloadAddr`、`PlayAddrStruct`、`bitrateInfo` 查找候选。
- 图集：从 `imagePostInfo` 或相近结构收集图片。

结构化脚本全部解析失败时，最后从 HTML 中直接查找 `playAddr`。

### 评论

请求 `/api/comment/list/`，使用作品 ID 与游标并沿用解析代理，按平台默认顺序展示。

## 二十六、YouTube

支持能力：视频 / 文本 / 热评

### 链接范围

支持常见的单视频链接：`youtube.com/watch?v=...`、`youtube.com/shorts/...`、`youtu.be/...`、`youtube.com/embed/...`、`youtube-nocookie.com/embed/...`、旧式 `youtube.com/v/...` 与 `youtube.com/e/...`，以及可解包为上述链接的 `attribution_link` 跳转。直播、`clip`、播放列表、频道、私有、年龄限制、地区限制或触发机器人校验的内容不保证可解析。

### 取数流程

观看页通常只返回不含媒体 URL 的自适应格式，因此分两步取数：先读取页面中的 `ytInitialPlayerResponse`、`INNERTUBE_API_KEY` 与访客信息，再调用内置 Android 播放接口获取带签名的格式 URL。播放器客户端版本固定为 `20.10.38`；该接口属于未公开协议，版本或返回结构变化时需要调整。

```text
watch / shorts / youtu.be / embed / nocookie embed / v / attribution_link
  ↓
规范为 youtube.com/watch?v={video_id}
  ↓
读取页面启动配置与初始播放信息
  ↓
/youtubei/v1/player?key={INNERTUBE_API_KEY}
  └─ Android 客户端播放响应
       ├─ muxed MP4 -> 直接作为视频候选
       └─ 视频 + 音频 -> dash:video_url||audio_url
```

### 字段与媒体

每条视频只保留一个候选组：同时存在视频与音频自适应流时，以兼容性最高的一组组成 `dash:` 候选，并追加 muxed MP4 作为回退。缓存可用时下载器优先处理 DASH 并调用 ffmpeg 合并；缓存不可用时剔除 DASH 候选，改用 muxed MP4 直链发送。

播放 URL 带有过期时间、签名与请求出口信息，不能长期缓存复用。解析与下载应使用相同的代理出口，`proxy.youtube` 同时控制页面、播放器接口与视频下载请求。消息协议端无法携带请求头或代理时，建议配置缓存目录并发送本地文件。

当前实现不处理 `signatureCipher`、播放器 JavaScript 中的 `s` / `n` 变换与 SABR 分段协议；遇到这些返回形态、登录要求、DRM 或机器人挑战时返回明确的解析失败信息。

### 评论

复用观看页 `ytInitialData` 中的热门或默认评论入口，以及 `ytcfg` 中的 WEB 客户端上下文，请求 `/youtubei/v1/next`。按根列表中的 `commentViewModel` 关联 `commentEntityPayload`，不跟进子回复游标。

## 二十七、Steam

支持能力：视频 / 图片 / 文本 / 热评

### 链接范围

Steam 游戏页以 `/app/{appid}` 为稳定标识，末尾的 slug（如 `/_/`）只是路由占位，不参与识别。以下两个 URL 解析为同一 appid：

```text
https://store.steampowered.com/app/3998900/_/
https://store.steampowered.com/app/3998900
```

### 取数流程

默认调用 Steam 商店的 `appdetails` 接口：

```text
store.steampowered.com/app/{appid}/...
  ↓
store.steampowered.com/api/appdetails/?appids={appid}&l=schinese&cc=cn
  ├─ 标题、简介、发行日期、开发商、发行商、类型与价格
  ├─ screenshots / header_image 图片
  └─ movies HLS、简介内嵌视频与封面
```

开启 `steam.use_xiaoheihe` 后，解析器将同一 appid 交由小黑盒游戏详情接口处理，结果保留原始 Steam 链接，并可额外获得小黑盒评分、在线人数、峰值、销量排行与平均游戏时间等统计；该模式不请求 Steam HTML 页面。

### 字段与媒体

每个预告片保留一个候选组，优先使用 `m3u8:` HLS 地址，失败时依次降级为 MP4 / WebM 候选。解析结果设置 `video_force_download`，预告片须缓存到本地后发送。截图、封面与预告片请求携带 Steam 商店页 Referer。

### 代理

代理配置位于 `proxy.steam`：`parse` 控制 Steam 或小黑盒详情接口，`image` 控制截图与封面下载，`video` 控制预告片下载。

### 评论

玩家评测请求官方 `/appreviews/{appid}`，优先取过去一年内的中文“有用”评测，不足时补充所有语言的近期评测，使用 Steam 解析代理。游戏详情委托小黑盒时，评测仍取自 Steam，内部的小黑盒实例不重复请求评价。

## 二十八、Twitter/X

支持能力：视频 / 图片 / 文本

### 链接范围与取数流程

以 tweet ID 为稳定入口，仅处理 `twitter.com` 与 `x.com`（含 `www.`、`mobile.` 子域）上包含 `/status/{tweet_id}` 的链接。

```text
twitter.com / x.com
  ↓
提取 tweet_id
  ↓
优先请求 FxTwitter
  ├─ 成功 -> 使用公开聚合结构
  ├─ 目标不可用 -> 不回退
  └─ 服务不可用 -> 回退 Guest GraphQL
```

FxTwitter 可直接提供推文、作者、引用推文与媒体结构，作为首选路径。回退条件从严：FxTwitter 明确返回目标不可用时，通常说明内容本身不可访问，不再改用官方接口；仅在网络错误、超时或服务端错误时进入 Guest GraphQL。

```text
guest/activate.json
  ↓
TweetResultByRestId
  ↓
递归遍历响应树
  ↓
定位匹配的 tweet 节点
```

GraphQL 响应嵌套较深且路径不固定，解析器递归查找带有 tweet legacy 信息的节点。正文优先取长文结构，普通推文读取 `full_text` 并按显示范围去除回复前缀。

### 字段与媒体

- 图片取原图地址。
- 视频与动图从 variants 中选择较高质量的 MP4，并设置 `video_force_download`。
- 引用推文作为正文补充保留。

没有图片和视频但有正文的推文同样视为解析成功。

## 二十九、Pixiv

支持能力：图片 / 文本 / 热评

### 链接范围与取数流程

以作品 ID 为稳定入口，支持 `artworks/{id}`、`i/{id}` 及带 `/en/` 前缀的链接。提链时保留原始匹配文本、按作品 ID 去重，以便在原消息中定位链接。

```text
pixiv.net/artworks/{illust_id} / pixiv.net/i/{illust_id}
  ↓
提取 illust_id
  ↓
/ajax/illust/{illust_id}
  └─ 标题、作者、标签、访问限制、AI 类型
  ↓
/ajax/illust/{illust_id}/pages?lang=zh
  └─ 每页的 original / regular / small 图片地址
```

单个作品依次请求元信息与分页接口；多个作品并发解析时受 `Config.PARSER_MAX_CONCURRENT` 限制。

### 字段与媒体

元信息接口的 `body` 提供 `illustTitle`、`userName`、`userId`、`tags`、`xRestrict`、`aiType` 与 `sl`。文本描述最多使用前 20 个标签；`xRestrict` 映射为 R-18 或 R-18G，`aiType=2` 标记为 AI 生成。

分页接口按页返回图片 URL，每页对应一个独立候选组，组内按原图、较低分辨率的顺序排列，下载器在原图失败后降级；不同页面不合并为同一候选组。

```text
image_urls = [
  [original_page_0, regular_or_small_page_0],
  [original_page_1, regular_or_small_page_1],
]
```

### 请求与访问限制

请求头包含桌面 User-Agent、Accept-Language 与指向当前作品页的 Referer。公开作品可不带 Cookie；登录或年龄限制作品需要配置包含 `PHPSESSID` 的完整 Cookie。接口返回 HTML 时，先识别 Cloudflare 防护页，再处理 HTTP 状态与 JSON，避免将拦截页误报为 JSON 错误。

`proxy.pixiv` 同时覆盖 Ajax 接口与图片下载，解析结果写入 `use_image_proxy` 与 `proxy_url`，图片下载继续携带作品页 Referer。图片必须缓存后发送，缓存不可用时标记为 `skip`。

### 评论

请求 `/ajax/illusts/comments/roots`，按 `offset` 分页，保留时间倒序，复用 Cookie 与代理；纯贴纸评论转换为 `[贴纸]`。

## 三十、GitHub

支持能力：文本

仅解析 `github.com/{owner}/{repo}` 公开仓库首页，接受 `.git` 后缀、尾斜杠、查询参数与锚点，仓库身份由所有者与仓库名确定。Issue、Pull Request、Release 与代码文件等子页不按仓库首页解析。

请求官方匿名接口 `GET https://api.github.com/repos/{owner}/{repo}`，提取仓库名、所有者、简介 `description`、主要语言、Star / Fork 数、许可证、归档状态与更新时间，写入 `MediaMetadata` 文本字段并保留仓库链接。不读取 README，不调用大模型生成摘要，也不请求图片、视频、评论或代码。

输出模式可选 `全部发送` 或 `仅文本`，`仅富媒体` 没有可发送内容。`proxy.github` 默认关闭，仅控制仓库 API 请求，开启后使用 `proxy.address`；解析结果不附加媒体下载代理字段。不配置 Cookie 或 Token，匿名接口受 GitHub 频率限制；限流、私有或不存在的仓库返回明确错误，不改为网页抓取或重复请求。

## 三十一、GitLab

支持能力：文本 / 热评

### 仓库概况

仅接入 `gitlab.com`，项目路径支持多级子组。匿名请求 `GET /api/v4/projects/{URL 编码的完整路径}?license=true` 获取概况，再以项目数字 ID 获取语言统计；语言统计失败时保留其余信息。响应缺少归档状态时不推断为未归档。项目迁移仅接受经过主机、接口与项目身份校验的有限次跳转（最多 2 次）。

### Issue 与讨论

Issue 接入 `/-/issues/{编号}` 以及 Issue 类型的 `/-/work_items/{编号}`，读取 `/api/v4/projects/{项目ID}/issues/{编号}`，核对项目 ID、议题 ID、公开状态与工作项类型。讨论通过匿名 `/api/graphql` 查询，按项目与议题的全局 ID 校验，最多读取 3 页讨论，每个讨论最多 20 条记录，排除系统事件并去重；保留普通顺序，不推断热门或点赞数。仓库概况不附加评论。

## 三十二、维护检查项

修改平台解析逻辑前，逐项确认：

- 链接最终指向哪种内容形态？
- 平台前端实际使用的是页面状态、接口 JSON 还是旧版内联数据？
- 短链与分享参数是否影响稳定 ID 的提取？
- 是否存在移动端与 PC 端两套结构？
- 视频、图集、音频、转发、引用、番剧与帖子是否需要分流处理？
- 媒体地址是否依赖 Referer、Cookie、User-Agent 或代理环境？
- 没有媒体地址时，原因是内容受限、纯文本内容还是解析失败？
- 兜底逻辑是否可能将防护页、错误页或 HTML / JSON 错误响应误判为媒体？
