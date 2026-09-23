# 平台解析备忘

## 一、总体思路

分享链接本身不存数据，只是入口。有用的信息在这些地方：

- 短链重定向后的稳定 URL
- 平台 Web 前端调的公开接口
- HTML 里注入的页面状态
- SSR / rehydration 脚本
- 旧版页面残留的内联 JSON 或媒体字段

核心流程：

```text
分享链接
  ↓
展开短链 / 清理分享参数
  ↓
识别内容 ID 和内容形态
  ↓
选择对应页面或接口
  ↓
读取结构化数据
  ↓
提取标题、作者、正文、时间、媒体线索和访问状态
  ↓
保留可用候选，交给后续流程
```

三条原则：

1. 先判内容形态再取字段。视频、图集、动态、番剧、帖子、游戏页的数据结构不一样。
2. 优先用平台前端已经在用的结构化数据。页面脚本和接口 JSON 比正则扫 HTML 稳定。
3. 保留上下文和候选。媒体地址、访问限制、来源页、请求环境都可能影响后续取内容。

## 二、B站

支持能力：视频 / 图片 / 文本 / 热评

看着都在 `bilibili.com` 下面，实际有几套不同的内容模型：UGC 视频、PGC 番剧、动态/opus、短链。第一步是展开入口、判断目标类型。

```text
b23.tv / bilibili.com / t.bilibili.com
  ↓
展开 b23 短链
  ↓
过滤直播入口
  ↓
判断 opus / UGC / PGC
  ↓
进入对应数据链
```

### UGC 视频

关键不是 BV/AV 本身，是播放分 P 对应的 `cid`。BV/AV 定位视频主体，`cid` 定位具体播放单元。

```text
BV/AV
  ↓
x/web-interface/view
  ↓
x/player/pagelist
  ↓
根据 p 参数选择 cid
  ↓
x/player/playurl
```

`view` 给主体信息（标题、作者、简介、发布时间）；`pagelist` 给分 P 列表和 `cid`；`playurl` 给播放结构。播放结构可能是普通直链，也可能是 DASH 音视频分离流。解析阶段只识别和保留，不做合并。

### PGC 番剧

番剧不能套 UGC 链路。入口可能是 `ep_id` 或 `season_id`，只有 season 时要先找到可播放 episode。

```text
ep_id / season_id
  ↓
season_id -> first ep_id
  ↓
番剧详情信息
  ↓
pgc/player/web/v2/playurl
  ↓
探测清晰度和播放结构
```

番剧容易碰到会员、试看、地区、付费限制，不能只看有没有媒体地址。页面和播放接口返回的访问状态、可看时长、完整时长也要一起保留，用来解释"为什么只拿到预览"或"为什么没有完整视频"。

### 动态 / opus

动态是个容器。本身有作者、正文、发布时间，但里面可能是图片，也可能引用或转发视频。

```text
opus_id
  ↓
动态接口
  ↓
解析 card / inner card / origin
  ├─ 图片动态 -> 提取 pictures
  ├─ 视频动态 -> 找到内嵌视频链接，再走视频链路
  └─ 转发视频 -> 合并外层动态和内层视频信息
```

转发动态要注意：只保留原视频会丢转发人的文字，只保留动态又丢视频主体。把外层动态和内层视频信息组合起来，让用户能看到"谁转发了什么"和"原视频是什么"。

视频封面使用统一字段 `video_cover_urls`：UGC 从 `x/web-interface/view` 的 `pic` 读取，PGC 优先取目标 episode 的 `cover`，缺失时回退 season 的 `cover`，再统一规范为 HTTPS 二维候选组。动态、opus 和转发内容重新组合视频元数据时必须同步透传该字段，避免「视频仅发送封面」模式退回截取首帧。

### Cookie 与评论

Cookie 是增强条件，不是前提。有 Cookie 时 Web 播放接口可能返回更完整的清晰度和可访问内容；没有时仍走无 Cookie 解析。UGC 播放用 `/x/player/wbi/playurl` + 动态 WBI 签名，DASH 请求用 `fnval=4048`，MP4 兼容回退用 `fnval=1`；不再用旧的 `/x/player/playurl`、HTML5 平台或 `fnval=0` FLV 回退。

评论和热评接口依赖 WBI 签名。先从导航接口拿签名材料，再按前端规则生成请求参数，不硬编码固定签名。

## 三、抖音

支持能力：视频 / 图片 / 文本 / 热评

分享链常见入口是短链，展开后稳定目标通常是 `/video/{id}`、`/note/{id}` 或 `/slides/{id}`，三种形态分开处理。

```text
v.douyin.com / douyin.com
  ↓
HEAD 展开，失败再 GET 展开
  ↓
判断 video、note 或 slides
  ↓
优先请求 douyin.com/aweme/v1/web/aweme/detail/
（open.douyin.com 来源上下文 + 最小参数）
  ├─ 成功 -> 使用目标作品详情
  └─ 失败 -> a_bogus 签名 + 有界 ttwid 会话
               ├─ 成功 -> 使用目标作品详情
               └─ 失败 -> slidesinfo 或 iesdouyin.com/share/{type}/{id}/
                         ↓
                    读取 window._ROUTER_DATA
```

优先使用 `open.douyin.com` 的来源上下文和作品 ID 等最小参数请求 Web 详情接口，不依赖 Cookie 或签名；响应必须再次校验目标作品 ID。该路径不可用时，回退到 `a_bogus` 签名和短生命周期 `ttwid` 会话，遇到会话失效、非 JSON 或目标不匹配时执行有界重试。两条详情路径均不可用时再回退 slidesinfo 或分享页。移动分享页相对轻量，通常保留 `window._ROUTER_DATA`，是重要的末级兜底数据源。

视频和图文结构不同：

- 视频从 `videoInfoRes` 取作品信息和播放地址。
- 图文笔记从 `noteDetailRes` 取图片列表。
- slides 从 `slidesInfoRes` 或 `slidesinfo` 接口取混排条目。

视频地址有一层转换：平台可能返回完整 URL，也可能只返回资源 ID，这时需要按播放接口格式补成可访问地址。图文图片结构可能多层嵌套，递归寻找常见 URL 字段，保留同一张图片的多个候选。slides 的 `images` 条目可能内嵌分段视频，必须先识别视频 URL 和封面 URL；只有确认是纯图片条目时才加入图片列表，避免把多分段视频误解析成图片。

### 评论

热评复用匿名 `ttwid` 与现有 `a_bogus` 签名，请求 `/aweme/v1/web/comment/list/`，按作品 ID 和游标读取平台默认顺序评论；图片与表情以文字标记展示。

## 四、快手

支持能力：视频 / 图片 / 文本

