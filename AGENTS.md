# AGENTS.md

本项目是 AstrBot 聊天机器人的流媒体解析插件。所有代码注释、日志、文档均使用**中文**。

## 项目结构

```
main.py                  # 插件入口，VideoParserPlugin(Star)
_conf_schema.json        # AstrBot WebUI 配置面板 JSON Schema
metadata.yaml            # AstrBot 插件清单（名称、版本、依赖版本范围）
requirements.txt         # Python 依赖（aiohttp / cryptography / qrcode / pillow）
core/
  config_manager.py      # 所有配置 dataclass + 类型转换兜底
  constants.py           # 全局常量（Config 类）
  types.py               # MediaMetadata TypedDict — 全流程核心数据契约
  logger.py              # 全局日志实例（包装 AstrBot logger）
  parser/                # 链接路由 + 平台解析器
    manager.py           #   ParserManager — 并发调度
    router.py            #   LinkRouter — 链接提取 / 去重
    platform/            #   每个平台一个文件，继承 BaseVideoParser
      base.py            #   抽象基类：can_parse / extract_links / parse
  downloader/            # 媒体下载决策 + 多种下载策略
    manager.py           #   DownloadManager — 按媒体决策 local/direct/skip
    handler/             #   具体下载器：stream / range / dash / m3u8 / image
  message_adapter/       # AstrBot 消息构建与发送
  translation/           # LLM 翻译（OpenAI 兼容 / Ollama）
  storage/               # 缓存清理、过期标记、频率限制
  interaction/           # 管理员交互功能（如 B 站扫码登录）
```

## 编码规范

### 语言

- 注释、docstring、日志消息、用户可见字符串一律中文。
- 变量名、函数名、类名使用英文。

### 命名

| 类型 | 规则 | 示例 |
|------|------|------|
| 类 | PascalCase | `DownloadManager`, `BaseVideoParser` |
| 函数 / 方法 | snake_case | `can_parse`, `extract_links` |
| 私有 | 前导 `_` | `_normalize_metadata`, `_delayed_cleanup` |
| 常量 | UPPER_SNAKE_CASE | `DEFAULT_TIMEOUT`, `FNVAL_DASH`, `UA` |
| 配置 dataclass 字段 | snake_case | `max_video_size_mb`, `auto_parse` |

### 导入顺序

三组，组间空行分隔：

```python
# 1. 标准库
import asyncio
import os
from typing import Optional, List

# 2. 第三方
import aiohttp

# 3. 项目内（logger 优先，与其余内部导入间空一行）
from ...logger import logger

from .base import BaseVideoParser
from ..utils import build_request_headers
```

### 类型标注

- 公开方法的参数和返回值必须标注。
- 优先使用 `typing` 模块（`List`, `Dict`, `Optional` 等），部分新代码可用内建泛型（`list[str]`）。
- 核心数据契约是 `core/types.py` 中的 `MediaMetadata(TypedDict, total=False)`，贯穿解析→下载→发送全流程。

### 注释与 docstring

- 每个模块文件首行必须有单行中文 docstring：`"""配置管理模块，负责默认值处理、类型转换与配置兜底。"""`
- 类和公开方法使用 Google 风格 docstring（中文）：
  ```python
  def can_parse(self, url: str) -> bool:
      """判断是否可以解析此URL

      Args:
          url: 视频链接

      Returns:
          是否可以解析
      """
  ```
- 私有方法可只写单行 docstring。
- 段落分隔使用 Unicode 箱线注释：`# ── 解析阶段 ──────────────────────────────`

### 异常处理

- 捕获特定异常，不要裸 `except Exception`。
- `asyncio.CancelledError` 必须重新抛出。
- 配置参数用 `try/except (TypeError, ValueError)` 做防御性转换，回退到默认值。
- 自定义异常保持最小：`class SkipParse(Exception): pass`

### 日志

全局单例 `from ..logger import logger`，不要自建 logger。

- `debug` — 内部状态与流程跟踪（可受 `debug_mode` 控制）
- `info` — 管理操作完成
- `warning` — 可恢复的失败
- `error` — 解析失败、类型错误
- `exception` — 意外异常（自动附带堆栈）

### 文档范围

