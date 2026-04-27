"""本地调试脚本，用于在命令行环境验证解析与下载流程。"""
import sys
import os
import logging
import asyncio
import aiohttp
from typing import List, Dict, Any

_project_root = os.path.dirname(os.path.abspath(__file__))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

try:
    from core.constants import Config
    from core.parser import ParserManager
    from core.parser.utils import format_duration_ms
    from core.downloader import DownloadManager
    from core.downloader.utils import check_cache_dir_available
    from core.parser.platform import (
        BilibiliParser,
        DouyinParser,
        TikTokParser,
        KuaishouParser,
        WeiboParser,
        XiaohongshuParser,
        XianyuParser,
        ToutiaoParser,
        XiaoheiheParser,
        TwitterParser
    )
    from core.storage import set_stamp_subdir_enabled
except ImportError as e:
    print(f"导入模块失败: {e}")
    print("请确保所有模块在正确的路径下")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

from core.logger import logger

LOCAL_MEDIA_DIR = Config.build_cache_dir(_project_root)
set_stamp_subdir_enabled(False)

def print_metadata(
    metadata: Dict[str, Any],
    url: str,
    parser_name: str,
    enable_text_metadata: bool = True,
    enable_rich_media: bool = True
):
    """打印解析后的元数据"""
    print("\n" + "=" * 80)
    print(f"解析器: {parser_name} | 链接: {url}")
    print("-" * 80)
    
    if metadata.get('error'):
        print(f"❌ 解析失败: {metadata['error']}")
        print("=" * 80)
        return
    
    video_urls = metadata.get('video_urls', [])
    image_urls = metadata.get('image_urls', [])

    if enable_text_metadata:
        print(f"标题: {metadata.get('title', 'N/A')}")
        print(f"作者: {metadata.get('author', 'N/A')}")
        print(f"简介: {metadata.get('desc', 'N/A')}")
        print(f"发布时间: {metadata.get('timestamp', 'N/A')}")

        access_status = metadata.get("access_status")
        access_message = metadata.get("access_message")
        available_length = format_duration_ms(metadata.get("available_length_ms"))
        full_length = format_duration_ms(metadata.get("timelength_ms"))
        if access_status and access_status != "full" and access_message:
            print(f"时长: {access_message}")
        elif metadata.get("is_preview_only") and available_length:
            if full_length:
                print(f"时长: 当前可解析 {available_length} / 全长 {full_length}")
            else:
                print(f"时长: 当前可解析 {available_length}")
        elif full_length:
            print(f"时长: {full_length}")

    if enable_rich_media and video_urls:
        print(f"\n视频: {len(video_urls)} 个")
        for idx, url_list in enumerate(video_urls, 1):
            if url_list and isinstance(url_list, list) and len(url_list) > 0:
                main_url = url_list[0]
                backup_count = len(url_list) - 1
                backup_info = f" (备用URL: {backup_count}个)" if backup_count > 0 else ""
                print(f"  [{idx}] {main_url[:80]}{'...' if len(main_url) > 80 else ''}{backup_info}")

    if enable_rich_media and image_urls:
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
    if metadata.get('platform') == 'tiktok':
        print("平台: TikTok")
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
    video_modes = metadata.get('video_modes', [])
    image_modes = metadata.get('image_modes', [])
    video_skip_reasons = metadata.get('video_skip_reasons', [])
    image_skip_reasons = metadata.get('image_skip_reasons', [])
    video_status_codes = metadata.get('video_status_codes', [])
    image_status_codes = metadata.get('image_status_codes', [])

    print("\n媒体统计:")
    print(f"  视频: {video_count} 个 (失败: {failed_video_count})")
    print(f"  图片: {image_count} 张 (失败: {failed_image_count})")
    if video_modes:
        print(f"  视频模式: {', '.join(video_modes)}")
    if image_modes:
        print(f"  图片模式: {', '.join(image_modes)}")
    for idx, reason in enumerate(video_skip_reasons, 1):
        if reason:
            print(f"  视频[{idx}]跳过: {reason}")
    for idx, status_code in enumerate(video_status_codes, 1):
        if status_code is not None:
            print(f"  视频[{idx}]状态码: {status_code}")
    for idx, reason in enumerate(image_skip_reasons, 1):
        if reason:
            print(f"  图片[{idx}]跳过: {reason}")
    for idx, status_code in enumerate(image_status_codes, 1):
        if status_code is not None:
            print(f"  图片[{idx}]状态码: {status_code}")
    
    video_sizes = metadata.get('video_sizes', [])
    total_video_size = metadata.get('total_video_size_mb', 0.0)
    if video_sizes:
        print("\n视频大小:")
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


