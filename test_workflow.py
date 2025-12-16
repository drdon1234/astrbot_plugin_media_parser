# -*- coding: utf-8 -*-
"""
本地工作流测试脚本
用于测试完整的解析和下载工作流
导入常量、解析器和下载器包，使用管理器进行测试
"""
import sys
import os
import logging
import asyncio
import aiohttp
from typing import List, Dict, Any, Optional, Tuple

try:
    from astrbot.api import logger
except ImportError:
    logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

_project_root = os.path.dirname(os.path.abspath(__file__))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

try:
    from core.constants import Config
    from core.parser import ParserManager
    from core.downloader import DownloadManager
    from core.parser.handler import (
        BilibiliParser,
        DouyinParser,
        KuaishouParser,
        WeiboParser,
        XiaohongshuParser,
        TwitterParser,
        XiaoheiheParser
    )
except ImportError as e:
    logger.error(f"导入模块失败: {e}")
    logger.error("请确保所有模块在正确的路径下")
    sys.exit(1)


def init_components(
    debug_mode: bool = False,
    use_proxy: bool = False,
    proxy_url: str = None,
    max_video_size_mb: float = 0.0,
    large_video_threshold_mb: float = Config.DEFAULT_LARGE_VIDEO_THRESHOLD_MB,
    cache_dir: str = None,
    pre_download_all_media: bool = False,
    max_concurrent_downloads: int = 3
) -> Tuple[ParserManager, DownloadManager]:
    """
    初始化组件
    Args:
        debug_mode: 是否启用 debug 模式
        use_proxy: 是否使用代理（Twitter/X需要代理才能访问）
        proxy_url: 代理地址（格式：http://host:port 或 socks5://host:port）
        max_video_size_mb: 最大允许的视频大小(MB)，0表示不限制
        large_video_threshold_mb: 大视频阈值(MB)，超过此大小将单独发送
        cache_dir: 视频缓存目录
        pre_download_all_media: 是否预先下载所有媒体到本地
        max_concurrent_downloads: 最大并发下载数
    Returns:
        (ParserManager, DownloadManager) 元组
    """
    Config.DEBUG_MODE = debug_mode
    if debug_mode:
        logger.setLevel(logging.DEBUG)
        logger.debug("Debug模式已启用")
    
    parsers = [
        BilibiliParser(),
        DouyinParser(),
        KuaishouParser(),
        WeiboParser(),
        XiaohongshuParser(),
        XiaoheiheParser(),
        TwitterParser(
            use_image_proxy=use_proxy,
            use_video_proxy=use_proxy,
            proxy_url=proxy_url
        ) if use_proxy and proxy_url else TwitterParser(),
    ]
    
    parser_manager = ParserManager(parsers)
    
    if cache_dir is None:
        cache_dir = os.path.join(os.path.dirname(__file__), "media")
    
    download_manager = DownloadManager(
        max_video_size_mb=max_video_size_mb,
        large_video_threshold_mb=large_video_threshold_mb,
        cache_dir=cache_dir,
        pre_download_all_media=pre_download_all_media,
        max_concurrent_downloads=max_concurrent_downloads
    )
    
    return parser_manager, download_manager


