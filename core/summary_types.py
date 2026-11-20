"""内容总结分类、链接子类型与排除范围判定。"""

from urllib.parse import urlsplit


CATEGORY_LABELS = {"article": "文章与回答", "post": "帖子与动态", "gallery": "图集与作品",
                   "item": "商品与条目", "repository": "代码仓库", "issue": "Issue 与工单"}

# 每个平台仅列出能从当前解析结果确认的链接类型。
LINK_TYPES = {
    "bilibili": {"post": ("post", "动态与 Opus")},
    "douyin": {"gallery": ("gallery", "图集")},
    "kuaishou": {"gallery": ("gallery", "图集")},
    "acfun": {"article": ("article", "文章与动态")},
    "hongguo": {"intro": ("item", "剧集简介")},
    "netease": {"info": ("item", "单曲资料")},
    "ximalaya": {"intro": ("item", "节目简介")},
    "weibo": {"post": ("post", "微博正文")},
    "xiaohongshu": {"note": ("post", "图文笔记")},
    "xianyu": {"item": ("item", "商品详情")},
    "toutiao": {"article": ("article", "文章"), "post": ("post", "微头条")},
    "xiaoheihe": {"post": ("post", "BBS 帖子"), "game": ("item", "游戏详情")},
    "xueqiu": {"post": ("post", "帖子与转发"), "article": ("article", "长文")},
    "coolapk": {"post": ("post", "动态与转发"), "gallery": ("gallery", "酷图"), "article": ("article", "图文长文"),
               "reply": ("post", "指定评论"), "question": ("post", "问答"), "vote": ("post", "投票"),
               "app": ("item", "应用与游戏"), "user": ("item", "用户概况"), "topic": ("item", "话题"),
               "product": ("item", "产品"), "collection": ("item", "收藏单"), "dyh": ("item", "看看号")},
    "wechat": {"article": ("article", "公众号文章"), "gallery": ("gallery", "公众号图集")},
    "zhihu": {"answer": ("article", "指定回答"), "column": ("article", "专栏文章")},
    "tieba": {"post": ("post", "帖子")}, "nga": {"post": ("post", "帖子")}, "hupu": {"post": ("post", "帖子")},
    "douban": {"item": ("item", "条目与活动"), "article": ("article", "长评与日记"), "post": ("post", "讨论与短评"),
               "gallery": ("gallery", "照片与相册"), "reading": ("item", "阅读作品介绍")},
    "v2ex": {"post": ("post", "主题")}, "juejin": {"article": ("article", "文章")},
    "csdn": {"article": ("article", "博客文章")}, "cnblogs": {"article": ("article", "博客文章")},
    "gitee": {"repo": ("repository", "仓库"), "issue": ("issue", "Issue")},
    "tiktok": {"gallery": ("gallery", "照片帖")}, "youtube": {"intro": ("item", "视频说明")},
    "steam": {"game": ("item", "商店游戏"), "guide": ("article", "指南"), "workshop": ("item", "创意工坊物品与合集")},
    "twitter": {"post": ("post", "推文"), "article": ("article", "长文 Article")},
    "pixiv": {"gallery": ("gallery", "插画与漫画")},
    "github": {"repo": ("repository", "仓库"), "issue": ("issue", "Issue")},
    "gitlab": {"repo": ("repository", "仓库"), "issue": ("issue", "Issue")},
}


def classify_content(item: dict) -> tuple[str, str, str]:
    """按解析器确认的类型与规范地址返回平台、子类型和总结类别。"""
    platform = item.get("parser_name") or item.get("platform", "")
    kinds = LINK_TYPES.get(platform, {})
    if not kinds:
        return "", "", ""
    try:
        address = urlsplit(item.get("url") or "")
        path = address.path.lower()
    except ValueError:
        return "", "", ""
    subtype = item.get("summary_subtype")
    if subtype is None:
        if platform in {"github", "gitlab", "gitee"}:
            subtype = "repo" if item.get("readme_url") else "issue"
        elif platform == "zhihu":
            subtype = "answer" if "/answer/" in path else "column"
        elif platform in {"toutiao", "wechat"}:
            subtype = ""
        elif platform == "acfun" and "/a/ac" not in path:
            subtype = ""
        elif platform == "bilibili" and "/opus/" not in path and address.hostname != "t.bilibili.com":
            subtype = ""
        elif platform in {"douyin", "kuaishou", "tiktok"} and (
                not item.get("image_urls") or item.get("video_urls") or item.get("converted_cover_count")):
            subtype = ""
        elif platform == "xiaohongshu" and (item.get("video_urls") or item.get("converted_cover_count")):
            subtype = ""
        else:
            subtype = next(iter(kinds))
    return platform, subtype, kinds.get(subtype, ("", ""))[0]
