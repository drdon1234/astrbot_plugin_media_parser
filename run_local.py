# -*- coding: utf-8 -*-
"""
本地测试脚本
用于测试视频链接解析和下载功能
只导入 parsers 模块，完全独立的本地测试项目
"""
import sys
import os
import logging
import asyncio
import aiohttp
import re
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import urlparse

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

_project_root = os.path.dirname(os.path.abspath(__file__))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

try:
    from parsers import (
        BilibiliParser,
        DouyinParser,
        KuaishouParser,
        XiaohongshuParser,
        TwitterParser,
        LinkRouter
    )
except ImportError as e:
    logger.error(f"导入 parsers 模块失败: {e}")
    logger.error("请确保 parsers 模块在正确的路径下")
    sys.exit(1)


def init_parsers(use_proxy: bool = False, proxy_url: str = None) -> List:
    """
    初始化解析器列表
    Args:
        use_proxy: 是否使用代理（Twitter/X需要代理才能访问）
        proxy_url: 代理地址（格式：http://host:port 或 socks5://host:port）
    Returns:
        解析器列表
    """
    parsers = [
        BilibiliParser(),
        DouyinParser(),
        KuaishouParser(),
        XiaohongshuParser(),
        TwitterParser(
            use_image_proxy=use_proxy,
            use_video_proxy=use_proxy,
            proxy_url=proxy_url
        ) if use_proxy and proxy_url else TwitterParser(),
    ]
    return parsers


def print_metadata(metadata: Dict[str, Any], url: str, parser_name: str):
    """打印解析后的元数据。

    Args:
        metadata: 元数据字典
        url: 原始URL
        parser_name: 解析器名称
    """
    print("\n" + "=" * 80)
    print(f"解析器: {parser_name} | 链接: {url}")
    print("-" * 80)
    
    if metadata.get('error'):
        print(f"❌ 解析失败: {metadata['error']}")
        print("=" * 80)
        return
    
    print(f"标题: {metadata.get('title', 'N/A')}")
    print(f"作者: {metadata.get('author', 'N/A')}")
    print(f"简介: {metadata.get('desc', 'N/A')}")
    print(f"发布时间: {metadata.get('timestamp', 'N/A')}")
    
    media_type = metadata.get('media_type', 'unknown')
    media_urls = metadata.get('media_urls', [])
    
    if media_type == 'video':
        print(f"\n视频: {len(media_urls)} 个")
        for idx, video_url in enumerate(media_urls, 1):
            print(f"  [{idx}] {video_url}")
        if metadata.get('thumb_url'):
            print(f"封面: {metadata.get('thumb_url')}")
    elif media_type == 'gallery':
        print(f"\n图集: {len(media_urls)} 张")
        for idx, img_url in enumerate(media_urls[:5], 1):
            backup_count = 0
            image_url_lists = metadata.get('image_url_lists', [])
            if idx <= len(image_url_lists) and image_url_lists[idx - 1]:
                backup_count = len(image_url_lists[idx - 1]) - 1
            backup_info = f" (备用URL: {backup_count}个)" if backup_count > 0 else ""
            print(f"  [{idx}] {img_url}{backup_info}")
        if len(media_urls) > 5:
            print(f"  ... 还有 {len(media_urls) - 5} 张")
    
    if metadata.get('is_twitter_video'):
        print("标记: Twitter视频（需要下载）")
    if metadata.get('page_url'):
        print(f"页面URL: {metadata.get('page_url')}")
    
    print("=" * 80)


async def download_file(
    session: aiohttp.ClientSession,
    url: str,
    filepath: str,
    referer: str = None,
    proxy: str = None,
    headers: dict = None,
    max_retries: int = 2,
    retry_delay: float = 0.5
) -> bool:
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
    default_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
    }
    
    if headers:
        default_headers.update(headers)
    
    if referer:
        default_headers['Referer'] = referer
    
    if 'pbs.twimg.com' in url or 'video.twimg.com' in url:
        if 'Referer' not in default_headers:
            default_headers['Referer'] = 'https://x.com/'
    
    if 'xhscdn.com' in url:
        if 'Referer' not in default_headers:
            default_headers['Referer'] = 'https://www.xiaohongshu.com/'
    
    is_video = '.mp4' in url.lower() or filepath.endswith('.mp4')
    
    for attempt in range(max_retries + 1):
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
        except Exception as e:
            logger.warning(f"下载失败 (尝试 {attempt + 1}/{max_retries + 1}): {url}, 错误: {e}")
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


