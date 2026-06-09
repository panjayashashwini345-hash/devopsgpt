"""Splunk Hosted Models provider via the OpenAI-compatible chat API.

Targets gpt-oss-120b / gpt-oss-20b / Foundation-Sec served behind an
OpenAI-compatible ``/v1/chat/completions`` endpoint. The base URL and model id
are fully config-driven (the endpoint path is not assumed beyond OpenAI compat).

Uses the ``openai`` async SDK with overridden ``base_url`` + ``api_key``.
"""

from __future__ import annotations

import json
from typing import Any

from ..config import Settings
from ..logging import get_logger
from .base import AssistantTurn, ToolCall, ToolResult, ToolSpec

log = get_logger(__name__)


def _to_openai_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]


class HostedConversation:
    def __init__(self, client: Any, model: str, system: str, tools: list[ToolSpec]):
        self._client = client
        self._model = model
        self._tools = _to_openai_tools(tools)
        self._messages: list[dict[str, Any]] = [{"role": "system", "content": system}]

    async def send(self, user_message: str) -> AssistantTurn:
        self._messages.append({"role": "user", "content": user_message})
        return await self._roundtrip()

    async def submit_tool_results(self, results: list[ToolResult]) -> AssistantTurn:
        for r in results:
            self._messages.append(
                {
                    "role": "tool",
                    "tool_call_id": r.tool_call_id,
                    "name": r.name,
                    "content": r.content,
                }
            )
        return await self._roundtrip()

    async def _roundtrip(self) -> AssistantTurn:
        kwargs: dict[str, Any] = {"model": self._model, "messages": self._messages}
        if self._tools:
            kwargs["tools"] = self._tools
            kwargs["tool_choice"] = "auto"
        resp = await self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        msg = choice.message

        # Echo the assistant message back into history (with tool_calls intact).
        assistant_entry: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
        tool_calls: list[ToolCall] = []
        if getattr(msg, "tool_calls", None):
            assistant_entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        self._messages.append(assistant_entry)

        return AssistantTurn(
            text=msg.content or "",
            tool_calls=tool_calls,
            stop_reason="tool_use" if tool_calls else "end",
        )


class SplunkHostedProvider:
    name = "splunk-hosted"

    def __init__(self, settings: Settings) -> None:
        from openai import AsyncOpenAI

        if not settings.hosted_models_base_url:
            raise RuntimeError("HOSTED_MODELS_BASE_URL is required for the splunk-hosted provider")
        self._client = AsyncOpenAI(
            base_url=settings.hosted_models_base_url.rstrip("/"),
            api_key=settings.hosted_models_api_key or "not-needed",
        )
        self._model = settings.hosted_models_default_model

    def start_conversation(self, *, system: str, tools: list[ToolSpec]) -> HostedConversation:
        return HostedConversation(self._client, self._model, system, tools)

    async def aclose(self) -> None:
        await self._client.close()
