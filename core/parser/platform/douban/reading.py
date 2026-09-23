"""解析豆瓣阅读的公开作品介绍、阅读评论与匿名热评。"""

import asyncio
import re
from typing import Any, Dict, List, Tuple
from urllib.parse import urljoin, urlsplit

import aiohttp

from ....logger import logger

from ....types import MediaMetadata
from .content import Node, images_in, json_comment, media_url, meta, parse_html, text_of
from .web import DoubanWeb


READING_ORIGIN = "https://read.douban.com"
_IDENTIFIER = r"[A-Za-z_$][\w$]*"


def _reading_target(url: str) -> Tuple[str, str, str]:
    """将已知阅读器链接归到作品介绍，拒绝列表及非本站链接。"""
    parts = urlsplit(url)
    if (
        parts.hostname != "read.douban.com"
        or parts.scheme not in {"https", "http"}
        or parts.username is not None or parts.password is not None
        or parts.port not in {None, 80, 443}
    ):
        raise ValueError("豆瓣阅读链接无效")
    match = re.fullmatch(
        r"/(?:reader/)?(ebook|column|review)/(\d+)(?:/chapter/\d+|/toc/\d+)?/?",
        parts.path,
    )
    if not match:
        raise ValueError("暂不支持该豆瓣阅读链接类型")
    kind, identity = match.groups()
    if kind == "review" and (parts.path.startswith("/reader/") or "/chapter/" in parts.path or "/toc/" in parts.path):
        raise ValueError("豆瓣阅读评论链接无效")
    if (kind == "ebook" and "/chapter/" in parts.path) or (kind == "column" and "/toc/" in parts.path):
        raise ValueError("豆瓣阅读章节链接无效")
    return kind, identity, f"{READING_ORIGIN}/{kind}/{identity}/"


def _work_metadata(root: Node, kind: str, identity: str, url: str) -> MediaMetadata:
    """提取作品本身的完整介绍，排除推荐作品和阅读器片段。"""
    canonical = meta(root, "og:url")
    actual_kind, actual_id, canonical_url = _reading_target(canonical)
    same_work = actual_kind == kind and actual_id == identity
    redirected_column = (
        kind == "ebook" and actual_kind == "column"
        and root.find(attr="data-works-id", value=identity) is not None
    )
    if not same_work and not redirected_column:
        raise ValueError("豆瓣阅读页面与请求作品不匹配")
    title = meta(root, "og:novel:book_name") if actual_kind == "column" else meta(root, "og:title")
    body = root.find(attr="itemprop", value="description")
    if not title or body is None or not body.text():
        raise ValueError("豆瓣阅读未返回完整的公开作品介绍")
    author = meta(root, "og:novel:author")
    details = []
    if actual_kind == "ebook":
        attributes = root.find(cls="article-meta")
        if attributes:
            for line in attributes.find_all("p"):
                label = text_of(line.find(cls="label"))
                value = text_of(line.find(cls="labeled-text"))
                if label == "作者":
                    author = value
                elif label and value:
                    details.append(f"{label}：{value}")
        rating = root.find("meta", attr="itemprop", value="ratingValue")
        if rating and rating.attrs.get("content"):
            details.append(f"评分：{rating.attrs['content']}")
    else:
        for key, label in (("og:novel:category", "分类"), ("og:novel:status", "状态")):
            if value := meta(root, key):
                details.append(f"{label}：{value}")
        word_count = root.find(cls="word-count")
        if word_count and (value := text_of(word_count.find(cls="text"))):
            details.append(f"字数：{value}")
    details.append("作品简介：\n" + body.text())
    result: MediaMetadata = {
        "url": canonical_url, "platform": "douban", "title": title,
        "author": author, "desc": "\n\n".join(details),
    }
    cover = media_url(meta(root, "og:image"))
    if cover:
        result["image_urls"] = [[cover]]
        result["image_headers"] = {"Referer": url}
    if timestamp := meta(root, "og:novel:update_time"):
        result["timestamp"] = timestamp
    return result


def _review_metadata(root: Node, url: str) -> MediaMetadata:
    """只读取阅读评论的公开正文，不以页头摘要补齐缺失内容。"""
    article = root.find(id="page-static-content")
    content = article.find(cls="content") if article else None
    header = article.find("header") if article else None
    links = header.find_all("a") if header else []
    if content is None or not content.text() or len(links) < 2 or root.find(id="comment-root") is None:
        raise ValueError("豆瓣阅读评论正文不可公开读取")
    author_link = links[0].attrs.get("href", "")
    if urlsplit(urljoin(READING_ORIGIN, author_link)).hostname != "read.douban.com":
        raise ValueError("豆瓣阅读评论作者信息无效")
    title = text_of(article.find(cls="title")) or f"《{links[1].text()}》的评论"
    result: MediaMetadata = {
        "url": url, "platform": "douban", "title": title,
        "author": links[0].text(), "desc": content.text(),
        "timestamp": text_of(article.find("time")),
    }
    images = images_in(content)
    if images:
        result["image_urls"] = images
        result["image_headers"] = {"Referer": url}
    return result


