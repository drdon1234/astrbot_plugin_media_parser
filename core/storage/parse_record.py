"""解析频率限制与持久化记录。"""
import json
import os
import shutil
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from ..logger import logger


_TRACKING_PARAM_NAMES = {
    "app",
    "app_id",
    "appid",
    "app_platform",
    "app_version",
    "channel",
    "enter_from",
    "from",
    "from_source",
    "from_user",
    "from_user_id",
    "is_from_webapp",
    "platform",
    "previous_page",
    "refer",
    "referer",
    "sec_uid",
    "sender",
    "sender_device",
    "sender_id",
    "source",
    "source_share_type",
    "spm",
    "spmid",
    "subbiz",
    "t",
    "time",
    "timestamp",
    "ts",
    "tt_from",
    "u_code",
    "ug_source",
    "unique_k",
    "xsec_source",
    "xsec_token",
    "xhsshare",
}


@dataclass
class ParseRateLimitRule:
    max_count: int = 0
    window_seconds: int = 0

    @property
    def enabled(self) -> bool:
        return self.max_count > 0 and self.window_seconds > 0


@dataclass
class BlockedParseItem:
    link: str
    parser_name: str
    scope: str
    count: int
    max_count: int
    window_seconds: int

    @property
    def reason(self) -> str:
        subject = "同链接" if self.scope == "link" else "同用户"
        return (
            f"{subject}解析频率限制: "
            f"{self.count}/{self.window_seconds}s >= {self.max_count}"
        )


