"""解析器共用的文本、链接与 Git 对象处理工具。"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Union
from urllib.parse import parse_qs, unquote, urlparse


class SkipParse(Exception):
    pass


def select_snapshot_files(entries: Sequence[tuple[str, str, str, str]]) -> List[tuple[str, str]]:
    """按文档、构建清单、入口挑选最多四个普通文件，路径来自当前提交的目录树。

    Args:
        entries: 相对路径、对象类型、文件模式及对象哈希。

    Returns:
        选定文件的路径与对象哈希。
    """
    groups = (
        ("readme.zh-cn.md", "readme_zh.md", "readme.md", "readme.rst", "readme"),
        ("pyproject.toml", "package.json", "go.mod", "cargo.toml", "pom.xml", "build.gradle", "requirements.txt"),
        ("main.py", "main.go", "main.rs", "main.ts", "__main__.py", "index.ts", "index.js", "lib.rs", "app.py", "__init__.py"),
        ("dockerfile", "compose.yml", "compose.yaml", "docker-compose.yml", "setup.py", "makefile"),
    )
    files = [(path, sha) for path, kind, mode, sha in entries
             if kind == "blob" and mode in ("100644", "100755")
             and isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha)
             and isinstance(path, str) and re.fullmatch(r"[\w.-]+(?:/[\w.-]+)?", path)
             and not any(part in (".", "..") for part in path.split("/"))]
    selected = []
    for names in groups:
        matches = [(path, sha) for path, sha in files if path.rsplit("/", 1)[-1].lower() in names]
        if matches:
            selected.append(min(matches, key=lambda item: (
                names.index(item[0].rsplit("/", 1)[-1].lower()), item[0].count("/"), item[0]
            )))
    return selected


def _readme_excerpt(text: str) -> str:
    """为项目介绍与安装、快速开始章节分配摘录空间，不跟随文档链接。"""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    lines = [line for line in text.splitlines() if len(line) < 1500
             and not re.search(r"<img\b|!\[|shields\.io/", line, re.I)
             and not re.fullmatch(r"\s*(?:</?(?:div|p|br|a)\b[^>]*>\s*)+", line, re.I)]
    text = "\n".join(lines).strip()
    raw = text.encode("utf-8")
    if len(raw) <= 3072:
        return text

    # 快速开始与安装各选一个窗口。忽略代码围栏中的伪标题，保留原文顺序。
    sections = {}
    offset = 0
    fence = ""
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        start = offset
        offset += len(line.encode("utf-8"))
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            if not fence:
                fence = marker[1]
            elif marker[1].startswith(fence) and not line[marker.end():].strip():
                fence = ""
            continue
        if fence:
            continue
        if not (re.match(r"^ {0,3}#{1,6}\s+", line) or
                (line.strip() and index + 1 < len(lines) and re.fullmatch(r" {0,3}(?:=+|-+)\s*", lines[index + 1]))):
            continue
        if re.search(r"快速(?:开始|上手|入门)|快[速捷]接入|入门指南|\b(?:quick[ -]?start|get(?:ting)? started)\b", line, re.I):
            sections.setdefault("quickstart", start)
        elif re.search(r"安装|部署|引入|集成方式|引入依赖|\b(?:install(?:ation|ing)?|setup|set up|deployment)\b", line, re.I):
            sections.setdefault("install", start)

    if not sections:
        return raw[:2280].decode("utf-8", "ignore").strip() + "\n…\n" + raw[-780:].decode("utf-8", "ignore").strip()
    # 优先保证用法窗口。相邻或重叠窗口合并，剩余字节给项目定位和特性。
    ranges = []
    for start in sorted(sections.values()):
        end = min(len(raw), start + 1740 // len(sections))
        if ranges and start <= ranges[-1][1]:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], end))
        else:
            ranges.append((start, end))
    head = 3072 - sum(end - start for start, end in ranges) - 5 * len(ranges)
    slices = [(0, head)]
    for start, end in ranges:
        if start <= slices[-1][1]:
            slices[-1] = (slices[-1][0], max(slices[-1][1], end))
        else:
            slices.append((start, end))
    return "\n…\n".join(raw[start:end].decode("utf-8", "ignore").strip() for start, end in slices)


def verified_blob_excerpt(data: Any, sha: str, path: str) -> str:
    """校验 Git 对象哈希，提取 README 重点章节或普通文本的首尾摘录。

    Args:
        data: 包含 Base64 编码文件的接口响应。
        sha: 固定提交的目录树记录的文件哈希。
        path: 用于识别 README 的文件路径。

    Returns:
        至多 3072 字节的摘录，无有效材料时为空。
    """
    if not isinstance(data, dict) or data.get("encoding") != "base64":
        return ""
    content = data.get("content")
    if not isinstance(content, str) or len(content) > 100_000:
        return ""
    try:
        raw = base64.b64decode("".join(content.split()), validate=True)
        if len(raw) > 65536 or b"\x00" in raw:
            return ""
        digest = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\x00" + raw).hexdigest()
        if digest != sha:
            return ""
        text = raw.decode("utf-8-sig")
        if path.rsplit("/", 1)[-1].lower().startswith("readme"):
            return _readme_excerpt(text)
        raw = text.encode("utf-8")
        if len(raw) <= 3072:
            return text.strip()
        return (raw[:2280].decode("utf-8", errors="ignore").strip() + "\n…\n"
                + raw[-780:].decode("utf-8", errors="ignore").strip())
    except (UnicodeError, ValueError):
        return ""


def format_percentage(value: float) -> str:
    """将百分数显示为最多两位小数。

    Args:
        value: 从零到一百的百分数。

    Returns:
        带百分号的文本，极小的占比及其余量用不等号表示。
    """
    if 0 < value < 0.01:
        return "<0.01%"
    if 99.99 < value < 100:
        return ">99.99%"
    return f"{value:.2f}".rstrip("0").rstrip(".") + "%"


def format_directory_tree(
    entries: Sequence[tuple[str, str]], expanded_name: str = "",
    children: Sequence[tuple[str, str]] = (), root_note: str = "", child_note: str = "",
) -> str:
    """将根目录与一个展开的子目录排成同一棵树。

    Args:
        entries: 根目录的名称与 Git 对象类型。
        expanded_name: 展开的目录名。
        children: 展开目录的直属条目。
        root_note: 根目录列表不完整时的说明。
        child_note: 子目录列表不完整时的说明。

    Returns:
        带代码围栏的目录树，无有效条目时返回空串。
    """
    # 优先显示入口与构建清单，避免根目录的多语言文档占满快照。
    priority = {
        "main.py", "main.go", "main.rs", "main.ts", "index.js", "index.ts", "__main__.py",
        "pyproject.toml", "requirements.txt", "package.json", "go.mod", "cargo.toml",
        "pom.xml", "build.gradle", "build.gradle.kts", "cmakelists.txt", "dockerfile",
        "makefile", "gemfile", "composer.json", "pubspec.yaml", "readme.md",
    }
    def visible_items(items: Sequence[tuple[str, str]], pinned: str, note: str) -> List[str]:
        result = []
        omitted = []
        for directories in (True, False):
            names = sorted({
                name for name, kind in items
                if kind in ("tree", "blob", "commit") and (kind == "tree") == directories
                and isinstance(name, str) and name.isprintable() and "/" not in name
                and name not in ("", ".", "..")
            }, key=lambda name: (name != pinned, name.startswith("."), name.casefold() not in priority,
                                 name.casefold(), name))
            result.extend(name + "/" if directories else name for name in names[:8])
            if len(names) > 8:
                omitted.append(f"{len(names) - 8} 个{'目录' if directories else '文件'}")
        if omitted:
            result.append("… 另有 " + "、".join(omitted))
        if note:
            result.append("… " + note)
        return result

    root = visible_items(entries, expanded_name, root_note)
    if not root:
        return ""
    lines = []
    for index, name in enumerate(root):
        last = index == len(root) - 1
        lines.append(("└── " if last else "├── ") + name)
        if expanded_name and name == expanded_name + "/":
            nested = visible_items(children, "", child_note)
            prefix = "    " if last else "│   "
            for child_index, child in enumerate(nested):
                connector = "└── " if child_index == len(nested) - 1 else "├── "
                lines.append(prefix + connector + child)
    # 按字面量展示文件名，避免 __init__.py 等名称被当成强调标记。
    listing = "\n".join(lines)
    longest = max((len(run) for run in re.findall(r"`+", listing)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}tree\n{listing}\n{fence}"


def format_duration_ms(duration_ms) -> str:
    """将毫秒时长格式化为 mm:ss 或 hh:mm:ss。"""
    if duration_ms is None:
        return ""
    try:
        total_seconds = max(0, int(duration_ms) // 1000)
    except (TypeError, ValueError):
        return ""

    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _ensure_url_has_scheme(url: str) -> str:
    """确保URL带有scheme，便于urlparse正确解析hostname。"""
    if not url:
        return url
    u = url.strip()
    if u.startswith("//"):
        return "https:" + u
    if u.startswith(("http://", "https://")):
        return u
    return "https://" + u


def _is_live_url_basic(url: str) -> bool:
    """仅基于hostname标签判断是否为live域名。"""
    parsed = urlparse(_ensure_url_has_scheme(url))
    host = (parsed.hostname or "").strip(".").lower()
    if not host:
        return False
    labels = [x for x in host.split(".") if x]
    return "live" in labels


def is_live_url(url: str) -> bool:
    """判断是否为直播类型链接或“跳转到直播”的重定向链接。

    规则：
    - 直链：hostname 的任意标签为 live，则判定为直播域名链接
    - 重定向：若URL的 query 参数里包含一个可解码出的URL，且该URL为直播域名链接，则也判定为直播

    例：
    - https://live.bilibili.com/ -> True
    - https://api.live.bilibili.com/ -> True
    - https://example.com/redirect?url=https%3A%2F%2Flive.example.com%2Froom -> True
    - https://www.douyin.com/ -> False
    """
    if not url:
        return False
    try:
        if _is_live_url_basic(url):
            return True

        parsed = urlparse(_ensure_url_has_scheme(url))
        qs = parse_qs(parsed.query, keep_blank_values=True)
        for values in qs.values():
            for v in values:
                if not v:
                    continue
                candidate = v.strip()
                for _ in range(3):
                    if _is_live_url_basic(candidate):
                        return True
                    new_candidate = unquote(candidate)
                    if new_candidate == candidate:
                        break
                    candidate = new_candidate

        return False
    except Exception:
        return False


def extract_url_from_card_data(msg_data) -> Optional[str]:
    """从单个消息段的 data 字段中提取 QQ 结构化卡片 URL。"""
    try:
        curl_link = None
        if isinstance(msg_data, dict) and not msg_data.get('data'):
            meta = msg_data.get("meta") or {}
            detail_1 = meta.get("detail_1") or {}
            curl_link = detail_1.get("qqdocurl")
            if not curl_link:
                news = meta.get("news") or {}
                curl_link = news.get("jumpUrl")

        if not curl_link:
            json_str = (
                msg_data.get('data', '')
                if isinstance(msg_data, dict) else msg_data
            )
            if json_str and isinstance(json_str, str):
                message_data = json.loads(json_str)
                meta = message_data.get("meta") or {}
                detail_1 = meta.get("detail_1") or {}
                curl_link = detail_1.get("qqdocurl")
                if not curl_link:
                    news = meta.get("news") or {}
                    curl_link = news.get("jumpUrl")
        return curl_link
    except (AttributeError, KeyError, json.JSONDecodeError, TypeError):
        return None


def build_request_headers(
    is_video: bool = False,
    referer: str = None,
    default_referer: str = None,
    origin: str = None,
    user_agent: str = None,
    custom_headers: dict = None
) -> dict:
    """构建请求头

    Args:
        is_video: 是否为视频（True为视频，False为图片）
        referer: Referer URL，如果提供则使用
        default_referer: 默认Referer URL（如果referer未提供）
        origin: Origin URL（可选）
        user_agent: User-Agent（可选，默认使用桌面端 User-Agent）
        custom_headers: 自定义请求头（如果提供，会与默认请求头合并）

    Returns:
        请求头字典
    """
    if custom_headers and 'Referer' in custom_headers:
        referer_url = custom_headers['Referer']
    else:
        referer_url = referer if referer else (default_referer or '')
    
    if user_agent:
        effective_user_agent = user_agent
    else:
        effective_user_agent = (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        )
    
    default_accept_language = 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7'
    
    if is_video:
        headers = {
            'User-Agent': effective_user_agent,
            'Accept': '*/*',
            'Accept-Language': default_accept_language,
            'Accept-Encoding': 'gzip, deflate',
        }
    else:
        headers = {
            'User-Agent': effective_user_agent,
            'Accept': (
                'image/avif,image/webp,image/apng,image/svg+xml,'
                'image/*,*/*;q=0.8'
            ),
            'Accept-Language': default_accept_language,
            'Accept-Encoding': 'gzip, deflate',
        }
    
    if referer_url:
        headers['Referer'] = referer_url
    
    if origin:
        headers['Origin'] = origin
    
    if custom_headers:
        headers.update(custom_headers)
    
    return headers


def join_content_text(parts: Sequence[Union[str, int]]) -> str:
    """拼接正文片段中的文字，忽略配图位置标记。

    Args:
        parts: 文字片段与配图位置标记交错的序列，整数表示 image_urls 下标。

    Returns:
        仅由文字片段拼接的原始正文。
    """
    return "".join(part for part in parts if isinstance(part, str))


def build_content_blocks(
    parts: Sequence[Union[str, int]],
    clean_text: Callable[[str], str],
) -> List[Dict[str, Any]]:
    """按原文顺序生成正文文字块与配图块。

    Args:
        parts: 文字片段与配图位置标记交错的序列，整数表示 image_urls 下标。
        clean_text: 平台自身的正文清洗函数，对相邻配图之间的文字分别调用。

    Returns:
        同时包含文字与配图时返回有序块列表；缺少任一类型时返回空列表。
        同一配图重复出现时只保留首次位置。
    """
    blocks: List[Dict[str, Any]] = []
    buffer: List[str] = []
    seen_images = set()
    for part in [*parts, None]:
        if isinstance(part, str):
            buffer.append(part)
            continue
        text = clean_text("".join(buffer))
        buffer = []
        if text:
            blocks.append({"type": "text", "text": text})
        if (
            isinstance(part, int)
            and not isinstance(part, bool)
            and part >= 0
            and part not in seen_images
        ):
            seen_images.add(part)
            blocks.append({"type": "image", "index": part})
    has_text = any(block["type"] == "text" for block in blocks)
    return blocks if has_text and seen_images else []