def _script_url(root: Node, name: str) -> str:
    """从当前页面选择固定职责的官网脚本。"""
    for node in root.find_all("script"):
        url = node.attrs.get("src", "")
        parts = urlsplit(url)
        if (
            parts.scheme == "https" and parts.hostname == "static.arkread.com"
            and parts.port in {None, 443} and parts.username is None
            and re.fullmatch(r"/ark/store/" + re.escape(name) + r"\.[a-f0-9]+\.js", parts.path)
        ):
            return url
    raise ValueError("豆瓣阅读评论脚本入口发生变化")


def _comment_chunk(app_source: str, app_url: str, kind: str) -> str:
    """从当前路由引用确定评论页面代码块，不固定构建文件名。"""
    route = "/review/:reviewId" if kind == "review" else "/:worksType/:worksId/comments"
    route_start = app_source.find('path:"' + route + '"')
    if route_start < 0:
        raise ValueError("豆瓣阅读评论路由发生变化")
    route_end = app_source.find("{path:", route_start + 1)
    route_source = app_source[route_start:route_end if route_end >= 0 else route_start + 1000]
    pattern = (
        rf"({_IDENTIFIER})=\(0,{_IDENTIFIER}\.lazy\)\(\(\)=>Promise\.all\(\[([^\]]+)\]\)"
        rf"\.then\({_IDENTIFIER}\.bind\({_IDENTIFIER},\d+\)\)\)"
    )
    for match in re.finditer(pattern, app_source):
        if not re.search(r"\(" + re.escape(match[1]) + r",\{\}\)", route_source):
            continue
        chunks = re.findall(r"\.e\((\d+(?:e\d+)?)\)", match[2])
        if not chunks:
            continue
        chunk_id = str(int(float(chunks[-1])))
        fingerprint = re.search(r"(?<![\w])" + chunk_id + r':"([a-f0-9]+)"', app_source)
        if fingerprint:
            return urljoin(app_url, f"../{chunk_id}.{fingerprint[1]}.chunk.js")
    raise ValueError("豆瓣阅读评论代码块发生变化")


def _query_hash(common_source: str, chunk_source: str, kind: str) -> str:
    """沿当前脚本的语义选择器读取只读查询标识，拒绝猜测历史哈希。"""
    if kind == "review":
        selected = re.search(r"\[[\w$.]+\.REVIEW_COMMENT\]:" + _IDENTIFIER + r"\.([\w$]+)", chunk_source)
    else:
        selector = re.search(r"const " + _IDENTIFIER + r"=" + _IDENTIFIER + r"\?(" + _IDENTIFIER + r")\[" + _IDENTIFIER + r"\]:(" + _IDENTIFIER + r")\[" + _IDENTIFIER + r"\]", chunk_source)
        if not selector:
            raise ValueError("豆瓣阅读作品评论查询选择器发生变化")
        mapping_name = selector[1] if kind == "column" else selector[2]
        mapping = re.search(r"\b" + re.escape(mapping_name) + r"=\{([^}]+)\}", chunk_source)
        field = "discussion" if kind == "column" else "review"
        selected = re.search(r"\b" + field + r":" + _IDENTIFIER + r"\.([\w$]+)", mapping[1]) if mapping else None
    if not selected:
        raise ValueError("豆瓣阅读评论查询定义发生变化")
    exported = re.search(r"(?<![\w$])" + re.escape(selected[1]) + r":\(\)=>([\w$]+)", common_source)
    if not exported:
        raise ValueError("豆瓣阅读评论查询导出发生变化")
    hashed = re.search(
        r"[,;](?:const )?" + re.escape(exported[1])
        + r'=[^,;]{0,150}\{__meta__:\{hash:"(sha256:[a-f0-9]{64})"\}\}',
        common_source,
    )
    if not hashed:
        raise ValueError("豆瓣阅读评论查询标识缺失")
    return hashed[1]


