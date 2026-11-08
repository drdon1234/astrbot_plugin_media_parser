# 文档索引

文档按用途维护：使用说明描述当前对外能力，架构说明与平台解析备忘对应当前实现。

| 文档 | 用途 |
|------|------|
| [使用说明](../README.md) | 快速开始、支持平台、常用设置和用户需要关注的限制 |
| [更新日志](../CHANGELOG.md) | 按版本记录变化；历史版本内容不作为当前能力清单 |
| [协作规范](../AGENTS.md) | 代码风格、模块边界、平台顺序与测试约定 |
| [架构说明](ARCHITECTURE.md) | 当前模块职责、数据契约、消息与缓存生命周期 |
| [平台解析备忘](PARSER_METHOD_MEMO.md) | 各平台链接范围、请求路径、媒体与评论边界 |
| [平台能力与实测登记](PLATFORM_CAPABILITIES.md) | 逐站登记五类内容的实现状态、取数来源、不支持原因、公开样本与验证缺口 |
| [字体资源说明](../resource/font/README.md) | 字体来源、下载校验、自定义字体与许可证 |

配置项名称、选项和界面默认值以根目录 [_conf_schema.json](../_conf_schema.json) 为准；类型转换、运行条件和实际默认行为同时核对 [core/config_manager.py](../core/config_manager.py)。运行依赖见 [requirements.txt](../requirements.txt)，AstrBot 版本范围见 [metadata.yaml](../metadata.yaml)。

配置面板使用 `object` 分组与 `condition` 同层条件显示；平台输出模式、热评平台按国内视频与音频、国内资讯、国外视频、国外资讯的顺序展开。`show_*` 展开开关只控制显示，不参与运行决策；收起时保留已保存的配置。维护排版时保留业务配置路径、类型、选项与默认值，避免引入配置迁移。字体与间距使用 `slider`，长图分页独立展示，因为它也作用于 README 截图。

配置排版参考[随机抽图插件](https://github.com/drdon1234/astrbot_plugin_random_pic/blob/master/_conf_schema.json)，字段支持以 [AstrBot 插件配置文档](https://github.com/AstrBotDevs/AstrBot/blob/master/docs/zh/dev/star/guides/plugin-config.md)及[插件配置页渲染组件](https://github.com/AstrBotDevs/AstrBot/blob/master/dashboard/src/components/shared/AstrBotConfig.vue)为准；不要直接套用仅核心配置页使用的显示属性。

## 维护范围

快速开始只保留安装、发送链接和按需配置的步骤；字体、浏览器与缓存等使用前提放在使用说明的对应章节。资源预算、取消清理、提链定位和平台取数路径等实现细节放在架构说明与平台解析备忘，版本变化记录在更新日志中。

本地调试与定向验证入口见[架构说明](ARCHITECTURE.md)，修改时同时核对协作规范、当前实现和相关测试。版本发布同步插件清单、入口注册与 README 标识，保留历史日志及许可证原文。

平台能力及取数边界变化时，同步维护[能力与实测登记](PLATFORM_CAPABILITIES.md)中的来源、失败原因与验证层级；接口细节仍归入平台解析备忘。未实现、匿名访问受限、环境失败与已验证不可用须分别记录，不以历史样本通过代替当前实测。

## 本地资料边界

本地 `test/` 下的测试、抓包和调研样本是验证材料，不属于发布文档，也不加入 Git。`.kiro/` 中的旧规格与迁移会话是历史记录，不代表当前架构；迁移快照保留原状且不提交。项目及字体许可证保留原文，分别见 [LICENSE](../LICENSE) 和 [字体许可证](../resource/font/LICENSE-NotoSansCJK.txt)。
