"""按会话与解析结果保存嵌入图片的原文件，并关联可引用的消息。"""

import hashlib
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Iterator, Sequence

from .cache_marker import (
    EXPIRY_FILE_NAME,
    MARKER_FILE_NAME,
    mark_subdir_expires_at,
    stamp_subdir,
)


ORIGINAL_TTL = 3600
TOKEN_PATTERN = re.compile(r"〔(?:原图|分享)·([0-9a-f]{16})〕")


class OriginalImages:
    """使用独立的标记目录，复用媒体缓存的过期与手动清理机制。"""

    def __init__(self, root: str, scope: str):
        if not root or not scope:
            raise ValueError("原图登记缺少缓存目录或会话标识")
        self.root = Path(root)
        self.prefix = "original_" + self._digest(scope) + "_"

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()[:32]

    def save(self, paths: list[str], links: Sequence[tuple[str, str]] = ()) -> str:
        """按画面顺序保留原文件，返回该条结果所有分页共用的编号。"""
        token = uuid.uuid4().hex[:16]
        folder = self.root / (self.prefix + token)
        folder.mkdir(parents=True)
        try:
            stamp_subdir(str(folder))
            if links:
                (folder / "links.json").write_text(json.dumps(links, ensure_ascii=False), encoding="utf-8")
            for index, value in enumerate(dict.fromkeys(paths)):
                source = Path(value)
                target = folder / f"image_{index:08d}{source.suffix}"
                try:
                    os.link(source, target)
                except OSError:
                    shutil.copyfile(source, target)
            if not mark_subdir_expires_at(str(folder), time.time() + ORIGINAL_TTL):
                raise OSError("原图缓存无法设置过期时间")
        except (OSError, ValueError):
            shutil.rmtree(folder, ignore_errors=True)
            raise
        return token

    def bind(self, message_id: str, tokens: list[str]) -> None:
        """记录一条实际发送的消息包含哪些结果，保留合并转发的链接顺序。"""
        key = self._digest(message_id)
        for index, token in enumerate(tokens):
            folder = self.root / (self.prefix + token)
            (folder / f"reply_{key}_{index:08d}").touch()

    def _folders(
        self, message_id: str, quoted_text: str, delivery_ttl: int = 0
    ) -> Iterator[Path]:
        """由当前会话的消息回执或引用文字定位未过期的结果。"""
        matches = (
            self.root.glob(f"{self.prefix}*/reply_{self._digest(message_id)}_*")
            if message_id
            else ()
        )
        folders = [
            match.parent for match in sorted(matches, key=lambda path: path.name)
        ]
        folders.extend(
            self.root / (self.prefix + key)
            for key in TOKEN_PATTERN.findall(quoted_text)
        )
        for folder in dict.fromkeys(folders):
            try:
                expires_at = float(
                    (folder / EXPIRY_FILE_NAME).read_text(encoding="utf-8")
                )
                now = time.time()
                if (
                    expires_at <= now
                    or (folder / MARKER_FILE_NAME).stat().st_mtime + ORIGINAL_TTL <= now
                ):
                    continue
                if delivery_ttl and expires_at < now + delivery_ttl:
                    mark_subdir_expires_at(str(folder), now + delivery_ttl)
                yield folder
            except (OSError, ValueError):
                continue

    def find(self, message_id: str, quoted_text: str, delivery_ttl: int = 0) -> list[str]:
        """读取引用结果中仍可发送的原图文件。"""
        return [str(path) for folder in self._folders(message_id, quoted_text, delivery_ttl)
                for path in sorted(folder.glob("image_*")) if path.is_file()]

    def find_links(self, message_id: str, quoted_text: str) -> list[tuple[str, str]]:
        """按分享卡顺序合并正文网址，通过同一次引用取回的地址只发送一次。"""
        result = {}
        for folder in self._folders(message_id, quoted_text):
            try:
                entries = json.loads((folder / "links.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for label, url in entries:
                result.setdefault(url, label)
        return [(label, url) for url, label in result.items()]
