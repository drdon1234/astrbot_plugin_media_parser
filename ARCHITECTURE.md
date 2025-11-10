# 架构说明

## 目录结构

```
astrbot_plugin_video_parser/
├── __init__.py                 # 插件初始化文件
├── main.py                     # 插件主入口
├── parser_manager.py           # 解析器管理器
├── _conf_schema.json           # 配置架构定义
├── metadata.yaml               # 插件元数据
├── requirements.txt            # 依赖列表
├── README.md                   # 使用说明
├── ARCHITECTURE.md             # 架构说明（本文件）
├── run_local.py                # 本地测试脚本
└── parsers/                    # 解析器目录
    ├── __init__.py             # 解析器模块导出
    ├── base_parser.py          # 基础解析器抽象类
    ├── bilibili.py             # B站解析器
    ├── douyin.py               # 抖音解析器
    ├── kuaishou.py             # 快手解析器
    ├── twitter.py              # Twitter/X 解析器
    └── example.py              # 示例解析器（用于参考）
```

## 核心组件

### 1. BaseVideoParser (parsers/base_parser.py)

所有解析器的基类，定义了统一的接口：

- **必须实现的方法**：
  - `can_parse(url)`: 判断是否可以解析此URL
  - `extract_links(text)`: 从文本中提取链接
  - `parse(session, url)`: 解析视频链接

- **提供的工具方法**：
  - `get_video_size()`: 获取视频文件大小
  - `get_image_size()`: 获取图片文件大小
  - `check_media_size()`: 检查媒体大小是否在限制内（支持视频和图片）
  - `check_video_size()`: 检查视频大小是否在限制内（兼容旧接口）
  - `build_text_node()`: 构建文本节点（返回 Plain 对象）
  - `build_media_nodes()`: 构建媒体节点（返回 Plain/Image/Video 对象列表）
  - `_download_large_media_to_cache()`: 下载大媒体到缓存目录（支持视频和图片）
  - `_download_large_video_to_cache()`: 下载大视频到缓存目录（兼容旧接口）
  - `_check_cache_dir_available()`: 检查缓存目录是否可用（可写）
  - `_pre_download_media()`: 预先下载所有媒体到本地
  - `_build_gallery_nodes_from_files()`: 从文件构建图片图集节点
  - `_build_gallery_nodes_from_urls()`: 从URL构建图片图集节点
  - `_build_video_node_from_file()`: 从文件构建视频节点
  - `_build_video_node_from_url()`: 从URL构建视频节点
  - `_build_video_gallery_nodes_from_files()`: 从文件构建视频图集节点

- **配置参数**：
  - `max_media_size_mb`: 最大允许的媒体大小(MB)，超过此大小的媒体（视频和图片）将被跳过
  - `large_media_threshold_mb`: 大媒体阈值(MB)，超过此阈值的媒体（视频和图片）将单独发送
  - `cache_dir`: 媒体文件缓存目录，用于存储大媒体和 Twitter 视频
  - `pre_download_all_media`: 是否预先下载所有媒体到本地
  - `max_concurrent_downloads`: 最大并发下载数

### 2. ParserManager (parser_manager.py)

解析器管理器，负责：

- 管理和注册解析器
- 自动识别链接类型并选择合适的解析器
- 统一调度解析任务（并行解析）
- 构建消息节点
- 处理解析失败的情况
- 区分普通链接和大视频链接

主要方法：
- `register_parser()`: 注册新解析器
- `find_parser()`: 查找合适的解析器
- `extract_all_links()`: 提取所有可解析的链接（去重）
- `parse_text()`: 解析文本中的所有链接
- `build_nodes()`: 构建消息节点，返回扁平化的节点列表
  - 返回格式：`(all_link_nodes, link_metadata, temp_files, video_files, normal_link_count)`
  - `all_link_nodes`: 所有链接的节点列表（每个元素是一个链接的节点列表）
  - `link_metadata`: 链接元数据列表（包含是否为大视频、视频文件路径等信息）
  - `temp_files`: 临时文件列表（图片文件）
  - `video_files`: 视频文件列表
  - `normal_link_count`: 普通链接数量（用于决定节点组装方式）

### 3. 具体解析器 (parsers/)

每个平台的解析器实现（使用"平台名.py"命名）：

- **BilibiliParser** (`bilibili.py`): 解析B站视频（UGC/PGC）
  - 支持视频大小检测（需要 Referer 请求头）
  - 支持大视频下载到缓存
  - 支持预先下载所有视频到缓存目录
  - 重写 `get_video_size()` 方法以支持 B站特殊的请求头要求

