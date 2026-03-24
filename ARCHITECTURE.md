# 架构文档

## 一、整体框架

### 1.1 系统概述

本项目是一个流媒体平台链接解析插件，主要功能是自动识别消息中的媒体链接，解析获取媒体元数据和直链，并转换为可发送的媒体消息。

### 1.2 核心模块架构

```
astrbot_plugin_media_parser/
├── main.py                          # 插件主入口
├── run_local.py                     # 本地测试工具脚本
├── core/
│   ├── config_manager.py            # 配置管理器（含 dataclass 分组：Trigger/Message/Permission/Download/Proxy/Bilibili/Relay/Admin）
│   ├── logger.py                    # 统一日志打印器
│   ├── types.py                     # 类型定义（MediaMetadata / LinkBuildMeta / BuildAllNodesResult）
│   ├── constants.py                 # 常量定义
│   ├── storage/                     # 存储与缓存管理模块
│   │   ├── __init__.py              # 导出 cleanup_file/cleanup_files/cleanup_directory/CacheRegistry/stamp_subdir/register_files_with_token_service
│   │   ├── file_cleaner.py          # 文件清理工具
│   │   ├── file_token.py            # 文件Token服务集成（注册已下载媒体为可回调的临时URL）
│   │   └── cache_registry.py        # 缓存目录注册表（跨配置追踪 + 安全清理）
│   ├── parser/                      # 解析器模块
│   │   ├── manager.py               # 解析器管理器
│   │   ├── router.py                # 链接路由分发器
│   │   ├── utils.py                 # 解析器工具函数（含 extract_url_from_card_data）
│   │   ├── runtime_manager/         # 平台运行时管理模块（Cookie/登录态）
│   │   │   └── bilibili/
│   │   │       └── auth.py          # B站登录态运行时（BilibiliAuthRuntime）
│   │   └── platform/                # 各平台解析器实现
│   │       ├── base.py              # 解析器基类
│   │       ├── bilibili.py          # B站解析器
│   │       ├── douyin.py            # 抖音解析器
│   │       ├── kuaishou.py          # 快手解析器
│   │       ├── weibo.py             # 微博解析器
│   │       ├── xiaohongshu.py       # 小红书解析器
│   │       ├── xiaoheihe.py         # 小黑盒解析器
│   │       └── twitter.py           # 推特解析器
│   ├── downloader/                  # 下载器模块
│   │   ├── manager.py               # 下载管理器
│   │   ├── router.py                # 媒体下载路由
│   │   ├── utils.py                 # 下载器工具函数
│   │   ├── validator.py             # 媒体验证器
│   │   └── handler/                 # 各类型下载处理器
│   │       ├── base.py              # 下载器基础工具（流式下载、Range并发下载）
│   │       ├── image.py             # 图片下载器
│   │       ├── normal_video.py      # 普通视频下载器
│   │       ├── range_downloader.py  # Range下载封装器（range:前缀 → Range + 降级normal）
│   │       ├── dash.py              # DASH音视频下载器（video+audio分别下载 + ffmpeg合并）
│   │       └── m3u8.py              # M3U8流媒体下载器
│   ├── interaction/                 # 管理员交互模块
│   │   ├── base.py                  # 管理员协助基类（AdminAssistManager）
│   │   └── platform/
│   │       └── bilibili/
│   │           └── cookie_assist.py # B站Cookie协助登录管理器
│   └── message_adapter/             # 消息适配器模块
│       ├── __init__.py              # 暴露 Sender 和 Builder
│       ├── node_builder.py          # 节点构建器
│       └── sender.py                # 消息发送器
```

### 1.3 模块职责

#### 1.3.1 主入口模块 (main.py)
- **VideoParserPlugin**: 插件主类
  - 初始化所有管理器
  - 监听消息事件
  - 协调各模块工作流程
  - 处理插件生命周期

