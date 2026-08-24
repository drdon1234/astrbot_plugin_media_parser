# 架构文档

本文件按当前项目真实实现描述插件边界、模块职责和主流程。平台解析细节见 `docs/PARSER_METHOD_MEMO.md`。

## 一、整体框架

### 1.1 系统概述

本项目是 AstrBot 流媒体平台链接解析插件。插件监听消息事件，识别可解析平台链接，调用对应平台解析器提取文本元数据和媒体候选 URL，再按缓存目录能力与媒体类型决定 `local/direct/skip` 发送模式，最终构建 AstrBot 消息节点并完成清理。

当前支持的平台解析器包括：

- B站：支持 视频 / 图片 / 文本 / 热评；覆盖普通视频、番剧、动态 / opus，支持 Cookie 增强和扫码登录运行时。
- 抖音：支持 视频 / 图片 / 文本；覆盖短链、视频、图集和 slides 多分段分享页。
- TikTok：支持 视频 / 图片 / 文本；覆盖短链、视频和图集作品页，使用独立解析器和代理开关。
- 快手：支持 视频 / 图片 / 文本；覆盖短链和作品分享页。
- 微博：支持 视频 / 图片 / 文本 / 热评；覆盖桌面详情、移动详情和视频组件页。
- 小红书：支持 视频 / 图片 / 文本 / 热评；覆盖短链、移动端和 PC 端笔记页。
- 闲鱼：支持 视频 / 图片 / 文本；覆盖短链、H5 商品页和 PC 商品页。
- 今日头条：支持 视频 / 图片 / 文本；覆盖文章、微头条、视频、短链跳转页和 `message.meta.news.jumpUrl` 小程序卡片。
- 小黑盒：支持 视频 / 图片 / 文本；覆盖游戏详情页和 BBS/link 帖子。
- Steam：支持 视频 / 图片 / 文本；通过 Steam `appdetails` 接口解析游戏页，可选委托小黑盒完整游戏路径补充统计信息。
- Twitter/X：支持 视频 / 图片 / 文本；优先 FxTwitter/FxEmbed，服务不可用时回退 Guest GraphQL。
- Pixiv：支持 图片 / 文本；覆盖插画和漫画作品页、多页原图候选、Cookie 访问限制与解析/图片代理。

### 1.2 核心模块结构

```text
astrbot_plugin_media_parser/
├── main.py                          # AstrBot 插件入口与生命周期
├── _conf_schema.json                # AstrBot 配置 schema
├── docs/
│   ├── ARCHITECTURE.md              # 当前架构文档
│   └── PARSER_METHOD_MEMO.md        # 平台解析方法说明
└── core/
    ├── config_manager.py            # 配置解析、默认值、解析器工厂
    ├── constants.py                 # 常量与默认路径/超时/并发值
    ├── logger.py                    # 统一 logger
    ├── types.py                     # MediaMetadata / LinkBuildMeta / BuildAllNodesResult
    ├── message_text.py              # 消息文本长度约束与分片
    ├── metadata_visibility.py       # 文本元数据字段可见性读取
    ├── parser/
    │   ├── manager.py               # ParserManager，并发解析与结果归一
    │   ├── router.py                # LinkRouter，链接提取、去重、直播过滤
    │   ├── utils.py                 # 通用工具、卡片 URL 提取、直播判断、请求头构建
    │   ├── runtime_manager/
    │   │   └── bilibili/auth.py     # BilibiliAuthRuntime，Cookie 校验与扫码登录
    │   └── platform/                # 各平台解析器
    │       ├── base.py              # BaseVideoParser 接口定义与共用方法
    │       ├── short_video_shared.py # 抖音/TikTok 共享的 URL、HTML、JSON 工具
    │       ├── bilibili.py          # B站视频/番剧/动态解析器
    │       ├── douyin.py            # 抖音视频/图集解析器
    │       ├── douyin_web.py        # 抖音 Web 详情接口与会话管理
    │       ├── douyin_sign.py       # 抖音 a_bogus 签名
    │       ├── tiktok.py            # TikTok 视频/图集解析器
    │       ├── kuaishou.py          # 快手视频/图集解析器
    │       ├── weibo.py             # 微博桌面/移动/视频组件解析器
    │       ├── xiaohongshu.py       # 小红书笔记解析器
    │       ├── xianyu.py            # 闲鱼商品页解析器
    │       ├── toutiao.py           # 今日头条文章/微头条/视频解析器
    │       ├── xiaoheihe.py         # 小黑盒游戏详情/BBS 帖子解析器
    │       ├── steam.py             # Steam 游戏详情页解析器
    │       ├── twitter.py           # Twitter/X 解析器（FxTwitter + Guest GraphQL）
    │       └── pixiv.py             # Pixiv 插画/漫画解析器
    ├── downloader/
    │   ├── manager.py               # DownloadManager，媒体模式决策与下载调度
    │   ├── router.py                # 下载路由：dash/m3u8/image/video/range
    │   ├── utils.py                 # 缓存路径、扩展名、URL 前缀、Content-Type 工具
    │   ├── validator.py             # 媒体预检、大小探测、响应校验
    │   ├── budget.py                # 流式下载字节预算与硬上限
    │   ├── fileio.py                # 取消安全的异步文件 I/O 辅助
    │   ├── image_format.py          # 图片格式 MIME、签名与后缀判定
    │   └── handler/
    │       ├── base.py              # 通用流式下载、Range 下载、重试
    │       ├── normal_video.py      # 普通视频缓存下载
    │       ├── range_downloader.py  # range: 前缀下载封装，失败降级普通下载
    │       ├── dash.py              # DASH 音视频下载与 ffmpeg 合并
    │       ├── m3u8.py              # M3U8 分片下载、拼接、音视频合并
    │       ├── image.py             # 图片下载与可选 ffmpeg 转 PNG
    │       └── video_cover.py       # 视频仅封面模式的首帧截取
    ├── message_adapter/
    │   ├── node_builder.py          # Plain/Image/Video 节点构建
    │   ├── text_renderer.py         # 文本元数据 PNG 渲染
    │   ├── sender.py                # 聚合/独立/文件发送
    │   └── archive_builder.py       # 解析结果 ZIP 归档
    ├── translation/
    │   ├── manager.py               # 元数据翻译与严格 JSON 结果回填
    │   ├── llm_client.py            # 自定义 OpenAI 兼容 / Ollama 调用
    │   └── provider_defs.py         # 翻译相关厂商标签与默认值
    ├── storage/
    │   ├── __init__.py              # 导出清理、标记、文件 Token 注册能力
    │   ├── file_cleaner.py          # 文件与空父目录清理
    │   ├── cache_marker.py          # .astrbot_media_parser 标记与安全清理
    │   ├── file_token.py            # AstrBot file_token_service 集成
    │   └── parse_record.py          # 解析频率限制与持久化记录
    └── interaction/
        ├── base.py                  # AdminAssistManager 基类
        └── platform/bilibili/
            └── cookie_assist.py     # B站 Cookie 管理员协助登录
```

