# 音乐平台接入与 AstrBot 音频发送可行性调研

调研与实现核对日期：2026-09-24（北京时间）。本文保留 AstrBot 音频组件、公开音频和评论的接入前调研。独立音频链路、网易云音乐和喜马拉雅随后已实现；QQ 音乐与 Spotify 仍未接入。本次文档整理未重新执行下列在线请求或聊天平台发送。

## 结论

**本项目已具备独立音频下载和发送链路，已接入网易云单曲与喜马拉雅单集。QQ 音乐只有详情和热评的调研证据，音频尚未打通；Spotify 的图文与试听证据也未转化为插件功能。**

| 平台 | 标题、作者、封面等 | 音频调研结果 | 评论调研结果 | 当前状态 |
| --- | --- | --- | --- | --- |
| 网易云音乐 | 3 首成功 | 1 首返回完整长度 MP3，另外 2 首无地址 | 3 首各取得 3 条明确热评 | 已接入单曲图文、热评与当前可用音频；区分完整音频、试听和不可播 |
| 喜马拉雅 | 免费单集、付费专辑内单集均成功 | 免费单集 M4A 字节验证成功；另一单集无地址 | 移动端取得 20 条按高赞排列的评论；网页端普通评论也成功 | 已接入单集图文、当前可用音频与最多首屏 20 条高赞主评论 |
| QQ 音乐 | 2 首成功 | 音频接口报错、地址为空；没有成功媒体样本 | 《晴天》取得 6 条明确热评 | 未接入，音频仍需验证 |
| Spotify | 歌曲网页与官方 oEmbed 均成功 | 页面提供的 MP3 试听字节验证成功；未取得整曲 | 未发现可用歌曲热评公开接口；播客评论另有产品能力 | 未接入，不承诺整曲或热评 |

成功只表示本次匿名样本和当前出口可用，不代表所有作品、地区、账号权限均可用。音频不可用时仍应保留详情和已经取得的评论。

## 一、AstrBot 的音频发送能力

### 已核查版本与证据

