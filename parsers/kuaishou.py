# -*- coding: utf-8 -*-
import asyncio
import json
import os
import re
from datetime import datetime
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse

import aiohttp

from .base_parser import BaseVideoParser

MOBILE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) '
                  'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Accept-Encoding': 'gzip, deflate',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1'
}


class KuaishouParser(BaseVideoParser):
    """快手视频解析器"""

    def __init__(self, max_media_size_mb: float = 0.0, large_media_threshold_mb: float = 50.0, cache_dir: str = "/app/sharedFolder/video_parser/cache", pre_download_all_media: bool = False, max_concurrent_downloads: int = 3):
        super().__init__("快手", max_media_size_mb, large_media_threshold_mb, cache_dir, pre_download_all_media, max_concurrent_downloads)
        self.headers = MOBILE_HEADERS
        self.semaphore = asyncio.Semaphore(10)

    def can_parse(self, url: str) -> bool:
        """
        判断是否可以解析此URL
        Args:
            url: 视频链接
        Returns:
            bool: 如果可以解析返回True，否则返回False
        """
        if not url:
            return False
        url_lower = url.lower()
        if 'kuaishou.com' in url_lower or 'kspkg.com' in url_lower:
            return True
        return False

    def extract_links(self, text: str) -> List[str]:
        """
        从文本中提取快手链接
        Args:
            text: 输入文本
        Returns:
            List[str]: 快手链接列表
        """
        result_links = []
        short_pattern = r'https?://v\.kuaishou\.com/[^\s]+'
        short_links = re.findall(short_pattern, text)
        result_links.extend(short_links)
        long_pattern = r'https?://(?:www\.)?kuaishou\.com/[^\s]+'
        long_links = re.findall(long_pattern, text)
        result_links.extend(long_links)
        return result_links

    def _extract_media_id(self, url: str) -> str:
        """
        从URL中提取媒体ID
        Args:
            url: 快手URL
        Returns:
            str: 媒体ID，如果无法提取则返回 "kuaishou"
        """
        video_id_match = re.search(r'/(\w+)(?:\.html|/|\?|$)', url)
        return video_id_match.group(1) if video_id_match else "kuaishou"

    def _min_mp4(self, url: str) -> str:
        """处理MP4 URL，提取最小格式"""
        pu = urlparse(url)
        domain = pu.netloc
        filename = pu.path.split('/')[-1].split('?')[0]
        path_wo_file = '/'.join(pu.path.split('/')[1:-1])
        return f"https://{domain}/{path_wo_file}/{filename}"

    def _extract_upload_time(self, url: str) -> Optional[str]:
        """从URL中提取上传时间"""
        try:
            match = re.search(r'/(\d{4})/(\d{2})/(\d{2})/', url)
            if match:
                year, month, day = match.groups()
                return f"{year}-{month}-{day}"
            match = re.search(r'_(\d{11,13})_', url)
            if match:
                timestamp = int(match.group(1))
                if len(match.group(1)) == 13:
                    timestamp = timestamp // 1000
                dt = datetime.fromtimestamp(timestamp)
                return dt.strftime('%Y-%m-%d')
        except Exception:
            pass
        return None

    def _extract_metadata(self, html: str) -> Dict[str, Optional[str]]:
        """提取用户名、UID、标题"""
        metadata = {'userName': None, 'userId': None, 'caption': None}
        json_match = re.search(r'window\.INIT_STATE\s*=\s*({.*?});', html, re.DOTALL)
        if not json_match:
            json_match = re.search(r'window\.__APOLLO_STATE__\s*=\s*({.*?});', html, re.DOTALL)
        if json_match:
            try:
                json_str = json_match.group(1)
                user_match = re.search(r'"userName"\s*:\s*"([^"]+)"', json_str)
                if user_match:
                    metadata['userName'] = user_match.group(1)
                uid_match = re.search(r'"userId"\s*:\s*["\']?(\d+)["\']?', json_str)
                if uid_match:
                    metadata['userId'] = uid_match.group(1)
                caption_match = re.search(r'"caption"\s*:\s*"([^"]*(?:\\.[^"]*)*)"', json_str)
                if caption_match:
                    raw_caption = caption_match.group(1)
                    try:
                        test_json = f'{{"text":"{raw_caption}"}}'
                        parsed = json.loads(test_json)
                        metadata['caption'] = parsed['text']
                    except Exception:
                        metadata['caption'] = raw_caption
            except Exception:
                pass
        if not metadata['caption']:
            title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE)
            if title_match:
                metadata['caption'] = title_match.group(1).strip()
        return metadata

    def _extract_album_image_url(self, html: str) -> Optional[str]:
        """提取图集图片URL"""
        match = re.search(r'<img\s+class="image"\s+src="([^"]+)"', html)
        if match:
            return match.group(1).split('?')[0]
        match = re.search(r'src="(https?://[^"]*?/upic/[^"]*?\.jpg)', html)
        if match:
            return match.group(1)
        return None

    def _build_album(self, cdns: List[str], music_path: Optional[str], img_paths: List[str]) -> Dict[str, Any]:
        """
        构建图集数据，支持多个CDN
        Args:
            cdns: CDN列表
            music_path: 音乐路径
            img_paths: 图片路径列表
        Returns:
            包含 images（主URL列表）和 image_url_lists（每个图片的所有CDN URL列表）的字典
        """
        cleaned_cdns = [re.sub(r'https?://', '', cdn) for cdn in cdns if cdn]
        if not cleaned_cdns:
            return None
        cleaned_paths = [p.strip('"') for p in img_paths if p.strip('"')]
        if not cleaned_paths:
            return None
        images = []
        image_url_lists = []
        for img_path in cleaned_paths:
            url_list = []
            for cdn in cleaned_cdns:
                url = f"https://{cdn}{img_path}"
                url_list.append(url)
            if url_list:
                images.append(url_list[0])
                image_url_lists.append(url_list)
        seen = set()
        uniq_images = []
        uniq_image_url_lists = []
        for idx, img_url in enumerate(images):
            if img_url not in seen:
                seen.add(img_url)
                uniq_images.append(img_url)
                url_list = image_url_lists[idx].copy() if image_url_lists[idx] else []
                if url_list and url_list[0] != img_url:
                    if img_url in url_list:
                        url_list.remove(img_url)
                    url_list.insert(0, img_url)
                uniq_image_url_lists.append(url_list)
        bgm = None
        if music_path and cleaned_cdns:
            cleaned_music = music_path.strip('"')
            bgm = f"https://{cleaned_cdns[0]}{cleaned_music}"
        return {
            'type': 'album',
            'bgm': bgm,
            'images': uniq_images,
            'image_url_lists': uniq_image_url_lists
        }

    def _parse_album(self, html: str) -> Optional[Dict[str, Any]]:
        """
        解析图集，提取所有CDN
        Returns:
            包含 images 和 image_url_lists 的字典，如果解析失败返回 None
        """
        cdn_matches = re.findall(r'"cdnList"\s*:\s*\[.*?"cdn"\s*:\s*"([^"]+)"', html, re.DOTALL)
        if not cdn_matches:
            cdn_matches = re.findall(r'"cdn"\s*:\s*\["([^"]+)"', html)
        if not cdn_matches:
            cdn_matches = re.findall(r'"cdn"\s*:\s*"([^"]+)"', html)
        if not cdn_matches:
            return None
        cdns = list(set(cdn_matches))
        img_paths = re.findall(r'"/ufile/atlas/[^"]+?\.jpg"', html)
        if not img_paths:
            return None
        m = re.search(r'"music"\s*:\s*"(/ufile/atlas/[^"]+?\.m4a)"', html)
        music_path = m.group(1) if m else None
        return self._build_album(cdns, music_path, img_paths)

    def _parse_video(self, html: str) -> Optional[str]:
        """解析视频URL"""
        m = re.search(r'"(url|srcNoMark|photoUrl|videoUrl)"\s*:\s*"(https?://[^"]+?\.mp4[^"]*)"', html)
        if not m:
            m = re.search(r'"url"\s*:\s*"(https?://[^"]+?\.mp4[^"]*)"', html)
        if m:
            return self._min_mp4(m.group(2))
        return None

    async def _download_image_to_file(self, session: aiohttp.ClientSession, image_url: str, index: int = 0) -> Optional[str]:
        """
        下载图片到临时文件（使用基类方法）
        Args:
            session: aiohttp 会话
            image_url: 图片URL
            index: 图片索引
        Returns:
            Optional[str]: 临时文件路径，失败返回 None
        """
        kuaishou_headers = {
            'Referer': 'https://www.kuaishou.com/',
        }
        return await super()._download_image_to_file(
            session, image_url, index, kuaishou_headers, None, 'https://www.kuaishou.com/'
        )

    async def parse(self, session: aiohttp.ClientSession, url: str) -> Optional[Dict[str, Any]]:
        """
        解析单个快手链接
        Args:
            session: aiohttp会话
            url: 快手链接
        Returns:
            Optional[Dict[str, Any]]: 解析结果字典，如果解析失败则返回None
        """
        async with self.semaphore:
            try:
                is_short = 'v.kuaishou.com' in urlparse(url).netloc
                if is_short:
                    async with session.get(url, headers=self.headers, allow_redirects=False) as r1:
                        if r1.status != 302:
                            return None
                        loc = r1.headers.get('Location')
                        if not loc:
                            return None
                    async with session.get(loc, headers=self.headers) as r2:
                        if r2.status != 200:
                            return None
                        html = await r2.text()
                else:
                    async with session.get(url, headers=self.headers) as r:
                        if r.status != 200:
                            return None
                        html = await r.text()
                metadata = self._extract_metadata(html)
                userName = metadata.get('userName', '')
                userId = metadata.get('userId', '')
                if userName and userId:
                    author = f"{userName}(uid:{userId})"
                elif userName:
                    author = userName
                elif userId:
                    author = f"(uid:{userId})"
                else:
                    author = ""
                title = metadata.get('caption', '') or "快手视频"
                if len(title) > 100:
                    title = title[:100]
                video_url = self._parse_video(html)
                if video_url:
                    video_size = await self.get_video_size(video_url, session)
                    if self.max_media_size_mb > 0 and video_size is not None:
                        if video_size > self.max_media_size_mb:
                            return None
                    has_large_video = False
                    video_file_path = None
                    if self.large_media_threshold_mb > 0 and video_size is not None and video_size > self.large_media_threshold_mb:
                        if not self.cache_dir_available:
                            raise RuntimeError("解析失败：本地缓存路径无效")
                        if self.max_media_size_mb <= 0 or video_size <= self.max_media_size_mb:
                            video_id = self._extract_media_id(url)
                            video_file_path = await self._download_large_media_to_cache(
                                session,
                                video_url,
                                video_id,
                                index=0,
                                headers=self.headers,
                                is_video=True,
                                referer=url
                            )
                    upload_time = self._extract_upload_time(video_url)
                    parse_result = {
                        "video_url": url,
                        "title": title,
                        "author": author,
                        "timestamp": upload_time or "",
                        "direct_url": video_url,
                        "file_size_mb": video_size
                    }
                    if has_large_video:
                        parse_result['force_separate_send'] = True
                        if video_file_path:
                            parse_result['video_files'] = [{'file_path': video_file_path}]
                    # 预先下载所有媒体
                    if self.pre_download_all_media and self.cache_dir_available and not has_large_video:
                        media_items = []
                        if video_url:
                            video_id = self._extract_media_id(url)
                            media_items.append({
                                'url': video_url,
                                'media_id': video_id,
                                'index': 0,
                                'is_video': True,
                                'headers': self.headers,
                                'referer': url
                            })
                        if media_items:
                            download_results = await self._pre_download_media(session, media_items, self.headers)
                            for download_result in download_results:
                                if download_result.get('success') and download_result.get('file_path'):
                                    parse_result['video_files'] = [{'file_path': download_result['file_path']}]
                                    parse_result['direct_url'] = None
                                    break
                    return parse_result
                album = self._parse_album(html)
                if album:
                    if not self.cache_dir_available:
                        raise RuntimeError("解析失败：本地缓存路径无效")
                    images = album.get('images', [])
                    image_url_lists = album.get('image_url_lists', [])
                    if images:
                        image_files = []
                        image_url = self._extract_album_image_url(html)
                        upload_time = self._extract_upload_time(image_url) if image_url else None
                        # 优先检查是否开启预下载，避免重复下载
                        if self.pre_download_all_media and self.cache_dir_available:
                            media_items = []
                            for idx, img_url in enumerate(images):
                                if img_url and isinstance(img_url, str) and img_url.startswith(('http://', 'https://')):
                                    image_size = await self.get_image_size(img_url, session)
                                    if self.max_media_size_mb > 0 and image_size is not None:
                                        if image_size > self.max_media_size_mb:
                                            continue
                                    image_id = self._extract_media_id(url)
                                    # 添加备用URL列表
                                    backup_urls = []
                                    if idx < len(image_url_lists) and image_url_lists[idx]:
                                        backup_urls = image_url_lists[idx][1:] if len(image_url_lists[idx]) > 1 else []
                                    media_items.append({
                                        'url': img_url,
                                        'media_id': image_id,
                                        'index': idx,
                                        'is_video': False,
                                        'headers': self.headers,
                                        'backup_urls': backup_urls,
                                        'referer': url,
                                        'default_referer': 'https://www.kuaishou.com/'
                                    })
                            if media_items:
                                # 先尝试预下载主URL
                                download_results = await self._pre_download_media(session, media_items, self.headers)
                                # 对于失败的下载，尝试备用URL
                                for idx, download_result in enumerate(download_results):
                                    if download_result.get('success') and download_result.get('file_path'):
                                        image_files.append(download_result['file_path'])
                                    else:
                                        # 主URL下载失败，尝试备用URL
                                        item = media_items[idx]
                                        backup_urls = item.get('backup_urls', [])
                                        if backup_urls:
                                            image_id = self._extract_media_id(url)
                                            cache_path = await self._retry_download_with_backup_urls(
                                                session,
                                                download_result,
                                                backup_urls,
                                                image_id,
                                                item['index'],
                                                headers=self.headers,
                                                default_referer='https://www.kuaishou.com/'
                                            )
                                            if cache_path:
                                                image_files.append(cache_path)
                            if image_files:
                                images = []
                        else:
                            # 未开启预下载，使用原有逻辑
                            for idx, primary_url in enumerate(images):
                                if not primary_url or not isinstance(primary_url, str) or not primary_url.startswith(('http://', 'https://')):
                                    continue
                                image_size = await self.get_image_size(primary_url, session)
                                if self.max_media_size_mb > 0 and image_size is not None:
                                    if image_size > self.max_media_size_mb:
                                        continue
                                backup_urls = []
                                if idx < len(image_url_lists) and image_url_lists[idx]:
                                    backup_urls = image_url_lists[idx][1:]
                                    all_urls = image_url_lists[idx]
                                else:
                                    all_urls = [primary_url]
                                image_file = None
                                if self.large_media_threshold_mb > 0 and image_size is not None and image_size > self.large_media_threshold_mb:
                                    if self.max_media_size_mb <= 0 or image_size <= self.max_media_size_mb:
                                        image_id = self._extract_media_id(url)
                                        # 先尝试主URL下载到缓存（使用与临时文件下载相同的逻辑）
                                        image_file = await self._download_large_media_to_cache(
                                            session,
                                            primary_url,
                                            image_id,
                                            index=idx,
                                            headers=self.headers,
                                            is_video=False,
                                            referer=url,
                                            default_referer='https://www.kuaishou.com/'
                                        )
                                        # 如果主URL下载到缓存失败，尝试备用URL下载到缓存（使用与临时文件下载相同的逻辑）
                                        if not image_file and backup_urls:
                                            for backup_url in backup_urls:
                                                image_file = await self._download_large_media_to_cache(
                                                    session,
                                                    backup_url,
                                                    image_id,
                                                    index=idx,
                                                    headers=self.headers,
                                                    is_video=False,
                                                    referer=url,
                                                    default_referer='https://www.kuaishou.com/'
                                                )
                                                if image_file:
                                                    break
                                if not image_file:
                                    # 如果大图片下载到缓存都失败，降级为下载到临时文件
                                    for url_item in all_urls:
                                        image_file = await self._download_image_to_file(session, url_item, index=idx)
                                        if image_file:
                                            break
                                if image_file:
                                    image_files.append(image_file)
                        if not image_files:
                            return None
                        parse_result = {
                            "video_url": url,
                            "title": title or "快手图集",
                            "author": author,
                            "timestamp": upload_time or "",
                            "images": images,
                            "image_files": image_files,
                            "is_gallery": True
                        }
                        return parse_result
                json_match = re.search(r'<script[^>]*>window\.rawData\s*=\s*({.*?});?</script>', html, re.DOTALL)
                if json_match:
                    try:
                        data = json.loads(json_match.group(1))
                        if 'video' in data:
                            vurl = data['video'].get('url') or data['video'].get('srcNoMark')
                            if vurl and '.mp4' in vurl:
                                video_url = self._min_mp4(vurl)
                                if not await self.check_media_size(video_url, session, is_video=True):
                                    return None
                                upload_time = self._extract_upload_time(video_url)
                                return {
                                    "video_url": url,
                                    "title": title,
                                    "author": author,
                                    "timestamp": upload_time or "",
                                    "direct_url": video_url
                                }
                        elif 'photo' in data and data.get('type') == 1:
                            cdn_raw = data['photo'].get('cdn', ['p3.a.yximgs.com'])
                            if isinstance(cdn_raw, list):
                                cdns = cdn_raw if len(cdn_raw) > 0 else ['p3.a.yximgs.com']
                            elif isinstance(cdn_raw, str):
                                cdns = [cdn_raw]
                            else:
                                cdns = ['p3.a.yximgs.com']
                            music = data['photo'].get('music')
                            img_list = data['photo'].get('list', [])
                            album_data = self._build_album(cdns, music, img_list)
                            if album_data:
                                images = album_data.get('images', [])
                                image_url_lists = album_data.get('image_url_lists', [])
                                if images:
                                    image_files = []
                                    image_url = self._extract_album_image_url(html)
                                    upload_time = self._extract_upload_time(image_url) if image_url else None
                                    # 优先检查是否开启预下载，避免重复下载
                                    if self.pre_download_all_media and self.cache_dir_available:
                                        media_items = []
                                        for idx, img_url in enumerate(images):
                                            if img_url and isinstance(img_url, str) and img_url.startswith(('http://', 'https://')):
                                                image_size = await self.get_image_size(img_url, session)
                                                if self.max_media_size_mb > 0 and image_size is not None:
                                                    if image_size > self.max_media_size_mb:
                                                        continue
                                                image_id = self._extract_media_id(url)
                                                # 添加备用URL列表
                                                backup_urls = []
                                                if idx < len(image_url_lists) and image_url_lists[idx]:
                                                    backup_urls = image_url_lists[idx][1:] if len(image_url_lists[idx]) > 1 else []
                                                media_items.append({
                                                    'url': img_url,
                                                    'media_id': image_id,
                                                    'index': idx,
                                                    'is_video': False,
                                                    'headers': self.headers,
                                                    'backup_urls': backup_urls
                                                })
                                        if media_items:
                                            # 先尝试预下载主URL
                                            download_results = await self._pre_download_media(session, media_items, self.headers)
                                            # 对于失败的下载，尝试备用URL
                                            for idx, download_result in enumerate(download_results):
                                                if download_result.get('success') and download_result.get('file_path'):
                                                    image_files.append(download_result['file_path'])
                                                else:
                                                    # 主URL下载失败，尝试备用URL
                                                    item = media_items[idx]
                                                    backup_urls = item.get('backup_urls', [])
                                                    if backup_urls:
                                                        image_id = self._extract_media_id(url)
                                                        cache_path = await self._retry_download_with_backup_urls(
                                                            session,
                                                            download_result,
                                                            backup_urls,
                                                            image_id,
                                                            item['index'],
                                                            headers=self.headers,
                                                            default_referer='https://www.kuaishou.com/'
                                                        )
                                                        if cache_path:
                                                            image_files.append(cache_path)
                                            if image_files:
                                                images = []
                                    else:
                                        # 未开启预下载，使用原有逻辑
                                        for idx, primary_url in enumerate(images):
                                            if not primary_url or not isinstance(primary_url, str) or not primary_url.startswith(('http://', 'https://')):
                                                continue
                                            image_size = await self.get_image_size(primary_url, session)
                                            if self.max_media_size_mb > 0 and image_size is not None:
                                                if image_size > self.max_media_size_mb:
                                                    continue
                                            backup_urls = []
                                            if idx < len(image_url_lists) and image_url_lists[idx]:
                                                backup_urls = image_url_lists[idx][1:]
                                                all_urls = image_url_lists[idx]
                                            else:
                                                all_urls = [primary_url]
                                            image_file = None
                                            if self.large_media_threshold_mb > 0 and image_size is not None and image_size > self.large_media_threshold_mb:
                                                if self.max_media_size_mb <= 0 or image_size <= self.max_media_size_mb:
                                                    image_id = self._extract_media_id(url)
                                                    # 先尝试主URL下载到缓存
                                                    image_file = await self._download_large_media_to_cache(
                                                        session,
                                                        primary_url,
                                                        image_id,
                                                        index=idx,
                                                        headers=self.headers,
                                                        is_video=False,
                                                        referer=url
                                                    )
                                                    # 如果主URL下载到缓存失败，尝试备用URL下载到缓存
                                                    if not image_file and backup_urls:
                                                        for backup_url in backup_urls:
                                                            image_file = await self._download_large_media_to_cache(
                                                                session,
                                                                backup_url,
                                                                image_id,
                                                                index=idx,
                                                                headers=self.headers,
                                                                is_video=False,
                                                                referer=url
                                                            )
                                                            if image_file:
                                                                break
                                            if not image_file:
                                                # 如果大图片下载到缓存都失败，降级为下载到临时文件
                                                for url_item in all_urls:
                                                    image_file = await self._download_image_to_file(session, url_item, index=idx)
                                                    if image_file:
                                                        break
                                            if image_file:
                                                image_files.append(image_file)
                                    if not image_files:
                                        return None
                                    parse_result = {
                                        "video_url": url,
                                        "title": title or "快手图集",
                                        "author": author,
                                        "timestamp": upload_time or "",
                                        "images": images,
                                        "image_files": image_files,
                                        "is_gallery": True
                                    }
                                    return parse_result
                    except json.JSONDecodeError:
                        pass
                if metadata.get('userName') or metadata.get('userId') or metadata.get('caption'):
                    return {
                        "video_url": url,
                        "title": title,
                        "author": author,
                        "timestamp": "",
                        "direct_url": None
                    }
                return None
            except Exception:
                return None

    def build_media_nodes(self, result: Dict[str, Any], sender_name: str, sender_id: Any, is_auto_pack: bool) -> List:
        """
        构建媒体节点（视频或图片）
        优先使用下载的图片文件，避免发送时下载失败
        使用下载的图片文件而不是URL，以避免QQ/NapCat无法识别文件类型的问题
        如果解析结果中有 video_files（大视频已下载到缓存目录），优先使用文件方式构建节点
        Args:
            result: 解析结果
            sender_name: 发送者名称
            sender_id: 发送者ID
            is_auto_pack: 是否打包为Node
        Returns:
            List: 媒体节点列表
        """
        nodes = []
        if result.get('video_files'):
            return self._build_video_gallery_nodes_from_files(
                result['video_files'],
                sender_name,
                sender_id,
                is_auto_pack
            )
        if result.get('is_gallery') and result.get('image_files'):
            gallery_nodes = self._build_gallery_nodes_from_files(
                result['image_files'],
                sender_name,
                sender_id,
                is_auto_pack
            )
            nodes.extend(gallery_nodes)
        elif result.get('is_gallery') and result.get('images'):
            gallery_nodes = self._build_gallery_nodes_from_urls(
                result['images'],
                sender_name,
                sender_id,
                is_auto_pack
            )
            nodes.extend(gallery_nodes)
        elif result.get('direct_url'):
            video_node = self._build_video_node_from_url(
                result['direct_url'],
                sender_name,
                sender_id,
                is_auto_pack,
                result.get('thumb_url')
            )
            if video_node:
                nodes.append(video_node)
        return nodes