### 1.3 核心契约

#### 输出开关

`parsers.<平台>` 同时控制解析器是否启用以及该平台的输出模式。

- `关闭`：不创建该平台解析器，不提取/解析该平台链接。
- `全部发送`：发送文本元数据节点和图片/视频节点。
- `仅文本`：解析并发送文本元数据，不进入下载处理、文件 Token 注册和富媒体节点构建。
- `仅富媒体`：解析并发送图片/视频，不构建文本节点；热评条数会对该平台归零。
- 所有平台均为 `关闭` 时：普通消息不进入解析，但管理员清缓存命令仍在停用检查之前处理。
- 普通解析的开场语只在富媒体流程中触发，且只有出现可发送媒体时才发送；如果已发送开场语但最终没有节点，会补发空结果说明。ZIP 归档不构建聊天节点，但会在归档流程中按 `message.opening.enable` 发送一次 `message.opening.archive_content`。

#### 消息聚合与 ZIP 归档

`message.packing.mode` 是为保留现有用户配置而继续使用的持久化路径，内部映射为 `AggregationConfig`，控制最终发送策略：

- `不聚合`：始终逐链接独立发送。
- `全部聚合`：普通媒体使用 `Nodes` 消息集合发送，大媒体仍按 `download.large_video_threshold_mb` 单独发送。
- `按条件聚合`：统计真正进入合并转发的图片、视频、文本和翻译节点，任一数量达到 `message.packing.thresholds` 对应阈值时使用消息集合；单独发送的大媒体不参与统计。

`message.packing.thresholds.image_count`、`video_count`、`node_count` 均为非负整数。阈值为 `0` 时表示不按该项触发聚合。

`message.text_metadata.quote_user_message` 控制非聚合发送时文本元数据节点是否引用对应的用户消息。媒体节点、热评节点、翻译节点和消息集合不引用用户消息。

`message.archive.command` 为空时关闭 ZIP 功能。配置命令后，用户必须引用含可解析链接的消息，并发送一条只包含该命令的消息；命令与 `admin.clean_cache_keyword` 相同时会被禁用。归档流程会按当前平台输出模式过滤链接并尝试下载原始媒体，不构建聊天节点，但在 `message.opening.enable` 开启时发送一次 `message.opening.archive_content`，也不会注册普通媒体中转 Token 或继承聊天的字段可见性和“视频仅发送封面”策略。`archive_builder.py` 在工作线程中以固定 `media_parser/序号_标题/` 布局写入 ZIP；每条链接生成 `metadata.txt` 与白名单化的 `details.json`，失败媒体记录链接和原因。`message.archive.max_total_size_mb` 限制单次归档媒体总量，配置值会限制在 1–4096 MB。源媒体在发送后立即清理；ZIP 至少保留 300 秒供 AstrBot/协议端延迟拉取，并用持久过期标记回收。

