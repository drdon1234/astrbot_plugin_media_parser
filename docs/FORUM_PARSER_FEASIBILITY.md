# V2EX、Linux.do 与 NodeSeek 解析可行性调研

调研与实现核对日期：2026-09-24（北京时间）。本文保留接入前的匿名请求与访问条件调研；V2EX 随后已接入，Linux.do、NodeSeek 仍未接入。本次文档整理未重新执行下列在线请求。

## 结论

| 平台 | 公开主题匿名解析 | 不手工配置 Cookie 的路径 | 当前实现状态 |
| --- | --- | --- | --- |
| V2EX | 已实测成功：主题 JSON、网页正文和样本图片均可匿名访问 | 现有 `aiohttp` 直接请求，无需账号、Token 或浏览器 | 已实现公开主题正文、图片和有限范围内优先高感谢数的评论 |
| Linux.do | Discourse 协议支持公开主题匿名读取，但调研时本站请求均遇到 Cloudflare 挑战 | 可先验证部署出口的匿名请求；浏览器会话或用户 API Key 是条件性方案，两者解决的问题不同 | 未接入，尚不能承诺零配置稳定运行 |
| NodeSeek | RSS 最新帖摘要已实测成功；任意帖子完整正文在调研出口被挑战拦截 | 专属浏览器会话自动保存验证或登录状态，有第三方客户端实践 | 未接入，RSS 不能替代通用帖子解析 |

这里需要区分三个目标：

- **匿名**：不登录站点账号；匿名访问仍可能产生访客或反爬 Cookie。
- **不手工配置 Cookie**：可以由程序管理会话，也可以采用用户授权的 Token；不等于完全不需要登录或交互。
- **无人值守稳定运行**：挑战到期、请求出口变化和站点权限变化后仍能自动恢复。现有证据不足以对 Linux.do、NodeSeek 作此承诺。

## 实测方法与边界

使用当前机器默认网络出口，进行少量公开只读 HTTP 请求，没有发送账号 Cookie 或 Authorization，没有读取本机浏览器资料，也没有启动浏览器、MCP 服务或登录站点。V2EX 额外用 `aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar())` 验证了现有依赖下的匿名请求。

结论仅代表本次样本和出口。Cloudflare 返回 `403`、`cf-mitigated: challenge` 与挑战 HTML 时，说明请求被边缘验证拦截，不能据此推断帖子需要登录、已删除，或没有公开接口。

## V2EX

### 已验证的数据路径

