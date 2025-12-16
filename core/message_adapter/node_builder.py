# -*- coding: utf-8 -*-
"""
节点构建模块
负责从元数据构建astrbot消息节点
集中所有 astrbot 消息组件的导入
"""
import os
from typing import Dict, Any, List, Optional, Tuple, Union

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from astrbot.api.message_components import Plain, Image, Video, Node, Nodes

from ..file_cleaner import cleanup_file


def build_text_node(metadata: Dict[str, Any], max_video_size_mb: float = 0.0) -> Optional[Plain]:
    """构建文本节点

    Args:
        metadata: 元数据字典
        max_video_size_mb: 最大允许的视频大小(MB)，用于显示详细的错误信息

    Returns:
        Plain文本节点，如果无内容返回None
    """
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
    
    logger.debug(
        f"构建媒体节点: {url}, "
        f"视频: {len(video_urls)}, 图片: {len(image_urls)}, "
        f"文件路径: {len(file_paths)}, 使用本地文件: {use_local_files}"
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
        
        video_file_path = None
        if use_local_files and file_idx < len(file_paths):
            video_file_path = file_paths[file_idx]
        
        if use_local_files and video_file_path and os.path.exists(video_file_path):
            try:
                nodes.append(Video.fromFileSystem(video_file_path))
            except Exception as e:
                logger.warning(f"构建视频节点失败: {video_file_path}, 错误: {e}")
        else:
            try:
                nodes.append(Video.fromURL(video_url))
            except Exception as e:
                logger.warning(f"构建视频节点失败: {video_url}, 错误: {e}")
        
        file_idx += 1
    
    for url_list in image_urls:
        if not url_list or not isinstance(url_list, list):
            file_idx += 1
            continue
        
        image_url = url_list[0] if url_list else None
        if not image_url:
            file_idx += 1
            continue
        
        image_file_path = None
        if use_local_files and file_idx < len(file_paths):
            image_file_path = file_paths[file_idx]
        
        if use_local_files and image_file_path:
            try:
                nodes.append(Image.fromFileSystem(image_file_path))
            except Exception as e:
                logger.warning(f"构建图片节点失败: {image_file_path}, 错误: {e}")
                cleanup_file(image_file_path)
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
    sender_name: str = "",
    sender_id: Any = None,
    max_video_size_mb: float = 0.0
) -> List[Union[Plain, Image, Video]]:
    """构建单个链接的节点列表

    Args:
        metadata: 元数据字典
        use_local_files: 是否使用本地文件
        sender_name: 发送者名称（未使用，保留兼容性）
        sender_id: 发送者ID（未使用，保留兼容性）
        max_video_size_mb: 最大允许的视频大小(MB)，用于显示详细的错误信息

    Returns:
        节点列表（Plain、Image、Video对象）
    """
    nodes = []
    
    text_node = build_text_node(metadata, max_video_size_mb)
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
        如果是纯图片图集返回True，否则返回False
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
    sender_name: str,
    sender_id: Any,
    large_video_threshold_mb: float = 0.0,
    max_video_size_mb: float = 0.0
) -> Tuple[List[List[Union[Plain, Image, Video]]], List[Dict], List[str], List[str]]:
    """构建所有链接的节点，处理消息打包逻辑

    Args:
        metadata_list: 元数据列表
        is_auto_pack: 是否打包为Node
        sender_name: 发送者名称
        sender_id: 发送者ID
        large_video_threshold_mb: 大视频阈值(MB)
        max_video_size_mb: 最大允许的视频大小(MB)，用于显示错误信息

    Returns:
        包含(all_link_nodes, link_metadata, temp_files, video_files)的元组
    """
    all_link_nodes = []
    link_metadata = []
    temp_files = []
    video_files = []
    separator = "-------------------------------------"
    
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
            sender_name,
            sender_id,
            max_video_size_mb
        )
        
        logger.debug(f"节点构建完成[{idx}]: {url}, 节点数量: {len(link_nodes)}")
        
        link_file_paths = metadata.get('file_paths', [])
        link_video_files = []
        link_temp_files = []
        
        if use_local_files:
            video_urls = metadata.get('video_urls', [])
            video_count = len(video_urls)
            
            for idx, file_path in enumerate(link_file_paths):
                if file_path:
                    if idx < video_count:
                        link_video_files.append(file_path)
                        video_files.append(file_path)
                    else:
                        link_temp_files.append(file_path)
                        temp_files.append(file_path)
        
        all_link_nodes.append(link_nodes)
        link_metadata.append({
            'link_nodes': link_nodes,
            'is_large_media': is_large_media,
            'is_normal': not is_large_media,
            'video_files': link_video_files,
            'temp_files': link_temp_files
        })
    
    logger.debug(
        f"所有节点构建完成: "
        f"链接节点: {len(all_link_nodes)}, "
        f"临时文件: {len(temp_files)}, "
        f"视频文件: {len(video_files)}"
    )
    
    return all_link_nodes, link_metadata, temp_files, video_files