#### 1.3.2 核心支撑组件
- **ConfigManager** (config_manager.py): 配置管理器
  - 解析配置文件，管理解析器启用状态与下载配置
  - 配置按领域分组为 dataclass：`TriggerConfig`、`MessageConfig`、`PermissionConfig`、`DownloadConfig`、`ProxyConfig`、`BilibiliEnhancedConfig`、`MediaRelayConfig`、`AdminConfig`
  - `PermissionConfig.check()` 提供权限判定（黑白名单优先级）
  - `TriggerConfig.should_parse()` / `has_keyword()` 提供触发判定
  - 提供类型安全的解析辅助方法（`_parse_positive_int`、`_parse_non_negative_float` 等）
- **Logger** (logger.py): 日志记录器
  - 导出全局统一的 `logger` 对象，方便各模块导入使用
- **Types** (types.py): 类型模块
  - `MediaMetadata`：解析→下载→中转全流程 TypedDict，按阶段分组字段
  - `LinkBuildMeta`：节点构建阶段的辅助元数据
  - `BuildAllNodesResult`：`build_all_nodes` 的 NamedTuple 返回类型

#### 1.3.3 解析器模块 (parser/)
- **ParserManager**: 解析器管理器
  - 管理所有解析器实例
  - 协调链接提取和解析流程
  - 处理解析结果聚合

- **LinkRouter**: 链接路由分发器
  - 从文本中提取所有可解析链接
  - 为每个链接匹配对应的解析器
  - 过滤直播链接和重复链接

- **BaseVideoParser**: 解析器基类
  - 定义解析器接口规范
  - 各平台解析器继承此基类

- **平台解析器** (platform/)
  - 实现各平台特定的链接识别和解析逻辑
  - 提取媒体元数据（标题、作者、视频URL、图片URL等）
  - 处理平台特定的请求头和参数

- **运行时管理器** (runtime_manager/)
  - 管理平台辅助运行时（Cookie/登录态等）
  - 当前包含 `BilibiliAuthRuntime`，供 `BilibiliParser` 调用

#### 1.3.4 下载器模块 (downloader/)
- **DownloadManager**: 下载管理器
  - 管理媒体下载流程
  - 处理视频大小验证
  - 管理并发下载（使用Semaphore控制）
  - 决定使用直链还是本地文件
  - 管理活动会话和任务（用于shutdown时清理）
  - 处理代理配置（从元数据中读取平台特定的代理设置）

- **Router**: 媒体下载路由
  - 检测媒体类型（DASH/M3U8/图片/视频）
  - 按 `dash:` → `m3u8:` → `range:` → 普通 的优先级路由到对应下载处理器

- **下载处理器** (handler/)
  - **Base Handler**: 基础下载工具
    - `range_download_file`：通用 Range 并发下载（探测大小、分片、seek+write）
    - `download_media_from_url`：通用流式下载
    - `download_media_stream`：底层流写入
  - **Image Handler**: 图片下载器
  - **Normal Video Handler**: 普通视频下载器（流式下载）
  - **Range Downloader Handler**: Range 下载封装器（`range:` 前缀 → Range 并发 → 降级 normal_video）
  - **DASH Handler**: DASH 音视频下载器（拆分 video/audio 并发下载 + ffmpeg 合并；各子流可独立带 `range:` 前缀加速）
  - **M3U8 Handler**: M3U8流媒体下载器（支持FFmpeg转换）

- **Validator**: 媒体验证器
  - 验证媒体URL可访问性
  - 获取媒体大小信息
  - 检测访问权限问题（403 Forbidden）
  - 验证响应Content-Type
  - 处理HEAD请求失败时的GET回退

#### 1.3.5 消息适配器模块 (message_adapter/)
- **NodeBuilder**: 节点构建器
  - 构建文本节点（标题、作者、描述等）
  - 构建媒体节点（图片、视频）
  - 处理打包逻辑（大视频单独发送）

