import sys
import os
import logging
import asyncio
import aiohttp
from typing import List, Dict, Any

_project_root = os.path.dirname(os.path.abspath(__file__))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

try:
    from core.constants import Config
    from core.parser import ParserManager
    from core.downloader import DownloadManager
    from core.parser.platform import (
        BilibiliParser,
        DouyinParser,
        KuaishouParser,
        WeiboParser,
        XiaohongshuParser,
        XiaoheiheParser,
        TwitterParser
    )
except ImportError as e:
    print(f"导入模块失败: {e}")
    print("请确保所有模块在正确的路径下")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

try:
    from astrbot.api import logger
except ImportError:
    logger = logging.getLogger(__name__)


def print_metadata(metadata: Dict[str, Any], url: str, parser_name: str):
    """打印解析后的元数据"""
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
                print(f"  [{idx}] {main_url[:80]}{'...' if len(main_url) > 80 else ''}{backup_info}")
    
    if image_urls:
        print(f"\n图集: {len(image_urls)} 张")
        for idx, url_list in enumerate(image_urls[:5], 1):
            if url_list and isinstance(url_list, list) and len(url_list) > 0:
                main_url = url_list[0]
                backup_count = len(url_list) - 1
                backup_info = f" (备用URL: {backup_count}个)" if backup_count > 0 else ""
                print(f"  [{idx}] {main_url[:80]}{'...' if len(main_url) > 80 else ''}{backup_info}")
        if len(image_urls) > 5:
            print(f"  ... 还有 {len(image_urls) - 5} 张")
    
    if metadata.get('is_twitter_video'):
        print("标记: Twitter视频")
    if metadata.get('referer'):
        print(f"Referer: {metadata.get('referer')}")
    
    print("=" * 80)


def print_download_result(metadata: Dict[str, Any], url: str):
    """打印下载结果"""
    print("\n" + "=" * 80)
    print(f"下载结果: {url}")
    print("-" * 80)
    
    if metadata.get('error'):
        print(f"❌ 下载失败: {metadata['error']}")
        print("=" * 80)
        return
    
    video_count = metadata.get('video_count', 0)
    image_count = metadata.get('image_count', 0)
    failed_video_count = metadata.get('failed_video_count', 0)
    failed_image_count = metadata.get('failed_image_count', 0)
    
    print(f"\n媒体统计:")
    print(f"  视频: {video_count} 个 (失败: {failed_video_count})")
    print(f"  图片: {image_count} 张 (失败: {failed_image_count})")
    
    video_sizes = metadata.get('video_sizes', [])
    total_video_size = metadata.get('total_video_size_mb', 0.0)
    if video_sizes:
        print(f"\n视频大小:")
        for idx, size in enumerate(video_sizes, 1):
            if size is not None:
                print(f"  视频[{idx}]: {size:.2f} MB")
        if total_video_size > 0:
            print(f"  总大小: {total_video_size:.2f} MB")
    
    file_paths = metadata.get('file_paths', [])
    if file_paths:
        print(f"\n下载的文件 ({len([fp for fp in file_paths if fp])} 个):")
        for idx, file_path in enumerate(file_paths, 1):
            if file_path:
                print(f"  [{idx}] {file_path}")
            else:
                print(f"  [{idx}] (下载失败)")
    
    print("=" * 80)