`message.text_metadata.show_title/show_author/show_timestamp/show_original_link/show_description` 分别控制来源元数据字段。开关默认均为 `true`，只改变展示与翻译输入；访问状态、媒体大小、跳过原因和错误提示不受影响。现有 `message.*` 路径保持不变，避免 AstrBot 递归更新 schema 时删除用户旧配置。

`message.text_metadata.render_to_image` 开启后，主流程会在节点构建和翻译完成后收集所有文本节点，使用 `text_renderer.py` 在缓存目录的 `rendered_text/` 下生成单张 PNG，再移除已成功渲染的 Plain 节点并发送图片。可选样式为 `fresh/tech/serious/card`，字体大小限制为 16–42；默认优先使用插件内置的 Noto Sans CJK，也可通过 `ASTRBOT_MEDIA_PARSER_FONT` 指定字体文件，再按配置的字体族和系统字体路径回退。渲染失败、字体不可用或缺少 Pillow 时保留原文本节点，不影响富媒体发送。启用文件 Token 中转时，渲染图片也会单独注册 Token，并纳入同一 TTL 清理流程。

配置 schema 对依赖开关的字段使用条件显隐，例如翻译提供商、权限名单、B站 Cookie、管理员协助登录和媒体中转参数。显隐只影响配置页展示，不会删除已保存的隐藏值。

#### 缓存目录

`download.cache_dir` 是媒体缓存根目录，但非 Docker 环境不会直接使用用户配置值：

- Docker 环境：使用配置值；为空时使用 `Config.DEFAULT_CACHE_DIR`。
- 非 Docker 环境：优先使用 AstrBot 数据目录下的 `plugin_data/astrbot_plugin_media_parser/cache`，取不到时回退当前工作目录的 `cache/`。
B站运行时 Cookie 文件位于当前缓存根目录下：

```text
cache/runtime_manager/bilibili/cookie.json
```

缓存目录不可用时，普通视频会尽量走 `direct`；图片、DASH、M3U8、平台强制缓存视频会 `skip`。

#### 媒体模式

`local/direct/skip` 是下载层和节点层之间的核心契约。

- `local`：媒体已缓存到本地文件，节点层优先使用文件 Token URL，否则使用本地文件。
- `direct`：节点层直接使用 URL 发送。目前主要用于缓存不可用时的普通视频。
- `skip`：不构建富媒体节点，但文本节点可展示跳过原因。

下载失败后不会静默回退直链。失败原因必须留在 `video_skip_reasons` 或 `image_skip_reasons` 中。

## 二、模块职责

### 2.1 主入口 `main.py`

`VideoParserPlugin` 负责：

- 初始化 `ConfigManager`、`ParserManager`、`DownloadManager`、`MessageSender`、`ParseRecordManager`、`BilibiliAdminCookieAssistManager`。
- 监听所有消息事件。
- 执行权限检查、触发判断、卡片 URL 和回复 URL 提取。
- 协调解析限流、解析、下载、文件 Token 注册、节点构建、发送与清理。
- 在 `terminate()` 中关闭周期清理、延迟清理、管理员交互和下载任务；仍处于 Token TTL 内的已标记文件由下次加载后的过期扫描回收。

管理员私聊发送 `admin.clean_cache_keyword`，且发送者为 `permissions.admin_id` 时，会触发 `cleanup_marked_in(cache_dir)` 主动清理媒体缓存。

### 2.2 配置管理 `core/config_manager.py`

配置被归一为 dataclass 分组：

- `TriggerConfig`：`auto_parse`、`keywords`、`reply_trigger`，提供 `should_parse()` 和 `has_keyword()`。
- `ParserOutputConfig`：各平台输出模式，负责解析器启用、文本/富媒体输出判定。
- `MessageConfig`：消息输出域，由 `OpeningMessageConfig`、`AggregationConfig`、`ArchiveConfig`、`MediaDisplayConfig`、`TextMetadataConfig` 和 `HotCommentConfig` 组成。
- `PermissionConfig`：管理员、白名单、黑名单，提供 `check()`。
- `DownloadConfig`：大小限制、缓存目录、缓存可用性、下载并发。
- `ParseRateLimitConfig`：同链接/同用户解析频率限制、时间窗和持久化记录文件。
- `ProxyConfig`：全局代理、TikTok、小黑盒、Steam、Twitter/X、Pixiv 代理开关。
- `BilibiliEnhancedConfig`：Cookie、最高画质、运行时文件、管理员协助登录与主动更新指令。
- `PixivConfig`：Pixiv Web Ajax API 使用的可选 Cookie。
- `SteamConfig`：Steam 游戏页是否改用小黑盒完整路径解析。
- `MediaRelayConfig`：文件 Token 中转开关、回调地址、TTL。
- `TranslationConfig`：翻译开关、翻译范围、目标语言、AstrBot 内置或自定义大模型配置。输入/输出上限固定为 4000，超时固定为 60 秒，随机性固定为 0。
- `AdminConfig`：清理关键词和 debug 模式。

