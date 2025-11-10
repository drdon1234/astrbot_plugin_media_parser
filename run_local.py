# -*- coding: utf-8 -*-
"""
本地测试脚本
用于测试视频链接解析功能
"""
import sys
import os
import importlib.util
import types
import re

# 获取项目根目录
_project_root = os.path.dirname(os.path.abspath(__file__))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# 包名称
_package_name = "astrbot_plugin_video_parser"

# 创建包模块结构
def _setup_package_structure():
    """设置包结构以支持相对导入"""
    # 创建主包
    if _package_name not in sys.modules:
        _main_package = types.ModuleType(_package_name)
        _main_package.__path__ = [_project_root]
        _main_package.__file__ = os.path.join(_project_root, "__init__.py")
        sys.modules[_package_name] = _main_package
    
    # 创建 parsers 子包
    _parsers_pkg_name = f"{_package_name}.parsers"
    _parsers_dir = os.path.join(_project_root, "parsers")
    
    if _parsers_pkg_name not in sys.modules:
        _parsers_package = types.ModuleType(_parsers_pkg_name)
        _parsers_package.__path__ = [_parsers_dir]
        _parsers_package.__file__ = os.path.join(_parsers_dir, "__init__.py")
        _parsers_package.__package__ = _parsers_pkg_name
        sys.modules[_parsers_pkg_name] = _parsers_package
        setattr(sys.modules[_package_name], "parsers", _parsers_package)
    
    # 加载 base_parser 模块
    _base_parser_file = os.path.join(_parsers_dir, "base_parser.py")
    if os.path.exists(_base_parser_file):
        _base_parser_spec = importlib.util.spec_from_file_location(
            f"{_parsers_pkg_name}.base_parser", _base_parser_file
        )
        _base_parser_module = importlib.util.module_from_spec(_base_parser_spec)
        _base_parser_module.__package__ = _parsers_pkg_name
        sys.modules[f"{_parsers_pkg_name}.base_parser"] = _base_parser_module
        _base_parser_spec.loader.exec_module(_base_parser_module)
        setattr(sys.modules[_parsers_pkg_name], "base_parser", _base_parser_module)
    
    # 加载所有解析器模块
    _parser_files = {
        "bilibili": "bilibili.py",
        "douyin": "douyin.py",
        "twitter": "twitter.py",
        "kuaishou": "kuaishou.py",
    }
    
    for _parser_name, _parser_file in _parser_files.items():
        _parser_path = os.path.join(_parsers_dir, _parser_file)
        if os.path.exists(_parser_path):
            _parser_module_name = f"{_parsers_pkg_name}.{_parser_name}"
            _parser_spec = importlib.util.spec_from_file_location(
                _parser_module_name, _parser_path
            )
            _parser_module = importlib.util.module_from_spec(_parser_spec)
            _parser_module.__package__ = _parsers_pkg_name
            sys.modules[_parser_module_name] = _parser_module
            _parser_spec.loader.exec_module(_parser_module)
            setattr(sys.modules[_parsers_pkg_name], _parser_name, _parser_module)
    
    # 加载 parsers/__init__.py（必须在所有解析器模块加载后）
    _parsers_init_file = os.path.join(_parsers_dir, "__init__.py")
    if os.path.exists(_parsers_init_file):
        _parsers_init_spec = importlib.util.spec_from_file_location(
            _parsers_pkg_name, _parsers_init_file
        )
        _parsers_init_module = importlib.util.module_from_spec(_parsers_init_spec)
        _parsers_init_module.__package__ = _parsers_pkg_name
        sys.modules[_parsers_pkg_name] = _parsers_init_module
        _parsers_init_spec.loader.exec_module(_parsers_init_module)
        # 将 parsers 模块添加到主包
        setattr(sys.modules[_package_name], "parsers", _parsers_init_module)
    
    # 加载 parser_manager 模块
    # 使用 importlib 加载，确保相对导入能够工作
    _parser_manager_file = os.path.join(_project_root, "parser_manager.py")
    if os.path.exists(_parser_manager_file):
        _parser_manager_spec = importlib.util.spec_from_file_location(
            "parser_manager", _parser_manager_file
        )
        _parser_manager_module = importlib.util.module_from_spec(_parser_manager_spec)
        _parser_manager_module.__package__ = _package_name
        # 确保模块可以访问包
        _parser_manager_module.__dict__[_package_name] = sys.modules[_package_name]
        sys.modules["parser_manager"] = _parser_manager_module
        _parser_manager_spec.loader.exec_module(_parser_manager_module)

