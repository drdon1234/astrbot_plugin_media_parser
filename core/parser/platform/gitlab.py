"""GitLab 解析器，读取公开仓库概况、议题正文及有限讨论。"""

import asyncio
import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote, urljoin, urlsplit

import aiohttp

from ...logger import logger

from ...constants import Config
from ...types import MediaMetadata
from ..utils import build_request_headers
from .base import BaseVideoParser


BASE_URL = "https://gitlab.com"
GITLAB_URL_RE = re.compile(
    r"(?<![A-Za-z0-9_.:/@%?=&#-])(?:https?://|//)?gitlab\.com"
    r"[^\s<>\"'`()\[\]{}，。！？；：、（）【】《》「」,;!]*", re.IGNORECASE,
)
PATH_COMPONENT_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,254}")
RESERVED_ROUTES = frozenset({
    "admin", "api", "dashboard", "explore", "groups", "help", "oauth",
    "profile", "search", "snippets", "users", "uploads", "assets", "-",
})
PROJECT_ROUTES = frozenset({
    "blob", "blame", "builds", "commit", "commits", "edit", "environments",
    "graphs", "issues", "jobs", "merge_requests", "network", "new", "pipelines",
    "raw", "releases", "repository", "settings", "tags", "tree", "wiki", "wikis",
})
MAX_DESCRIPTION_LENGTH = 400
MAX_COMMENT_PAGES = 3
MAX_NOTES_PER_DISCUSSION = 20
MAX_REDIRECTS = 2
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
DISCUSSION_QUERY = """query($fullPath: ID!, $iid: String!, $after: String, $notes: Int!) {
  project(fullPath: $fullPath) { id fullPath issue(iid: $iid) { id iid
    discussions(first: 10, after: $after) {
      nodes { id notes(first: $notes) { nodes {
        id body system createdAt author { id username }
      } } }
      pageInfo { hasNextPage endCursor }
    }
  } }
}"""


def _link_parts(value: Any) -> Optional[Tuple[str, str]]:
    """严格限定官方主机上的项目根路径和议题路径。"""
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
    if parsed.scheme.lower() not in {"http", "https"} or parsed.netloc.lower() != "gitlab.com":
        return None
    path = parsed.path.removeprefix("/").removesuffix("/")
    issue_id = ""
    if "/-/" in path:
        path, tail = path.split("/-/", 1)
        match = re.fullmatch(r"(?:issues|work_items)/([1-9][0-9]{0,19})", tail)
        if not match:
            return None
        issue_id = match.group(1)
    elif path.lower().endswith(".git"):
        path = path[:-4]
    components = path.split("/")
    if (len(components) < 2 or components[0].lower() in RESERVED_ROUTES
            or any(not PATH_COMPONENT_RE.fullmatch(part) or part in {".", ".."}
                   or part.lower().endswith(".git") for part in components)
            or any(part.lower() in PROJECT_ROUTES for part in components[2:])):
        return None
    return path, issue_id


def _project_api_identity(value: str) -> str:
    """只允许官方项目详情接口作为项目迁移目标。"""
    if not isinstance(value, str) or re.search(r"[\s\x00-\x1f\x7f\\]", value):
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if (parsed.scheme != "https" or parsed.netloc.lower() != "gitlab.com"
            or parsed.query or parsed.fragment):
        return ""
    match = re.fullmatch(r"/api/v4/projects/([^/]+)", parsed.path)
    if not match:
        return ""
    identity = unquote(match.group(1))
    if re.fullmatch(r"[1-9][0-9]*", identity):
        return identity
    parts = _link_parts(BASE_URL + "/" + identity)
    return identity if parts == (identity, "") else ""


def _text(value: Any) -> str:
    """读取文本字段并保留 Markdown 段落和代码缩进。"""
    return value.strip() if isinstance(value, str) else ""


def _timestamp(value: Any) -> str:
    """将明确带时区的时间转换为北京时间。"""
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else None
        if date is not None and date.tzinfo is not None:
            return date.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OverflowError):
        pass
    return ""