class ParseRecordManager:
    """按标准链接和用户维度限制解析次数，并裁剪持久化记录。"""

    def __init__(
        self,
        *,
        record_file: str = "",
        same_link_max_count: int = 0,
        same_link_window_seconds: int = 0,
        same_user_max_count: int = 0,
        same_user_window_seconds: int = 0,
    ):
        self.record_file = str(record_file or "").strip()
        self.same_link = ParseRateLimitRule(
            max(0, int(same_link_max_count or 0)),
            max(0, int(same_link_window_seconds or 0)),
        )
        self.same_user = ParseRateLimitRule(
            max(0, int(same_user_max_count or 0)),
            max(0, int(same_user_window_seconds or 0)),
        )
        self._lock = threading.RLock()
        self._loaded = False
        self._records: Dict[str, Any] = self._empty_records()
        self._persist_warning_emitted = False

    @property
    def enabled(self) -> bool:
        return self.same_link.enabled or self.same_user.enabled

    @property
    def retention_seconds(self) -> int:
        windows = [
            rule.window_seconds
            for rule in (self.same_link, self.same_user)
            if rule.enabled
        ]
        return max(windows) if windows else 0

    @staticmethod
    def _empty_records() -> Dict[str, Any]:
        return {"version": 1, "links": {}, "users": {}, "updated_at": 0}

    @staticmethod
    def build_user_key(platform_name: Any, sender_id: Any) -> str:
        platform = str(platform_name or "unknown").strip() or "unknown"
        sender = str(sender_id or "unknown").strip() or "unknown"
        return f"{platform}:{sender}"

    @staticmethod
    def _should_drop_query_param(name: str) -> bool:
        lower = str(name or "").strip().lower()
        if not lower:
            return True
        if lower.startswith("utm_"):
            return True
        if "share" in lower:
            return True
        return lower in _TRACKING_PARAM_NAMES

    @classmethod
    def canonicalize_url(cls, url: str) -> str:
        text = str(url or "").strip()
        if not text:
            return ""

        parsed = urlparse(text)
        if not parsed.scheme or not parsed.netloc:
            return text.rstrip("/")

        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").lower()
        try:
            port = parsed.port
        except ValueError:
            port = None
        if port and not (
            (scheme == "http" and port == 80) or
            (scheme == "https" and port == 443)
        ):
            host = f"{host}:{port}"

        path = parsed.path or "/"
        if path != "/":
            path = path.rstrip("/")

        kept_params = []
        for name, value in parse_qsl(parsed.query, keep_blank_values=True):
            if cls._should_drop_query_param(name):
                continue
            kept_params.append((name, value))
        kept_params.sort(key=lambda item: (item[0].lower(), item[1]))
        query = urlencode(kept_params, doseq=True)
        return urlunparse((scheme, host, path, "", query, ""))

    @classmethod
    def build_link_key(cls, url: str, parser_name: Any = "") -> str:
        parser = str(parser_name or "unknown").strip() or "unknown"
        canonical = cls.canonicalize_url(url)
        return f"{parser}:{canonical}" if canonical else ""

    def filter_links(
        self,
        links_with_parser: List[Tuple[str, Any]],
        *,
        user_key: str,
        now: Optional[float] = None,
    ) -> Tuple[List[Tuple[str, Any]], List[BlockedParseItem]]:
        """返回允许解析的链接，并记录本次允许的解析尝试。"""
        if not self.enabled or not links_with_parser:
            return links_with_parser, []

        current = int(now or time.time())
        normalized_user_key = str(user_key or "unknown").strip() or "unknown"

        with self._lock:
            self._load()
            self._prune(current)
            allowed: List[Tuple[str, Any]] = []
            blocked: List[BlockedParseItem] = []
            changed = False

            for link, parser in links_with_parser:
                parser_name = getattr(parser, "name", "") or "unknown"
                link_key = self.build_link_key(link, parser_name)
                if self.same_link.enabled and link_key:
                    link_count = self._count_recent(
                        "links",
                        link_key,
                        current,
                        self.same_link.window_seconds,
                    )
                    if link_count >= self.same_link.max_count:
                        blocked.append(BlockedParseItem(
                            link=link,
                            parser_name=str(parser_name),
                            scope="link",
                            count=link_count,
                            max_count=self.same_link.max_count,
                            window_seconds=self.same_link.window_seconds,
                        ))
                        continue

                if self.same_user.enabled:
                    user_count = self._count_recent(
                        "users",
                        normalized_user_key,
                        current,
                        self.same_user.window_seconds,
                    )
                    if user_count >= self.same_user.max_count:
                        blocked.append(BlockedParseItem(
                            link=link,
                            parser_name=str(parser_name),
                            scope="user",
                            count=user_count,
                            max_count=self.same_user.max_count,
                            window_seconds=self.same_user.window_seconds,
                        ))
                        continue

                allowed.append((link, parser))
                if self.same_link.enabled and link_key:
                    self._append_timestamp("links", link_key, current)
                    changed = True
                if self.same_user.enabled:
                    self._append_timestamp("users", normalized_user_key, current)
                    changed = True

            if changed or blocked:
                self._save(current)
            return allowed, blocked

    def record_metadata_links(
        self,
        metadata_list: Iterable[Dict[str, Any]],
        *,
        now: Optional[float] = None,
    ) -> None:
        """解析完成后记录平台返回的最终链接别名。"""
        if not self.same_link.enabled:
            return

        current = int(now or time.time())
        with self._lock:
            self._load()
            self._prune(current)
            changed = False
            seen_keys = set()

            for metadata in metadata_list or []:
                if not isinstance(metadata, dict):
                    continue
                parser_name = (
                    metadata.get("parser_name") or
                    metadata.get("platform") or
                    "unknown"
                )
                source_url = metadata.get("source_url") or ""
                final_url = metadata.get("url") or ""
                source_key = self.build_link_key(source_url, parser_name)
                final_key = self.build_link_key(final_url, parser_name)
                if (
                    not final_key or
                    final_key == source_key or
                    final_key in seen_keys
                ):
                    continue
                seen_keys.add(final_key)
                self._append_timestamp("links", final_key, current)
                changed = True

            if changed:
                self._save(current)

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.record_file or not os.path.isfile(self.record_file):
            self._records = self._empty_records()
            return
        try:
            with open(self.record_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("records root is not an object")
            self._records = {
                "version": 1,
                "links": (
                    data.get("links")
                    if isinstance(data.get("links"), dict) else
                    {}
                ),
                "users": (
                    data.get("users")
                    if isinstance(data.get("users"), dict) else
                    {}
                ),
                "updated_at": data.get("updated_at", 0),
            }
        except Exception as e:
            self._backup_corrupt_record_file()
            logger.warning(f"读取解析频率记录失败，已重置记录: {e}")
            self._records = self._empty_records()

    def _backup_corrupt_record_file(self) -> None:
        if not self.record_file or not os.path.isfile(self.record_file):
            return
        try:
            stamp = time.strftime("%Y%m%d%H%M%S")
            backup = f"{self.record_file}.corrupt-{stamp}.bak"
            shutil.copy2(self.record_file, backup)
        except Exception as e:
            logger.warning(f"备份损坏的解析频率记录失败: {e}")

    def _save(self, current: Optional[int] = None) -> None:
        if current is None:
            current = int(time.time())
        self._records["updated_at"] = current
        if not self.record_file:
            if not self._persist_warning_emitted:
                logger.warning("未配置解析频率记录文件，限流记录仅在内存中生效")
                self._persist_warning_emitted = True
            return

        directory = os.path.dirname(self.record_file)
        try:
            if directory:
                os.makedirs(directory, exist_ok=True)
            tmp_path = f"{self.record_file}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(
                    self._records,
                    f,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            os.replace(tmp_path, self.record_file)
        except Exception as e:
            logger.warning(f"写入解析频率记录失败: {e}")

    def _prune(self, current: int) -> None:
        retention = self.retention_seconds
        if retention <= 0:
            self._records = self._empty_records()
            return
        cutoff = current - retention
        for bucket in ("links", "users"):
            raw_items = self._records.get(bucket)
            if not isinstance(raw_items, dict):
                self._records[bucket] = {}
                continue
            for key in list(raw_items.keys()):
                values = self._normalize_timestamps(raw_items.get(key))
                values = [ts for ts in values if ts >= cutoff]
                if values:
                    raw_items[key] = values
                else:
                    raw_items.pop(key, None)

    @staticmethod
    def _normalize_timestamps(values: Any) -> List[int]:
        if not isinstance(values, list):
            return []
        timestamps: List[int] = []
        for value in values:
            try:
                timestamp = int(value)
            except (TypeError, ValueError):
                continue
            if timestamp > 0:
                timestamps.append(timestamp)
        timestamps.sort()
        return timestamps

    def _count_recent(
        self,
        bucket: str,
        key: str,
        current: int,
        window_seconds: int,
    ) -> int:
        cutoff = current - window_seconds
        values = self._records.get(bucket, {}).get(key, [])
        return sum(1 for ts in self._normalize_timestamps(values) if ts >= cutoff)

    def _append_timestamp(self, bucket: str, key: str, timestamp: int) -> None:
        if not key:
            return
        items = self._records.setdefault(bucket, {})
        if not isinstance(items, dict):
            items = {}
            self._records[bucket] = items
        values = self._normalize_timestamps(items.get(key))
        values.append(int(timestamp))
        items[key] = values
