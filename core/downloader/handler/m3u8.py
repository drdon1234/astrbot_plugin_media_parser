"""M3U8 下载处理器，负责索引解析、分片下载与拼接。"""

import asyncio
import os
import re
import shutil
import tempfile
import uuid
from typing import Dict, Any, List, Optional, Tuple
from urllib.parse import urljoin

import aiohttp

from ...logger import logger

from ...storage import cleanup_directory, cleanup_file, stamp_subdir
from ...constants import Config
from ..budget import (
    ByteBudget,
    MAX_HLS_INIT_BYTES,
    MAX_HLS_SEGMENTS,
    MAX_MANIFEST_BYTES,
    resolve_max_bytes,
)
from ..fileio import gather_cancel_on_error, run_blocking
from .base import (
    _format_download_error,
    _is_retryable_exception,
    _sleep_before_retry,
)


class M3U8DownloadError(RuntimeError):
    """M3U8 下载失败，携带可回填的 HTTP 状态码。"""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


def _status_code_from_exception(exc: BaseException) -> Optional[int]:
    """从 aiohttp 异常中提取 HTTP 状态码。"""
    if isinstance(exc, aiohttp.ClientResponseError):
        return exc.status
    return None


def _extract_uri_attribute(line: str) -> Optional[str]:
    """提取 M3U8 标签中的 URI 属性，兼容带引号和不带引号格式。"""
    match = re.search(r'URI=(?:"([^"]+)"|([^,\s]+))', line, re.IGNORECASE)
    if not match:
        return None
    return (match.group(1) or match.group(2) or "").strip()


async def _terminate_process(process, label: str) -> None:
    """终止并回收子进程，避免取消或超时后遗留后台进程。"""
    try:
        if process.returncode is None:
            process.kill()
    except ProcessLookupError:
        pass
    except Exception as e:
        logger.warning(f"{label} 进程终止失败: {e}")
    try:
        await process.communicate()
    except Exception as e:
        logger.warning(f"{label} 进程回收失败: {e}")


async def _communicate_process(process, label: str):
    """等待子进程完成，超时或取消时确保回收。"""
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=Config.VIDEO_DOWNLOAD_TIMEOUT
        )
        return stdout, stderr, False
    except asyncio.TimeoutError:
        await _terminate_process(process, label)
        logger.warning(f"{label} 超时")
        return b"", b"", True
    except asyncio.CancelledError:
        await _terminate_process(process, label)
        raise