# 设置包结构
_setup_package_structure()

# 现在可以正常导入
import asyncio
import aiohttp
import shutil
import traceback
import json
from urllib.parse import urlparse
from typing import Optional, Dict, Any

# 导入解析器管理器
from parser_manager import ParserManager
from parsers import BilibiliParser, DouyinParser, TwitterParser, KuaishouParser


class LocalTestDouyinParser(DouyinParser):
    """本地测试用的抖音解析器包装器，解析时不下载文件，只保存URL信息"""

    async def parse(self, session: aiohttp.ClientSession, url: str) -> Optional[Dict[str, Any]]:
        """
        解析抖音链接，但不下载文件，只保存URL信息
        Args:
            session: aiohttp会话
            url: 抖音链接
        Returns:
            解析结果字典，包含视频/图片URL但不包含文件路径
        """
        try:
            redirected_url = await self.get_redirected_url(session, url)
            
            is_note = '/note/' in redirected_url or '/note/' in url
            note_id = None
            if is_note:
                note_match = re.search(r'/note/(\d+)', redirected_url)
                if not note_match:
                    note_match = re.search(r'/note/(\d+)', url)
                if note_match:
                    note_id = note_match.group(1)
                    result = await self.fetch_video_info(session, note_id, is_note=True)
                else:
                    return None
            else:
                video_match = re.search(r'/video/(\d+)', redirected_url)
                if video_match:
                    video_id = video_match.group(1)
                    result = await self.fetch_video_info(session, video_id, is_note=False)
                else:
                    match = re.search(r'(\d{19})', redirected_url)
                    if match:
                        item_id = match.group(1)
                        result = await self.fetch_video_info(session, item_id, is_note=False)
                    else:
                        return None
            if not result:
                return None
            
            is_gallery = result.get('is_gallery', False)
            images = result.get('images', [])
            image_url_lists = result.get('image_url_lists', [])
            
            if is_gallery and images:
                # 图集：只返回URL，不下载
                if is_note and note_id:
                    display_url = f"https://www.douyin.com/note/{note_id}"
                else:
                    display_url = url
                parse_result = {
                    "video_url": display_url,
                    "title": result.get('title', ''),
                    "author": result.get('author', result.get('nickname', '')),
                    "timestamp": result.get('timestamp', ''),
                    "thumb_url": result.get('thumb_url'),
                    "images": images,
                    "image_url_lists": image_url_lists,
                    "is_gallery": True
                }
                return parse_result
            
            video_url = result.get('video_url')
            if video_url:
                # 本地测试版本跳过文件大小检查以加快速度
                video_size = None  # 跳过文件大小检查
                if self.max_media_size_mb > 0 and video_size is not None:
                    if video_size > self.max_media_size_mb:
                        return None
                parse_result = {
                    "video_url": url,
                    "title": result.get('title', ''),
                    "author": result.get('author', result.get('nickname', '')),
                    "timestamp": result.get('timestamp', ''),
                    "thumb_url": result.get('thumb_url'),
                    "direct_url": video_url,
                    "file_size_mb": video_size
                }
                return parse_result
            return None
        except Exception as e:
            raise RuntimeError(f"解析失败：{str(e)}")