`ConfigManager` 会将 `parsers` 的输出模式归一到 `ParserOutputConfig.modes`。使用 `关闭`、`全部发送`、`仅文本`、`仅富媒体` 四种字符串模式。缺省平台使用 `全部发送`；显式无效值会安全关闭并记录警告。`message.packing.mode` 会被归一为 `不聚合`、`全部聚合`、`按条件聚合`；条件阈值按非负整数兜底。旧模式值和旧 ZIP 命令会在 schema 仍保留这些字段时迁移，避免 AstrBot 完整性检查提前删除用户配置。

权限优先级为：管理员直接放行，其次个人白名单、个人黑名单、群组白名单、群组黑名单；均未命中时，白名单开启则拒绝，白名单关闭则放行。管理员 ID 会自动加入用户白名单。权限根配置、白/黑名单子配置、开关值或名单类型无效时整段权限配置 fail-closed，所有消息均拒绝。

### 2.3 解析器模块 `core/parser/`

`LinkRouter` 负责：

- 只负责文本提链，不再使用用户可伪造的文本哨兵；`main.py` 在事件层通过发送者 ID 跳过机器人自身消息。
- 遍历启用的解析器调用 `extract_links()`。
- 过滤 hostname 标签含 `live` 的直播链接，也会识别 query 参数内嵌的直播跳转。
- 按原文出现位置排序并去重。

`ParserManager` 负责：

- 接收 `(url, parser)` 列表，按 URL 去重。
- 使用 `asyncio.gather(..., return_exceptions=True)` 并发调用平台解析器。
- 将解析异常转成带 `error` 的 metadata；`SkipParse` 只跳过该链接。
- 归一 `platform`、`parser_name`、`source_url`、`video_urls`、`image_urls`、headers。

`BaseVideoParser` 定义 `can_parse()`、`extract_links()`、`parse()` 接口，并提供 `_add_range_prefix_to_video_urls()`，可给普通视频候选 URL 或 DASH 子流增加 `range:` 前缀。

### 2.4 B站运行时与管理员交互

`BilibiliAuthRuntime` 管理 Cookie 来源和扫码登录：

- 优先使用运行时 Cookie，其次配置 Cookie。
- 通过 B站 nav 接口校验登录态，并对有效/无效结果做短 TTL 缓存。
- 运行时 Cookie 失效时会清空本地凭据，再尝试配置 Cookie。
- 可生成登录链接，在本地生成二维码 PNG，轮询扫码结果，并原子保存新凭据；登录令牌不会发送到第三方二维码服务。
`BilibiliAdminCookieAssistManager` 是插件运行时的非阻塞协助流程：

- 只有管理员私聊过机器人后，才有可主动发送的私聊会话标识。
- 当 B站解析器消费到 Cookie 不可用请求后，后台向管理员发送确认消息。
- 管理员回复 `确定` 后发送登录链接和本地二维码，并在受管理任务中轮询登录结果；等待确认和扫码均会主动超时并清理状态。
- 管理员私聊发送配置的主动更新指令（默认 `B站更新Cookie`）会绕过自动请求冷却，直接进入二维码登录；同一时间只允许一轮扫码登录。
- Notice、Request 等非用户消息事件不会更新私聊会话或消费待确认状态。
- 管理员发送可解析链接时会优先进入解析流程，不会被纯文本协助回复处理抢走。

### 2.5 下载器模块 `core/downloader/`

`DownloadManager.process_metadata()` 是下载决策入口。它会把解析器输出归一为：

```text
video_urls: List[List[str]]
image_urls: List[List[str]]
file_paths: List[Optional[str]]
```

当 `message.media_display.video_cover_only=true` 时，下载器会先把视频媒体转换为图片媒体：解析结果提供 `video_cover_urls` 等封面字段时直接按图片下载封面；没有封面字段时创建本地 `video_cover` 任务。远端视频先经 `handler/video_cover.py` 的本地 HTTP 流式中继读取，按 `download.max_video_size_mb` 及下载器硬上限限制输入字节，再由 ffmpeg 截取第一帧；中继也负责让 HTTPS 来源以本地 HTTP 输入形式兼容 ffmpeg。

`file_paths` 索引固定为：

