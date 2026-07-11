"""文本元数据字段可见性的统一读取。"""
from typing import Any, Dict


TEXT_METADATA_FIELD_DEFAULTS = {
    "title": True,
    "author": True,
    "timestamp": True,
    "original_link": True,
    "description": True,
}


def text_metadata_field_enabled(
    metadata: Dict[str, Any],
    field_name: str,
) -> bool:
    """读取 metadata 中的字段开关，缺省时保持历史展示行为。"""
    fields = metadata.get("_text_metadata_fields")
    if not isinstance(fields, dict):
        return TEXT_METADATA_FIELD_DEFAULTS.get(field_name, True)
    return bool(
        fields.get(
            field_name,
            TEXT_METADATA_FIELD_DEFAULTS.get(field_name, True),
        )
    )