class LocalTestKuaishouParser(KuaishouParser):
    """本地测试用的快手解析器包装器，解析时不下载文件，只保存URL信息"""

    async def parse(self, session: aiohttp.ClientSession, url: str) -> Optional[Dict[str, Any]]:
        """
        解析快手链接，但不下载文件，只保存URL信息
        Args:
            session: aiohttp会话
            url: 快手链接
        Returns:
            解析结果字典，包含视频/图片URL但不包含文件路径
        """
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
                # 本地测试版本跳过文件大小检查以加快速度
                video_size = None  # 跳过文件大小检查
                if self.max_media_size_mb > 0 and video_size is not None:
                    if video_size > self.max_media_size_mb:
                        return None
                upload_time = self._extract_upload_time(video_url)
                parse_result = {
                    "video_url": url,
                    "title": title,
                    "author": author,
                    "timestamp": upload_time or "",
                    "direct_url": video_url,
                    "file_size_mb": video_size
                }
                return parse_result
            
            album = self._parse_album(html)
            if album:
                images = album.get('images', [])
                image_url_lists = album.get('image_url_lists', [])
                if images:
                    image_url = self._extract_album_image_url(html)
                    upload_time = self._extract_upload_time(image_url) if image_url else None
                    parse_result = {
                        "video_url": url,
                        "title": title or "快手图集",
                        "author": author,
                        "timestamp": upload_time or "",
                        "images": images,
                        "image_url_lists": image_url_lists,
                        "is_gallery": True
                    }
                    return parse_result
            
            # 尝试从 rawData 中解析
            json_match = re.search(r'<script[^>]*>window\.rawData\s*=\s*({.*?});?</script>', html, re.DOTALL)
            if json_match:
                try:
                    data = json.loads(json_match.group(1))
                    if 'video' in data:
                        vurl = data['video'].get('url') or data['video'].get('srcNoMark')
                        if vurl and '.mp4' in vurl:
                            video_url = self._min_mp4(vurl)
                            # 跳过文件大小检查
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
                                image_url = self._extract_album_image_url(html)
                                upload_time = self._extract_upload_time(image_url) if image_url else None
                                parse_result = {
                                    "video_url": url,
                                    "title": title or "快手图集",
                                    "author": author,
                                    "timestamp": upload_time or "",
                                    "images": images,
                                    "image_url_lists": image_url_lists,
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
        except Exception as e:
            raise RuntimeError(f"解析失败：{str(e)}")


class LocalTestTwitterParser(TwitterParser):
    """本地测试用的Twitter解析器包装器，解析时不下载文件，只保存URL信息"""

    async def parse(self, session: aiohttp.ClientSession, url: str) -> Optional[Dict[str, Any]]:
        """
        解析Twitter链接，但不下载文件，只保存URL信息
        Args:
            session: aiohttp会话
            url: Twitter链接
        Returns:
            解析结果字典，包含视频/图片URL但不包含文件路径
        """
        try:
            tweet_id_match = re.search(r'/status/(\d+)', url)
            if not tweet_id_match:
                return None
            tweet_id = tweet_id_match.group(1)
            media_info = await self._fetch_media_info(session, tweet_id)
            
            if not media_info.get('images') and not media_info.get('videos'):
                raise RuntimeError("解析失败：推文不包含图片或视频")
            
            video_files = []
            has_large_video = False
            max_video_size = None
            
            # 处理视频：只保存URL信息，不下载
            if media_info.get('videos'):
                for idx, video_info in enumerate(media_info['videos']):
                    video_url = video_info.get('url')
                    if video_url:
                        # 本地测试版本跳过文件大小检查以加快速度
                        video_size = None  # 跳过文件大小检查
                        if self.max_media_size_mb > 0 and video_size is not None:
                            if video_size > self.max_media_size_mb:
                                continue
                        exceeds_large_threshold = False
                        if self.large_media_threshold_mb > 0 and video_size is not None and video_size > self.large_media_threshold_mb:
                            if self.max_media_size_mb <= 0 or video_size <= self.max_media_size_mb:
                                exceeds_large_threshold = True
                                has_large_video = True
                        # 不下载文件，只保存URL信息
                        video_files.append({
                            'url': video_url,
                            'thumbnail': video_info.get('thumbnail', ''),
                            'duration': video_info.get('duration', 0),
                            'exceeds_large_threshold': exceeds_large_threshold,
                            'file_size_mb': video_size
                        })
                        if video_size is not None:
                            if max_video_size is None or video_size > max_video_size:
                                max_video_size = video_size
            
            # 处理图片：只保存URL信息，不下载
            image_urls = []
            if media_info.get('images'):
                image_urls = media_info['images']
            
            if not video_files and not image_urls:
                raise RuntimeError("解析失败：无有效的媒体内容")
            
            result = {
                "video_url": url,
                "title": media_info.get('text', '')[:100] or "Twitter 推文",
                "author": media_info.get('author', ''),
                "desc": media_info.get('text', ''),
            }
            
            if video_files:
                result['video_files'] = video_files
                result['is_twitter_video'] = True
                result['has_large_video'] = has_large_video
                if has_large_video:
                    result['force_separate_send'] = True
                if max_video_size is not None:
                    result['file_size_mb'] = max_video_size
                # 保存第一个视频的URL作为direct_url，用于下载
                if video_files:
                    result['direct_url'] = video_files[0].get('url')
            
            if image_urls:
                result['images'] = image_urls
                result['is_twitter_images'] = True
                if len(image_urls) > 1:
                    result['is_gallery'] = True
            
            return result
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"解析失败：{str(e)}")