async def prepare_bilibili_cookie_interaction(
    links_with_parser,
    session: aiohttp.ClientSession
) -> None:
    """本地调试时先阻塞处理 B 站 Cookie 交互，再进入并发解析。"""
    handled_runtimes = set()
    for _, parser in links_with_parser:
        if not isinstance(parser, BilibiliParser):
            continue
        auth_runtime = parser.get_auth_runtime()
        if not (
            getattr(auth_runtime, "enabled", False) and
            getattr(auth_runtime, "local_debug_mode", False)
        ):
            continue
        runtime_key = id(auth_runtime)
        if runtime_key in handled_runtimes:
            continue
        handled_runtimes.add(runtime_key)

        timeout_seconds = max(
            1,
            int(getattr(parser, "admin_reply_timeout_minutes", 1440)) * 60
        )
        await run_bilibili_cookie_interaction_blocking(
            auth_runtime,
            session,
            timeout_seconds=timeout_seconds
        )


async def run_bilibili_cookie_interaction_blocking(
    auth_runtime,
    session: aiohttp.ClientSession,
    timeout_seconds: int
) -> str:
    """run_local 专用：使用标准 input() 阻塞处理 B 站登录确认。"""
    cookie_header = await auth_runtime.get_cookie_header_for_request(session)
    if cookie_header:
        return cookie_header

    if getattr(auth_runtime, "_local_prompt_asked", False):
        return ""
    setattr(auth_runtime, "_local_prompt_asked", True)

    try:
        payload = await auth_runtime.generate_login_payload(session)
    except Exception as e:
        logger.warning(f"[bilibili] 本地调试生成登录链接失败: {e}")
        return ""

    print("\n检测到B站链接，先处理本地Cookie交互（如需）。")
    print("\n" + "=" * 60)
    print("B站Cookie不可用，检测到本地调试模式。")
    print(f"登录链接: {payload['login_url']}")
    print(f"二维码链接: {payload['qr_code_url']}")
    print("=" * 60)

    try:
        answer = input("是否协助登录? (y/n): ")
    except (EOFError, KeyboardInterrupt):
        print("\n已中断本轮协助登录。")
        return ""

    answer = (answer or "").strip().lower()
    if answer not in ("y", "yes", "是", "确定"):
        print("已跳过本轮协助登录。")
        return ""

    print("已进入扫码等待...")
    result = await auth_runtime.poll_login_until_complete(
        session,
        payload["qrcode_key"],
        timeout_seconds=max(1, timeout_seconds)
    )

    if result.get("status") == "success":
        print("B站登录成功，Cookie已更新。")
        return await auth_runtime.get_cookie_header_for_request(session)

    print(f"B站扫码未完成，状态: {result.get('status')}")
    return ""


