"""项目统一类型定义（TypedDict、别名与结构约束）。"""
from typing import TypedDict, NamedTuple, List, Dict, Optional, Any


class MediaMetadata(TypedDict, total=False):
    """从解析到下载全流程流转的媒体元数据。

    字段按产出阶段分组，total=False 表示任一字段均可缺省。
    """

    # ── 解析阶段（平台解析器产出）──────────────────────

    url: str
    source_url: str
    title: str
    author: str
    desc: str
    timestamp: str
    platform: str
    parser_name: str

    video_urls: List[List[str]]
    image_urls: List[List[str]]
    image_headers: Dict[str, str]
    video_headers: Dict[str, str]
    video_force_download: bool
    video_force_downloads: List[bool]

    access_status: str
    restriction_type: str
    restriction_label: str
    can_access_full_video: bool
    is_preview_only: bool
    access_message: str
    timelength_ms: int
    available_length_ms: int
    hot_comments: List[Dict[str, Any]]

    use_image_proxy: bool
    use_video_proxy: bool
    proxy_url: str

    error: str
    _enable_text_metadata: bool
    _enable_rich_media: bool

    # ── 下载阶段（DownloadManager 回填）─────────────────

    file_paths: List[Optional[str]]
    video_sizes: List[Optional[float]]
    video_status_codes: List[Optional[int]]
    image_status_codes: List[Optional[int]]
    video_modes: List[str]
    image_modes: List[str]
    video_skip_reasons: List[Optional[str]]
    image_skip_reasons: List[Optional[str]]
    media_cache_dir_available: bool
    max_video_size_mb: float
    total_video_size_mb: float
    video_count: int
    image_count: int
    has_valid_media: bool
    use_local_files: bool
    exceeds_max_size: bool
    has_access_denied: bool
    failed_video_count: int
    failed_image_count: int

    # ── 中转阶段（文件 Token 服务注册后回填）──────────────

    use_file_token_service: bool
    file_token_urls: List[Optional[str]]


class LinkBuildMeta(TypedDict):
    """node_builder 为每条链接构建的辅助元数据，用于发送阶段。"""
    link_nodes: List[Any]
    is_large_media: bool
    is_normal: bool
    video_files: List[str]
    temp_files: List[str]


class BuildAllNodesResult(NamedTuple):
    """build_all_nodes 的结构化返回值。"""
    all_link_nodes: List[List[Any]]
    link_metadata: List[LinkBuildMeta]
    temp_files: List[str]
    video_files: List[str]
