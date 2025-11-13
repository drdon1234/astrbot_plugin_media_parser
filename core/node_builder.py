# -*- coding: utf-8 -*-
"""
节点构建模块
负责从元数据构建astrbot消息节点
"""
import os
from typing import Dict, Any, List, Optional, Tuple, Union

from astrbot.api import logger
from astrbot.api.message_components import Plain, Image, Video, Node, Nodes


def build_text_node(metadata: Dict[str, Any]) -> Optional[Plain]:
    """构建文本节点。

    Args:
        metadata: 元数据字典

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
    media_type = metadata.get('media_type', 'video')
    if media_type in ('video', 'mixed'):
        video_count = metadata.get('video_count', 0)
        max_video_size_mb = metadata.get('max_video_size_mb')
        total_video_size_mb = metadata.get('total_video_size_mb', 0.0)
        
        if max_video_size_mb is not None and video_count > 0:
            if video_count == 1:
                text_parts.append(f"视频大小：{max_video_size_mb:.1f} MB")
            else:
                text_parts.append(
                    f"视频大小：最大 {max_video_size_mb:.1f} MB "
                    f"(共 {video_count} 个视频, 总计 {total_video_size_mb:.1f} MB)"
                )
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
    """构建媒体节点。

    Args:
        metadata: 元数据字典
        use_local_files: 是否使用本地文件

    Returns:
        媒体节点列表（Image或Video节点）
    """
    nodes = []
    media_type = metadata.get('media_type', 'video')
    media_urls = metadata.get('media_urls', [])
    file_paths = metadata.get('file_paths', [])
    thumb_url = metadata.get('thumb_url')
    
    if not media_urls and not file_paths:
        return nodes
    
    if media_type == 'mixed':
        video_urls = metadata.get('video_urls', [])
        image_urls = metadata.get('image_urls', [])
        video_thumb_urls = metadata.get('video_thumb_urls', [])
        
        video_count = len(video_urls)
        for idx in range(video_count):
            video_url = video_urls[idx] if idx < len(video_urls) else None
            if not video_url:
                continue
            
            video_thumb = None
            if idx < len(video_thumb_urls):
                video_thumb = video_thumb_urls[idx]
            
            video_file_path = None
            if use_local_files and file_paths:
                video_url_idx = media_urls.index(video_url) if video_url in media_urls else None
                if video_url_idx is not None and video_url_idx < len(file_paths):
                    video_file_path = file_paths[video_url_idx]
            
            if use_local_files and video_file_path and os.path.exists(video_file_path):
                try:
                    nodes.append(Video.fromFileSystem(video_file_path))
                except Exception as e:
                    logger.warning(f"构建视频节点失败: {video_file_path}, 错误: {e}")
            else:
                try:
                    if video_thumb:
                        nodes.append(Video.fromURL(video_url, cover=video_thumb))
                    else:
                        nodes.append(Video.fromURL(video_url))
                except Exception as e:
                    logger.warning(f"构建视频节点失败: {video_url}, 错误: {e}")
        
        for idx in range(len(image_urls)):
            image_url = image_urls[idx]
            if not image_url:
                continue
            
            image_file_path = None
            if use_local_files and file_paths:
                image_url_idx = media_urls.index(image_url) if image_url in media_urls else None
                if image_url_idx is not None and image_url_idx < len(file_paths):
                    image_file_path = file_paths[image_url_idx]
            
            if use_local_files and image_file_path and os.path.exists(image_file_path):
                try:
                    nodes.append(Image.fromFileSystem(image_file_path))
                except Exception as e:
                    logger.warning(f"构建图片节点失败: {image_file_path}, 错误: {e}")
            else:
                try:
                    nodes.append(Image.fromURL(image_url))
                except Exception as e:
                    logger.warning(f"构建图片节点失败: {image_url}, 错误: {e}")
    elif media_type == 'gallery':
        if use_local_files and file_paths:
            for file_path in file_paths:
                if file_path and os.path.exists(file_path):
                    try:
                        nodes.append(Image.fromFileSystem(file_path))
                    except Exception as e:
                        logger.warning(f"构建图片节点失败: {file_path}, 错误: {e}")
                        if os.path.exists(file_path):
                            try:
                                os.unlink(file_path)
                            except Exception:
                                pass
        else:
            for url in media_urls:
                if url and isinstance(url, str) and url.startswith(('http://', 'https://')):
                    try:
                        nodes.append(Image.fromURL(url))
                    except Exception as e:
                        logger.warning(f"构建图片节点失败: {url}, 错误: {e}")
    elif media_type == 'video':
        video_urls = metadata.get('video_urls', media_urls)
        video_thumb_urls = metadata.get('video_thumb_urls', [])
        
        for idx, video_url in enumerate(video_urls):
            if not video_url:
                continue
            
            video_thumb = None
            if idx < len(video_thumb_urls):
                video_thumb = video_thumb_urls[idx]
            elif thumb_url:
                video_thumb = thumb_url
            
            video_file_path = None
            if use_local_files and file_paths:
                if idx < len(file_paths):
                    video_file_path = file_paths[idx]
            
            if use_local_files and video_file_path and os.path.exists(video_file_path):
                try:
                    nodes.append(Video.fromFileSystem(video_file_path))
                except Exception as e:
                    logger.warning(f"构建视频节点失败: {video_file_path}, 错误: {e}")
            else:
                try:
                    if video_thumb:
                        nodes.append(Video.fromURL(video_url, cover=video_thumb))
                    else:
                        nodes.append(Video.fromURL(video_url))
                except Exception as e:
                    logger.warning(f"构建视频节点失败: {video_url}, 错误: {e}")
    elif media_type == 'image':
        if use_local_files and file_paths and file_paths[0]:
            file_path = file_paths[0]
            if os.path.exists(file_path):
                try:
                    nodes.append(Image.fromFileSystem(file_path))
                except Exception as e:
                    logger.warning(f"构建图片节点失败: {file_path}, 错误: {e}")
        else:
            if media_urls and media_urls[0]:
                image_url = media_urls[0]
                try:
                    nodes.append(Image.fromURL(image_url))
                except Exception as e:
                    logger.warning(f"构建图片节点失败: {image_url}, 错误: {e}")
    
    return nodes


def build_nodes_for_link(
    metadata: Dict[str, Any],
    use_local_files: bool = False,
    sender_name: str = "",
    sender_id: Any = None
) -> List[Union[Plain, Image, Video]]:
    """构建单个链接的节点列表。

    Args:
        metadata: 元数据字典
        use_local_files: 是否使用本地文件
        sender_name: 发送者名称（未使用，保留兼容性）
        sender_id: 发送者ID（未使用，保留兼容性）

    Returns:
        节点列表（Plain、Image、Video对象）
    """
    nodes = []
    
    text_node = build_text_node(metadata)
    if text_node:
        nodes.append(text_node)
    
    media_nodes = build_media_nodes(metadata, use_local_files)
    nodes.extend(media_nodes)
    
    return nodes


def is_pure_image_gallery(nodes: List[Union[Plain, Image, Video]]) -> bool:
    """判断节点列表是否是纯图片图集。

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
    large_video_threshold_mb: float = 0.0
) -> Tuple[List[List[Node]], List[Dict], List[str], List[str]]:
    """构建所有链接的节点，处理消息打包逻辑。

    Args:
        metadata_list: 元数据列表
        is_auto_pack: 是否打包为Node
        sender_name: 发送者名称
        sender_id: 发送者ID
        large_video_threshold_mb: 大视频阈值(MB)

    Returns:
        包含(all_link_nodes, link_metadata, temp_files, video_files)的元组
    """
    all_link_nodes = []
    link_metadata = []
    temp_files = []
    video_files = []
    separator = "-------------------------------------"
    
    for metadata in metadata_list:
        max_video_size = metadata.get('max_video_size_mb')
        is_large_media = False
        if large_video_threshold_mb > 0 and max_video_size is not None:
            if max_video_size > large_video_threshold_mb:
                is_large_media = True
        
        if metadata.get('exceeds_max_size'):
            error_text = f"解析失败：视频大小超过限制\n原始链接：{metadata.get('url', '')}"
            link_nodes = [Plain(error_text)]
            all_link_nodes.append(link_nodes)
            link_metadata.append({
                'link_nodes': link_nodes,
                'is_large_media': False,
                'is_normal': False,
                'video_files': [],
                'temp_files': []
            })
            continue
        
        use_local_files = metadata.get('use_local_files', False)
        
        link_nodes = build_nodes_for_link(
            metadata,
            use_local_files,
            sender_name,
            sender_id
        )
        
        link_file_paths = metadata.get('file_paths', [])
        link_video_files = []
        link_temp_files = []
        
        if use_local_files:
            for file_path in link_file_paths:
                if file_path:
                    if metadata.get('media_type') == 'video':
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
    
    return all_link_nodes, link_metadata, temp_files, video_files

