"""Translate parsed text metadata through AstrBot or custom LLM providers."""
from __future__ import annotations

import asyncio
import inspect
import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from ..logger import logger
from .llm_client import LLMClient


TRANSLATION_SYSTEM_PROMPT = """你是严格的翻译引擎，只执行翻译任务。

规则：
1. 先判断每个 item 是否真正需要翻译为目标语言。
2. 如果原文已经是目标语言，或非目标语言内容只是品牌名、项目名、用户名、ID、URL、话题标签、表情、代码、数字、单位、API/LLM/URL/Docker 等技术词或平台术语，则 needs_translation=false。
3. needs_translation=false 时不要返回 text 字段，也不要复述原文。
4. needs_translation=true 时，只把输入 text 翻译为目标语言，不解释、不总结、不扩写、不补充事实。
5. 不知道或不确定的专有名词、用户名、ID、URL、话题标签、表情、代码、数字、单位和平台术语保持原样。
6. 必须保持每个 item 的 id 不变，不能新增、删除或重排 item。
7. 只能输出严格 JSON：{"translations":[{"id":"...","needs_translation":false},{"id":"...","needs_translation":true,"text":"..."}]}。
8. 如果整个请求没有任何 item 需要翻译，可以只输出严格 JSON：{"needs_translation":false}。
"""

LANGUAGE_CHECK_NOISE_RE = re.compile(
    r"https?://\S+|www\.\S+|[\w.+-]+@[\w.-]+\.\w+|`[^`]*`|"
    r"@[A-Za-z0-9_.-]+|#[^\s#]+#?",
    re.I,
)
OBVIOUS_TRADITIONAL_CHARS = set(
    "體臺國廣門們風雲網頁與為來這還說對時會個開關啟發無後裡裏"
    "圖標題測試簡複雜點擊獲處訊號視頻頻權檔資"
)
SIMPLIFIED_CHINESE = "简体中文"
TRADITIONAL_CHINESE = "繁体中文"
CONTENT_SCOPE_BODY_ONLY = "仅正文"
CONTENT_SCOPE_BODY_AND_TITLE = "正文和标题"