def init_parsers(use_proxy=False, proxy_url=None):
    """
    初始化解析器
    Args:
        use_proxy: 是否使用代理（Twitter/X需要代理才能访问）
        proxy_url: 代理地址（格式：http://host:port 或 socks5://host:port）
    Returns:
        ParserManager: 解析器管理器
    """
    # 本地测试时，所有解析器都不使用缓存目录
    # 解析结果只保存在内存中，包含URL信息
    # 用户选择下载时，再从URL下载到download目录
    
    # 如果提供了代理，默认同时用于图片和视频（测试用）
    use_image_proxy = use_proxy
    use_video_proxy = use_proxy
    
    parsers = [
        BilibiliParser(
            max_media_size_mb=0.0,
            large_media_threshold_mb=50.0,
            cache_dir=None
        ),
        LocalTestDouyinParser(
            max_media_size_mb=0.0,
            large_media_threshold_mb=50.0,
            cache_dir=None
        ),
        LocalTestTwitterParser(
            max_media_size_mb=0.0,
            large_media_threshold_mb=50.0,
            use_image_proxy=use_image_proxy,
            use_video_proxy=use_video_proxy,
            proxy_url=proxy_url,
            cache_dir=None
        ),
        LocalTestKuaishouParser(
            max_media_size_mb=0.0,
            large_media_threshold_mb=50.0,
            cache_dir=None
        )
    ]
    return ParserManager(parsers)


def print_metadata(result: dict, url: str, parser_name: str):
    """打印解析后的元数据"""
    print("\n" + "=" * 80)
    print(f"解析器: {parser_name} | 链接: {url}")
    print("-" * 80)
    print(f"标题: {result.get('title', 'N/A')}")
    print(f"作者: {result.get('author', 'N/A')}")
    
    if result.get('video_files'):
        print(f"\n视频: {len(result['video_files'])} 个")
        for idx, vf in enumerate(result['video_files'], 1):
            url_or_path = vf.get('url') or vf.get('direct_url') or vf.get('file_path', 'N/A')
            size = f" ({vf.get('file_size_mb', 0):.2f} MB)" if vf.get('file_size_mb') else ""
            print(f"  [{idx}] {url_or_path}{size}")
    
    if result.get('images'):
        image_count = len(result['images'])
        is_gallery = result.get('is_gallery', False)
        gallery_type = "图集" if is_gallery else "图片"
        print(f"\n{gallery_type}: {image_count} 张")
        image_url_lists = result.get('image_url_lists', [])
        for idx, img_url in enumerate(result['images'][:5], 1):
            backup_count = 0
            if idx <= len(image_url_lists) and image_url_lists[idx - 1]:
                backup_count = len(image_url_lists[idx - 1]) - 1
            backup_info = f" (备用URL: {backup_count}个)" if backup_count > 0 else ""
            print(f"  [{idx}] {img_url}{backup_info}")
        if len(result['images']) > 5:
            print(f"  ... 还有 {len(result['images']) - 5} 张")
    
    if result.get('direct_url'):
        print(f"\n直链: {result.get('direct_url')}")
    
    print("=" * 80)


