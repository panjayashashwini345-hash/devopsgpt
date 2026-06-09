"""LLM provider factory with graceful degradation.

Selects the provider per config. If the requested provider can't be constructed
(missing key / base URL / SDK), logs a warning and falls back to the mock
provider rather than crashing — so the app always boots.
"""

from __future__ import annotations

from ..config import LLMProvider as LLMProviderEnum
from ..config import Settings
from ..logging import get_logger
from .base import LLMProvider
from .mock import MockProvider

log = get_logger(__name__)


def build_llm_provider(settings: Settings) -> LLMProvider:
    choice = settings.llm_provider

    if choice is LLMProviderEnum.MOCK:
        return MockProvider()

    try:
        if choice is LLMProviderEnum.CLAUDE:
            from .claude import ClaudeProvider

            return ClaudeProvider(settings)
        if choice is LLMProviderEnum.SPLUNK_HOSTED:
            from .hosted import SplunkHostedProvider

            return SplunkHostedProvider(settings)
    except Exception as exc:  # noqa: BLE001 - never let provider init kill startup
        log.warning(
            "llm.provider_init_failed",
            requested=choice.value,
            error=str(exc),
            fallback="mock",
        )
        return MockProvider()

    log.warning("llm.unknown_provider", requested=str(choice), fallback="mock")
    return MockProvider()
