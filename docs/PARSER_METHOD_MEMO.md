# 平台解析思路

## 一、总思路

大多数分享链接本身并不保存内容数据，它只是一个入口。真正有用的信息通常藏在以下位置：

- 短链重定向后的稳定 URL。
- 平台 Web 前端请求的公开接口。
- HTML 中注入的页面状态。
- SSR 或 rehydration 脚本。
- 旧版页面残留的内联 JSON 或媒体字段。

解析器要做的事，不是猜媒体地址，而是尽量复现平台前端取数路径。

```text
分享链接
  ↓
展开短链或清理分享参数
  ↓
识别内容 ID 和内容形态
  ↓
选择对应页面或接口
  ↓
读取结构化数据
  ↓
提取标题、作者、正文、时间、媒体线索和访问状态
  ↓
保留可用候选，交给后续流程处理
```

这里有三个基本原则：

1. 先确认内容形态，再取字段。视频、图集、动态、番剧、帖子、游戏详情页往往不是同一套数据结构。
2. 优先使用平台前端已经使用的结构化数据。页面脚本和接口 JSON 通常比正则扫 HTML 稳定。
3. 保留上下文和候选。媒体地址、访问限制、来源页面、请求环境都可能影响后续能否成功取到内容。

## 二、B站

支持能力：视频 / 图片 / 文本 / 热评

B站看起来都在 `bilibili.com` 附近，但实际有多套内容模型：普通 UGC 视频、PGC 番剧、动态/opus、短链。解析的第一步是展开入口并判断目标类型。

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

### 普通 UGC 视频

UGC 视频的关键不是 BV/AV 本身，而是播放分 P 对应的 `cid`。BV/AV 负责定位视频主体，`cid` 负责定位具体播放单元。

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

`view` 提供标题、作者、简介、发布时间等主体信息；`pagelist` 提供分 P 列表和 `cid`；`playurl` 提供可播放结构。播放结构可能是普通直链，也可能是 DASH 音视频分离流。解析阶段只识别并保留这些结构，不做合并。

### PGC 番剧

番剧不能套用 UGC 链路。番剧入口可能是 `ep_id`，也可能是 `season_id`。如果只有 season，需要先找到可播放 episode。

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

番剧更容易出现会员、试看、地区或付费限制，所以解析时不能只看有没有媒体地址。页面和播放接口返回的访问状态、可看时长、完整时长也要一起保留，用来解释“为什么只能拿到预览”或“为什么没有完整视频”。

### 动态 / opus

动态更像容器。动态本身有作者、正文和发布时间，但里面可能是图片，也可能引用或转发视频。

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

转发动态要特别处理。只保留原视频会丢掉转发人的文字，只保留动态又丢掉视频主体。较稳的做法是把外层动态和内层视频的信息组合起来，让用户知道“谁转发了什么”和“原视频是什么”。

### Cookie 与评论

Cookie 在 B站是增强条件，不是解析前提。Cookie 可用时，播放接口可能返回更完整的清晰度、时长或可访问内容；不可用时仍然走无 Cookie 解析。

评论和热评接口依赖 WBI 签名。解析思路是先从导航接口拿到签名材料，再按 B站前端的规则生成请求参数，而不是硬编码一个固定签名。

## 三、抖音

支持能力：视频 / 图片 / 文本

抖音分享链常见入口是短链。短链展开后，稳定目标通常是 `/video/{id}` 或 `/note/{id}`。这两个形态需要分开处理。

```text
v.douyin.com / douyin.com
  ↓
HEAD 展开，失败再 GET 展开
  ↓
判断 video 或 note
  ↓
请求 iesdouyin.com/share/video/{id}/
或 iesdouyin.com/share/note/{id}/
  ↓
读取 window._ROUTER_DATA
```

抖音移动分享页是主要数据源。它相对轻量，并且通常保留 `window._ROUTER_DATA`，这正是前端渲染分享页时使用的状态。

视频和图文的结构不同：

- 视频通常从 `videoInfoRes` 中取作品信息和播放地址。
- 图文笔记通常从 `noteDetailRes` 中取图片列表。

视频地址还有一层转换：平台可能返回完整 URL，也可能只返回资源 ID。遇到资源 ID 时，需要按平台播放接口格式补成可访问地址。图文图片结构可能多层嵌套，所以解析时会递归寻找常见 URL 字段，保留同一张图片的多个候选地址。

## 四、TikTok

支持能力：视频 / 图片 / 文本

TikTok 使用独立解析器模块，取数路线与抖音完全不同。TikTok 作品页的数据主要在页面 rehydration 脚本里，而普通 HTTP 客户端容易拿到防护页或不完整页面。