def _reading_comment(item: Dict[str, Any], kind: str, identity: str) -> Dict[str, Any]:
    """转换阅读评论字段并保留引用与章节归属。"""
    if item.get("isHidden") or item.get("isDeleted"):
        return {}
    user = item.get("user")
    if not isinstance(user, dict) or not isinstance(item.get("content"), str):
        return {}
    if kind == "review" and str(item.get("targetId") or "") != identity:
        return {}
    works = item.get("works")
    if kind != "review":
        if not isinstance(works, dict):
            return {}
        if kind == "ebook" and str(works.get("id") or "") != identity:
            return {}
        if kind == "column" and not str(works.get("url") or "").startswith(f"/column/{identity}/"):
            return {}
    normalized = {
        "id": item.get("id"), "text": item["content"], "author": user,
        "create_time": item.get("createTime"), "vote_count": item.get("upvoteCount"),
    }
    reference = item.get("refComment")
    if isinstance(reference, dict) and not reference.get("isHidden") and not reference.get("isDeleted"):
        reference_user = reference.get("user")
        normalized["ref_comment"] = {
            "text": reference.get("content"),
            "author": reference_user if isinstance(reference_user, dict) else {},
        }
    comment = json_comment(normalized) or {}
    if comment and kind == "column" and works.get("title"):
        comment["message"] = f"章节：{works['title']}\n{comment['message']}"
    return comment


async def _fetch_comments(web: DoubanWeb, url: str, kind: str, identity: str, count: int, source: str) -> List[Dict[str, Any]]:
    """读取当前前端公开的匿名评论查询，限制单页数量。"""
    page_url = url if kind == "review" else url.rstrip("/") + "/comments"
    page_source = source if kind == "review" else await web.get_text(page_url, referer=url)
    root = parse_html(page_source)
    token = re.search(r"Ark\.CSRF_TOKEN\s*=\s*['\"]([^'\"]+)['\"]", page_source)
    if not token:
        raise ValueError("豆瓣阅读匿名评论令牌缺失")
    app_url = _script_url(root, "comment/app")
    common_url = _script_url(root, "common")
    app_source, common_source = await asyncio.gather(
        web.get_text(app_url, referer=page_url), web.get_text(common_url, referer=page_url),
    )
    chunk_url = _comment_chunk(app_source, app_url, kind)
    chunk_source = await web.get_text(chunk_url, referer=page_url)
    query_hash = _query_hash(common_source, chunk_source, kind)
    variables: Dict[str, Any] = {"isAdmin": False, "start": 0, "limit": min(count * 2, 50)}
    if kind == "review":
        variables.update({"commentId": identity, "commentType": "ReviewComment"})
    else:
        variables.update({"sort": "SCORE_DESC", "onlyUserId": None})
        if kind == "column":
            variables["columnId"] = identity
        else:
            variables.update({"worksId": identity, "onlyCompetition": False, "reviewType": "short"})
    payload = await web.post_json(
        READING_ORIGIN + "/j/graphql",
        {"variables": variables, "extensions": {"persistedQuery": {"version": 1, "sha256Hash": query_hash}}},
        headers={"X-CSRF-Token": token[1], "X-Requested-With": "XMLHttpRequest", "Referer": page_url},
    )
    if payload.get("errors"):
        raise ValueError("豆瓣阅读公开评论查询未成功")
    data = payload.get("data")
    target = data.get("comment" if kind == "review" else "works") if isinstance(data, dict) else None
    comments = target.get("comments") if isinstance(target, dict) else None
    items = comments.get("list") if isinstance(comments, dict) else None
    if not isinstance(items, list):
        raise ValueError("豆瓣阅读评论数据结构发生变化")
    result = []
    seen = set()
    for item in items:
        comment = _reading_comment(item, kind, identity) if isinstance(item, dict) else {}
        if comment and comment["id"] not in seen:
            result.append(comment)
            seen.add(comment["id"])
            if len(result) >= count:
                break
    return result


async def parse_reading(web: DoubanWeb, url: str, hot_comment_count: int) -> MediaMetadata:
    """解析公开作品介绍或阅读评论，并按需附加可见评论。

    Args:
        web: 本次解析使用的独立匿名会话。
        url: 豆瓣阅读作品、评论或阅读器链接。
        hot_comment_count: 需要附加的评论数量，零表示关闭。

    Returns:
        作品介绍或完整公开评论的统一媒体元数据。
    """
    kind, identity, page_url = _reading_target(url)
    final_url, source = await web.get_page(page_url)
    if kind == "review" and _reading_target(final_url) != (kind, identity, page_url):
        raise ValueError("豆瓣阅读评论跳转到了其他内容")
    root = parse_html(source)
    if kind == "review":
        result = _review_metadata(root, page_url)
    else:
        result = _work_metadata(root, kind, identity, page_url)
        kind, identity, page_url = _reading_target(result["url"])
    if hot_comment_count > 0:
        try:
            comments = await _fetch_comments(web, page_url, kind, identity, hot_comment_count, source)
            if comments:
                result["hot_comments"] = comments
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, RuntimeError) as exc:
            logger.warning(f"豆瓣阅读评论读取失败，保留正文：{exc}")
    return result