- **DouyinParser** (`douyin.py`): 解析抖音视频/图片集
  - 支持视频和图集解析
  - 支持媒体大小检测（视频和图片，需要 Referer 请求头）
  - 支持大媒体下载到缓存
  - 支持预先下载所有媒体到缓存目录
  - 优先检查预下载开关，避免重复下载
  - 重写 `get_video_size()` 方法以支持抖音特殊的请求头要求

- **KuaishouParser** (`kuaishou.py`): 解析快手视频/图片集
  - 支持视频和图集解析
  - 支持媒体大小检测（视频和图片）
  - 支持大媒体下载到缓存
  - 支持预先下载所有媒体到缓存目录
  - 优先检查预下载开关，避免重复下载

- **TwitterParser** (`twitter.py`): 解析Twitter/X视频/图片
  - 支持视频和图片解析
  - 支持代理配置（图片和视频可分别控制是否使用代理，共用同一个代理地址）
  - fxtwitter API 接口不需要代理，会自动直连
  - 所有视频都会下载到缓存目录（因为 Twitter 视频无法直接通过 URL 发送）
  - 支持预先下载所有媒体到缓存目录（优先检查预下载开关）
  - 支持重试机制（API 调用和媒体下载）
  - 重写 `get_video_size()` 方法以支持 Twitter 特殊的请求头要求

- **ExampleParser** (`example.py`): 示例解析器（用于参考）

### 4. VideoParserPlugin (main.py)

AstrBot插件主类：

- 处理消息事件
- 管理配置（分组配置：触发设置、视频大小设置、解析器启用设置、Twitter代理设置）
- 初始化解析器和管理器
- 发送解析结果
- 处理自动打包逻辑
- 处理大视频单独发送逻辑
- 文件清理（临时文件和视频文件）

主要方法：
- `_should_parse()`: 判断是否应该解析消息
  - 自动解析模式：直接返回 True
  - 手动解析模式：检查触发关键词或平台特定关键词
- `_cleanup_files()`: 清理文件列表
- `_cleanup_all_files()`: 清理所有临时文件和视频文件
- `_is_pure_image_gallery()`: 判断节点列表是否是纯图片图集
- `auto_parse()`: 自动解析消息中的视频链接
  - 提取链接
  - 构建节点
  - 根据 `is_auto_pack` 决定发送方式
  - 处理大视频单独发送
  - 清理文件

### 5. run_local.py

本地测试脚本，用于测试视频链接解析功能：

- 设置虚拟包环境以支持相对导入
- 初始化解析器（所有解析器不使用缓存目录，解析结果只保存在内存中）
- 解析链接并显示元数据
- 支持用户选择下载媒体文件到本地
- 支持代理配置（用于 Twitter 链接）
- 支持退出选项（输入链接时和询问下载时都可以退出）

## 数据流

```
1. 消息事件触发
   ↓
2. VideoParserPlugin.auto_parse()
   ↓
3. ParserManager.extract_all_links() - 提取所有可解析的链接（去重）
   ↓
4. ParserManager.build_nodes()
   ↓
5. 并行解析所有链接 (asyncio.gather)
   ↓ (对每个链接)
6. 具体解析器.parse()
   - 检测媒体大小（视频和图片）
   - 优先检查预下载开关（pre_download_all_media）
     * 如果开启预下载：所有媒体（视频和图片）并发下载到缓存目录
     * 如果未开启预下载：
       - 大媒体（超过阈值）：下载到缓存目录
       - 小媒体：视频使用URL，图片下载到临时文件
   - 设置 force_separate_send 标志（大媒体）
   - 返回统一格式的解析结果
   ↓
7. 解析器.build_text_node() 和 build_media_nodes()
   - build_text_node(): 返回 Plain 对象
   - build_media_nodes(): 返回扁平化的节点列表（Plain/Image/Video 对象）
   ↓
8. ParserManager 组织节点
   - 区分普通链接和大视频链接
   - 返回扁平化的节点列表
   ↓
9. VideoParserPlugin 发送消息
   - 如果 is_auto_pack=True:
     * 普通链接：扁平化节点放入一个转发消息集合（Nodes）
     * 纯图片图集：使用一个 chain_result 包含所有 Image
     * 视频图集混合：全部单独发送
     * 大视频链接：单独发送，发送前显示提示消息
   - 如果 is_auto_pack=False:
     * 所有链接：单独发送，使用分隔线分割
     * 纯图片图集：使用一个 chain_result 包含所有 Image
     * 视频图集混合：全部单独发送
   - 发送后立即清理视频文件
   ↓
10. 清理临时文件
```