- **MessageSender**: 消息发送器
  - 获取发送者信息（`get_sender_info`）
  - 打包模式发送（使用Nodes）
  - 非打包模式发送（独立发送）
  - 处理大媒体单独发送逻辑

#### 1.3.6 管理员交互模块 (interaction/)
- **AdminAssistManager**: 管理员协助基类
  - 管理管理员私聊会话标识
  - 提供 `try_update_admin_origin`、`_send_private_text`、`shutdown` 等通用能力
  - 定义 `handle_admin_reply`、`trigger_assist_request` 抽象接口

- **BilibiliAdminCookieAssistManager**: B站Cookie协助登录管理器
  - 继承 `AdminAssistManager`
  - 位于 `interaction/platform/bilibili/cookie_assist.py`
  - 当B站Cookie不可用时，后台触发管理员确认 → 发送登录链接/二维码 → 轮询登录状态
  - 管理员发送可解析链接时不拦截消息（仅拦截纯文本回复）

#### 1.3.7 存储与工具模块 (storage/)
- **FileCleaner** (storage/file_cleaner.py): 文件清理工具
  - 清理临时文件
  - 清理缓存目录
  - 删除文件后自动移除空父目录（感知 `.astrbot_media_parser` 标记文件：若目录仅剩标记则一并清除）
  - 文件清理统一由 main.py 的 finally 块调度，node_builder/sender 不参与清理

- **FileToken** (storage/file_token.py): 文件Token服务集成
  - 将已下载的媒体文件注册到 AstrBot 文件 Token 服务
  - 生成可回调的临时 URL，供消息平台拉取媒体

- **CacheRegistry** (storage/cache_registry.py): 缓存目录注册表
  - 持久化记录本插件使用过的所有缓存路径（JSON 文件，存于系统临时目录）
  - 在媒体子目录中放置标记文件（`.astrbot_media_parser`）标识归属
  - 安全清理：仅删除带标记的子目录，不触碰无标记内容或根目录
  - 管理员主动清理 / 插件终止时自动清理

- **Constants**: 常量定义
  - 超时时间
  - 大小限制
  - 默认配置值

#### 1.3.8 本地测试工具 (run_local.py)
- **run_local.py**: 本地测试工具脚本
  - 提供交互式命令行界面
  - 支持输入链接并解析
  - 支持用户确认后下载媒体
  - 显示解析和下载统计信息
  - 支持代理配置和调试模式
  - 用于本地开发和调试

#### 1.3.9 资源管理
- **DownloadManager.shutdown()**: 资源清理方法
  - 关闭所有活动的aiohttp会话
  - 取消所有正在进行的下载任务（含 `_batch_download_media` 内部任务）
  - 在插件terminate时调用
- **storage 模块**: 文件与缓存清理
  - 清理临时文件（图片）
  - 清理视频文件（含 DASH 临时 .m4s 文件）
  - 删除文件后自动移除空的缓存子目录（标记感知：仅剩 `.astrbot_media_parser` 时视为空目录一并删除）
  - 预下载模式下全部下载失败时，由 `DownloadManager` 主动清理空壳子目录
  - CacheRegistry 追踪所有缓存根目录，在插件终止或管理员手动清理时安全清理带标记的子目录
- **文件Token服务模式**: 延迟清理
  - 当 `use_file_token_service` 启用时，文件在发送后延迟 `file_token_ttl` 秒清理
  - 延迟清理仅清理当前请求的文件，不做全局目录扫描，避免并发请求间竞态
  - 确保消息平台有足够时间通过回调 URL 拉取文件

## 二、程序执行链

### 2.1 完整处理流程

