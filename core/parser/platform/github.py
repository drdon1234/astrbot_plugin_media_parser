"""GitHub 仓库解析器，提取公开仓库概况与简短介绍。"""

import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import aiohttp

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
    """通过官方接口解析公开 GitHub 仓库，不读取 README 或下载文件。"""

    def __init__(self, proxy_url: Optional[str] = None) -> None:
        """初始化仓库解析器。

        Args:
            proxy_url: 仅供仓库接口请求使用的代理地址。
        """
        super().__init__("github")
        self.proxy_url = proxy_url
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)

    def can_parse(self, url: str) -> bool:
        """判断是否为受支持的 GitHub 仓库根链接。

        Args:
            url: 待判断的链接。

        Returns:
            是否为仓库根链接。
        """
        return _repository_parts(url) is not None

    def extract_links(self, text: str) -> List[str]:
        """从消息中提取仓库根链接，并按仓库身份去重。

        Args:
            text: 包含链接的消息文本。

        Returns:
            保留首次出现形式的仓库链接，供路由器按消息位置排序。
        """
        links: List[str] = []
        seen = set()
        for match in GITHUB_URL_RE.finditer(text or ""):
            candidate = match.group(0).rstrip(".,!?:")
            parts = _repository_parts(candidate)
            if not parts:
                continue
            identity = "/".join(parts).lower()
            if identity not in seen:
                seen.add(identity)
                links.append(candidate)
        return links

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
        """解析公开仓库根链接并返回纯文本概况。

        Args:
            session: 已有的异步 HTTP 会话。
            url: GitHub 仓库根链接。

        Returns:
            包含仓库名、作者、简短概况和规范链接的元数据。
        """
        parts = _repository_parts(url)
        if not parts:
            raise RuntimeError("不支持此 GitHub 链接，仅支持公开仓库首页")
        async with self.semaphore:
            repository = await self._fetch_repository(session, *parts)
        return {
            "url": "https://github.com/" + repository["full_name"],
            "title": repository["full_name"],
            "author": repository["owner"]["login"],
            "desc": self._build_description(repository),
            "platform": self.name,
            "video_urls": [],
            "image_urls": [],
        }