## 配置架构

配置文件 `_conf_schema.json` 定义了以下配置组：

### 1. is_auto_pack
- **类型**: bool
- **默认值**: true
- **说明**: 是否将解析结果打包为消息集合

### 2. trigger_settings (触发设置)
- **is_auto_parse**: 是否自动解析视频链接（bool，默认 true）
- **trigger_keywords**: 手动触发解析的关键词列表（list，默认 ["视频解析", "解析视频"]）

### 3. media_size_settings (媒体大小设置)
- **max_media_size_mb**: 最大允许发送的媒体大小(MB)（float，默认 0.0，0表示不限制）
- **large_media_threshold_mb**: 大媒体阈值(MB)（float，默认 100.0，不能超过100MB，0表示不启用）

### 4. download_settings (下载和缓存设置)
- **cache_dir**: 媒体缓存目录（string，默认 "/app/sharedFolder/video_parser/cache"）
- **pre_download_all_media**: 是否预先下载所有媒体到本地（bool，默认 false）
- **max_concurrent_downloads**: 最大并发下载数（int，默认 3，建议值：3-5）

### 5. parser_enable_settings (解析器启用设置)
- **enable_bilibili**: 是否启用B站解析器（bool，默认 true）
- **enable_douyin**: 是否启用抖音解析器（bool，默认 true）
- **enable_twitter**: 是否启用Twitter/X解析器（bool，默认 true）
- **enable_kuaishou**: 是否启用快手解析器（bool，默认 true）

### 6. twitter_proxy_settings (Twitter代理设置)
- **twitter_use_image_proxy**: Twitter图片下载是否使用代理（bool，默认 false）
- **twitter_use_video_proxy**: Twitter视频下载是否使用代理（bool，默认 false）
- **twitter_proxy_url**: Twitter代理地址（string，默认 ""，格式：http://host:port 或 socks5://host:port），图片和视频共用此代理地址

**说明**：
- 图片和视频可以分别控制是否使用代理
- 图片和视频共用同一个代理地址（`twitter_proxy_url`）
- fxtwitter API 接口不需要代理，会自动直连
- 推荐配置：仅开启图片代理（图片 CDN 大多被墙），视频代理通常无需开启（视频 CDN 几乎不受影响）

## 核心特性

### 1. 大媒体处理机制

当媒体大小（视频或图片）超过 `large_media_threshold_mb` 阈值时：

1. **检查缓存目录**：如果缓存目录不可用，直接结束下载流程并返回"本地缓存路径无效"错误
2. **下载到缓存**：媒体会被下载到配置的缓存目录
3. **设置标志**：`force_separate_send = True`，`has_large_video = True`（仅视频）
4. **单独发送**：大媒体链接的所有节点（文本和媒体）都会单独发送，不包含在转发消息集合中
5. **提示消息**：在发送大媒体前，会显示提示消息（仅当 `is_auto_pack=True` 时）
6. **立即清理**：发送后立即删除缓存文件

### 2. 自动打包机制

当 `is_auto_pack=True` 时：

1. **普通链接**：所有节点扁平化放入一个转发消息集合（Nodes）
2. **纯图片图集**：所有图片使用一个 `chain_result` 包含
3. **视频图集混合**：所有媒体单独发送
4. **大视频链接**：单独发送，不包含在转发消息集合中
5. **分隔线**：不同链接之间使用分隔线分割

当 `is_auto_pack=False` 时：

1. **所有链接**：单独发送，按原始顺序
2. **纯图片图集**：所有图片使用一个 `chain_result` 包含
3. **视频图集混合**：所有媒体单独发送
4. **分隔线**：不同链接之间使用分隔线分割

### 3. 文件清理机制

1. **临时文件**：图片文件在发送后清理
2. **视频文件**：所有下载的视频文件在发送后立即清理
3. **清理时机**：
   - 大视频：发送后立即清理
   - 普通视频：发送后立即清理
   - 临时图片：所有链接处理完成后清理

### 4. 错误处理机制

1. **解析失败**：显示 "解析失败：{失败原因}\n原始链接：{url}"
2. **缓存目录无效**：如果缓存目录不可用，返回"本地缓存路径无效"错误
3. **网络错误**：重试机制（Twitter API 和媒体下载）
4. **文件清理**：即使发生异常或发送失败也会清理文件
5. **媒体大小检查**：支持视频和图片的大小检查，超过限制的媒体将被跳过