def print_metadata(metadata: Dict[str, Any], url: str, parser_name: str):
    """打印解析后的元数据

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
    
    video_urls = metadata.get('video_urls', [])
    image_urls = metadata.get('image_urls', [])
    
    if video_urls:
        print(f"\n视频: {len(video_urls)} 个")
        for idx, url_list in enumerate(video_urls, 1):
            if url_list and isinstance(url_list, list) and len(url_list) > 0:
                main_url = url_list[0]
                backup_count = len(url_list) - 1
                backup_info = f" (备用URL: {backup_count}个)" if backup_count > 0 else ""
                print(f"  [{idx}] {main_url}{backup_info}")
    
    if image_urls:
        print(f"\n图集: {len(image_urls)} 张")
        for idx, url_list in enumerate(image_urls[:5], 1):
            if url_list and isinstance(url_list, list) and len(url_list) > 0:
                main_url = url_list[0]
                backup_count = len(url_list) - 1
                backup_info = f" (备用URL: {backup_count}个)" if backup_count > 0 else ""
                print(f"  [{idx}] {main_url}{backup_info}")
        if len(image_urls) > 5:
            print(f"  ... 还有 {len(image_urls) - 5} 张")
    
    if metadata.get('is_twitter_video'):
        print("标记: Twitter视频")
    if metadata.get('referer'):
        print(f"Referer: {metadata.get('referer')}")
    
    print("=" * 80)


def print_processed_metadata(metadata: Dict[str, Any], url: str, parser_name: str):
    """打印处理后的元数据，包括下载状态和文件路径

    Args:
        metadata: 处理后的元数据字典
        url: 原始URL
        parser_name: 解析器名称
    """
    print("\n" + "=" * 80)
    print(f"处理结果: {parser_name} | 链接: {url}")
    print("-" * 80)
    
    if metadata.get('error'):
        print(f"❌ 处理失败: {metadata['error']}")
        print("=" * 80)
        return
    
    print(f"标题: {metadata.get('title', 'N/A')}")
    print(f"作者: {metadata.get('author', 'N/A')}")
    
    video_count = metadata.get('video_count', 0)
    image_count = metadata.get('image_count', 0)
    failed_video_count = metadata.get('failed_video_count', 0)
    failed_image_count = metadata.get('failed_image_count', 0)
    
    print(f"\n媒体统计:")
    print(f"  视频: {video_count} 个 (失败: {failed_video_count})")
    print(f"  图片: {image_count} 张 (失败: {failed_image_count})")
    
    video_sizes = metadata.get('video_sizes', [])
    max_video_size = metadata.get('max_video_size_mb')
    total_video_size = metadata.get('total_video_size_mb', 0.0)
    if video_sizes:
        print(f"\n视频大小:")
        for idx, size in enumerate(video_sizes, 1):
            if size is not None:
                print(f"  视频[{idx}]: {size:.2f} MB")
        if max_video_size is not None:
            print(f"  最大视频: {max_video_size:.2f} MB")
        if total_video_size > 0:
            print(f"  总大小: {total_video_size:.2f} MB")
    
    has_valid_media = metadata.get('has_valid_media', False)
    use_local_files = metadata.get('use_local_files', False)
    is_large_media = metadata.get('is_large_media', False)
    exceeds_max_size = metadata.get('exceeds_max_size', False)
    
    print(f"\n下载状态:")
    print(f"  有效媒体: {'是' if has_valid_media else '否'}")
    print(f"  使用本地文件: {'是' if use_local_files else '否'}")
    print(f"  大媒体: {'是' if is_large_media else '否'}")
    if exceeds_max_size:
        print(f"  ⚠️ 超过大小限制")
    
    file_paths = metadata.get('file_paths', [])
    if file_paths:
        print(f"\n下载的文件 ({len([fp for fp in file_paths if fp])} 个):")
        for idx, file_path in enumerate(file_paths, 1):
            if file_path:
                print(f"  [{idx}] {file_path}")
            else:
                print(f"  [{idx}] (下载失败)")
    
    print("=" * 80)


async def test_workflow(
    text: str,
    parser_manager: ParserManager,
    download_manager: DownloadManager,
    session: aiohttp.ClientSession,
    proxy_url: str = None
) -> List[Dict[str, Any]]:
    """
    测试完整工作流：解析 -> 下载处理
    
    Args:
        text: 输入文本
        parser_manager: 解析器管理器
        download_manager: 下载管理器
        session: aiohttp会话
        proxy_url: 代理地址（可选）
    
    Returns:
        处理后的元数据列表
    """
    print(f"\n正在解析文本... ({len(text)} 字符)")
    print("-" * 80)
    
    metadata_list = await parser_manager.parse_text(text, session)
    
    if not metadata_list:
        print("未找到可解析的链接或解析失败")
        return []
    
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
                parser = parser_manager.find_parser(metadata.get('url', ''))
                parser_name = parser.name if parser else "未知解析器"
            except ValueError:
                pass
            print_metadata(metadata, metadata.get('url', ''), parser_name)
    
    print(f"\n解析完成: 成功 {success_count} 个, 失败 {fail_count} 个")
    
    parse_success_total = success_count
    parse_fail_total = fail_count
    
    if not metadata_list:
        return []
    
    print(f"\n开始处理元数据（下载检查/下载）...")
    print("-" * 80)
    
    processed_metadata_list = []
    download_success_count = 0
    download_fail_count = 0
    
    for metadata in metadata_list:
        if metadata.get('error'):
            processed_metadata_list.append(metadata)
            download_fail_count += 1
            continue
        
        try:
            processed_metadata = await download_manager.process_metadata(
                session,
                metadata,
                proxy_addr=proxy_url
            )
            processed_metadata_list.append(processed_metadata)
            
            if processed_metadata.get('error'):
                download_fail_count += 1
            else:
                has_valid_media = processed_metadata.get('has_valid_media', False)
                file_paths = processed_metadata.get('file_paths', [])
                if has_valid_media or (file_paths and any(file_paths)):
                    download_success_count += 1
                else:
                    failed_video_count = processed_metadata.get('failed_video_count', 0)
                    failed_image_count = processed_metadata.get('failed_image_count', 0)
                    video_count = processed_metadata.get('video_count', 0)
                    image_count = processed_metadata.get('image_count', 0)
                    if (video_count > 0 and failed_video_count == video_count and image_count == 0) or \
                       (image_count > 0 and failed_image_count == image_count and video_count == 0) or \
                       (video_count > 0 and image_count > 0 and failed_video_count == video_count and failed_image_count == image_count):
                        download_fail_count += 1
                    else:
                        download_success_count += 1
            
            parser_name = "未知解析器"
            try:
                parser = parser_manager.find_parser(metadata.get('url', ''))
                parser_name = parser.name if parser else "未知解析器"
            except ValueError:
                pass
            print_processed_metadata(processed_metadata, metadata.get('url', ''), parser_name)
        except Exception as e:
            logger.exception(f"处理元数据失败: {metadata.get('url', '')}, 错误: {e}")
            metadata['error'] = str(e)
            processed_metadata_list.append(metadata)
            download_fail_count += 1
    
    if processed_metadata_list:
        processed_metadata_list[0]['_stats'] = {
            'parse_success': parse_success_total,
            'parse_fail': parse_fail_total,
            'download_success': download_success_count,
            'download_fail': download_fail_count
        }
    
    return processed_metadata_list


async def main(
    debug_mode: bool = False,
    use_proxy: bool = False,
    proxy_url: str = None,
    max_video_size_mb: float = 0.0,
    large_video_threshold_mb: float = Config.DEFAULT_LARGE_VIDEO_THRESHOLD_MB,
    cache_dir: str = None,
    pre_download_all_media: bool = False,
    max_concurrent_downloads: int = 3
):
    """主函数，运行交互式测试工具
    
    Args:
        debug_mode: 是否启用 debug 模式
        use_proxy: 是否使用代理
        proxy_url: 代理地址
        max_video_size_mb: 最大允许的视频大小(MB)，0表示不限制
        large_video_threshold_mb: 大视频阈值(MB)
        cache_dir: 下载目录（缓存目录）
        pre_download_all_media: 是否预下载所有媒体
        max_concurrent_downloads: 最大并发下载数
    """
    print("=" * 80)
    print("工作流测试工具")
    print("支持的平台: B站、抖音、快手、小红书、Twitter/X、微博、小黑盒")
    print("输入 'q' 退出程序")
    print("=" * 80)
    
    parser_manager, download_manager = init_components(
        debug_mode=debug_mode,
        use_proxy=use_proxy,
        proxy_url=proxy_url,
        max_video_size_mb=max_video_size_mb,
        large_video_threshold_mb=large_video_threshold_mb,
        cache_dir=cache_dir,
        pre_download_all_media=pre_download_all_media,
        max_concurrent_downloads=max_concurrent_downloads
    )
    
    actual_cache_dir = download_manager.cache_dir
    
    print("\n" + "=" * 80)
    print("当前配置:")
    print(f"  Debug 模式: {'启用' if debug_mode else '禁用'}")
    if use_proxy and proxy_url:
        print(f"  代理: {proxy_url}")
    print(f"  最大视频大小: {max_video_size_mb} MB" if max_video_size_mb > 0 else "  最大视频大小: 不限制")
    print(f"  大视频阈值: {large_video_threshold_mb} MB")
    print(f"  下载目录: {actual_cache_dir}")
    print(f"  预下载所有媒体: {'是' if pre_download_all_media else '否'}")
    print(f"  最大并发下载数: {max_concurrent_downloads}")
    print("=" * 80)
    
    timeout = aiohttp.ClientTimeout(total=Config.DEFAULT_TIMEOUT)
    
    try:
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
                
                connector = aiohttp.TCPConnector(
                    limit=100,
                    limit_per_host=10,
                    ttl_dns_cache=300,
                    force_close=False,
                    enable_cleanup_closed=True
                )
                async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                    processed_metadata_list = await test_workflow(
                        text,
                        parser_manager,
                        download_manager,
                        session,
                        proxy_url=proxy_url if use_proxy else None
                    )
                    
                    if processed_metadata_list:
                        print(f"\n工作流测试完成: 共处理 {len(processed_metadata_list)} 个链接")
                        
                        stats = processed_metadata_list[0].get('_stats') if processed_metadata_list else None
                        if stats:
                            print("\n" + "=" * 80)
                            print("最终统计")
                            print("-" * 80)
                            print(f"链接解析: 成功 {stats.get('parse_success', 0)} 个, 失败 {stats.get('parse_fail', 0)} 个")
                            print(f"媒体下载: 成功 {stats.get('download_success', 0)} 个, 失败 {stats.get('download_fail', 0)} 个")
                            print("=" * 80)
                
                print("\n" + "=" * 80 + "\n")
            
            except (KeyboardInterrupt, EOFError):
                print("\n\n程序已中断")
                break
            except Exception as e:
                print(f"\n错误: {e}")
                import traceback
                traceback.print_exc()
    
    finally:
        try:
            await download_manager.shutdown()
        except Exception as e:
            logger.warning(f"关闭下载管理器时出错: {e}")


if __name__ == "__main__":
    DEBUG_MODE = False
    
    USE_PROXY = True
    PROXY_URL = "http://127.0.0.1:7897"
    
    MAX_VIDEO_SIZE_MB = 0.0
    
    LARGE_VIDEO_THRESHOLD_MB = 0.0
    
    PRE_DOWNLOAD_ALL_MEDIA = True
    
    MAX_CONCURRENT_DOWNLOADS = 5
    
    CACHE_DIR = os.path.join(os.path.dirname(__file__), "media")
    
    asyncio.run(main(
        debug_mode=DEBUG_MODE,
        use_proxy=USE_PROXY,
        proxy_url=PROXY_URL if USE_PROXY else None,
        max_video_size_mb=MAX_VIDEO_SIZE_MB,
        large_video_threshold_mb=LARGE_VIDEO_THRESHOLD_MB,
        cache_dir=CACHE_DIR,
        pre_download_all_media=PRE_DOWNLOAD_ALL_MEDIA,
        max_concurrent_downloads=MAX_CONCURRENT_DOWNLOADS
    ))

