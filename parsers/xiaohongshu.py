# -*- coding: utf-8 -*-
import asyncio
import json
import re
from datetime import datetime
from typing import Optional, Dict, Any, List
from urllib.parse import unquote, urlparse, parse_qs, urlencode, urlunparse

import aiohttp

from .base_parser import BaseVideoParser


ANDROID_UA = (
    "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Mobile Safari/537.36 Edg/142.0.0.0"
)


class XiaohongshuParser(BaseVideoParser):
    """小红书链接解析器。"""

    def __init__(self):
        """初始化小红书解析器。"""
        super().__init__("小红书")
        self.headers = {
            "User-Agent": ANDROID_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        self.semaphore = asyncio.Semaphore(10)

    def can_parse(self, url: str) -> bool:
        """判断是否可以解析此URL。

        Args:
            url: 视频链接

        Returns:
            如果可以解析返回True，否则返回False
        """
        if not url:
            return False
        url_lower = url.lower()
        if 'xhslink.com' in url_lower or 'xiaohongshu.com' in url_lower:
            return True
        return False

    def extract_links(self, text: str) -> List[str]:
        """从文本中提取小红书链接。

        Args:
            text: 输入文本

        Returns:
            小红书链接列表
        """
        result_links = []
        seen_urls = set()
        short_pattern = r'https?://xhslink\.com/[^\s<>"\'()]+'
        short_links = re.findall(short_pattern, text, re.IGNORECASE)
        for link in short_links:
            normalized = link.lower()
            if normalized not in seen_urls:
                seen_urls.add(normalized)
                result_links.append(link)
        long_pattern = (
            r'https?://(?:www\.)?xiaohongshu\.com/'
            r'(?:explore|discovery/item)/[^\s<>"\'()]+'
        )
        long_links = re.findall(long_pattern, text, re.IGNORECASE)
        for link in long_links:
            normalized = link.lower()
            if normalized not in seen_urls:
                seen_urls.add(normalized)
                if not link.startswith("http://") and not link.startswith("https://"):
                    link = "https://" + link
                result_links.append(link)
        return result_links

    def _clean_share_url(self, url: str) -> str:
        """清理分享长链URL，删除source和xhsshare参数。

        Args:
            url: 原始URL

        Returns:
            清理后的URL
        """
        if "discovery/item" not in url:
            return url

        parsed = urlparse(url)
        query_params = parse_qs(parsed.query, keep_blank_values=True)
        query_params.pop('source', None)
        query_params.pop('xhsshare', None)

        flat_params = {}
        for key, value_list in query_params.items():
            flat_params[key] = value_list[0] if value_list and value_list[0] else ''

        new_query = urlencode(flat_params)
        new_parsed = parsed._replace(query=new_query)
        return urlunparse(new_parsed)

    async def _get_redirect_url(
        self,
        session: aiohttp.ClientSession,
        short_url: str
    ) -> str:
        """获取短链接重定向后的完整URL。

        Args:
            session: aiohttp会话
            short_url: 短链接URL

        Returns:
            重定向后的完整URL

        Raises:
            RuntimeError: 当无法获取重定向URL时
        """
        async with session.get(
            short_url,
            headers=self.headers,
            allow_redirects=False
        ) as response:
            if response.status == 302:
                redirect_url = response.headers.get("Location", "")
                if not redirect_url:
                    raise RuntimeError("无法获取重定向URL")
                return unquote(redirect_url)
            else:
                raise RuntimeError(
                    f"无法获取重定向URL，状态码: {response.status}"
                )

    async def _fetch_page(
        self,
        session: aiohttp.ClientSession,
        url: str
    ) -> str:
        """获取页面HTML内容。

        Args:
            session: aiohttp会话
            url: 页面URL

        Returns:
            HTML内容

        Raises:
            RuntimeError: 当无法获取页面内容时
        """
        async with session.get(url, headers=self.headers) as response:
            if response.status == 200:
                return await response.text()
            else:
                raise RuntimeError(
                    f"无法获取页面内容，状态码: {response.status}"
                )

    def _extract_initial_state(self, html: str) -> dict:
        """从HTML中提取window.__INITIAL_STATE__的JSON数据。

        Args:
            html: HTML内容

        Returns:
            JSON数据字典

        Raises:
            RuntimeError: 当无法提取JSON数据时
        """
        pattern = r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>'
        match = re.search(pattern, html, re.DOTALL)
        if match:
            json_str = match.group(1)
            json_str = re.sub(r'\bundefined\b', 'null', json_str)
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass

        start_marker = 'window.__INITIAL_STATE__'
        start_idx = html.find(start_marker)
        if start_idx == -1:
            raise RuntimeError("无法找到window.__INITIAL_STATE__数据")

        json_start = html.find('{', start_idx)
        if json_start == -1:
            raise RuntimeError("无法找到JSON开始位置")

        script_end = html.find('</script>', start_idx)
        if script_end == -1:
            script_end = len(html)

        brace_count = 0
        json_end = json_start
        in_string = False
        escape_next = False
        in_single_quote = False

        search_end = min(script_end, len(html))
        for i in range(json_start, search_end):
            char = html[i]

            if escape_next:
                escape_next = False
                continue

            if char == '\\':
                escape_next = True
                continue

            if char == '"' and not escape_next and not in_single_quote:
                in_string = not in_string
                continue

            if char == "'" and not escape_next and not in_string:
                in_single_quote = not in_single_quote
                continue

            if not in_string and not in_single_quote:
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        json_end = i + 1
                        break

        if brace_count != 0:
            raise RuntimeError("无法找到完整的JSON对象")

        json_str = html[json_start:json_end]
        json_str = re.sub(r'\bundefined\b', 'null', json_str)

        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            error_pos = getattr(e, 'pos', 0)
            start_debug = max(0, error_pos - 200)
            end_debug = min(len(json_str), error_pos + 200)
            error_msg = (
                f"JSON解析失败: {e}\n"
                f"错误位置: {error_pos}\n"
                f"附近内容: {json_str[start_debug:end_debug]}"
            )
            raise RuntimeError(error_msg)

    def _clean_topic_tags(self, text: str) -> str:
        """清理简介中的话题标签，将#标签[话题]#格式改为#标签。

        Args:
            text: 原始文本

        Returns:
            清理后的文本
        """
        if not text:
            return text
        pattern = r'#([^#\[]+)\[话题\]#'
        return re.sub(pattern, r'#\1', text)

    def _parse_note_data(self, data: dict) -> dict:
        """从JSON数据中提取所需信息。

        Args:
            data: JSON数据字典

        Returns:
            包含笔记信息的字典

        Raises:
            RuntimeError: 当数据提取失败时
        """
        try:
            note_data = data["noteData"]["data"]["noteData"]
            user_data = note_data.get("user", {})
        except (KeyError, TypeError):
            raise RuntimeError("无法找到笔记数据，JSON结构可能不同")

        note_type = note_data.get("type", "normal")
        title = note_data.get("title", "")
        desc = note_data.get("desc", "")

        author_name = ""
        author_id = ""
        if user_data:
            author_name = user_data.get("nickName", "")
            author_id = user_data.get("userId", "")

        timestamp = note_data.get("time", 0)
        if timestamp:
            dt = datetime.fromtimestamp(timestamp / 1000)
            publish_time = dt.strftime("%Y-%m-%d")
        else:
            publish_time = ""

        video_url = ""
        image_urls = []

        if note_type == "video":
            video_info = note_data.get("video", {})
            if video_info and "media" in video_info:
                media = video_info["media"]
                if "stream" in media:
                    stream = media["stream"]
                    if "h264" in stream and len(stream["h264"]) > 0:
                        h264 = stream["h264"][0]
                        video_url = h264.get("masterUrl", "")

            if video_url and video_url.startswith("http://"):
                video_url = video_url.replace("http://", "https://", 1)
            elif video_url and video_url.startswith("//"):
                video_url = "https:" + video_url
        else:
            image_list = note_data.get("imageList", [])
            if image_list:
                for img in image_list:
                    if isinstance(img, dict):
                        url = img.get("url", "")
                        if url:
                            if "picasso-static" not in url and "fe-platform" not in url:
                                if url.startswith("//"):
                                    url = "https:" + url
                                elif url.startswith("http://"):
                                    url = url.replace("http://", "https://", 1)
                                image_urls.append(url)

        desc = self._clean_topic_tags(desc)

        return {
            "type": note_type,
            "title": title,
            "desc": desc,
            "author_name": author_name,
            "author_id": author_id,
            "publish_time": publish_time,
            "video_url": video_url,
            "image_urls": image_urls,
        }

    async def parse(
        self,
        session: aiohttp.ClientSession,
        url: str
    ) -> Optional[Dict[str, Any]]:
        """解析单个小红书链接。

        Args:
            session: aiohttp会话
            url: 小红书链接

        Returns:
            解析结果字典，包含标准化的元数据格式

        Raises:
            RuntimeError: 当解析失败时
        """
        async with self.semaphore:
            if "xhslink.com" in url:
                full_url = await self._get_redirect_url(session, url)
            else:
                full_url = url
                if not full_url.startswith("http://") and not full_url.startswith("https://"):
                    full_url = "https://" + full_url

            full_url = self._clean_share_url(full_url)

            html = await self._fetch_page(session, full_url)
            initial_state = self._extract_initial_state(html)
            note_data = self._parse_note_data(initial_state)

            note_type = note_data.get("type", "normal")
            video_url = note_data.get("video_url", "")
            image_urls = note_data.get("image_urls", [])
            title = note_data.get("title", "")
            desc = note_data.get("desc", "")
            author_name = note_data.get("author_name", "")
            author_id = note_data.get("author_id", "")
            publish_time = note_data.get("publish_time", "")

            author = ""
            if author_name and author_id:
                author = f"{author_name}(主页id:{author_id})"
            elif author_name:
                author = author_name
            elif author_id:
                author = f"(主页id:{author_id})"

            if note_type == "video":
                if not video_url:
                    raise RuntimeError(f"无法获取视频URL: {url}")

                return {
                    "url": url,
                    "media_type": "video",
                    "title": title,
                    "author": author,
                    "desc": desc,
                    "timestamp": publish_time,
                    "media_urls": [video_url],
                    "thumb_url": None,
                    "page_url": full_url,
                }
            else:
                if not image_urls:
                    raise RuntimeError(f"无法获取图片URL: {url}")

                return {
                    "url": url,
                    "media_type": "gallery",
                    "title": title,
                    "author": author,
                    "desc": desc,
                    "timestamp": publish_time,
                    "media_urls": image_urls,
                    "thumb_url": None,
                    "page_url": full_url,
                }