### 5. 预先下载机制

当 `pre_download_all_media=True` 时：

1. **优先检查**：解析器在处理媒体时，优先检查预下载开关，避免重复下载
2. **并发下载**：所有媒体文件（视频和图片）将并发下载到缓存目录
3. **并发控制**：使用 `max_concurrent_downloads` 控制同时下载的媒体数量（默认3，建议3-5）
4. **避免重复**：
   - 如果开启预下载，直接使用预下载方法，跳过原有的下载逻辑
   - 如果未开启预下载，使用原有逻辑（大媒体下载到缓存，小媒体使用URL或临时文件）
5. **提高成功率**：使用本地路径发送，可以提高发送成功率
6. **减少总时间**：并发下载可以减少总下载时间
7. **磁盘占用**：会短时间增加磁盘占用

**工作流程**：

- **图片图集**：
  - 开启预下载：直接并发下载所有图片到缓存目录
  - 未开启预下载：大图片下载到缓存，小图片下载到临时文件

- **视频**：
  - 大视频（超过阈值）：必须下载到缓存目录（无论是否开启预下载）
  - 非大视频 + 开启预下载：下载到缓存目录
  - 非大视频 + 未开启预下载：使用URL发送

### 6. 节点构建机制

所有解析器返回扁平化的节点结构：

- **文本节点**：`Plain` 对象
- **图片节点**：`Image` 对象列表
- **视频节点**：`Video` 对象列表
- **混合节点**：`Plain`、`Image`、`Video` 对象列表

节点构建规则：

1. **纯图片图集**：所有图片放在一个 `chain_result` 中
2. **视频图集混合**：所有媒体单独发送
3. **大视频**：所有节点单独发送

## 扩展流程

### 添加新解析器步骤：

1. 在 `parsers/` 目录创建新文件，使用"平台名.py"命名，例如 `youtube.py`

2. 继承 `BaseVideoParser` 并实现三个必要方法：
   ```python
   from .base_parser import BaseVideoParser
   
   class YoutubeParser(BaseVideoParser):
       def can_parse(self, url: str) -> bool:
           # 判断是否可以解析此URL
           pass
       
       def extract_links(self, text: str) -> List[str]:
           # 从文本中提取链接
           pass
       
       async def parse(self, session: aiohttp.ClientSession, url: str) -> Optional[Dict[str, Any]]:
           # 解析视频链接
           pass
   ```

3. 可选：重写 `get_video_size()` 方法（如果需要特殊的请求头）

4. 可选：重写 `build_media_nodes()` 方法（如果需要自定义媒体节点构建逻辑）

5. 在 `parsers/__init__.py` 中导出新解析器

6. 在 `main.py` 中根据配置初始化新解析器

7. 在 `_conf_schema.json` 中添加配置项（启用/禁用开关）

### 解析结果格式

所有解析器应返回以下格式的字典：

```python
{
    "video_url": str,              # 原始视频页面URL（必需）
    "direct_url": str,             # 视频直链（如果有视频，可选）
    "title": str,                  # 视频标题（可选）
    "author": str,                 # 作者信息（可选）
    "desc": str,                   # 视频描述（可选）
    "thumb_url": str,              # 封面图URL（可选）
    "images": List[str],           # 图片URL列表（如果是图片集，可选）
    "image_files": List[str],      # 图片文件路径列表（临时文件，可选）
    "video_files": List[dict],     # 视频文件信息列表（可选）
    "is_gallery": bool,            # 是否为图片集（可选）
    "is_twitter_video": bool,      # 是否为Twitter视频（Twitter解析器专用，可选）
    "is_twitter_images": bool,     # 是否为Twitter图片（Twitter解析器专用，可选）
    "has_large_video": bool,       # 是否包含大视频（可选）
    "force_separate_send": bool,   # 是否强制单独发送（可选）
    "file_size_mb": float,         # 视频大小(MB)（可选）
    "timestamp": str,              # 发布时间（可选）
}
```

`video_files` 格式：

```python
[
    {
        "file_path": str,          # 视频文件路径（如果下载到缓存）
        "url": str,                # 视频URL（如果未下载）
        "thumbnail": str,          # 缩略图URL（可选）
        "duration": float,         # 视频时长（秒，可选）
        "exceeds_large_threshold": bool,  # 是否超过大视频阈值（可选）
        "file_size_mb": float,     # 视频大小(MB)（可选）
    },
    ...
]
```

## 设计原则

