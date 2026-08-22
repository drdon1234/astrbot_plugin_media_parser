"""消息适配子系统公共入口，具体实现仅在访问时加载。"""

from importlib import import_module
from typing import TYPE_CHECKING, Any


_EXPORTS = {
    "MessageSender": (".sender", "MessageSender"),
    "build_all_nodes": (".node_builder", "build_all_nodes"),
    "build_translation_nodes_for_all": (
        ".node_builder",
        "build_translation_nodes_for_all",
    ),
    "is_pure_image_gallery": (".node_builder", "is_pure_image_gallery"),
    "summarize_node_counts": (".node_builder", "summarize_node_counts"),
}

__all__ = (
    "MessageSender",
    "build_all_nodes",
    "build_translation_nodes_for_all",
    "is_pure_image_gallery",
    "summarize_node_counts",
)


if TYPE_CHECKING:
    from .node_builder import (
        build_all_nodes as build_all_nodes,
        build_translation_nodes_for_all as build_translation_nodes_for_all,
        is_pure_image_gallery as is_pure_image_gallery,
        summarize_node_counts as summarize_node_counts,
    )
    from .sender import MessageSender as MessageSender


def __getattr__(name: str) -> Any:
    """延迟解析公共符号，避免导入包时强制加载 AstrBot。"""
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """向交互式工具暴露延迟导出的公共符号。"""
    return sorted(set(globals()) | set(__all__))
