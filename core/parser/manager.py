"""解析管理器，维护解析器列表并按链接匹配。"""

import asyncio
from typing import List, Dict, Any, Optional, Tuple

import aiohttp

from ..logger import logger

from ..types import MediaMetadata
from .platform.base import BaseVideoParser
from .router import LinkRouter
from .utils import SkipParse


_PARSER_STRING_FIELDS = frozenset(
    {
        "access_message",
        "access_status",
        "author",
        "desc",
        "image_tls_ciphers",
        "platform",
        "restriction_label",
        "restriction_type",
        "timestamp",
        "title",
    }
)

_PARSER_BOOLEAN_FIELDS = frozenset(
    {
        "is_preview_only",
        "use_image_proxy",
        "use_video_proxy",
        "video_force_download",
    }
)

_PARSER_NULLABLE_BOOLEAN_FIELDS = frozenset({"can_access_full_video"})
_PARSER_NULLABLE_INTEGER_FIELDS = frozenset(
    {"available_length_ms", "timelength_ms"}
)
_PARSER_SPECIAL_FIELDS = frozenset(
    {
        "hot_comments",
        "image_headers",
        "image_urls",
        "proxy_url",
        "url",
        "video_cover_urls",
        "video_headers",
        "video_urls",
    }
)
_PARSER_METADATA_FIELDS = (
    _PARSER_STRING_FIELDS
    | _PARSER_BOOLEAN_FIELDS
    | _PARSER_NULLABLE_BOOLEAN_FIELDS
    | _PARSER_NULLABLE_INTEGER_FIELDS
    | _PARSER_SPECIAL_FIELDS
)
_NON_PARSER_METADATA_FIELDS = (
    frozenset(MediaMetadata.__annotations__) - _PARSER_METADATA_FIELDS
)