核查了 AstrBot 调研时的最新稳定版 [v4.28.1](https://github.com/AstrBotDevs/AstrBot/releases/tag/v4.28.1) 的组件及适配器源码，并对照[插件开发文档](https://docs.astrbot.app/dev/star/plugin.html)。本项目 `metadata.yaml` 声明 `>=4,<5`，因此不能把本次稳定版的行为自动推广到整个 4.x 范围。

开发文档中的平台矩阵仍将 QQ 官方接口的语音标为不支持，但稳定版源码已包含转换和上传分支。本报告以指定版本源码作为该能力的证据；实际部署仍需端到端验证。

### 三种发送方式需要区分

| 方式 | 已确认能力 | 对本项目的意义 |
| --- | --- | --- |
| `Record` | `fromFileSystem()`、`fromURL()`、`fromBase64()`；可解析音频文件并转换为 WAV | 通用语音入口，适合先建立独立音频节点；不保证音乐播放器样式或保留原始音质 |
| `File` | 可发送本地文件或 URL，具体支持取决于适配器 | 适合保留原始 MP3/M4A、较长节目或语音发送受限的场景 |
| `Music` | 存在音乐分享组件；OneBot 标准定义平台歌曲卡片与自定义卡片 | 属于特定协议的分享能力，不能替代音频解析，也不等于各聊天平台均能播放 |

特别注意：OneBot 标准的 `music.type=xm` 指**虾米音乐**，不是喜马拉雅。不能因 AstrBot `Music` 源码出现 `xm` 就判断喜马拉雅原生卡片受支持。

### 适配器的实际行为

| 聊天平台 | v4.28.1 源码行为 | 接入判断 |
| --- | --- | --- |
| OneBot / aiocqhttp | `Record` 转换为音频 Base64，输出 `record` 消息段；`Node` 内也有 `Record` 序列化 | 有明确发送路径；NapCat 等协议实现对格式、时长、大小和转发内播放的支持仍需实测 |
| Telegram | `Record` 转本地文件后调用 `send_voice`；用户禁止语音时有转文档分支 | 可以走语音路径，但不是原生音乐播放器 `sendAudio`；当前 WAV 转换结果与 Telegram 格式要求还需联调 |
| QQ 官方接口 | `Record` 转换为 `tencent_silk`，群聊/C2C 走语音媒体上传 | 有源码支持；账号权限、文件限制和实际发送未验证，不能沿用旧文档结论直接判定不支持 |

Telegram [官方 `sendAudio`](https://core.telegram.org/bots/api#sendaudio) 可显示音乐播放器，接受 MP3/M4A，并支持标题、表演者和时长；当前文档大小上限为 50 MB。[`sendVoice`](https://core.telegram.org/bots/api#sendvoice) 则用于语音，列出的播放格式是 OGG/Opus、MP3、M4A，其他格式可能显示为音频或文件。这两者不能混为同一种体验。

建议优先验证目标部署的 OneBot 音频独立发送，以及原始文件发送。文字、封面、评论可以继续使用现有构建方式；不能仅因为 `Node` 能序列化 `Record`，就承诺所有客户端能在合并转发中播放音频。若需要 Telegram 原生音乐播放器，应单独评估适配器发送能力，而不是把 `Music` 当作跨平台组件。

音频转换依赖 AstrBot 的媒体处理工具，通常涉及 ffmpeg；QQ 官方语音还涉及 Silk 转换。要同时关注原文件大小、转换后 WAV 的内存/体积以及平台时长限制。本次本机可找到 ffmpeg/ffprobe，但未验证实际 AstrBot 部署中的依赖。

主要源码：

- [Record、Music、File 与 Node](https://github.com/AstrBotDevs/AstrBot/blob/v4.28.1/astrbot/core/message/components.py)。
- [OneBot 适配器](https://github.com/AstrBotDevs/AstrBot/blob/v4.28.1/astrbot/core/platform/sources/aiocqhttp/aiocqhttp_message_event.py)。
- [Telegram 适配器](https://github.com/AstrBotDevs/AstrBot/blob/v4.28.1/astrbot/core/platform/sources/telegram/tg_event.py)。
- [QQ 官方适配器](https://github.com/AstrBotDevs/AstrBot/blob/v4.28.1/astrbot/core/platform/sources/qqofficial/qqofficial_message_event.py)。
- [OneBot 11 消息段规范](https://github.com/botuniverse/onebot-11/blob/master/message/segment.md)。

## 二、本项目已经实现的音频链路

当前使用独立的 `audio_urls`，没有将 MP3/M4A 混入视频字段。音频与图片、视频复用解析、下载、状态、消息和清理流程。

| 位置 | 当前实现 |
| --- | --- |
| `core/types.py`、`core/parser/manager.py` | 独立音频候选、请求头、下载结果及字段校验；复用权限与试听状态字段 |
| `core/downloader/manager.py`、`core/downloader/handler/audio.py` | 音频先下载到缓存，检查真实格式、响应和体积，保存正确后缀；缓存不可用或下载失败时跳过该音频 |
| `core/metadata_state.py`、`main.py` | 只有音频的结果也能参与媒体状态、输出和生命周期处理 |
| `core/message_adapter/node_builder.py`、`core/message_adapter/sender.py` | 按配置构建 `Record` 或 `File`；语音与音频文件独立发送，聚合模式也不将其放进合并转发 |
| `core/storage/file_token.py`、`core/message_adapter/archive_builder.py` | `file_paths` 按视频→图片→音频索引，音频参与 Token 注册和 ZIP 归档 |
| `core/config_manager.py`、`_conf_schema.json` | `message.media_display.audio_send_mode` 默认为“语音”，可选“文件”；`download.max_audio_size_mb` 默认为 30，填 0 时仍受 128 MB 安全上限约束 |

音频文件复用现有缓存清理，没有另建生命周期。文件模式保留原始音频，语音模式的转码与最终显示由 AstrBot 适配器处理；现有实现没有引入 `Music` 卡片或 Telegram 原生 `sendAudio` 专用发送分支。

网易云音乐与喜马拉雅评论已经映射到统一结构：`id`、`username`、`uid`、`message`、`time`、可选 `likes`，复用 `message.hot_comments.count`、平台开关、文本分片与 ZIP 文本输出。条数为 0 或只输出富媒体时不请求评论；评论失败不影响音频和详情。

网易云音乐、喜马拉雅已归入“国内视频类平台（含音频）”，在 AcFun 之后依次注册，配置 schema、平台导出和本地发现顺序一致。两者没有新增账号、代理、浏览器或外部 API 服务配置。

## 三、网易云音乐

### 实测结果

无账号 Cookie、无 Authorization，直接请求网易云自身接口：

| 歌曲 | 详情 | 独立热评接口 | 音频接口 |
| --- | --- | --- | --- |
| 晴天，`186016` | 周杰伦、叶惠美、269 秒、封面 | 3 条 | `url=null`、`code=404`、`fee=0` |
| 海阔天空，`347230` | Beyond、326 秒、封面 | 3 条 | `url=null`、`code=-110`、`fee=1` |
| 世间美好与你环环相扣，`1363948882` | 柏松、听闻余生、191.96 秒、封面 | 3 条 | 标准音质 128 kbps MP3，3,072,462 字节 |

第三首返回 `time=191960`、`freeTrialInfo=null`，与歌曲总时长一致。CDN 范围请求实际返回 `206`、`audio/mpeg`、`bytes 0-1023/3072462`，文件头为 ID3，因此已验证完整长度资源的元数据及真实音频字节；未下载整首或发送到聊天平台。

### 可采用的接口与链接

- [歌曲详情](https://music.163.com/api/song/detail/?ids=%5B186016,347230,1363948882%5D)：歌名、歌手、专辑、封面、时长等。
- [独立热评](https://music.163.com/api/v1/resource/hotcomments/R_SO_4_186016?limit=3&offset=0)：`hotComments` 含正文、作者、时间和点赞。
- [歌曲评论](https://music.163.com/api/v1/resource/comments/R_SO_4_186016?limit=3&offset=0)：本次同时返回 15 条热评和 3 条最新评论。不同接口的 `total` 含义不能混用。
- [音频 URL](https://music.163.com/api/song/enhance/player/url?ids=%5B186016,347230,1363948882%5D&br=128000)与[新版音频 URL](https://music.163.com/api/song/enhance/player/url/v1?ids=%5B186016,347230,1363948882%5D&level=standard&encodeType=mp3)：本次两条路线的逐首结果一致。

当前 `core/parser/platform/netease.py` 已支持 `music.163.com/song?id=…`、`music.163.com/#/song?id=…` 和 `music.163.com/m/song?id=…`，本地归一歌曲 ID。`163cn.tv` 短链、歌单、专辑、播客未接入。音频使用 `/api/song/enhance/player/url/v1` 的标准 MP3 请求，评论使用独立热评接口，最多读取 3 页且每页最多 20 条。

`commentId/content/time/likedCount/user.nickname/user.userId` 可映射当前热评结构。`fee=0` 不能直接解释为可播放，本次《晴天》就是反例；应依据实际 `code/url/freeTrialInfo/time` 判断。返回直链的 `expi=1200`，应临近下载时获取，不作为永久链接缓存。

原始调研没有实测登录、会员、高音质或试听样本，不能承诺所有会员歌曲都能获得试听。当前实现根据音频地址、返回状态、试听区间和可用时长记录完整、试听或不可播放状态；会员、购买、版权与地区只作为不可播的可能原因，不保证确切归因。没有引入账号体系、替代音源或外置 Node API 服务。

参考：[增强版 API 的歌曲音频实现](https://github.com/NeteaseCloudMusicApiEnhanced/api-enhanced/blob/master/module/song_url_v1.js)、[热评实现](https://github.com/NeteaseCloudMusicApiEnhanced/api-enhanced/blob/master/module/comment_hot.js)。参考项目的能力描述不代替本次接口实测。

## 四、喜马拉雅

### 免费单集音频已验证

[单集 `47740352` 的移动 JSON](https://m.ximalaya.com/tracks/47740352.json) 返回标题、主播、简介、封面、93 秒时长以及两档 M4A 直链。对 `play_path_64` 做范围请求，得到 `206`、`audio/mp4`、M4A 文件签名和总大小 `762063` 字节。

当前 `core/parser/platform/ximalaya.py` 已使用移动单集 JSON 读取详情与音频，支持可信主机下的 `/sound/<id>` 和 `/<专辑ID>/sound/<id>`，无需浏览器或独立解析服务。`play_path_64`、`play_path_32`、`play_path` 作为同一音频的候选；有普通候选时不混入带 `_preview_` 标记的试听候选。M4A 通过独立音频链路处理。

### 评论有高赞与时间两条路线

- [移动端首屏评论](https://m.ximalaya.com/m-revision/common/track/queryTrackCommentsFirstPage?trackId=562111701&pageSize=20&page=1)：返回 `ret=0`、`totalCount=146`，实际 20 条；服务器顺序的点赞数为 `46、38、17、15、11、11、9、8、5…`。包含 `id/content/nickname/uid/likes/createdAt`，可直接映射热评。接口来自[当前官方移动前端脚本](https://s1.xmcdn.com/yx/ximalaya-mobile-resource/last/dist/scripts/825351.js)。
- [网页端评论](https://www.ximalaya.com/revision/comment/queryComments?trackId=562111701&page=1&pageSize=20)：同样成功，但样本按时间倒序，前几条赞数为 `0、0、0、2、1…`。另一个免费单集也取得一级评论和回复。

移动接口的高赞顺序来自服务端，不是对一页最新评论自行排序。不过尚未验证其全量排名算法、翻页和所有内容类型，因此准确描述应是“移动端首屏高赞评论”，不承诺全站/全帖完整热度排名。当前实现只读移动端第一页，最多 20 条主评论，保留服务端顺序并排除楼中楼；网页普通评论路线仅为调研证据，未作为生产回退。

### 付费与集合范围

付费专辑内单集 `562111701` 返回 `is_paid=true`、`is_free=true`，但移动 JSON 的音频直链均为空。这个样本说明不能仅根据某个免费标志判断可播；本次没有验证专用付费或试听接口，也不能断言其无法试听。该内容评论仍然可以匿名读取。

网页专辑 `getTracksList` 路线本次返回 `ret=407`、`webtk` 缺失。参考 RSSHub 后，另一条[移动专辑列表](https://mobile.ximalaya.com/mobile/v1/album/track/?albumId=299146&pageSize=3&pageId=1)匿名返回 `ret=0`、3 条 `isPaid=false` 的节目，含 `trackId/title/duration/createdAt`，总数 `3679`。原专辑样本 `5534601` 在移动接口返回 `ret=924`、内容下架，说明内容状态也需要单独处理。

因此，**专辑列表并非整体受阻**：移动列表首屏已验证；列表没有直接返回音频 URL，需要逐条获取单集详情。原始调研未验证全部分页和批量下载，也未验证 RSSHub 另行调用的网页专辑详情接口，不能称为 RSSHub 整条专辑流程已联调通过。当前仅接入单集，专辑展开、短链、账号鉴权和专用付费播放接口均未接入；无音频地址时保留图文与评论，带已识别试听标记时明确提示试听。

参考：[yt-dlp 喜马拉雅提取器](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/ximalaya.py)。其付费相关实现不属于本次已经打通的能力，也不建议直接搬入整个提取器框架。

## 五、QQ 音乐

### 已打通详情与热评

通过 [QQ 音乐 CGI](https://u.y.qq.com/cgi-bin/musicu.fcg) 的 `music.pf_song_detail_svr / get_song_detail_yqq` 查询，《晴天》歌曲 MID `0039MnYb0qxYhV`、歌曲 ID `97773` 的详情返回歌名、作者、专辑、269 秒时长及付费/试听字段；另一首 ID `100`《七个Happy Party》也返回详情。两个音频测试样本都属于付费歌曲，不能据此推断所有免费歌曲的结果。

通过 `music.globalComment.CommentRead / GetHotCommentList` 匿名取得《晴天》热评。请求 `PageSize=5` 实际返回 6 条，含 `CmId/Nick/EncryptUin/Content/PubTime/PraiseNum/HotScore`；应按项目配置自行截断和去重，不假定响应条数严格等于请求值。响应热评总数 `3963`、总评论数 `330698`，两者应区别处理。

[旧移动分享链接](https://i.y.qq.com/v8/playsong.html?songmid=0039MnYb0qxYhV) 实测跳转到 [当前歌曲页](https://y.qq.com/n/ryqq_v2/songDetail/0039MnYb0qxYhV)。当前页面主要是动态壳，详情接口比只抓 HTML 更合适。接入时需要识别 MID 和数值 ID，分别用于对应接口，不能把二者混用。

### 音频仍是未解决项

新旧 vkey 请求均得到 `req.code=1000`、`retcode=104009`、`invalidq`、空 `purl`；《晴天》的 `RS02` 试听文件请求也未成功。本次没有拿到可验证的音频字节。

这只能证明当前匿名请求没有打通，可能涉及请求参数、接口校验或访问条件，不能据此断定必须登录、全站不可解析或错误一定由版权导致。失败响应的最外层 `code=0`、文件项 `result=0`，因此必须同时检查模块状态及非空 `purl`，不能只检查 HTTP 200 或单个状态字段。详情中的 `size_try/try_begin/try_end` 也不等于已拿到试听文件。

[QQMusicApi 下载说明](https://l-1124.github.io/QQMusicApi/tutorial/download/)提供媒体文件类型、凭据与播放 URL 的研究线索。后续应以有效 `purl`、CDN 字节和实际时长为验收标准；源码支持的格式列表不能直接成为本插件对用户的能力承诺。

本次公共请求参数为 `comm={ct:24,cv:0,format:"json",uin:0}`，未携带用户凭据。接口结构参考 [歌曲源码](https://github.com/L-1124/QQMusicApi/blob/main/qqmusic_api/modules/song.py)和[评论源码](https://github.com/L-1124/QQMusicApi/blob/main/qqmusic_api/modules/comment.py)。评论请求采用歌曲业务类型 `BizType=1`、数值 `BizId=97773`，不能直接替换为 MID；专辑和歌单评论未实测。

**建议：单曲图文与热评可以先接；音频标为待验证，不把登录或额外服务作为未经验证的必需配置。**

## 六、Spotify

### 图文解析与试听都有实际证据

样例 [Cut To The Feeling](https://open.spotify.com/track/11dFghVXANMlKmJXsNCbNl)：

- [官方 oEmbed](https://open.spotify.com/oembed?url=https%3A%2F%2Fopen.spotify.com%2Ftrack%2F11dFghVXANMlKmJXsNCbNl) 匿名 HTTP 200，返回标题、封面和 iframe。iframe 是网页播放器，不是聊天平台可发送的音频 URL；oEmbed 也没有本次歌曲的歌手字段。
- 歌曲页 HTTP 200，`og:title`、`og:description`、`og:image` 包含标题、歌手、专辑/年份等信息。`og:audio` 提供 `p.scdn.co/mp3-preview/…` 试听地址。
- 对该试听地址读取 32 字节，返回 `206`、`audio/mpeg`、`Content-Range: bytes 0-31/201247` 和 MP3 帧头。已验证试听字节，不是完整歌曲；没有通过媒体解码测量这个样本的实际时长。
- 无令牌调用 `api.spotify.com/v1/tracks/{id}` 返回 `401`；Web API 路线需授权凭据，不能与匿名 oEmbed 混为一谈。

[Get Track 文档](https://developer.spotify.com/documentation/web-api/reference/get-track)将 `preview_url` 标为已弃用、可为 null，并描述为 30 秒 MP3 预览。一个页面仍有试听不代表每首歌都有，也不能把这个字段作为稳定核心能力。

### 整曲播放不能转化为下载承诺

官方 Web API 提供元数据与播放控制，没有可供本插件下载发送的整曲文件接口。[Web Playback SDK](https://developer.spotify.com/documentation/web-playback-sdk)用于浏览器播放，需要符合要求的 Premium 订阅，并非返回 MP3 文件的下载接口。

Spotify 在 Get Track 文档和[开发者政策](https://developer.spotify.com/policy)中明确限制下载/流抓取及预览片段的独立服务用途。这直接影响本项目“下载后转发”的适用性：建议首期仅输出图文和原站链接，试听只记录为技术调研结果，不默认纳入音频转发功能。

如后续选择官方 Web API，还要评估当前[开发模式规则](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide)：开发模式要求应用所有者保持 Premium，新增应用授权用户数受限制。2026 年 7 月开发者 Client ID 数量上限又有调整，不能照搬旧教程中的固定额度。本次没有配置凭据或验证授权调用。

### 热评结论

未找到可用的歌曲热评公开接口，当前官方 Web API 参考也没有歌曲/播客评论端点。不能承诺 Spotify 歌曲热评。

Spotify [官方公告](https://newsroom.spotify.com/2024-07-09/podcast-app-comments-update/)确认**播客单集**具有评论功能，且经历了自动发布规则更新；这不等于歌曲评论能力，也不等于提供匿名评论 API。本次没有取得 Spotify 评论正文，播客评论应单独标为未打通，不应把 Spotify 整个平台写成“没有评论”。

## 七、RSSHub 参考复核

根据补充要求，对阻塞项参考 RSSHub 的平台路由实现。此处记录其能提供的实际解析思路，不把订阅源、元数据接口或第三方依赖视为已经解决音频与热评。

检查的仓库快照为 `89d6390a96eeb2996335ce4902b023645322aa92`，结论如下：

| 平台 | RSSHub 实现范围 | 对本次问题的帮助 |
| --- | --- | --- |
| 网易云音乐 | `163/music/` 下的歌手歌曲、专辑、歌单、电台与用户相关路由 | [歌单实现](https://github.com/DIYgod/RSSHub/blob/89d6390a96eeb2996335ce4902b023645322aa92/lib/routes/163/music/playlist.ts)使用歌曲/歌单详情接口，可作为后续集合入口参考；无需因此引入 RSSHub 服务 |
| 喜马拉雅 | [专辑路由](https://github.com/DIYgod/RSSHub/blob/89d6390a96eeb2996335ce4902b023645322aa92/lib/routes/ximalaya/album.ts)和平台内工具 | 移动专辑列表路线经补充实测成功，解除网页接口受限造成的列表阻塞；免费音频使用单集 JSON，付费路径要求已购买账号 token |
| QQ 音乐 | 已检查 `qq/` 目录及常见音乐命名空间 | 本次未找到可以解决 vkey/试听失败的实现；这不是对整个仓库历史作不存在断言 |
| Spotify | artist、playlist、show、saved、top artists、top tracks 共 6 个路由 | 仍围绕官方 Web API；没有发现歌曲/播客评论请求或整曲音频实现 |

Spotify 的 [utils.ts](https://github.com/DIYgod/RSSHub/blob/89d6390a96eeb2996335ce4902b023645322aa92/lib/routes/spotify/utils.ts)通过 Client ID/Secret 和 `client_credentials` 获取令牌；个人数据还需要 refresh token。`parseTrack` 只整理标题、歌手、专辑和链接。[show.ts](https://github.com/DIYgod/RSSHub/blob/89d6390a96eeb2996335ce4902b023645322aa92/lib/routes/spotify/show.ts)请求 `/v1/shows/{id}?market=US`，把 `audio_preview_url` 写入附件，不能由此推断完整播客音频可下载，字段实时是否非空也未在本次验证。

因此，RSSHub 对集合入口有参考价值，但没有解除 QQ 音乐的音频请求问题，也没有提供 Spotify 整曲/热评的突破。没有运行或部署 RSSHub。

## 八、当前实现与验证边界

1. **音频基础链路已经实现**：缓存音频经 `Record` 或 `File` 独立发送，并参与大小限制、Token、ZIP 和清理。目标聊天平台的实际格式、时长和发送效果仍需在部署环境验证，不能由组件源码或本地桩断言端到端成功。
2. **网易云单曲与喜马拉雅单集已经接入**：具备匿名元数据、音频字节和真实评论的调研证据；生产解析器按实际响应输出可用音频或保留图文，附加评论失败不影响主结果。样本成功不代表会员、付费、地区限制内容都可播放。
3. **QQ 音乐尚未接入**：若后续立项，可从图文和热评开始；需先解决 vkey 请求并验证真实音频，再承诺音频能力。
4. **Spotify 尚未接入**：若后续立项，应先评估图文与原站链接；试听证据不能扩展为整曲承诺，也不提供尚无数据证据的热评开关。

原始调研使用少量公开 HTTP 请求和源码阅读，没有账号登录，没有读取本机用户凭据，没有启动 MCP、浏览器或第三方 API 服务。没有向聊天平台发送消息，也没有执行全量回归；该阶段的验证是接口业务结果、非空评论正文以及媒体 Range 响应。本次整理核对了生产解析器、音频链路、配置 schema 与平台注册，未将原始探针记录重新表述为新增在线验证。

原始响应和探针位于被忽略的 `test/music_research/`，不加入 Git 跟踪，也不随仓库分发。音频流程和对应解析器的定向测试统一保存在 `test/`。
