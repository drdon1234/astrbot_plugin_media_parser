# -*- coding: utf-8 -*-
"""
M3U8 媒体处理模块
负责解析和下载 M3U8 格式的视频流
"""
import asyncio
import os
import re
import shutil
import subprocess
import tempfile
import time
from typing import Dict, Any, List, Optional, Tuple
from urllib.parse import urljoin

import aiohttp

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from ...file_cleaner import cleanup_directory


class M3U8Handler:
    """M3U8 媒体处理器"""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        headers: dict = None,
        proxy: str = None,
        max_concurrent_segments: int = 10
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
        async with self.session.get(
            url,
            headers=self.headers,
            proxy=self.proxy
        ) as response:
            response.raise_for_status()
            return await response.text()

    async def fetch_bytes(self, url: str) -> bytes:
        """获取二进制内容

        Args:
            url: URL地址

        Returns:
            二进制内容
        """
        async with self.session.get(
            url,
            headers=self.headers,
            proxy=self.proxy
        ) as response:
            response.raise_for_status()
            return await response.read()

    async def download_file(self, url: str, output_path: str) -> bool:
        """下载文件

        Args:
            url: 文件URL
            output_path: 输出路径

        Returns:
            下载是否成功
        """
        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            async with self.session.get(
                url,
                headers=self.headers,
                proxy=self.proxy
            ) as response:
                response.raise_for_status()
                with open(output_path, 'wb') as f:
                    async for chunk in response.content.iter_chunked(8192):
                        f.write(chunk)
            return True
        except Exception as e:
            logger.warning(f"下载文件失败 {url}: {e}")
            return False

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

        for line in content.split('\n'):
            line = line.strip()
            if 'URI=' in line:
                match = re.search(r'URI="([^"]+)"', line)
                if match:
                    init_seg = match.group(1)
            elif line and not line.startswith('#'):
                segments.append(line)

        base = url.rsplit('/', 1)[0] + '/'
        if init_seg:
            init_seg = urljoin(base, init_seg)
        segments = [urljoin(base, s) for s in segments]
        return init_seg, segments

    async def download_segments(
        self,
        segments: List[str],
        output_dir: str,
        prefix: str = "seg"
    ) -> List[str]:
        """并发下载所有分片

        Args:
            segments: 分片 URL 列表
            output_dir: 输出目录
            prefix: 文件前缀

        Returns:
            成功下载的文件路径列表（已排序）
        """
        os.makedirs(output_dir, exist_ok=True)

        async def download_segment(i: int, url: str) -> Optional[str]:
            """下载单个分片"""
            path = os.path.join(output_dir, f"{prefix}_{i:05d}.m4s")
            success = await self.download_file(url, path)
            return path if success else None

        semaphore = asyncio.Semaphore(self.max_concurrent_segments)

        async def download_with_limit(i: int, url: str) -> Optional[str]:
            async with semaphore:
                return await download_segment(i, url)

        tasks = [download_with_limit(i, url) for i, url in enumerate(segments)]
        results = await asyncio.gather(*tasks)

        files = [f for f in results if f is not None]
        return sorted(files)

    async def merge_segments(
        self,
        init_seg: Optional[str],
        files: List[str],
        output: str
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
            with open(output, 'wb') as out:
                if init_seg:
                    init_data = await self.fetch_bytes(init_seg)
                    out.write(init_data)
                for f in files:
                    with open(f, 'rb') as inp:
                        shutil.copyfileobj(inp, out)
            return True
        except Exception as e:
            logger.warning(f"合并分片失败: {e}")
            return False

    async def parse_master_m3u8(
        self,
        m3u8_url: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """解析主 m3u8 文件，获取视频和音频 m3u8 URL

        Args:
            m3u8_url: 主 m3u8 URL

        Returns:
            (video_m3u8_url, audio_m3u8_url) 元组
        """
        master = await self.fetch_text(m3u8_url)
        video_m3u8 = None
        audio_m3u8 = None

        for line in master.split('\n'):
            line = line.strip()
            if 'TYPE=AUDIO' in line and 'URI=' in line:
                match = re.search(r'URI="([^"]+)"', line)
                if match:
                    audio_m3u8 = match.group(1)
            elif not line.startswith('#') and '.m3u8' in line:
                if 'TYPE=AUDIO' not in line and 'video' in line.lower():
                    video_m3u8 = line
                elif video_m3u8 is None and 'TYPE=AUDIO' not in line:
                    video_m3u8 = line

        base = m3u8_url.split('?')[0].rsplit('/', 1)[0] + '/'
        if video_m3u8:
            video_m3u8 = urljoin(base, video_m3u8)
        if audio_m3u8:
            audio_m3u8 = urljoin(base, audio_m3u8)

        return video_m3u8, audio_m3u8

    async def download_m3u8_video(
        self,
        m3u8_url: str,
        output_path: str,
        use_ffmpeg: bool = True
    ) -> bool:
        """下载完整的 m3u8 视频

        Args:
            m3u8_url: m3u8 URL
            output_path: 输出文件路径
            use_ffmpeg: 是否使用 ffmpeg 合并音视频（如果音视频分离）

        Returns:
            下载是否成功
        """
        temp_dir = tempfile.mkdtemp()
        try:
            video_m3u8, audio_m3u8 = await self.parse_master_m3u8(m3u8_url)

            if not video_m3u8:
                logger.debug("未找到视频流，尝试直接解析 m3u8")
                v_init, v_segs = await self.parse_m3u8(m3u8_url)
                v_files = await self.download_segments(
                    v_segs, os.path.join(temp_dir, "video"), "v"
                )
                video_merged = os.path.join(temp_dir, "video.m4s")
                if await self.merge_segments(v_init, v_files, video_merged):
                    shutil.move(video_merged, output_path)
                    logger.info(f"✓ 视频下载完成: {output_path}")
                    return True
                return False

            if not audio_m3u8:
                logger.debug("只有视频流，没有音频流")
                v_init, v_segs = await self.parse_m3u8(video_m3u8)
                v_files = await self.download_segments(
                    v_segs, os.path.join(temp_dir, "video"), "v"
                )
                video_merged = os.path.join(temp_dir, "video.m4s")
                if await self.merge_segments(v_init, v_files, video_merged):
                    shutil.move(video_merged, output_path)
                    logger.info(f"✓ 视频下载完成: {output_path}")
                    return True
                return False

            (v_init, v_segs), (a_init, a_segs) = await asyncio.gather(
                self.parse_m3u8(video_m3u8),
                self.parse_m3u8(audio_m3u8)
            )

            v_files, a_files = await asyncio.gather(
                self.download_segments(
                    v_segs, os.path.join(temp_dir, "video"), "v"
                ),
                self.download_segments(
                    a_segs, os.path.join(temp_dir, "audio"), "a"
                )
            )

            video_merged = os.path.join(temp_dir, "video.m4s")
            audio_merged = os.path.join(temp_dir, "audio.m4s")
            merge_results = await asyncio.gather(
                self.merge_segments(v_init, v_files, video_merged),
                self.merge_segments(a_init, a_files, audio_merged)
            )

            if not all(merge_results):
                logger.warning("合并分片失败")
                return False

            if use_ffmpeg:
                try:
                    subprocess.run([
                        "ffmpeg", "-y", "-i", video_merged, "-i", audio_merged,
                        "-c", "copy", "-map", "0:v:0", "-map", "1:a:0",
                        output_path
                    ], check=True, capture_output=True)
                    logger.info(f"✓ 视频下载完成: {output_path}")
                    return True
                except subprocess.CalledProcessError as e:
                    logger.warning(f"ffmpeg 合并失败: {e}")
                    shutil.move(video_merged, output_path)
                    logger.info(f"✓ 视频下载完成（无音频）: {output_path}")
                    return True
                except FileNotFoundError:
                    logger.warning("ffmpeg 未找到，尝试只保存视频")
                    shutil.move(video_merged, output_path)
                    logger.info(f"✓ 视频下载完成（无音频）: {output_path}")
                    return True
            else:
                shutil.move(video_merged, output_path)
                logger.info(f"✓ 视频下载完成（无音频）: {output_path}")
                return True

        except Exception as e:
            logger.error(f"✗ 视频下载失败: {e}")
            return False
        finally:
            cleanup_directory(temp_dir, ignore_errors=True)

    async def download_m3u8_to_cache(
        self,
        m3u8_url: str,
        cache_dir: str,
        media_id: str,
        index: int = 0,
        use_ffmpeg: bool = True
    ) -> Optional[Dict[str, Any]]:
        """下载 m3u8 视频到缓存目录

        Args:
            m3u8_url: m3u8 URL
            cache_dir: 缓存目录路径
            media_id: 媒体ID
            index: 索引
            use_ffmpeg: 是否使用 ffmpeg 合并音视频

        Returns:
            包含 file_path 和 size_mb 的字典，失败返回 None
        """
        if not cache_dir:
            return None

        try:
            cache_subdir = os.path.join(cache_dir, media_id)
            os.makedirs(cache_subdir, exist_ok=True)
            filename = f"video_{index}.mp4"
            output_path = os.path.join(cache_subdir, filename)

            success = await self.download_m3u8_video(
                m3u8_url, output_path, use_ffmpeg
            )

            if success and os.path.exists(output_path):
                try:
                    file_size_bytes = os.path.getsize(output_path)
                    size_mb = file_size_bytes / (1024 * 1024)
                except Exception:
                    size_mb = None

                return {
                    'file_path': os.path.normpath(output_path),
                    'size_mb': size_mb
                }

            return None
        except Exception as e:
            logger.warning(f"下载 m3u8 到缓存目录失败: {m3u8_url}, 错误: {e}")
            return None