重点是"优先读结构化页面状态，兼容旧页面痕迹"。短链 `v.kuaishou.com` 先跳转到真实页面；部分域名或路径还会被改写到更容易取到状态的移动页面。

```text
v.kuaishou.com / kuaishou.com / gifshow.com / chenzhongtech.com
  ↓
短链展开
  ↓
必要时改写到 m.gifshow.com
  ↓
拉取页面 HTML
  ↓
优先读取 INIT_STATE / __APOLLO_STATE__
  ↓
失败或字段不完整时，用旧字段和 rawData 兜底
```

结构化状态里通常能找到作品主体 `photo`。图集完整列表通常在 `photo.ext_params.atlas.list`，补充字段可能在 `single` 或相近对象里。先判断视频还是图集：

- 视频：直接取作品视频地址。
- 图集：优先读完整图集列表，再组合 CDN、图片路径和相关资源，形成多张图片的候选地址。

`coverUrls` 是封面候选，不能当成整套图集。只有拿到完整图集列表时才算图集解析成功。

旧页面兼容很重要。历史链接不一定提供完整 SSR 状态，但页面里可能还有 `photoUrl`、`videoUrl`、`srcNoMark`、`window.rawData` 等字段，不如结构化状态稳定，但能覆盖旧链接和非标准分享页。用这些兜底字段时要避免把不完整结果误判为成功。

快手图集的图片地址经常不是完整 URL，而是 CDN 前缀加路径。先组合，再去重，保持候选顺序。

## 五、AcFun

支持能力：视频 / 图片 / 文本 / 热评。

支持 `acfun.cn`、`www.acfun.cn`、`m.acfun.cn` 上的视频 `/v/ac{ID}`、多 P `/v/ac{ID}_{P}`、文章/动态 `/a/ac{ID}`、番剧 `/bangumi/aa{ID}` 和指定集 `/bangumi/aa{ID}_36188_{itemId}`，以及移动分享 `/v/?ac={ID}`（含分 P）。消息中的无协议链接和中文标点相邻链接也会提取。所有入口规范化为 HTTPS 桌面链接，移除追踪参数，保留决定内容的分 P、分集信息；同一作品的不同分 P 和同一番剧的不同集分别解析。

移动分享入口可能把多 P 链接跳回第一 P，因此在请求前主动规范化。视频和文章共用 ac 编号，`/v/` 分享入口可能返回文章，按实际页面类型处理。番剧路径中间的 `36188` 为官方脚本使用的固定路由段，真正决定集数的是末尾 `itemId`。

番剧的 `?ac={序号}` 选择花絮，必须保留。按番剧状态的 `sidelights` 清单查找花絮，再读取对应投稿页面，并交叉校验清单与投稿的视频 ID；返回投稿规范链接。无效索引或身份不符直接报错，不能误发同页正片。

取数链路：

```text
规范链接
  ↓
window.videoInfo / window.articleInfo / window.bangumiData
  ↓
核对作品 ID、当前视频 ID 和指定分 P / itemId
  ├─ 视频 / 番剧 → currentVideoInfo.ksPlayJson / ksPlayJsonHevc
  └─ 文章 / 动态 → parts[].content
```

页面请求失败或缺少可解码状态时，没有选集信息的 ac 链接可以尝试 `GET /rest/pc-direct/article/info?articleId=...`。接口必须同时通过业务 `result=0` 和返回 `articleId` 校验。已解码但身份不符的页面直接报错，不通过回退掩盖错误；不存在的分 P、集数即使返回 HTTP 200，也不能取第一条视频代替。

页面身份有效但播放数据缺失时，调用 `GET /rest/pc-direct/play/playInfo/ksPlayJson`。携带当前 `videoId`、页面 `mkey`、作品 `resourceId` 和 `resourceType`（投稿为 2、番剧为 1），由接口校验视频归属。只补充播放数据，不覆盖页面原有的毫秒时长。

同一视频的流按 H.264 优先、清晰度降序排列，每档保留主地址与备用 CDN，随后保留 HEVC 候选。所有候选属于一个 `video_urls` 项，现有下载器负责失败回退；`m3u8Slice` 只是片头分片，不作为完整视频。HLS 地址加 `m3u8:` 前缀，由下载器逐项决定本地拼接，无需设置整条作品的强制下载开关。

文章正文按 HTML 结构读取：图片优先使用懒加载原图，同一 `<video>` 下的 `<source>` 合并为候选组，脚本与样式不进入正文。封面仅在正文没有图片和视频时补充。视频封面单独放入 `video_cover_urls`，不混入图集。

当前只处理链接指定的单个播放单元或文章正文，不批量抓取整季、全部分 P，不支持直播、个人空间、独立音频或应用私有协议。页面和接口均属于上游网页协议，登录、地区与内容访问限制仍可能导致解析失败。

### 评论

普通视频投稿评论通过 `/rest/pc-direct/comment/list` 的 `sourceType=3` 获取，先展示 `hotComments`，再补充 `rootComments`，按评论 ID 去重。番剧和动态尚未接入评论，不能将其页面 ID 当作普通投稿评论资源 ID。

## 六、微博

支持能力：视频 / 图片 / 文本 / 热评

复杂点在于不同 URL 形态背后是三套数据源，先判断 URL 类型再选链路。

```text
微博链接
  ↓
判断 URL 类型
  ├─ weibo.com       -> 桌面详情接口
  ├─ m.weibo.cn      -> 移动详情页内联状态
  └─ video.weibo.com -> 视频组件接口
```

### 桌面详情

走 `weibo.com/ajax/statuses/show`，需要访客 Cookie、Referer 和 XSRF 相关请求头。拿到 JSON 后，媒体可能散在多个结构中：混合媒体列表、图片信息表、普通图片列表、页面卡片信息、视频信息对象。按优先级扫描这些结构，把图片、GIF 视频化资源、普通视频分别识别出来。

### 移动详情

不走桌面接口，页面数据注入到 HTML 中：

```text
var $render_data = [...][0]
```

媒体主要在 `status` 下的图片列表和页面卡片中。正文可能包含 HTML、表情图片和跳转标签，需要清理后才适合展示。

### 视频组件页

`video.weibo.com/show` 和 `/tv/show` 走组件接口：

```text
weibo.com/tv/api/component
Component_Play_Playinfo
```

视频地址来自播放组件的 URL 集合。视频页能提供的作者、标题和正文比普通微博少，以组件返回为准，缺失时保持空值。

## 七、小红书

支持能力：视频 / 图片 / 文本 / 热评

要兼容移动端和 PC 端两套状态树。短链 `xhslink.com` / `xhslink.cn` 只是入口，必须先展开到正式笔记页。

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
  ├─ 移动端: noteData.data.noteData
  └─ PC 端: note.noteDetailMap[*].note
