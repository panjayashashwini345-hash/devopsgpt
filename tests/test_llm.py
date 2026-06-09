"""LLM provider tests — mock conversation plan + factory degradation."""

from __future__ import annotations

from devopsgpt.config import Settings
from devopsgpt.llm import ToolSpec, build_llm_provider
from devopsgpt.llm.mock import MockProvider


def _tools(*names: str) -> list[ToolSpec]:
    return [ToolSpec(name=n, description=n, parameters={"type": "object", "properties": {}}) for n in names]


async def test_mock_provider_walks_investigation_plan():
    provider = MockProvider()
    conv = provider.start_conversation(
        system="sys",
        tools=_tools(
            "search_splunk_logs",
            "search_traces",
            "correlate_deployments",
            "get_source_code",
            "create_jira_ticket",
            "create_github_pr",
        ),
    )
    seen: list[str] = []
    turn = await conv.send("Checkout API is slow")
    # Drive the loop, feeding empty tool results back each time.
    while turn.wants_tools:
        seen.extend(tc.name for tc in turn.tool_calls)
        from devopsgpt.llm import ToolResult

        turn = await conv.submit_tool_results(
            [ToolResult(tool_call_id=tc.id, name=tc.name, content="{}") for tc in turn.tool_calls]
        )

    assert seen == [
        "search_splunk_logs",
        "search_traces",
        "correlate_deployments",
        "get_source_code",
        "create_jira_ticket",
        "create_github_pr",
    ]
    assert turn.stop_reason == "end"
    assert "Root cause" in turn.text


async def test_mock_provider_skips_unavailable_tools():
    provider = MockProvider()
    # Only logs + final; no ticket/PR tools registered.
    conv = provider.start_conversation(system="s", tools=_tools("search_splunk_logs"))
    turn = await conv.send("Checkout API is slow")
    names = []
    from devopsgpt.llm import ToolResult

    while turn.wants_tools:
        names.extend(tc.name for tc in turn.tool_calls)
        turn = await conv.submit_tool_results(
            [ToolResult(tool_call_id=tc.id, name=tc.name, content="{}") for tc in turn.tool_calls]
        )
    assert names == ["search_splunk_logs"]  # others gated out
    assert turn.stop_reason == "end"


def test_factory_returns_mock_for_mock_provider():
    provider = build_llm_provider(Settings(_env_file=None, DEVOPSGPT_LLM_PROVIDER="mock"))
    assert provider.name == "mock"


def test_factory_degrades_claude_without_key():
    # Requesting claude without ANTHROPIC_API_KEY must not crash; falls back.
    provider = build_llm_provider(Settings(_env_file=None, DEVOPSGPT_LLM_PROVIDER="claude"))
    assert provider.name == "mock"


def test_factory_degrades_hosted_without_base_url():
    provider = build_llm_provider(
        Settings(_env_file=None, DEVOPSGPT_LLM_PROVIDER="splunk-hosted")
    )
    assert provider.name == "mock"
