"""Provider-neutral LLM protocol with normalized tool calling.

The agent speaks only in these types. Each provider adapts them to/from its
native wire format (Anthropic content blocks, OpenAI ``tool_calls``, etc.).

The interaction model is a **stateful conversation**: the agent creates a
:class:`Conversation` from a provider, calls :meth:`Conversation.send` with the
user question, and then repeatedly calls :meth:`Conversation.submit_tool_results`
until the model stops requesting tools. Each provider keeps its own native
message history inside the conversation, so the agent never has to translate
wire formats.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Message(BaseModel):
    role: Role
    content: str = ""


class ToolSpec(BaseModel):
    """A tool advertised to the model. ``parameters`` is a JSON Schema object."""

    name: str
    description: str
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )


class ToolCall(BaseModel):
    """A tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """The result of running a :class:`ToolCall`, fed back to the model."""

    tool_call_id: str
    name: str
    content: str  # JSON-encoded tool output
    is_error: bool = False


class AssistantTurn(BaseModel):
    """One model response: free text and/or a batch of tool calls."""

    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stop_reason: str = "end"  # "tool_use" | "end"

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


@runtime_checkable
class Conversation(Protocol):
    """A stateful, tool-using exchange with a single model."""

    async def send(self, user_message: str) -> AssistantTurn:
        """Send the (first or next) user message; return the model's turn."""
        ...

    async def submit_tool_results(self, results: list[ToolResult]) -> AssistantTurn:
        """Return tool outputs to the model; return its next turn."""
        ...


@runtime_checkable
class LLMProvider(Protocol):
    """Factory for conversations against one model backend."""

    #: Identifier surfaced in reports ("claude" | "splunk-hosted" | "mock").
    name: str

    def start_conversation(self, *, system: str, tools: list[ToolSpec]) -> Conversation:
        """Begin a new tool-enabled conversation."""
        ...

    async def aclose(self) -> None:
        ...
