"""Unit tests for the network-backed Splunk clients without real servers.

* MCP: tool-capability discovery + result normalization (the agentic core).
* REST: request building + result parsing for oneshot / async, via respx.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import respx

from devopsgpt.config import Settings
from devopsgpt.splunk.mcp_client import _coerce, _extract_rows
from devopsgpt.splunk.normalization import (
    row_to_event as _row_to_event,
)
from devopsgpt.splunk.normalization import (
    sanitize_service,
    service_filter_clause,
)
from devopsgpt.splunk.rest_client import RestSplunkClient, _normalize_spl


# ---- SPL injection defense (H1/H2) ----------------------------------------
def test_sanitize_service_strips_injection_chars():
    # Double-quote breakout attempt is neutralized.
    assert '"' not in sanitize_service('checkout" OR 1=1')
    assert "|" not in sanitize_service("checkout | delete")
    # Backslashes and brackets too.
    assert sanitize_service('a\\"[b]') == "ab"


def test_sanitize_service_keeps_legitimate_names():
    assert sanitize_service("checkout-service") == "checkout-service"
    assert sanitize_service("team_a/payments-api") == "team_a/payments-api"


def test_sanitize_service_empty_becomes_wildcard():
    # If sanitization empties the value, fall back to a valid wildcard.
    assert sanitize_service('"|') == "*"


def test_service_filter_clause_is_quoted_and_safe():
    clause = service_filter_clause('evil" | delete')
    # Exactly one opening and one closing quote — no breakout.
    assert clause.count('"') == 2
    assert "delete" in clause  # sanitized text preserved, syntax neutralized
    assert clause.startswith('service="') and clause.endswith('"')


def test_events_to_deployments_maps_fields_and_fallbacks():
    from devopsgpt.models import SplunkEvent
    from devopsgpt.splunk.normalization import events_to_deployments

    events = [
        SplunkEvent(
            timestamp="2026-06-05T13:55:00Z",
            fields={"service": "checkout", "version": "v2.4.0", "git_sha": "9f3c1a2", "author": "dev"},
        ),
        # Missing service/version/commit -> falls back to the passed service + defaults.
        SplunkEvent(timestamp="2026-06-04T09:00:00Z", fields={"build": "b-17"}),
    ]
    deps = events_to_deployments(events, service="checkout-service")
    assert deps[0].service == "checkout"
    assert deps[0].version == "v2.4.0"
    assert deps[0].commit == "9f3c1a2"  # git_sha fallback
    assert deps[0].environment == "production"  # default
    assert deps[1].service == "checkout-service"  # fell back to arg
    assert deps[1].version == "b-17"  # build fallback
    assert deps[1].commit is None



# ---- MCP normalization ----------------------------------------------------
def test_normalize_spl_prepends_search():
    assert _normalize_spl("index=main error").startswith("search ")
    # generating commands and explicit `search` are left alone
    assert _normalize_spl("| tstats count").startswith("|")
    assert _normalize_spl("search index=main").startswith("search index")


def test_coerce_handles_results_envelope():
    rows = _coerce({"results": [{"a": 1}, {"b": 2}], "preview": False})
    assert rows == [{"a": 1}, {"b": 2}]


def test_coerce_handles_bare_list_and_object():
    assert _coerce([{"x": 1}, "skip", {"y": 2}]) == [{"x": 1}, {"y": 2}]
    assert _coerce({"k": "v"}) == [{"k": "v"}]
    assert _coerce("plain text") == [{"_raw": "plain text"}]


def test_extract_rows_from_mcp_content_blocks():
    # Simulate an MCP CallToolResult: .content = [TextContent(text=...)]
    payload = json.dumps({"results": [{"_raw": "evt", "service": "checkout"}]})
    raw = SimpleNamespace(content=[SimpleNamespace(text=payload)])
    rows = _extract_rows(raw)
    assert rows == [{"_raw": "evt", "service": "checkout"}]


def test_extract_rows_from_ndjson():
    ndjson = '{"_raw":"a"}\n{"_raw":"b"}'
    raw = SimpleNamespace(content=[SimpleNamespace(text=ndjson)])
    rows = _extract_rows(raw)
    assert [r["_raw"] for r in rows] == ["a", "b"]


def test_row_to_event_splits_internal_fields():
    ev = _row_to_event({"_raw": "x", "_time": "t", "service": "s", "status": 503})
    assert ev.raw == "x" and ev.timestamp == "t"
    assert ev.fields == {"service": "s", "status": 503}
    assert "_time" not in ev.fields


def test_mcp_capability_matching_via_discovery_logic():
    # The discovery maps tool names to capabilities by substring hints.
    from devopsgpt.splunk.mcp_client import _SEARCH_HINTS

    assert any(h in "run_search" for h in _SEARCH_HINTS)
    assert any(h in "execute_spl_query" for h in _SEARCH_HINTS)


# ---- REST request building ------------------------------------------------
def _rest(**kw) -> RestSplunkClient:
    s = Settings(
        _env_file=None,
        SPLUNK_MODE="rest",
        SPLUNK_HOST="splunk.local",
        SPLUNK_MGMT_PORT=8089,
        SPLUNK_AUTH_SCHEME="bearer",
        SPLUNK_TOKEN="tok",
        SPLUNK_VERIFY_SSL="false",
        **kw,
    )
    return RestSplunkClient(s)


async def test_rest_oneshot_parses_results():
    client = _rest(SPLUNK_SEARCH_MODE="oneshot")
    with respx.mock:
        respx.post("https://splunk.local:8089/services/search/jobs").mock(
            return_value=httpx.Response(
                200,
                json={"results": [{"_raw": "boom", "status": "503", "service": "checkout"}]},
            )
        )
        res = await client.search("index=main error")
        assert res.backend == "rest"
        assert res.error is None
        assert res.count == 1
        assert res.events[0].fields["status"] == "503"
    await client.aclose()


async def test_rest_v2_path_used_when_configured():
    client = _rest(SPLUNK_REST_API_VERSION="v2", SPLUNK_SEARCH_MODE="oneshot")
    with respx.mock:
        route = respx.post("https://splunk.local:8089/services/search/v2/jobs").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        await client.search("index=main")
        assert route.called
    await client.aclose()


async def test_rest_async_create_poll_results_flow():
    client = _rest(SPLUNK_SEARCH_MODE="async", POLL_INTERVAL_S="0", MAX_POLL_S="5")
    base = "https://splunk.local:8089/services/search/jobs"
    with respx.mock:
        respx.post(base).mock(return_value=httpx.Response(201, json={"sid": "SID1"}))
        respx.get(f"{base}/SID1").mock(
            return_value=httpx.Response(
                200, json={"entry": [{"content": {"isDone": True, "dispatchState": "DONE"}}]}
            )
        )
        respx.get(f"{base}/SID1/results").mock(
            return_value=httpx.Response(200, json={"results": [{"_raw": "ok"}]})
        )
        res = await client.search("index=main")
        assert res.count == 1 and res.events[0].raw == "ok"
    await client.aclose()


async def test_rest_search_surfaces_http_error_as_field():
    client = _rest(SPLUNK_SEARCH_MODE="oneshot")
    with respx.mock:
        respx.post("https://splunk.local:8089/services/search/jobs").mock(
            return_value=httpx.Response(503, text="unavailable")
        )
        res = await client.search("index=main")
        assert res.error is not None
        assert res.count == 0  # never raises
    await client.aclose()


async def test_rest_health_false_without_credentials():
    s = Settings(_env_file=None, SPLUNK_MODE="rest", SPLUNK_AUTH_SCHEME="bearer")
    client = RestSplunkClient(s)
    assert await client.health_check() is False
    await client.aclose()
