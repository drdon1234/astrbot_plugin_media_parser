# 掘金、CSDN、博客园、Gitee 与 GitLab 解析可行性调研

调研与实现核对日期：2026-09-24（北京时间）。以下保留接入前的实测依据；五个平台随后已接入，当前实现与边界见 [平台解析备忘](PARSER_METHOD_MEMO.md)。本次文档整理未重新执行下列在线请求。

## 结论

| 平台 | 正文或仓库概况的调研证据 | 评论调研结果 | 当前接入范围 |
| --- | --- | --- | --- |
| 稀土掘金 | 两篇公开文章的详情 JSON 和网页正文均成功，另验证一篇正文图片 | 两篇分别取得 3 条一级评论、2 条一级评论及 1 条作者回复；游标分页成功 | 已接入公开文章图文、热门评论与有限回复 |
| CSDN | 免费博客正文、代码块和图片成功；VIP 样本只有公开预览 | 免费样本两页各 10 条根评论，另含回复；另一作者样本取得 7 条评论 | 已接入公开文章图文、可见付费预览及评论/回复 |
| 博客园 | 两篇不同作者博客的标题、作者、时间和正文成功，正文图片可访问 | 两篇有 4/10 条评论，但接口只返回数量，明确要求登录查看 | 已接入公开博客图文，未接入评论 |
| Gitee | 两个公开仓库概况成功，包括迁移后的规范仓库信息 | Issue 评论两页各 5 条，均含正文、作者和时间，ID 不重复 | 已接入公开仓库概况、Issue 正文与评论；不下载正文附件 |
| GitLab | 两个普通项目和一个多级子组项目的公开概况成功；语言、许可证也已取得 | REST Notes 返回 401；官方 GraphQL 匿名取得两页讨论，共 12 条非系统记录 | 已接入 gitlab.com 公开仓库概况、Issue 正文与讨论；不下载正文附件 |

GitLab、Gitee 的评论样本来自明确的 Issue，不是仓库首页的“评论区”。仓库链接应展示本仓库概况；只有解析 Issue 链接时才附加该 Issue 的讨论，不能把最近几个 Issue 的评论混进仓库简介。

## 实测方法与边界

使用 Python 与现有 `aiohttp` 进行少量公开只读 HTTP 请求，未使用账号 Cookie、Token、浏览器登录态、浏览器执行环境或 MCP。GitLab、Gitee 使用 `DummyCookieJar`；掘金的详情、评论分页和图片也复核了禁用 Cookie 的请求。各请求均设置有界超时，没有修改评论、点赞或仓库的远端操作。

成功依据是实际取得正文或评论内容、作者等字段，并检查业务状态、分页和响应来源。HTTP 200、非零评论数量、页面中的接口名称均不单独作为成功依据。POST 请求只用于网站自身的只读查询接口。

结果仅代表本次公开样本与直连出口。没有验证全部文章类型、私有项目、付费全文、自建 GitLab 实例或 AstrBot 实际发送。正文图片的验证深度分别注明，不能由一个图床样本推断全部媒体可下载。

## 稀土掘金

### 正文与媒体

