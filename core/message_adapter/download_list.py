"""识别分享卡简介里成组的下载入口，保留作者说明与完整地址。"""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class DownloadList:
    """同一区域的下载入口，依次保存作者标签、完整地址及可选提取码。"""

    entries: tuple[tuple[str, str, str], ...]


_ENTRY = re.compile(
    r"^[ \t]*(?P<label>(?:百度云?网盘|百度云|夸克网盘|阿里云盘|天翼云盘|迅雷云盘|"
    r"蓝奏云|123云盘|OneDrive|Google Drive|CurseForge)(?:下载)?(?:地址|链接|入口)?|"
    r"(?:主|备用|镜像|文件|资源)?下载(?:地址|链接|入口)?)"
    r"[ \t]*[：:][ \t]*(?:\n[ \t]*)?(?P<url>https?://[^\s<>]+)"
    r"(?:(?:[ \t]+|[ \t]*\n[ \t]*)(?P<code>(?:提取码|访问码)[ \t]*[：:][ \t]*[A-Za-z0-9]+))?[ \t]*$",
    re.I | re.M,
)
_STRUCTURED = re.compile(r"^\s*(?:```|~~~|(?:\d+[.)、]|步骤\s*\d+|第[一二三四五六七八九十\d]+步))", re.M)
_NOTE = re.compile(r"(?:或通过|也可通过|亦可通过|下载说明[：:]|安装说明[：:]).*|.{1,30}(?:同步发布|全平台发布)[）)]?")


def download_blocks(description: str) -> list[str | DownloadList]:
    """将至少两个不同的明确下载地址排为清单，其余区域沿用原文。"""
    if _STRUCTURED.search(description):
        return [description]
    source = description.replace("\r\n", "\n").replace("\r", "\n")
    groups = []
    for match in _ENTRY.finditer(source):
        url = match["url"]
        try:
            host = urlsplit(url).hostname
        except ValueError:
            continue
        if not host or url.count("://") != 1:
            continue
        if groups:
            gap = source[groups[-1][-1].end():match.start()]
            notes = [line.strip() for line in gap.splitlines() if line.strip()]
            if len(notes) <= 2 and all(_NOTE.fullmatch(line) for line in notes):
                groups[-1].append(match)
                continue
        groups.append([match])

    blocks = []
    cursor = 0
    notes = ""
    for group in groups:
        if len({match["url"] for match in group}) < 2:
            continue
        blocks.append(notes + source[cursor:group[0].start()])
        blocks.append(DownloadList(tuple((m["label"], m["url"], m["code"] or "") for m in group)))
        notes = "\n".join(note for first, second in zip(group, group[1:])
                           if (note := source[first.end():second.start()].strip()))
        cursor = group[-1].end()
    if not blocks:
        return [description]
    blocks.append(notes + source[cursor:])
    return [block for block in blocks if not isinstance(block, str) or block.strip()]
