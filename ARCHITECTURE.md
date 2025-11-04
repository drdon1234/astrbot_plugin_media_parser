# 架构说明

## 目录结构

```
astrbot_plugin_video_parser/
├── __init__.py                 # 插件初始化文件
├── main.py                     # 插件主入口
├── base_parser.py              # 基础解析器抽象类
├── parser_manager.py           # 解析器管理器
├── _conf_schema.json           # 配置架构定义
├── metadata.yaml               # 插件元数据
├── requirements.txt            # 依赖列表
├── README.md                   # 使用说明
├── ARCHITECTURE.md             # 架构说明（本文件）
└── parsers/                    # 解析器目录
    ├── __init__.py             # 解析器模块导出
    ├── bilibili_parser.py      # B站解析器
    ├── douyin_parser.py        # 抖音解析器
    └── example_parser.py       # 示例解析器（用于参考）
```

## 核心组件

### 1. BaseVideoParser (base_parser.py)

所有解析器的基类，定义了统一的接口：

- **必须实现的方法**：
  - `can_parse(url)`: 判断是否可以解析此URL
  - `extract_links(text)`: 从文本中提取链接
  - `parse(session, url)`: 解析视频链接

- **提供的工具方法**：
  - `get_video_size()`: 获取视频大小
  - `check_video_size()`: 检查视频大小是否在限制内
  - `build_text_node()`: 构建文本节点（可选使用）
  - `build_media_nodes()`: 构建媒体节点（可选使用）

### 2. ParserManager (parser_manager.py)

解析器管理器，负责：

- 管理和注册解析器
- 自动识别链接类型并选择合适的解析器
- 统一调度解析任务
- 构建消息节点

主要方法：
- `register_parser()`: 注册新解析器
- `find_parser()`: 查找合适的解析器
- `extract_all_links()`: 提取所有可解析的链接
- `parse_text()`: 解析文本中的所有链接
- `build_nodes()`: 构建消息节点

### 3. 具体解析器 (parsers/)

每个平台的解析器实现：

- **BilibiliParser**: 解析B站视频（UGC/PGC）
- **DouyinParser**: 解析抖音视频/图片集
- **ExampleParser**: 示例解析器（用于参考）

### 4. VideoParserPlugin (main.py)

AstrBot插件主类：

- 处理消息事件
- 管理配置
- 初始化解析器和管理器
- 发送解析结果

## 数据流

```
1. 消息事件触发
   ↓
2. VideoParserPlugin.auto_parse()
   ↓
3. ParserManager.build_nodes()
   ↓
4. ParserManager.extract_all_links()
   ↓ (对每个链接)
5. 具体解析器.parse()
   ↓
6. 返回统一格式的解析结果
   ↓
7. ParserManager._build_text_node() 和 _build_media_nodes()
   ↓
8. 返回节点列表
   ↓
9. VideoParserPlugin 发送消息
```

## 扩展流程

### 添加新解析器步骤：

1. 在 `parsers/` 目录创建新文件，例如 `youtube_parser.py`
2. 继承 `BaseVideoParser` 并实现三个必要方法
3. 在 `parsers/__init__.py` 中导出新解析器
4. 在 `main.py` 中根据配置初始化新解析器
5. 在 `_conf_schema.json` 中添加配置项

### 解析结果格式

所有解析器应返回以下格式的字典：

```python
{
    "video_url": str,      # 原始视频页面URL（必需）
    "direct_url": str,     # 视频直链（如果有视频）
    "title": str,          # 视频标题（可选）
    "author": str,         # 作者信息（可选）
    "desc": str,           # 视频描述（可选）
    "thumb_url": str,      # 封面图URL（可选）
    "images": List[str],   # 图片列表（如果是图片集，可选）
    "is_gallery": bool,    # 是否为图片集（可选）
    "timestamp": str,      # 发布时间（可选）
}
```

## 设计原则

1. **单一职责**：每个解析器只负责一个平台的解析
2. **开闭原则**：对扩展开放，对修改封闭
3. **统一接口**：所有解析器使用相同的接口
4. **自动识别**：管理器自动识别链接类型并选择合适的解析器
5. **可配置**：支持启用/禁用特定解析器

## 优势

1. **易于扩展**：添加新解析器只需实现三个方法
2. **统一管理**：所有解析器由管理器统一调度
3. **自动识别**：无需手动指定解析器
4. **灵活配置**：可以按需启用/禁用解析器
5. **代码复用**：基类提供通用功能

