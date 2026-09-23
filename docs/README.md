# 文档索引

文档按用途维护：使用说明描述当前对外能力，架构与解析备忘对应当前实现，调研文档保留有日期的验证证据。调研中可行的接口或接入建议不等于插件已支持。

## 使用与维护

| 文档 | 用途 |
|------|------|
| [使用说明](../README.md) | 安装、平台媒体类型、常用设置和用户需要关注的限制 |
| [更新日志](../CHANGELOG.md) | 按版本记录变化；历史版本内容不作为当前能力清单 |
| [协作规范](../AGENTS.md) | 代码风格、模块边界、平台顺序与测试约定 |
| [架构说明](ARCHITECTURE.md) | 当前模块职责、数据契约、消息与缓存生命周期 |
| [平台解析备忘](PARSER_METHOD_MEMO.md) | 各平台链接范围、请求路径、媒体与评论边界 |
| [字体资源说明](../resource/font/README.md) | 字体来源、下载校验、自定义字体与许可证 |

配置项名称、选项和界面默认值以根目录 [_conf_schema.json](../_conf_schema.json) 为准；类型转换、运行条件和实际默认行为同时核对 [core/config_manager.py](../core/config_manager.py)。运行依赖见 [requirements.txt](../requirements.txt)，AstrBot 版本范围见 [metadata.yaml](../metadata.yaml)。

## 调研记录

| 文档 | 内容 |
|------|------|
| [评论能力调研](COMMENT_FEASIBILITY.md) | 评论来源、当前接入范围与有界请求策略 |
| [论坛平台调研](FORUM_PARSER_FEASIBILITY.md) | V2EX 及候选社区的接口证据与接入结论 |
| [技术平台调研](TECH_PLATFORM_FEASIBILITY.md) | 技术博客、代码托管平台的调研与落地边界 |
| [音频平台调研](MUSIC_PLATFORM_FEASIBILITY.md) | 音乐与播客候选、当前音频链路和访问权限 |

调研结果受测试时间、网络和平台状态影响。更新时修正“当前实现”与“后续建议”，保留历史观察的日期、来源和限制，不将单次成功推广为长期可用承诺。

## 本地资料边界

本地 `test/` 下的测试、抓包和调研样本是验证材料，不属于发布文档，也不加入 Git。`.kiro/` 中的旧规格与迁移会话是历史记录，不代表当前架构；迁移快照保留原状且不提交。项目及字体许可证保留原文，分别见 [LICENSE](../LICENSE) 和 [字体许可证](../resource/font/LICENSE-NotoSansCJK.txt)。
