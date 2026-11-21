"""从分享卡可见的作者正文中按顺序提取网址及其说明。"""

import re
from urllib.parse import urlsplit


_URL = re.compile(r"""https?://[^\s<>"\x60，。；！？、（）【】「」《》“”]+""", re.I)
_CODE = re.compile(r"^\s*(?:[,，;；]\s*)?((?:提取码|访问码)\s*[:：]?\s*[a-zA-Z0-9]+)")


def collect_body_links(text: str) -> list[tuple[str, str]]:
    """收集原样网址和相邻说明，同一地址只保留第一次出现的位置。"""
    links = {}
    lines = text.splitlines()
    for index, line in enumerate(lines):
        end = 0
        while match := _URL.search(line, end):
            prefix = line[end:match.start()].strip()
            url = match[0]
            # Markdown 或行文中包住网址的右括号结束该地址，保留路径内成对的括号。
            depth = 0
            for pos, char in enumerate(url):
                if char == "(":
                    depth += 1
                elif char == ")":
                    if not depth:
                        url = url[:pos]
                        break
                    depth -= 1
            url = url.rstrip(".,;!")
            while url.endswith("]") and url.count("]") > url.count("["):
                url = url[:-1]
            for mark in ("**", "__", "~~", "*"):
                if prefix.endswith(mark) and url.endswith(mark):
                    prefix = prefix[:-len(mark)]
                    url = url[:-len(mark)]
                    break
            end = match.start() + len(url)
            markdown = re.search(r"\[([^\]]*)\]\($", prefix)
            try:
                host = urlsplit(url).hostname
            except ValueError:
                continue
            if not host or url in links:
                continue
            label = markdown[1] if markdown else prefix.strip(" \t<>（）()[]：:，,；;")
            if not label and index:
                previous = lines[index - 1].strip()
                if (len(previous) <= 40 and not _URL.search(previous) and not _CODE.match(previous)
                        and not previous.endswith(("。", "！", "？", ".", "!", "?"))):
                    label = previous.rstrip("：:").strip()
            if not label or len(label) > 80:
                label = host
            tail = line[match.start() + len(url):].strip(" \t)>）")
            code = _CODE.match(tail or (lines[index + 1] if index + 1 < len(lines) else ""))
            if code:
                label += " · " + code[1]
            links[url] = label
    return [(label, url) for url, label in links.items()]