```
消息接收
  ↓
更新管理员私聊会话标识 (AdminAssistManager.try_update_admin_origin)
  ↓
权限检查 → 消息文本提取 → 提取可解析链接
  ├─ 有链接 → 跳过交互处理器，进入解析流程
  └─ 无链接
      ├─ 回复触发模式 → 检查回复消息中的链接（enable_reply_trigger）
      │   └─ 提取到链接 → 进入解析流程
      └─ 无链接 → 检查管理员交互（handle_admin_reply） → 返回
  ↓
判断是否需要解析 (ConfigManager)
  ├─ 自动解析模式 → 直接解析
  └─ 关键词触发模式 → 检查关键词
  ↓
提取链接 (LinkRouter)
  ├─ 遍历所有解析器
  ├─ 提取匹配的链接
  ├─ 过滤直播链接
  └─ 去重处理
  ↓
解析链接 (ParserManager)
  ├─ 并发调用各平台解析器
  ├─ 获取媒体元数据
  └─ 聚合解析结果
  ↓
处理元数据 (DownloadManager)
  ├─ 检查视频大小限制
  ├─ 验证媒体可访问性
  ├─ 决定下载策略
  │   ├─ 预下载模式 → 批量下载所有媒体
  │   └─ 直链模式 → 图片下载到临时目录，视频使用直链
  └─ 处理强制下载标志
  ↓
构建消息节点 (NodeBuilder)
  ├─ 构建文本节点（元数据信息）
  ├─ 构建媒体节点（图片/视频）
  ├─ 判断大媒体（超过阈值）
  └─ 处理打包逻辑
  ↓
发送消息 (MessageSender)
  ├─ 打包模式
  │   ├─ 普通媒体 → Nodes打包发送
  │   └─ 大媒体 → 单独发送
  └─ 非打包模式 → 逐个独立发送
  ↓
清理临时文件 (storage)
  ├─ 文件Token服务模式 → 延迟清理（等待 file_token_ttl 秒）
  └─ 普通模式 → 立即清理
```

### 2.2 详细程序链

#### 2.2.1 消息接收与判断阶段

```
main.py::VideoParserPlugin.auto_parse()
  ↓
admin_cookie_assist.try_update_admin_origin(event)
  ↓
权限与黑白名单检查
  ├─ 检查 白名单/黑名单 使能状态
  ├─ 优先级：个人白名单 > 个人黑名单 > 群组白名单 > 群组黑名单
  └─ 均未命中时，依据“是否启用白名单”开关判定兜底策略：开启则拒绝，关闭则放行
  ↓
提取消息文本
  ├─ 普通消息 → 直接使用 message_str
  └─ QQ小程序卡片 → 提取 qqdocurl 或 jumpUrl
  ↓
提取可解析链接 (extract_all_links)
  ├─ 有链接 → 进入 _should_parse 检查
  └─ 无链接
      ├─ enable_reply_trigger 且回复文本含触发关键词
      │   └─ _try_extract_reply_links()
      │       ├─ 从 Reply.message_str 提取链接
      │       └─ 从 Reply.chain 提取卡片 URL
      │       ├─ 提取到链接 → 进入 _should_parse 检查
      │       └─ 未提取到 → handle_admin_reply → 返回
      └─ 否则 → handle_admin_reply（管理员交互） → 返回
  ↓
main.py::VideoParserPlugin._should_parse()
  ├─ is_auto_parse = True → 返回 True
  └─ 检查 trigger_keywords → 匹配则返回 True
```

#### 2.2.2 链接提取阶段

```
parser::manager::ParserManager.extract_all_links()
  ↓
parser::router::LinkRouter.extract_links_with_parser()
  ├─ 检查 "原始链接：" 标记 → 跳过解析
  ├─ 遍历所有解析器
  │   └─ parser::platform::BaseVideoParser.extract_links()
  ├─ 过滤直播链接 (utils::is_live_url)
  ├─ 按位置排序
  └─ 去重处理
  ↓
返回 (链接, 解析器) 元组列表
```

#### 2.2.3 链接解析阶段