```text
tiktok.com / vm.tiktok.com / vt.tiktok.com
  ↓
优先用系统 curl 拉取页面
  ↓
确认页面不是防护页
  ↓
读取 __UNIVERSAL_DATA_FOR_REHYDRATION__
  ↓
失败时读取 SIGI_STATE
  ↓
按新旧结构寻找 itemStruct
```

当前较可靠的主路径是 `__UNIVERSAL_DATA_FOR_REHYDRATION__`。新版页面常见路径是 `webapp.video-detail.itemInfo.itemStruct`。旧页面可能使用 `SIGI_STATE`，或者把作品结构散落在更深层对象里，因此需要递归搜索 `itemStruct`、`video`、`imagePost` 等线索。

TikTok 还会调用 oEmbed，但它只适合作为标题、作者等文本补充。媒体资源仍以页面脚本中的作品结构为主。

TikTok 也要区分视频和图集：

- 视频从 `playAddr`、`downloadAddr`、`PlayAddrStruct`、`bitrateInfo` 中寻找候选。
- 图集从 `imagePostInfo` 或相近结构中收集图片。

如果结构化脚本失败，最后还会从 HTML 中直接查找 `playAddr` 作为兜底。

## 五、快手

支持能力：视频 / 图片 / 文本

快手的解析重点是“优先读结构化页面状态，兼容旧页面痕迹”。短链 `v.kuaishou.com` 会先跳转到真实页面；部分域名或路径还会被改写到更容易取到状态的移动页面。

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
失败或字段不完整时，再用旧字段和 rawData 兜底
```

结构化状态里通常能找到作品主体 `photo`。图集的完整列表通常在 `photo.ext_params.atlas.list`，补充字段可能出现在 `single` 或相近对象里。解析器先判断作品是视频还是图集：

- 视频：直接取作品视频地址。
- 图集：优先读取完整图集列表，再组合 CDN、图片路径和相关资源，形成多张图片的候选地址。

`coverUrls` 这类字段更适合作为封面候选，不能默认等同于整套图集。只有在拿到完整图集列表时才应视为图集解析成功。

旧页面兼容很重要。部分历史链接不会提供完整 SSR 状态，但页面里可能仍有 `photoUrl`、`videoUrl`、`srcNoMark`、`window.rawData` 等字段。它们不如结构化状态稳定，但可以覆盖旧链接和非标准分享页；使用这些兜底字段时要避免把不完整结果误判为成功。

快手图集的难点是图片地址经常不是完整 URL，而是 CDN 前缀加路径。解析时要先组合，再去重，并保持候选顺序。

## 六、微博

支持能力：视频 / 图片 / 文本 / 热评

微博的复杂点在于不同 URL 形态背后是三套数据源。解析前先判断 URL 类型，再选择对应链路。

```text
微博链接
  ↓
判断 URL 类型
  ├─ weibo.com       -> 桌面详情接口
  ├─ m.weibo.cn      -> 移动详情页内联状态
  └─ video.weibo.com -> 视频组件接口
```

### 桌面详情

桌面详情走 `weibo.com/ajax/statuses/show`。这个接口通常需要访客 Cookie、Referer 和 XSRF 相关请求头。拿到 JSON 后，媒体可能散落在多个结构中：

- 混合媒体列表。
- 图片信息表。
- 普通图片列表。
- 页面卡片信息。
- 视频信息对象。

所以解析时不是只读一个字段，而是按优先级扫描这些结构，并把图片、GIF 视频化资源、普通视频分别识别出来。

### 移动详情

移动端详情页不走桌面接口。它会把页面数据注入到 HTML 中，常见形式是：

```text
var $render_data = [...][0]
```

移动端媒体主要在 `status` 下的图片列表和页面卡片中。正文同样可能包含 HTML、表情图片和跳转标签，需要清理后才适合展示。

### 视频组件页

`video.weibo.com/show` 和 `/tv/show` 走组件接口：

```text
weibo.com/tv/api/component
Component_Play_Playinfo
```

视频地址主要来自播放组件的 URL 集合。视频页能提供的作者、标题和正文信息通常比普通微博详情少，因此要以组件返回为准，缺失时保持空值，而不是猜测。

## 七、小红书

支持能力：视频 / 图片 / 文本 / 热评

小红书要兼容移动端和 PC 端两套状态树。短链 `xhslink.com` 只是入口，必须先展开到正式笔记页。

```text
xhslink.com / xiaohongshu.com
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

参数清理要谨慎。移动端分享链接可以去掉部分来源参数，但 PC 链接中的访问参数可能影响页面能否返回完整状态，不能盲目删除。