- [文章一：Vue3 + Element Plus + Vite 企业级后台框架搭建全流程](https://juejin.cn/post/7649586648073961510)：作者 mqcode，网页正文约 9328 字符；详情接口返回 32155 字符的 HTML。
- [文章二：Vue3 + ECharts 地图可视化](https://juejin.cn/post/7647395004021915694)：作者 漂流技术客，网页正文约 3458 字符；详情接口返回 21403 字符的 HTML。
- [正文图片样本](https://juejin.cn/post/7646255632630874152)：抽取一张带签名的 WebP，范围请求返回 HTTP 206，前 32 字节具有 RIFF/WEBP 文件头；未完整下载该图片。

当前公开前端使用：

```text
POST https://api.juejin.cn/content_api/v1/article/detail
```

实测参数：

```json
{"article_id":"7649586648073961510","client_type":2608,"req_from":0,"need_theme":true,"forbid_count":true,"is_pre_load":true,"prefer_html":true}
```

两个样本均返回 `err_no=0`。标题、正文、时间取自 `data.article_info` 的 `title`、`web_html_content`、`ctime`、`mtime`；作者取 `data.author_user_info.user_name`。单传 `article_id` 曾返回参数错误，不能误判为必须登录。上面是已验证的完整前端参数组合，未证明每一项都必需。

网页也直接包含 `h1.article-title`、`.author-name .name`、`time[datetime]`、`#article-root`。HTML 转文本须排除主题样式和脚本，保留代码块、表格、段落与链接。正文图片查询参数含过期时间和签名，应完整保留并及时下载。

### 评论与回复

```text
POST https://api.juejin.cn/interact_api/v1/comment/list
{"item_id":"文章ID","item_type":2,"cursor":"0","limit":20,"sort":0,"client_type":2608}

POST https://api.juejin.cn/interact_api/v1/reply/list
{"item_id":"文章ID","item_type":2,"comment_id":"一级评论ID","cursor":"0","limit":5,"client_type":2608}
```

一级评论在 `data[].comment_info`，包含 `comment_id`、`comment_content`、`ctime`、`digg_count`、`reply_count`，作者在同层 `user_info`。回复正文位于 `reply_info.reply_content`。文章一取得 3 条真实评论；文章二取得 2 条一级评论及 1 条作者回复，独立回复接口也成功。

当前前端枚举确认 `sort=0` 最新、`sort=1` 最热，两者均成功返回内容；本次样本点赞相同，不能据此证明热度排序的次序差异。设置 `limit=1`，使用服务端返回的 `cursor` 请求第二页，得到不同 ID 的评论，分页有效。`count` 可能包含回复，不能当作一级评论数。

另测 `/comment/hots` 返回空数组，但当前 `/comment/list` 有真实评论，接入应以当前前端调用为依据。匿名请求本次不需要签名；前端仍将这些接口注册到安全 SDK，风控风险不能忽略。建议有界获取评论，评论失败保留正文。

当前支持公开 `/post/<id>` 技术文章，也识别旧 `juejin.im` 的同 ID 路径；真实分享短链、沸点、付费小册未接入。

## CSDN

### 正文与媒体

- [免费样本](https://blog.csdn.net/qq_53153535/article/details/141297614)：作者 Chen.^，取得约 4238 字符正文、2 个代码块、5 张正文图片。首图带文章 Referer 完整下载成功，HTTP 200 / PNG / 501646 字节 / 2531×995。
- [VIP 样本](https://blog.csdn.net/Dong_HFUT/article/details/124290674)：作者 loongknown，返回约 2100 字符公开预览，并存在 `isVipArticle=true`、付费解锁文案和遮罩。不能认定已取得全文。

免费正文位于 `#content_views`，标题取 `h1#articleContentId`；作者与发布时间可读取 `article:author`、`article:published_time`。只抽正文容器，避免推荐、广告、侧栏和评论混入。图片来自正文 `img[src]`，代码块需保留换行。

### 评论与分页

由样本实际引用的[当前前端脚本](https://csdnimg.cn/release/blogv2/dist/pc/js/detail-3bce3ddbab.min.js)确认只读请求：

```text
POST https://blog.csdn.net/phoenix/web/v1/comment/list/{articleId}?page=1&size=10&fold=unfold
```

不附账号 Cookie、Token 或签名，带文章 Referer 即在本次样本中成功。响应 `code=200`，根评论在 `data.list[].info`，子回复在 `data.list[].sub[]`。可取 `commentId`、`content`、`nickName`、`userName`、`postTime`、`digg`、`parentId`。

| 样本请求 | 实际内容 |
| --- | --- |
| 免费文章，第 1 页 | 10 条根评论＋6 条回复 |
| 免费文章，第 2 页 | 10 条根评论＋12 条回复，根评论 ID 与第 1 页无重复 |
| VIP 文章，第 1 页 | 7 条公开评论 |
| 免费文章，`fold=fold` | 3 条公开折叠评论；建议默认只展示未折叠评论 |

样本根评论按时间倒序，没有验证独立的最热排序。`pageCount`、`floorCount`、`foldCount` 的语义不同，不应仅凭总数循环抓取。平台表情标记和回复关系需要清洗，评论失败应独立处理。

当前支持 `blog.csdn.net/{user}/article/details/{id}`。主要维护成本来自正文 DOM、网站内部评论接口和 WAF；调研时未触发挑战。付费或受限文章只输出实际可见部分，已识别的付费预览会标记为非完整内容。

## 博客园

### 公开博客正文可读

- [liulun 的样本](https://www.cnblogs.com/liulun/p/23092153)：1304 字符正文、5 张图片；首图 HEAD 返回 HTTP 200 / `image/png`。
- [WAKU 的样本](https://www.cnblogs.com/waku/p/23091139)：2871 字符正文，无正文图片。

正文取 `#cnblogs_post_body`，标题取 `#cb_post_title_url`，可见发布时间取 `#post-date`，作者可取 JSON-LD `author`。结构化数据的 `image` 为空时正文仍可能有图；样本 JSON-LD 时间与页面可见时间也有差异，不能只依赖 JSON-LD。

### 评论有明确登录限制

从当前 `blog-common.min.js` 定位到：

```text
GET https://www.cnblogs.com/{blog}/ajax/comments-block
    ?postId={id}&anchorCommentId=0&isDesc=false&order=0&loadCommentBox=true
```

请求带文章 Referer 和 `X-Requested-With: XMLHttpRequest`，两篇均返回 HTTP 200 / JSON。`commentCount` 分别为 4 和 10，但 `comments` 均为空；`commentForm` 明确包含“登录后才能查看或发表评论”。这不是零评论或网络故障，未取得非空评论正文。

前端存在 `pageIndex`、`order=0` 时间排序、`order=1` 点赞排序，但没有已成功内容可验证其排序和分页结果。登录态下是否可读未测试，不应先添加 Cookie 配置并宣称评论已支持。

当前已实现公开博客图文解析，评论登录限制不影响正文。样本为 `/{blog}/p/{id}`；接入时已补测 `.html`，可规范化到同一文章。历史日期路径和自定义域名未接入。

新闻子站须单独判断：[新闻样本](https://news.cnblogs.com/n/839522/)最终跳转到账号登录页，即使最终 HTTP 200 也不是新闻正文成功；本次不承诺新闻解析。

## Gitee

### 仓库概况已验证

[官方 API 文档](https://gitee.com/api/v5/swagger)及[文档 JSON](https://gitee.com/api/v5/doc_json)将仓库读取接口的 `access_token` 标为可选。本次匿名直连两个公开仓库均成功：

- [mirrors/Python](https://gitee.com/mirrors/Python)：ID 9138384，主要语言 Python。
- [chinabugotech/hutool](https://gitee.com/chinabugotech/hutool)：ID 164748，主要语言 Java、许可证 MulanPSL-2.0。

```text
GET https://gitee.com/api/v5/repos/{owner}/{repo}
```

可直接取 `full_name`、`description`、`namespace`、`language`、`license`、`stargazers_count`、`forks_count`、`updated_at`、`pushed_at`、`private`。

两个实测细节需单独处理：

1. 旧 `dromara/hutool` 接口没有 HTTP 跳转，直接以 200 返回 `chinabugotech/hutool`，其 ID 与新地址相同；网页也跳转至新地址。不能照搬“请求路径必须等于 full_name”的校验，否则迁移仓库会误报。应验证返回的仓库 ID、规范主机及结构，并使用规范链接。
2. API `html_url` 样本带 `.git`；展示链接可规范为仓库网页。组织仓库的 `owner.login` 还可能是管理员账号：`mirrors/Python` 返回 `mirrors_admin`，所属空间应优先参考 `namespace` / `full_name`。

### Issue 评论已验证

样本：[Hutool 迁移说明 Issue IBY7QA](https://gitee.com/chinabugotech/hutool/issues/IBY7QA)。

```text
GET https://gitee.com/api/v5/repos/chinabugotech/hutool/issues/IBY7QA/comments?per_page=5&page=1
```

第 1、2 页各返回 5 条非空评论，ID 无重复，包含 `id`、`body`、`user.login`、`created_at`、`updated_at`，部分含 `in_reply_to_id`。已实际读到“请问 Hutool-7 大概什么时候会有第一个版本？”及作者回复等内容。

接入时另验证单 Issue 详情接口，可取得正文并核对仓库身份。官方文档说明该评论接口 Token 可选，支持 `page`、`per_page` 和 `order=asc/desc`。本次默认时间升序，没有点赞字段或最热排序证据，缺失点赞不要写成 0。Issue 编号区分大小写，不要按 GitHub 的纯数字编号规则实现。

仓库概况和 Issue 评论均可用现有 HTTP 依赖实现，维护成本较低；本次未验证私有仓库和登录态权限，不需要为了公开仓库概况先增加 Token 配置。

## GitLab

### 仓库概况已验证

[官方 Projects API 文档](https://docs.gitlab.com/api/projects/)明确公开项目可以匿名读取。本次以下项目均 HTTP 200、`visibility=public`：

- [gitlab-org/gitlab](https://gitlab.com/gitlab-org/gitlab)：项目 ID 278964。
- [gitlab-org/gitlab-runner](https://gitlab.com/gitlab-org/gitlab-runner)：项目 ID 250833。
- [多级子组项目 secrets](https://gitlab.com/gitlab-org/security-products/analyzers/secrets)：项目 ID 10861561。

```text
GET https://gitlab.com/api/v4/projects/{URL编码的完整namespace/project路径}
GET https://gitlab.com/api/v4/projects/250833?license=true
GET https://gitlab.com/api/v4/projects/250833/languages
```

字段包括 `name`、`path_with_namespace`、`description`、`namespace`、`star_count`、`forks_count`、`web_url`、`last_activity_at`。`license=true` 已返回 MIT License，语言接口返回 Go 98.38% 等占比。`last_activity_at` 表示项目活动时间，不应标成最后代码提交时间。匿名 REST 样本没有返回 `archived`；额外 GraphQL 查询确认 Runner 的 `archived=false`，缺字段时应省略状态，不能默认未归档。

与 GitHub 的差异：仓库可能位于多级子组，不能限定恰好两段路径；要以 `/-/` 区分 Issue、文件等子页面。URL 规范化保留完整命名空间并正确编码。先限定 `gitlab.com`，不能由本次成功推断所有自建实例的认证、版本和网络策略都相同。

### Issue 讨论也有匿名路径

样本：[Runner Issue 39178](https://gitlab.com/gitlab-org/gitlab-runner/-/work_items/39178)。公开 Issue 列表含正文与作者，Notes REST 请求 `/api/v4/projects/250833/issues/39178/notes` 的两页均返回 `401 Unauthorized`。

[官方 GraphQL](https://docs.gitlab.com/api/graphql/)的 `/api/graphql` 匿名查询则成功：

```graphql
query {
  project(fullPath: "gitlab-org/gitlab-runner") {
    issue(iid: "39178") {
      discussions(first: 5) {
        nodes {
          notes(first: 10) {
            nodes { id body system createdAt author { username } }
            pageInfo { hasNextPage endCursor }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
```

实际第一页取 5 个讨论，含 7 条记录，其中 6 条 `system=false`；带返回游标请求第二页得到 8 条记录，其中 6 条非系统记录，两页 ID 无重叠。接入时另验证单 Issue REST 详情接口，可取得正文、作者并核对项目身份。非系统记录仍可能由机器人发表，不等同于全是人工评论。系统状态变更应排除或单独标记。

本次未取点赞和最热排序，保留讨论/回复关系；`discussions` 与每个 `notes` 都要有数量上限，不能无限展开。仓库概况的接入成本较低，Issue 讨论为中等。

## 当前项目适配

1. 掘金、CSDN、博客园分别在 `core/parser/platform/juejin.py`、`csdn.py`、`cnblogs.py` 实现公开文章解析。正文与图片清洗保留在各自平台内；CSDN 标记可见的付费预览，博客园没有评论开关。
2. Gitee、GitLab 分别在 `core/parser/platform/gitee.py`、`gitlab.py` 实现仓库和 Issue 的文字元数据。输入仓库只输出概况，输入 Issue 才附加该 Issue 的讨论；GitLab 的 `work_items` 路径仍按 Issue 详情解析，不代表支持所有工作项类型。
3. 五个平台复用 `MediaMetadata`、现有下载与文本渲染以及 `parsers.<平台>` 输出模式控制。掘金、CSDN、Gitee、GitLab 的附加评论受 `message.hot_comments.count` 和同名平台开关控制；默认数量为 `0`，仅富媒体模式不请求评论。
4. 掘金使用 `sort=1` 的热门列表；CSDN 保留接口评论顺序；Gitee、GitLab 展示普通议题讨论，不承诺最热排序。附加评论失败保留正文，无新增代理、登录配置或运行依赖。

## 本地证据

原始响应、抽取结果和调研脚本均保存在被忽略的 `test/` 下，不纳入 Git，也不随仓库分发：

- 掘金：`test/tech_platform_research/juejin/notes.md`、`extracted.json`、`detail_full_*.body`、`comments_page*.body`、`replies.body`、`image_probe.json`。
- CSDN：`test/tech_platform_research/csdn/notes.md`、`summary.json`、文章/评论 `*.response` 及同名请求元数据。
- 博客园：`test/tech_platform_research/cnblogs/notes.md`、`summary.json`、`comments_*_xhr.json`、正文 HTML 和请求记录。
- GitLab/Gitee：`test/tech_platform_research/code_hosts/verified_summary.json`、对应 `*.body`；只读请求脚本为 `test/tech_hosts*.py`。

已针对保存响应核对业务状态、正文/评论非空、作者字段和有效分页去重；没有运行与调研无关的全量回归。

## 接入验证

五个解析器已实现，沿用平台注册、输出模式、全局评论条数和消息链路，无新增依赖或代理/登录配置。定向测试覆盖链接边界、身份核验、正文隔离、签名图片、付费预览、分页去重、失败保留正文、取消传播，以及配置和消息构建；测试文件仍保留在忽略的 `test/` 中。

生产 `parse()` 的匿名在线样本共 9 项成功：掘金两篇，CSDN 免费/付费预览各一篇，博客园一篇，Gitee 和 GitLab 各一个仓库及一个 Issue。掘金、CSDN 免费文章及两个代码托管 Issue 均取得 3 条评论；博客园按已确认的登录限制不请求评论。在线成功只代表这些样本和当前网络出口，未验证 AstrBot 内实际发送。
