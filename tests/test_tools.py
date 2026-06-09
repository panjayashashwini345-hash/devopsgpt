"""Tool registry tests — specs, dispatch, write kill-switch, side effects."""

from __future__ import annotations

from devopsgpt.config import Settings
from devopsgpt.integrations import build_github_adapter, build_jira_adapter
from devopsgpt.splunk import build_splunk_client
from devopsgpt.tools import ToolRegistry


def _registry(**overrides) -> ToolRegistry:
    s = Settings(_env_file=None, SPLUNK_MODE="mock", JIRA_MODE="mock", GITHUB_MODE="mock", **overrides)
    return ToolRegistry(s, build_splunk_client(s), build_jira_adapter(s), build_github_adapter(s))


def test_specs_cover_all_tools():
    names = {spec.name for spec in _registry().specs()}
    assert names == {
        "search_splunk_logs",
        "search_traces",
        "correlate_deployments",
        "get_source_code",
        "create_jira_ticket",
        "create_github_pr",
    }


def test_specs_have_valid_json_schema():
    for spec in _registry().specs():
        assert spec.parameters["type"] == "object"
        assert "properties" in spec.parameters


async def test_dispatch_search_logs():
    out = await _registry().dispatch("search_splunk_logs", {"query": "index=main checkout error"})
    assert out["count"] > 0
    assert out["backend"] == "mock"


async def test_dispatch_get_source_sets_diff():
    reg = _registry()
    out = await reg.dispatch("get_source_code", {"path": "checkout-service/src/checkout/order.py"})
    assert out["found"] is True
    assert "N+1" in out["content"]
    assert reg.last_proposed_diff  # diff staged for the PR


async def test_dispatch_get_source_basename_fallback():
    out = await _registry().dispatch("get_source_code", {"path": "order.py"})
    assert out["found"] is True


async def test_create_ticket_records_side_effect():
    reg = _registry()
    out = await reg.dispatch("create_jira_ticket", {"summary": "s", "description": "d"})
    assert out["created"] is True
    assert reg.created_ticket is not None
    assert reg.created_ticket.key == out["key"]


async def test_create_pr_records_side_effect():
    reg = _registry()
    out = await reg.dispatch("create_github_pr", {"title": "t", "body": "b", "branch": "x"})
    assert out["created"] is True
    assert reg.created_pr is not None


async def test_write_killswitch_suppresses_actions():
    reg = _registry(DEVOPSGPT_ALLOW_WRITE_ACTIONS="false")
    out = await reg.dispatch("create_jira_ticket", {"summary": "s", "description": "d"})
    assert out["suppressed"] is True
    assert reg.created_ticket is None  # nothing actually created


async def test_unknown_tool_returns_error():
    out = await _registry().dispatch("does_not_exist", {})
    assert "error" in out


def test_serialize_handles_pydantic_and_dict():
    from devopsgpt.models import JiraTicket

    assert "OPS-1" in ToolRegistry.serialize(JiraTicket(key="OPS-1"))
    assert ToolRegistry.serialize({"a": 1}) == '{"a": 1}'
