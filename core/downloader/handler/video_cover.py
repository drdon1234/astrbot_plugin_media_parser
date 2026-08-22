"""视频封面截取处理器。"""

import asyncio
import os
import socket
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from aiohttp import web

from ...logger import logger
from ...storage import cleanup_file
from ..utils import generate_cache_file_path, strip_media_prefixes
from ..budget import (
    ByteBudget,
    DEFAULT_IMAGE_MAX_BYTES,
    DownloadLimitExceeded,
    resolve_max_bytes,
)
from ..fileio import run_blocking


VIDEO_COVER_TIMEOUT = 45


@asynccontextmanager
async def _media_relay(
    session: aiohttp.ClientSession,
    source_url: str,
    headers: Optional[Dict[str, Any]],
    proxy: str,
    max_bytes: Optional[int],
):
    """通过本地 HTTP 流式中继为 ffmpeg 提供有字节上限的输入。"""
    budget = ByteBudget(resolve_max_bytes(max_bytes, is_video=True))
    route_path = f"/{uuid.uuid4().hex}/media"

    async def relay(request: web.Request) -> web.StreamResponse:
        if request.path != route_path or request.method not in {"GET", "HEAD"}:
            raise web.HTTPNotFound()

        upstream_headers = {
            str(key): str(value)
            for key, value in (headers or {}).items()
            if str(key).lower()
            not in {"host", "content-length", "connection", "accept-encoding"}
        }
        upstream_headers["Accept-Encoding"] = "identity"
        range_header = request.headers.get("Range")
        if range_header:
            upstream_headers["Range"] = range_header

        try:
            response = await session.request(
                request.method,
                source_url,
                headers=upstream_headers,
                proxy=proxy,
                timeout=aiohttp.ClientTimeout(total=VIDEO_COVER_TIMEOUT),
                allow_redirects=True,
            )
            async with response:
                forwarded_headers = {
                    key: value
                    for key, value in response.headers.items()
                    if key.lower()
                    in {
                        "content-type",
                        "content-length",
                        "content-range",
                        "accept-ranges",
                        "etag",
                        "last-modified",
                    }
                }
                if request.method == "HEAD":
                    return web.Response(
                        status=response.status,
                        headers=forwarded_headers,
                    )

                content_type = response.headers.get("Content-Type", "").lower()
                if any(
                    marker in content_type
                    for marker in ("mpegurl", "application/json", "text/html")
                ):
                    raise web.HTTPUnsupportedMediaType(
                        text="视频封面来源必须是直接媒体，不能是播放清单或网页"
                    )

                declared_length = response.headers.get("Content-Length")
                if declared_length:
                    try:
                        if int(declared_length) > budget.limit:
                            raise web.HTTPRequestEntityTooLarge(
                                max_size=budget.limit,
                                actual_size=int(declared_length),
                                text="视频封面来源超过下载硬限制",
                            )
                    except ValueError:
                        pass

                preview = await response.content.read(512)
                if (
                    not range_header or range_header.lower().startswith("bytes=0-")
                ) and preview.lstrip().lower().startswith(
                    (b"#extm3u", b"<!doctype", b"<html", b"{")
                ):
                    raise web.HTTPUnsupportedMediaType(
                        text="视频封面来源不是可安全截帧的直接媒体"
                    )

                try:
                    if preview:
                        await budget.consume(len(preview))
                except DownloadLimitExceeded as exc:
                    raise web.HTTPRequestEntityTooLarge(
                        max_size=budget.limit,
                        actual_size=budget.used + len(preview),
                        text="视频封面来源超过下载硬限制",
                    ) from exc

                downstream = web.StreamResponse(
                    status=response.status,
                    headers=forwarded_headers,
                )
                await downstream.prepare(request)
                try:
                    if preview:
                        await downstream.write(preview)
                    async for chunk in response.content.iter_chunked(256 * 1024):
                        await budget.consume(len(chunk))
                        await downstream.write(chunk)
                    await downstream.write_eof()
                except DownloadLimitExceeded:
                    downstream.force_close()
                    logger.warning(
                        f"视频封面来源超过下载硬限制: {source_url}"
                    )
                return downstream
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise web.HTTPBadGateway(text="视频封面上游请求失败") from exc

    application = web.Application(client_max_size=1024)
    application.router.add_route("*", route_path, relay)
    runner = web.AppRunner(application, access_log=None)
    await runner.setup()
    relay_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    relay_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    relay_socket.bind(("127.0.0.1", 0))
    relay_socket.listen(8)
    relay_socket.setblocking(False)
    port = relay_socket.getsockname()[1]
    site = web.SockSite(runner, relay_socket)
    await site.start()
    try:
        yield f"http://127.0.0.1:{port}{route_path}"
    finally:
        await runner.cleanup()


def _build_ffmpeg_headers(headers: Optional[Dict[str, Any]]) -> str:
    """将 HTTP 头转换成 ffmpeg -headers 可接受的格式。"""
    if not isinstance(headers, dict):
        return ""

    lines = []
    skipped = {"host", "content-length", "connection"}
    for key, value in headers.items():
        name = str(key or "").strip()
        if not name or name.lower() in skipped or value is None:
            continue
        lines.append(f"{name}: {value}")
    return "\r\n".join(lines) + ("\r\n" if lines else "")


