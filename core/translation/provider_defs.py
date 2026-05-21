"""LLM provider labels and defaults for metadata translation."""
from __future__ import annotations

from typing import Dict


LLM_PROVIDER_OPTIONS = {
    "自定义 OpenAI 兼容": "openai_compatible",
    "OpenAI": "openai",
    "DeepSeek": "deepseek",
    "Moonshot / Kimi": "moonshot",
    "阿里云百炼 / 通义千问": "qwen",
    "智谱 AI / GLM": "glm",
    "火山引擎方舟 / 豆包": "volcengine",
    "OpenRouter": "openrouter",
    "SiliconFlow": "siliconflow",
    "Ollama": "ollama",
}


LLM_PROVIDER_DEFAULTS: Dict[str, Dict[str, object]] = {
    "openai_compatible": {
        "base_url": "",
        "requires_api_key": True,
        "protocol": "openai",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "requires_api_key": True,
        "protocol": "openai",
        "token_limit_field": "max_completion_tokens",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "requires_api_key": True,
        "protocol": "openai",
    },
    "moonshot": {
        "base_url": "https://api.moonshot.cn/v1",
        "requires_api_key": True,
        "protocol": "openai",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "requires_api_key": True,
        "protocol": "openai",
    },
    "glm": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "requires_api_key": True,
        "protocol": "openai",
    },
    "volcengine": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "requires_api_key": True,
        "protocol": "openai",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "requires_api_key": True,
        "protocol": "openai",
    },
    "siliconflow": {
        "base_url": "https://api.siliconflow.cn/v1",
        "requires_api_key": True,
        "protocol": "openai",
    },
    "ollama": {
        "base_url": "http://localhost:11434",
        "requires_api_key": False,
        "protocol": "ollama",
    },
}


LLM_PROVIDER_LABELS = {
    value: key for key, value in LLM_PROVIDER_OPTIONS.items()
}
