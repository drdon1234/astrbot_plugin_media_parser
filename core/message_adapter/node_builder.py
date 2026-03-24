"""消息节点构建器，将解析结果转换为可发送消息节点。"""
import os
from typing import Dict, Any, List, Optional, Union

from ..logger import logger

from astrbot.api.message_components import Plain, Image, Video, Node, Nodes

from ..downloader.utils import strip_media_prefixes
from ..types import BuildAllNodesResult, LinkBuildMeta


def build_text_node(metadata: Dict[str, Any], max_video_size_mb: float = 0.0, enable_text_metadata: bool = True) -> Optional[Plain]:
    """构建文本节点

    Args:
        metadata: 元数据字典
        max_video_size_mb: 最大允许的视频大小(MB)，用于显示详细的错误信息
        enable_text_metadata: 是否包含视频图文文本信息的附加文本

    Returns:
        Plain文本节点，无内容时为None
    """
    if not enable_text_metadata:
        return None
        
    text_parts = []
    
    if metadata.get('title'):
        text_parts.append(f"标题：{metadata['title']}")
    if metadata.get('author'):
        text_parts.append(f"作者：{metadata['author']}")
    if metadata.get('desc'):
        text_parts.append(f"简介：{metadata['desc']}")
    if metadata.get('timestamp'):
        text_parts.append(f"发布时间：{metadata['timestamp']}")
    
    video_count = metadata.get('video_count', 0)
    if video_count > 0:
        actual_max_video_size_mb = metadata.get('max_video_size_mb')
        total_video_size_mb = metadata.get('total_video_size_mb', 0.0)
        
        if actual_max_video_size_mb is not None:
            if video_count == 1:
                text_parts.append(f"视频大小：{actual_max_video_size_mb:.1f} MB")
            else:
                text_parts.append(
                    f"视频大小：最大 {actual_max_video_size_mb:.1f} MB "
                    f"(共 {video_count} 个视频, 总计 {total_video_size_mb:.1f} MB)"
                )
    
    has_valid_media = metadata.get('has_valid_media')
    video_urls = metadata.get('video_urls', [])
    image_urls = metadata.get('image_urls', [])
    
    has_text_metadata = bool(
        metadata.get('title') or 
        metadata.get('author') or 
        metadata.get('desc') or 
        metadata.get('timestamp')
    )

    access_status = metadata.get("access_status")
    access_message = metadata.get("access_message")
    available_length_ms = metadata.get("available_length_ms")
    timelength_ms = metadata.get("timelength_ms")
    is_preview_only = metadata.get("is_preview_only")
    if access_status and access_status != "full" and access_message:
        text_parts.append(f"时长：{access_message}")
    elif is_preview_only and available_length_ms:
        try:
            available_seconds = max(0, int(available_length_ms) // 1000)
            full_seconds = (
                max(0, int(timelength_ms) // 1000)
                if timelength_ms is not None else
                None
            )
            available_min, available_sec = divmod(available_seconds, 60)
            if full_seconds is not None:
                full_min, full_sec = divmod(full_seconds, 60)
                text_parts.append(
                    f"时长：当前可解析 {available_min:02d}:{available_sec:02d} / "
                    f"全长 {full_min:02d}:{full_sec:02d}"
                )
            else:
                text_parts.append(
                    f"时长：当前可解析 {available_min:02d}:{available_sec:02d}"
                )
        except (TypeError, ValueError):
            pass

    hot_comments = metadata.get("hot_comments", [])
    if isinstance(hot_comments, list) and hot_comments:
        text_parts.append(f"热评（{len(hot_comments)}条）:")
        total = len(hot_comments)
        for idx, item in enumerate(hot_comments, start=1):
            if not isinstance(item, dict):
                continue
            username = str(item.get("username", "") or "").strip() or "未知用户"
            uid = str(item.get("uid", "") or "").strip()
            try:
                likes = int(item.get("likes", 0) or 0)
            except (TypeError, ValueError):
                likes = 0
            time_text = str(item.get("time", "") or "").strip() or "-"
            message = str(item.get("message", "") or "").strip() or "（无文本内容）"
            user_label = f"{username}(uid:{uid})" if uid else username
            text_parts.append(f"[{idx}] {user_label}")
            text_parts.append(f"点赞: {likes} | 时间: {time_text}")
            text_parts.append(message)
            if idx < total:
                text_parts.append("")
    
    if metadata.get('error'):
        text_parts.append(f"解析失败：{metadata['error']}")

    if has_valid_media is False and (video_urls or image_urls) and has_text_metadata and not metadata.get('exceeds_max_size'):
        if metadata.get('has_access_denied'):
            text_parts.append("解析失败：媒体访问被拒绝(403 Forbidden)")
        else:
            text_parts.append("解析失败：直链内未找到有效媒体")
    
    if metadata.get('exceeds_max_size'):
        actual_video_size = metadata.get('max_video_size_mb')
        if actual_video_size is not None:
            if max_video_size_mb > 0:
                text_parts.append(
                    f"解析失败：视频大小超过管理员设定的限制（{actual_video_size:.1f}MB > {max_video_size_mb:.1f}MB）"
                )
            else:
                text_parts.append(f"解析失败：视频大小超过限制（{actual_video_size:.1f}MB）")
    
    failed_video_count = metadata.get('failed_video_count', 0)
    failed_image_count = metadata.get('failed_image_count', 0)
    video_count = metadata.get('video_count', 0)
    image_count = metadata.get('image_count', 0)
    
    if (failed_video_count > 0 or failed_image_count > 0) and (video_count > 0 or image_count > 0):
        failure_parts = []
        if video_count > 0:
            failure_parts.append(f"视频 {failed_video_count}/{video_count}")
        if image_count > 0:
            failure_parts.append(f"图片 {failed_image_count}/{image_count}")
        if failure_parts:
            text_parts.append(f"下载失败：{', '.join(failure_parts)}")
    
    if metadata.get('url'):
        text_parts.append(f"原始链接：{metadata['url']}")
    
    if not text_parts:
        return None
    desc_text = "\n".join(text_parts)
    return Plain(desc_text)


def build_media_nodes(
    metadata: Dict[str, Any],
    use_local_files: bool = False
) -> List[Union[Image, Video]]:
    """构建媒体节点

    Args:
        metadata: 元数据字典
        use_local_files: 是否使用本地文件

    Returns:
        媒体节点列表（Image或Video节点）
    """
    nodes = []
    url = metadata.get('url', '')
    
    if metadata.get('exceeds_max_size'):
        logger.debug(f"媒体超过大小限制，跳过节点构建: {url}")
        return nodes
    
    has_valid_media = metadata.get('has_valid_media')
    if has_valid_media is None:
        logger.warning(f"元数据中has_valid_media字段为None，视为False: {url}")
        has_valid_media = False
    
    if has_valid_media is False:
        logger.debug(f"媒体无效，跳过节点构建: {url}")
        return nodes
    
    video_urls = metadata.get('video_urls', [])
    image_urls = metadata.get('image_urls', [])
    file_paths = metadata.get('file_paths', [])
    video_sizes = metadata.get('video_sizes', [])
    use_fts = metadata.get('use_file_token_service', False)
    file_token_urls = metadata.get('file_token_urls', [])
    
    logger.debug(
        f"构建媒体节点: {url}, "
        f"视频: {len(video_urls)}, 图片: {len(image_urls)}, "
        f"文件路径: {len(file_paths)}, 使用本地文件: {use_local_files}, "
        f"文件Token服务: {use_fts}"
    )
    
    if not video_urls and not image_urls and not file_paths:
        logger.debug(f"无媒体内容，跳过节点构建: {url}")
        return nodes
    
    file_idx = 0
    
    for idx, url_list in enumerate(video_urls):
        if not url_list or not isinstance(url_list, list):
            file_idx += 1
            continue
        
        if video_sizes and idx < len(video_sizes) and video_sizes[idx] is None:
            file_idx += 1
            continue
        
        video_url = url_list[0] if url_list else None
        if not video_url:
            file_idx += 1
            continue
        
        token_url = (
            file_token_urls[file_idx]
            if use_fts and file_idx < len(file_token_urls)
            else None
        )
        if token_url:
            try:
                nodes.append(Video.fromURL(token_url))
                file_idx += 1
                continue
            except Exception as e:
                logger.warning(f"使用Token URL构建视频节点失败: {token_url}, 错误: {e}")
        
        if use_fts:
            actual_video_url = strip_media_prefixes(video_url)
            try:
                nodes.append(Video.fromURL(actual_video_url))
            except Exception as e:
                logger.warning(f"构建视频节点失败(直链回退): {actual_video_url}, 错误: {e}")
        elif use_local_files and file_idx < len(file_paths) and file_paths[file_idx] and os.path.exists(file_paths[file_idx]):
            try:
                nodes.append(Video.fromFileSystem(file_paths[file_idx]))
            except Exception as e:
                logger.warning(f"构建视频节点失败: {file_paths[file_idx]}, 错误: {e}")
        else:
            actual_video_url = strip_media_prefixes(video_url)
            try:
                nodes.append(Video.fromURL(actual_video_url))
            except Exception as e:
                logger.warning(f"构建视频节点失败: {actual_video_url}, 错误: {e}")
        
        file_idx += 1
    
    for url_list in image_urls:
        if not url_list or not isinstance(url_list, list):
            file_idx += 1
            continue
        
        image_url = url_list[0] if url_list else None
        if not image_url:
            file_idx += 1
            continue
        
        token_url = (
            file_token_urls[file_idx]
            if use_fts and file_idx < len(file_token_urls)
            else None
        )
        if token_url:
            try:
                nodes.append(Image.fromURL(token_url))
                file_idx += 1
                continue
            except Exception as e:
                logger.warning(f"使用Token URL构建图片节点失败: {token_url}, 错误: {e}")
        
        if use_fts:
            try:
                nodes.append(Image.fromURL(image_url))
            except Exception as e:
                logger.warning(f"构建图片节点失败(直链回退): {image_url}, 错误: {e}")
        elif use_local_files and file_idx < len(file_paths) and file_paths[file_idx]:
            try:
                nodes.append(Image.fromFileSystem(file_paths[file_idx]))
            except Exception as e:
                logger.warning(f"构建图片节点失败: {file_paths[file_idx]}, 错误: {e}")
        else:
            try:
                nodes.append(Image.fromURL(image_url))
            except Exception as e:
                logger.warning(f"构建图片节点失败: {image_url}, 错误: {e}")
        
        file_idx += 1
    
    logger.debug(f"构建媒体节点完成: {url}, 共 {len(nodes)} 个节点")
    return nodes


def build_nodes_for_link(
    metadata: Dict[str, Any],
    use_local_files: bool = False,
    max_video_size_mb: float = 0.0,
    enable_text_metadata: bool = True
) -> List[Union[Plain, Image, Video]]:
    """构建单个链接的节点列表

    Args:
        metadata: 元数据字典
        use_local_files: 是否使用本地文件
        max_video_size_mb: 最大允许的视频大小(MB)，用于显示详细的错误信息
        enable_text_metadata: 是否发送图文文本消息

    Returns:
        节点列表（Plain、Image、Video对象）
    """
    nodes = []
    
    text_node = build_text_node(metadata, max_video_size_mb, enable_text_metadata)
    if text_node:
        nodes.append(text_node)
    
    media_nodes = build_media_nodes(metadata, use_local_files)
    nodes.extend(media_nodes)
    
    return nodes


def is_pure_image_gallery(nodes: List[Union[Plain, Image, Video]]) -> bool:
    """判断节点列表是否是纯图片图集

    Args:
        nodes: 节点列表

    Returns:
        是否为纯图片图集
    """
    has_video = False
    has_image = False
    for node in nodes:
        if isinstance(node, Video):
            has_video = True
            break
        elif isinstance(node, Image):
            has_image = True
    return has_image and not has_video


def build_all_nodes(
    metadata_list: List[Dict[str, Any]],
    is_auto_pack: bool,
    large_video_threshold_mb: float = 0.0,
    max_video_size_mb: float = 0.0,
    enable_text_metadata: bool = True
) -> BuildAllNodesResult:
    """构建所有链接的节点，处理消息打包逻辑

    Args:
        metadata_list: 元数据列表
        is_auto_pack: 是否打包为Node
        large_video_threshold_mb: 大视频阈值(MB)
        max_video_size_mb: 最大允许的视频大小(MB)，用于显示错误信息
        enable_text_metadata: 是否发送图文文本消息

    Returns:
        BuildAllNodesResult 命名元组
    """
    all_link_nodes = []
    link_metadata = []
    temp_files = []
    video_files = []
    
    logger.debug(f"开始构建所有节点，元数据数量: {len(metadata_list)}, 打包模式: {is_auto_pack}")
    
    for idx, metadata in enumerate(metadata_list):
        url = metadata.get('url', '')
        max_video_size = metadata.get('max_video_size_mb')
        exceeds_max_size = metadata.get('exceeds_max_size', False)
        is_large_media = False
        if large_video_threshold_mb > 0 and max_video_size is not None and not exceeds_max_size:
            if max_video_size > large_video_threshold_mb:
                is_large_media = True
        
        use_local_files = metadata.get('use_local_files', False)
        
        logger.debug(
            f"构建节点[{idx}]: {url}, "
            f"大媒体: {is_large_media}, 使用本地文件: {use_local_files}"
        )
        
        link_nodes = build_nodes_for_link(
            metadata,
            use_local_files,
            max_video_size_mb,
            enable_text_metadata
        )
        
        logger.debug(f"节点构建完成[{idx}]: {url}, 节点数量: {len(link_nodes)}")
        
        link_file_paths = metadata.get('file_paths', [])
        link_video_files = []
        link_temp_files = []
        
        if use_local_files:
            video_urls = metadata.get('video_urls', [])
            video_count = len(video_urls)
            
            for fp_idx, file_path in enumerate(link_file_paths):
                if file_path:
                    if fp_idx < video_count:
                        link_video_files.append(file_path)
                        video_files.append(file_path)
                    else:
                        link_temp_files.append(file_path)
                        temp_files.append(file_path)
        
        all_link_nodes.append(link_nodes)
        link_metadata.append(LinkBuildMeta(
            link_nodes=link_nodes,
            is_large_media=is_large_media,
            is_normal=not is_large_media,
            video_files=link_video_files,
            temp_files=link_temp_files,
        ))
    
    logger.debug(
        f"所有节点构建完成: "
        f"链接节点: {len(all_link_nodes)}, "
        f"临时文件: {len(temp_files)}, "
        f"视频文件: {len(video_files)}"
    )
    
    return BuildAllNodesResult(all_link_nodes, link_metadata, temp_files, video_files)

