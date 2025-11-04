# 适配 AstrBot 的插件，自动识别视频链接并转换为直链发送

## 功能特性

- ✅ 支持B站视频解析（UGC/PGC）
- ✅ 支持抖音视频解析（视频/图片集）
- ✅ 自动识别链接类型并选择合适的解析器
- ✅ 可扩展架构，方便添加新的解析器
- ✅ 支持配置最大视频大小限制
- ✅ 支持启用/禁用特定平台的解析器
- ✅ 支持自动解析和手动触发

## 安装

1. 将插件目录复制到 AstrBot 的插件目录
2. 安装依赖：
   ```bash
   pip install -r requirements.txt
   ```

## 配置说明

在 AstrBot 的配置文件中添加以下配置项：

```json
{
    "is_auto_parse": true,
    "is_auto_pack": true,
    "max_video_size_mb": 0.0,
    "enable_bilibili": true,
    "enable_douyin": true,
    "trigger_keywords": ["视频解析", "解析视频"]
}
```

### 配置项说明

- `is_auto_parse`: 是否自动解析视频链接（默认：true）
- `is_auto_pack`: 是否将解析结果打包为消息集合（默认：true）
  - 在微信平台使用时需要禁用此项
- `max_video_size_mb`: 最大允许发送的视频大小（MB），0表示不限制（默认：0.0）
- `enable_bilibili`: 是否启用B站解析器（默认：true）
- `enable_douyin`: 是否启用抖音解析器（默认：true）
- `trigger_keywords`: 手动触发解析的关键词列表（默认：["视频解析", "解析视频"]）

## 扩展开发

### 添加新的解析器

1. 在 `parsers/` 目录下创建新的解析器文件，例如 `youtube_parser.py`
2. 继承 `BaseVideoParser` 类并实现必要的方法：

```python
from ..base_parser import BaseVideoParser
from typing import Optional, Dict, Any, List
import aiohttp

class YouTubeParser(BaseVideoParser):
    def __init__(self, max_video_size_mb: float = 0.0):
        super().__init__("YouTube", max_video_size_mb)
        # 初始化信号量等
    
    def can_parse(self, url: str) -> bool:
        """判断是否可以解析此URL"""
        return "youtube.com" in url or "youtu.be" in url
    
    def extract_links(self, text: str) -> List[str]:
        """从文本中提取链接"""
        # 实现链接提取逻辑
        pass
    
    async def parse(self, session: aiohttp.ClientSession, url: str) -> Optional[Dict[str, Any]]:
        """解析视频链接"""
        # 实现解析逻辑
        pass
```

3. 在 `parsers/__init__.py` 中导入新解析器：

```python
from .youtube_parser import YouTubeParser
__all__ = ['BilibiliParser', 'DouyinParser', 'YouTubeParser']
```

4. 在 `main.py` 中注册新解析器：

```python
from .parsers import BilibiliParser, DouyinParser, YouTubeParser

# 在 __init__ 方法中
if config.get("enable_youtube", True):
    parsers.append(YouTubeParser(max_video_size_mb=max_video_size_mb))
```

5. 在配置文件中添加对应的配置项：

```json
{
    "enable_youtube": true
}
```

## 架构说明

### 核心组件

1. **BaseVideoParser** (`base_parser.py`)
   - 所有解析器的基类
   - 定义了统一的接口和通用方法
   - 提供视频大小检查、节点构建等辅助功能

2. **ParserManager** (`parser_manager.py`)
   - 解析器管理器
   - 统一管理和调度所有解析器
   - 自动识别链接类型并选择合适的解析器

3. **具体解析器** (`parsers/`)
   - 各平台的具体实现
   - 继承自 `BaseVideoParser`
   - 实现平台特定的解析逻辑

4. **主插件** (`main.py`)
   - AstrBot 插件入口
   - 处理消息事件
   - 调用解析器管理器进行解析

### 数据流

```
消息事件 → VideoParserPlugin → ParserManager → 具体解析器 → 返回结果 → 构建节点 → 发送消息
```

## 许可证

请查看 LICENSE 文件

## 作者

drdon1234