1. **单一职责**：每个解析器只负责一个平台的解析
2. **开闭原则**：对扩展开放，对修改封闭
3. **统一接口**：所有解析器使用相同的接口
4. **自动识别**：管理器自动识别链接类型并选择合适的解析器
5. **可配置**：支持启用/禁用特定解析器
6. **扁平化节点**：所有节点都是扁平化的（Plain/Image/Video 对象），不再使用嵌套的 Node 结构
7. **立即清理**：文件发送后立即清理，不占用磁盘空间
8. **错误处理**：完善的错误处理和重试机制

## 优势

1. **易于扩展**：添加新解析器只需实现三个方法
2. **统一管理**：所有解析器由管理器统一调度
3. **自动识别**：无需手动指定解析器
4. **灵活配置**：可以按需启用/禁用解析器
5. **代码复用**：基类提供通用功能
6. **并行解析**：多个链接并行解析，提高效率
7. **大媒体处理**：自动处理大媒体（视频和图片），避免消息适配器限制
8. **预先下载**：支持预先下载所有媒体到本地，提高发送成功率，减少总下载时间
9. **避免重复**：优先检查预下载开关，避免重复下载媒体文件
10. **文件管理**：自动清理临时文件，节省磁盘空间
11. **错误恢复**：完善的错误处理和重试机制
12. **平台兼容**：支持多种消息平台和流媒体平台

## 技术细节

### 1. 节点类型

- **Plain**: 纯文本消息
- **Image**: 图片消息
- **Video**: 视频消息
- **Node**: 转发消息节点（包含发送者信息和内容）
- **Nodes**: 转发消息集合（包含多个 Node）

### 2. 消息发送方式

- **chain_result**: 发送单个消息组件（Plain/Image/Video）或列表
- **plain_result**: 发送纯文本消息
- **Nodes**: 发送转发消息集合

### 3. 媒体大小检测

- 支持视频和图片大小检测
- 使用 HEAD 请求获取 Content-Length 或 Content-Range
- 部分平台需要特殊的请求头（如 Referer）
- 支持 Range 请求作为备选方案
- 超过 `max_media_size_mb` 的媒体将被跳过
- 超过 `large_media_threshold_mb` 的媒体将下载到缓存目录

### 4. 文件下载

- 使用 aiohttp 异步下载
- 支持代理（Twitter，图片和视频可分别控制）
  - Twitter 图片和视频共用同一个代理地址
  - 图片和视频可以分别控制是否使用代理
  - fxtwitter API 接口不需要代理，会自动直连
- 支持重试机制（Twitter API 调用和媒体下载）
- 支持并发下载（预先下载功能）
  - 优先检查预下载开关，避免重复下载
  - 使用 `max_concurrent_downloads` 控制并发数
  - 所有媒体（视频和图片）并发下载到缓存目录
- 支持缓存目录可用性检查
- 下载后立即刷新到磁盘（run_local.py）

### 5. 并发控制

- 使用 asyncio.Semaphore 控制并发数
- 使用 asyncio.gather 并行解析多个链接
- 使用 asyncio.ClientSession 管理 HTTP 连接
- 预先下载功能使用独立的并发控制（`max_concurrent_downloads`）

## 测试

### 本地测试

使用 `run_local.py` 脚本进行本地测试：

1. 运行脚本：`python run_local.py`
2. 输入包含视频链接的文本
3. 查看解析结果
4. 选择是否下载媒体文件

### 配置代理

在 `run_local.py` 中配置代理（用于 Twitter 链接）：

```python
use_proxy = True  # 是否使用代理（同时用于图片和视频，测试用）
proxy_url = "http://127.0.0.1:7890"  # 或 "socks5://127.0.0.1:1080"
```

**说明**：
- 本地测试版本中，如果设置了 `use_proxy=True`，图片和视频都会使用同一个代理地址
- 生产环境中，可以通过配置项分别控制图片和视频是否使用代理
- fxtwitter API 接口在本地测试和生产环境中都不需要代理

## 已知问题

1. 微信平台不支持转发消息集合，需要禁用 `is_auto_pack`
2. Twitter 视频需要下载到缓存目录，无法直接通过 URL 发送
3. 大媒体阈值不能超过消息适配器的硬性限制（100MB）
4. 预先下载功能需要有效的缓存目录，如果缓存目录不可用，预下载功能将不会生效

## 未来改进

1. 支持更多流媒体平台
2. 支持视频转码（降低视频大小）
3. 支持视频预览图生成
4. 支持批量下载和离线缓存
5. 支持自定义解析规则
6. 支持插件热重载