```text
0 .. video_count - 1                       视频
video_count .. video_count + image_count   图片
```

每个视频独立决策：

- `video_force_download` 或逐项 `video_force_downloads` 为真：必须 `local`。
- URL 含 `dash:` 或 `m3u8:`：必须 `local`。
- 缓存可用的普通视频：`local`。
- 缓存不可用的普通视频：通过大小与可访问性预检后 `direct`。
- 必须 `local` 但缓存不可用：`skip`。
- 普通视频会先走 `get_video_size()`，必要时再 `validate_media_url()`；超过 `download.max_video_size_mb` 或 403 会记录跳过原因。

每个图片独立决策：

- 缓存可用：`local`。
- 缓存不可用：`skip`。
- 当前实现不使用裸图片直链发送。

需要缓存的媒体进入 `local_items`，由 `_download_local_items()` 使用实例级 `asyncio.Semaphore` 控制总下载并发。每个媒体项按候选 URL 顺序尝试，成功回填 `file_path/size_mb/status_code`，全部失败则回填错误原因。

下载路由规则：

- `dash:video_url||audio_url`：进入 DASH 处理器，video/audio 并发下载，音频存在时必须 ffmpeg 合并成功。
- `m3u8:` 或 URL 中含 `.m3u8`：进入 M3U8 处理器，下载分片、合并；音视频分离时需要 ffmpeg。
- `range:`：普通视频路径中先尝试并发 Range 下载，失败降级普通视频下载。
- `image`：进入图片处理器；非 jpg/jpeg/png 会尝试 ffmpeg 转 PNG，缺少 ffmpeg 时保留原格式并写入警告。
- 其他：普通视频流式下载。

`validator.py` 负责 HEAD/Range GET 预检、大小提取、Content-Type 检查、HTML/JSON/文本错误响应识别和 403 状态传递。`budget.py` 为普通视频、图片、DASH、HLS 和封面截取提供流式硬字节预算。所有文件先写 `.part` 再原子替换，取消或失败不会留下伪成功文件。HLS 会选择最高分辨率/带宽变体并限制清单、初始化片和分片总量；`EXT-X-BYTERANGE` 当前明确拒绝。

### 2.6 存储与清理 `core/storage/`

当前实现使用 `cache_marker.py` 管理媒体缓存目录标记，没有持久化的 `CacheRegistry` 文件。解析频率记录由 `parse_record.py` 以 JSON 写入 `cache/runtime_manager/parse_records/records.json`，并按启用限制中的最大时间窗裁剪旧记录。

- `stamp_subdir(directory)` 在媒体缓存子目录中写 `.astrbot_media_parser`。
- `cleanup_marked_in(root_dir)` 只删除缓存根目录的直接子目录中带标记的条目，不删除根目录，不触碰未标记目录。
- `cleanup_file()` 删除单个文件后尝试删除空父目录；如果父目录仅剩标记文件，会同时删除标记和目录。
- `cleanup_files()` 清理本次构建结果记录的图片和视频文件。
- `cleanup_directory()` 用于全部媒体失败后的空壳子目录清理，或 M3U8 临时目录清理。

文件 Token 中转由 `file_token.py` 实现：

- 仅增强已经存在且模式为 `local` 的文件。
- 优先使用插件配置 `media_relay.callback_url`；为空时回退 AstrBot 全局 `callback_api_base`。
- 注册失败不会改变媒体模式，节点层会回退本地文件。
- 文本元数据渲染生成的 PNG 不属于媒体索引，但在中转开启时会单独注册，并与媒体文件使用相同 TTL。
- `main.py` 会按 `media_relay.ttl` 延迟清理本次文件，延迟任务受插件生命周期管理。

### 2.7 消息适配器 `core/message_adapter/`

`node_builder.py` 负责将 metadata 转成节点：

- 文本元数据节点按 `_text_metadata_fields` 展示标题、作者、发布时间、原始链接和简介/正文；访问状态、视频大小、跳过原因和解析错误始终保留。简介/正文放在最后，并用分隔符与前面的元数据分开。
- 热评节点和翻译节点是独立文本节点，不混入文本元数据节点。热评不进入翻译流程。
- 翻译结果来自后台大模型任务，按链接独立请求，每条请求最多包含标题和简介/正文；无需翻译时不会生成翻译节点。
- `collect_text_metadata()` 按发送顺序收集基础文本、热评和翻译；启用图片渲染时，`strip_text_metadata_nodes()` 只在 PNG 生成成功后移除这些 Plain 节点。`text_renderer.py` 在线程中调用 Pillow 绘制中文换行、字段标签和样式背景。
- 富媒体节点只消费 `video_modes/image_modes`：`local` 用 Token URL 或本地文件，`direct` 用剥离前缀后的 URL，`skip` 不构建节点。
- 内部先尝试构建富媒体节点，再构建文本节点，这样节点构建失败时可把原因回填到 metadata，文本节点可展示。
- `build_all_nodes()` 返回 `BuildAllNodesResult(all_link_nodes, link_metadata, temp_files, video_files)`。
- `summarize_node_counts()` 统计真正进入合并转发的图片、视频和总节点数量，供按条件聚合判断使用。
- `archive_builder.py` 直接把 `metadata.file_paths` 中已成功下载的媒体以 `ZIP_STORED` 写入归档，避免额外副本和无效压缩；同时生成 `metadata.txt` 与不含请求头、Cookie、Token、本地路径的 `details.json`。归档前检查请求级总量与可用磁盘空间。