```
main.py::VideoParserPlugin.auto_parse()
  ↓
创建 aiohttp.ClientSession
  ↓
parser::manager::ParserManager.parse_text()
  ├─ 提取唯一链接（去重）
  ├─ 并发调用各解析器
  │   └─ parser::platform::BaseVideoParser.parse()
  │       ├─ 请求平台API
  │       ├─ 解析响应数据
  │       └─ 提取元数据
  └─ 聚合解析结果
  ↓
返回元数据列表
  ├─ url: 原始链接
  ├─ source_url: 原始来源链接（可选，如短链展开前的地址）
  ├─ title: 标题
  ├─ author: 作者
  ├─ desc: 描述（可选）
  ├─ timestamp: 发布时间（可选，格式：Y-M-D）
  ├─ platform: 平台标识（解析器名称）
  ├─ video_urls: 视频URL列表（二维列表，可能包含dash:、range:、m3u8:等可组合前缀）
  ├─ image_urls: 图片URL列表（二维列表）
  ├─ video_headers: 视频请求头
  ├─ image_headers: 图片请求头
  ├─ video_force_download: 是否强制下载
  ├─ access_status: 访问状态（如 "full"、"preview" 等，B站会员/付费限制）
  ├─ restriction_type: 限制类型（可选）
  ├─ restriction_label: 限制标签（可选）
  ├─ can_access_full_video: 是否可访问完整视频
  ├─ is_preview_only: 是否仅有预览片段
  ├─ access_message: 访问信息描述（如时长受限说明）
  ├─ timelength_ms: 视频总时长（毫秒）
  ├─ available_length_ms: 当前可访问时长（毫秒）
  ├─ hot_comments: 热评列表（可选，List[Dict]，包含 username/uid/likes/time/message）
  ├─ use_image_proxy: 图片是否使用代理（Twitter等平台）
  ├─ use_video_proxy: 视频是否使用代理（Twitter、小黑盒等平台）
  └─ proxy_url: 代理地址（可选，平台特定）
  ↓
触发B站Cookie协助检查
  └─ _trigger_bilibili_cookie_assist_if_needed()
  ↓
检查有效元数据
  └─ 至少存在一条非 error 的元数据包含 video_urls、image_urls 或 access_message
  ↓
发送开场语（若 enable_opening_msg 启用）
```

#### 2.2.4 元数据处理阶段

```
main.py::VideoParserPlugin.auto_parse()
  ↓
并发处理每个元数据
  ↓
downloader::manager::DownloadManager.process_metadata()
  ├─ 检查视频大小限制
  │   └─ validator::get_video_size()
  │       └─ 如果超过限制 → 返回错误元数据
  │
  ├─ 预下载模式 (effective_pre_download = True)
  │   ├─ 说明：effective_pre_download = pre_download_all_media && 缓存目录可用
  │   ├─ 构建媒体项列表（包含代理配置信息）
  │   ├─ 批量下载所有媒体（并发控制，任务注册到 _active_tasks 以支持 shutdown 取消）
  │   │   └─ downloader::router::download_media()
  │   │       ├─ 检测媒体类型（通过URL特征或前缀）
  │   │       └─ 路由到对应下载器（按 dash: → m3u8: → range: → 普通 优先级）
  │   │           ├─ dash:前缀 → handler::dash（video+audio 分别下载+ffmpeg合并，子流可带 range: 加速）
  │   │           ├─ m3u8:前缀或.m3u8扩展名 → handler::m3u8（FFmpeg转换，支持代理）
  │   │           ├─ range:前缀 → handler::range_downloader（并发Range请求，降级normal_video）
  │   │           ├─ image → handler::image（支持代理）
  │   │           └─ video → handler::normal_video（支持代理）
  │   ├─ 处理下载结果（统计成功/失败数量）
  │   ├─ 处理video_force_download标志（全部失败时跳过视频）
  │   ├─ 全部下载失败时主动清理空壳缓存子目录（cleanup_directory）
  │   └─ 更新元数据（file_paths, video_sizes, has_valid_media等）
  │
  └─ 直链模式 (effective_pre_download = False)
      ├─ 处理 video_force_download 标志
      │   └─ 如果为True且未启用预下载 → 跳过视频
      ├─ 检查视频可访问性
      │   └─ validator::get_video_size()（并发检查所有视频）
      │       └─ 检测403访问被拒绝
      ├─ 下载图片到临时目录
      │   └─ downloader::manager::DownloadManager._download_images()
      │       └─ 并发下载所有图片（支持代理配置）
      ├─ 构建对齐的 file_paths（前置 [None]*视频数 + 图片路径，确保与 build_media_nodes 的 file_idx 一致）
      └─ 视频超限时立即清理已下载的图片临时文件
  ↓
返回处理后的元数据
```