class M3U8Handler:
    """M3U8 下载处理器，负责分片任务调度与结果合并。"""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        headers: dict = None,
        proxy: str = None,
        max_concurrent_segments: int = Config.M3U8_MAX_CONCURRENT_SEGMENTS,
    ):
        """初始化 M3U8 处理器

        Args:
            session: aiohttp 会话
            headers: 请求头（可选）
            proxy: 代理地址（可选）
            max_concurrent_segments: 最大并发下载分片数
        """
        self.session = session
        self.headers = headers or {}
        self.proxy = proxy
        self.max_concurrent_segments = max_concurrent_segments

    async def fetch_text(self, url: str) -> str:
        """获取文本内容

        Args:
            url: URL地址

        Returns:
            文本内容
        """
        attempts = Config.DOWNLOAD_RETRY_ATTEMPTS
        for attempt in range(1, attempts + 1):
            try:
                response = await self.session.get(
                    url,
                    headers=self.headers,
                    proxy=self.proxy,
                    timeout=aiohttp.ClientTimeout(
                        total=Config.VIDEO_SIZE_CHECK_TIMEOUT
                    ),
                    allow_redirects=True,
                )
                async with response:
                    response.raise_for_status()
                    declared = response.headers.get("Content-Length")
                    if declared:
                        try:
                            if int(declared) > MAX_MANIFEST_BYTES:
                                raise M3U8DownloadError("M3U8清单超过安全大小限制")
                        except ValueError:
                            pass
                    chunks = []
                    total = 0
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        total += len(chunk)
                        if total > MAX_MANIFEST_BYTES:
                            raise M3U8DownloadError("M3U8清单超过安全大小限制")
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    charset = response.charset or "utf-8"
                    return raw.decode(charset, errors="replace")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if attempt < attempts and _is_retryable_exception(e):
                    await _sleep_before_retry(attempt)
                    continue
                raise M3U8DownloadError(
                    _format_download_error(e), _status_code_from_exception(e)
                ) from e

    async def fetch_bytes(
        self,
        url: str,
        budget: Optional[ByteBudget] = None,
    ) -> bytes:
        """获取二进制内容

        Args:
            url: URL地址

        Returns:
            二进制内容
        """
        attempts = Config.DOWNLOAD_RETRY_ATTEMPTS
        active_budget = budget or ByteBudget(resolve_max_bytes(None, is_video=True))
        for attempt in range(1, attempts + 1):
            consumed = 0
            try:
                response = await self.session.get(
                    url,
                    headers=self.headers,
                    proxy=self.proxy,
                    timeout=aiohttp.ClientTimeout(total=Config.VIDEO_DOWNLOAD_TIMEOUT),
                    allow_redirects=True,
                )
                async with response:
                    response.raise_for_status()
                    chunks = []
                    async for chunk in response.content.iter_chunked(
                        Config.STREAM_DOWNLOAD_CHUNK_SIZE
                    ):
                        await active_budget.consume(len(chunk))
                        consumed += len(chunk)
                        if consumed > MAX_HLS_INIT_BYTES:
                            raise M3U8DownloadError("M3U8初始化分片超过安全大小限制")
                        chunks.append(chunk)
                    return b"".join(chunks)
            except asyncio.CancelledError:
                await active_budget.release(consumed)
                raise
            except Exception as e:
                await active_budget.release(consumed)
                if attempt < attempts and _is_retryable_exception(e):
                    await _sleep_before_retry(attempt)
                    continue
                raise M3U8DownloadError(
                    _format_download_error(e), _status_code_from_exception(e)
                ) from e

    async def download_file(
        self,
        url: str,
        output_path: str,
        budget: Optional[ByteBudget] = None,
    ) -> None:
        """下载文件

        Args:
            url: 文件URL
            output_path: 输出路径

        Returns:
            失败时抛出 M3U8DownloadError
        """
        attempts = Config.DOWNLOAD_RETRY_ATTEMPTS
        active_budget = budget or ByteBudget(resolve_max_bytes(None, is_video=True))
        for attempt in range(1, attempts + 1):
            temp_path = f"{output_path}.{uuid.uuid4().hex}.part"
            consumed = 0
            output_file = None
            try:
                await run_blocking(
                    os.makedirs, os.path.dirname(output_path), exist_ok=True
                )
                response = await self.session.get(
                    url,
                    headers=self.headers,
                    proxy=self.proxy,
                    timeout=aiohttp.ClientTimeout(total=Config.VIDEO_DOWNLOAD_TIMEOUT),
                    allow_redirects=True,
                )
                async with response:
                    response.raise_for_status()
                    output_file = await run_blocking(open, temp_path, "wb")
                    async for chunk in response.content.iter_chunked(
                        Config.STREAM_DOWNLOAD_CHUNK_SIZE
                    ):
                        await active_budget.consume(len(chunk))
                        consumed += len(chunk)
                        await run_blocking(output_file.write, chunk)
                    await run_blocking(output_file.flush)
                    await run_blocking(os.fsync, output_file.fileno())
                    await run_blocking(output_file.close)
                    output_file = None
                    os.replace(temp_path, output_path)
                return
            except asyncio.CancelledError:
                if output_file is not None:
                    await run_blocking(output_file.close)
                cleanup_file(temp_path)
                await active_budget.release(consumed)
                raise
            except Exception as e:
                if output_file is not None:
                    try:
                        await run_blocking(output_file.close)
                    except Exception:
                        pass
                cleanup_file(temp_path)
                await active_budget.release(consumed)
                if attempt < attempts and _is_retryable_exception(e):
                    await _sleep_before_retry(attempt)
                    continue
                error_text = _format_download_error(e)
                logger.warning(f"下载文件失败 {url}: {error_text}")
                raise M3U8DownloadError(
                    error_text, _status_code_from_exception(e)
                ) from e

    async def parse_m3u8(self, url: str) -> Tuple[Optional[str], List[str]]:
        """解析 m3u8 获取 init segment 和分片列表

        Args:
            url: m3u8 URL

        Returns:
            (init_seg_url, segments) 元组，init_seg_url 为 init segment URL（如果有），
            segments 为分片 URL 列表
        """
        content = await self.fetch_text(url)
        init_seg = None
        segments = []

        for line in content.split("\n"):
            line = line.strip()
            upper_line = line.upper()
            if upper_line.startswith("#EXT-X-KEY"):
                if "METHOD=NONE" not in upper_line:
                    raise M3U8DownloadError("加密M3U8暂不支持")
                continue
            if upper_line.startswith("#EXT-X-BYTERANGE") or (
                upper_line.startswith("#EXT-X-MAP") and "BYTERANGE=" in upper_line
            ):
                raise M3U8DownloadError(
                    "M3U8使用EXT-X-BYTERANGE，当前为避免生成损坏文件已拒绝下载"
                )
            if upper_line.startswith("#EXT-X-MAP"):
                uri = _extract_uri_attribute(line)
                if uri:
                    init_seg = uri
            elif line and not line.startswith("#"):
                segments.append(line)

        if init_seg:
            init_seg = urljoin(url, init_seg)
        segments = [urljoin(url, segment) for segment in segments]
        return init_seg, segments

    async def download_segments(
        self,
        segments: List[str],
        output_dir: str,
        prefix: str = "seg",
        budget: Optional[ByteBudget] = None,
    ) -> List[str]:
        """并发下载所有分片

        Args:
            segments: 分片 URL 列表
            output_dir: 输出目录
            prefix: 文件前缀

        Returns:
            全部分片成功下载后的文件路径列表（已排序）
        """
        if not segments:
            raise M3U8DownloadError("M3U8未找到任何分片")
        if len(segments) > MAX_HLS_SEGMENTS:
            raise M3U8DownloadError(
                f"M3U8分片数量超过安全限制（{len(segments)} > {MAX_HLS_SEGMENTS}）"
            )

        os.makedirs(output_dir, exist_ok=True)

        async def download_segment(i: int, url: str) -> Optional[str]:
            """下载单个分片"""
            path = os.path.join(output_dir, f"{prefix}_{i:05d}.m4s")
            await self.download_file(url, path, budget=budget)
            return path

        semaphore = asyncio.Semaphore(self.max_concurrent_segments)

        async def download_with_limit(i: int, url: str) -> Optional[str]:
            """在并发信号量限制下下载单个分片。"""
            async with semaphore:
                return await download_segment(i, url)

        try:
            files = await gather_cancel_on_error(
                *(
                    download_with_limit(index, segment_url)
                    for index, segment_url in enumerate(segments)
                )
            )
        except M3U8DownloadError:
            raise
        except Exception as exc:
            raise M3U8DownloadError(
                str(exc) or type(exc).__name__,
                _status_code_from_exception(exc),
            ) from exc
        return sorted(files)

    async def merge_segments(
        self,
        init_seg: Optional[str],
        files: List[str],
        output: str,
        budget: Optional[ByteBudget] = None,
    ) -> bool:
        """合并分片

        Args:
            init_seg: init segment URL（可选）
            files: 分片文件路径列表
            output: 输出文件路径

        Returns:
            合并是否成功
        """
        try:
            if not files:
                raise M3U8DownloadError("M3U8无可合并分片")
            init_data = (
                await self.fetch_bytes(init_seg, budget=budget) if init_seg else b""
            )
            temp_output = f"{output}.{uuid.uuid4().hex}.part"

            def concatenate() -> None:
                with open(temp_output, "wb") as out:
                    if init_data:
                        out.write(init_data)
                    for file_path in files:
                        with open(file_path, "rb") as inp:
                            shutil.copyfileobj(inp, out, length=1024 * 1024)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(temp_output, output)

            try:
                await run_blocking(concatenate)
            finally:
                cleanup_file(temp_output)
            return True
        except asyncio.CancelledError:
            raise
        except M3U8DownloadError:
            raise
        except Exception as e:
            logger.warning(f"合并分片失败: {e}")
            return False

    async def parse_master_m3u8(
        self, m3u8_url: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """解析主 m3u8 文件，获取视频和音频 m3u8 URL

        Args:
            m3u8_url: 主 m3u8 URL

        Returns:
            (video_m3u8_url, audio_m3u8_url) 元组
        """
        master = await self.fetch_text(m3u8_url)
        lines = [line.strip() for line in master.splitlines()]
        audio_groups: Dict[str, List[Tuple[bool, str]]] = {}
        variants: List[Tuple[int, int, str, Optional[str]]] = []

        for line in lines:
            upper = line.upper()
            if not upper.startswith("#EXT-X-MEDIA") or "TYPE=AUDIO" not in upper:
                continue
            uri = _extract_uri_attribute(line)
            group_match = re.search(
                r'GROUP-ID=(?:"([^"]+)"|([^,\s]+))',
                line,
                re.IGNORECASE,
            )
            if not uri or not group_match:
                continue
            group = group_match.group(1) or group_match.group(2)
            is_default = bool(re.search(r"DEFAULT=YES", line, re.IGNORECASE))
            audio_groups.setdefault(group, []).append((is_default, uri))

        for index, line in enumerate(lines):
            if not line.upper().startswith("#EXT-X-STREAM-INF"):
                continue
            uri = next(
                (
                    candidate
                    for candidate in lines[index + 1 :]
                    if candidate and not candidate.startswith("#")
                ),
                None,
            )
            if not uri:
                continue
            bandwidth_match = re.search(
                r"(?:AVERAGE-)?BANDWIDTH=(\d+)", line, re.IGNORECASE
            )
            resolution_match = re.search(r"RESOLUTION=(\d+)x(\d+)", line, re.IGNORECASE)
            audio_match = re.search(
                r'AUDIO=(?:"([^"]+)"|([^,\s]+))',
                line,
                re.IGNORECASE,
            )
            bandwidth = int(bandwidth_match.group(1)) if bandwidth_match else 0
            pixels = (
                int(resolution_match.group(1)) * int(resolution_match.group(2))
                if resolution_match
                else 0
            )
            audio_group = (
                (audio_match.group(1) or audio_match.group(2)) if audio_match else None
            )
            variants.append((pixels, bandwidth, uri, audio_group))

        if not variants:
            return None, None

        # 明确选取最高分辨率；相同分辨率再按带宽选择，避免稳定落到最低画质。
        _, _, selected_uri, selected_audio_group = max(
            variants,
            key=lambda item: (item[0], item[1]),
        )
        video_m3u8 = urljoin(m3u8_url, selected_uri)
        audio_m3u8 = None
        candidates = audio_groups.get(selected_audio_group or "", [])
        if candidates:
            _, audio_uri = max(candidates, key=lambda item: item[0])
            audio_m3u8 = urljoin(m3u8_url, audio_uri)
        return video_m3u8, audio_m3u8

    async def _publish_single_stream(
        self,
        merged_path: str,
        output_path: str,
        use_ffmpeg: bool,
        budget: ByteBudget,
    ) -> bool:
        """将拼接后的 TS/fMP4 正确封装为输出文件并原子提交。"""
        if not use_ffmpeg:
            os.replace(merged_path, output_path)
            return True

        ffmpeg_output = f"{merged_path}.mp4"
        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-y",
                "-i",
                merged_path,
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                ffmpeg_output,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr, timed_out = await _communicate_process(process, "M3U8单流封装")
            if timed_out or process.returncode != 0:
                detail = (
                    stderr.decode("utf-8", errors="ignore").strip() if stderr else ""
                )
                logger.warning(f"M3U8单流封装失败: {detail[:200]}")
                return False
            size = await run_blocking(os.path.getsize, ffmpeg_output)
            if size > budget.limit:
                logger.warning("M3U8单流封装输出超过下载硬限制")
                return False
            os.replace(ffmpeg_output, output_path)
            return True
        except FileNotFoundError:
            logger.warning("ffmpeg 未找到，无法正确封装M3U8单流")
            return False
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"M3U8单流封装异常: {exc}")
            return False
        finally:
            cleanup_file(ffmpeg_output)

    async def download_m3u8_video(
        self,
        m3u8_url: str,
        output_path: str,
        use_ffmpeg: bool = True,
        max_bytes: Optional[int] = None,
    ) -> Tuple[bool, Optional[str], Optional[int]]:
        """下载完整的 m3u8 视频

        Args:
            m3u8_url: m3u8 URL
            output_path: 输出文件路径
            use_ffmpeg: 是否使用 ffmpeg 合并音视频（如果音视频分离）

        Returns:
            (下载是否成功, 错误原因, HTTP状态码)
        """
        output_dir = os.path.dirname(os.path.abspath(output_path))
        os.makedirs(output_dir, exist_ok=True)
        temp_dir = tempfile.mkdtemp(prefix=".m3u8_", dir=output_dir)
        budget = ByteBudget(resolve_max_bytes(max_bytes, is_video=True))
        try:
            video_m3u8, audio_m3u8 = await self.parse_master_m3u8(m3u8_url)

            if not video_m3u8:
                logger.debug("未找到视频流，尝试直接解析 m3u8")
                v_init, v_segs = await self.parse_m3u8(m3u8_url)
                v_files = await self.download_segments(
                    v_segs, os.path.join(temp_dir, "video"), "v", budget
                )
                video_merged = os.path.join(temp_dir, "video.m4s")
                if await self.merge_segments(v_init, v_files, video_merged, budget):
                    if await self._publish_single_stream(
                        video_merged, output_path, use_ffmpeg, budget
                    ):
                        logger.info(f"✓ 视频下载完成: {output_path}")
                        return True, None, None
                    return False, "M3U8视频封装失败", None
                return False, "M3U8视频合并失败", None

            if not audio_m3u8:
                logger.debug("只有视频流，没有音频流")
                v_init, v_segs = await self.parse_m3u8(video_m3u8)
                v_files = await self.download_segments(
                    v_segs, os.path.join(temp_dir, "video"), "v", budget
                )
                video_merged = os.path.join(temp_dir, "video.m4s")
                if await self.merge_segments(v_init, v_files, video_merged, budget):
                    if await self._publish_single_stream(
                        video_merged, output_path, use_ffmpeg, budget
                    ):
                        logger.info(f"✓ 视频下载完成: {output_path}")
                        return True, None, None
                    return False, "M3U8视频封装失败", None
                return False, "M3U8视频合并失败", None

            (v_init, v_segs), (a_init, a_segs) = await gather_cancel_on_error(
                self.parse_m3u8(video_m3u8), self.parse_m3u8(audio_m3u8)
            )

            v_files, a_files = await gather_cancel_on_error(
                self.download_segments(
                    v_segs, os.path.join(temp_dir, "video"), "v", budget
                ),
                self.download_segments(
                    a_segs, os.path.join(temp_dir, "audio"), "a", budget
                ),
            )

            video_merged = os.path.join(temp_dir, "video.m4s")
            audio_merged = os.path.join(temp_dir, "audio.m4s")
            merge_results = await gather_cancel_on_error(
                self.merge_segments(v_init, v_files, video_merged, budget),
                self.merge_segments(a_init, a_files, audio_merged, budget),
            )

            if not all(merge_results):
                logger.warning("合并分片失败")
                return False, "M3U8音视频分片合并失败", None

            if use_ffmpeg:
                try:
                    ffmpeg_output = os.path.join(temp_dir, "output.mp4")
                    process = await asyncio.create_subprocess_exec(
                        "ffmpeg",
                        "-y",
                        "-i",
                        video_merged,
                        "-i",
                        audio_merged,
                        "-c",
                        "copy",
                        "-map",
                        "0:v:0",
                        "-map",
                        "1:a:0",
                        ffmpeg_output,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    _, stderr, timed_out = await _communicate_process(
                        process, "M3U8 ffmpeg 合并"
                    )
                    if timed_out:
                        return False, "M3U8音视频合并超时", None
                    if process.returncode == 0:
                        output_size = await run_blocking(os.path.getsize, ffmpeg_output)
                        if output_size > budget.limit:
                            raise M3U8DownloadError("M3U8合并输出超过下载硬限制")
                        os.replace(ffmpeg_output, output_path)
                        logger.info(f"✓ 视频下载完成: {output_path}")
                        return True, None, None

                    error_output = (
                        stderr.decode("utf-8", errors="ignore").strip()
                        if stderr
                        else ""
                    )
                    logger.warning(
                        f"ffmpeg 合并失败(退出码 {process.returncode}): "
                        f"{error_output[:200]}"
                    )
                    return False, "M3U8音视频合并失败", None
                except FileNotFoundError:
                    logger.warning("ffmpeg 未找到，无法合并M3U8音视频")
                    return False, "ffmpeg未找到，无法合并M3U8音视频", None
            else:
                logger.warning("M3U8存在独立音频流，但未启用ffmpeg合并")
                return False, "M3U8音视频分离但未启用ffmpeg合并", None

        except asyncio.CancelledError:
            raise
        except M3U8DownloadError as e:
            logger.error(f"✗ 视频下载失败: {e}")
            return False, str(e), e.status_code
        except Exception as e:
            logger.error(f"✗ 视频下载失败: {e}")
            return False, str(e), _status_code_from_exception(e)
        finally:
            cleanup_directory(temp_dir, ignore_errors=True)

    async def download_m3u8_to_cache(
        self,
        m3u8_url: str,
        cache_dir: str,
        media_id: str,
        index: int = 0,
        use_ffmpeg: bool = True,
        max_bytes: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """下载 m3u8 视频到缓存目录

        Args:
            m3u8_url: m3u8 URL
            cache_dir: 缓存目录路径
            media_id: 媒体ID
            index: 索引
            use_ffmpeg: 是否使用 ffmpeg 合并音视频

        Returns:
            下载结果字典，失败时保留错误原因与 HTTP 状态码。
        """
        if not cache_dir:
            return None

        try:
            cache_subdir = os.path.join(cache_dir, media_id)
            os.makedirs(cache_subdir, exist_ok=True)
            stamp_subdir(cache_subdir)
            filename = f"video_{index}.mp4"
            output_path = os.path.join(cache_subdir, filename)

            success, error, status_code = await self.download_m3u8_video(
                m3u8_url, output_path, use_ffmpeg, max_bytes
            )

            if success and os.path.exists(output_path):
                try:
                    file_size_bytes = os.path.getsize(output_path)
                    size_mb = file_size_bytes / (1024 * 1024)
                except Exception:
                    size_mb = None

                return {
                    "file_path": os.path.normpath(output_path),
                    "size_mb": size_mb,
                    "status_code": status_code,
                }

            cleanup_file(output_path)
            return {
                "file_path": None,
                "size_mb": None,
                "status_code": status_code,
                "error": error or "M3U8下载失败",
            }
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"下载 m3u8 到缓存目录失败: {m3u8_url}, 错误: {e}")
            return {
                "file_path": None,
                "size_mb": None,
                "status_code": _status_code_from_exception(e),
                "error": str(e) or "M3U8下载失败",
            }