async def download_media(
    metadata: Dict[str, Any],
    download_dir: str,
    session: aiohttp.ClientSession,
    proxy_url: str = None
) -> bool:
    """
    下载媒体文件
    Args:
        metadata: 解析结果元数据
        download_dir: 下载目录
        session: aiohttp会话
        proxy_url: 代理地址（格式：http://host:port 或 socks5://host:port），用于Twitter媒体下载
    Returns:
        bool: 下载是否成功
    """
    try:
        if metadata.get('error'):
            logger.warning(f"跳过下载（解析失败）: {metadata.get('url')}")
            return False
        
        url = metadata.get('url', '')
        media_type = metadata.get('media_type', '')
        media_urls = metadata.get('media_urls', [])
        
        if not media_urls:
            logger.warning(f"跳过下载（无媒体URL）: {url}")
            return False
        
        download_dir = os.path.abspath(download_dir)
        os.makedirs(download_dir, exist_ok=True)
        
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
        
        referer = metadata.get('page_url') or url
        headers = {}
        is_twitter = ('twitter.com' in url or 'x.com' in url)
        
        if 'bilibili.com' in url:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.bilibili.com",
                "Origin": "https://www.bilibili.com"
            }
        elif 'douyin.com' in url:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Linux; Android 8.0.0; SM-G955U Build/R16NW) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36',
                'Referer': 'https://www.douyin.com/'
            }
        elif 'xiaohongshu.com' in url or 'xhslink.com' in url:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Referer': referer if 'xiaohongshu.com' in referer else 'https://www.xiaohongshu.com/'
            }
        elif is_twitter:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            referer = url
        
        if media_type == 'video':
            for idx, media_url in enumerate(media_urls, 1):
                dest_path = os.path.join(link_dir, f"video_{idx}.mp4")
                
                media_proxy = None
                if proxy_url and ('pbs.twimg.com' in media_url or 'video.twimg.com' in media_url):
                    media_proxy = proxy_url
                
                success = await download_file(
                    session,
                    media_url,
                    dest_path,
                    referer=referer,
                    proxy=media_proxy,
                    headers=headers,
                    max_retries=3,
                    retry_delay=1.0
                )
                if success:
                    downloaded_files.append(dest_path)
                else:
                    failed_count += 1
        elif media_type == 'gallery':
            image_url_lists = metadata.get('image_url_lists', [])
            for idx, media_url in enumerate(media_urls, 1):
                url_list = [media_url]
                if idx <= len(image_url_lists) and image_url_lists[idx - 1]:
                    backup_urls = image_url_lists[idx - 1]
                    if backup_urls and backup_urls[0] == media_url:
                        url_list = backup_urls
                    else:
                        url_list = [media_url] + backup_urls
                
                ext = '.jpg'
                for url_item in url_list:
                    if '.png' in url_item.lower():
                        ext = '.png'
                        break
                    elif '.webp' in url_item.lower():
                        ext = '.webp'
                        break
                    elif '.gif' in url_item.lower():
                        ext = '.gif'
                        break
                
                dest_path = os.path.join(link_dir, f"image_{idx}{ext}")
                success = False
                for url_item in url_list:
                    if url_item and isinstance(url_item, str) and url_item.startswith(('http://', 'https://')):
                        media_proxy = None
                        if proxy_url and ('pbs.twimg.com' in url_item or 'video.twimg.com' in url_item):
                            media_proxy = proxy_url
                        
                        success = await download_file(
                            session,
                            url_item,
                            dest_path,
                            referer=referer,
                            proxy=media_proxy,
                            headers=headers,
                            max_retries=1,
                            retry_delay=0.5
                        )
                        if success:
                            break
                if success:
                    downloaded_files.append(dest_path)
                else:
                    failed_count += 1
        
        if failed_count == 0 and len(downloaded_files) > 0:
            print(f"✓ {url} 下载成功 ({len(downloaded_files)} 个文件)")
            return True
        else:
            print(f"✗ {url} 下载失败 (成功: {len(downloaded_files)}, 失败: {failed_count})")
            return False
    except Exception as e:
        logger.exception(f"下载媒体失败: {metadata.get('url', '')}, 错误: {e}")
        print(f"✗ {metadata.get('url', '')} 下载失败: {e}")
        return False


async def download_media_concurrent(
    metadata_list: List[Dict[str, Any]],
    download_dir: str,
    session: aiohttp.ClientSession,
    proxy_url: str = None,
    max_concurrent: int = 5
):
    """并发下载多个媒体文件。

    Args:
        metadata_list: 元数据列表
        download_dir: 下载目录
        session: aiohttp会话
        proxy_url: 代理地址（可选）
        max_concurrent: 最大并发数
    """
    os.makedirs(os.path.abspath(download_dir), exist_ok=True)
    if not metadata_list:
        return
    
    total_files = sum(len(meta.get('media_urls', [])) for meta in metadata_list)
    print(f"\n开始并发下载 {len(metadata_list)} 个链接内的媒体（共 {total_files} 个文件，最大并发数: {max_concurrent}）...")
    if proxy_url:
        print(f"使用代理: {proxy_url}")
    
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def download_single_metadata(metadata):
        """下载单个元数据的媒体。

        Args:
            metadata: 元数据字典

        Returns:
            下载是否成功
        """
        async with semaphore:
            return await download_media(metadata, download_dir, session, proxy_url)
    
    tasks = [download_single_metadata(meta) for meta in metadata_list if meta.get('media_urls')]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    success_count = sum(1 for r in results if r is True)
    failed_count = len(results) - success_count
    print(f"\n下载完成: 共 {len(results)} 个链接，成功 {success_count} 个，失败 {failed_count} 个")
    if failed_count > 0:
        print("提示: 部分媒体下载失败，请检查网络连接和代理配置")


