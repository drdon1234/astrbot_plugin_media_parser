"""Gitee 解析器，读取公开仓库概况、议题正文及有限评论。"""

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import aiohttp

from ...logger import logger

from ...constants import Config
from ...types import MediaMetadata
from ..utils import build_request_headers
from .base import BaseVideoParser


BASE_URL = "https://gitee.com"
GITEE_URL_RE = re.compile(
    r"(?<![A-Za-z0-9_.:/@%?=&#-])(?:https?://|//)?gitee\.com"
    r"[^\s<>\"'`()\[\]{}，。！？；：、（）【】《》「」,;!]*", re.IGNORECASE,
)
REPOSITORY_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,254}")
RESERVED_ROUTES = frozenset({
    "api", "dashboard", "enterprises", "explore", "help", "login", "oauth",
    "organizations", "profile", "search", "signup", "snippets", "users", "assets",
})
MAX_DESCRIPTION_LENGTH = 400
MAX_COMMENT_PAGES = 3
MAX_COMMENTS_PER_PAGE = 20
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def _link_parts(value: Any) -> Optional[Tuple[str, str]]:
    """仅接纳官方主机上的完整仓库路径和大小写不变的议题编号。"""
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if re.search(r"[\s\x00-\x1f\x7f\\]", value):
        return None
    if value.startswith("//"):
        value = "https:" + value
    elif "://" not in value:
        value = "https://" + value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or parsed.netloc.lower() != "gitee.com":
        return None
    path = parsed.path.removeprefix("/").removesuffix("/")
    issue_id = ""
    if "/issues/" in path:
        path, issue_id = path.split("/issues/", 1)
        if not re.fullmatch(r"[A-Za-z0-9]{1,32}", issue_id):
            return None
    elif path.lower().endswith(".git"):
        path = path[:-4]
    if not REPOSITORY_RE.fullmatch(path):
        return None
    owner, repo = path.split("/")
    if owner.lower() in RESERVED_ROUTES or repo in {".", ".."} or path.lower().endswith(".git"):
        return None
    return path, issue_id


def _text(value: Any) -> str:
    """保留 Markdown 正文中的换行和代码缩进。"""
    return value.strip() if isinstance(value, str) else ""


def _timestamp(value: Any) -> str:
    """将接口中带时区的时间转换为北京时间。"""
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else None
        if date is not None and date.tzinfo is not None:
            return date.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OverflowError):
        pass
    return ""