async def parse_and_confirm_download(
    text: str,
    parser_manager: ParserManager,
    download_manager: DownloadManager,
    session: aiohttp.ClientSession,
    proxy_url: str = None
) -> List[Dict[str, Any]]:
    """
    解析文本中的链接，等待用户确认后下载
    
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
    
    for metadata in metadata_list:
        url = metadata.get('url', '未知')
        parser_name = "未知解析器"
        try:
            parser = parser_manager.find_parser(url)
            parser_name = parser.name if parser else "未知解析器"
        except ValueError:
            pass
        print_metadata(metadata, url, parser_name)
    
    has_valid_media = any(
        not metadata.get('error') and 
        (bool(metadata.get('video_urls')) or bool(metadata.get('image_urls')))
        for metadata in metadata_list
    )
    
    parse_success_count = sum(1 for m in metadata_list if not m.get('error'))
    parse_fail_count = sum(1 for m in metadata_list if m.get('error'))
    
    if not has_valid_media:
        print("\n⚠️ 没有找到有效的媒体内容（视频或图片）")
        print("\n" + "=" * 80)
        print("统计汇总")
        print("-" * 80)
        print(f"链接解析:")
        print(f"  成功: {parse_success_count} 个")
        print(f"  失败: {parse_fail_count} 个")
        print(f"  总计: {len(metadata_list)} 个")
        print("=" * 80)
        return metadata_list
    
    print("\n" + "=" * 80)
    print("是否下载媒体文件？")
    print("=" * 80)
    while True:
        try:
            user_input = input("输入 'y' 或 'yes' 下载，输入 'n' 或 'no' 跳过，输入 'q' 退出: ").strip().lower()
            if user_input in ['q', 'quit', 'exit']:
                print("退出程序")
                return metadata_list
            elif user_input in ['y', 'yes']:
                break
            elif user_input in ['n', 'no']:
                print("跳过下载")
                print("\n" + "=" * 80)
                print("统计汇总")
                print("-" * 80)
                print(f"链接解析:")
                print(f"  成功: {parse_success_count} 个")
                print(f"  失败: {parse_fail_count} 个")
                print(f"  总计: {len(metadata_list)} 个")
                print("=" * 80)
                return metadata_list
            else:
                print("无效输入，请重新输入")
        except (EOFError, KeyboardInterrupt):
            print("\n\n程序已中断")
            return metadata_list
    
    print("\n开始下载媒体文件...")
    print("-" * 80)
    
    processed_metadata_list = []
    for metadata in metadata_list:
        if metadata.get('error'):
            processed_metadata_list.append(metadata)
            continue
        
        try:
            processed_metadata = await download_manager.process_metadata(
                session,
                metadata,
                proxy_addr=proxy_url
            )
            processed_metadata_list.append(processed_metadata)
            print_download_result(processed_metadata, metadata.get('url', ''))
        except Exception as e:
            logger.exception(f"处理元数据失败: {metadata.get('url', '')}, 错误: {e}")
            metadata['error'] = str(e)
            processed_metadata_list.append(metadata)
    
    total_video_success = 0
    total_video_fail = 0
    total_image_success = 0
    total_image_fail = 0
    
    for processed_metadata in processed_metadata_list:
        if processed_metadata.get('error'):
            continue
        
        video_count = processed_metadata.get('video_count', 0)
        image_count = processed_metadata.get('image_count', 0)
        failed_video_count = processed_metadata.get('failed_video_count', 0)
        failed_image_count = processed_metadata.get('failed_image_count', 0)
        
        total_video_success += video_count - failed_video_count
        total_video_fail += failed_video_count
        total_image_success += image_count - failed_image_count
        total_image_fail += failed_image_count
    
    print("\n" + "=" * 80)
    print("统计汇总")
    print("-" * 80)
    print(f"链接解析:")
    print(f"  成功: {parse_success_count} 个")
    print(f"  失败: {parse_fail_count} 个")
    print(f"  总计: {len(metadata_list)} 个")
    print(f"\n媒体下载:")
    print(f"  视频成功: {total_video_success} 个")
    print(f"  视频失败: {total_video_fail} 个")
    print(f"  图片成功: {total_image_success} 张")
    print(f"  图片失败: {total_image_fail} 张")
    print("=" * 80)
    
    return processed_metadata_list


async def main(
    debug_mode: bool = False,
    use_proxy: bool = False,
    proxy_url: str = None,
    cache_dir: str = None
):
    """
    主函数，运行交互式测试工具
    
    Args:
        debug_mode: 是否启用 debug 模式
        use_proxy: 是否使用代理
        proxy_url: 代理地址
        cache_dir: 下载目录
    """
    print("=" * 80)
    print("媒体链接解析测试工具（简化版）")
    print("支持的平台: B站、抖音、快手、小红书、Twitter/X、微博、小黑盒")
    print("输入 'q' 退出程序")
    print("=" * 80)
    
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
        max_video_size_mb=0.0,
        large_video_threshold_mb=0.0,
        cache_dir=cache_dir,
        pre_download_all_media=True,
        max_concurrent_downloads=3
    )
    
    print("\n" + "=" * 80)
    print("当前配置:")
    print(f"  Debug 模式: {'启用' if debug_mode else '禁用'}")
    if use_proxy and proxy_url:
        print(f"  代理: {proxy_url}")
    print(f"  下载目录: {cache_dir}")
    print("=" * 80)
    
    timeout = aiohttp.ClientTimeout(total=Config.DEFAULT_TIMEOUT)
    
    try:
        while True:
            try:
                print("\n请输入包含媒体链接的文本（可粘贴多行，输入空行结束，输入 q 退出）:")
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
                    await parse_and_confirm_download(
                        text,
                        parser_manager,
                        download_manager,
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
    
    finally:
        try:
            await download_manager.shutdown()
        except Exception as e:
            logger.warning(f"关闭下载管理器时出错: {e}")


if __name__ == "__main__":
    DEBUG_MODE = False
    
    USE_PROXY = False
    PROXY_URL = "http://127.0.0.1:7897"
    
    CACHE_DIR = os.path.join(os.path.dirname(__file__), "media")
    
    asyncio.run(main(
        debug_mode=DEBUG_MODE,
        use_proxy=USE_PROXY,
        proxy_url=PROXY_URL if USE_PROXY else None,
        cache_dir=CACHE_DIR
    ))