async def _terminate_ffmpeg_process(process, label: str) -> None:
    """取消或超时时终止并回收 ffmpeg 子进程。"""
    try:
        if process.returncode is None:
            process.kill()
    except ProcessLookupError:
        pass
    except Exception as e:
        logger.warning(f"终止 ffmpeg 截帧进程失败: {label}, 错误: {e}")
    try:
        await process.communicate()
    except Exception as e:
        logger.warning(f"回收 ffmpeg 截帧进程失败: {label}, 错误: {e}")


async def _run_ffmpeg_cover_extract(
    source_url: str,
    output_path: str,
    headers: Optional[Dict[str, Any]] = None,
    proxy: str = None,
) -> Tuple[bool, str]:
    """执行 ffmpeg 首帧截取。"""
    temp_output = f"{output_path}.part.jpg"
    args = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-protocol_whitelist",
        "http,https,tcp,tls",
    ]
    if source_url.startswith(("http://", "https://")):
        if proxy:
            args.extend(["-http_proxy", proxy])
        if isinstance(headers, dict):
            user_agent = str(headers.get("User-Agent") or "").strip()
            referer = str(headers.get("Referer") or "").strip()
            if user_agent:
                args.extend(["-user_agent", user_agent])
            if referer:
                args.extend(["-referer", referer])
        header_blob = _build_ffmpeg_headers(headers)
        if header_blob:
            args.extend(["-headers", header_blob])

    args.extend(
        [
            "-i",
            source_url,
            "-frames:v",
            "1",
            "-an",
            "-update",
            "1",
            "-fs",
            str(DEFAULT_IMAGE_MAX_BYTES),
            temp_output,
        ]
    )

    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return False, "ffmpeg未找到，无法截取视频封面"
    except Exception as e:
        return False, f"启动ffmpeg截帧失败: {e}"

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=VIDEO_COVER_TIMEOUT,
        )
    except asyncio.TimeoutError:
        await _terminate_ffmpeg_process(process, source_url)
        cleanup_file(temp_output)
        return False, "ffmpeg截取视频封面超时"
    except asyncio.CancelledError:
        await _terminate_ffmpeg_process(process, source_url)
        cleanup_file(temp_output)
        raise

    if process.returncode == 0 and os.path.exists(temp_output):
        try:
            size = await run_blocking(os.path.getsize, temp_output)
            if size > DEFAULT_IMAGE_MAX_BYTES:
                cleanup_file(temp_output)
                return False, "截取的视频封面超过安全大小限制"
            os.replace(temp_output, output_path)
            return True, ""
        except OSError as e:
            cleanup_file(temp_output)
            return False, f"提交视频封面文件失败: {e}"

    detail = (stderr or stdout or b"").decode("utf-8", errors="ignore").strip()
    if detail:
        detail = detail.splitlines()[-1]
    cleanup_file(temp_output)
    return False, detail or f"ffmpeg截帧失败(退出码 {process.returncode})"


async def extract_video_cover_to_cache(
    session: aiohttp.ClientSession,
    video_urls: List[str],
    cache_dir: str,
    media_id: str,
    index: int = 0,
    headers: dict = None,
    proxy: str = None,
    max_bytes: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """从视频候选 URL 中截取首帧并写入图片缓存。"""
    if not cache_dir or not media_id:
        return {
            "file_path": None,
            "size_mb": None,
            "status_code": None,
            "error": "缓存目录不可用，无法截取视频封面",
        }

    candidates = [
        strip_media_prefixes(url)
        for url in (video_urls or [])
        if isinstance(url, str) and strip_media_prefixes(url)
    ]
    if not candidates:
        return {
            "file_path": None,
            "size_mb": None,
            "status_code": None,
            "error": "未找到可截取封面的视频URL",
        }

    last_error = "截取视频封面失败"
    output_path = generate_cache_file_path(
        cache_dir=cache_dir,
        media_id=media_id,
        media_type="image",
        index=index,
        content_type="image/jpeg",
        url="cover.jpg",
    )

    for candidate in candidates:
        cleanup_file(output_path)
        try:
            if candidate.startswith(("http://", "https://")):
                async with _media_relay(
                    session=session,
                    source_url=candidate,
                    headers=headers,
                    proxy=proxy,
                    max_bytes=max_bytes,
                ) as relay_url:
                    success, error = await _run_ffmpeg_cover_extract(
                        source_url=relay_url,
                        output_path=output_path,
                    )
            else:
                success, error = await _run_ffmpeg_cover_extract(
                    source_url=candidate,
                    output_path=output_path,
                    headers=headers,
                    proxy=proxy,
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            success, error = False, str(e)
        if success:
            try:
                size_mb = os.path.getsize(output_path) / (1024 * 1024)
            except OSError:
                size_mb = None
            return {
                "file_path": os.path.normpath(output_path),
                "size_mb": size_mb,
                "status_code": None,
                "error": None,
            }
        last_error = error or last_error
        logger.debug(
            f"截取视频封面失败，尝试下一个候选: {candidate}, 错误: {last_error}"
        )

    cleanup_file(output_path)
    return {
        "file_path": None,
        "size_mb": None,
        "status_code": None,
        "error": last_error,
    }