async def parse_text(
    text: str,
    parsers: List,
    session: aiohttp.ClientSession
) -> List[Dict[str, Any]]:
    """
    解析文本中的所有链接
    Args:
        text: 输入文本
        parsers: 解析器列表
        session: aiohttp会话
    Returns:
        解析结果元数据列表
    """
    link_router = LinkRouter(parsers)
    links_with_parser = link_router.extract_links_with_parser(text)
    
    if not links_with_parser:
        return []
    
    seen_links = set()
    unique_links_with_parser = []
    for link, parser in links_with_parser:
        if link not in seen_links:
            seen_links.add(link)
            unique_links_with_parser.append((link, parser))
    
    async def parse_one_link(url: str, parser) -> Tuple[str, Dict[str, Any], Optional[str]]:
        """解析单个链接。

        Args:
            url: 链接URL
            parser: 解析器实例

        Returns:
            包含(url, metadata, error)的元组
        """
        try:
            result = await parser.parse(session, url)
            if result:
                return (url, result, None)
            else:
                return (url, None, "解析失败：未返回结果")
        except RuntimeError as e:
            error_msg = str(e)
            return (url, None, error_msg)
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)
            return (url, None, f"{error_type}: {error_msg}")
    
    tasks = [parse_one_link(url, parser) for url, parser in unique_links_with_parser]
    parse_results = await asyncio.gather(*tasks, return_exceptions=True)
    
    metadata_list = []
    for i, result in enumerate(parse_results):
        if isinstance(result, Exception):
            url, parser = unique_links_with_parser[i]
            metadata_list.append({
                'url': url,
                'error': str(result),
                'media_type': 'error',
                'media_urls': []
            })
        else:
            url, metadata, error = result
            if error:
                metadata_list.append({
                    'url': url,
                    'error': error,
                    'media_type': 'error',
                    'media_urls': []
                })
            elif metadata:
                metadata_list.append(metadata)
            else:
                metadata_list.append({
                    'url': url,
                    'error': '解析失败：未返回结果',
                    'media_type': 'error',
                    'media_urls': []
                })
    
    return metadata_list


async def main():
    """主函数，运行交互式测试工具。"""
    print("=" * 80)
    print("视频链接解析测试工具")
    print("支持的平台: B站、抖音、快手、小红书、Twitter/X")
    print("输入 'q' 退出程序")
    print("=" * 80)
    
    use_proxy = False
    proxy_url = "http://127.0.0.1:7897" # 这是 clash verge 的默认端口
    
    parsers = init_parsers(use_proxy=use_proxy, proxy_url=proxy_url)
    download_dir = os.path.join(os.path.dirname(__file__), "download")
    os.makedirs(download_dir, exist_ok=True)
    
    if use_proxy and proxy_url:
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
                        if '\n' in line or '\r' in line:
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
            print("-" * 80)

            connector = aiohttp.TCPConnector(
                limit=100,
                limit_per_host=10,
                ttl_dns_cache=300,
                force_close=False,
                enable_cleanup_closed=True
            )
            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                metadata_list = await parse_text(text, parsers, session)
                
                if not metadata_list:
                    print("未找到可解析的链接或解析失败")
                    continue

                print(f"找到 {len(metadata_list)} 个链接的解析结果\n")
                
                success_count = 0
                fail_count = 0
                for metadata in metadata_list:
                    if metadata.get('error'):
                        fail_count += 1
                        url = metadata.get('url', '未知')
                        print(f"\n⚠️ 解析失败")
                        print(f"   链接: {url}")
                        print(f"   错误: {metadata.get('error')}")
                    else:
                        success_count += 1
                        parser_name = "未知解析器"
                        try:
                            link_router = LinkRouter(parsers)
                            parser = link_router.find_parser(metadata.get('url', ''))
                            parser_name = parser.name if parser else "未知解析器"
                        except ValueError:
                            pass
                        print_metadata(metadata, metadata.get('url', ''), parser_name)
                
                print(f"\n解析完成: 成功 {success_count} 个, 失败 {fail_count} 个")
                
                if metadata_list:
                    choice = input("\n是否下载所有媒体到本地? (y/n/q退出): ").strip().lower()
                    if choice == 'q':
                        return
                    elif choice in ('y', 'yes', '是'):
                        valid_metadata_list = [
                            meta for meta in metadata_list
                            if not meta.get('error') and meta.get('media_urls')
                        ]
                        if valid_metadata_list:
                            await download_media_concurrent(
                                valid_metadata_list,
                                download_dir,
                                session,
                                proxy_url=proxy_url if use_proxy else None
                            )
                
                print("\n" + "=" * 80 + "\n")

        except (KeyboardInterrupt, EOFError):
            print("\n\n程序已中断")
            break
        except Exception as e:
            print(f"\n错误: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
