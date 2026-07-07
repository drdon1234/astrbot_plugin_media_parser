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

_SENT_LINK_RECORD_LIMIT = 10000


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
        sent_link_ttl_seconds: int = 0,
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
        self.sent_link_ttl_seconds = max(
            0,
            int(sent_link_ttl_seconds or 0),
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
        return {
            "version": 1,
            "links": {},
            "users": {},
            "sent_links": {},
            "updated_at": 0,
        }

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

    @classmethod
    def build_metadata_link_keys(cls, metadata: Dict[str, Any]) -> List[str]:
        parser_name = (
            metadata.get("parser_name") or
            metadata.get("platform") or
            "unknown"
        )
        urls = (metadata.get("source_url") or "", metadata.get("url") or "")
        keys: List[str] = []
        seen = set()
        for url in urls:
            key = cls.build_link_key(url, parser_name)
            if key and key not in seen:
                keys.append(key)
                seen.add(key)
        return keys

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

    def filter_duplicate_links(
        self,
        links_with_parser: List[Tuple[str, Any]],
        *,
        now: Optional[float] = None,
    ) -> Tuple[List[Tuple[str, Any]], List[Tuple[str, str]]]:
        """过滤已经发送过结果的原始链接，避免重复解析和重复输出。"""
        if not links_with_parser:
            return [], []

        current = int(now or time.time())
        with self._lock:
            self._load()
            self._ensure_record_buckets()
            if self._prune_sent_links(current):
                self._save(current)
            sent_links = self._records["sent_links"]
            allowed: List[Tuple[str, Any]] = []
            duplicates: List[Tuple[str, str]] = []
            seen_in_batch = set()

            for link, parser in links_with_parser:
                parser_name = getattr(parser, "name", "") or "unknown"
                link_key = self.build_link_key(link, parser_name)
                if link_key and (
                    link_key in sent_links or link_key in seen_in_batch
                ):
                    duplicates.append((link, str(parser_name)))
                    continue

                allowed.append((link, parser))
                if link_key:
                    seen_in_batch.add(link_key)

            return allowed, duplicates

    def filter_duplicate_metadata(
        self,
        metadata_list: Iterable[Dict[str, Any]],
        *,
        now: Optional[float] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """过滤已经发送过结果的 metadata；用于解析后识别短链别名。"""
        if not metadata_list:
            return [], 0

        current = int(now or time.time())
        with self._lock:
            self._load()
            self._ensure_record_buckets()
            if self._prune_sent_links(current):
                self._save(current)
            sent_links = self._records["sent_links"]
            filtered: List[Dict[str, Any]] = []
            duplicate_count = 0
            seen_in_batch = set()

            for metadata in metadata_list or []:
                if not isinstance(metadata, dict):
                    filtered.append(metadata)
                    continue

                keys = self.build_metadata_link_keys(metadata)
                if not keys:
                    filtered.append(metadata)
                    continue

                is_duplicate = any(
                    key in sent_links or key in seen_in_batch
                    for key in keys
                )
                if is_duplicate:
                    duplicate_count += 1
                    continue

                filtered.append(metadata)
                seen_in_batch.update(keys)

            return filtered, duplicate_count

    def record_sent_link_metadata(
        self,
        metadata_list: Iterable[Dict[str, Any]],
        *,
        now: Optional[float] = None,
    ) -> int:
        """记录本次已发送过结果的链接，用于后续重复解析时整条跳过。"""
        current = int(now or time.time())
        recorded = 0

        with self._lock:
            self._load()
            self._ensure_record_buckets()
            changed = self._prune_sent_links(current)
            sent_links = self._records["sent_links"]

            for metadata in metadata_list or []:
                if not isinstance(metadata, dict):
                    continue

                keys = self.build_metadata_link_keys(metadata)
                if not keys:
                    continue

                has_new_key = False
                for key in keys:
                    if key in sent_links:
                        continue
                    sent_links[key] = current
                    has_new_key = True
                    changed = True
                if has_new_key:
                    recorded += 1

            changed = self._trim_sent_link_records() or changed
            if changed:
                self._save(current)

        return recorded

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
                "sent_links": (
                    data.get("sent_links")
                    if isinstance(data.get("sent_links"), dict) else
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
            self._records["links"] = {}
            self._records["users"] = {}
            self._records.setdefault("sent_links", {})
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

    def _ensure_record_buckets(self) -> None:
        for bucket in ("links", "users", "sent_links"):
            if not isinstance(self._records.get(bucket), dict):
                self._records[bucket] = {}

    @staticmethod
    def _coerce_timestamp(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _trim_sent_link_records(self) -> bool:
        sent_links = self._records.get("sent_links")
        if not isinstance(sent_links, dict):
            self._records["sent_links"] = {}
            return True

        overflow = len(sent_links) - _SENT_LINK_RECORD_LIMIT
        if overflow <= 0:
            return False

        oldest_keys = sorted(
            sent_links,
            key=lambda key: self._coerce_timestamp(sent_links.get(key)),
        )[:overflow]
        for key in oldest_keys:
            sent_links.pop(key, None)
        return True

    def _prune_sent_links(self, current: int) -> bool:
        if self.sent_link_ttl_seconds <= 0:
            return False

        sent_links = self._records.get("sent_links")
        if not isinstance(sent_links, dict):
            self._records["sent_links"] = {}
            return True

        expired_keys = []
        for key, value in sent_links.items():
            timestamp = self._coerce_timestamp(value)
            if (
                timestamp <= 0 or
                current - timestamp >= self.sent_link_ttl_seconds
            ):
                expired_keys.append(key)
        if not expired_keys:
            return False

        for key in expired_keys:
            sent_links.pop(key, None)
        return True
