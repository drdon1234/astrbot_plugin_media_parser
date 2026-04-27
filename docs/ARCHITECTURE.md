# 架构文档

本文件按当前项目真实实现描述插件边界、模块职责和主流程。字段逐阶段如何变化可结合 `docs/WORKFLOW_TRACE.md` 阅读；平台解析细节见 `docs/PARSER_METHOD_MEMO.md`。

## 一、整体框架

### 1.1 系统概述

本项目是 AstrBot 流媒体平台链接解析插件。插件监听消息事件，识别可解析平台链接，调用对应平台解析器提取文本元数据和媒体候选 URL，再按缓存目录能力与媒体类型决定 `local/direct/skip` 发送模式，最终构建 AstrBot 消息节点并完成清理。

当前支持的平台解析器包括：

- B站：普通视频、番剧、动态/opus，支持 Cookie 增强、扫码登录运行时、热评。
- 抖音：短链、视频和图集分享页。
- TikTok：短链、视频和图集作品页，使用独立解析器和代理开关。
- 快手。
- 微博，支持热评。
- 小红书，支持热评。
- 小黑盒。
- Twitter/X，优先 FxTwitter/FxEmbed，服务不可用时回退 Guest GraphQL。

### 1.2 核心模块结构

```text
astrbot_plugin_media_parser/
├── main.py                          # AstrBot 插件入口与生命周期
├── run_local.py                     # 本地交互式调试脚本
├── _conf_schema.json                # AstrBot 配置 schema
├── docs/
│   ├── ARCHITECTURE.md              # 当前架构文档
│   ├── WORKFLOW_TRACE.md            # 元数据流转追踪
│   └── PARSER_METHOD_MEMO.md        # 平台解析方法说明
└── core/
    ├── config_manager.py            # 配置解析、默认值、解析器工厂
    ├── constants.py                 # 常量与默认路径/超时/并发值
    ├── logger.py                    # 统一 logger
    ├── types.py                     # MediaMetadata / LinkBuildMeta / BuildAllNodesResult
    ├── parser/
    │   ├── manager.py               # ParserManager，并发解析与结果归一
    │   ├── router.py                # LinkRouter，链接提取、去重、直播过滤
    │   ├── utils.py                 # 通用工具、卡片 URL 提取、直播判断、请求头构建
    │   ├── runtime_manager/
    │   │   └── bilibili/auth.py     # BilibiliAuthRuntime，Cookie 校验与扫码登录
    │   └── platform/                # 各平台解析器
    ├── downloader/
    │   ├── manager.py               # DownloadManager，媒体模式决策与下载调度
    │   ├── router.py                # 下载路由：dash/m3u8/image/video/range
    │   ├── utils.py                 # 缓存路径、扩展名、URL 前缀、Content-Type 工具
    │   ├── validator.py             # 媒体预检、大小探测、响应校验
    │   └── handler/
    │       ├── base.py              # 通用流式下载、Range 下载、重试
    │       ├── normal_video.py      # 普通视频缓存下载
    │       ├── range_downloader.py  # range: 前缀下载封装，失败降级普通下载
    │       ├── dash.py              # DASH 音视频下载与 ffmpeg 合并
    │       ├── m3u8.py              # M3U8 分片下载、拼接、音视频合并
    │       └── image.py             # 图片下载与可选 ffmpeg 转 PNG
    ├── message_adapter/
    │   ├── node_builder.py          # Plain/Image/Video 节点构建
    │   └── sender.py                # 打包/非打包发送
    ├── storage/
    │   ├── __init__.py              # 导出清理、标记、文件 Token 注册能力
    │   ├── file_cleaner.py          # 文件与空父目录清理
    │   ├── cache_marker.py          # .astrbot_media_parser 标记与安全清理
    │   └── file_token.py            # AstrBot file_token_service 集成
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
- 所有平台均为 `关闭` 时：`main.py::auto_parse()` 直接跳过，不进入解析。
- 开场语只在富媒体流程中触发，且只有出现可发送媒体时才发送；如果已发送开场语但最终没有节点，会补发空结果说明。

#### 缓存目录

`download.cache_dir` 是媒体缓存根目录，但非 Docker 环境不会直接使用用户配置值：

- Docker 环境：使用配置值；为空时使用 `Config.DEFAULT_CACHE_DIR`。
- 非 Docker 环境：优先使用 AstrBot 数据目录下的 `plugin_data/astrbot_plugin_media_parser/cache`，取不到时回退当前工作目录的 `cache/`。
- `run_local.py`：固定使用项目根目录下的 `cache/`，并关闭缓存子目录标记写入。

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

- 初始化 `ConfigManager`、`ParserManager`、`DownloadManager`、`MessageSender`、`BilibiliAdminCookieAssistManager`。
- 监听所有消息事件。
- 执行权限检查、触发判断、卡片 URL 和回复 URL 提取。
- 协调解析、下载、文件 Token 注册、节点构建、发送与清理。
- 在 `terminate()` 中关闭延迟清理任务、管理员交互任务、下载任务，并清理当前缓存根目录下带标记的媒体子目录。

管理员私聊发送 `admin.clean_cache_keyword`，且发送者为 `permissions.admin_id` 时，会触发 `cleanup_marked_in(cache_dir)` 主动清理媒体缓存。

### 2.2 配置管理 `core/config_manager.py`

配置被归一为 dataclass 分组：

- `TriggerConfig`：`auto_parse`、`keywords`、`reply_trigger`，提供 `should_parse()` 和 `has_keyword()`。
- `MessageConfig`：打包、开场语、各平台输出模式、热评开关。
- `PermissionConfig`：管理员、白名单、黑名单，提供 `check()`。
- `DownloadConfig`：大小限制、缓存目录、缓存可用性、下载并发。
- `ProxyConfig`：全局代理、TikTok、小黑盒、Twitter 代理开关。
- `BilibiliEnhancedConfig`：Cookie、最高画质、运行时文件、管理员协助登录。
- `MediaRelayConfig`：文件 Token 中转开关、回调地址、TTL。
- `AdminConfig`：清理关键词和 debug 模式。

`ConfigManager` 会将 `parsers` 的输出模式归一到 `MessageConfig.parser_outputs`。使用 `关闭`、`全部发送`、`仅文本`、`仅富媒体` 四种字符串模式。缺省平台使用 `全部发送`，不同平台之间不互相继承配置。

权限优先级为：管理员直接放行，其次个人白名单、个人黑名单、群组白名单、群组黑名单；均未命中时，白名单开启则拒绝，白名单关闭则放行。管理员 ID 会自动加入用户白名单。

### 2.3 解析器模块 `core/parser/`

`LinkRouter` 负责：

- 跳过含有 `原始链接：` 标记的文本，避免二次解析机器人自己发出的结果。
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
- 可生成登录链接和二维码链接，轮询扫码结果，并保存新凭据。
- `run_local.py` 可在解析前用阻塞式交互完成本地扫码。

`BilibiliAdminCookieAssistManager` 是插件运行时的非阻塞协助流程：

- 只有管理员私聊过机器人后，才有可主动发送的私聊会话标识。
- 当 B站解析器消费到 Cookie 不可用请求后，后台向管理员发送确认消息。
- 管理员回复 `确定` 后发送登录链接/二维码，并后台轮询登录结果。
- 管理员发送可解析链接时会优先进入解析流程，不会被纯文本协助回复处理抢走。

### 2.5 下载器模块 `core/downloader/`

`DownloadManager.process_metadata()` 是下载决策入口。它会把解析器输出归一为：

```text
video_urls: List[List[str]]
image_urls: List[List[str]]
file_paths: List[Optional[str]]
```

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
- `image`：进入图片处理器；非 jpg/jpeg/png 会尝试 ffmpeg 转 PNG。
- 其他：普通视频流式下载。

`validator.py` 负责 HEAD/Range GET 预检、大小提取、Content-Type 检查、HTML/JSON/文本错误响应识别和 403 状态传递。

### 2.6 存储与清理 `core/storage/`

当前实现使用 `cache_marker.py`，没有持久化的 `CacheRegistry` 文件。

- `stamp_subdir(directory)` 在媒体缓存子目录中写 `.astrbot_media_parser`。
- `cleanup_marked_in(root_dir)` 只删除缓存根目录的直接子目录中带标记的条目，不删除根目录，不触碰未标记目录。
- `cleanup_file()` 删除单个文件后尝试删除空父目录；如果父目录仅剩标记文件，会同时删除标记和目录。
- `cleanup_files()` 清理本次构建结果记录的图片和视频文件。
- `cleanup_directory()` 用于全部媒体失败后的空壳子目录清理，或 M3U8 临时目录清理。

文件 Token 中转由 `file_token.py` 实现：

- 仅增强已经存在且模式为 `local` 的文件。
- 优先使用插件配置 `media_relay.callback_url`；为空时回退 AstrBot 全局 `callback_api_base`。
- 注册失败不会改变媒体模式，节点层会回退本地文件。
- `main.py` 会按 `media_relay.ttl` 延迟清理本次文件，延迟任务受插件生命周期管理。

### 2.7 消息适配器 `core/message_adapter/`

`node_builder.py` 负责将 metadata 转成节点：

- 文本节点展示标题、作者、简介、发布时间、访问状态、热评、视频大小、跳过原因、解析错误、原始链接。
- 富媒体节点只消费 `video_modes/image_modes`：`local` 用 Token URL 或本地文件，`direct` 用剥离前缀后的 URL，`skip` 不构建节点。
- 内部先尝试构建富媒体节点，再构建文本节点，这样节点构建失败时可把原因回填到 metadata，文本节点可展示。
- `build_all_nodes()` 返回 `BuildAllNodesResult(all_link_nodes, link_metadata, temp_files, video_files)`。

`sender.py` 负责发送：

- `auto_pack=true`：使用 `Nodes` 打包发送普通媒体；大媒体单独发送。
- `auto_pack=false`：逐链接独立发送。
- 纯图片图集会把文本和图片分组发送；混合内容按节点逐个发送。
- 大媒体判定来自 `download.large_video_threshold_mb` 和当前 metadata 的最大视频大小。

## 三、程序执行链

### 3.1 插件消息流程

```text
main.py::VideoParserPlugin.auto_parse(event)
  ↓