#### 2.2.5 节点构建阶段

```
main.py::VideoParserPlugin.auto_parse()
  ↓
message_adapter::node_builder::build_all_nodes(enable_text_metadata)
  ├─ 遍历所有元数据
  │   └─ message_adapter::node_builder::build_nodes_for_link()
  │       ├─ 构建文本节点（受 enable_text_metadata 控制）
  │       │   └─ message_adapter::node_builder::build_text_node()
  │       │       ├─ 标题、作者、描述、发布时间
  │       │       ├─ 视频大小信息
  │       │       ├─ 时长/访问状态（access_message、is_preview_only、timelength_ms、available_length_ms）
  │       │       ├─ 热评展示（hot_comments）
  │       │       ├─ 错误信息（解析失败、403被拒、直链无效媒体、超大小限制）
  │       │       ├─ 下载失败统计（failed_video_count、failed_image_count）
  │       │       └─ 原始链接
  │       │
  │       └─ 构建媒体节点
  │           └─ message_adapter::node_builder::build_media_nodes()
  │               ├─ 判断是否使用本地文件（use_local_files）
  │               ├─ 构建视频节点
  │               │   ├─ 本地文件 → Video.fromFileSystem()
  │               │   └─ 直链 → Video.fromURL()（去除range:或m3u8:前缀）
  │               └─ 构建图片节点
  │                   ├─ 本地文件 → Image.fromFileSystem()
  │                   └─ 直链 → Image.fromURL()
  │
  ├─ 判断大媒体（超过 large_video_threshold_mb）
  └─ 分类文件路径（临时文件、视频文件）
  ↓
返回 (all_link_nodes, link_metadata, temp_files, video_files)
```

#### 2.2.6 消息发送阶段

```
main.py::VideoParserPlugin.auto_parse()
  ↓
判断发送模式
  ├─ 打包模式 (is_auto_pack = True)
  │   └─ message_adapter::sender::MessageSender.send_packed_results()
  │       ├─ 分离普通媒体和大媒体
  │       ├─ 普通媒体打包发送
  │       │   ├─ 纯图片图集 → 文本和图片分组
  │       │   ├─ 混合内容 → 每个节点单独打包
  │       │   └─ 使用 Nodes 发送
  │       └─ 大媒体单独发送
  │           └─ message_adapter::sender::MessageSender.send_large_media_results()
  │               ├─ 发送提示信息
  │               └─ 逐个发送节点
  │
  └─ 非打包模式 (is_auto_pack = False)
      └─ message_adapter::sender::MessageSender.send_unpacked_results()
          ├─ 遍历所有链接节点
          ├─ 纯图片图集 → 文本和图片分组发送
          └─ 其他内容 → 逐个节点独立发送
  ↓
发送完成
```

#### 2.2.7 文件清理阶段

