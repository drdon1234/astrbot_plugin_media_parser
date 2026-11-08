"""GitHub 解析器，提取公开仓库概况、README 截图入口及议题评论。"""

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import aiohttp

from ...logger import logger

from ...constants import Config
from ...types import MediaMetadata
from ..utils import build_request_headers
from .base import BaseVideoParser


GITHUB_URL_RE = re.compile(
    r"(?<![A-Za-z0-9_.:/@%?=&#-])(?:https?://|//)?(?:www\.)?github\.com"
    r"[^\s<>\"'`()\[\]{}，。！？；：、（）【】《》「」,;!]*",
    re.IGNORECASE,
)
REPOSITORY_RE = re.compile(
    r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)/"
    r"(?P<repo>[A-Za-z0-9_.-]{1,100})"
)
RESERVED_ROUTES = frozenset({
    "apps", "collections", "enterprise", "features", "login", "marketplace",
    "organizations", "orgs", "search", "settings", "site", "sponsors", "topics",
    "users",
})
MAX_DESCRIPTION_LENGTH = 400
MAX_REDIRECTS = 2
README_SELECTOR = 'article.markdown-body, #readme .markdown-body'
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_COMMENT_PAGES = 3


def _link_parts(url: str) -> Optional[Tuple[str, str, str]]:
    """识别仓库根或明确的正整数 Issue 编号，不截断其他子页。"""
    repository = _repository_parts(url)
    if repository:
        return *repository, ""
    if not isinstance(url, str) or re.search(r"[\s\x00-\x1f\x7f\\]", url):
        return None
    value = url.strip()
    if value.startswith("//"):
        value = "https:" + value
    elif "://" not in value:
        value = "https://" + value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    match = re.fullmatch(r"/([^/]+/[^/]+)/issues/([1-9][0-9]{0,19})/?", parsed.path)
    if not match:
        return None
    repository = _repository_parts(f"{parsed.scheme}://{parsed.netloc}/{match[1]}")
    if not repository or match[1].lower().endswith(".git"):
        return None
    return *repository, match[2]


def _repository_parts(url: str) -> Optional[Tuple[str, str]]:
    """只接受可信 GitHub 主机上的完整仓库根路径。"""
    if not isinstance(url, str) or not url.strip():
        return None
    normalized = url.strip()
    if re.search(r"[\s\x00-\x1f\x7f\\]", normalized):
        return None
    if normalized.startswith("//"):
        normalized = "https:" + normalized
    elif "://" not in normalized:
        normalized = "https://" + normalized
    try:
        parsed = urlsplit(normalized)
    except ValueError:
        return None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or parsed.netloc.lower() not in {"github.com", "www.github.com"}
    ):
        return None
    path = parsed.path.removeprefix("/").removesuffix("/")
    if path.lower().endswith(".git"):
        path = path[:-4]
    match = REPOSITORY_RE.fullmatch(path)
    if not match:
        return None
    owner, repo = match.group("owner", "repo")
    if owner.lower() in RESERVED_ROUTES or repo in {".", ".."}:
        return None
    return owner, repo


def _api_url(url: str) -> bool:
    """校验仓库重定向目标，限制为官方 HTTPS 仓库接口。"""
    if not isinstance(url, str) or re.search(r"[\s\x00-\x1f\x7f\\]", url):
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if (
        parsed.scheme != "https" or parsed.netloc.lower() != "api.github.com"
        or parsed.query or parsed.fragment
    ):
        return False
    if re.fullmatch(r"/repositories/[1-9][0-9]*", parsed.path):
        return True
    if parsed.path.startswith("/repos/"):
        return _repository_parts("https://github.com/" + parsed.path[7:]) is not None
    return False