`sender.py` 负责发送，是否进入消息集合由 `main.py` 在节点构建后决定：

- `message.packing.mode=不聚合`：逐链接独立发送。
- `message.packing.mode=全部聚合`：使用 `Nodes` 聚合发送普通媒体；大媒体单独发送。
- `message.packing.mode=按条件聚合`：节点构建和翻译完成后统计可聚合节点，任一数量达到 `message.packing.thresholds` 中配置的阈值时合并转发。
- 非聚合时，如果 `message.text_metadata.quote_user_message=true`，只让文本元数据节点引用对应的用户消息；媒体、热评、翻译和分隔符不引用。
- 纯图片图集会把文本和图片分组发送；混合内容按节点逐个发送。
- 大媒体判定来自 `download.large_video_threshold_mb` 和当前 metadata 的最大视频大小。

## 三、程序执行链

### 3.1 插件消息流程

```text
main.py::VideoParserPlugin.auto_parse(event)
  ↓
admin_cookie_assist.try_update_admin_origin(event)
  ↓
PermissionConfig.check(is_private, sender_id, group_id)
  ├─ false -> 返回
  └─ true  -> 继续
  ↓
管理员清理关键词检查
  ├─ 命中且为管理员私聊 -> cleanup_marked_in(cache_dir) -> 返回
  └─ 未命中 -> 继续
  ↓
parser_output.has_any_output()
  ├─ false -> 返回（管理命令仍可用）
  └─ true  -> 继续
  ↓
提取当前消息文本 / QQ 卡片 URL
  ↓
ParserManager.extract_all_links()
  ├─ 当前消息有链接 -> 进入触发判断
  └─ 当前消息无链接
      ├─ reply_trigger=true 且当前消息含关键词 -> 从 Reply.message_str / Reply.chain 卡片提链
      └─ 仍无链接 -> admin_cookie_assist.handle_admin_reply() -> 返回
  ↓
按 parsers 输出模式过滤无输出链接
  ↓
TriggerConfig.should_parse(original_message_text)
  ├─ false -> 返回
  └─ true  -> 继续
  ↓
ParseRecordManager.filter_links()
  ├─ 同标准链接或同用户超出时间窗限制 -> 跳过对应链接
  └─ 允许解析 -> 写入本次解析尝试记录
  ↓
创建 aiohttp.ClientSession
  ↓
ParserManager.parse_text(parse_text, session, links_with_parser)
  ↓
触发 B站 Cookie 协助请求检查
  ↓
有效 metadata 检查
  ├─ 无有效 metadata -> 返回
  └─ 有效 -> 继续
  ↓
translation.enable=true?
  ├─ 是 -> 复制 metadata_list 并后台启动 MetadataTranslator.translate_metadata_list()
  └─ 否 -> translation_task = None
  ↓
存在启用富媒体输出的 metadata?
  ├─ 是 -> 仅对这些 metadata 并发 DownloadManager.process_metadata()
  └─ 否 -> processed_metadata_list = metadata_list
  ↓
ZIP 命令?
  ├─ 是 -> 强制原视频/完整字段 -> 发送归档开场语（如启用） -> 等待翻译 -> 在线程中 build_zip_archive()
  │        -> send_zip_result() -> 源媒体立即清理，ZIP 至少延迟 300 秒清理
  └─ 否 -> media_relay.enable 时 register_files_with_token_service()
           ↓
         build_all_nodes() + 等待翻译
           ↓
         message.text_metadata.render_to_image=true?
           ├─ 是 -> 合并文本节点 -> Pillow 生成 PNG -> 成功后移除 Plain 节点
           └─ 否/失败 -> 保留原文本节点
           ↓
         summarize_node_counts()
           ↓
         按 message.packing.mode 与条件阈值选择发送路径
           ├─ 聚合 -> send_aggregated_results()
           └─ 独立 -> send_individual_results()
                        └─ 可按 message.text_metadata.quote_user_message 引用用户消息
           ↓
         send_translation_results()
  ↓
finally 清理本次 temp_files + video_files
  ├─ relay 开启 -> 延迟 media_relay.ttl 秒
  └─ relay 关闭 -> 立即清理
```