class ParserManager:
    """解析器管理器，按链接选择并调用具体平台解析器。"""

    def __init__(self, parsers: List[BaseVideoParser]):
        """初始化解析器管理器；空列表表示插件处于安全停用状态。"""
        self.parsers = list(parsers or [])
        self.link_router = LinkRouter(self.parsers)

    @staticmethod
    def _resolve_platform_name(
        parser: BaseVideoParser, metadata: Optional[MediaMetadata] = None
    ) -> str:
        """按解析结果归一平台名。"""
        explicit = (metadata or {}).get("platform")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()
        return parser.name

    @staticmethod
    def _validate_url_groups(field_name: str, value: Any) -> List[List[str]]:
        """校验媒体 URL 候选组。"""
        if not isinstance(value, list):
            raise TypeError(f"{field_name} 必须是 List[List[str]]")

        groups: List[List[str]] = []
        for group_index, group in enumerate(value):
            if not isinstance(group, list):
                raise TypeError(
                    f"{field_name}[{group_index}] 必须是 URL 字符串列表"
                )
            normalized_group: List[str] = []
            for url_index, candidate in enumerate(group):
                if not isinstance(candidate, str) or not candidate.strip():
                    raise TypeError(
                        f"{field_name}[{group_index}][{url_index}] 必须是非空字符串"
                    )
                normalized_group.append(candidate.strip())
            groups.append(normalized_group)
        return groups

    @staticmethod
    def _validate_headers(field_name: str, value: Any) -> Dict[str, str]:
        """校验媒体请求头。"""
        if not isinstance(value, dict):
            raise TypeError(f"{field_name} 必须是 Dict[str, str]")
        if any(
            not isinstance(key, str) or not isinstance(item, str)
            for key, item in value.items()
        ):
            raise TypeError(f"{field_name} 的键和值必须是字符串")
        return dict(value)

    def _normalize_metadata(
        self, url: str, parser: BaseVideoParser, metadata: MediaMetadata
    ) -> MediaMetadata:
        """补齐并校验解析结果的统一字段。"""
        if any(not isinstance(key, str) for key in metadata):
            raise TypeError("元数据字段名必须是字符串")
        unknown_fields = set(metadata) - set(MediaMetadata.__annotations__)
        if unknown_fields:
            names = ", ".join(sorted(unknown_fields))
            raise ValueError(f"包含未声明字段: {names}")
        misplaced_fields = set(metadata) & _NON_PARSER_METADATA_FIELDS
        if misplaced_fields:
            names = ", ".join(sorted(misplaced_fields))
            raise ValueError(f"解析器不得写入边界或下游阶段字段: {names}")

        for field_name in _PARSER_STRING_FIELDS:
            if field_name not in metadata:
                continue
            if metadata[field_name] is None:
                metadata.pop(field_name)
                continue
            if not isinstance(metadata[field_name], str):
                raise TypeError(f"{field_name} 必须是字符串")

        for field_name in _PARSER_BOOLEAN_FIELDS:
            if field_name in metadata and not isinstance(metadata[field_name], bool):
                raise TypeError(f"{field_name} 必须是布尔值")

        for field_name in _PARSER_NULLABLE_BOOLEAN_FIELDS:
            value = metadata.get(field_name)
            if value is not None and not isinstance(value, bool):
                raise TypeError(f"{field_name} 必须是布尔值或 None")

        for field_name in _PARSER_NULLABLE_INTEGER_FIELDS:
            value = metadata.get(field_name)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool)
            ):
                raise TypeError(f"{field_name} 必须是整数或 None")

        if "hot_comments" in metadata and metadata["hot_comments"] is None:
            metadata.pop("hot_comments")
        hot_comments = metadata.get("hot_comments")
        if hot_comments is not None:
            if not isinstance(hot_comments, list):
                raise TypeError("hot_comments 必须是字典列表")
            for index, comment in enumerate(hot_comments):
                if not isinstance(comment, dict) or any(
                    not isinstance(key, str) for key in comment
                ):
                    raise TypeError(
                        f"hot_comments[{index}] 必须是字符串键的字典"
                    )

        canonical_url = metadata.get("url")
        if canonical_url in (None, ""):
            canonical_url = url
        if not isinstance(canonical_url, str) or not canonical_url.strip():
            raise TypeError("url 必须是非空字符串")

        platform = self._resolve_platform_name(parser, metadata)
        metadata["url"] = canonical_url.strip()
        metadata["source_url"] = url
        metadata["platform"] = platform
        metadata["parser_name"] = parser.name
        metadata["video_urls"] = self._validate_url_groups(
            "video_urls", metadata.get("video_urls", [])
        )
        metadata["image_urls"] = self._validate_url_groups(
            "image_urls", metadata.get("image_urls", [])
        )
        if "video_cover_urls" in metadata:
            cover_groups = self._validate_url_groups(
                "video_cover_urls", metadata["video_cover_urls"]
            )
            video_count = len(metadata["video_urls"])
            if cover_groups and video_count == 0:
                raise ValueError("video_cover_urls 不得在没有视频时单独出现")
            if cover_groups and len(cover_groups) not in (1, video_count):
                raise ValueError("video_cover_urls 必须为空、仅含一个通用封面组或与视频数量一致")
            metadata["video_cover_urls"] = cover_groups

        metadata["image_headers"] = self._validate_headers(
            "image_headers", metadata.get("image_headers", {})
        )
        metadata["video_headers"] = self._validate_headers(
            "video_headers", metadata.get("video_headers", {})
        )
        proxy_url = metadata.get("proxy_url")
        if proxy_url is not None and not isinstance(proxy_url, str):
            raise TypeError("proxy_url 必须是字符串或 None")
        return metadata

    @classmethod
    def _error_metadata(
        cls, url: str, parser: BaseVideoParser, error: str
    ) -> MediaMetadata:
        """构造单链接解析失败结果，供后续统一展示或调试。"""
        return {
            "url": url,
            "source_url": url,
            "error": error,
            "video_urls": [],
            "image_urls": [],
            "image_headers": {},
            "video_headers": {},
            "platform": cls._resolve_platform_name(parser),
            "parser_name": parser.name,
        }

    @staticmethod
    async def _parse_one(
        parser: BaseVideoParser,
        session: aiohttp.ClientSession,
        url: str,
    ) -> Optional[MediaMetadata]:
        """在协程边界内调用解析器，使同步抛错也能按链接隔离。"""
        return await parser.parse(session, url)

    def find_parser(self, url: str) -> Optional[BaseVideoParser]:
        """根据URL查找合适的解析器

        Args:
            url: 视频链接

        Returns:
            匹配的解析器实例，未找到时为None
        """
        try:
            return self.link_router.find_parser(url)
        except ValueError:
            return None

    def extract_all_links(self, text: str) -> List[Tuple[str, BaseVideoParser]]:
        """从文本中提取所有可解析的链接

        Args:
            text: 输入文本

        Returns:
            包含(链接, 解析器)元组的列表。原文可定位项按出现位置排序，
            解析器规范化后无法定位的链接按提取顺序保留在其后。
        """
        return self.link_router.extract_links_with_parser(text)

    async def parse_text(
        self,
        text: str,
        session: aiohttp.ClientSession,
        links_with_parser: Optional[List[Tuple[str, BaseVideoParser]]] = None,
    ) -> List[MediaMetadata]:
        """解析文本中的所有链接

        Args:
            text: 输入文本
            session: aiohttp会话
            links_with_parser: 预先提取好的链接与解析器列表（可选）

        Returns:
            解析结果字典列表（元数据列表）
        """
        if links_with_parser is None:
            links_with_parser = self.extract_all_links(text)
        if not links_with_parser:
            logger.debug("未提取到任何可解析链接")
            return []
        unique_links = {link: parser for link, parser in links_with_parser}
        logger.debug(f"需要解析 {len(unique_links)} 个链接")
        tasks = [
            self._parse_one(parser, session, url)
            for url, parser in unique_links.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        metadata_list: List[MediaMetadata] = []
        link_items = list(unique_links.items())
        for i, result in enumerate(results):
            url, parser = link_items[i]
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, Exception):
                if isinstance(result, SkipParse):
                    logger.debug(f"跳过解析: {url}, 原因: {result}")
                    continue
                logger.error(f"解析URL失败: {url}, 错误: {result}")
                metadata_list.append(self._error_metadata(url, parser, str(result)))
            elif isinstance(result, BaseException):
                raise result
            elif result is None:
                continue
            elif not isinstance(result, dict):
                error = (
                    "解析器返回了无效结果类型: "
                    f"{type(result).__name__}（应为 dict 或 None）"
                )
                logger.error(f"解析URL失败: {url}, 错误: {error}")
                metadata_list.append(self._error_metadata(url, parser, error))
            elif not result:
                error = "解析器返回了空元数据"
                logger.error(f"解析URL失败: {url}, 错误: {error}")
                metadata_list.append(self._error_metadata(url, parser, error))
            else:
                try:
                    metadata_list.append(self._normalize_metadata(url, parser, result))
                except (TypeError, ValueError) as exc:
                    error = f"解析器返回的元数据不符合契约: {exc}"
                    logger.error(f"解析URL失败: {url}, 错误: {error}")
                    metadata_list.append(self._error_metadata(url, parser, error))
        logger.debug(f"解析完成，获得 {len(metadata_list)} 条元数据")
        return metadata_list