class MetadataTranslator:
    """Apply optional LLM translation to title, description, and comments."""

    def __init__(self, config: Any, astrbot_context: Optional[Any] = None):
        self.config = config
        self.astrbot_context = astrbot_context
        self.llm_client = LLMClient(config)

    async def translate_metadata_list(
        self,
        metadata_list: List[Dict[str, Any]],
        *,
        event_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not getattr(self.config, "enabled", False):
            return
        target_language = str(
            getattr(self.config, "target_language", "") or ""
        ).strip()
        if not target_language:
            logger.warning("翻译已启用，但未配置目标语言，跳过翻译")
            return

        item_groups = self._collect_item_groups(metadata_list, target_language)
        if not item_groups:
            return

        missing = self._missing_llm_fields(event_context)
        if missing:
            logger.warning(f"翻译已启用，但大模型配置不完整: {', '.join(missing)}")
            return

        started_at = time.perf_counter()
        translated: Dict[str, str] = {}
        try:
            for batch in item_groups:
                batch_result = await self._translate_batch(
                    batch,
                    target_language=target_language,
                    event_context=event_context or {},
                )
                translated.update(batch_result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"元数据翻译失败，使用原文输出: {exc}")
            return

        if not translated:
            return
        self._apply_translations(metadata_list, translated, target_language)
        logger.debug(
            f"元数据翻译完成: requests={len(item_groups)} "
            f"items={sum(len(group) for group in item_groups)} "
            f"translated={len(translated)} "
            f"elapsed={time.perf_counter() - started_at:.2f}s"
        )

    def _collect_item_groups(
        self,
        metadata_list: List[Dict[str, Any]],
        target_language: str,
    ) -> List[List[Dict[str, str]]]:
        item_groups: List[List[Dict[str, str]]] = []
        max_chars = max(
            1,
            int(
                getattr(self.config, "max_text_chars_per_request", 4000)
                or 4000
            ),
        )
        for meta_idx, metadata in enumerate(metadata_list):
            if metadata.get("error"):
                continue
            if not metadata.get("_enable_text_metadata", True):
                continue
            items: List[Dict[str, str]] = []
            content_scope = str(
                getattr(
                    self.config,
                    "content_scope",
                    CONTENT_SCOPE_BODY_AND_TITLE,
                )
                or CONTENT_SCOPE_BODY_AND_TITLE
            ).strip()
            if content_scope == CONTENT_SCOPE_BODY_AND_TITLE:
                self._append_text_item(
                    items,
                    meta_idx,
                    "title",
                    metadata.get("title"),
                    target_language,
                )
            self._append_text_item(
                items,
                meta_idx,
                "desc",
                metadata.get("desc"),
                target_language,
            )
            if not items:
                continue
            total_chars = sum(len(item["text"]) for item in items)
            if total_chars > max_chars:
                logger.debug(
                    f"跳过过长链接文本翻译: metadata={meta_idx} "
                    f"chars={total_chars} limit={max_chars}"
                )
                continue
            item_groups.append(items)
        return item_groups

    @classmethod
    def _append_text_item(
        cls,
        items: List[Dict[str, str]],
        meta_idx: int,
        field: str,
        value: Any,
        target_language: str,
    ) -> None:
        text = str(value or "").strip()
        if not text:
            return
        if cls._is_already_target_language(text, target_language):
            logger.debug(
                f"跳过已是目标语言的文本翻译: metadata={meta_idx} "
                f"field={field} target={target_language}"
            )
            return
        items.append({
            "id": f"{meta_idx}:{field}",
            "text": text,
        })

    @classmethod
    def _is_already_target_language(cls, text: str, target_language: str) -> bool:
        cleaned = cls._clean_for_language_check(text)
        if not cleaned:
            return True
        if not cls._has_word_like_text(cleaned):
            return True
        if target_language == SIMPLIFIED_CHINESE:
            return cls._is_simplified_chinese_text(cleaned)
        if target_language == TRADITIONAL_CHINESE:
            return cls._is_traditional_chinese_text(cleaned)
        return False

    @classmethod
    def _is_simplified_chinese_text(cls, text: str) -> bool:
        if cls._has_non_chinese_letters(text):
            return False
        if any(ch in OBVIOUS_TRADITIONAL_CHARS for ch in text):
            return False
        return True

    @classmethod
    def _is_traditional_chinese_text(cls, text: str) -> bool:
        if cls._has_non_chinese_letters(text):
            return False
        if not any(ch in OBVIOUS_TRADITIONAL_CHARS for ch in text):
            return False
        return True

    @staticmethod
    def _clean_for_language_check(text: str) -> str:
        cleaned = LANGUAGE_CHECK_NOISE_RE.sub(" ", str(text or ""))
        return cleaned.strip()

    @staticmethod
    def _is_cjk_char(ch: str) -> bool:
        return (
            "\u3400" <= ch <= "\u4dbf"
            or "\u4e00" <= ch <= "\u9fff"
            or "\uf900" <= ch <= "\ufaff"
        )

    @staticmethod
    def _has_word_like_text(text: str) -> bool:
        return any(ch.isalpha() for ch in text)

    @classmethod
    def _has_non_chinese_letters(cls, text: str) -> bool:
        return any(
            ch.isalpha() and not cls._is_cjk_char(ch)
            for ch in text
        )

    async def _translate_batch(
        self,
        items: List[Dict[str, str]],
        *,
        target_language: str,
        event_context: Dict[str, Any],
    ) -> Dict[str, str]:
        payload = self._build_payload(items, target_language)
        if self._use_astrbot_provider():
            text = await self._post_astrbot_completion(payload, event_context)
        else:
            text = await self.llm_client.complete(
                payload,
                timeout_seconds=int(
                    getattr(self.config, "request_timeout_seconds", 60) or 60
                ),
            )
        return self._parse_translation_response(text, {item["id"] for item in items})

    def _build_payload(
        self,
        items: List[Dict[str, str]],
        target_language: str,
    ) -> Dict[str, Any]:
        user_payload = {
            "target_language": target_language,
            "items": items,
        }
        return {
            "model": str(getattr(self.config, "model", "") or "gpt-5.5").strip(),
            "messages": [
                {
                    "role": "system",
                    "content": TRANSLATION_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
            "temperature": float(getattr(self.config, "temperature", 0.0) or 0.0),
            "max_tokens": max(
                256,
                int(getattr(self.config, "max_completion_tokens", 4000) or 4000),
            ),
        }

    def _parse_translation_response(
        self,
        text: str,
        expected_ids: set[str],
    ) -> Dict[str, str]:
        expected = set(expected_ids)
        data = self._loads_json_object(text)
        if data.get("needs_translation") is False:
            return {}
        translations = data.get("translations")
        if not isinstance(translations, list):
            raise RuntimeError("LLM 翻译响应缺少 translations 数组")

        result: Dict[str, str] = {}
        skipped_ids = set()
        for item in translations:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id", "") or "").strip()
            if item_id not in expected:
                continue
            if item.get("needs_translation") is False:
                skipped_ids.add(item_id)
                continue
            value = str(item.get("text", "") or "").strip()
            if value:
                result[item_id] = value
        if not result and skipped_ids >= expected:
            return {}
        if not result:
            raise RuntimeError("LLM 翻译响应没有可用译文")
        return result

    @staticmethod
    def _loads_json_object(text: str) -> Dict[str, Any]:
        raw = str(text or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
            raw = re.sub(r"\s*```$", "", raw)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start < 0 or end <= start:
                raise
            data = json.loads(raw[start:end + 1])
        if not isinstance(data, dict):
            raise RuntimeError("LLM 翻译响应不是 JSON 对象")
        return data

    def _apply_translations(
        self,
        metadata_list: List[Dict[str, Any]],
        translations: Dict[str, str],
        target_language: Optional[str] = None,
    ) -> None:
        language = str(
            target_language
            or getattr(self.config, "target_language", "")
            or ""
        ).strip()
        for item_id, translated in translations.items():
            meta_idx, field = self._split_item_id(item_id)
            if meta_idx < 0 or meta_idx >= len(metadata_list):
                continue
            metadata = metadata_list[meta_idx]
            if field in {"title", "desc"}:
                translated_fields = metadata.setdefault("_translated_fields", {})
                if isinstance(translated_fields, dict):
                    translated_fields[field] = translated
                metadata["translation_target_language"] = language
                continue

    @staticmethod
    def _split_item_id(item_id: str) -> Tuple[int, str]:
        meta_idx_text, _, field = str(item_id or "").partition(":")
        try:
            meta_idx = int(meta_idx_text)
        except (TypeError, ValueError):
            return -1, ""
        return meta_idx, field

    def _use_astrbot_provider(self) -> bool:
        return getattr(self.config, "llm_provider_source", "astrbot") == "astrbot"

    def _missing_llm_fields(
        self,
        event_context: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        if self._use_astrbot_provider():
            missing: List[str] = []
            if self.astrbot_context is None:
                missing.append("AstrBot Context")
            configured_provider = str(
                getattr(self.config, "astrbot_provider_id", "") or ""
            ).strip()
            current_origin = str(
                (event_context or {}).get("_astrbot_unified_msg_origin", "") or ""
            ).strip()
            if not configured_provider and not current_origin:
                missing.append("AstrBot AI Provider")
            return missing
        return self.llm_client.missing_fields()

    async def _post_astrbot_completion(
        self,
        payload: Dict[str, Any],
        event_context: Dict[str, Any],
    ) -> str:
        provider_id = await self._astrbot_provider_id(event_context)
        system_prompt, prompt = self._payload_to_astrbot_chat(payload)
        response = await self._call_astrbot_llm_generate(
            provider_id=provider_id,
            prompt=prompt,
            system_prompt=system_prompt,
        )
        text = self._extract_astrbot_response_text(response)
        if not text:
            raise RuntimeError("AstrBot AI 返回空翻译")
        return text

    async def _astrbot_provider_id(self, event_context: Dict[str, Any]) -> str:
        configured_provider = str(
            getattr(self.config, "astrbot_provider_id", "") or ""
        ).strip()
        if configured_provider:
            return configured_provider
        if self.astrbot_context is None:
            raise RuntimeError("未接入 AstrBot Context，无法使用 AstrBot 内置提供商")

        umo = str(event_context.get("_astrbot_unified_msg_origin", "") or "").strip()
        provider_id = ""
        if umo and hasattr(self.astrbot_context, "get_current_chat_provider_id"):
            provider_id = str(
                await self._maybe_await(
                    self.astrbot_context.get_current_chat_provider_id(umo)
                )
                or ""
            ).strip()
        if provider_id:
            return provider_id

        provider = None
        if hasattr(self.astrbot_context, "get_using_provider"):
            provider = await self._maybe_await(
                self.astrbot_context.get_using_provider(umo or None)
            )
        if provider is not None and hasattr(provider, "meta"):
            meta = provider.meta()
            provider_id = str(getattr(meta, "id", "") or "").strip()
            if provider_id:
                return provider_id

        raise RuntimeError("未选择 AstrBot AI，且当前会话没有可用的 AstrBot AI")

    async def _call_astrbot_llm_generate(
        self,
        *,
        provider_id: str,
        prompt: str,
        system_prompt: str,
    ) -> Any:
        if self.astrbot_context is None:
            raise RuntimeError("未接入 AstrBot Context，无法使用 AstrBot 内置提供商")
        if hasattr(self.astrbot_context, "llm_generate"):
            return await self._maybe_await(
                self.astrbot_context.llm_generate(
                    chat_provider_id=provider_id,
                    prompt=prompt,
                    image_urls=None,
                    system_prompt=system_prompt or None,
                )
            )

        provider = None
        if hasattr(self.astrbot_context, "get_provider_by_id"):
            provider = await self._maybe_await(
                self.astrbot_context.get_provider_by_id(provider_id)
            )
        if provider is None or not hasattr(provider, "text_chat"):
            raise RuntimeError(f"未找到 AstrBot AI Provider: {provider_id}")
        return await self._maybe_await(
            provider.text_chat(
                prompt=prompt,
                image_urls=None,
                system_prompt=system_prompt or None,
            )
        )

    @staticmethod
    async def _maybe_await(value: Any) -> Any:
        if inspect.isawaitable(value):
            return await value
        return value

    @staticmethod
    def _payload_to_astrbot_chat(payload: Dict[str, Any]) -> Tuple[str, str]:
        system_parts: List[str] = []
        user_parts: List[str] = []
        for message in payload.get("messages") or []:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "") or "").strip()
            content = str(message.get("content", "") or "").strip()
            if not content:
                continue
            if role == "system":
                system_parts.append(content)
            elif role == "user":
                user_parts.append(content)
        return (
            "\n\n".join(system_parts).strip(),
            "\n\n".join(user_parts).strip(),
        )

    @staticmethod
    def _extract_astrbot_response_text(response: Any) -> str:
        if response is None:
            return ""
        role = str(getattr(response, "role", "") or "").strip()
        if role == "err":
            detail = (
                getattr(response, "completion_text", "")
                or getattr(response, "_completion_text", "")
                or str(response)
            )
            raise RuntimeError(f"AstrBot AI 返回错误: {detail}")
        text = str(
            getattr(response, "completion_text", "")
            or getattr(response, "_completion_text", "")
            or ""
        ).strip()
        if text:
            return text
        result_chain = getattr(response, "result_chain", None)
        if result_chain is not None:
            getter = getattr(result_chain, "get_plain_text", None)
            if callable(getter):
                text = str(getter() or "").strip()
                if text:
                    return text
        if isinstance(response, str):
            return response.strip()
        return str(response or "").strip()