admin_cookie_assist.try_update_admin_origin(event)
  ↓
message.has_any_output()
  ├─ false -> 返回
  └─ true  -> 继续
  ↓
PermissionConfig.check(is_private, sender_id, group_id)
  ├─ false -> 返回
  └─ true  -> 继续
  ↓
管理员清理关键词检查
  ├─ 命中且为管理员私聊 -> cleanup_marked_in(cache_dir) -> 返回
  └─ 未命中 -> 继续
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
存在启用富媒体输出的 metadata?
  ├─ 是 -> 仅对这些 metadata 并发 DownloadManager.process_metadata()
  └─ 否 -> processed_metadata_list = metadata_list
  ↓
media_relay.enable 且该 metadata 启用富媒体 -> register_files_with_token_service()
  ↓
build_all_nodes()
  ↓
按 auto_pack 调用 MessageSender
  ↓
finally 清理本次 temp_files + video_files
  ├─ relay 开启 -> 延迟 media_relay.ttl 秒
  └─ relay 关闭 -> 立即清理
```

有效 metadata 的判定条件是：至少一条结果没有 `error`，且在当前平台输出模式下可能构建节点。富媒体输出开启时需要包含视频或图片；文本输出开启时可由标题、作者、简介、发布时间、访问提示或媒体跳过信息构建文本节点。

### 3.2 链接提取与解析链

```text
文本
  ↓
