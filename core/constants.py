class Config:
    """配置常量类，包含下载、解析等功能的配置参数"""
    
    # 超时配置
    DEFAULT_TIMEOUT = 30  # 默认超时时间（秒）
    VIDEO_SIZE_CHECK_TIMEOUT = 10  # 视频大小检查超时时间（秒）
    IMAGE_DOWNLOAD_TIMEOUT = 10  # 图片下载超时时间（秒）
    VIDEO_DOWNLOAD_TIMEOUT = 300  # 视频下载超时时间（秒）
    
    # 视频大小阈值配置
    DEFAULT_LARGE_VIDEO_THRESHOLD_MB = 40.0  # 默认大视频阈值（MB），超过此大小的视频将被视为大视频
    MAX_LARGE_VIDEO_THRESHOLD_MB = 100.0  # 最大大视频阈值（MB），用于限制大视频的判断上限
    
    # 流式下载配置
    STREAM_DOWNLOAD_CHUNK_SIZE = 2 * 1024 * 1024  # 流式下载块大小（字节），2MB
    
    # 范围下载配置
    RANGE_DOWNLOAD_CHUNK_SIZE = 2 * 1024 * 1024  # 范围下载块大小（字节），2MB
    RANGE_DOWNLOAD_MAX_CONCURRENT = 64  # 范围下载最大并发连接数
    
    # M3U8下载配置
    M3U8_MAX_CONCURRENT_SEGMENTS = 10  # M3U8视频下载时最大并发段数
    
    # 并发控制配置
    DOWNLOAD_MANAGER_MAX_CONCURRENT = 3  # 下载管理器最大并发任务数
    PARSER_MAX_CONCURRENT = 10  # 解析器最大并发任务数
    
    # 调试配置
    DEBUG_MODE = False  # 调试模式开关，开启后会输出更详细的调试信息

