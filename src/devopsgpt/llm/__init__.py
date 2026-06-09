"""Pluggable LLM provider abstraction.

A single :class:`LLMProvider` interface with a normalized, stateful tool-calling
protocol, so the agent loop is identical no matter which model is behind it:

* :class:`~devopsgpt.llm.claude.ClaudeProvider` — Anthropic Messages API
  (native tool use).
* :class:`~devopsgpt.llm.hosted.SplunkHostedProvider` — Splunk Hosted Models via
  the OpenAI-compatible chat-completions API (gpt-oss-*).
* :class:`~devopsgpt.llm.mock.MockProvider` — deterministic, offline; drives the
  canonical demo without any API key.

:func:`build_llm_provider` selects per :class:`~devopsgpt.config.LLMProvider`.
"""

from __future__ import annotations

from .base import (
    AssistantTurn,
    Conversation,
    LLMProvider,
    Message,
    Role,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from .factory import build_llm_provider

__all__ = [
    "AssistantTurn",
    "Conversation",
    "LLMProvider",
    "Message",
    "Role",
    "ToolCall",
    "ToolResult",
    "ToolSpec",
    "build_llm_provider",
]