async def download_file(session: aiohttp.ClientSession, url: str, filepath: str, referer: str = None, proxy: str = None, headers: dict = None, max_retries: int = 2, retry_delay: float = 0.5) -> bool:
    """
    下载文件到指定路径（带重试机制）
    Args:
        session: aiohttp会话
        url: 文件URL
        filepath: 保存路径
        referer: Referer请求头
        proxy: 代理地址（格式：http://host:port 或 socks5://host:port）
        headers: 自定义请求头（如果提供，会与默认请求头合并）
        max_retries: 最大重试次数，默认2次
        retry_delay: 重试延迟（秒），默认0.5秒，使用指数退避
    Returns:
        bool: 下载是否成功
    """
    # 默认请求头
    default_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Sec-Fetch-Dest': 'image',
        'Sec-Fetch-Mode': 'no-cors',
        'Sec-Fetch-Site': 'cross-site',
    }
    
    # 合并自定义请求头
    if headers:
        default_headers.update(headers)
    
    # 设置Referer
    if referer:
        default_headers['Referer'] = referer
    
    # 对于Twitter图片/视频，如果没有提供Referer，使用默认值
    if 'pbs.twimg.com' in url or 'video.twimg.com' in url:
        if 'Referer' not in default_headers:
            default_headers['Referer'] = 'https://x.com/'
    
    # 判断是否为视频（根据URL或文件扩展名）
    is_video = '.mp4' in url.lower() or filepath.endswith('.mp4')
    
    for attempt in range(max_retries + 1):
        file_path = None
        try:
            if is_video:
                timeout = aiohttp.ClientTimeout(
                    total=600,
                    connect=30,
                    sock_read=300
                )
            else:
                timeout = aiohttp.ClientTimeout(total=60)
            
            async with session.get(
                url, 
                headers=default_headers, 
                timeout=timeout,
                proxy=proxy
            ) as response:
                response.raise_for_status()
                file_dir = os.path.dirname(filepath)
                if file_dir:
                    os.makedirs(file_dir, exist_ok=True)
                chunk_size = 1024 * 1024
                with open(filepath, 'wb') as f:
                    async for chunk in response.content.iter_chunked(chunk_size):
                        f.write(chunk)
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except Exception:
                        pass
            return True
        except Exception:
            if os.path.exists(filepath):
                try:
                    os.unlink(filepath)
                except Exception:
                    pass
            if attempt < max_retries:
                await asyncio.sleep(retry_delay * (2 ** attempt))
                continue
            return False
    
    return False


async def download_media_concurrent(download_tasks: list, download_dir: str, session: aiohttp.ClientSession, max_concurrent: int = 5):
    """并发下载多个媒体文件"""
    os.makedirs(os.path.abspath(download_dir), exist_ok=True)
    if not download_tasks:
        return
    
    total_files = 0
    for r, _, _ in download_tasks:
        total_files += len(r.get('video_files', [])) or (1 if r.get('direct_url') else 0)
        total_files += len(r.get('images', [])) or len([img for img in r.get('image_files', []) if isinstance(img, str) and os.path.exists(img)])
    
    print(f"\n开始并发下载 {len(download_tasks)} 个链接内的媒体（共 {total_files} 个文件，最大并发数: {max_concurrent}）...")
    
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def download_single_media(result, url, parser):
        async with semaphore:
            return await download_media(result, url, download_dir, session, parser)
    
    tasks = [download_single_media(r, u, p) for r, u, p in download_tasks]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    success_count = sum(1 for r in results if r is True)
    failed_count = len(results) - success_count
    print(f"\n下载完成: 共 {len(results)} 个链接，失败 {failed_count} 个")
    if failed_count > 0:
        print("提示: 部分媒体下载失败，请检查网络连接和代理配置")


