import asyncio
import json
import re
from datetime import datetime
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse

import aiohttp

from ...logger import logger

from .base import BaseVideoParser
from ..utils import build_request_headers, is_live_url, SkipParse
from ...constants import Config

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

    def __init__(self):
        """初始化快手解析器"""
        super().__init__("kuaishou")
        self.headers = MOBILE_HEADERS
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)

    def can_parse(self, url: str) -> bool:
        """判断是否可以解析此URL

        Args:
            url: 视频链接

        Returns:
            是否可以解析
        """
        if not url:
            logger.debug(f"[{self.name}] can_parse: URL为空")
            return False
        url_lower = url.lower()
        if 'kuaishou.com' in url_lower or 'kspkg.com' in url_lower:
            logger.debug(f"[{self.name}] can_parse: 匹配快手链接 {url}")
            return True
        logger.debug(f"[{self.name}] can_parse: 无法解析 {url}")
        return False

    def extract_links(self, text: str) -> List[str]:
        """从文本中提取快手链接

        Args:
            text: 输入文本

        Returns:
            快手链接列表
        """
        result_links_set = set()
        
        short_pattern = r'https?://v\.kuaishou\.com/[^\s]+'
        short_links = re.findall(short_pattern, text)
        result_links_set.update(short_links)
        
        long_pattern = r'https?://(?:www\.)?kuaishou\.com/[^\s]+'
        long_links = re.findall(long_pattern, text)
        result_links_set.update(long_links)
        
        result = list(result_links_set)
        if result:
            logger.debug(f"[{self.name}] extract_links: 提取到 {len(result)} 个链接: {result[:3]}{'...' if len(result) > 3 else ''}")
        else:
            logger.debug(f"[{self.name}] extract_links: 未提取到链接")
        return result

    def _min_mp4(self, url: str) -> str:
        """处理MP4 URL，提取最小格式

        Args:
            url: 原始URL

        Returns:
            处理后的URL
        """
        pu = urlparse(url)
        domain = pu.netloc
        filename = pu.path.split('/')[-1].split('?')[0]
        path_wo_file = '/'.join(pu.path.split('/')[1:-1])
        return f"https://{domain}/{path_wo_file}/{filename}"

    def _extract_upload_time(self, url: str) -> Optional[str]:
        """从URL中提取上传时间

        Args:
            url: 视频或图片URL

        Returns:
            上传时间字符串（YYYY-MM-DD格式），无法提取时为None
        """
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
        """提取用户名、UID、标题

        Args:
            html: HTML内容

        Returns:
            包含userName、userId、caption的字典
        """
        metadata = {'userName': None, 'userId': None, 'caption': None}
        json_match = re.search(
            r'window\.INIT_STATE\s*=\s*({.*?});',
            html,
            re.DOTALL
        )
        if not json_match:
            json_match = re.search(
                r'window\.__APOLLO_STATE__\s*=\s*({.*?});',
                html,
                re.DOTALL
            )
        if json_match:
            try:
                json_str = json_match.group(1)
                user_match = re.search(
                    r'"userName"\s*:\s*"([^"]+)"',
                    json_str
                )
                if user_match:
                    metadata['userName'] = user_match.group(1)
                uid_match = re.search(
                    r'"userId"\s*:\s*["\']?(\d+)["\']?',
                    json_str
                )
                if uid_match:
                    metadata['userId'] = uid_match.group(1)
                caption_match = re.search(
                    r'"caption"\s*:\s*"([^"]*(?:\\.[^"]*)*)"',
                    json_str
                )
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
            title_match = re.search(
                r'<title[^>]*>(.*?)</title>',
                html,
                re.IGNORECASE
            )
            if title_match:
                metadata['caption'] = title_match.group(1).strip()
        return metadata

    def _extract_album_image_url(self, html: str) -> Optional[str]:
        """提取图集图片URL

        Args:
            html: HTML内容

        Returns:
            图片URL，无法提取时为None
        """
        match = re.search(r'<img\s+class="image"\s+src="([^"]+)"', html)
        if match:
            return match.group(1).split('?')[0]
        match = re.search(
            r'src="(https?://[^"]*?/upic/[^"]*?\.jpg)',
            html
        )
        if match:
            return match.group(1)
        return None

    def _build_album(
        self,
        cdns: List[str],
        music_path: Optional[str],
        img_paths: List[str]
    ) -> Dict[str, Any]:
        """构建图集数据，支持多个CDN

        Args:
            cdns: CDN列表
            music_path: 音乐路径
            img_paths: 图片路径列表

        Returns:
            包含images和image_url_lists的字典，构建失败时为None
        """
        cleaned_cdns = [
            re.sub(r'https?://', '', cdn) for cdn in cdns if cdn
        ]
        if not cleaned_cdns:
            return None
        cleaned_paths = [
            p.strip('"') for p in img_paths if p.strip('"')
        ]
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
                url_list = (
                    image_url_lists[idx].copy()
                    if image_url_lists[idx]
                    else []
                )
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
        """解析图集，提取所有CDN

        Args:
            html: HTML内容

        Returns:
            包含images和image_url_lists的字典，解析失败时为None
        """
        cdn_matches = re.findall(
            r'"cdnList"\s*:\s*\[.*?"cdn"\s*:\s*"([^"]+)"',
            html,
            re.DOTALL
        )
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
        m = re.search(
            r'"music"\s*:\s*"(/ufile/atlas/[^"]+?\.m4a)"',
            html
        )
        music_path = m.group(1) if m else None
        return self._build_album(cdns, music_path, img_paths)

    def _parse_video(self, html: str) -> Optional[str]:
        """解析视频URL

        Args:
            html: HTML内容

        Returns:
            视频URL，解析失败时为None
        """
        m = re.search(
            r'"(url|srcNoMark|photoUrl|videoUrl)"\s*:\s*"'
            r'(https?://[^"]+?\.mp4[^"]*)"',
            html
        )
        if not m:
            m = re.search(
                r'"url"\s*:\s*"(https?://[^"]+?\.mp4[^"]*)"',
                html
            )
        if m:
            return self._min_mp4(m.group(2))
        return None


    async def _fetch_html(
        self,
        session: aiohttp.ClientSession,
        url: str
    ) -> Optional[str]:
        """获取HTML内容（处理短链）

        Args:
            session: aiohttp会话
            url: 快手链接

        Returns:
            HTML内容，获取失败时为None
        """
        is_short = 'v.kuaishou.com' in urlparse(url).netloc
        if is_short:
            async with session.get(
                url,
                headers=self.headers,
                allow_redirects=False
            ) as r1:
                if r1.status != 302:
                    return None
                loc = r1.headers.get('Location')
                if not loc:
                    return None
            if is_live_url(loc):
                logger.debug(f"[{self.name}] _fetch_html: 短链重定向到直播域名，跳过解析 {url} -> {loc}")
                raise SkipParse("直播域名链接不解析")
            async with session.get(loc, headers=self.headers) as r2:
                if r2.status != 200:
                    return None
                return await r2.text()
        else:
            if is_live_url(url):
                logger.debug(f"[{self.name}] _fetch_html: 检测到直播域名链接，跳过解析 {url}")
                raise SkipParse("直播域名链接不解析")
            async with session.get(url, headers=self.headers) as r:
                if r.status != 200:
                    return None
                return await r.text()

    def _build_author_info(
        self,
        metadata: Dict[str, Optional[str]]
    ) -> str:
        """构建作者信息

        Args:
            metadata: 元数据字典

        Returns:
            作者信息字符串
        """
        userName = metadata.get('userName', '')
        userId = metadata.get('userId', '')
        if userName and userId:
            return f"{userName}(uid:{userId})"
        elif userName:
            return userName
        elif userId:
            return f"(uid:{userId})"
        else:
            return ""

    def _parse_rawdata_json(self, html: str) -> Optional[Dict[str, Any]]:
        """解析rawData JSON数据

        Args:
            html: HTML内容

        Returns:
            解析后的数据，解析失败时为None
        """
        json_match = re.search(
            r'<script[^>]*>window\.rawData\s*=\s*({.*?});?</script>',
            html,
            re.DOTALL
        )
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                return None
        return None


    async def parse(
        self,
        session: aiohttp.ClientSession,
        url: str
    ) -> Optional[Dict[str, Any]]:
        """解析单个快手链接

        Args:
            session: aiohttp会话
            url: 快手链接

        Returns:
            解析结果字典，包含标准化的元数据格式

        Raises:
            RuntimeError: 当解析失败时
        """
        logger.debug(f"[{self.name}] parse: 开始解析 {url}")
        async with self.semaphore:
            html = await self._fetch_html(session, url)
            if not html:
                logger.debug(f"[{self.name}] parse: 无法获取HTML内容 {url}")
                raise RuntimeError(f"无法获取HTML内容: {url}")

            logger.debug(f"[{self.name}] parse: HTML获取成功，开始提取元数据")
            metadata = self._extract_metadata(html)
            author = self._build_author_info(metadata)
            title = metadata.get('caption', '') or "快手视频"
            if len(title) > 100:
                title = title[:100]

            video_url = self._parse_video(html)
            if video_url:
                logger.debug(f"[{self.name}] parse: 检测到视频")
                upload_time = self._extract_upload_time(video_url)
                user_agent = (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/120.0.0.0 Safari/537.36'
                )
                referer = "https://www.kuaishou.com/"
                image_headers = build_request_headers(
                    is_video=False,
                    referer=referer,
                    user_agent=user_agent
                )
                video_headers = build_request_headers(
                    is_video=True,
                    referer=referer,
                    user_agent=user_agent
                )
                result_dict = {
                    "url": url,
                    "title": title,
                    "author": author,
                    "desc": "",
                    "timestamp": upload_time or "",
                    "video_urls": [[video_url]],
                    "image_urls": [],
                    "image_headers": image_headers,
                    "video_headers": video_headers,
                }
                logger.debug(f"[{self.name}] parse: 解析完成(视频) {url}, title={title[:50]}")
                return result_dict

            album = self._parse_album(html)
            if album:
                image_url_lists = album.get('image_url_lists', [])
                if image_url_lists:
                    image_url = self._extract_album_image_url(html)
                    upload_time = (
                        self._extract_upload_time(image_url)
                        if image_url
                        else None
                    )
                    user_agent = (
                        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/120.0.0.0 Safari/537.36'
                    )
                    referer = "https://www.kuaishou.com/"
                    image_headers = build_request_headers(
                        is_video=False,
                        referer=referer,
                        user_agent=user_agent
                    )
                    video_headers = build_request_headers(
                        is_video=True,
                        referer=referer,
                        user_agent=user_agent
                    )
                    result_dict = {
                        "url": url,
                        "title": title or "快手图集",
                        "author": author,
                        "desc": "",
                        "timestamp": upload_time or "",
                        "video_urls": [],
                        "image_urls": image_url_lists,
                        "image_headers": image_headers,
                        "video_headers": video_headers,
                    }
                    logger.debug(f"[{self.name}] parse: 解析完成(图片集) {url}, title={title[:50] if title else '快手图集'}, image_count={len(image_url_lists)}")
                    return result_dict

            rawdata = self._parse_rawdata_json(html)
            if rawdata:
                if 'video' in rawdata:
                    vurl = rawdata['video'].get('url') or rawdata['video'].get('srcNoMark')
                    if vurl and '.mp4' in vurl:
                        video_url = self._min_mp4(vurl)
                        upload_time = self._extract_upload_time(video_url)
                        user_agent = (
                            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                            'AppleWebKit/537.36 (KHTML, like Gecko) '
                            'Chrome/120.0.0.0 Safari/537.36'
                        )
                        referer = "https://www.kuaishou.com/"
                        image_headers = build_request_headers(
                            is_video=False,
                            referer=referer,
                            user_agent=user_agent
                        )
                        video_headers = build_request_headers(
                            is_video=True,
                            referer=referer,
                            user_agent=user_agent
                        )
                        return {
                            "url": url,
                            "title": title,
                            "author": author,
                            "desc": "",
                            "timestamp": upload_time or "",
                            "video_urls": [[video_url]],
                            "image_urls": [],
                            "image_headers": image_headers,
                            "video_headers": video_headers,
                        }
                
                if 'photo' in rawdata and rawdata.get('type') == 1:
                    cdn_raw = rawdata['photo'].get('cdn', ['p3.a.yximgs.com'])
                    if isinstance(cdn_raw, list):
                        cdns = cdn_raw if len(cdn_raw) > 0 else ['p3.a.yximgs.com']
                    elif isinstance(cdn_raw, str):
                        cdns = [cdn_raw]
                    else:
                        cdns = ['p3.a.yximgs.com']
                    
                    img_paths = rawdata['photo'].get('path', [])
                    if isinstance(img_paths, str):
                        img_paths = [img_paths]
                    
                    music_path = rawdata['photo'].get('music')
                    album_data = self._build_album(cdns, music_path, img_paths)
                    if album_data:
                        image_url_lists = album_data.get('image_url_lists', [])
                        if image_url_lists:
                            upload_time = None
                            if image_url_lists[0] and image_url_lists[0][0]:
                                upload_time = self._extract_upload_time(image_url_lists[0][0])
                            user_agent = (
                                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                                'AppleWebKit/537.36 (KHTML, like Gecko) '
                                'Chrome/120.0.0.0 Safari/537.36'
                            )
                            referer = "https://www.kuaishou.com/"
                            image_headers = build_request_headers(
                                is_video=False,
                                referer=referer,
                                user_agent=user_agent
                            )
                            video_headers = build_request_headers(
                                is_video=True,
                                referer=referer,
                                user_agent=user_agent
                            )
                            return {
                                "url": url,
                                "title": title or "快手图集",
                                "author": author,
                                "desc": "",
                                "timestamp": upload_time or "",
                                "video_urls": [],
                                "image_urls": image_url_lists,
                                "image_headers": image_headers,
                                "video_headers": video_headers,
                            }

            if (metadata.get('userName') or
                    metadata.get('userId') or
                    metadata.get('caption')):
                raise RuntimeError(f"无法获取媒体URL: {url}")

            raise RuntimeError(f"无法解析此URL: {url}")