```

参数清理要小心。移动端 `discovery/item` 分享链接只去掉 `source` 和 `xhsshare` 参数；然后优先改写为对应的 PC `explore` 页面，完整保留其余查询参数。PC 链接中的访问参数可能影响页面能否返回完整状态，不能盲目删除。

拿到笔记数据后按类型处理：

- 视频笔记：优先从 `video.media.stream.h264` 的 `masterUrl` 中选最高质量 H.264 地址；没有 H.264 时回退 H.265、AV1 或 H.266，统一协议。PC `explore` 页面通常能提供无水印播放地址。
- 图文笔记：从 `imageList`、`urlDefault`、`url`、`infoList` 中选可用图片地址。

正文里的话题标签带前端标记，解析时清理成可读文本。评论信息如果已随页面状态下发，从状态树中收集并按点赞数排序；状态里没有就不额外请求高风险接口。

## 八、闲鱼

支持能力：视频 / 图片 / 文本 / 热评

关键不是直接抓页面 HTML，是先稳定拿到 `itemId`，再复现 H5 前端调的详情接口。`m.tb.cn` 只是中转页，真正的商品入口通常落到 `h5.m.goofish.com/item`；PC 链接落到 `www.goofish.com/item`。

```text
m.tb.cn / h5.m.goofish.com / www.goofish.com
  ↓
短链页提取真实商品 URL
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

三个稳定性要点：

1. 短链展开不能只看 HTTP 重定向。`m.tb.cn` 经常返回中转 HTML，需要从脚本里的 `var url = '...'` 提取真实商品页。
2. 优先保留分享页里原始商品 URL 作为上下文，但调详情接口时只依赖稳定的 `itemId`。兼容带参数分享链，又不把解析结果绑在 `ut_sk`、`spm` 之类易变参数上。
3. 详情主链走移动端 H5 的 `mtop.taobao.idle.awesome.detail`，这是前端直接用的数据源，比扫 CSR 页面 HTML 稳定；令牌失效时重新申请 `_m_h5_tk` 再签名重试。

字段提取：

- 标题、正文、价格、发布时间优先读 `itemDO`。
- 作者信息优先读 `flowData.floating` 里的未脱敏昵称，回退 `sellerDO`。
- 图片优先读 `itemDO.imageInfos`，回退 `flowData.body.sections` 里的图片组件。
- 视频不假设一定存在；只有详情 JSON 里明确出现可用播放 URL 时才作为视频返回，否则按图集商品处理。

### 平台约束

- 实测一个闲鱼商品最多挂一个视频。
- 如果详情 JSON 为同一商品暴露出多条播放类 URL，当前实现把它们视为同一视频的候选链路，不是多个独立视频项。
- 只有平台规则变了、一个商品允许挂多个视频时，才需要重新设计分组逻辑。

### 评论

商品留言使用 `mtop.taobao.idle.comment.list` 版本 `5.0`，复用现有 MTop 匿名签名，参数按平台使用 `roesPerPage`。保留接口顺序和打码昵称；公开列表通常只返回 3 条，即便总数非零或标记还有下一页，也可能拿不到更多留言。

## 九、今日头条

支持能力：视频 / 图片 / 文本 / 热评

稳定路径不是 PC 页壳，而是移动端 `m.toutiao.com` 页面。PC 文章页、视频页和微头条 `/w/...` 页面都可以作为入口，但可复用的结构化状态和视频取数线索都在移动端页里。QQ 小程序卡片通常通过 `message.meta.news.jumpUrl` 落到 `/w/<id>/` 微头条分享页。

```text
www.toutiao.com / m.toutiao.com / /w/ / 短链 / 小程序卡片
  ↓
提取内容类型和内容 ID
  ↓
归一到 m.toutiao.com/article|video|w/<id>/
  ↓
读取页面中的百分号编码 JSON
  ↓
按文章 / 微头条 / 视频三条路径提取
```

### 文章

关键数据不在可见 HTML，在 `<script>` 标签中的百分号编码 JSON。解码后通常得到 `articleInfo`，包含标题、发布时间、来源、作者信息、正文 HTML 和封面。

```text
m.toutiao.com/article/<id>/
  ↓
script 内 %7B...%7D
  ↓
urllib.parse.unquote
  ↓
state.articleInfo
```

正文图片直接嵌在 `articleInfo.content` 的 `<img src>` 中，不需要额外调图片接口，保留正文里的图片地址即可。正文文本通过去标签和 HTML 反转义得到简介。

### 微头条 / 小程序卡片

微头条分享页通常是 `m.toutiao.com/w/<id>/`，路径不是 `article/<id>`，但仍然在编码脚本里下发 `articleInfo`，只是 `sessionConfig.articleType`、`pageType` 等字段通常标成 `weitoutiao`。处理上更接近图文内容，没有 `playAuthTokenV2` 时直接按图文处理。

```text
message.meta.news.jumpUrl
  ↓
m.toutiao.com/w/<id>/
  ↓
script 内 %7B...%7D
  ↓
state.articleInfo
```

如果 `articleInfo` 里没有视频播放令牌就按普通图文微头条处理；如果未来某些 `/w/` 页面下发了 `playAuthTokenV2`，可以继续沿用视频页的播放信息链路。

### 视频

视频页同样先取 `articleInfo`，但 MP4 地址不直接放在正文里，通过 `playAuthTokenV2` 间接提供。

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

`playAuthTokenV2` 解码后得到 `GetPlayInfoToken`，请求 VOD 接口拿到 `PlayInfoList`，包含多档码率的 `MainPlayUrl`。按码率从高到低排序，作为同一视频的候选 URL 列表保留。

### 注意

- 短链 `m.toutiao.com/is/...` 先展开再抽取真实 `article|video` 和内容 ID。
- QQ 小程序卡片从 `message.meta.news.jumpUrl` 提取 URL，常见落地页是 `m.toutiao.com/w/<id>/`。
- 文章图片链接通常可直接下载，但 URL 带签名和过期时间，适合解析后立即下载，不适合长期缓存。
- 当前实现允许在解析阶段额外刷新页面几次，用新签名 URL 补充候选列表，但不会在 `parse()` 阶段直接探测或下载图片本体；媒体访问和缓存写入延后到下载层。
- 优先用移动端页面里的结构化状态，不依赖 PC 壳页面或浏览器执行 JS。

### 评论

评论使用移动前端的 `https://api.toutiaoapi.com/article/v4/tab_comments/`，优先根据页面 `sessionConfig.groupId`，再用 `articleInfo.gid` 等字段确定 `group_id`，校验响应资源身份后分页读取，保留平台排序。文章、视频与微头条的完整解析和评论分页均已在线验证。

## 十、小黑盒

支持能力：视频 / 图片 / 文本 / 热评