拿到笔记数据后，按类型处理：

- 视频笔记：从 `video.media.stream.h264` 等结构里取播放地址，并统一协议。
- 图文笔记：从 `imageList`、`urlDefault`、`url`、`infoList` 中选择可用图片地址。

正文里的话题标签带有前端标记，解析时会清理成可读文本。评论信息如果已经随页面状态下发，可以从状态树中收集并按点赞数排序；如果状态里没有，就不额外强行请求高风险接口。

## 八、闲鱼

支持能力：视频 / 图片 / 文本

闲鱼分享链的关键不是直接抓页面 HTML，而是先稳定拿到 `itemId`，再复现 H5 前端真实调用的详情接口。`m.tb.cn` 只是中转页，真正的商品入口通常会落到 `h5.m.goofish.com/item`；PC 链接则会落到 `www.goofish.com/item`。

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

稳定性重点有三个：

1. 短链展开不能只看 HTTP 重定向。`m.tb.cn` 经常返回中转 HTML，需要从脚本里的 `var url = '...'` 提取真实商品页。
2. 优先保留分享页里原始的商品 URL 作为第一手上下文，但真正调详情接口时只依赖稳定的 `itemId`。这样既兼容带参数分享链，也不把解析结果绑死在 `ut_sk`、`spm` 之类易变参数上。
3. 详情主链优先走移动端 H5 的 `mtop.taobao.idle.awesome.detail`。这是当前前端直接使用的数据源，比扫 CSR 页面 HTML 稳定；如果令牌失效，就重新向 MTop 申请 `_m_h5_tk` 后再签名重试。

字段提取建议：

- 标题、正文、价格、发布时间优先读 `itemDO`。
- 作者信息优先读 `flowData.floating` 里的未脱敏昵称，回退 `sellerDO`。
- 图片优先读 `itemDO.imageInfos`，再回退 `flowData.body.sections` 里的图片组件。
- 视频不要假设一定存在；只有在详情 JSON 里明确出现可用播放 URL 时才作为视频返回，否则按图集商品处理。

### 平台约束

- 按实测，自发布商品场景下一个闲鱼商品最多只挂一个视频。
- 因此，如果详情 JSON 中为同一商品暴露出多条播放类 URL，当前实现会把它们视为“同一视频的候选链路”，而不是多个独立视频项。
- 只有未来平台规则变化、一个商品允许挂多个视频时，才需要把这部分分组逻辑重新设计。

## 九、今日头条

支持能力：视频 / 图片 / 文本

今日头条的稳定路径不是 PC 页壳，而是移动端 `m.toutiao.com` 页面。PC 文章页、视频页和微头条 `/w/...` 页面本身都可以作为入口，真正可复用的结构化状态和视频取数线索都能在移动端页里找到。QQ 小程序卡片通常不会直接给文章页，而是通过 `message.meta.news.jumpUrl` 落到 `/w/<id>/` 这类微头条分享页。

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

文章页的关键数据不在可见 HTML，而在一个 `<script>` 标签中的百分号编码 JSON。解码后通常能得到 `articleInfo`，里面已经包含标题、发布时间、来源、作者信息、正文 HTML 和封面。

```text
m.toutiao.com/article/<id>/
  ↓
script 内 %7B...%7D
  ↓
urllib.parse.unquote
  ↓
state.articleInfo
```

正文图片通常直接嵌在 `articleInfo.content` 的 `<img src>` 中，所以解析时不需要额外调用图片接口，只要保留正文里的图片地址即可。正文文本则通过去标签和 HTML 反转义得到简介。

### 微头条 / 小程序卡片

微头条分享页通常是 `m.toutiao.com/w/<id>/`。这类页面虽然路径不是 `article/<id>`，但仍然会在编码脚本里下发 `articleInfo`，只是 `sessionConfig.articleType`、`pageType` 等字段通常会标成 `weitoutiao`。对解析器来说，它更接近“文章型内容”，没有 `playAuthTokenV2` 时就直接按图文内容处理。

```text
message.meta.news.jumpUrl
  ↓
m.toutiao.com/w/<id>/
  ↓
script 内 %7B...%7D
  ↓
state.articleInfo
```

如果这条 `articleInfo` 里没有视频播放令牌，就按普通图文微头条处理；如果未来某些 `/w/` 页面下发了 `playAuthTokenV2`，则可以继续沿用视频页的播放信息链路。

### 视频

视频页同样先取 `articleInfo`，但真正的 MP4 地址不会直接放在正文里，而是通过 `playAuthTokenV2` 间接提供。

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