async def parse_and_confirm_download(
    text: str,
    parser_manager: ParserManager,
    download_manager: DownloadManager,
    session: aiohttp.ClientSession,
    proxy_url: str = None,
    enable_text_metadata: bool = True,
    enable_rich_media: bool = True
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
    if not (enable_text_metadata or enable_rich_media):
        print("文本元数据和富媒体输出均已关闭，跳过解析")
        return []

    print(f"\n正在解析文本... ({len(text)} 字符)")
    print("-" * 80)

    links_with_parser = parser_manager.extract_all_links(text)
    if not links_with_parser:
        print("未找到可解析的链接或解析失败")
        return []

    await prepare_bilibili_cookie_interaction(links_with_parser, session)
    metadata_list = await parser_manager.parse_text(
        text,
        session,
        links_with_parser=links_with_parser
    )
    
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
        print_metadata(
            metadata,
            url,
            parser_name,
            enable_text_metadata=enable_text_metadata,
            enable_rich_media=enable_rich_media
        )

    has_valid_media = enable_rich_media and any(
        not metadata.get('error') and
        (bool(metadata.get('video_urls')) or bool(metadata.get('image_urls')))
        for metadata in metadata_list
    )
    has_text_result = enable_text_metadata and any(
        not metadata.get('error') and
        any(
            bool(str(metadata.get(key) or "").strip())
            for key in ("title", "author", "desc", "timestamp")
        )
        for metadata in metadata_list
    )
    
    parse_success_count = sum(1 for m in metadata_list if not m.get('error'))
    parse_fail_count = sum(1 for m in metadata_list if m.get('error'))
    
    if not has_valid_media:
        if has_text_result:
            print("\n已解析文本元数据；没有可下载的富媒体内容")
        elif enable_rich_media:
            print("\n⚠️ 没有找到有效的媒体内容（视频或图片）")
        else:
            print("\n富媒体输出已关闭，跳过下载阶段")
        print("\n" + "=" * 80)
        print("统计汇总")
        print("-" * 80)
        print("链接解析:")
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
                print("链接解析:")
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
    print("链接解析:")
    print(f"  成功: {parse_success_count} 个")
    print(f"  失败: {parse_fail_count} 个")
    print(f"  总计: {len(metadata_list)} 个")
    print("\n媒体下载:")
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
    cache_dir: str = None,
    enable_text_metadata: bool = True,
    enable_rich_media: bool = True
):
    """
    主函数，运行交互式测试工具
    
    Args:
        debug_mode: 是否启用 debug 模式
        use_proxy: 是否使用代理
        proxy_url: 代理地址
        cache_dir: 缓存目录
    """
    print("=" * 80)
    print("媒体链接解析测试工具（简化版）")
    print("支持的平台: B站、抖音、TikTok、快手、小红书、微博、闲鱼、今日头条、Twitter/X、小黑盒")
    print("输入 'q' 退出程序")
    print("=" * 80)
    
    if debug_mode:
        logger.setLevel(logging.DEBUG)
        logger.debug("Debug模式已启用")

    if cache_dir is None:
        cache_dir = LOCAL_MEDIA_DIR

    cache_dir_available = check_cache_dir_available(cache_dir)

    bilibili_cookie_runtime_file = ""
    if cache_dir_available:
        bilibili_cookie_dir = Config.build_runtime_dir(cache_dir, "bilibili")
        os.makedirs(bilibili_cookie_dir, exist_ok=True)
        bilibili_cookie_runtime_file = os.path.join(
            bilibili_cookie_dir,
            "cookie.json"
        )
    
    parsers = [
        BilibiliParser(
            cookie_runtime_enabled=cache_dir_available,
            configured_cookie="",
            max_quality=0,
            admin_assist_enabled=False,
            credential_path=bilibili_cookie_runtime_file,
            local_debug_mode=cache_dir_available
        ),
        DouyinParser(),
        TikTokParser(
            use_proxy=use_proxy,
            proxy_url=proxy_url if use_proxy else None,
        ),
        KuaishouParser(),
        WeiboParser(),
        XiaohongshuParser(),
        XianyuParser(),
        ToutiaoParser(),
        XiaoheiheParser(
            use_video_proxy=use_proxy,
            proxy_url=proxy_url
        ) if use_proxy and proxy_url else XiaoheiheParser(),
        TwitterParser(
            use_parse_proxy=use_proxy,
            use_image_proxy=use_proxy,
            use_video_proxy=use_proxy,
            proxy_url=proxy_url
        ) if use_proxy and proxy_url else TwitterParser(),
    ]
    
    parser_manager = ParserManager(parsers)
    
    download_manager = DownloadManager(
        max_video_size_mb=0.0,
        large_video_threshold_mb=0.0,
        cache_dir=cache_dir,
        cache_dir_available=cache_dir_available,
        max_concurrent_downloads=3
    )
    
    print("\n" + "=" * 80)
    print("当前配置:")
    print(f"  Debug 模式: {'启用' if debug_mode else '禁用'}")
    print(f"  文本元数据输出: {'启用' if enable_text_metadata else '禁用'}")
    print(f"  富媒体输出: {'启用' if enable_rich_media else '禁用'}")
    if use_proxy and proxy_url:
        print(f"  代理: {proxy_url}")
    print(f"  缓存目录: {cache_dir}")
    print(f"  缓存目录可用: {'是' if cache_dir_available else '否'}")
    print(
        "  B站Cookie文件: "
        f"{bilibili_cookie_runtime_file or '不可用（缓存目录不可用）'}"
    )
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
                        proxy_url=proxy_url if use_proxy else None,
                        enable_text_metadata=enable_text_metadata,
                        enable_rich_media=enable_rich_media
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
    ENABLE_TEXT_METADATA = True
    ENABLE_RICH_MEDIA = True
    
    CACHE_DIR = LOCAL_MEDIA_DIR
    
    asyncio.run(main(
        debug_mode=DEBUG_MODE,
        use_proxy=USE_PROXY,
        proxy_url=PROXY_URL if USE_PROXY else None,
        cache_dir=CACHE_DIR,
        enable_text_metadata=ENABLE_TEXT_METADATA,
        enable_rich_media=ENABLE_RICH_MEDIA
    ))

