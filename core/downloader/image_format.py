"""受支持图片格式的 MIME、签名与文件后缀边界。"""

from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit


DIRECT_IMAGE_FORMATS = frozenset({"jpeg", "png"})

_FORMAT_SUFFIXES = {
    "jpeg": ".jpg",
    "png": ".png",
    "webp": ".webp",
    "gif": ".gif",
    "avif": ".avif",
    "bmp": ".bmp",
}

_URL_SUFFIX_FORMATS = {
    suffix: image_format for image_format, suffix in _FORMAT_SUFFIXES.items()
}
_URL_SUFFIX_FORMATS[".jpeg"] = "jpeg"

_CONTENT_TYPE_FORMATS = {
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/pjpeg": "jpeg",
    "image/jfif": "jpeg",
    "image/png": "png",
    "image/x-png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/avif": "avif",
    "image/bmp": "bmp",
    "image/x-bmp": "bmp",
    "image/x-ms-bmp": "bmp",
}

_GENERIC_BINARY_CONTENT_TYPES = frozenset(
    {
        "application/octet-stream",
        "binary/octet-stream",
        "application/x-binary",
    }
)


def normalize_content_type(content_type: str) -> str:
    """去除参数并规范化 Content-Type。"""
    return (content_type or "").split(";", 1)[0].strip().lower()


def image_format_from_content_type(content_type: str) -> Optional[str]:
    """返回白名单 MIME 对应的图片格式。"""
    return _CONTENT_TYPE_FORMATS.get(normalize_content_type(content_type))


def is_supported_image_content_type(content_type: str) -> bool:
    """仅接受已知栅格 MIME、泛型二进制或缺失的 Content-Type。"""
    normalized = normalize_content_type(content_type)
    return (
        not normalized
        or normalized in _CONTENT_TYPE_FORMATS
        or normalized in _GENERIC_BINARY_CONTENT_TYPES
    )


def image_content_type_requires_probe(content_type: str) -> bool:
    """判断仅凭响应头是否不足以确认图片格式。"""
    normalized = normalize_content_type(content_type)
    return not normalized or normalized in _GENERIC_BINARY_CONTENT_TYPES


def _is_bmp_header(data: bytes) -> bool:
    """校验 BMP 文件头与 DIB 头的基本边界。"""
    if len(data) < 26 or not data.startswith(b"BM"):
        return False
    pixel_offset = int.from_bytes(data[10:14], "little")
    dib_size = int.from_bytes(data[14:18], "little")
    return dib_size >= 12 and pixel_offset >= 14 + dib_size


def _is_avif_header(data: bytes) -> bool:
    """识别 ISO BMFF 中的 AVIF 主品牌或兼容品牌。"""
    if len(data) < 16 or data[4:8] != b"ftyp":
        return False
    box_size = int.from_bytes(data[:4], "big")
    if box_size < 16:
        return False
    brand_data = data[8 : min(len(data), box_size)]
    brands = {brand_data[:4]}
    brands.update(
        brand_data[offset : offset + 4] for offset in range(8, len(brand_data) - 3, 4)
    )
    return bool(brands & {b"avif", b"avis"})


def detect_supported_image_format(data: bytes) -> Optional[str]:
    """根据文件签名识别受支持的安全栅格格式。"""
    if not data:
        return None
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    if _is_avif_header(data):
        return "avif"
    if _is_bmp_header(data):
        return "bmp"
    return None


def detect_supported_image_file_format(file_path: str) -> Optional[str]:
    """读取文件头并识别受支持的图片格式。"""
    with open(file_path, "rb") as image_file:
        return detect_supported_image_format(image_file.read(512))


def image_suffix(image_format: str) -> str:
    """返回已知图片格式的规范文件后缀。"""
    try:
        return _FORMAT_SUFFIXES[image_format]
    except KeyError as exc:
        raise ValueError(f"不支持的图片格式: {image_format}") from exc


def infer_image_suffix(content_type: str = "", url: str = "") -> str:
    """按白名单 MIME 或 URL 路径推导缓存后缀。"""
    declared_format = image_format_from_content_type(content_type)
    if declared_format:
        return image_suffix(declared_format)

    try:
        url_suffix = Path(urlsplit(url or "").path).suffix.lower()
    except (TypeError, ValueError):
        url_suffix = ""
    url_format = _URL_SUFFIX_FORMATS.get(url_suffix)
    return image_suffix(url_format) if url_format else ".jpg"


def normalized_image_path(file_path: str, image_format: str) -> str:
    """按真实文件签名生成同目录下的规范路径。"""
    return str(Path(file_path).with_suffix(image_suffix(image_format)))