LinkRouter.extract_links_with_parser()
  ├─ 跳过含 "原始链接：" 的文本
  ├─ 遍历 parser.extract_links()
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
  ├─ range -> Range 并发下载 + 降级普通下载
  ├─ image -> 下载 + 必要时转 PNG
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
  ├─ 判定大媒体
  └─ 分类 temp_files/video_files
  ↓
MessageSender
  ├─ auto_pack=true  -> send_packed_results()
  └─ auto_pack=false -> send_unpacked_results()
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
use_image_proxy/use_video_proxy/proxy_url
error
```

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
_enable_text_metadata -> Plain
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
  ├─ relay token URL
  ├─ fromFileSystem()
  └─ fromURL()
  ↓
发送
  ↓
main.py finally 统一清理本次文件
  ├─ relay -> 延迟清理
  └─ 普通 -> 立即清理
  ↓
terminate/admin clean -> cleanup_marked_in(cache_dir)
```

DASH 临时 `.m4s` 在合并后由 DASH 处理器清理；M3U8 临时分片目录由 M3U8 处理器在 finally 中清理。

### 4.3 代理流转

配置来源：

```text
proxy.address
proxy.tiktok
proxy.xiaoheihe_video
proxy.twitter.parse
proxy.twitter.image
proxy.twitter.video
```

解析器初始化时接收代理配置：

- `TikTokParser`：TikTok 解析和媒体代理。
- `XiaoheiheParser`：视频代理。
- `TwitterParser`：解析、图片、视频代理。

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
- `main.py` 在至少一条 metadata 启用富媒体输出时创建下载处理任务，并用 `asyncio.as_completed()` 按完成顺序处理开场语触发。
- `DownloadManager` 使用实例级 `_download_semaphore` 限制所有本地媒体下载总并发。
- Range 下载内部使用分片级 semaphore。
- DASH 音视频子流并发下载。
- M3U8 分片下载内部使用独立分片并发上限。
- B站管理员协助登录和 relay 延迟清理都是插件生命周期内登记的后台任务。

### 5.2 异常处理

- 解析阶段：`SkipParse` 跳过；普通异常生成 error metadata；`CancelledError` 继续抛出。
- 下载阶段：单个候选失败会尝试下一个候选；媒体项全部失败写入 skip reason；本条 metadata 全部媒体失败时清理对应缓存子目录。
- 大小限制：普通视频下载前预检，DASH/M3U8/强制缓存视频下载后再兜底检查，超限会删除文件并置为 `skip`。
- 发送阶段：单个大媒体节点发送失败记录 warning 后继续；主发送异常会继续进入 finally 清理。
- 外部子进程：DASH/M3U8/图片转换涉及 ffmpeg，TikTok 涉及系统 curl；超时或取消路径会终止并回收子进程。

## 六、本地调试脚本

`run_local.py` 复用解析器、下载器和节点相关数据结构，但不走 AstrBot 消息发送链：

```text
输入链接
  ↓
extract_all_links()
  ↓
prepare_bilibili_cookie_interaction()
  ↓
parse_text()
  ↓
打印 metadata
  ↓
用户确认是否下载
  ↓
DownloadManager.process_metadata()
  ↓
打印下载结果
```

本地调试的差异：

- B站 Cookie 交互在并发解析前阻塞处理。
- 缓存根目录固定为项目根 `cache/`。
- 调用 `set_stamp_subdir_enabled(False)`，本地调试不写 `.astrbot_media_parser` 标记。
- 输出以终端展示为主，不构建 AstrBot 发送链。