分两类：游戏详情页和 BBS/link 帖子。入口判断优先看能否提取帖子 `link_id`，否则按游戏 `appid/game_type` 处理。

### BBS/link 帖子

要走签名接口，不是直接扫网页。

```text
小黑盒 BBS/link 分享
  ↓
提取 link_id
  ↓
生成签名参数
  ↓
获取设备 token
  ↓
/bbs/app/link/tree
  ↓
解析 link 文本和富媒体
```

帖子正文可能是富文本 JSON 数组，混有 HTML、纯文本、图片、视频和 GIF。逐项处理：文本拼成正文，图片进入图片候选，视频和 M3U8 保留为视频线索，GIF 根据资源形态判断是图片还是视频。

接口返回的 `link_id`、`linkid` 或 `id` 不一定是分享 URL 中的字符串 ID，部分响应会返回数字内部别名。解析器优先用返回的 `share_url` 校验规范分享 ID；规范 ID 与请求一致时接受该数字别名，无法对应或明确指向其他帖子时拒绝响应，避免把别的帖子媒体归到当前链接。

### 游戏详情页

游戏分享链接只提取接口所需的 `appid` 与 `game_type`，不再请求 Web 详情页。`appid` 是不透明字符串，不能强制转换为整数：

```text
share_game_detail?appid=...
或 /app/topic/game/{game_type}/{appid}
  ↓
/game/get_game_detail/?appid=...
```

当游戏分享 ID 不是 Steam 数字 appid、详情接口返回空结果时，再调用 `game_introduction` 获取 Steam appid，然后重新请求游戏详情接口：

```text
game_introduction?steam_appid=...
  └─ 映射 Steam appid、简介、发行时间与厂商
        ↓
/game/get_game_detail/?appid={steam_appid}
  └─ 标题、评分、价格、标签、统计、奖项、截图和预览媒体
```

游戏详情响应中的 `about_the_game`、`screenshots`、`image`、`user_num`、`game_award` 等字段直接用于构建文本和媒体候选，不依赖页面 HTML、Nuxt 注入数据或浏览器执行 JavaScript。`user_num.game_data` 提供当前在线、昨日峰值在线、全球销量排行、平均游戏时间等统计；统计值由小黑盒接口实时决定，接口返回 `-` 时按接口原值展示。

`proxy.xiaoheihe_video` 仅控制直接解析小黑盒时的视频下载，帖子和游戏详情接口不使用代理。Steam 委托小黑盒时，详情请求由 `proxy.steam.parse` 控制。

### 评论

帖子评论复用 `/bbs/app/link/tree`，以 `owner_only=0` 获取楼层，只将每楼首项作为主评论，排除楼中楼混入。PC 游戏评价使用 `/bbs/app/link/game/comments` 的 `sort_type=4`（有用），两条路线复用现有签名及请求代理。主机、手游评价尚未接入。

## 十一、雪球

支持能力：视频 / 图片 / 文本 / 热评

稳定入口是帖子 ID。分享链形如 `xueqiu.com/{user_id}/{status_id}`，查询串里常挂 `md5__1038` 等 WAF/CDN 参数，只取路径即可，不需要展开重定向。

关键约束是取数入口的选择。`xueqiu.com` 的 HTML 页面和同域 `/statuses/*.json` 都在阿里云 WAF 的 JS 挑战后面，普通 HTTP 客户端只会拿到 `aliyun_waf` 挑战页；`api.xueqiu.com` 不受该挑战保护，但要求访客令牌。

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
校验返回 id 与请求一致
  ↓
按普通帖 / 长文 / 转发帖提取文本与图片
```

`/service/csrf` 只返回 `{}`，价值在 `Set-Cookie`。访客令牌写入会话 Cookie 后即可访问详情接口，无需登录账号。

详情接口的错误用业务码表达，HTTP 状态是 400 而不是 200：

- `400016` 表示令牌缺失或过期，重新申请访客令牌后可重试一次。
- `20210` 表示帖子不存在，不要重试。
- 返回体含 `aliyun_waf` 说明请求被挑战页拦截，通常是短时间内请求过密，按解析失败处理，不要当成 JSON 错误。

字段提取：

- 标题读 `title`，回退 `rawTitle`、`topic_title`。普通帖没有标题是正常状态，正文本身就是主体内容，不能因缺标题判定失败。
- 正文优先读完整的 `text`，`description` 是截断版本，只作兜底。
- 作者读 `user.screen_name`，时间读毫秒级 `created_at`。
- 图片有三个来源：`text` 里的 `<img src>`、`pic`（逗号分隔）和 `image_info_list[].filename`（拼 `https://xqimg.imedao.com/{filename}`）。长文的配图按阅读顺序内嵌在正文里，先按正文顺序收集，再用另两个来源补齐，按原图地址去重。
- `pic` 里的地址常带 `!thumb.jpg`、`!custom.jpg` 之类的缩略后缀。去掉 `!` 之后的部分得到原图，原图放首位、原始形态作为降级候选。
- `assets.imedao.com` 下的图片是站点表情等静态资源，不是帖子配图。表情标签要按 `title` / `alt` 还原成 `[捂脸]` 这类文字，其余 `<img>` 直接从正文移除。

转发帖的 `retweeted_status` 是一个同构的帖子对象。外层 `text` 只有转发语和评论链，原帖正文不在里面，两侧都要保留：外层正文之后追加 `转发原帖：` 段落，带上原帖作者、标题和正文，图片按外层在前、原帖在后合并去重。

视频不假设一定存在。`video_info` 和 `vod_info` 为空是常态，只有出现可播放 URL 时才作为视频返回。一条帖子最多挂一个视频，两个字段描述的是同一个视频，收集到的多条地址视为同一视频的候选链路，不拆成多个独立视频项。命中 HLS 时加 `m3u8:` 前缀并标记 `video_force_download`。

图片直链不需要 Referer 或 Cookie 即可下载，但仍按平台惯例携带帖子页 Referer。

图文路径已按普通帖、长文、多图长文、转发帖和表情帖实测通过；视频路径按上述字段防御性实现，暂未取到公开视频帖样本验证。

### 评论

评论复用匿名访客令牌与失效重试，先请求 `api.xueqiu.com/statuses/comments_excellent.json` 的精选，再用 `comments.json` 普通评论补足；按评论 ID 去重。评论总数字段不等于本次实际取得数量。

## 十二、微信

支持能力：公众号图片 / 公众号正文 / 视频号视频 / 视频号文本

公众号与视频号共用 `wechat` 平台开关，取数链路分开。公众号读取匿名文章页，视频号通过带令牌的预览接口取得视频。实现位于 `core/parser/platform/wechat/`：`parser.py` 负责链接分流和请求，`article.py` 只负责公众号 HTML 字段提取。

