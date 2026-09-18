"""下载字节预算。

预算在响应流写入磁盘前扣减，确保服务端缺少或伪造 Content-Length 时仍会
在硬上限处中止。多个 HLS/DASH 子资源可以共享同一个实例。
"""

import asyncio
from typing import Literal, Optional


# 未配置业务大小限制时仍保留防资源耗尽的安全上限。该上限不是产品层的
# “大文件”阈值，而是下载器不可绕过的最后一道保护。
DEFAULT_VIDEO_MAX_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_IMAGE_MAX_BYTES = 64 * 1024 * 1024
# 限制单帧解码规模，避免体积很小但声明超大画布的压缩图片耗尽内存。
MAX_IMAGE_PIXELS = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_HLS_INIT_BYTES = 64 * 1024 * 1024
MAX_HLS_SEGMENTS = 10_000


DownloadLimitSource = Literal["configured", "safety", "both"]


class DownloadLimitExceeded(RuntimeError):
    """下载内容超过硬字节上限。"""

    def __init__(
        self,
        message: str,
        *,
        limit_source: DownloadLimitSource = "safety",
        observed_bytes: Optional[int] = None,
    ):
        super().__init__(message)
        self.limit_source = limit_source
        self.observed_bytes = observed_bytes


def _normalize_configured_limit(max_bytes: Optional[int]) -> Optional[int]:
    """把用户配置规范为正整数字节数。"""
    try:
        configured = int(max_bytes) if max_bytes is not None else 0
    except (TypeError, ValueError):
        return None
    return configured if configured > 0 else None


def classify_limit_source(
    max_bytes: Optional[int],
    *,
    is_video: bool,
    observed_bytes: Optional[int] = None,
) -> DownloadLimitSource:
    """判定本次超限来自用户配置、安全上限或二者。"""
    safety_limit = DEFAULT_VIDEO_MAX_BYTES if is_video else DEFAULT_IMAGE_MAX_BYTES
    configured = _normalize_configured_limit(max_bytes)
    if observed_bytes is not None:
        configured_exceeded = bool(
            configured is not None and observed_bytes > configured
        )
        safety_exceeded = observed_bytes > safety_limit
        if configured_exceeded and safety_exceeded:
            return "both"
        if configured_exceeded:
            return "configured"
        return "safety"
    if configured is not None and configured <= safety_limit:
        return "configured"
    return "safety"


def is_configured_limit_source(source: Optional[str]) -> bool:
    """判断限额来源是否包含用户配置上限。"""
    return source in {"configured", "both"}


def merge_limit_sources(
    *sources: Optional[str],
) -> Optional[DownloadLimitSource]:
    """合并多个候选或并发子流的限额来源。"""
    valid_sources = {
        source for source in sources if source in {"configured", "safety", "both"}
    }
    if "both" in valid_sources or len(valid_sources) > 1:
        return "both"
    if valid_sources:
        return next(iter(valid_sources))
    return None


def resolve_max_bytes(
    max_bytes: Optional[int],
    *,
    is_video: bool,
) -> int:
    """将可选业务限制转换为始终有效的正整数硬限制。"""
    safety_limit = DEFAULT_VIDEO_MAX_BYTES if is_video else DEFAULT_IMAGE_MAX_BYTES
    configured = _normalize_configured_limit(max_bytes)
    if configured is None:
        return safety_limit
    return min(configured, safety_limit)


class ByteBudget:
    """可由并发下载任务共享的原子字节预算。"""

    def __init__(
        self,
        limit: int,
        *,
        limit_source: DownloadLimitSource = "safety",
    ):
        self.limit = max(1, int(limit))
        self.limit_source = limit_source
        self.used = 0
        self._lock = asyncio.Lock()

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    async def consume(self, amount: int) -> None:
        """预留 ``amount`` 字节；超限时不改变预算并抛出异常。"""
        amount = max(0, int(amount))
        async with self._lock:
            new_total = self.used + amount
            if new_total > self.limit:
                raise DownloadLimitExceeded(
                    "下载内容超过硬限制"
                    f"（{new_total / 1024 / 1024:.1f}MB > "
                    f"{self.limit / 1024 / 1024:.1f}MB）",
                    limit_source=self.limit_source,
                )
            self.used = new_total

    async def release(self, amount: int) -> None:
        """失败文件被删除时返还其已占用预算。"""
        amount = max(0, int(amount))
        async with self._lock:
            self.used = max(0, self.used - amount)


def create_byte_budget(
    max_bytes: Optional[int],
    *,
    is_video: bool,
) -> ByteBudget:
    """按用户配置和内置安全阈值创建字节预算。"""
    return ByteBudget(
        resolve_max_bytes(max_bytes, is_video=is_video),
        limit_source=classify_limit_source(max_bytes, is_video=is_video),
    )
