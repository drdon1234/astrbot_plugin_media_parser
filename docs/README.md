# 文档索引

文档按用途维护：使用说明描述当前对外能力，架构说明与平台解析备忘对应当前实现。

| 文档 | 用途 |
|------|------|
| [使用说明](../README.md) | 安装、支持平台、常用设置和用户需要关注的限制 |
| [更新日志](../CHANGELOG.md) | 按版本记录变化；历史版本内容不作为当前能力清单 |
| [协作规范](../AGENTS.md) | 代码风格、模块边界、平台顺序与测试约定 |
| [架构说明](ARCHITECTURE.md) | 当前模块职责、数据契约、消息与缓存生命周期 |
| [平台解析备忘](PARSER_METHOD_MEMO.md) | 各平台链接范围、请求路径、媒体与评论边界 |
| [字体资源说明](../resource/font/README.md) | 字体来源、下载校验、自定义字体与许可证 |

配置项名称、选项和界面默认值以根目录 [_conf_schema.json](../_conf_schema.json) 为准；类型转换、运行条件和实际默认行为同时核对 [core/config_manager.py](../core/config_manager.py)。运行依赖见 [requirements.txt](../requirements.txt)，AstrBot 版本范围见 [metadata.yaml](../metadata.yaml)。

## 本地资料边界

本地 `test/` 下的测试、抓包和调研样本是验证材料，不属于发布文档，也不加入 Git。`.kiro/` 中的旧规格与迁移会话是历史记录，不代表当前架构；迁移快照保留原状且不提交。项目及字体许可证保留原文，分别见 [LICENSE](../LICENSE) 和 [字体许可证](../resource/font/LICENSE-NotoSansCJK.txt)。
