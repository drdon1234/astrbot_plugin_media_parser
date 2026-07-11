"""消息文本的统一长度约束与分片。"""
from typing import List


MAX_MESSAGE_TEXT_LENGTH = 4000


def split_message_text(
    text: str,
    max_length: int = MAX_MESSAGE_TEXT_LENGTH,
) -> List[str]:
    """按消息长度上限分片，优先在换行处切分并保留全部原文。"""
    if max_length <= 0:
        raise ValueError("max_length 必须大于 0")
    if not text:
        return []

    chunks: List[str] = []
    start = 0
    text_length = len(text)

    while text_length - start > max_length:
        hard_end = start + max_length
        newline_index = text.rfind("\n", start, hard_end)
        end = newline_index + 1 if newline_index > start else hard_end
        chunks.append(text[start:end])
        start = end

    if start < text_length:
        chunks.append(text[start:])
    return chunks
