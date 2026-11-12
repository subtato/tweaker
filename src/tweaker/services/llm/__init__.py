from __future__ import annotations

import logging
from typing import Any

from ...config import get_settings
from .anthropic_provider import AnthropicProvider
from .base import LLMProvider, ProviderError, StructuredResult, Usage
from .demo import DemoProvider
from .openai_provider import OpenAICompatibleProvider

logger = logging.getLogger(__name__)

__all__ = [
    "LLMProvider",
    "ProviderError",
    "StructuredResult",
    "Usage",
    "get_provider",
    "provider_status",
]


def get_provider(org: Any | None = None) -> LLMProvider:
    settings = get_settings()
    if org is not None and not getattr(org, "ai_enabled", True):
        return DemoProvider()
    provider = settings.resolved_ai_provider
    if provider == "anthropic":
        if not settings.anthropic_api_key:
            logger.warning("Anthropic selected but ANTHROPIC_API_KEY is unset; using local analyzer")
            return DemoProvider()
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            effort=settings.anthropic_effort,
        )
    if provider == "openai":
        return OpenAICompatibleProvider(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            model=settings.openai_model,
        )
    if provider == "none":
        return DemoProvider()
    return DemoProvider()


def provider_status() -> dict[str, str | bool]:
    settings = get_settings()
    provider = get_provider()
    return {
        "provider": provider.name,
        "model": provider.model,
        "available": provider.available,
        "configured": settings.resolved_ai_provider,
    }