- `README.md` 面向插件使用者，只记录稳定且需要用户了解的平台能力、配置前提、使用方式和已知限制。
- 常规版本更新、内部实现细节、维护记录，以及用户无需感知或无需手动处理的变更，不写入 `README.md`；按内容归入 `CHANGELOG.md`、`docs/` 或提交记录。
- 修改文档前先对照当前实现和配置 schema，避免把内部模块名、临时实现或未对外承诺的行为写成用户能力。

### 实现一致性

- 修改代码时必须保持原项目的代码风格、命名、导入顺序、异常处理、日志语言、模块边界和实现模式。
- 优先复用现有辅助函数、配置 dataclass、`MediaMetadata` 契约和已有生命周期处理，不重复实现同类逻辑。
- 未经明确需求，不引入新的框架、临时绕过、重复实现、历史兼容层或架构重构；新增抽象必须能实际减少复杂度并符合现有结构。
- 修改前先检查相关实现、配置、文档、测试和工作区状态；保留用户已有改动，只修改任务范围内的文件。

## 架构要点

### 数据流

```
消息事件 → LinkRouter（提取/去重）→ ParserManager（并发解析）
→ DownloadManager（按媒体决策下载）→ NodeBuilder（构建消息节点）
→ MessageSender（聚合/逐条发送）
```

### 新增平台解析器

1. 在 `core/parser/platform/` 新建文件，继承 `BaseVideoParser`。
2. 实现 `can_parse` / `extract_links` / `parse` 三个方法。
3. 在 `core/parser/platform/__init__.py` 中导出。
4. 在 `core/config_manager.py` 的 `PARSER_OUTPUT_KEYS` 和 `create_parsers` 中注册。
5. 在 `_conf_schema.json` 中添加对应的配置项。

### 关键约定

- `parse()` 返回 `Optional[MediaMetadata]`；失败时返回含 `"error"` 键的字典，不要抛异常。
- 下载管理器通过回填 `MediaMetadata` 中的下载阶段字段传递结果，不引入额外数据结构。
- `__init__.py` 作为子包的导出面，使用 `__all__` 暴露公开 API。

## 运行环境

- 本插件在 AstrBot 框架内运行，不是独立 Python 包。
- 入口类继承 `astrbot.api.star.Star`，通过 `@register` 装饰器注册。
- 依赖 AstrBot 的 `Context`、`AstrMessageEvent`、消息组件（`Plain`/`Image`/`Video`）和 `file_token_service`。
- 本地调试可用 `run_local.py`。

## 测试

- 测试在 `test/` 目录（已 gitignore），使用 `unittest.TestCase` / `IsolatedAsyncioTestCase`。
- 不依赖 pytest，不使用 mock 框架；用内联轻量 stub 类替代。
- AstrBot 运行时模块通过 `sys.modules` 注入 stub。
- 运行：`python -m unittest discover -s test`

## Git 约定

- 提交信息应直接描述本次提交的具体改动，使用中文；不要使用 `发布 X.Y.Z：...` 这类只表达版本号的标题。
- 只有在建立稳定基线时才使用基线性质的提交说明；后续提交应按实际功能、修复或文档内容命名。
- 版本号和变更日志按实际发布需要单独维护，不决定提交信息格式。

## AI 协作规范

- 任何 AI 在修改代码、配置或文档前，必须先完整阅读仓库根目录的 `AGENTS.md`，并确认当前任务的要求、修改范围和边界；未完成阅读前不得编辑文件。
- AI 必须先检查现有实现和工作区状态，保留用户已有改动，只在明确请求范围内工作；不得因发现无关问题擅自扩展任务。
- AI 必须遵守本文件的语言、风格、架构、异常、日志、测试、文档和 Git 约定，保持代码风格与实现模式和原项目一致。
- AI 应优先复用现有辅助函数、配置 dataclass、`MediaMetadata` 契约和生命周期处理，不得为了方便引入临时绕过、重复实现、无明确需求的兼容层或架构重构。
- README 只放稳定且对用户有帮助的内容；常规更新、内部实现、维护记录和用户无需感知的变更不得写入 README，应放入 `CHANGELOG.md`、`docs/` 或提交记录。
- 完成修改后，AI 必须运行与改动直接相关的检查，说明验证结果和未验证部分；需求边界不明确时不得擅自扩大修改范围。