| 请求 | 实测结果 |
| --- | --- |
| [旧主题接口，主题 1](https://www.v2ex.com/api/topics/show.json?id=1) | HTTP 200，返回主题 JSON |
| [旧主题接口，图文主题 1244361](https://www.v2ex.com/api/topics/show.json?id=1244361) | HTTP 200，含标题、正文 HTML、作者、时间、节点和回复数；`aiohttp` 同样成功 |
| [对应网页](https://www.v2ex.com/t/1244361) | HTTP 200，`.topic_content` 含正文和图片 |
| [正文中的样本图片](https://i.v2ex.co/Z6Gr57KB.png) | HEAD HTTP 200，`image/png` |
| [新版主题接口，无 Token](https://www.v2ex.com/api/v2/topics/1) | HTTP 401，返回 `Token not found` |

旧主题接口的 `title`、`member.username`、`created`、`content`、`content_rendered` 可直接映射到项目现有元数据。解析 `content_rendered` 中的图片即可取得媒体地址，不需要为普通公开主题引入账号会话。

样本接口未返回独立的附言列表，当前解析器也没有抓取网页附言；不能把接口主正文宣称为整个页面的全部内容。外链图床是否可下载也要逐个处理，不能由一个样本图片成功推断所有图床都可用。

### 官方文档与维护条件

- [现行官方 API 文档](https://www.v2ex.com/help/api)说明新版 API 使用 Personal Access Token，通过 `Authorization: Bearer` 传递。它可以免复制 Cookie，但仍需创建、提供并维护 Token。
- [官方个人访问令牌说明](https://www.v2ex.com/help/personal-access-token)给出的最长有效期为 180 天。
- [旧官方 API 说明](https://www.v2ex.com/p/7v9TEc53)将列出的部分旧接口标为 `Authentication: None`，但未列出本次使用的 `topics/show.json`。该详情接口的可用性依据本次实测，不能说旧文档明确承诺了它。
- [RSSHub 的 V2EX 帖子实现](https://github.com/DIYgod/RSSHub/blob/master/lib/routes/v2ex/post.ts)使用旧主题接口和回复接口，是可参考的第三方实现先例。

本次旧 API 响应头 `x-rate-limit-limit` 为 `600`；旧文档曾写 `120`。接入时应尊重实际限流响应、控制并发和重复请求，不把历史额度写死。登录受限主题、新版 Token 对这些主题的权限均未验证；匿名支持范围应限定为接口实际返回的公开主题。

### 当前接入范围

`core/parser/platform/v2ex.py` 已实现公开 `/t/<id>` 主题的标题、作者、时间、主帖正文和图片，使用旧主题接口，并核对返回的主题 ID 与规范链接。没有新增 Cookie、Token、代理或浏览器配置。

评论复用 `message.hot_comments.count` 和 `message.hot_comments.v2ex`：默认全局数量为 `0`；平台评论关闭或仅输出富媒体时不请求。开启后只请求一次匿名旧回复接口，再最多读取前 5 页主题网页的 JSON-LD 感谢数，将 ID、作者与时间对应的回复在已取得范围内排序。网页不可用时保留接口楼层顺序，未知感谢数省略；这不代表官方热评或全帖完整热度排名。附加评论失败保留主帖。

当前实现不展开附言、不识别外站播放器为视频，也不读取需要账号权限的主题。注册、输出模式和本地发现已同步；实现细节见 [平台解析备忘](PARSER_METHOD_MEMO.md)。

## Linux.do

### 标准协议明确，本站访问仍需验证

Linux.do 使用 Discourse。公开主题的标准读取路径是 `/t/{topic_id}.json`，主要字段为：

- `title`：主题标题。
- `post_stream.posts` 中 `post_number == 1` 的帖子：楼主正文，不能直接把任意返回楼层当成主帖。
- 首帖的 `username`、`created_at`、`cooked`：作者、时间和已渲染正文 HTML。
- 正文 HTML：可提取图片和原生媒体地址，外站播放器是否支持需另行判断。

[Discourse 官方 TopicsController](https://github.com/discourse/discourse/blob/main/app/controllers/topics_controller.rb)没有将 `show`、`feed` 列入该控制器的强制登录动作，但仍执行权限判断；[TopicGuardian](https://github.com/discourse/discourse/blob/main/lib/guardian/topic_guardian.rb)处理分类权限、私信等限制。站点全局设置也可能要求登录，因此通用协议能力不等于每个部署都允许匿名访问。

作为协议对照，本次匿名访问 [Discourse 官方社区主题 JSON](https://meta.discourse.org/t/48536.json)成功，取得首帖作者及 `cooked`。这不是 Linux.do 本站成功记录。[官方 API schema](https://docs.discourse.org/openapi.json)可以核对字段，但不能仅凭其通用鉴权头描述判断公开网页 JSON 一律需要管理员 API Key。

本站实测：

| 请求 | 实测结果 |
| --- | --- |
| [最新主题 JSON](https://linux.do/latest.json) | HTTP 403，Cloudflare challenge |
| [最新主题 RSS](https://linux.do/latest.rss) | HTTP 403，同上 |
| [主题 521627 JSON](https://linux.do/t/topic/521627.json) | HTTP 403，同上 |
| [主题 521627 RSS](https://linux.do/t/topic/521627.rss) | HTTP 403，同上 |
| [用户 API Key 授权入口](https://linux.do/user-api-key/new) | HTTP 403，同上 |

521627 来自[相关项目作者发布的公开链接](https://github.com/Cunninger/ocr-based-qwen/issues/4)，未通过枚举取得；本次被挑战拦截，无法确认其当前可见性。没有成功提取本站正文，RSS 也未形成可用替代路径。

### 不复制 Cookie 的用户授权方案

[Discourse 官方 User API keys specification](https://meta.discourse.org/t/user-api-keys-specification/48536)提供客户端授权流程：客户端生成 RSA 公钥和 nonce，用户在浏览器打开 `/user-api-key/new` 并登录授权，客户端接收并解密 payload，此后使用 `User-Api-Key` 请求。只读解析可申请 `read` 权限，无需让用户复制浏览器 Cookie。

这不是普通用户直接取得管理员 `Api-Key` 的方案。[官方授权控制器](https://github.com/discourse/discourse/blob/main/app/controllers/user_api_keys_controller.rb)和[站点配置](https://github.com/discourse/discourse/blob/main/config/site_settings.yml)还会限制允许的 scopes、用户组和回调地址。

Linux.do 历史上存在[公开使用脚本](https://github.com/Pleasurecruise/linux-do-mcp/blob/main/src/get-pat.py)，申请 `read` 并让用户粘贴授权后的加密 payload，引用了[站内令牌说明主题](https://linux.do/t/topic/31549)。该脚本只是第三方历史证据；本站当前是否开放授权、允许哪些用户组及回调，本次未登录验证。

**Token 不能替代 Cloudflare 验证。** [使用者反馈](https://github.com/Pleasurecruise/linux-do-mcp/issues/3#issuecomment-2843964355)曾报告带不带 API Key 都会被 Cloudflare 拦截；本次无 Token 请求也触发了挑战，没有测试有效 Token。

另有 [RSSHub PR #23041](https://github.com/DIYgod/RSSHub/pull/23041)报告匿名 Chromium 在某个出口成功读取 Linux.do RSS，而普通 HTTP 失败。该 PR 已关闭且未合并，这仅是第三方成功案例，不是本次浏览器实测，也不是稳定性保证。

**建议：公开主题匿名 JSON 是首选设计；先在实际 AstrBot 部署出口验证。若必须依赖浏览器，应单独评估运行依赖和验证流程；如需账号权限，再核实本站 User API Key 授权是否可用。**

## NodeSeek

### RSS 可匿名读取，但不是完整帖子接口

| 请求 | 实测结果 |
| --- | --- |
| [主站首页](https://www.nodeseek.com/) | HTTP 403，Cloudflare challenge |
| [RSS 中的实际帖子](https://www.nodeseek.com/post-945326-1) | HTTP 403，同上 |
| [RSS 子域](https://rss.nodeseek.com/) | HTTP 200，XML |
| [主站 RSS 路径](https://www.nodeseek.com/rss.xml) | HTTP 200，XML |

本次 RSS 返回 20 条最新帖子，有标题、链接、作者、分类、发布时间，部分有 `description`。没有 `content:encoded` 或图片字段；描述为纯文本，长文存在截断，图片分享帖可能没有描述。

因此 RSS 适合近期动态预览，不能保证任意历史链接命中，更不能冒充完整正文与图片解析。

### 完整帖子的已知实现路径

本次未找到可依赖的 NodeSeek 官方公开帖子详情 API 文档。这个结论不等于断言该站完全没有 JSON 接口。

第三方客户端 [Nodyssey](https://github.com/5151561/nodyssey)提供了较明确的参考：

- [README](https://github.com/5151561/nodyssey/blob/main/README.md)说明主题详情依赖服务端 HTML，使用 WebView 管理会话；这是第三方实践，不是官方接口承诺。
- [帖子解析器](https://github.com/5151561/nodyssey/blob/main/shared/src/commonMain/kotlin/io/github/nodyssey/core/html/PostDetailParser.kt)读取 `/post-{id}-{page}`，区分楼主首帖和回复，提取标题、作者、时间与富文本。
- [HTML 选择器](https://github.com/5151561/nodyssey/blob/main/shared/src/commonMain/kotlin/io/github/nodyssey/core/html/Selectors.kt)使用 `div.nsk-post > div.content-item`、`article.post-content` 等结构，也识别注册、等级和私有帖子限制。
- [JSON 客户端](https://github.com/5151561/nodyssey/blob/main/shared/src/commonMain/kotlin/io/github/nodyssey/core/net/NodeSeekJsonClient.kt)包含按用户列主题、列评论的接口，这些不等于按帖子 ID 获取完整正文的接口；详情仍走 HTML。
- [会话处理](https://github.com/5151561/nodyssey/blob/main/shared/src/commonMain/kotlin/io/github/plaza/core/net/SessionCookies.kt)区分 `cf_clearance` 和账号登录会话，验证通过与登录是两个独立问题。

无需手填 Cookie 的条件性方案是：使用插件专属浏览器会话，必要时让用户完成站点验证或登录，由程序保存并复用会话。公开帖可能只需访客验证，受限帖仍需具备相应账号权限。本次没有运行浏览器验证此方案，也没有确认挑战后的 Cookie 能否在当前 `aiohttp` 传输环境稳定复用。

**建议：如果目标是现有依赖下低维护的完整图文解析，NodeSeek 排在后面。不要用最新 RSS 摘要替代完整解析后仍标为成功。**

## 对尚未接入平台的建议

当前 `BaseVideoParser` 和 `MediaMetadata` 已能表示标题、作者、时间、正文及图片，不需要为 Linux.do、NodeSeek 新建数据契约。若后续接入，可沿用 `can_parse`、`extract_links`、`parse`，以楼主首帖为首期范围，评论数量和分页另按需求设计。

V2EX 已独立落地。Linux.do 和 NodeSeek 的优先任务仍是确认可持续的数据访问路径；单纯编写 HTML/JSON 字段提取代码，不能解决前面的 Cloudflare 验证问题。

若以后接受浏览器运行时，需要把会话获取、存储、失效重验和退出清理作为实际功能实现，并考虑 AstrBot 常见的无桌面 Docker 部署。用户在本机浏览器完成验证后，不能假设该会话一定可迁移到服务器出口。平台专属逻辑应归属各自解析器，不新建跨平台抓取框架，也不默认引入第三方镜像或验证码服务。

Cloudflare 官方说明：[Challenge Pages](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/)需要浏览器执行验证，可能要求用户交互；[支持的浏览器](https://developers.cloudflare.com/cloudflare-challenges/reference/supported-browsers/)明确说明自动化浏览器不受支持。因此不能把 Playwright、换 User-Agent 或更换 TLS 指纹描述为必定成功的解决办法。

后续实际接入 Linux.do、NodeSeek 时再同步平台注册、配置 schema、本地发现顺序和用户文档；仅在确认请求或媒体下载确实需要代理时添加相应配置。当前未为这两站添加配置或注册，也不将未验证能力写入 README。