### 公众号文章

支持 `mp.weixin.qq.com/s/{文章标识}` 短链接，以及同时带有 `__biz`、`mid`、`idx`、`sn` 的 `mp.weixin.qq.com/s?...` 参数链接。提链时还原 HTML 实体，保留文章标识和查询参数的大小写。

```text
mp.weixin.qq.com/s/...
  ↓
匿名 GET 文章页，允许正常重定向
  ↓
确认 HTTP 状态与最终页面不是验证码入口
  ↓
识别普通文章或 item_show_type=8 纯图集
  ├─ 普通文章：从 js_content 按段落提取正文和 img 图片
  └─ 纯图集：从 picture_page_info_list 按顺序提取图片
  ↓
组合标题、公众号署名和发布日期
```

普通文章的正文和图片只从 `js_content` 容器提取，忽略 `script`、`style`、`noscript`、`template` 内容，不把正文外的公众号头像和页面控件收为配图。HTML 实体由标准库 `HTMLParser` 解码；文本整理空白并保留段落换行。图片优先读 `data-src`，缺失时读 `src`，相对地址按文章链接补全；只保留不含用户信息的 HTTP/HTTPS 地址，按完整地址去重并保持顺序，每张图片独立存为一个候选组。

纯图集页面没有 `js_content`，以 `window.cgiDataNew` 顶层的 `item_show_type=8` 为明确类型边界。解析器使用识别引号、转义和嵌套括号的扫描逻辑读取 `picture_page_info_list`，仅保留每个顶层图片对象自身的顶层 `cdn_url`；不全页搜索同名字段，因此不会混入文章封面、`watermark_info`、`share_cover`、`original_info`、头像或页面资源。图片去重后保持原顺序。

文本字段来源：

- 标题优先读 `activity-name`，缺失时依次读 `og:title` 和 `cgiDataNew.title`。
- 公众号名称优先读 `js_name`，纯图集缺失该节点时读 `cgiDataNew.nick_name`；署名读 `author` 或 `og:article:author` 元标签，两者不同则组合为“公众号（署名）”。
- 正文写入 `desc`；没有正文文本时，使用 `description`、`og:description` 或 `cgiDataNew.desc` 摘要补充。摘要中的有限 JavaScript 转义和 HTML 标签会还原为纯文本。
- 日期优先读 `publish_time` 节点，其次读 `cgiDataNew.ori_create_time`。仍缺失时按 `ct`、`create_time`、`oriCreateTime` 顺序读取本页明确声明的秒级 JS 时间变量，以东八区转换为日期；不使用关联文章对象中的同名发布时间字段。

公众号页面和图片请求携带桌面 User-Agent，Referer 为 `https://mp.weixin.qq.com/`，不携带配置中的腾讯元宝 Cookie。普通文章必须存在 `js_content` 且至少提取到正文或图片；纯图集必须同时满足 `item_show_type=8` 和图集列表含有效图片。两者都不能仅凭标题或摘要判成功。

HTTP 200 仍可能是验证码或错误页。最终 URL 为 `/mp/wappoc_appmsgcaptcha` 时直接报告需要验证；页面没有有效正文和图片时，再按可见提示区分验证/访问频繁、删除/失效及普通空页面。正常文章即使讨论“验证码”等词语，也不会因此被误判。当前只处理文章图文，不提取内嵌视频；验证、登录或访问限制不会被绕过。

### 视频号

