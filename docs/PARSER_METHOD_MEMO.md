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

### Cookie 与评论

Cookie 是增强条件，不是前提。有 Cookie 时 Web 播放接口可能返回更完整的清晰度和可访问内容；没有时仍走无 Cookie 解析。UGC 播放用 `/x/player/wbi/playurl` + 动态 WBI 签名，DASH 请求用 `fnval=4048`，MP4 兼容回退用 `fnval=1`；不再用旧的 `/x/player/playurl`、HTML5 平台或 `fnval=0` FLV 回退。

评论和热评接口依赖 WBI 签名。先从导航接口拿签名材料，再按前端规则生成请求参数，不硬编码固定签名。

## 三、抖音

支持能力：视频 / 图片 / 文本

分享链常见入口是短链，展开后稳定目标通常是 `/video/{id}`、`/note/{id}` 或 `/slides/{id}`，三种形态分开处理。

```text
v.douyin.com / douyin.com
  ↓
HEAD 展开，失败再 GET 展开
  ↓
判断 video、note 或 slides
  ↓
优先请求 douyin.com/aweme/v1/web/aweme/detail/
（a_bogus 签名 + 有界 ttwid 会话）
  ├─ 成功 -> 使用目标作品详情
  └─ 失败 -> slidesinfo 或 iesdouyin.com/share/{type}/{id}/
                         ↓
                    读取 window._ROUTER_DATA
```

优先走 Web 详情接口：只带作品 ID 等稳定参数，用 `a_bogus` 签名和短生命周期 `ttwid` 会话完成访问；目标作品 ID 会再次校验，遇到会话失效、非 JSON 或目标不匹配时最多刷新一次会话。详情接口不可用时再回退 slidesinfo 或分享页。移动分享页相对轻量，通常保留 `window._ROUTER_DATA`，是重要的兜底数据源。

视频和图文结构不同：

- 视频从 `videoInfoRes` 取作品信息和播放地址。
- 图文笔记从 `noteDetailRes` 取图片列表。
- slides 从 `slidesInfoRes` 或 `slidesinfo` 接口取混排条目。

视频地址有一层转换：平台可能返回完整 URL，也可能只返回资源 ID，这时需要按播放接口格式补成可访问地址。图文图片结构可能多层嵌套，递归寻找常见 URL 字段，保留同一张图片的多个候选。slides 的 `images` 条目可能内嵌分段视频，必须先识别视频 URL 和封面 URL；只有确认是纯图片条目时才加入图片列表，避免把多分段视频误解析成图片。

## 四、TikTok

支持能力：视频 / 图片 / 文本

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

## 五、快手

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

支持能力：视频 / 图片 / 文本

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

## 九、今日头条

支持能力：视频 / 图片 / 文本

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

## 十、小黑盒

支持能力：视频 / 图片 / 文本

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

## 十一、Steam

支持能力：视频 / 图片 / 文本

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

## 十二、Twitter/X

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

## 十三、Pixiv

支持能力：图片 / 文本

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

## 十四、雪球

支持能力：图片 / 文本

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

## 十五、NGA

当前未提供解析器。

NGA 已关闭访客浏览：`read.php?tid=...` 直接返回 `ERROR:1 未登录`，站点根路径返回 `ERROR:15 访客不能直接访问`。挑战页里的 `guestJs` Cookie 每次请求都会重新生成，回填后仍被拒绝；`app_api.php` 的 `post/list` 返回 `code:12 未登录`，随响应下发的 `guest_token` 无法换取内容，带 `access_token` 时改报 `code:5 签名错误`。`__output=8`、`__output=11`、`lite=js` 等输出形式只是换了错误载体，同样是 403。

也就是说取数必须依赖 `ngaPassportUid` + `ngaPassportCid` 登录 Cookie。若之后决定支持，需要先引入用户提供 Cookie 的配置项，并注意页面是 GBK/GB18030 编码。

## 十六、维护原则

改平台解析逻辑前，过一遍这些问题：

- 这个链接最终指向哪种内容形态？
- 平台前端真正用的是页面状态、接口 JSON，还是旧版内联数据？
- 短链和分享参数是否影响稳定 ID 的提取？
- 是否有移动端和 PC 端两套结构？
- 视频、图集、转发、引用、番剧、帖子是否需要分流？
- 媒体地址是否依赖 Referer、Cookie、User-Agent 或代理环境？
- 没有媒体地址时，是受限内容、纯文本内容，还是解析失败？
- 当前兜底是否会误把防护页、错误页、HTML/JSON 错误响应当成媒体？