class GitHubParser(BaseVideoParser):
    """通过官方接口解析公开仓库概况、README 截图入口和议题评论。"""

    def __init__(self, proxy_url: Optional[str] = None, hot_comment_count: int = 0) -> None:
        """初始化仓库解析器。

        Args:
            proxy_url: 供仓库接口和 README 页面截图使用的代理地址。
            hot_comment_count: 议题普通评论条数上限，零表示不请求。
        """
        super().__init__("github")
        self.proxy_url = proxy_url
        try:
            self.hot_comment_count = max(0, int(hot_comment_count))
        except (TypeError, ValueError):
            self.hot_comment_count = 0
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)

    def can_parse(self, url: str) -> bool:
        """判断是否为受支持的 GitHub 仓库或议题链接。

        Args:
            url: 待判断的链接。

        Returns:
            是否为仓库根链接或指定议题。
        """
        return _link_parts(url) is not None

    def extract_links(self, text: str) -> List[str]:
        """从消息中提取仓库与议题链接，按内容身份去重。

        Args:
            text: 包含链接的消息文本。

        Returns:
            保留首次出现形式的内容链接，供路由器按消息位置排序。
        """
        links: List[str] = []
        seen = set()
        for match in GITHUB_URL_RE.finditer(text or ""):
            candidate = match.group(0).rstrip(".,!?:")
            parts = _link_parts(candidate)
            if not parts:
                continue
            identity = "/".join(parts).lower()
            if identity not in seen:
                seen.add(identity)
                links.append(candidate)
        return links

    async def _issue_json(
        self, session: aiohttp.ClientSession, path: str, params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """读取有限大小的官方议题响应，不跟随议题转移跳转。"""
        try:
            async with session.get(
                "https://api.github.com/repos/" + path, params=params,
                headers=build_request_headers(custom_headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}),
                proxy=self.proxy_url, allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=Config.DEFAULT_TIMEOUT),
            ) as response:
                if response.status != 200:
                    raise RuntimeError(f"GitHub 议题接口请求失败（HTTP {response.status}），内容可能不可见或访问受限")
                chunks, size = [], 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise RuntimeError("GitHub 议题响应过大，已停止读取")
                    chunks.append(chunk)
                return json.loads(b"".join(chunks))
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, RecursionError) as exc:
            raise RuntimeError("GitHub 议题网络请求失败或响应格式无效") from exc

    @staticmethod
    def _comment_time(value: Any) -> str:
        """将明确带时区的时间转换为北京时间。"""
        try:
            date = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else None
            if date is not None and date.tzinfo is not None:
                return date.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OverflowError):
            pass
        return ""

    async def _comments(
        self, session: aiohttp.ClientSession, path: str, issue_id: str,
    ) -> List[Dict[str, Any]]:
        """限量读取普通评论，校验归属并保留失败前已取得的结果。"""
        comments, seen = [], set()
        page_size = min(100, self.hot_comment_count)
        if page_size <= 0:
            return comments
        try:
            for page in range(1, MAX_COMMENT_PAGES + 1):
                rows = await self._issue_json(session, f"{path}/issues/{issue_id}/comments", {"per_page": page_size, "page": page})
                if not isinstance(rows, list):
                    raise RuntimeError("GitHub 议题评论列表格式无效")
                previous = len(seen)
                for row in rows:
                    if not isinstance(row, dict) or row.get("issue_url") != f"https://api.github.com/repos/{path}/issues/{issue_id}":
                        continue
                    identity, message, user = row.get("id"), row.get("body"), row.get("user")
                    if type(identity) is not int or identity <= 0 or identity in seen or not isinstance(message, str) or not message.strip():
                        continue
                    user = user if isinstance(user, dict) else {}
                    seen.add(identity)
                    comment = {
                        "id": str(identity), "username": str(user.get("login") or "未知用户"),
                        "uid": str(user.get("id") or ""), "message": message.strip(),
                        "time": self._comment_time(row.get("created_at")),
                    }
                    reactions = row.get("reactions")
                    likes = reactions.get("+1") if isinstance(reactions, dict) else None
                    if type(likes) is int and likes >= 0:
                        comment["likes"] = likes
                    comments.append(comment)
                    if len(comments) >= self.hot_comment_count:
                        return comments
                if len(rows) < page_size or len(seen) == previous:
                    break
        except asyncio.CancelledError:
            raise
        except RuntimeError as exc:
            logger.warning(f"GitHub 议题评论获取失败，保留正文及已取得评论：{exc}")
        return comments

    async def _fetch_repository(
        self, session: aiohttp.ClientSession, owner: str, repo: str
    ) -> Dict[str, Any]:
        """获取公开仓库详情，并有限跟随仓库迁移产生的官方重定向。"""
        api_url = f"https://api.github.com/repos/{owner}/{repo}"
        headers = build_request_headers(custom_headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        try:
            for redirect_count in range(MAX_REDIRECTS + 1):
                async with session.get(
                    api_url,
                    headers=headers,
                    proxy=self.proxy_url,
                    timeout=aiohttp.ClientTimeout(total=Config.DEFAULT_TIMEOUT),
                    allow_redirects=False,
                ) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        location = response.headers.get("Location", "")
                        redirected_url = urljoin(api_url, location)
                        if not location or not _api_url(redirected_url):
                            raise RuntimeError("GitHub 仓库接口返回了无效重定向")
                        if redirect_count == MAX_REDIRECTS:
                            raise RuntimeError("GitHub 仓库接口重定向次数过多")
                        api_url = redirected_url
                        continue
                    if response.status in {403, 429}:
                        raise RuntimeError("GitHub 接口访问受限或请求频率超限，请稍后再试")
                    if response.status == 404:
                        raise RuntimeError("GitHub 仓库不存在、已删除或不是公开仓库")
                    if response.status != 200:
                        raise RuntimeError(f"GitHub 仓库请求失败（HTTP {response.status}）")
                    payload = await response.json(content_type=None)
                    self._validate_repository(payload, api_url)
                    return payload
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError as exc:
            raise RuntimeError("GitHub 仓库请求超时，请稍后再试") from exc
        except aiohttp.ClientError as exc:
            raise RuntimeError("GitHub 仓库网络请求失败") from exc
        except ValueError as exc:
            raise RuntimeError("GitHub 仓库接口返回的数据格式无效") from exc
        raise RuntimeError("GitHub 仓库接口未返回有效结果")

    @staticmethod
    def _validate_repository(payload: Any, api_url: str) -> None:
        """校验公开仓库身份，防止错误对象被当作仓库概况。"""
        if not isinstance(payload, dict):
            raise RuntimeError("GitHub 仓库接口返回的数据格式无效")
        repository_id = payload.get("id")
        full_name = payload.get("full_name")
        owner = payload.get("owner")
        parts = REPOSITORY_RE.fullmatch(full_name) if isinstance(full_name, str) else None
        if (
            type(repository_id) is not int or repository_id <= 0
            or payload.get("private") is not False
            or not parts or not isinstance(owner, dict)
            or owner.get("login") != parts.group("owner")
            or payload.get("name") != parts.group("repo")
            or parts.group("repo") in {".", ".."}
        ):
            raise RuntimeError("GitHub 接口未返回有效公开仓库详情")
        canonical_parts = _repository_parts(payload.get("html_url"))
        if not canonical_parts or "/".join(canonical_parts).lower() != full_name.lower():
            raise RuntimeError("GitHub 接口返回的仓库链接与身份不一致")
        api_path = urlsplit(api_url).path
        if api_path.startswith("/repositories/"):
            matches_request = api_path[14:] == str(repository_id)
        else:
            matches_request = api_path[7:].lower() == full_name.lower()
        if not matches_request:
            raise RuntimeError("GitHub 接口返回了其他仓库的数据")

    @staticmethod
    def _build_description(repository: Dict[str, Any]) -> str:
        """将仓库简介和关键统计整理为简短中文概况。"""
        description = repository.get("description")
        description = " ".join(description.split()) if isinstance(description, str) else ""
        if len(description) > MAX_DESCRIPTION_LENGTH:
            description = description[:MAX_DESCRIPTION_LENGTH].rstrip() + "…"
        language = repository.get("language")
        language = language.strip() if isinstance(language, str) else ""
        counts = []
        for key in ("stargazers_count", "forks_count"):
            value = repository.get(key)
            counts.append(f"{value:,}" if type(value) is int and value >= 0 else "未知")
        license_data = repository.get("license")
        license_name = "未标注"
        if isinstance(license_data, dict):
            spdx_id = license_data.get("spdx_id")
            name = license_data.get("name")
            if isinstance(spdx_id, str) and spdx_id.strip() and spdx_id != "NOASSERTION":
                license_name = spdx_id.strip()
            elif isinstance(name, str) and name.strip():
                license_name = name.strip()
        states = ["公开"]
        for key, label in (("fork", "分叉仓库"), ("archived", "已归档"), ("disabled", "已禁用")):
            if repository.get(key) is True:
                states.append(label)
        lines = [
            description or "暂无仓库简介",
            f"主要语言：{language or '未标注'}",
            f"Star：{counts[0]} · Fork：{counts[1]}",
            f"许可证：{license_name}",
            "状态：" + " · ".join(states),
        ]
        updated_at = repository.get("updated_at")
        if isinstance(updated_at, str):
            try:
                updated_time = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                if updated_time.tzinfo is not None:
                    lines.append(
                        "更新时间：" + updated_time.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                    )
            except (ValueError, OverflowError):
                pass
        return "\n".join(lines)

    async def parse(
        self, session: aiohttp.ClientSession, url: str
    ) -> Optional[MediaMetadata]:
        """解析公开仓库概况或议题正文与有限评论。

        Args:
            session: 已有的异步 HTTP 会话。
            url: GitHub 仓库根链接或 Issue 链接。

        Returns:
            仓库概况与截图请求，或议题正文及可选普通评论。
        """
        parts = _link_parts(url)
        if not parts:
            raise RuntimeError("不支持此 GitHub 链接，仅支持公开仓库首页和 Issue")
        owner, repo, issue_id = parts
        async with self.semaphore:
            repository = await self._fetch_repository(session, owner, repo)
            if issue_id:
                path = repository["full_name"]
                issue = await self._issue_json(session, f"{path}/issues/{issue_id}")
                expected_url = f"https://github.com/{path}/issues/{issue_id}"
                if (not isinstance(issue, dict) or type(issue.get("number")) is not int
                        or str(issue["number"]) != issue_id or issue.get("html_url") != expected_url
                        or issue.get("repository_url") != f"https://api.github.com/repos/{path}"
                        or "pull_request" in issue or not isinstance(issue.get("title"), str)
                        or not issue["title"].strip() or not isinstance(issue.get("user"), dict)):
                    raise RuntimeError("GitHub 返回的议题身份与目标不一致或内容不是 Issue")
                body = issue.get("body")
                state = {"open": "开放", "closed": "已关闭"}.get(issue.get("state"), "未知")
                metadata: MediaMetadata = {
                    "url": expected_url, "platform": self.name, "title": f"{path} #{issue_id} · {issue['title'].strip()}",
                    "author": str(issue["user"].get("login") or "未知用户"),
                    "timestamp": self._comment_time(issue.get("created_at")),
                    "desc": f"状态：{state}\n\n" + (body.strip() if isinstance(body, str) and body.strip() else "暂无议题正文"),
                    "video_urls": [], "image_urls": [],
                }
                if self.hot_comment_count:
                    metadata["hot_comments"] = await self._comments(session, path, issue_id)
                return metadata
        return {
            "url": "https://github.com/" + repository["full_name"],
            "title": repository["full_name"],
            "author": repository["owner"]["login"],
            "desc": self._build_description(repository),
            "platform": self.name,
            "readme_url": "https://github.com/" + repository["full_name"],
            "readme_selector": README_SELECTOR,
            "proxy_url": self.proxy_url,
            "video_urls": [],
            "image_urls": [],
        }