`playAuthTokenV2` 解码后能得到 `GetPlayInfoToken`。再请求 VOD 接口即可拿到 `PlayInfoList`，其中包含多档码率的 `MainPlayUrl`。解析器应按码率从高到低排序，把这些 MP4 作为同一视频的候选 URL 列表保留。

### 注意点

- 短链 `m.toutiao.com/is/...` 先展开，再抽取真实 `article|video` 和内容 ID。
- QQ 小程序卡片通常从 `message.meta.news.jumpUrl` 提取 URL，常见落地页是 `m.toutiao.com/w/<id>/`。
- 文章图片链接通常可直接下载，但 URL 带签名和过期时间，适合“解析后立即下载”，不适合长期缓存直链。
- 当前实现允许在解析阶段额外刷新页面几次，用同一位置的新签名 URL 补充候选列表，但不会在 `parse()` 阶段直接探测或下载图片本体；真正的媒体访问和缓存写入仍然延后到下载层处理。
- 优先使用移动端页面里的结构化状态，不直接依赖 PC 壳页面或浏览器执行 JS。

## 十、小黑盒

支持能力：视频 / 图片 / 文本

小黑盒分两类对象：游戏详情页和 BBS/link 帖子。入口判断优先看能否提取帖子 `link_id`，否则按游戏 `appid/game_type` 处理。

### BBS/link 帖子

帖子需要走签名接口，而不是直接扫网页。

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

帖子正文可能是富文本 JSON 数组，里面混有 HTML、纯文本、图片、视频和 GIF。解析时要逐项解释：文本拼成正文，图片进入图片候选，视频和 M3U8 保留为视频线索，GIF 根据资源形态判断是图片还是视频。

### 游戏详情页

游戏分享链接会先归一成标准 Web 详情页：

```text
share_game_detail?appid=...
或 /app/topic/game/{game_type}/{appid}
  ↓
/app/topic/game/{game_type}/{appid}
```

游戏页解析分三层：

```text
HTML
  ├─ M3U8 视频线索
  └─ 截图/预览图线索
__NUXT_DATA__
  └─ 还原 Nuxt/devalue 引用结构
game_introduction 接口
  └─ 补充简介、发行时间、开发商、发行商
```

`__NUXT_DATA__` 不是普通对象，而是 Nuxt 的索引引用结构。解析器需要先还原对象树，再在里面寻找与目标 appid 匹配、且最像游戏详情的对象。评分、评价人数、在线人数、价格、奖项、类型标签等信息都从这棵树中整理出来。

游戏简介通常来自 `game_introduction` 接口。页面树提供的是统计和展示卡片，接口提供更完整的正文、发行信息和厂商信息。两者合并后，才能形成比较完整的游戏详情。

## 十一、Twitter/X

支持能力：视频 / 图片 / 文本

Twitter/X 的稳定入口是 tweet ID。解析器只处理包含 `/status/{tweet_id}` 的链接。

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

FxTwitter 能直接给出推文、作者、引用推文和媒体结构，是优先路径。回退条件必须收紧：如果 FxTwitter 明确返回目标不可用，通常说明内容本身不可访问，不应该继续用官方接口绕一圈；只有网络错误、超时或服务端错误，才进入 Guest GraphQL。

Guest GraphQL 的链路是：

```text
guest/activate.json
  ↓
TweetResultByRestId
  ↓
递归遍历响应树
  ↓
寻找匹配 tweet 节点
```

Twitter 的响应嵌套很深，不能假设固定路径永远存在。解析时会递归找带有 tweet legacy 信息的节点。正文优先取长文结构，普通文本再看 `full_text`，并按显示范围裁掉回复前缀。

媒体提取规则：

- 图片取原图地址。
- 视频和动图从 variants 中选择质量较高的 MP4。
- 引用推文作为正文补充，而不是丢弃。

如果一条推文没有图片和视频，但有正文，也仍然是可解析内容。

## 十二、维护原则

修改平台解析逻辑时，优先问这些问题：

- 这个链接最终指向哪种内容形态？
- 平台前端真正用的是页面状态、接口 JSON，还是旧版内联数据？
- 短链和分享参数是否会影响稳定 ID 的提取？
- 是否有移动端和 PC 端两套结构？
- 视频、图集、转发、引用、番剧、帖子是否需要分流？
- 媒体地址是否依赖 Referer、Cookie、User-Agent 或代理环境？
- 没有媒体地址时，是受限内容、纯文本内容，还是解析失败？
- 当前兜底是否会误把防护页、错误页、HTML/JSON 错误响应当成媒体？

好的平台解析不是正则堆叠，而是把平台前端“如何拿到这条内容”的路径尽量复现清楚。
