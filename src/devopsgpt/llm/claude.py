"""Claude (Anthropic Messages API) provider with native tool use."""

from __future__ import annotations

import json
from typing import Any

from ..config import Settings
from ..logging import get_logger
from .base import AssistantTurn, ToolCall, ToolResult, ToolSpec

log = get_logger(__name__)


class ClaudeConversation:
    def __init__(self, client: Any, model: str, max_tokens: int, system: str, tools: list[ToolSpec]):
        self._client = client
        self._model = model
        self._max_tokens = max_tokens
        self._system = system
        self._tools = [
            {"name": t.name, "description": t.description, "input_schema": t.parameters}
            for t in tools
        ]
        self._messages: list[dict[str, Any]] = []

    async def send(self, user_message: str) -> AssistantTurn:
        self._messages.append({"role": "user", "content": user_message})
        return await self._roundtrip()

    async def submit_tool_results(self, results: list[ToolResult]) -> AssistantTurn:
        self._messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": r.tool_call_id,
                        "content": r.content,
                        "is_error": r.is_error,
                    }
                    for r in results
                ],
            }
        )
        return await self._roundtrip()

    async def _roundtrip(self) -> AssistantTurn:
        resp = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=self._system,
            tools=self._tools,
            messages=self._messages,
        )
        # Record the assistant turn verbatim so tool_use ids line up next round.
        self._messages.append({"role": "assistant", "content": resp.content})

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=dict(block.input or {}))
                )
        return AssistantTurn(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason="tool_use" if resp.stop_reason == "tool_use" else "end",
        )


class ClaudeProvider:
    name = "claude"

    def __init__(self, settings: Settings) -> None:
        from anthropic import AsyncAnthropic

        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is required for the claude provider")
        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._model = settings.claude_model
        self._max_tokens = settings.claude_max_tokens

    def start_conversation(self, *, system: str, tools: list[ToolSpec]) -> ClaudeConversation:
        return ClaudeConversation(self._client, self._model, self._max_tokens, system, tools)

    async def aclose(self) -> None:
        await self._client.close()


# Re-exported for tests that build tool-result JSON.
def _dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)
