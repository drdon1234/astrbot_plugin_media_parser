"""下载管理器，按单个媒体决策 local/direct/skip 并回填元数据。"""
import asyncio
import hashlib
import os
import re
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import aiohttp

from ..constants import Config
from ..logger import logger
from ..storage import cleanup_directory, cleanup_file
from .router import download_media
from .utils import check_cache_dir_available, strip_media_prefixes
from .validator import get_video_size, validate_media_url


class DownloadManager:

    """下载调度器，为每个媒体独立决定本地、直链或跳过。"""

    def __init__(
        self,
        max_video_size_mb: float = 0.0,
        large_video_threshold_mb: float = Config.DEFAULT_LARGE_VIDEO_THRESHOLD_MB,
        cache_dir: str = Config.DEFAULT_CACHE_DIR,
        cache_dir_available: Optional[bool] = None,
        max_concurrent_downloads: int = None
    ):
        self.max_video_size_mb = max_video_size_mb
        self.large_video_threshold_mb = large_video_threshold_mb
        self.cache_dir = cache_dir
        self.cache_dir_available = (
            bool(cache_dir_available)
            if cache_dir_available is not None else
            check_cache_dir_available(cache_dir)
        )
        concurrency = (
            max_concurrent_downloads
            if max_concurrent_downloads is not None else
            Config.DOWNLOAD_MANAGER_MAX_CONCURRENT
        )
        try:
            concurrency = max(1, int(concurrency))
        except (TypeError, ValueError):
            concurrency = Config.DOWNLOAD_MANAGER_MAX_CONCURRENT
        self.max_concurrent_downloads = concurrency
        self._download_semaphore = asyncio.Semaphore(concurrency)

        self._active_tasks: set[asyncio.Task] = set()
        self._shutting_down = False

    # ── 决策辅助 ────────────────────────────────────────

    @staticmethod
    def _normalize_url_groups(value: Any) -> List[List[str]]:
        """将解析器输出标准化为 List[List[str]]。"""
        if not isinstance(value, list):
            return []
        groups: List[List[str]] = []
        for item in value:
            if isinstance(item, list):
                groups.append([u for u in item if isinstance(u, str) and u])
            elif isinstance(item, str) and item:
                groups.append([item])
        return groups

    @staticmethod
    def _is_dash_url(url: str) -> bool:
        return bool(url and url.startswith("dash:"))

    @staticmethod
    def _is_m3u8_url(url: str) -> bool:
        if not url:
            return False
        stripped = strip_media_prefixes(url)
        return url.startswith("m3u8:") or ".m3u8" in stripped.lower()

    def _video_requires_local(
        self,
        url_list: List[str],
        force_download: bool
    ) -> bool:
        if force_download:
            return True
        for url in url_list:
            if self._is_dash_url(url) or self._is_m3u8_url(url):
                return True
        return False

    @staticmethod
    def _effective_force_flags(
        metadata: Dict[str, Any],
        video_count: int
    ) -> List[bool]:
        global_force = bool(metadata.get("video_force_download", False))
        raw_flags = metadata.get("video_force_downloads")
        flags: List[bool] = []
        if isinstance(raw_flags, list):
            for idx in range(video_count):
                if idx < len(raw_flags):
                    flags.append(bool(raw_flags[idx]))
                else:
                    flags.append(global_force)
        else:
            flags = [global_force] * video_count
        return flags

    @staticmethod
    def _proxy_for(
        metadata: Dict[str, Any],
        kind: str,
        proxy_addr: str = None
    ) -> Optional[str]:
        proxy_url = metadata.get("proxy_url") or proxy_addr
        if not proxy_url:
            return None
        if kind == "video" and metadata.get("use_video_proxy", False):
            return proxy_url
        if kind == "image" and metadata.get("use_image_proxy", False):
            return proxy_url
        return None

    @staticmethod
    def _extract_status_code_from_error(error: Any) -> Optional[int]:
        """从下载错误文本中提取 HTTP 状态码。"""
        if not error:
            return None
        match = re.search(r"\b([1-5]\d{2})\b", str(error))
        if not match:
            return None
        try:
            return int(match.group(1))
        except (TypeError, ValueError):
            return None

    async def _precheck_video(
        self,
        session: aiohttp.ClientSession,
        url_list: List[str],
        metadata: Dict[str, Any],
        proxy_addr: str = None,
        require_accessible_for_direct: bool = False
    ) -> Tuple[Optional[float], Optional[int], Optional[str], bool]:
        """预检普通视频大小与可访问性。

        Returns:
            (size_mb, status_code, skip_reason, access_denied)
        """
        if not url_list:
            return None, None, "未找到视频URL", False

        headers = metadata.get("video_headers", {})
        proxy = self._proxy_for(metadata, "video", proxy_addr)

        last_status_code = None
        denied_seen = False
        size_limit_reason = None
        size_limit_value = None
        invalid_reason = "直链不可访问或不是有效视频"

        for candidate_index, candidate in enumerate(list(url_list)):
            url = strip_media_prefixes(candidate)
            if not url:
                continue

            size_mb, status_code = await get_video_size(
                session, url, headers=headers, proxy=proxy
            )
            if status_code is not None:
                last_status_code = status_code
            if status_code == 403:
                denied_seen = True
                continue
            if (
                size_mb is not None and
                self.max_video_size_mb > 0 and
                size_mb > self.max_video_size_mb
            ):
                size_limit_value = size_mb
                size_limit_reason = (
                    f"视频大小超过限制（{size_mb:.1f}MB > "
                    f"{self.max_video_size_mb:.1f}MB）"
                )
                continue

            if require_accessible_for_direct and size_mb is None:
                is_valid, validate_status = await validate_media_url(
                    session,
                    url,
                    headers=headers,
                    proxy=proxy,
                    is_video=True
                )
                if validate_status is not None:
                    last_status_code = validate_status
                if validate_status == 403:
                    denied_seen = True
                    continue
                if not is_valid:
                    continue
                status_code = validate_status

            if candidate_index != 0:
                url_list.insert(0, url_list.pop(candidate_index))
            return size_mb, status_code, None, False

        if denied_seen:
            return None, last_status_code, "媒体访问被拒绝(403 Forbidden)", True
        if size_limit_reason:
            return (
                size_limit_value,
                last_status_code,
                size_limit_reason,
                False
            )
        return None, last_status_code, invalid_reason, False

    # ── 下载执行 ────────────────────────────────────────

    async def _download_local_items(
        self,
        session: aiohttp.ClientSession,
        media_items: List[Dict[str, Any]],
        cache_dir: str
    ) -> List[Dict[str, Any]]:
        """并发下载需要写入缓存的媒体项。"""
        if not media_items or not cache_dir or self._shutting_down:
            return []

        async def download_one(item: Dict[str, Any]) -> Dict[str, Any]:
            async with self._download_semaphore:
                url_list = item.get("url_list") or []
                index = int(item.get("index", 0))
                kind = item.get("kind", "video")
                media_id = item.get("media_id") or "media"
                headers = item.get("headers") or {}
                proxy = item.get("proxy")

                if not url_list:
                    return {
                        **item,
                        "success": False,
                        "file_path": None,
                        "size_mb": None,
                        "error": "未找到媒体URL",
                    }

                last_error = "下载失败"
                last_status_code = None
                for candidate in url_list:
                    try:
                        result = await download_media(
                            session=session,
                            media_url=candidate,
                            media_type="image" if kind == "image" else None,
                            cache_dir=cache_dir,
                            media_id=media_id,
                            index=index,
                            headers=headers,
                            proxy=proxy,
                        )
                        if result and result.get("file_path"):
                            return {
                                **item,
                                "url": candidate,
                                "file_path": result.get("file_path"),
                                "size_mb": result.get("size_mb"),
                                "status_code": (
                                    result.get("status_code")
                                    or last_status_code
                                ),
                                "success": True,
                            }
                        if result and result.get("error"):
                            last_error = str(result.get("error"))
                            last_status_code = (
                                result.get("status_code")
                                or self._extract_status_code_from_error(
                                    last_error
                                )
                                or last_status_code
                            )
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        last_error = str(e)
                        last_status_code = (
                            self._extract_status_code_from_error(last_error)
                            or last_status_code
                        )
                        logger.warning(
                            f"下载媒体失败: {candidate}, 错误: {e}"
                        )

                return {
                    **item,
                    "url": url_list[0],
                    "file_path": None,
                    "size_mb": None,
                    "status_code": last_status_code,
                    "success": False,
                    "error": last_error,
                }

        tasks = [asyncio.create_task(download_one(item)) for item in media_items]
        self._active_tasks.update(tasks)
        try:
            raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            for task in tasks:
                self._active_tasks.discard(task)

        results: List[Dict[str, Any]] = []
        for idx, result in enumerate(raw_results):
            item = media_items[idx] if idx < len(media_items) else {}
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, Exception):
                results.append({
                    **item,
                    "success": False,
                    "file_path": None,
                    "size_mb": None,
                    "status_code": self._extract_status_code_from_error(
                        str(result)
                    ),
                    "error": str(result),
                })
            elif isinstance(result, dict):
                results.append(result)
        return results

    # ── 主入口 ──────────────────────────────────────────

    async def process_metadata(
        self,
        session: aiohttp.ClientSession,
        metadata: Dict[str, Any],
        proxy_addr: str = None,
        on_sendable_media: Optional[Callable[[], Awaitable[None]]] = None
    ) -> Dict[str, Any]:
        """处理元数据，回填媒体模式、本地文件、大小和跳过原因。"""
        if self._shutting_down or not metadata:
            return metadata

        url = metadata.get("url", "")
        video_urls = self._normalize_url_groups(metadata.get("video_urls", []))
        image_urls = self._normalize_url_groups(metadata.get("image_urls", []))
        metadata["video_urls"] = video_urls
        metadata["image_urls"] = image_urls
        metadata.setdefault("video_headers", {})
        metadata.setdefault("image_headers", {})

        video_count = len(video_urls)
        image_count = len(image_urls)
        file_paths: List[Optional[str]] = [None] * (video_count + image_count)
        video_sizes: List[Optional[float]] = [None] * video_count
        video_status_codes: List[Optional[int]] = [None] * video_count
        image_status_codes: List[Optional[int]] = [None] * image_count
        video_modes: List[str] = ["skip"] * video_count
        image_modes: List[str] = ["skip"] * image_count
        video_skip_reasons: List[Optional[str]] = [None] * video_count
        image_skip_reasons: List[Optional[str]] = [None] * image_count
        has_access_denied = False
        size_exceeded = False

        force_flags = self._effective_force_flags(metadata, video_count)
        media_id = self._generate_media_id(url, metadata)
        local_items: List[Dict[str, Any]] = []

        logger.debug(
            f"处理元数据: {url}, 视频={video_count}, 图片={image_count}, "
            f"缓存目录可用={self.cache_dir_available}"
        )

        for idx, url_list in enumerate(video_urls):
            force_download = force_flags[idx] if idx < len(force_flags) else False
            requires_local = self._video_requires_local(url_list, force_download)
            contains_stream = any(
                self._is_dash_url(u) or self._is_m3u8_url(u)
                for u in url_list
            )

            if not url_list:
                video_skip_reasons[idx] = "未找到视频URL"
                continue

            if requires_local and not self.cache_dir_available:
                video_skip_reasons[idx] = (
                    "媒体文件缓存目录不可用，无法处理必须下载到缓存的视频"
                )
                continue

            mode = "local" if self.cache_dir_available else "direct"
            if requires_local:
                mode = "local"

            if not contains_stream:
                size_mb, status_code, reason, denied = await self._precheck_video(
                    session=session,
                    url_list=url_list,
                    metadata=metadata,
                    proxy_addr=proxy_addr,
                    require_accessible_for_direct=(mode == "direct")
                )
                video_sizes[idx] = size_mb
                video_status_codes[idx] = status_code
                has_access_denied = has_access_denied or denied
                if reason:
                    if "超过限制" in reason:
                        size_exceeded = True
                    video_skip_reasons[idx] = reason
                    continue

            video_modes[idx] = mode
            if on_sendable_media:
                await on_sendable_media()
            if mode == "local":
                local_items.append({
                    "kind": "video",
                    "position": idx,
                    "index": idx,
                    "url_list": url_list,
                    "media_id": media_id,
                    "headers": metadata.get("video_headers", {}),
                    "proxy": self._proxy_for(metadata, "video", proxy_addr),
                })

        for idx, url_list in enumerate(image_urls):
            if not url_list:
                image_skip_reasons[idx] = "未找到图片URL"
                continue
            if not self.cache_dir_available:
                image_skip_reasons[idx] = (
                    "媒体文件缓存目录不可用，图片无法直链发送"
                )
                continue
            image_modes[idx] = "local"
            if on_sendable_media:
                await on_sendable_media()
            local_items.append({
                "kind": "image",
                "position": video_count + idx,
                "index": idx,
                "url_list": url_list,
                "media_id": media_id,
                "headers": metadata.get("image_headers", {}),
                "proxy": self._proxy_for(metadata, "image", proxy_addr),
            })

        download_results = await self._download_local_items(
            session=session,
            media_items=local_items,
            cache_dir=self.cache_dir
        )

        for result in download_results:
            kind = result.get("kind")
            position = int(result.get("position", 0))
            status_code = result.get("status_code")
            success = bool(result.get("success") and result.get("file_path"))
            if not success:
                reason = result.get("error") or "缓存下载失败"
                if kind == "video":
                    idx = position
                    if status_code is not None:
                        video_status_codes[idx] = status_code
                    video_modes[idx] = "skip"
                    video_skip_reasons[idx] = f"缓存下载失败: {reason}"
                else:
                    idx = position - video_count
                    if status_code is not None:
                        image_status_codes[idx] = status_code
                    image_modes[idx] = "skip"
                    image_skip_reasons[idx] = f"缓存下载失败: {reason}"
                continue

            file_path = result.get("file_path")
            size_mb = result.get("size_mb")
            if kind == "video":
                idx = position
                if status_code is not None:
                    video_status_codes[idx] = status_code
                if size_mb is not None:
                    video_sizes[idx] = size_mb
                if (
                    size_mb is not None and
                    self.max_video_size_mb > 0 and
                    size_mb > self.max_video_size_mb
                ):
                    cleanup_file(file_path)
                    file_paths[position] = None
                    video_modes[idx] = "skip"
                    video_skip_reasons[idx] = (
                        f"下载后视频大小超过限制（{size_mb:.1f}MB > "
                        f"{self.max_video_size_mb:.1f}MB）"
                    )
                    size_exceeded = True
                    continue
            else:
                idx = position - video_count
                if status_code is not None:
                    image_status_codes[idx] = status_code
            file_paths[position] = file_path

        valid_video_count = sum(1 for mode in video_modes if mode in ("local", "direct"))
        valid_image_count = sum(1 for mode in image_modes if mode in ("local", "direct"))
        has_valid_media = bool(valid_video_count or valid_image_count)

        if not has_valid_media and self.cache_dir:
            cleanup_directory(os.path.join(self.cache_dir, media_id))

        valid_sizes = [s for s in video_sizes if s is not None]
        metadata["file_paths"] = file_paths
        metadata["video_sizes"] = video_sizes
        metadata["video_status_codes"] = video_status_codes
        metadata["image_status_codes"] = image_status_codes
        metadata["video_modes"] = video_modes
        metadata["image_modes"] = image_modes
        metadata["video_skip_reasons"] = video_skip_reasons
        metadata["image_skip_reasons"] = image_skip_reasons
        metadata["media_cache_dir_available"] = self.cache_dir_available
        metadata["max_video_size_mb"] = max(valid_sizes) if valid_sizes else None
        metadata["total_video_size_mb"] = sum(valid_sizes) if valid_sizes else 0.0
        metadata["video_count"] = video_count
        metadata["image_count"] = image_count
        metadata["has_valid_media"] = has_valid_media
        metadata["use_local_files"] = any(
            mode == "local" and idx < len(file_paths) and file_paths[idx]
            for idx, mode in enumerate(video_modes)
        ) or any(
            mode == "local" and (video_count + idx) < len(file_paths)
            and file_paths[video_count + idx]
            for idx, mode in enumerate(image_modes)
        )
        metadata["exceeds_max_size"] = bool(size_exceeded and not has_valid_media)
        metadata["has_access_denied"] = bool(
            has_access_denied or
            any(code == 403 for code in video_status_codes) or
            any(code == 403 for code in image_status_codes)
        )
        metadata["failed_video_count"] = sum(1 for mode in video_modes if mode == "skip")
        metadata["failed_image_count"] = sum(1 for mode in image_modes if mode == "skip")
        return metadata

    def _generate_media_id(
        self,
        url: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        platform = "unknown"
        if metadata and metadata.get("platform"):
            platform = str(metadata.get("platform"))
        url_hash = hashlib.md5((url or "").encode()).hexdigest()[:8]
        timestamp = int(time.time())
        nonce = uuid.uuid4().hex[:8]
        return f"{platform}_{url_hash}_{timestamp}_{nonce}"

    async def shutdown(self):
        """取消所有活动下载任务。"""
        self._shutting_down = True
        tasks = list(self._active_tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._active_tasks.clear()