class GitLabParser(BaseVideoParser):
    """解析 gitlab.com 上的公开项目及 Issue，不读取仓库文件。"""

    def __init__(self, hot_comment_count: int = 0) -> None:
        """初始化解析器。

        Args:
            hot_comment_count: 议题评论总数上限，零表示不请求讨论。
        """
        super().__init__("gitlab")
        try:
            self.hot_comment_count = max(0, int(hot_comment_count))
        except (TypeError, ValueError, OverflowError):
            self.hot_comment_count = 0
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)

    def can_parse(self, url: str) -> bool:
        """判断是否为支持的项目或议题链接。

        Args:
            url: 待判断的链接。

        Returns:
            是否为受支持的官方链接。
        """
        return _link_parts(url) is not None

    def extract_links(self, text: str) -> List[str]:
        """提取链接，保留首次原串并按项目与议题身份去重。

        Args:
            text: 包含分享链接的消息。

        Returns:
            按消息出现顺序排列的原始链接。
        """
        links, seen = [], set()
        for match in GITLAB_URL_RE.finditer(text or ""):
            candidate = match.group(0).rstrip(".,!?:")
            parts = _link_parts(candidate)
            if parts:
                identity = (parts[0].lower(), parts[1])
                if identity not in seen:
                    seen.add(identity)
                    links.append(candidate)
        return links

    async def _request_json(
        self, session: aiohttp.ClientSession, url: str,
        params: Optional[Dict[str, Any]] = None, project_redirects: bool = False,
    ) -> Tuple[Any, str]:
        """请求官方只读接口，仅项目详情允许有限且校验过的迁移跳转。"""
        try:
            for redirect_count in range(MAX_REDIRECTS + 1):
                async with session.get(
                    url, params=params, headers=build_request_headers(custom_headers={"Accept": "application/json"}),
                    timeout=aiohttp.ClientTimeout(total=Config.DEFAULT_TIMEOUT), allow_redirects=False,
                ) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        location = response.headers.get("Location", "")
                        target = urljoin(url, location)
                        if not project_redirects or not location or not _project_api_identity(target):
                            raise RuntimeError("GitLab 接口返回了无效重定向")
                        if redirect_count == MAX_REDIRECTS:
                            raise RuntimeError("GitLab 项目接口重定向次数过多")
                        url = target
                        continue
                    if response.status in {401, 403, 429}:
                        raise RuntimeError("GitLab 接口访问受限、需要登录或请求频率超限")
                    if response.status == 404:
                        raise RuntimeError("GitLab 项目或议题不存在、已删除或不是公开内容")
                    if response.status != 200:
                        raise RuntimeError(f"GitLab 接口请求失败（HTTP {response.status}）")
                    chunks, total = [], 0
                    async for chunk in response.content.iter_chunked(65536):
                        total += len(chunk)
                        if total > MAX_RESPONSE_BYTES:
                            raise RuntimeError("GitLab 接口响应超过大小限制")
                        chunks.append(chunk)
                    return json.loads(b"".join(chunks)), url
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError as exc:
            raise RuntimeError("GitLab 接口请求超时") from exc
        except aiohttp.ClientError as exc:
            raise RuntimeError("GitLab 接口网络请求失败") from exc
        except ValueError as exc:
            raise RuntimeError("GitLab 接口返回的数据格式无效") from exc
        raise RuntimeError("GitLab 接口未返回有效结果")

    @staticmethod
    def _validate_project(data: Any, api_url: str) -> None:
        """确认公开项目的数字身份、路径与规范网址相互一致。"""
        if not isinstance(data, dict):
            raise RuntimeError("GitLab 未返回有效公开项目详情")
        path = data.get("path_with_namespace")
        parts = _link_parts(data.get("web_url"))
        namespace = data.get("namespace")
        identity = _project_api_identity(api_url)
        if (type(data.get("id")) is not int or data["id"] <= 0
                or data.get("visibility") != "public" or not isinstance(path, str)
                or parts != (path, "") or not isinstance(namespace, dict)
                or namespace.get("full_path") != path.rsplit("/", 1)[0]
                or data.get("path") != path.rsplit("/", 1)[-1]
                or (identity != str(data["id"]) and identity.lower() != path.lower())):
            raise RuntimeError("GitLab 接口返回的公开项目身份不一致")

    @staticmethod
    def _validate_issue(data: Any, project: Dict[str, Any], issue_id: str) -> None:
        """验证议题属于当前公开项目并排除其他工作项类型。"""
        if not isinstance(data, dict):
            raise RuntimeError("GitLab 未返回有效公开议题")
        author = data.get("author")
        if (type(data.get("id")) is not int or data["id"] <= 0
                or type(data.get("iid")) is not int or str(data["iid"]) != issue_id
                or data.get("project_id") != project["id"]
                or data.get("confidential") is not False or data.get("issue_type") != "issue"
                or _link_parts(data.get("web_url")) != (project["path_with_namespace"], issue_id)
                or not _text(data.get("title")) or not isinstance(author, dict)
                or not _text(author.get("username"))):
            raise RuntimeError("GitLab 返回的议题身份、可见性或工作项类型不受支持")

    async def _comments(
        self, session: aiohttp.ClientSession, project: Dict[str, Any], issue: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """有限分页公开讨论，过滤系统记录并保留已获取的回复。"""
        comments, seen, cursors = [], set(), set()
        cursor = None
        try:
            for _ in range(MAX_COMMENT_PAGES):
                variables = {
                    "fullPath": project["path_with_namespace"], "iid": str(issue["iid"]),
                    "after": cursor, "notes": min(self.hot_comment_count, MAX_NOTES_PER_DISCUSSION),
                }
                payload, _ = await self._request_json(session, BASE_URL + "/api/graphql", {
                    "query": DISCUSSION_QUERY, "variables": json.dumps(variables),
                })
                if not isinstance(payload, dict) or payload.get("errors"):
                    raise RuntimeError("GitLab 讨论接口返回错误")
                data = payload.get("data")
                source = data.get("project") if isinstance(data, dict) else None
                entry = source.get("issue") if isinstance(source, dict) else None
                if (not isinstance(source, dict) or source.get("id") != f"gid://gitlab/Project/{project['id']}"
                        or source.get("fullPath") != project["path_with_namespace"]
                        or not isinstance(entry, dict) or entry.get("id") != f"gid://gitlab/Issue/{issue['id']}"
                        or entry.get("iid") != str(issue["iid"])):
                    raise RuntimeError("GitLab 讨论接口返回了其他项目或议题")
                discussions = entry.get("discussions")
                if not isinstance(discussions, dict) or not isinstance(discussions.get("nodes"), list):
                    raise RuntimeError("GitLab 讨论数据格式无效")
                for discussion in discussions["nodes"][:10]:
                    notes = discussion.get("notes") if isinstance(discussion, dict) else None
                    nodes = notes.get("nodes") if isinstance(notes, dict) else None
                    if not isinstance(nodes, list):
                        continue
                    has_comment = False
                    for note in nodes[:MAX_NOTES_PER_DISCUSSION]:
                        if not isinstance(note, dict) or note.get("system") is not False:
                            continue
                        author = note.get("author")
                        note_id, message = _text(note.get("id")), _text(note.get("body"))
                        username = _text(author.get("username")) if isinstance(author, dict) else ""
                        if not note_id or not message or not username:
                            continue
                        if has_comment:
                            message = "同一讨论下的回复：\n" + message
                        has_comment = True
                        if note_id in seen:
                            continue
                        seen.add(note_id)
                        comments.append({
                            "id": note_id, "username": username, "uid": _text(author.get("id")),
                            "message": message, "time": _timestamp(note.get("createdAt")),
                        })
                        if len(comments) >= self.hot_comment_count:
                            return comments
                page = discussions.get("pageInfo")
                if not isinstance(page, dict) or page.get("hasNextPage") is not True:
                    break
                cursor = page.get("endCursor")
                if not isinstance(cursor, str) or not cursor or cursor in cursors:
                    break
                cursors.add(cursor)
        except asyncio.CancelledError:
            raise
        except RuntimeError as exc:
            logger.warning(f"GitLab 议题评论获取失败，保留正文及已取得评论：{exc}")
        return comments

    @staticmethod
    def _description(project: Dict[str, Any], languages: Any) -> str:
        """构建项目概况，不推断缺失的归档状态。"""
        description = " ".join(_text(project.get("description")).split())
        if len(description) > MAX_DESCRIPTION_LENGTH:
            description = description[:MAX_DESCRIPTION_LENGTH].rstrip() + "…"
        language = "未标注"
        if isinstance(languages, dict):
            values = [(name, value) for name, value in languages.items()
                      if isinstance(name, str) and type(value) in {int, float}
                      and 0 <= value <= 100 and math.isfinite(value)]
            if values:
                language = max(values, key=lambda pair: pair[1])[0]
        counts = [f"{project[key]:,}" if type(project.get(key)) is int and project[key] >= 0 else "未知"
                  for key in ("star_count", "forks_count")]
        license_data = project.get("license")
        license_name = _text(license_data.get("name")) if isinstance(license_data, dict) else ""
        states = ["公开"]
        if project.get("archived") is True:
            states.append("已归档")
        if isinstance(project.get("forked_from_project"), dict):
            states.append("分叉仓库")
        lines = [description or "暂无仓库简介", f"主要语言：{language}",
                 f"Star：{counts[0]} · Fork：{counts[1]}", f"许可证：{license_name or '未标注'}",
                 "状态：" + " · ".join(states)]
        activity = _timestamp(project.get("last_activity_at"))
        if activity:
            lines.append("最近活动：" + activity)
        return "\n".join(lines)

    async def parse(self, session: aiohttp.ClientSession, url: str) -> Optional[MediaMetadata]:
        """解析公开仓库概况或议题正文及有限评论。

        Args:
            session: 已有的异步 HTTP 会话。
            url: GitLab 项目根链接或 Issue 链接。

        Returns:
            纯文本仓库概况，或含议题正文和评论的元数据。
        """
        parts = _link_parts(url)
        if not parts:
            raise RuntimeError("不支持此 GitLab 链接，仅支持官方公开项目首页和 Issue")
        path, issue_id = parts
        async with self.semaphore:
            project, api_url = await self._request_json(
                session, BASE_URL + "/api/v4/projects/" + quote(path, safe=""),
                {"license": "true"} if not issue_id else None, project_redirects=True,
            )
            self._validate_project(project, api_url)
            project_url = BASE_URL + "/" + project["path_with_namespace"]
            metadata: MediaMetadata = {
                "url": project_url, "title": project["path_with_namespace"],
                "author": project["namespace"]["full_path"], "platform": self.name,
                "video_urls": [], "image_urls": [],
            }
            if not issue_id:
                languages = {}
                try:
                    languages, _ = await self._request_json(session, f"{BASE_URL}/api/v4/projects/{project['id']}/languages")
                except asyncio.CancelledError:
                    raise
                except RuntimeError as exc:
                    logger.warning(f"GitLab 项目语言获取失败，保留仓库概况：{exc}")
                metadata["desc"] = self._description(project, languages)
                return metadata
            issue, _ = await self._request_json(session, f"{BASE_URL}/api/v4/projects/{project['id']}/issues/{issue_id}")
            self._validate_issue(issue, project, issue_id)
            state = {"opened": "开放", "closed": "已关闭"}.get(_text(issue.get("state")), "未知")
            metadata.update({
                "url": project_url + "/-/issues/" + issue_id,
                "title": f"{project['path_with_namespace']} #{issue_id} · {issue['title']}",
                "author": issue["author"]["username"], "timestamp": _timestamp(issue.get("created_at")),
                "desc": f"状态：{state}\n\n" + (_text(issue.get("description")) or "暂无议题正文"),
            })
            if self.hot_comment_count:
                metadata["hot_comments"] = await self._comments(session, project, issue)
            return metadata
