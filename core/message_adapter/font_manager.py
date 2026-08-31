"""字体资源管理模块，负责运行时校验并补全图片渲染字体。"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

import aiohttp


FONT_DIR = Path(__file__).resolve().parents[2] / "resource" / "font"
FONT_RELEASE_BASE_URL = (
    "https://github.com/drdon1234/fonts/releases/download/v1.0.0"
)
FONT_DOWNLOAD_CHUNK_SIZE = 1024 * 1024
FONT_DOWNLOAD_TIMEOUT_SECONDS = 180


class FontDownloadError(RuntimeError):
    """字体资源下载或校验失败。"""


@dataclass(frozen=True)
class FontAsset:
    """远程字体资源描述。"""

    filename: str
    size: int
    sha256: str

    @property
    def url(self) -> str:
        """返回固定版本的 Release 资源地址。"""
        return f"{FONT_RELEASE_BASE_URL}/{self.filename}"


FONT_ASSETS = (
    FontAsset(
        filename="NotoSansCJKsc-Regular.otf",
        size=16437364,
        sha256=(
            "2c76254f6fc379fddfce0a7e84fb5385"
            "bb135d3e399294f6eeb6680d0365b74b"
        ),
    ),
    FontAsset(
        filename="NotoSansCJKsc-Bold.otf",
        size=17002248,
        sha256=(
            "b5f0d1a190a7f9b43c310a8850630af"
            "12553df32c4c050543f9059732d9b4c0a"
        ),
    ),
)

_ensure_lock = asyncio.Lock()
_fonts_ready = False


async def ensure_default_fonts() -> None:
    """校验并补全默认 Noto Sans CJK 字体。"""
    global _fonts_ready

    if _fonts_ready and _all_assets_have_expected_size():
        return

    async with _ensure_lock:
        if _fonts_ready and _all_assets_have_expected_size():
            return

        _fonts_ready = False
        if await asyncio.to_thread(_all_assets_are_valid):
            _fonts_ready = True
            return

        try:
            FONT_DIR.mkdir(parents=True, exist_ok=True)
            timeout = aiohttp.ClientTimeout(total=FONT_DOWNLOAD_TIMEOUT_SECONDS)
            headers = {"User-Agent": "AstrBot-Media-Parser-Font-Downloader"}
            async with aiohttp.ClientSession(
                timeout=timeout,
                headers=headers,
            ) as session:
                for asset in FONT_ASSETS:
                    target = FONT_DIR / asset.filename
                    if await asyncio.to_thread(_font_is_valid, target, asset):
                        continue
                    await _download_font(session, asset, target)
        except asyncio.CancelledError:
            _fonts_ready = False
            raise
        except FontDownloadError:
            _fonts_ready = False
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            _fonts_ready = False
            raise FontDownloadError(f"补全字体资源失败: {exc}") from exc

        if not await asyncio.to_thread(_all_assets_are_valid):
            raise FontDownloadError("字体资源补全后校验失败")
        _fonts_ready = True


async def _download_font(
    session: aiohttp.ClientSession,
    asset: FontAsset,
    target: Path,
) -> None:
    """下载并原子替换单个字体文件。"""
    temp_path = target.with_name(f"{target.name}.{uuid.uuid4().hex}.part")
    received_size = 0
    digest = hashlib.sha256()
    try:
        async with session.get(asset.url) as response:
            if response.status != 200:
                raise FontDownloadError(
                    f"下载字体 {asset.filename} 失败: HTTP {response.status}"
                )
            with temp_path.open("xb") as output:
                async for chunk in response.content.iter_chunked(
                    FONT_DOWNLOAD_CHUNK_SIZE
                ):
                    if chunk:
                        received_size += len(chunk)
                        if received_size > asset.size:
                            raise FontDownloadError(
                                f"字体 {asset.filename} 大小超出预期: "
                                f"已接收 {received_size} 字节，预期 {asset.size} 字节"
                            )
                        output.write(chunk)
                        digest.update(chunk)

        actual_sha256 = digest.hexdigest()
        if received_size != asset.size or actual_sha256 != asset.sha256:
            raise FontDownloadError(
                f"字体 {asset.filename} 校验失败: "
                f"已接收 {received_size} 字节，预期 {asset.size} 字节，"
                f"SHA256 {actual_sha256}，预期 {asset.sha256}"
            )
        os.replace(temp_path, target)
    except asyncio.CancelledError:
        raise
    except FontDownloadError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
        raise FontDownloadError(f"下载字体 {asset.filename} 失败: {exc}") from exc
    finally:
        _remove_temp_file(temp_path)


def _all_assets_have_expected_size() -> bool:
    """快速确认全部字体仍存在且大小正确。"""
    return all(
        _font_has_expected_size(FONT_DIR / asset.filename, asset)
        for asset in FONT_ASSETS
    )


def _all_assets_are_valid() -> bool:
    """完整校验全部字体资源。"""
    return all(
        _font_is_valid(FONT_DIR / asset.filename, asset) for asset in FONT_ASSETS
    )


def _font_has_expected_size(path: Path, asset: FontAsset) -> bool:
    """确认字体文件存在且大小符合预期。"""
    try:
        return path.is_file() and path.stat().st_size == asset.size
    except OSError:
        return False


def _font_is_valid(path: Path, asset: FontAsset) -> bool:
    """校验字体文件大小和 SHA256。"""
    if not _font_has_expected_size(path, asset):
        return False
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(FONT_DOWNLOAD_CHUNK_SIZE), b""):
                digest.update(chunk)
    except OSError:
        return False
    return digest.hexdigest() == asset.sha256


def _remove_temp_file(path: Path) -> None:
    """尽力清理下载临时文件。"""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