class GiteeParser(BaseVideoParser):
    """通过 Gitee 官方接口解析公开仓库和议题，不下载仓库文件。"""

    def __init__(self, hot_comment_count: int = 0) -> None:
        """初始化解析器。

        Args:
            hot_comment_count: 议题评论总数上限，零表示不请求评论。
        """
        super().__init__("gitee")
        try:
            self.hot_comment_count = max(0, int(hot_comment_count))
        except (TypeError, ValueError, OverflowError):
            self.hot_comment_count = 0
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)

    def can_parse(self, url: str) -> bool:
        """判断是否为官方仓库根链接或议题链接。

        Args:
            url: 待判断的链接。

        Returns:
            是否属于本解析器支持范围。
        """
        return _link_parts(url) is not None

    def extract_links(self, text: str) -> List[str]:
        """提取链接并去重，保留原串及议题编号大小写。

        Args:
            text: 包含链接的消息。

        Returns:
            按出现顺序排列的首次原始链接。
        """
        links, seen = [], set()
        for match in GITEE_URL_RE.finditer(text or ""):
            candidate = match.group(0).rstrip(".,!?:")
            parts = _link_parts(candidate)
            if parts:
                identity = (parts[0].lower(), parts[1])
                if identity not in seen:
                    seen.add(identity)
                    links.append(candidate)
        return links

    async def _request_json(
        self, session: aiohttp.ClientSession, path: str, params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """只请求本解析器构造的官方接口，不跟随到登录页或其他资源。"""
        try:
            async with session.get(
                BASE_URL + "/api/v5/repos/" + path, params=params,
                headers=build_request_headers(custom_headers={"Accept": "application/json"}),
                timeout=aiohttp.ClientTimeout(total=Config.DEFAULT_TIMEOUT), allow_redirects=False,
            ) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    raise RuntimeError("Gitee 接口返回了不受支持的重定向")
                if response.status in {401, 403, 429}:
                    raise RuntimeError("Gitee 接口访问受限、需要登录或请求频率超限")
                if response.status == 404:
                    raise RuntimeError("Gitee 仓库或议题不存在、已删除或不是公开内容")
                if response.status != 200:
                    raise RuntimeError(f"Gitee 接口请求失败（HTTP {response.status}）")
                chunks, total = [], 0
                async for chunk in response.content.iter_chunked(65536):
                    total += len(chunk)
                    if total > MAX_RESPONSE_BYTES:
                        raise RuntimeError("Gitee 接口响应超过大小限制")
                    chunks.append(chunk)
                return json.loads(b"".join(chunks))
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError as exc:
            raise RuntimeError("Gitee 接口请求超时") from exc
        except aiohttp.ClientError as exc:
            raise RuntimeError("Gitee 接口网络请求失败") from exc
        except ValueError as exc:
            raise RuntimeError("Gitee 接口返回的数据格式无效") from exc

    @staticmethod
    def _validate_repository(data: Any) -> None:
        """校验规范仓库身份，允许官方旧路径直接返回迁移后的仓库。"""
        if not isinstance(data, dict):
            raise RuntimeError("Gitee 未返回有效公开仓库详情")
        full_name = data.get("full_name")
        parts = _link_parts(data.get("html_url"))
        namespace = data.get("namespace")
        if (type(data.get("id")) is not int or data["id"] <= 0
                or data.get("private") is not False or data.get("public") is not True
                or data.get("internal") is True or not isinstance(full_name, str)
                or parts != (full_name, "") or not isinstance(namespace, dict)
                or namespace.get("path") != full_name.split("/")[0]
                or data.get("path") != full_name.split("/")[-1]
                or data.get("url") != BASE_URL + "/api/v5/repos/" + full_name):
            raise RuntimeError("Gitee 接口返回的公开仓库身份不一致")

    @staticmethod
    def _validate_issue(data: Any, repository: Dict[str, Any], issue_id: str) -> None:
        """确保议题编号、仓库身份及规范链接全部匹配。"""
        if not isinstance(data, dict):
            raise RuntimeError("Gitee 未返回有效公开议题")
        user, source = data.get("user"), data.get("repository")
        if (type(data.get("id")) is not int or data["id"] <= 0
                or data.get("number") != issue_id
                or _link_parts(data.get("html_url")) != (repository["full_name"], issue_id)
                or data.get("repository_url") != repository["url"]
                or not isinstance(source, dict) or source.get("id") != repository["id"]
                or source.get("full_name") != repository["full_name"]
                or source.get("private") is not False or source.get("public") is not True
                or data.get("pull_request") or data.get("private") is True
                or not _text(data.get("title")) or not isinstance(user, dict)
                or not _text(user.get("login"))):
            raise RuntimeError("Gitee 返回的议题身份或可见性不受支持")

    async def _comments(
        self, session: aiohttp.ClientSession, repository: Dict[str, Any], issue: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """按接口顺序有限分页，保留回复提示与已经获取的评论。"""
        comments, seen, authors = [], set(), {}
        page_size = min(self.hot_comment_count, MAX_COMMENTS_PER_PAGE)
        try:
            for page in range(1, MAX_COMMENT_PAGES + 1):
                data = await self._request_json(
                    session, repository["full_name"] + "/issues/" + issue["number"] + "/comments",
                    {"page": page, "per_page": page_size, "order": "asc"},
                )
                if not isinstance(data, list):
                    raise RuntimeError("Gitee 评论接口返回的数据格式无效")
                new_ids = 0
                for item in data[:page_size]:
                    if not isinstance(item, dict) or type(item.get("id")) is not int or item["id"] <= 0:
                        continue
                    target = item.get("target")
                    target_issue = target.get("issue") if isinstance(target, dict) else None
                    if (not isinstance(target_issue, dict) or target_issue.get("id") != issue["id"]
                            or target_issue.get("number") != issue["number"] or target.get("pull_request")):
                        continue
                    comment_id = str(item["id"])
                    if comment_id in seen:
                        continue
                    seen.add(comment_id)
                    new_ids += 1
                    user = item.get("user")
                    username = (_text(user.get("name")) or _text(user.get("login"))) if isinstance(user, dict) else ""
                    message = _text(item.get("body"))
                    if not username or not message:
                        continue
                    reply_id = item.get("in_reply_to_id")
                    if type(reply_id) is int and reply_id > 0:
                        target_name = authors.get(str(reply_id))
                        prefix = f"回复 @{target_name}" if target_name else f"回复评论 #{reply_id}"
                        message = prefix + "：\n" + message
                    authors[comment_id] = username
                    comments.append({
                        "id": comment_id, "username": username, "uid": _text(user.get("login")),
                        "message": message, "time": _timestamp(item.get("created_at")),
                    })
                    if len(comments) >= self.hot_comment_count:
                        return comments
                if len(data) < page_size or not new_ids:
                    break
        except asyncio.CancelledError:
            raise
        except RuntimeError as exc:
            logger.warning(f"Gitee 议题评论获取失败，保留正文及已取得评论：{exc}")
        return comments

    @staticmethod
    def _description(repository: Dict[str, Any]) -> str:
        """构建简短仓库概况，缺失状态或统计不伪造成已知值。"""
        description = " ".join(_text(repository.get("description")).split())
        if len(description) > MAX_DESCRIPTION_LENGTH:
            description = description[:MAX_DESCRIPTION_LENGTH].rstrip() + "…"
        counts = [f"{repository[key]:,}" if type(repository.get(key)) is int and repository[key] >= 0 else "未知"
                  for key in ("stargazers_count", "forks_count")]
        states = ["公开"]
        for key, label in (("fork", "分叉仓库"), ("archived", "已归档")):
            if repository.get(key) is True:
                states.append(label)
        lines = [description or "暂无仓库简介", f"主要语言：{_text(repository.get('language')) or '未标注'}",
                 f"Star：{counts[0]} · Fork：{counts[1]}", f"许可证：{_text(repository.get('license')) or '未标注'}",
                 "状态：" + " · ".join(states)]
        updated = _timestamp(repository.get("updated_at"))
        if updated:
            lines.append("更新时间：" + updated)
        return "\n".join(lines)

    async def parse(self, session: aiohttp.ClientSession, url: str) -> Optional[MediaMetadata]:
        """解析公开仓库概况或议题正文及评论。

        Args:
            session: 已有的异步 HTTP 会话。
            url: Gitee 仓库根链接或议题链接。

        Returns:
            纯文本仓库概况，或含议题正文和有限评论的元数据。
        """
        parts = _link_parts(url)
        if not parts:
            raise RuntimeError("不支持此 Gitee 链接，仅支持官方公开仓库首页和 Issue")
        path, issue_id = parts
        async with self.semaphore:
            repository = await self._request_json(session, path)
            self._validate_repository(repository)
            repository_url = BASE_URL + "/" + repository["full_name"]
            metadata: MediaMetadata = {
                "url": repository_url, "title": repository["full_name"],
                "author": repository["namespace"]["path"], "platform": self.name,
                "video_urls": [], "image_urls": [],
            }
            if not issue_id:
                metadata["desc"] = self._description(repository)
                return metadata
            issue = await self._request_json(session, repository["full_name"] + "/issues/" + issue_id)
            self._validate_issue(issue, repository, issue_id)
            state = {"open": "开放", "closed": "已关闭", "progressing": "进行中", "rejected": "已拒绝"}.get(_text(issue.get("state")), "未知")
            metadata.update({
                "url": repository_url + "/issues/" + issue_id,
                "title": f"{repository['full_name']} #{issue_id} · {issue['title']}",
                "author": _text(issue["user"].get("name")) or issue["user"]["login"],
                "timestamp": _timestamp(issue.get("created_at")),
                "desc": f"状态：{state}\n\n" + (_text(issue.get("body")) or "暂无议题正文"),
            })
            if self.hot_comment_count:
                metadata["hot_comments"] = await self._comments(session, repository, issue)
            return metadata
