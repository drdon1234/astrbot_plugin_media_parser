# 架构与技术说明文档

## 目录

- [项目概述](#项目概述)
- [系统架构](#系统架构)
- [核心模块](#核心模块)
- [工作流程](#工作流程)
- [技术栈](#技术栈)
- [设计模式](#设计模式)
- [数据流](#数据流)
- [扩展性设计](#扩展性设计)

---

## 项目概述

**astrbot_plugin_video_parser** 是一个 AstrBot 插件，用于自动识别和解析多个流媒体平台的视频/图片链接，将其转换为媒体直链并发送给用户。

### 主要功能

- **多平台支持**：支持 B站、抖音、快手、小红书、推特等主流平台
- **自动识别**：自动识别会话中的视频或图片链接
- **并行解析**：支持多平台并行解析与批量处理
- **智能下载**：根据视频大小自动决定使用直链或本地缓存
- **消息打包**：支持将解析结果打包为消息集合（可配置）

### 支持的平台

| 平台 | 支持的链接类型 | 可解析的媒体类型 |
|------|--------------|----------------|
| B站 | 短链、AV号、BV号、动态长链、动态短链 | 视频、图片 |
| 抖音 | 短链、视频长链、图集长链 | 视频、图片 |
| 快手 | 短链、视频长链 | 视频、图片 |
| 小红书 | 短链、笔记长链 | 视频、图片 |
| 推特 | Twitter/X 链接 | 视频、图片 |

---

## 系统架构

### 整体架构图

```
┌─────────────────────────────────────────────────────────────┐
│                    AstrBot 消息事件                          │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│              VideoParserPlugin (main.py)                     │
│  - 事件监听与过滤                                             │
│  - 配置管理                                                   │
│  - 消息发送控制                                               │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│              ParserManager (core/parser_manager.py)           │
│  - 解析器注册与管理                                           │
│  - 链接提取与去重                                             │
│  - 并行解析调度                                               │
└──────────────┬───────────────────────┬──────────────────────┘
               │                       │
               ▼                       ▼
┌──────────────────────────┐  ┌──────────────────────────────┐
│   LinkRouter             │  │   BaseVideoParser            │
│  (parsers/link_router.py)│  │   (parsers/base_parser.py)   │
│  - 链接匹配               │  │   - 解析器接口定义           │
│  - 解析器路由             │  └───────────┬──────────────────┘
└──────────────────────────┘              │
                                         │
                    ┌────────────────────┼────────────────────┐
                    ▼                    ▼                    ▼
         ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐
         │ BilibiliParser  │  │ DouyinParser    │  │ TwitterParser│
         │ KuaishouParser  │  │ XiaohongshuParser│ │ ...          │
         └─────────────────┘  └─────────────────┘  └──────────────┘
                    │                    │                    │
                    └────────────────────┼────────────────────┘
                                         ▼
┌─────────────────────────────────────────────────────────────┐
│          DownloadManager (core/download_manager.py)          │
│  - 视频大小检查                                               │
│  - 下载策略决策                                               │
│  - 缓存管理                                                   │
└──────────────┬───────────────────────┬──────────────────────┘
               │                       │
               ▼                       ▼
┌──────────────────────────┐  ┌──────────────────────────────┐
│   Downloader             │  │   FileManager                │
│  (core/downloader.py)    │  │  (core/file_manager.py)      │
│  - 媒体下载               │  │  - 文件缓存管理              │
│  - 大小检查               │  │  - 临时文件清理              │
└──────────────────────────┘  └──────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────┐
│          NodeBuilder (core/node_builder.py)                 │
│  - 元数据转换为消息节点                                       │
│  - 消息打包逻辑                                               │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                    AstrBot 消息发送                           │
└─────────────────────────────────────────────────────────────┘
```

### 模块层次结构

```
astrbot_plugin_video_parser/
├── main.py                    # 插件主入口
├── core/                      # 核心功能模块
│   ├── parser_manager.py      # 解析器管理器
│   ├── download_manager.py    # 下载管理器
│   ├── downloader.py          # 下载器实现
│   ├── file_manager.py        # 文件管理
│   ├── node_builder.py       # 节点构建器
│   └── constants.py           # 常量配置
└── parsers/                   # 解析器模块
    ├── base_parser.py         # 解析器基类
    ├── link_router.py         # 链接路由器
    ├── bilibili.py            # B站解析器
    ├── douyin.py              # 抖音解析器
    ├── kuaishou.py            # 快手解析器
    ├── xiaohongshu.py         # 小红书解析器
    └── twitter.py             # 推特解析器
```

---

## 核心模块

### 1. VideoParserPlugin (main.py)

**职责**：插件主入口，负责事件监听、配置管理和消息发送。

**主要功能**：
- 监听 AstrBot 消息事件
- 解析配置参数（触发设置、下载设置、解析器启用等）
- 初始化解析器管理器和下载管理器
- 协调整个解析流程
- 处理消息发送（打包/非打包模式）

**关键方法**：
- `__init__()`: 初始化插件，加载配置，创建管理器实例
- `auto_parse()`: 自动解析消息中的链接
- `_should_parse()`: 判断是否应该解析消息
- `_send_packed_results()`: 发送打包的结果（使用 Nodes）
- `_send_unpacked_results()`: 发送非打包的结果（独立发送）

### 2. ParserManager (core/parser_manager.py)

**职责**：管理所有解析器，负责链接提取、去重和并行解析调度。

**主要功能**：
- 注册和管理解析器实例
- 从文本中提取所有可解析的链接
- 对链接进行去重处理
- 并行调用解析器解析链接
- 异常处理和错误记录

**关键方法**：
- `extract_all_links()`: 从文本中提取所有可解析的链接
- `parse_text()`: 解析文本中的所有链接（并行）
- `parse_url()`: 解析单个URL
- `find_parser()`: 根据URL查找合适的解析器

**设计特点**：
- 使用 `asyncio.gather()` 实现并行解析
- 自动处理解析异常，不会因单个链接失败而中断整个流程

### 3. LinkRouter (parsers/link_router.py)

**职责**：从文本中匹配可解析的链接并确定对应的解析器。

**主要功能**：
- 遍历所有解析器，提取匹配的链接
- 按链接在文本中的位置排序
- 去重处理
- 为每个链接匹配对应的解析器

**关键方法**：
- `extract_links_with_parser()`: 提取链接并匹配解析器
- `find_parser()`: 根据URL查找解析器

### 4. BaseVideoParser (parsers/base_parser.py)

**职责**：定义解析器的抽象接口，所有平台解析器必须实现此接口。

**接口方法**：
- `can_parse(url: str) -> bool`: 判断是否可以解析此URL
- `extract_links(text: str) -> List[str]`: 从文本中提取链接
- `parse(session, url: str) -> Dict[str, Any]`: 解析单个链接，返回元数据

**元数据格式**：
```python
{
    'url': str,                    # 原始URL（必需）
    'media_type': str,             # 媒体类型: "video", "image", "gallery", "mixed"（必需）
    'title': str,                  # 标题（可选）
    'author': str,                 # 作者（可选）
    'desc': str,                   # 简介（可选）
    'timestamp': str,              # 发布时间（可选）
    'media_urls': List[str],       # 媒体直链列表（必需）
    'video_urls': List[str],       # 视频URL列表（mixed类型需要）
    'image_urls': List[str],       # 图片URL列表（mixed类型需要）
    'thumb_url': str,              # 缩略图URL（可选）
    # 其他平台特定字段...
}
```

### 5. DownloadManager (core/download_manager.py)

**职责**：管理媒体下载流程，根据配置决定使用网络直链还是本地文件。

**主要功能**：
- 检查视频大小（HEAD请求获取Content-Length，或从实际文件大小获取）
- 根据配置决定下载策略：
  - 超过 `max_video_size_mb`：跳过，不下载（下载前和下载后都会检查）
  - 超过 `large_video_threshold_mb`：下载到缓存目录
  - 推特视频：强制下载到缓存
  - 启用 `pre_download_all_media`：预先下载所有媒体（下载前会先检查大小）
- 管理并发下载数量
- 为元数据添加文件路径信息
- 下载后再次验证视频大小，确保不超过限制

**关键方法**：
- `process_metadata()`: 处理单个元数据，决定下载策略
- `_determine_media_type()`: 判断媒体类型（视频/图片）
- `_build_media_items()`: 构建媒体项列表
- `_generate_media_id()`: 生成媒体ID（用于文件命名）

**下载策略决策流程**：

**预先下载模式（pre_download_all_media = True）**：
```
1. 检查媒体类型
   ├─ gallery → 直接下载所有媒体到缓存
   └─ video/mixed → 继续

2. 下载前检查视频大小（HEAD请求）
   ├─ 超过 max_video_size_mb → 标记 exceeds_max_size，跳过下载
   └─ 未超过 → 继续

3. 下载所有媒体到缓存

4. 下载后检查视频大小（从实际文件大小获取）
   ├─ 超过 max_video_size_mb → 清理已下载文件，标记 exceeds_max_size
   └─ 未超过 → 使用本地文件
```

**非预先下载模式（pre_download_all_media = False）**：
```
1. 检查媒体类型
   ├─ gallery → 不检查大小，直接使用直链
   └─ video/mixed → 继续

2. 获取视频大小（HEAD请求）
   ├─ 超过 max_video_size_mb → 标记 exceeds_max_size，跳过
   └─ 未超过 → 继续

3. 检查是否需要下载
   ├─ 超过 large_video_threshold_mb → 下载到缓存
   ├─ 是推特视频 → 下载到缓存
   └─ 其他 → 使用直链

4. 如果下载了视频，下载后再次检查大小（从实际文件大小获取）
   ├─ 超过 max_video_size_mb → 清理已下载文件，标记 exceeds_max_size
   └─ 未超过 → 使用本地文件
```

### 6. Downloader (core/downloader.py)

**职责**：实现具体的下载功能，包括视频大小检查和媒体下载。

**主要功能**：
- 通过 HEAD 请求获取视频大小
- 下载媒体文件到临时目录
- 将临时文件移动到缓存目录
- 支持并发下载控制

**关键函数**：
- `get_video_size()`: 获取视频大小（MB）
- `download_media_to_cache()`: 下载媒体到缓存目录
- `download_image_to_file()`: 下载图片到文件
- `pre_download_media()`: 预先下载多个媒体（并发）

### 7. FileManager (core/file_manager.py)

**职责**：管理文件缓存和清理。

**主要功能**：
- 检查缓存目录是否可用（可写）
- 根据文件内容或URL确定文件扩展名
- 清理临时文件和缓存文件
- 将临时文件移动到缓存目录

**关键函数**：
- `check_cache_dir_available()`: 检查缓存目录可用性
- `get_image_suffix()`: 确定图片文件扩展名
- `cleanup_files()`: 清理文件列表
- `move_temp_file_to_cache()`: 移动临时文件到缓存

### 8. NodeBuilder (core/node_builder.py)

**职责**：将元数据转换为 AstrBot 消息节点。

**主要功能**：
- 构建文本节点（标题、作者、简介等）
- 处理解析失败时的错误信息显示
- 构建媒体节点（Image/Video）
- 处理本地文件和网络URL
- 处理消息打包逻辑（区分普通媒体和大媒体）

**关键函数**：
- `build_text_node()`: 构建文本节点
- `build_media_nodes()`: 构建媒体节点列表
- `build_nodes_for_link()`: 构建单个链接的节点列表
- `build_all_nodes()`: 构建所有链接的节点，处理打包逻辑
- `is_pure_image_gallery()`: 判断是否为纯图片图集

---

## 工作流程

### 完整解析流程

```
1. 消息接收
   │
   ├─ VideoParserPlugin.auto_parse() 监听消息事件
   │
   └─ 检查是否应该解析 (_should_parse)
      ├─ 自动解析模式：直接解析
      └─ 手动触发模式：检查关键词

2. 链接提取
   │
   ├─ ParserManager.extract_all_links()
   │
   └─ LinkRouter.extract_links_with_parser()
      ├─ 遍历所有解析器
      ├─ 提取匹配的链接
      ├─ 按位置排序
      └─ 去重

3. 并行解析
   │
   ├─ ParserManager.parse_text()
   │
   └─ 为每个链接调用对应的解析器
      ├─ BilibiliParser.parse()
      ├─ DouyinParser.parse()
      ├─ KuaishouParser.parse()
      ├─ XiaohongshuParser.parse()
      └─ TwitterParser.parse()
      │
      └─ 返回元数据列表

4. 下载管理
   │
   ├─ DownloadManager.process_metadata()
   │
   └─ 对每个元数据：
      ├─ 检查视频大小（HEAD请求）
      ├─ 判断是否需要下载
      ├─ 下载到缓存（如需要）
      └─ 添加文件路径信息

5. 节点构建
   │
   ├─ NodeBuilder.build_all_nodes()
   │
   └─ 对每个元数据：
      ├─ 构建文本节点
      ├─ 构建媒体节点
      └─ 区分普通媒体和大媒体

6. 消息发送
   │
   ├─ 检查 is_auto_pack 配置
   │
   ├─ 打包模式：
   │   ├─ 普通媒体 → Nodes（消息集合）
   │   └─ 大媒体 → 单独发送
   │
   └─ 非打包模式：
       └─ 所有媒体 → 独立发送

7. 清理
   │
   └─ cleanup_files() 清理临时文件
```

### 解析器工作流程

```
BaseVideoParser.parse()
│
├─ 1. URL预处理
│   ├─ 短链展开（如需要）
│   ├─ 参数提取（BV号、AV号等）
│   └─ 构建API请求URL
│
├─ 2. API请求
│   ├─ 设置请求头（User-Agent、Referer等）
│   ├─ 发送HTTP请求
│   └─ 解析响应（JSON/HTML）
│
├─ 3. 数据提取
│   ├─ 提取标题、作者、简介
│   ├─ 提取媒体直链
│   ├─ 提取缩略图URL
│   └─ 处理特殊字段（如B站的视频分P）
│
└─ 4. 构建元数据
    └─ 返回标准格式的元数据字典
```

---

## 技术栈

### 核心技术

- **Python 3.x**: 主要编程语言
- **aiohttp**: 异步HTTP客户端，用于API请求和媒体下载
- **asyncio**: 异步编程框架，实现并行解析和下载
- **AstrBot API**: 机器人框架API，用于消息发送和事件监听

### 关键依赖

```python
aiohttp          # 异步HTTP客户端
astrbot          # AstrBot框架（运行时依赖）
```

### 异步编程模式

项目大量使用异步编程，主要优势：
- **并行解析**：多个链接同时解析，提高效率
- **并发下载**：控制并发数量，避免资源耗尽
- **非阻塞IO**：网络请求不阻塞主线程

**关键异步模式**：
- `asyncio.gather()`: 并行执行多个异步任务
- `asyncio.Semaphore`: 控制并发数量
- `aiohttp.ClientSession`: 复用HTTP连接，提高性能

---

## 设计模式

### 1. 策略模式（Strategy Pattern）

**应用场景**：不同平台的解析策略

**实现**：
- `BaseVideoParser` 定义解析策略接口
- 各平台解析器（`BilibiliParser`、`DouyinParser`等）实现具体策略
- `ParserManager` 根据URL选择合适的策略

### 2. 工厂模式（Factory Pattern）

**应用场景**：解析器实例创建

**实现**：
- `VideoParserPlugin.__init__()` 根据配置创建解析器实例
- 支持动态启用/禁用解析器

### 3. 责任链模式（Chain of Responsibility）

**应用场景**：链接匹配和解析器路由

**实现**：
- `LinkRouter` 遍历解析器列表，找到第一个匹配的解析器
- 每个解析器的 `can_parse()` 方法判断是否匹配

### 4. 模板方法模式（Template Method）

**应用场景**：下载流程的统一处理

**实现**：
- `DownloadManager.process_metadata()` 定义下载流程模板
- 根据配置和媒体类型决定具体执行步骤

---

## 数据流

### 元数据流转

```
原始URL
  │
  ▼
解析器解析
  │
  ▼
元数据字典 (metadata)
  │
  ├─ url: 原始URL
  ├─ media_type: 媒体类型
  ├─ title: 标题
  ├─ author: 作者
  ├─ media_urls: 媒体直链列表
  └─ ...
  │
  ▼
DownloadManager 处理
  │
  ├─ 添加 video_sizes: 视频大小列表
  ├─ 添加 max_video_size_mb: 最大视频大小
  ├─ 添加 file_paths: 文件路径列表（如已下载）
  ├─ 添加 use_local_files: 是否使用本地文件
  └─ 添加 is_large_media: 是否为大媒体
  │
  ▼
NodeBuilder 构建
  │
  ├─ Plain: 文本节点
  ├─ Image: 图片节点
  └─ Video: 视频节点
  │
  ▼
AstrBot 消息发送
```

### 文件流转

```
网络媒体URL
  │
  ▼
下载到临时文件 (tempfile)
  │
  ▼
移动到缓存目录 (cache_dir)
  │
  ├─ 文件命名: {media_id}_{index}.{suffix}
  └─ 文件路径添加到 metadata['file_paths']
  │
  ▼
构建消息节点 (使用本地文件路径)
  │
  ▼
消息发送
  │
  ▼
清理文件 (cleanup_files)
```

---

## 扩展性设计

### 添加新平台解析器

1. **创建解析器类**：
```python
from parsers.base_parser import BaseVideoParser

class NewPlatformParser(BaseVideoParser):
    def __init__(self):
        super().__init__("新平台")
    
    def can_parse(self, url: str) -> bool:
        # 判断是否可以解析此URL
        return "newplatform.com" in url
    
    def extract_links(self, text: str) -> List[str]:
        # 从文本中提取链接
        # 使用正则表达式匹配
        pass
    
    async def parse(self, session, url: str) -> Dict[str, Any]:
        # 解析链接，返回元数据
        pass
```

2. **注册解析器**：
在 `main.py` 的 `__init__` 方法中：
```python
from parsers import NewPlatformParser

if enable_new_platform:
    parsers.append(NewPlatformParser())
```

3. **添加配置项**：
在配置文件中添加启用开关：
```python
parser_enable_settings = config.get("parser_enable_settings", {})
enable_new_platform = parser_enable_settings.get("enable_new_platform", True)
```

### 扩展下载策略

在 `DownloadManager.process_metadata()` 中添加新的判断逻辑：

```python
# 自定义下载条件
if custom_condition:
    needs_download = True
```

### 扩展消息节点类型

在 `NodeBuilder.build_media_nodes()` 中添加新的节点类型处理：

```python
elif media_type == 'new_type':
    # 构建新类型的节点
    pass
```

---

## 性能优化

### 1. 并行处理

- **并行解析**：使用 `asyncio.gather()` 同时解析多个链接
- **并发下载**：使用 `asyncio.Semaphore` 控制并发数量，避免资源耗尽

### 2. 连接复用

- 使用 `aiohttp.ClientSession` 复用HTTP连接
- 单个会话处理所有请求，减少连接建立开销

### 3. 智能下载策略

- 小文件使用直链，避免不必要的下载
- 大文件下载到本地，提高发送成功率
- 预先下载模式可提高总下载速度
- 下载前和下载后双重检查视频大小，确保不超过限制
- 优先使用实际文件大小（更准确），HEAD请求结果作为补充

### 4. 资源管理

- 及时清理临时文件，避免磁盘空间浪费
- 使用信号量控制并发，避免内存溢出

---

## 错误处理

### 异常处理策略

1. **解析器异常**：
   - 单个链接解析失败不影响其他链接
   - 异常信息记录到元数据的 `error` 字段
   - 错误信息会在文本节点中显示（"解析失败：{错误信息}"）
   - 继续处理其他链接

2. **下载异常**：
   - 下载失败时回退到直链模式
   - 记录警告日志，不中断流程

3. **文件操作异常**：
   - 文件清理失败只记录警告，不抛出异常
   - 缓存目录不可用时自动降级到直链模式

4. **视频大小检查异常**：
   - 下载前通过HEAD请求检查大小，如果超过限制则跳过下载
   - 下载后从实际文件大小再次检查，如果超过限制则清理已下载文件
   - 如果HEAD请求无法获取大小，下载后会从实际文件大小检查
   - 确保 `max_video_size_mb` 配置在所有情况下都能正确生效

### 日志记录

- 使用 AstrBot 的 `logger` 记录关键操作
- 异常信息包含URL和错误详情，便于调试

---

## 配置说明

### 配置结构

```python
{
    "is_auto_pack": bool,                    # 是否打包为消息集合
    "trigger_settings": {
        "is_auto_parse": bool,               # 是否自动解析
        "trigger_keywords": List[str]        # 手动触发关键词
    },
    "video_size_settings": {
        "max_video_size_mb": float,          # 最大视频大小限制
        "large_video_threshold_mb": float    # 大视频阈值
    },
    "download_settings": {
        "cache_dir": str,                    # 缓存目录
        "pre_download_all_media": bool,      # 是否预先下载
        "max_concurrent_downloads": int       # 最大并发下载数
    },
    "parser_enable_settings": {
        "enable_bilibili": bool,
        "enable_douyin": bool,
        # ... 其他平台
    },
    "twitter_proxy_settings": {
        "twitter_use_image_proxy": bool,
        "twitter_use_video_proxy": bool,
        "twitter_proxy_url": str
    }
}
```

---

## 注意事项

### 平台特性

1. **小红书**：
   - 所有链接均有身份验证和时效性，需要在有效期内发送完整链接才能成功解析
   - 分享链接的解析结果有水印

2. **推特**：
   - 图片CDN大多被墙，建议开启代理
   - 视频CDN通常不受影响，可直连
   - 使用 fxtwitter API，无需代理

3. **B站**：
   - 转发动态会使用"转发动态数据（原始动态数据）"组织文本格式解析结果

---