async def download_media(result: dict, url: str, download_dir: str, session: aiohttp.ClientSession, parser=None):
    """
    下载媒体文件
    Args:
        result: 解析结果
        url: 原始链接
        download_dir: 下载目录
        session: aiohttp会话
        parser: 解析器实例（用于获取代理设置等）
    Returns:
        bool: 下载是否成功
    """
    try:
        download_dir = os.path.abspath(download_dir)
        os.makedirs(download_dir, exist_ok=True)
        
        # 获取图片和视频代理
        image_proxy = None
        video_proxy = None
        download_headers = None
        if parser:
            # 获取图片代理（使用统一的代理地址）
            if hasattr(parser, 'use_image_proxy') and hasattr(parser, 'proxy_url') and parser.use_image_proxy and parser.proxy_url:
                image_proxy = parser.proxy_url
            # 获取视频代理（使用统一的代理地址）
            if hasattr(parser, 'use_video_proxy') and hasattr(parser, 'proxy_url') and parser.use_video_proxy and parser.proxy_url:
                video_proxy = parser.proxy_url
            # 兼容旧代码：如果没有新的代理设置，尝试使用旧的
            if not image_proxy and not video_proxy:
                if hasattr(parser, 'use_proxy') and hasattr(parser, 'proxy_url') and parser.use_proxy and parser.proxy_url:
                    image_proxy = parser.proxy_url
                    video_proxy = parser.proxy_url
            if hasattr(parser, 'headers'):
                download_headers = parser.headers.copy()

        parsed_url = urlparse(url)
        domain = parsed_url.netloc.replace('.', '_').replace(':', '_')
        path_parts = [p for p in parsed_url.path.strip('/').split('/') if p]
        if path_parts:
            path = re.sub(r'[^\w\-_]', '_', '_'.join(path_parts[:2]))[:30]
            link_dir_name = f"{domain}_{path}"
        else:
            link_dir_name = f"{domain}_root"
        
        link_dir = os.path.normpath(os.path.join(download_dir, link_dir_name))
        os.makedirs(link_dir, exist_ok=True)

        downloaded_files = []
        failed_count = 0
        video_urls = []
        
        if result.get('video_files'):
            for idx, vf in enumerate(result['video_files'], 1):
                video_url = vf.get('url') or vf.get('direct_url')
                if video_url:
                    video_urls.append((idx, video_url))
                elif vf.get('file_path') and os.path.exists(vf['file_path']):
                    dest = os.path.join(link_dir, f"video_{idx}.mp4")
                    try:
                        shutil.copy2(vf['file_path'], dest)
                        downloaded_files.append(dest)
                    except Exception:
                        failed_count += 1
                else:
                    failed_count += 1
        
        if not video_urls and result.get('direct_url'):
            video_urls.append((1, result['direct_url']))
        
        download_file_tasks = []
        
        for idx, video_url in video_urls:
            dest_path = os.path.join(link_dir, f"video_{idx}.mp4")
            headers = download_headers.copy() if download_headers else {}
            referer = url
            
            if 'video.twimg.com' in video_url or 'pbs.twimg.com' in video_url:
                m = re.search(r'/status/(\d+)', url)
                referer = f'https://x.com/status/{m.group(1)}' if m else 'https://x.com/'
            
            if parser and hasattr(parser, 'headers'):
                headers.update(parser.headers)
            headers['Referer'] = referer
            download_file_tasks.append(('video', idx, video_url, dest_path, referer, video_proxy, headers))
        
        image_urls = result.get('images', [])
        image_files = result.get('image_files', [])
        valid_image_files = [img for img in image_files if isinstance(img, str) and os.path.exists(img)] if image_files else []
        
        if image_urls:
            image_url_lists = result.get('image_url_lists', [])
            for idx, primary_url in enumerate(image_urls):
                ext = next((e for e in ['.png', '.webp', '.gif'] if e in primary_url.lower()), '.jpg')
                dest_path = os.path.join(link_dir, f"image_{idx + 1}{ext}")
                
                url_list = [primary_url]
                if idx < len(image_url_lists) and image_url_lists[idx]:
                    backup_urls = image_url_lists[idx]
                    url_list = backup_urls if backup_urls[0] == primary_url else [primary_url] + backup_urls
                
                headers = download_headers.copy() if download_headers else {}
                referer = url
                
                if 'pbs.twimg.com' in primary_url:
                    m = re.search(r'/status/(\d+)', url)
                    referer = f'https://x.com/status/{m.group(1)}' if m else 'https://x.com/'
                elif 'douyinpic.com' in primary_url or 'p3-sign.douyinpic.com' in primary_url:
                    referer = url
                elif 'kuaishou.com' in url or any('kspkg.com' in u for u in url_list):
                    referer = url
                
                if parser and hasattr(parser, 'headers'):
                    headers.update(parser.headers)
                headers['Referer'] = referer
                download_file_tasks.append(('image', idx + 1, url_list, dest_path, referer, image_proxy, headers))
        elif valid_image_files:
            for idx, img_file in enumerate(valid_image_files, 1):
                dest_path = os.path.join(link_dir, f"image_{idx}{os.path.splitext(img_file)[1] or '.jpg'}")
                try:
                    shutil.copy2(img_file, dest_path)
                    downloaded_files.append(dest_path)
                except Exception:
                    failed_count += 1
        
        if download_file_tasks:
            semaphore = asyncio.Semaphore(3)
            failed_count_list = [failed_count]
            downloaded_files_list = [downloaded_files]
            
            async def download_with_info(media_type, idx, file_url_or_list, dest_path, referer, proxy, headers):
                async with semaphore:
                    try:
                        if isinstance(file_url_or_list, list):
                            success = False
                            for url in file_url_or_list:
                                if url and isinstance(url, str) and url.startswith(('http://', 'https://')):
                                    success = await download_file(session, url, dest_path, referer=referer, proxy=proxy, headers=headers, max_retries=1, retry_delay=0.5)
                                    if success:
                                        break
                        else:
                            success = await download_file(session, file_url_or_list, dest_path, referer=referer, proxy=proxy, headers=headers, max_retries=3, retry_delay=1.0)
                        
                        if success:
                            downloaded_files_list[0].append(dest_path)
                        else:
                            failed_count_list[0] += 1
                    except Exception:
                        failed_count_list[0] += 1
            
            tasks = [download_with_info(*task) for task in download_file_tasks]
            await asyncio.gather(*tasks, return_exceptions=True)
            
            failed_count = failed_count_list[0]
            downloaded_files = downloaded_files_list[0]

        if failed_count == 0 and len(downloaded_files) > 0:
            return True
        print(f"{url} 下载失败！")
        print("-" * 80)
        return False
    except Exception:
        print(f"{url} 下载失败！")
        print("-" * 80)
        return False