```
main.py::VideoParserPlugin.auto_parse()
  ↓
finally 块
  ↓
判断清理模式
  ├─ 文件Token服务模式 (use_file_token_service)
  │   └─ asyncio.create_task(_delayed_cleanup(files, file_token_ttl))
  │       ├─ 等待 file_token_ttl 秒
  │       └─ storage::cleanup_files()（仅清理当前请求的文件，不做全局扫描）
  └─ 普通模式
      └─ storage::cleanup_files()
          ├─ 清理临时文件（图片）
          ├─ 清理视频文件
          └─ 自动移除空的缓存子目录（_try_remove_empty_parent，标记感知）
  ↓
清理完成

注意：
- 所有媒体文件（视频和图片）均在 main.py 的 finally 块中统一清理
- sender 和 node_builder 不参与文件清理，仅负责发送/构建
- DASH下载的临时.m4s文件在合并后由dash handler内部清理
- 文件Token服务模式下，所有文件延迟 file_token_ttl 秒后再清理
- 逐请求清理不调用 cleanup_marked_in（避免并发竞态），全局清理仅在 terminate() 和管理员手动触发时执行
- 预下载全部失败时，DownloadManager 在返回元数据前主动 cleanup_directory 清理空壳子目录
```

#### 2.2.8 插件终止阶段

```
main.py::VideoParserPlugin.terminate()
  ↓
interaction::BilibiliAdminCookieAssistManager.shutdown()
  └─ 关闭管理员交互会话
  ↓
downloader::manager::DownloadManager.shutdown()
  ├─ 设置 _shutting_down 标志
  ├─ 取消所有正在进行的下载任务（含 _batch_download_media 内部任务）
  └─ 清理任务列表
  ↓
CacheRegistry.cleanup_marked_in(cache_dir)
  └─ 清理缓存目录中带标记的子目录
  ↓
终止完成
```

### 2.3 异常处理链

```
解析阶段异常
  ├─ SkipParse → 跳过该链接
  ├─ 其他异常 → 记录错误，返回错误元数据
  └─ 继续处理其他链接
  ↓
下载阶段异常
  ├─ 单个媒体下载失败 → 记录警告，继续其他媒体
  ├─ 全部媒体下载失败 → 标记 has_valid_media = False，主动清理空壳缓存子目录
  ├─ asyncio.CancelledError → 由 process_single 捕获，保留已回填的元数据状态
  └─ 继续构建节点（可能只有文本节点）
  ↓
发送阶段异常
  ├─ 单个节点发送失败 → 记录警告，继续发送其他节点
  └─ 确保文件清理执行
```

### 2.4 并发处理链

```
链接解析并发
  ├─ asyncio.gather() 并发调用所有解析器
  └─ 每个解析器独立处理，互不影响
  ↓
元数据处理并发
  ├─ asyncio.gather() 并发处理所有元数据
  └─ 每个元数据独立处理
  ↓
视频大小检查并发
  ├─ asyncio.gather() 并发检查所有视频大小
  └─ 每个视频独立检查，检测403状态码
  ↓
媒体下载并发
  ├─ Semaphore 控制最大并发数（max_concurrent_downloads）
  ├─ 批量下载时并发下载所有媒体项（任务注册到 _active_tasks，支持 shutdown 取消）
  ├─ Range视频下载：内部使用Semaphore控制Range请求并发数
  ├─ DASH视频下载：video+audio并发下载（各子流可独立走Range或普通），完成后ffmpeg合并
  ├─ M3U8视频下载：内部使用Semaphore控制分片下载并发数
  └─ 单个媒体失败不影响其他媒体
  ↓
图片下载并发（直链模式）
  ├─ asyncio.gather() 并发下载所有图片
  └─ 每个图片独立下载，支持代理配置
```

## 三、关键设计模式

### 3.1 管理器模式
- **ParserManager**: 统一管理所有解析器
- **DownloadManager**: 统一管理下载流程

### 3.2 路由模式
- **LinkRouter**: 根据URL特征路由到对应解析器
- **Download Router**: 根据媒体类型路由到对应下载器

### 3.3 策略模式
- **下载策略**: 预下载模式 vs 直链模式
- **发送策略**: 打包模式 vs 非打包模式

### 3.4 模板方法模式
- **BaseVideoParser**: 定义解析器接口，各平台实现具体逻辑
- **Base Download Handler**: 定义下载器接口，各类型实现具体逻辑

## 四、数据流

### 4.1 元数据流转

