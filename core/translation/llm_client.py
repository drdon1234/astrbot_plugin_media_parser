"""Provider-aware LLM adapter for metadata translation."""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import aiohttp

from .provider_defs import LLM_PROVIDER_DEFAULTS


@dataclass(frozen=True)
class LLMProviderDefinition:
    key: str
    protocol: str
    default_base_url: str
    requires_api_key: bool
    token_limit_field: str = "max_tokens"


@dataclass
class ProviderHttpRequest:
    url: str
    headers: Dict[str, str]
    json: Dict[str, Any]


PROVIDER_DEFINITIONS: Dict[str, LLMProviderDefinition] = {
    key: LLMProviderDefinition(
        key=key,
        protocol=str(value.get("protocol", "openai") or "openai"),
        default_base_url=str(value.get("base_url", "") or ""),
        requires_api_key=bool(value.get("requires_api_key", True)),
        token_limit_field=str(
            value.get("token_limit_field", "max_tokens") or "max_tokens"
        ),
    )
    for key, value in LLM_PROVIDER_DEFAULTS.items()
}


class LLMClient:
    """Build provider-specific requests and extract text responses."""

    def __init__(self, config: Any):
        self.config = config

    def is_configured(self) -> bool:
        return not self.missing_fields()

    def missing_fields(self) -> List[str]:
        provider = self._provider_definition()
        missing: List[str] = []
        if not self._model():
            missing.append("模型")
        if provider.requires_api_key and not self._api_key():
            missing.append("API Key")
        if provider.protocol in {"openai", "ollama"} and not self._base_url():
            missing.append("Base URL")
        return missing

    async def complete(
        self,
        payload: Dict[str, Any],
        *,
        timeout_seconds: int,
    ) -> str:
        timeout = aiohttp.ClientTimeout(total=max(10, int(timeout_seconds)))
        drop_temperature = False
        token_limit_field = self._provider_definition().token_limit_field
        working_payload = copy.deepcopy(payload)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            for attempt in range(3):
                request = self.build_http_request(
                    working_payload,
                    drop_temperature=drop_temperature,
                    token_limit_field=token_limit_field,
                )
                try:
                    async with session.post(
                        request.url,
                        json=request.json,
                        headers=request.headers,
                    ) as response:
                        body = await response.text()
                        if response.status >= 400:
                            raise RuntimeError(f"HTTP {response.status}: {body}")
                        return self.extract_content(json.loads(body))
                except RuntimeError as exc:
                    message = str(exc)
                    if self._should_retry_token_limit_field(message):
                        token_limit_field = self._alternate_token_limit_field(
                            token_limit_field
                        )
                        continue
                    if attempt <= 1 and self._should_drop_temperature(message):
                        drop_temperature = True
                        continue
                    raise

        raise RuntimeError("LLM 请求失败")

    def build_http_request(
        self,
        payload: Dict[str, Any],
        *,
        drop_temperature: bool = False,
        token_limit_field: Optional[str] = None,
    ) -> ProviderHttpRequest:
        provider = self._provider_definition()
        model = self._model()
        if not model:
            raise RuntimeError("未配置翻译模型")
        if provider.requires_api_key and not self._api_key():
            raise RuntimeError("未配置翻译 API Key")

        builder = {
            "openai": self._build_openai_request,
            "ollama": self._build_ollama_request,
        }.get(provider.protocol)
        if not builder:
            raise RuntimeError(f"不支持的 LLM 协议: {provider.protocol}")
        return builder(
            payload,
            drop_temperature=drop_temperature,
            token_limit_field=token_limit_field,
        )

    def extract_content(self, response: Dict[str, Any]) -> str:
        provider = self._provider_definition()
        parser = {
            "openai": self._extract_openai_content,
            "ollama": self._extract_ollama_content,
        }.get(provider.protocol)
        if not parser:
            raise RuntimeError(f"不支持的 LLM 协议: {provider.protocol}")
        return parser(response)

    def _provider_definition(self) -> LLMProviderDefinition:
        provider_key = str(getattr(self.config, "llm_provider", "") or "").strip()
        return PROVIDER_DEFINITIONS.get(
            provider_key,
            PROVIDER_DEFINITIONS["openai_compatible"],
        )

    def _model(self) -> str:
        return str(getattr(self.config, "model", "") or "").strip()

    def _api_key(self) -> str:
        return str(getattr(self.config, "api_key", "") or "").strip()

    def _base_url(self) -> str:
        return str(getattr(self.config, "base_url", "") or "").strip().rstrip("/")

    def _build_openai_request(
        self,
        payload: Dict[str, Any],
        *,
        drop_temperature: bool,
        token_limit_field: Optional[str],
    ) -> ProviderHttpRequest:
        body = copy.deepcopy(payload)
        self._apply_token_limit_field(
            body,
            self._token_limit_field(token_limit_field),
        )
        if drop_temperature:
            body.pop("temperature", None)
        url = self._join_chat_completions_url(
            self._base_url() or self._provider_definition().default_base_url
        )
        headers = {
            "Authorization": f"Bearer {self._api_key()}",
            "Content-Type": "application/json",
        }
        return ProviderHttpRequest(url=url, headers=headers, json=body)

    def _build_ollama_request(
        self,
        payload: Dict[str, Any],
        *,
        drop_temperature: bool,
        token_limit_field: Optional[str],
    ) -> ProviderHttpRequest:
        messages = copy.deepcopy(payload.get("messages") or [])
        body: Dict[str, Any] = {
            "model": self._model(),
            "messages": messages,
            "stream": False,
        }
        options: Dict[str, Any] = {}
        if not drop_temperature and payload.get("temperature") is not None:
            options["temperature"] = payload["temperature"]
        max_tokens = self._extract_max_tokens(payload)
        if max_tokens:
            options["num_predict"] = max_tokens
        if options:
            body["options"] = options
        url = self._join_path(
            self._base_url() or self._provider_definition().default_base_url,
            "/api/chat",
        )
        headers = {"Content-Type": "application/json"}
        if self._api_key():
            headers["Authorization"] = f"Bearer {self._api_key()}"
        return ProviderHttpRequest(url=url, headers=headers, json=body)

    @staticmethod
    def _extract_openai_content(response: Dict[str, Any]) -> str:
        choices = response.get("choices") or []
        if not choices:
            raise RuntimeError("LLM 响应中没有 choices")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            text = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            ).strip()
        else:
            text = ""
        if not text:
            raise RuntimeError("LLM 返回空内容")
        return text

    @staticmethod
    def _extract_ollama_content(response: Dict[str, Any]) -> str:
        message = response.get("message") or {}
        text = str(message.get("content", "") or "").strip()
        if not text:
            raise RuntimeError("LLM 返回空内容")
        return text

    @staticmethod
    def _should_retry_token_limit_field(message: str) -> bool:
        lowered = str(message or "").lower()
        return (
            "max_completion_tokens" in lowered
            or "max_tokens" in lowered
            or "unrecognized" in lowered
        )

    @staticmethod
    def _should_drop_temperature(message: str) -> bool:
        return "temperature" in str(message or "").lower()

    def _token_limit_field(self, token_limit_field: Optional[str]) -> str:
        if token_limit_field in {"max_tokens", "max_completion_tokens"}:
            return str(token_limit_field)
        provider = self._provider_definition()
        if provider.token_limit_field in {"max_tokens", "max_completion_tokens"}:
            return provider.token_limit_field
        return "max_tokens"

    @staticmethod
    def _alternate_token_limit_field(token_limit_field: str) -> str:
        if token_limit_field == "max_tokens":
            return "max_completion_tokens"
        return "max_tokens"

    @staticmethod
    def _apply_token_limit_field(body: Dict[str, Any], token_limit_field: str) -> None:
        if token_limit_field == "max_completion_tokens":
            if "max_completion_tokens" not in body and "max_tokens" in body:
                body["max_completion_tokens"] = body.pop("max_tokens")
            else:
                body.pop("max_tokens", None)
            return
        if "max_completion_tokens" in body:
            body["max_tokens"] = body.pop("max_completion_tokens")

    def _extract_max_tokens(self, payload: Dict[str, Any]) -> int:
        value = payload.get("max_completion_tokens")
        if value is None:
            value = payload.get("max_tokens")
        try:
            return max(1, int(value or 0))
        except (TypeError, ValueError):
            return 1

    @staticmethod
    def _join_chat_completions_url(base_url: str) -> str:
        base = str(base_url or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("未配置翻译 Base URL")
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    @staticmethod
    def _join_path(base_url: str, suffix: str) -> str:
        base = str(base_url or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("未配置翻译 Base URL")
        normalized_suffix = "/" + suffix.lstrip("/")
        if base.endswith(normalized_suffix):
            return base
        return base + normalized_suffix
