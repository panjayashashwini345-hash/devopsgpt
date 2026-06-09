"""Direct tests for the Claude and Splunk-hosted LLM providers (H5/H6).

Both conversation classes take their SDK client by injection, so we drive them
with hand-built fakes that mimic the Anthropic / OpenAI response shapes — no
network, no real SDK behavior.
"""

from __future__ import annotations

from types import SimpleNamespace

from devopsgpt.llm import ToolResult, ToolSpec
from devopsgpt.llm.claude import ClaudeConversation
from devopsgpt.llm.hosted import HostedConversation, _to_openai_tools


def _tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="search_splunk_logs",
            description="search logs",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
        )
    ]


# ===========================================================================
# Claude (Anthropic Messages API)
# ===========================================================================
class _FakeAnthropic:
    """Returns queued responses in order from messages.create()."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        # Snapshot messages (the conversation mutates the same list in place).
        snap = dict(kwargs)
        snap["messages"] = list(kwargs.get("messages", []))
        self.calls.append(snap)
        return self._responses.pop(0)


def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(id, name, input):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def _claude_resp(content, stop_reason):
    return SimpleNamespace(content=content, stop_reason=stop_reason)


async def test_claude_multi_turn_tool_flow():
    # Turn 1: model asks for a tool. Turn 2: model concludes.
    client = _FakeAnthropic(
        [
            _claude_resp(
                [_text_block("Looking."), _tool_use_block("tu_1", "search_splunk_logs", {"query": "x"})],
                "tool_use",
            ),
            _claude_resp([_text_block("## Root cause\nFound it.")], "end_turn"),
        ]
    )
    conv = ClaudeConversation(client, "claude-x", 1024, "sys", _tools())

    turn1 = await conv.send("Checkout slow")
    assert turn1.stop_reason == "tool_use"
    assert turn1.wants_tools
    assert turn1.tool_calls[0].name == "search_splunk_logs"
    assert turn1.tool_calls[0].arguments == {"query": "x"}
    assert turn1.text == "Looking."

    turn2 = await conv.submit_tool_results(
        [ToolResult(tool_call_id="tu_1", name="search_splunk_logs", content="{}")]
    )
    assert turn2.stop_reason == "end"
    assert not turn2.wants_tools
    assert "Root cause" in turn2.text

    # System prompt + tools forwarded; history accumulated across both calls.
    assert client.calls[0]["system"] == "sys"
    assert client.calls[0]["tools"][0]["name"] == "search_splunk_logs"
    # user, assistant(turn1), user(tool_result) => 3 messages on the 2nd call.
    assert len(client.calls[1]["messages"]) == 3
    assert client.calls[1]["messages"][-1]["content"][0]["type"] == "tool_result"


async def test_claude_handles_null_tool_input():
    # block.input == None must not crash (dict(None or {}) -> {}).
    client = _FakeAnthropic(
        [_claude_resp([_tool_use_block("tu_x", "search_splunk_logs", None)], "tool_use")]
    )
    conv = ClaudeConversation(client, "claude-x", 256, "sys", _tools())
    turn = await conv.send("go")
    assert turn.tool_calls[0].arguments == {}


async def test_claude_stop_reason_mapping():
    client = _FakeAnthropic([_claude_resp([_text_block("done")], "max_tokens")])
    conv = ClaudeConversation(client, "claude-x", 256, "sys", _tools())
    turn = await conv.send("go")
    # Anything other than "tool_use" normalizes to "end".
    assert turn.stop_reason == "end"


# ===========================================================================
# Splunk Hosted Models (OpenAI-compatible)
# ===========================================================================
class _FakeOpenAI:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    async def _create(self, **kwargs):
        snap = dict(kwargs)
        snap["messages"] = list(kwargs.get("messages", []))
        self.calls.append(snap)
        return self._responses.pop(0)


def _oai_tool_call(id, name, arguments):
    return SimpleNamespace(id=id, function=SimpleNamespace(name=name, arguments=arguments))


def _oai_resp(content, tool_calls=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def test_to_openai_tools_conversion():
    out = _to_openai_tools(_tools())
    assert out[0]["type"] == "function"
    assert out[0]["function"]["name"] == "search_splunk_logs"
    assert out[0]["function"]["parameters"]["type"] == "object"


async def test_hosted_multi_turn_with_system_message():
    client = _FakeOpenAI(
        [
            _oai_resp("thinking", [_oai_tool_call("c1", "search_splunk_logs", '{"query": "x"}')]),
            _oai_resp("## Root cause\ndone", None),
        ]
    )
    conv = HostedConversation(client, "gpt-oss-20b", "sys", _tools())

    turn1 = await conv.send("Checkout slow")
    assert turn1.stop_reason == "tool_use"
    assert turn1.tool_calls[0].arguments == {"query": "x"}

    turn2 = await conv.submit_tool_results(
        [ToolResult(tool_call_id="c1", name="search_splunk_logs", content="{}")]
    )
    assert turn2.stop_reason == "end"
    assert "Root cause" in turn2.text

    # First message is the system prompt; tool_choice=auto sent when tools exist.
    assert client.calls[0]["messages"][0] == {"role": "system", "content": "sys"}
    assert client.calls[0]["tool_choice"] == "auto"
    # A tool-role message was appended before the 2nd round.
    assert any(m.get("role") == "tool" for m in client.calls[1]["messages"])


async def test_hosted_recovers_from_malformed_tool_arguments():
    # Invalid JSON in arguments must fall back to {} rather than raise.
    client = _FakeOpenAI(
        [_oai_resp("", [_oai_tool_call("c1", "search_splunk_logs", "{not json")])]
    )
    conv = HostedConversation(client, "gpt-oss-20b", "sys", _tools())
    turn = await conv.send("go")
    assert turn.tool_calls[0].arguments == {}


async def test_hosted_no_tools_concludes():
    client = _FakeOpenAI([_oai_resp("just an answer", None)])
    conv = HostedConversation(client, "gpt-oss-20b", "sys", _tools())
    turn = await conv.send("go")
    assert turn.stop_reason == "end"
    assert turn.text == "just an answer"
    assert not turn.wants_tools