```
原始消息文本
  ↓
链接提取 → (链接, 解析器) 列表
  ↓
解析结果 → 元数据字典
  ├─ url, source_url
  ├─ title, author, desc, timestamp
  ├─ video_urls: List[List[str]]
  ├─ image_urls: List[List[str]]
  ├─ video_headers, image_headers
  ├─ video_force_download
  ├─ access_status, restriction_type, restriction_label
  ├─ can_access_full_video, is_preview_only, access_message
  ├─ timelength_ms, available_length_ms
  └─ hot_comments: List[Dict]
  ↓
下载处理 → 增强元数据
  ├─ file_paths: List[str]（本地文件路径列表）
  ├─ video_sizes: List[Optional[float]]（视频大小列表，MB）
  ├─ max_video_size_mb: Optional[float]（最大视频大小）
  ├─ total_video_size_mb: float（总视频大小）
  ├─ video_count: int（视频数量）
  ├─ image_count: int（图片数量）
  ├─ has_valid_media: bool（是否有有效媒体）
  ├─ use_local_files: bool（是否使用本地文件）
  ├─ exceeds_max_size: bool（是否超过大小限制）
  ├─ has_access_denied: bool（是否有403访问被拒绝）
  ├─ failed_video_count: int（失败的视频数量）
  └─ failed_image_count: int（失败的图片数量）
  ↓
节点构建 → 节点列表
  ├─ Plain 节点（文本信息）
  ├─ Image 节点（图片）
  └─ Video 节点（视频）
  ↓
消息发送 → 最终消息
```

### 4.2 文件流转

```
媒体URL（可能包含dash:、range:、m3u8:等可组合前缀）
  ↓
下载处理
  ├─ 预下载模式 → 缓存目录
  │   └─ {platform}_{url_hash}_{timestamp}/media_{index}.{ext}
  │       ├─ DASH视频：video+audio分别下载（可带range:加速）→ ffmpeg合并
  │       ├─ 普通视频：支持Range并发下载
  │       ├─ M3U8视频：分片下载+FFmpeg合并
  │       └─ 图片：完整下载
  └─ 直链模式（仅图片）→ 临时目录
      └─ temp_image_{index}.{ext}
  ↓
节点构建
  ├─ 本地文件 → fromFileSystem()
  └─ 直链 → fromURL()（strip_media_prefixes 剥离所有前缀）
  ↓
文件Token服务注册（若启用 use_file_token_service）
  └─ 遍历 file_paths，注册到 file_token_service → 获取回调 URL
  ↓
消息发送
  ├─ 打包模式：普通媒体发送后清理视频文件
  ├─ 非打包模式：每个链接发送后清理视频文件
  └─ 所有临时文件在finally块中统一清理
  ↓
文件清理
  ├─ 文件Token服务模式 → 延迟 file_token_ttl 秒后删除（仅当前请求的文件，不做全局扫描）
  └─ 普通模式 → 立即删除临时文件和视频文件 → 自动移除空父目录（标记感知）
  全局兜底：terminate() 时 CacheRegistry.cleanup_marked_in() 清理所有残留
```

### 4.3 代理流转

```
配置中的代理设置
  ├─ proxy_addr: 全局代理地址
  ├─ twitter.parse: Twitter解析是否使用代理
  ├─ twitter.image: Twitter图片是否使用代理
  ├─ twitter.video: Twitter视频是否使用代理
  └─ xiaoheihe.video: 小黑盒视频是否使用代理
  ↓
解析器初始化
  ├─ TwitterParser: 接收代理配置参数
  └─ XiaoheiheParser: 接收代理配置参数
  ↓
解析阶段
  └─ 元数据中包含 use_image_proxy, use_video_proxy, proxy_url
  ↓
下载阶段
  ├─ 从元数据读取代理配置
  ├─ 优先级：元数据中的proxy_url > 全局proxy_addr
  └─ 根据use_image_proxy和use_video_proxy决定是否使用代理
```