有效 metadata 的判定条件是：至少一条结果在当前平台输出模式下可能构建节点。带 `error` 的结果也会构建可见错误节点；富媒体输出开启时需要包含视频或图片；文本输出开启时可由标题、作者、简介、发布时间、访问提示、热评、媒体跳过信息或解析错误构建文本节点。

### 3.2 链接提取与解析链

```text
文本
  ↓
LinkRouter.extract_links_with_parser()
  ├─ 遍历 parser.extract_links()
  ├─ 机器人自发消息由 main.py 按发送者身份提前跳过
  ├─ 过滤直播链接
  ├─ 按出现位置排序
  └─ 去重
  ↓
ParserManager.parse_text()
  ├─ 按 URL 去重
  ├─ 并发 parser.parse(session, url)
  ├─ SkipParse -> 跳过
  ├─ 普通异常 -> error metadata
  └─ 成功结果 -> _normalize_metadata()
```

### 3.3 下载处理链

```text
metadata
  ↓
归一 video_urls/image_urls 为 List[List[str]]
  ↓
逐视频决策 local/direct/skip
  ├─ DASH/M3U8/强制缓存 -> local 或 skip
  ├─ 普通视频 + 缓存可用 -> local
  └─ 普通视频 + 缓存不可用 -> 预检后 direct 或 skip
  ↓
逐图片决策 local/skip
  ├─ 缓存可用 -> local
  └─ 缓存不可用 -> skip
  ↓
local_items 并发下载
  ├─ dash -> video/audio 下载 + ffmpeg 合并
  ├─ m3u8 -> 分片下载 + 拼接/ffmpeg 合并
  ├─ range -> 单次 0-0 探测；仅严格 206/Content-Range 才并发，否则降级单流
  ├─ image -> 下载 + 必要时转 PNG；ffmpeg 缺失时保留原格式
  └─ video -> 普通流式下载
  ↓
下载结果回填 metadata
  ├─ file_paths
  ├─ video_modes/image_modes
  ├─ video_skip_reasons/image_skip_reasons
  ├─ video_sizes/status_codes
  ├─ has_valid_media/use_local_files
  ├─ failed_video_count/failed_image_count
  └─ exceeds_max_size/has_access_denied
```

### 3.4 节点构建与发送链

```text
processed_metadata_list
  ↓
build_all_nodes()
  ├─ build_media_nodes()
  │   ├─ token URL
  │   ├─ local file
  │   ├─ direct URL
  │   └─ skip
  ├─ build_text_node()
  ├─ build_hot_comments_node()
  ├─ Plain 文本按 4000 字上限统一分片
  ├─ 可选 text_renderer.py 将所有文本节点合并为 PNG
  ├─ 判定大媒体
  └─ 分类 temp_files/video_files
  ↓
summarize_node_counts()
  ↓
AggregationConfig.should_aggregate_nodes()
  ↓
MessageSender
  ├─ 需要聚合 -> send_aggregated_results()
  └─ 独立发送 -> send_individual_results()
  ↓
translation_task 完成后
  └─ build_translation_nodes_for_all() -> send_translation_results()
```

### 3.5 清理与终止链

普通请求结束：

```text
build_result.temp_files + build_result.video_files
  ├─ media_relay.enable=false -> cleanup_files()
  └─ media_relay.enable=true  -> _schedule_delayed_cleanup(files, ttl)
```

插件终止：

```text
VideoParserPlugin.terminate()
  ↓
_shutdown_delayed_cleanups()
  ↓
admin_cookie_assist.shutdown()
  ↓
download_manager.shutdown()
  ↓
cleanup_marked_in(cache_dir)
```

`DownloadManager.shutdown()` 会设置 `_shutting_down`，取消 `_active_tasks` 快照并等待任务结束。

## 四、数据流

### 4.1 metadata 字段分组

解析器产出：

```text
url/source_url/platform/parser_name
title/author/desc/timestamp
video_urls/image_urls
video_headers/image_headers
video_force_download/video_force_downloads
access_status/restriction_type/restriction_label
can_access_full_video/is_preview_only/access_message
timelength_ms/available_length_ms
hot_comments
translation_target_language/_translated_fields
use_image_proxy/use_video_proxy/proxy_url
error
```

Pixiv 解析器还会附加 `pixiv_illust_id`、`pixiv_user_id`、`pixiv_x_restrict`、`pixiv_ai_type`、`pixiv_sanity_level` 和 `pixiv_page_count`，用于保留作品访问限制与分页信息。

下载层回填：

```text
file_paths
video_sizes
video_status_codes/image_status_codes
video_modes/image_modes
video_skip_reasons/image_skip_reasons
media_cache_dir_available
max_video_size_mb/total_video_size_mb
video_count/image_count
has_valid_media/use_local_files
exceeds_max_size/has_access_denied
failed_video_count/failed_image_count
```