支持 `weixin.qq.com/sph/{短链标识}`，以及 `channels.weixin.qq.com/finder-preview/pages/sph?...`、`.../pages/feed?...` 预览长链。视频号链路参考 [Zhalslar/astrbot_plugin_parser](https://github.com/Zhalslar/astrbot_plugin_parser) 的 `ShipinhaoParser` 方案。

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

第一步 POST `https://yuanbao.tencent.com/api/weixin/get_parse_result`，携带元宝 Cookie、元宝站点 Origin/Referer 和网页客户端请求头；请求体为 `type=video_channel_url`、原始分享链接 `url`、`scene=1`。从 `data.playable_url` 的查询参数提取 `token` 和 `eid`，其中 `eid` 缺失时使用 `data.wx_export_id`。必须同时取得有效的 `token` 和 `eid` 才进入下一步，HTTP 401 提示元宝 Cookie 失效。

第二步 POST `https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info`，请求体为 `baseReq.generalToken=token`、`exportId=eid`，Referer 使用携带同一组令牌的预览页地址，查询参数通过 URL 编码构建。元宝 Cookie 不传给预览接口或媒体 CDN；两个 POST 都不跟随重定向。预览接口会以 HTTP 201 返回成功媒体或结构化业务错误，因此任意 2xx 响应都会继续读取 JSON；响应先检查顶层 `errCode`，再检查 `data.errMsg.type` 并保留其用户可读提示。

视频地址按 `data.feedInfo.h264VideoInfo.videoUrl`、`h265VideoInfo.videoUrl`、`videoUrl` 顺序选择。`coverUrl` 只作为 `video_cover_urls` 封面；标题取 `feedInfo.description`，作者取 `authorInfo.nickname`，日期取 `feedInfo.createtime`，点赞/收藏/评论/转发的格式化计数写入 `desc`。编码信息中的 `duration` 按秒换算为 `timelength_ms`；没有可用视频地址则报错，不把封面当作视频或成功的图集结果。

预览接口给出的可播直链交给普通视频下载器，不引入抓包或解密流程。视频设置 `video_force_download=True`，下载携带 `https://channels.weixin.qq.com/` Referer 和对应 User-Agent；视频号视频及公众号图片均依赖可用缓存目录。

### 配置与边界

`parsers.wechat` 控制关闭、全部发送、仅文本或仅富媒体，默认全部发送。`wechat.yuanbao_cookie` 仅供视频号换取令牌，公众号无需填写；配置项默认为空，元宝 Cookie 失效后需要手动更新。已有有效 `token` 和 `eid` 的预览长链可跳过元宝请求；微信解析不提供代理开关。本地调试通过 `YUANBAO_COOKIE` 环境变量提供元宝 Cookie。

两个分支均使用现有 aiohttp 会话和解析并发限制，页面/API 单次请求超时为 30 秒。公众号是否返回可读正文、元宝是否接受分享链接以及视频号令牌的有效性都由上游决定；不支持视频号图集、直播或公众号内嵌视频，也不在解析失败时将受限内容伪装成媒体成功结果。

公众号图文已通过两篇普通文章和一篇纯图集真实样例匿名验证，并抽样确认图片可访问。视频号除覆盖两步请求、结果字段和异常分支的模拟验证外，还使用两个真实短链验证了元宝换票、HTTP 201 预览响应及媒体 CDN Range 访问；未验证 AstrBot 内的完整下载与发送流程。

## 十三、知乎

支持能力：图片 / 文本 / 热评

支持指定回答和专栏文章：回答使用 `https://api.zhihu.com/v4/answers/{answer_id}?include=content,author,question`，文章使用 `https://zhuanlan.zhihu.com/api/articles/{article_id}?ws_qiangzhisafe=0`。纯问题页不解析，避免在问题下误选回答。

回答请求不携带登录 Cookie，只校验返回的回答 ID、问题 ID 和非空正文。回答正文按 HTML 结构提取文本，`br` 与块级标签转换为换行，正文图片优先使用懒加载原图属性，并保留知乎图片下载所需的 User-Agent 和 Referer。

文章请求先访问 `https://www.zhihu.com/explore` 获取匿名访客 `d_c0`，再按当前接口路径、`d_c0` 和 `x-zse-93` 生成 `x-zse-96` 签名。签名算法参考 [RSSHub](https://github.com/DIYgod/RSSHub) 的知乎专栏实现。访客 Cookie 只保存在解析器实例内，短期缓存并使用独立 Cookie 会话，避免把其他平台的登录态带给知乎。文章返回 `content_need_truncated` 或 `force_login_when_click_read_more` 时直接失败，不把登录摘要当作完整正文。

知乎解析器将自身并发限制为最多 2 个请求。文章接口遇到 403 或 429 时会使当前 `d_c0` 缓存失效，并在 1 秒后最多重新取访客值重试一次；匿名接口仍受知乎上游风控和限流影响，不能通过无限重试规避。接口字段、签名规则或匿名访问策略变化时需要重新验证。

### 评论

回答和专栏评论分别请求 `/api/v4/comment_v5/answers/{id}/root_comment` 与 `/api/v4/comment_v5/articles/{id}/root_comment`，使用 `order=score`，复用匿名 `d_c0` 和 `x-zse-96`。根据响应下一页游标重新签名，限定当前资源路径，正文 HTML 和图片转换为评论文本。

## 十四、百度贴吧

支持能力：视频 / 图片 / 文本 / 热评

支持 `tieba.baidu.com`、`tiebac.baidu.com` 和 `wapp.baidu.com` 上的帖子入口：`/p/{帖子ID}`、`/mo/q*/m?kz={帖子ID}`、`/f?kz={帖子ID}`、`/f?z={帖子ID}` 和 `/mo/q/movideo/page?thread_id={帖子ID}`。移动入口中的 `q*` 表示 `q` 及其路径参数；不同端的链接统一为 `https://tieba.baidu.com/p/{帖子ID}`，分享参数不参与帖子身份判断。

帖子解析通过贴吧客户端 `POST https://tieba.baidu.com/c/f/pb/page` 接口获取首楼内容，不需要用户提供 Cookie。校验主题 ID、首楼楼层及接口提供的首楼 ID，不能把回复当作正文；从首楼提取标题、作者、时间、正文、图片、动图和原生视频候选。语音只保留收听提示，外部视频播放页只保留正文链接。

图片按接口提供的原图与 CDN 地址组织候选，保留签名参数，覆盖普通图片、贴图表情、涂鸦和表情商店图片；普通内置表情还原为文字。动图优先提取动态资源，但发送遵循统一图片转换策略：ffmpeg 可用时转为首帧 PNG，不能据此保证保留动画。原生视频按清晰度保留播放候选，封面单独写入 `video_cover_urls`，不混入正文图片。

首楼包含转发卡片时，根据 `origin_thread_info.tid` 额外获取一次原帖首楼，把原帖说明与媒体附加到本帖结果，不递归展开多层转发。结果仍保留本帖的标题、作者、时间与链接；原帖不可见时保留原帖链接及读取失败提示，不影响本帖解析。

热评使用同一接口的 `r=2`、`lz=0` 参数，按平台热门顺序分页读取本帖回复。优先请求 `sort_type=2` 的热门响应；帖子未提供热门排序、接口退回普通正序时仍展示有效回复，保留接口顺序且最多读取 20 页。首楼和重复回复会被排除，回复媒体转换为文字标记，热评请求失败时保留已获取内容与帖子正文。转发原帖不另行抓取热评。

热评复用 `message.hot_comments.count` 全局条数，默认 `0`；`message.hot_comments.tieba` 平台开关默认开启，只有条数大于 `0` 且贴吧输出模式包含文本时才请求热评。正文范围仍限于首楼与直接转发原帖，不批量抓取全部回帖或楼中楼。

网页可能返回 403 或验证页面，不能把这些页面当作帖子正文；客户端接口也可能受到平台访问控制，删除、不可见或获取失败的帖子返回明确错误。

链接形态参考 [TiebaLite](https://github.com/HuanCheng65/TiebaLite)，富文本字段参考 [open-tbm](https://github.com/n0099/open-tbm)，混排与转发验证样例参考 [aiotieba](https://github.com/lumina37/aiotieba)；最终字段与接口行为以实际响应核验。

## 十五、虎扑

支持能力：视频 / 图片 / 文本 / 热评

支持 `bbs.hupu.com/{帖子编号}.html` 与 `m.hupu.com/bbs/{帖子编号}.html`，按帖子编号归一到电脑端。使用普通浏览器请求头读取 `__NEXT_DATA__`，校验 `props.pageProps.detail.thread` 的编号和可见状态，只处理主帖内容，不扫描推荐、头像或评论图片作为主帖媒体。

正文 HTML 转为文本和媒体候选；原生视频保留完整签名参数及独立封面。赛事战报组件只输出原帖查看提示，不把组件 JSON 当正文。缺少实际正文或媒体、状态异常和访问受限时明确报错。

热评优先读取 `detail.lights`，不足时使用当前首屏的普通回复，按评论编号去重并受全局条数限制；不翻页抓取整楼。两种回复均保留作者、时间和可获得的赞数，纯媒体回复转换为文字标记。

虎扑注册独立输出模式与热评开关，复用全局评论条数和文本输出条件，无需 Cookie 或代理配置。

## 十六、豆瓣

支持能力：视频 / 图片 / 文本 / 热评

按实体类型路由公开影视、图书、音乐、游戏、舞台剧条目和长短评，小组话题、日记、广播、图书讨论、豆列、活动、照片、线上相册、预告片以及豆瓣阅读作品介绍与评论。实体身份由响应元数据和内容节点核对；独立短评保留必要的 `_spm_id` 分享参数，列表与相册仅有限展开并在正文标明范围。

小组、日记和广播采用移动 Rexxar 公开接口，正文使用完整 `content` / `text`，图片结合 `photos` 等字段提取；其余页面按实际正文区域提取，电影、图书和音乐优先展开后的完整简介，长评不使用 JSON-LD 中的短摘要替代全文。页面权限限制、删除、校验和空壳不会作为正常正文返回。

每次解析使用独立匿名会话，共用调用方连接池并在结束时关闭，不读取用户登录 Cookie。网页仅自动处理已验证的 `sec.douban.com/c` 固定 SHA-512 校验；计算量、响应大小、重试和跳转均有上限，规则变化时明确失败，不执行远端 JavaScript。图片访客校验同样只静态解析已知格式，验证真实图片后将十分钟有效的 Cookie 按精确 CDN 主机写回下载会话，不在通用媒体请求头里附带 Cookie。

短评读取公开评论页，小组等实体读取对应评论接口，热门列表优先，缺少热门排序时接受普通评论。热门区与普通区按身份去重，评论请求有界，接口失败保留正文和已取得的评论；未知赞数省略，纯图片评论保留文字和链接。预告片不借用作品短评冒充自身评论。

豆瓣阅读独立处理作品介绍、阅读评论及阅读器到作品介绍的映射，不提取付费正文。阅读评论请求沿当前页面脚本发现只读查询标识和匿名 CSRF 令牌，不固定历史查询哈希；作品短评、章节讨论和阅读评论自身回复分别核对归属，评论失败保留公开正文。

豆瓣注册独立输出模式与热评开关，复用全局评论条数和文本输出条件，不新增 Cookie 配置、第三方依赖或代理设置。

## 十七、TikTok

支持能力：视频 / 图片 / 文本 / 热评

独立解析器模块，取数路线与抖音完全不同。作品页数据主要在 rehydration 脚本里，普通 HTTP 客户端容易拿到防护页或不完整页面。

```text
tiktok.com / vm.tiktok.com / vt.tiktok.com
  ↓
优先用系统 curl 拉取页面
  ↓
确认不是防护页
  ↓
读取 __UNIVERSAL_DATA_FOR_REHYDRATION__
  ↓
失败时读取 SIGI_STATE
  ↓
按新旧结构寻找 itemStruct
```

主路径是 `__UNIVERSAL_DATA_FOR_REHYDRATION__`，新版页面的作品结构在 `webapp.video-detail.itemInfo.itemStruct`。旧页面可能用 `SIGI_STATE`，或者把作品结构散在更深层对象里，需要递归搜索 `itemStruct`、`video`、`imagePost` 等线索。

oEmbed 只适合补充标题、作者等文本，媒体资源以页面脚本中的作品结构为主。

视频和图集区分：

- 视频从 `playAddr`、`downloadAddr`、`PlayAddrStruct`、`bitrateInfo` 找候选。
- 图集从 `imagePostInfo` 或相近结构收集图片。

结构化脚本全部失败时，最后从 HTML 里直接查找 `playAddr` 兜底。

### 评论

评论调用 `/api/comment/list/`，使用作品 ID 和游标，沿用解析代理；按平台默认顺序展示，不推断为全站点赞排名。评论风控或空响应不影响已经解析成功的作品。

## 十八、YouTube

支持能力：视频 / 文本 / 热评

当前支持常见的单视频链接：`youtube.com/watch?v=...`、`youtube.com/shorts/...`、`youtu.be/...`、`youtube.com/embed/...`、`youtube-nocookie.com/embed/...`、旧式 `youtube.com/v/...` / `youtube.com/e/...`，以及可解包到上述链接的 `attribution_link` 分享跳转。直播、`clip`、播放列表、频道、私有、年龄限制、地区限制或触发机器人校验的内容不保证可解析。

YouTube 页面本身经常只返回没有媒体 URL 的自适应格式，因此解析器分两步取数：先读取页面中的 `ytInitialPlayerResponse`、`INNERTUBE_API_KEY` 和访客信息，再调用 YouTube 内置 Android 播放接口获取带签名的格式 URL。播放器客户端版本目前固定为 `20.10.38`，该接口属于未公开协议，版本或返回结构变化时可能需要调整。

```text
watch / shorts / youtu.be / embed / nocookie embed / v / attribution_link
  ↓
规范为 youtube.com/watch?v={video_id}
  ↓
读取页面启动配置和初始播放信息
  ↓
/youtubei/v1/player?key={INNERTUBE_API_KEY}
  └─ Android 客户端播放响应
       ├─ muxed MP4 -> 直接视频候选
       └─ 视频 + 音频 -> dash:video_url||audio_url
```

每条媒体只保留一个候选组。若播放器同时返回视频和音频自适应流，最高兼容性的视频和音频会组成 `dash:` 候选，后面追加 muxed MP4 作为回退。缓存目录可用时下载器优先走 DASH 并调用现有 ffmpeg 合并；缓存目录不可用时会剔除 DASH 候选，改用普通 muxed MP4 直发。

YouTube 播放 URL 带有过期时间、签名和请求出口信息，不能长期缓存复用。解析与下载应保持相同的代理出口，部分消息协议端无法携带请求头或代理时，建议配置缓存目录后发送本地文件。`proxy.youtube` 同时控制页面、播放器接口和视频下载请求。

当前实现不解析 `signatureCipher`、播放器 JavaScript 中的 `s`/`n` 变换或 SABR 分段协议；遇到这些返回形态、登录要求、DRM 或机器人挑战时会返回可见的解析失败信息。

### 评论

评论复用观看页 `ytInitialData` 的热门或默认入口，以及 `ytcfg` 中的 WEB 客户端上下文，请求 `/youtubei/v1/next`。按根列表的 `commentViewModel` 关联 `commentEntityPayload`，不追踪子回复游标；缩写赞数和相对时间保留平台原文。

## 十九、Steam

支持能力：视频 / 图片 / 文本 / 热评

Steam 游戏页 URL 的稳定标识是 `/app/{appid}`。末尾的 slug（例如 `/_/`）只是页面路由占位，不参与游戏识别；以下两种 URL 会解析为同一个 appid：

```text
https://store.steampowered.com/app/3998900/_/
https://store.steampowered.com/app/3998900
```

默认调用 Steam 商店的 `api/appdetails` 接口：

```text
store.steampowered.com/app/{appid}/...
  ↓
store.steampowered.com/api/appdetails/?appids={appid}&l=schinese&cc=cn
  ├─ 标题、简介、发行日期、开发商、发行商、类型和价格
  ├─ screenshots / header_image 图片
  └─ movies HLS、简介内嵌视频和封面
```

开启 `steam.use_xiaoheihe` 后，Steam 解析器会把相同 appid 转交给小黑盒完整游戏接口；结果仍保留原始 Steam 链接，因此可以获得小黑盒评分、在线人数、峰值、销量排行和平均游戏时间等额外统计。该选项不请求 Steam HTML 页面。

Steam 代理配置位于 `proxy.steam`：`parse` 控制 Steam 或小黑盒详情接口，`image` 控制截图/封面下载，`video` 控制预告片下载。

每个 Steam 预告片保留一个候选组，优先使用 `m3u8:` HLS 地址，失败时按 MP4/WebM 候选降级；解析结果会标记 `video_force_download`，因此预告片必须进入本地缓存后发送。截图、封面和预告片继续携带 Steam 商店页 Referer。

### 评论

玩家评测调用官方 `/appreviews/{appid}`，优先过去一年内的中文有用评测，不足时补充所有语言的近期评测；使用 Steam 解析代理。即使游戏详情委托小黑盒，评测仍取自 Steam，内部小黑盒实例不重复请求评价。

## 二十、Twitter/X

支持能力：视频 / 图片 / 文本

稳定入口是 tweet ID，只处理包含 `/status/{tweet_id}` 的链接。

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

FxTwitter 能直接给推文、作者、引用推文和媒体结构，是优先路径。回退条件要收紧：FxTwitter 明确返回目标不可用时，通常说明内容本身不可访问，不应该再用官方接口绕；只有网络错误、超时或服务端错误才进入 Guest GraphQL。

Guest GraphQL 链路：

```text
guest/activate.json
  ↓
TweetResultByRestId
  ↓
递归遍历响应树
  ↓
寻找匹配 tweet 节点
```

Twitter 响应嵌套很深，不能假设固定路径永远在。递归找带有 tweet legacy 信息的节点。正文优先取长文结构，普通文本看 `full_text`，按显示范围裁掉回复前缀。

媒体提取：

- 图片取原图地址。
- 视频和动图从 variants 中选质量较高的 MP4。
- 引用推文作为正文补充，不丢弃。

一条推文没有图片和视频但有正文，仍然是可解析内容。

## 二十一、Pixiv

支持能力：图片 / 文本 / 热评

稳定入口是作品 ID。支持 `artworks/{id}`、`i/{id}` 以及带 `/en/` 前缀的链接；提链时保留原始匹配文本，按作品 ID 去重，避免规范化链接后无法在原消息中定位。

```text
pixiv.net/artworks/{illust_id} / pixiv.net/i/{illust_id}
  ↓
提取 illust_id
  ↓
/ajax/illust/{illust_id}
  └─ 标题、作者、标签、访问限制、AI 类型
  ↓
/ajax/illust/{illust_id}/pages?lang=zh
  └─ 每页 original / regular / small 图片地址
```

元信息接口的 `body` 提供 `illustTitle`、`userName`、`userId`、`tags`、`xRestrict`、`aiType` 和 `sl`。标签最多取前 20 个用于文本描述；`xRestrict` 映射为 R-18 或 R-18G，`aiType=2` 标记为 AI 生成。

分页接口按作品页返回图片 URL。每一页必须保持为一个独立候选组：

```text
image_urls = [
  [original_page_0, regular_or_small_page_0],
  [original_page_1, regular_or_small_page_1],
]
```

下载管理器按组内顺序尝试，原图失败后降级较低分辨率，不同页面不能合并成一个候选组。

请求头需要桌面 User-Agent、Accept-Language 和指向当前作品页的 Referer。公开作品可不带 Cookie；登录或年龄限制作品需要配置包含 `PHPSESSID` 的完整 Cookie。API 返回 HTML 时要先识别 Cloudflare 防护页，再处理 HTTP 状态和 JSON，避免把拦截页面误报为普通 JSON 错误。

代理开关同时覆盖 Web Ajax API 和 `i.pximg.net` 图片下载。解析结果写入 `use_image_proxy` 与 `proxy_url`，图片下载继续携带作品页 Referer。图片只能缓存后发送，缓存目录不可用时标记为 `skip`。

单个作品依次请求元信息和分页接口；多个作品并发解析时由 `Config.PARSER_MAX_CONCURRENT` 限制，避免大量链接形成无界请求突发。

### 评论

作品评论使用 `/ajax/illusts/comments/roots`，按 `offset` 分页并保留时间倒序；复用原有 Cookie 和代理。纯贴纸评论转换为 `[贴纸]`，缺少点赞字段时不伪造零赞。

## 二十二、GitHub

支持能力：文本

仅解析 `github.com/{owner}/{repo}` 公开仓库首页，接受 `.git` 后缀、尾斜杠、查询参数和锚点；仓库身份由所有者和仓库名确定。Issue、Pull Request、Release、代码文件等子页不作为仓库首页解析。

请求官方匿名接口 `GET https://api.github.com/repos/{owner}/{repo}`，提取仓库名、所有者、简短 `description`、主要语言、Star/Fork 数、许可证、归档状态和更新时间，转换为已有 `MediaMetadata` 文本字段并保留仓库链接。不读取 README、不调用大模型总结，也不请求图片、视频、评论或仓库代码。

`parsers.github` 默认 `全部发送`，也可选择 `仅文本`；`仅富媒体` 没有可发送内容。文本可继续由现有消息链路渲染为图片。`proxy.github` 默认关闭，仅控制仓库 API 请求，开启后使用 `proxy.address`，解析结果不附加媒体下载代理字段。

不配置登录 Cookie 或 Token；匿名接口受到 GitHub 频率限制，限流、私有或不存在的仓库会明确失败，不切换为网页抓取或重复请求规避限制。

## 二十三、NGA

当前未提供解析器。

NGA 已关闭访客浏览：`read.php?tid=...` 直接返回 `ERROR:1 未登录`，站点根路径返回 `ERROR:15 访客不能直接访问`。挑战页里的 `guestJs` Cookie 每次请求都会重新生成，回填后仍被拒绝；`app_api.php` 的 `post/list` 返回 `code:12 未登录`，随响应下发的 `guest_token` 无法换取内容，带 `access_token` 时改报 `code:5 签名错误`。`__output=8`、`__output=11`、`lite=js` 等输出形式只是换了错误载体，同样是 403。

也就是说取数必须依赖 `ngaPassportUid` + `ngaPassportCid` 登录 Cookie。若之后决定支持，需要先引入用户提供 Cookie 的配置项，并注意页面是 GBK/GB18030 编码。

## 二十四、维护原则

改平台解析逻辑前，过一遍这些问题：

- 这个链接最终指向哪种内容形态？
- 平台前端真正用的是页面状态、接口 JSON，还是旧版内联数据？
- 短链和分享参数是否影响稳定 ID 的提取？
- 是否有移动端和 PC 端两套结构？
- 视频、图集、转发、引用、番剧、帖子是否需要分流？
- 媒体地址是否依赖 Referer、Cookie、User-Agent 或代理环境？
- 没有媒体地址时，是受限内容、纯文本内容，还是解析失败？
- 当前兜底是否会误把防护页、错误页、HTML/JSON 错误响应当成媒体？