async def main():
    """主函数"""
    print("=" * 80)
    print("视频链接解析测试工具")
    print("支持的平台: B站、抖音、Twitter/X、快手")
    print("输入 'q' 退出程序")
    print("=" * 80)
    
    use_proxy = True
    proxy_url = "http://127.0.0.1:7897"
    
    parser_manager = init_parsers(use_proxy=use_proxy, proxy_url=proxy_url)
    download_dir = os.path.join(os.path.dirname(__file__), "download")
    os.makedirs(download_dir, exist_ok=True)
    
    if use_proxy:
        print(f"✓ 代理: {proxy_url}\n")

    timeout = aiohttp.ClientTimeout(total=60)

    while True:
        try:
            print("\n请输入包含视频链接的文本（可粘贴多行，输入空行结束，输入 q 退出）:")
            print("提示: 如果直接粘贴多行文本，请在第一行粘贴，然后按回车，再输入一个空行结束")
            lines = []
            empty_line_count = 0
            while True:
                try:
                    line = input(">>> " if not lines else "... ").strip()
                    if line.lower() == 'q':
                        print("再见！")
                        return
                    if not line:
                        empty_line_count += 1
                        if empty_line_count >= 1 and lines:
                            break
                        if not lines:
                            continue
                    else:
                        empty_line_count = 0
                        # 检查是否包含多行（可能是直接粘贴的）
                        if '\n' in line or '\r' in line:
                            # 分割多行
                            multilines = [l.strip() for l in line.replace('\r\n', '\n').replace('\r', '\n').split('\n') if l.strip()]
                            lines.extend(multilines)
                        else:
                            lines.append(line)
                except (EOFError, KeyboardInterrupt):
                    if lines:
                        break
                    print("\n\n程序已中断")
                    return
            
            if not lines:
                print("输入不能为空，请重新输入。\n")
                continue
            
            text = '\n'.join(lines)
            print(f"\n正在解析... ({len(text)} 字符, {len(lines)} 行)")
            print(f"调试: 提取到的文本前100字符: {text[:100]}...")
            print("-" * 80)

            connector = aiohttp.TCPConnector(
                limit=100,
                limit_per_host=10,
                ttl_dns_cache=300,
                force_close=False,
                enable_cleanup_closed=True
            )
            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                # 提取链接
                links_with_parser = parser_manager.extract_all_links(text)
                if not links_with_parser:
                    print("未找到可解析的链接")
                    continue

                print(f"找到 {len(links_with_parser)} 个链接")
                if len(links_with_parser) > 0:
                    print("链接列表:")
                    for idx, (url, parser) in enumerate(links_with_parser, 1):
                        parser_name = parser.name if hasattr(parser, 'name') else type(parser).__name__
                        print(f"  [{idx}] {parser_name}: {url}")
                print()
                
                print("正在并发解析所有链接...")
                # 并发解析所有链接
                async def parse_one_link(url, parser):
                    """解析单个链接"""
                    try:
                        result = await parser.parse(session, url)
                        return (url, parser, result, None if result else "解析失败：未返回结果")
                    except RuntimeError as e:
                        # RuntimeError 是业务逻辑错误，直接使用错误消息
                        error_msg = str(e)
                        return (url, parser, None, error_msg)
                    except Exception as e:
                        # 其他异常，返回异常信息
                        error_type = type(e).__name__
                        error_msg = str(e)
                        return (url, parser, None, f"{error_type}: {error_msg}")
                
                # 创建所有解析任务
                tasks = [parse_one_link(url, parser) for url, parser in links_with_parser]
                # 并发执行所有任务
                parse_results = await asyncio.gather(*tasks, return_exceptions=True)
                
                # 处理结果，将异常转换为错误结果
                processed_results = []
                for i, result in enumerate(parse_results):
                    if isinstance(result, Exception):
                        url, parser = links_with_parser[i]
                        processed_results.append((url, parser, None, f"异常: {str(result)}"))
                    else:
                        processed_results.append(result)
                
                # 显示解析结果
                success_count = 0
                fail_count = 0
                for url, parser, result, error in processed_results:
                    if result:
                        success_count += 1
                        parser_name = parser.name if hasattr(parser, 'name') else type(parser).__name__
                        print_metadata(result, url, parser_name)
                    else:
                        fail_count += 1
                        parser_name = parser.name if hasattr(parser, 'name') else type(parser).__name__
                        print(f"\n⚠️ 解析失败: {parser_name}")
                        print(f"   链接: {url}")
                        if error:
                            print(f"   错误: {error}")
                
                print(f"\n解析完成: 成功 {success_count} 个, 失败 {fail_count} 个")
                
                if processed_results:
                    choice = input("\n是否下载所有媒体到本地? (y/n/q退出): ").strip().lower()
                    if choice == 'q':
                        return
                    elif choice in ('y', 'yes', '是'):
                        download_tasks = [(r, u, p) for u, p, r, e in processed_results if r]
                        if download_tasks:
                            await download_media_concurrent(download_tasks, download_dir, session)
                
                print("\n" + "=" * 80 + "\n")

        except (KeyboardInterrupt, EOFError):
            print("\n\n程序已中断")
            break
        except Exception as e:
            print(f"\n错误: {e}")
            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