文件 Token 层回填：

```text
use_file_token_service
file_token_urls
```

节点层消费：

```text
_enable_text_metadata -> 文本元数据 Plain / 热评 Plain / 翻译 Plain
_text_metadata_fields -> 标题 / 作者 / 发布时间 / 原始链接 / 简介正文的展示与翻译输入
_enable_rich_media + video_modes/image_modes + file_paths/file_token_urls/video_urls/image_urls -> Video/Image
```

### 4.2 文件流转

```text
媒体 URL
  ↓
DownloadManager 决策
  ├─ local -> cache_dir/{platform}_{url_hash}_{timestamp}_{nonce}/video_N.* 或 image_N.*
  ├─ direct -> 不写文件
  └─ skip -> 不写文件
  ↓
cache_marker.stamp_subdir() 写 .astrbot_media_parser
  ↓
节点构建
  ├─ relay token URL（媒体文件或渲染 PNG）
  ├─ fromFileSystem()
  └─ fromURL()
  ↓
发送
  ↓
main.py finally 统一清理本次文件
  ├─ relay -> 延迟清理
  └─ 普通 -> 立即清理
  ↓
周期/admin clean -> 在独占清理锁内 cleanup_marked_in(cache_dir)
```

DASH 临时 `.m4s` 在合并后由 DASH 处理器清理；M3U8 临时分片目录由 M3U8 处理器在 finally 中清理。

### 4.3 代理流转

配置来源：

```text
proxy.address
proxy.tiktok
proxy.xiaoheihe_video
proxy.steam.parse
proxy.steam.image
proxy.steam.video
proxy.pixiv
proxy.twitter.parse
proxy.twitter.image
proxy.twitter.video
```

解析器初始化时接收代理配置：

- `TikTokParser`：TikTok 解析和媒体代理。
- `XiaoheiheParser`：视频代理。
- `SteamParser`：Steam 官方接口解析；启用小黑盒路径时复用 `XiaoheiheParser` 的游戏详情能力，并分别控制详情解析、图片下载和视频下载代理。
- `TwitterParser`：Twitter/X 解析、图片、视频代理。
- `PixivParser`：Pixiv Web Ajax API 解析和图片下载共用同一代理开关。

解析结果写入：

```text
use_image_proxy
use_video_proxy
proxy_url
```

下载阶段代理优先级：

```text
metadata.proxy_url > ConfigManager.proxy.address
```

然后按媒体类型读取 `use_image_proxy` 或 `use_video_proxy` 决定是否传给 aiohttp。


## 五、并发与异常

### 5.1 并发模型

- `ParserManager.parse_text()` 对去重后的链接并发解析。
- Pixiv 等平台解析器使用 `Config.PARSER_MAX_CONCURRENT` 控制单平台解析并发，Pixiv 每个作品会依次请求元信息和分页图片接口。
- `main.py` 在至少一条 metadata 启用富媒体输出时并发处理各条 metadata；普通开场语由锁保护的一次性回调按首个可发送媒体触发，ZIP 流程使用独立的打包文本但共享开关。
- `DownloadManager` 使用实例级 `_download_semaphore` 限制所有本地媒体下载总并发。
- Range 下载在严格能力探测后使用分片级 semaphore；服务器忽略 Range 时不会读取 N 份完整正文。
- DASH 音视频子流并发下载。
- M3U8 分片下载内部使用独立分片并发上限。
- B站管理员协助登录和 relay/ZIP 延迟清理都是受观察的后台任务。延迟文件会写持久过期标记，热重载取消任务后仍能由后续周期扫描回收。

### 5.2 异常处理

- 解析阶段：`SkipParse` 跳过；普通异常生成 error metadata；`CancelledError` 继续抛出。
- Pixiv Ajax 返回 HTML 时会在 HTTP 状态抛错前识别 Cloudflare 防护页，避免把拦截页当作 JSON 处理。
- 下载阶段：单个候选失败会尝试下一个候选；媒体项全部失败写入 skip reason；本条 metadata 全部媒体失败时清理对应缓存子目录。
- 大小限制：普通视频下载前预检，DASH/M3U8/强制缓存视频下载后再兜底检查，超限会删除文件并置为 `skip`。
- 发送阶段：独立节点采用 best-effort，部分失败会给用户明确提示；预期节点全部发送失败时抛出聚合错误，不再记录虚假的“发送完成”。主发送异常始终进入 finally 清理。
- 外部依赖：DASH/M3U8/图片转换涉及 ffmpeg，TikTok 涉及系统 curl，文本元数据图片渲染依赖 Pillow 和可用中文字体；超时或取消路径会终止并回收子进程。
