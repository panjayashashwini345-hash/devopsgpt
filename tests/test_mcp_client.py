"""Tests for McpSplunkClient connect/discovery/search (H7).

The MCP client's primary path is exercised by injecting a fake ``ClientSession``
(with ``list_tools`` / ``call_tool``) rather than standing up a real server or
requiring the ``mcp`` SDK. Tool-capability discovery, search normalization,
health, and graceful degradation are all covered.
"""

from __future__ import annotations

from types import SimpleNamespace

from devopsgpt.config import Settings
from devopsgpt.splunk.mcp_client import McpSplunkClient


def _tool(name, description=""):
    return SimpleNamespace(name=name, description=description)


class _FakeSession:
    """Mimics the subset of mcp.ClientSession the client uses."""

    def __init__(self, tools, call_result=None, call_error=None):
        self._tools = tools
        self._call_result = call_result
        self._call_error = call_error
        self.initialized = False
        self.calls: list[tuple[str, dict]] = []

    async def initialize(self):
        self.initialized = True

    async def list_tools(self):
        return SimpleNamespace(tools=self._tools)

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        if self._call_error is not None:
            raise self._call_error
        return self._call_result


def _content(text):
    """An MCP CallToolResult-like object with one text content block."""
    return SimpleNamespace(content=[SimpleNamespace(text=text)])


def _client(**overrides) -> McpSplunkClient:
    return McpSplunkClient(Settings(_env_file=None, SPLUNK_MODE="mcp", **overrides))


# ---- tool discovery / capability mapping ----------------------------------
async def test_discover_tools_maps_capabilities_by_name():
    client = _client()
    client._session = _FakeSession(
        [_tool("list_indexes"), _tool("run_search", "Execute an SPL query"), _tool("get_releases", "deployment history")]
    )
    await client._discover_tools()
    assert client._search_tool == "run_search"
    assert client._deploy_tool == "get_releases"
    assert set(client._tools) == {"list_indexes", "run_search", "get_releases"}


async def test_discover_tools_matches_on_description_when_name_opaque():
    client = _client()
    client._session = _FakeSession([_tool("exec", "run an spl query against the index")])
    await client._discover_tools()
    assert client._search_tool == "exec"  # matched via 'spl'/'query' in description


async def test_discover_tools_no_search_capable_tool():
    client = _client()
    client._session = _FakeSession([_tool("list_indexes"), _tool("get_sourcetypes")])
    await client._discover_tools()
    assert client._search_tool is None


# ---- search ---------------------------------------------------------------
async def test_search_normalizes_results(monkeypatch):
    client = _client()

    async def fake_connect():
        client._session = _FakeSession(
            [_tool("run_search", "spl")],
            call_result=_content('{"results": [{"_raw": "boom", "service": "checkout", "status": 503}]}'),
        )
        client._search_tool = "run_search"
        return True

    monkeypatch.setattr(client, "connect", fake_connect)
    res = await client.search("index=main error", max_results=10)
    assert res.backend == "mcp"
    assert res.error is None
    assert res.count == 1
    assert res.events[0].fields["status"] == 503
    # All alias keys are passed (best-effort arg mapping).
    name, args = client._session.calls[0]
    assert name == "run_search"
    assert {"query", "search", "spl"} <= set(args)


async def test_search_errors_when_no_tool(monkeypatch):
    client = _client()

    async def fake_connect():
        return False

    monkeypatch.setattr(client, "connect", fake_connect)
    res = await client.search("index=main")
    assert res.error == "MCP search tool unavailable"
    assert res.count == 0


async def test_search_surfaces_call_tool_failure(monkeypatch):
    client = _client()

    async def fake_connect():
        client._session = _FakeSession([_tool("run_search")], call_error=RuntimeError("server boom"))
        client._search_tool = "run_search"
        return True

    monkeypatch.setattr(client, "connect", fake_connect)
    res = await client.search("index=main")
    assert res.error is not None and "server boom" in res.error
    assert res.count == 0  # never raises


# ---- health & connect degradation -----------------------------------------
async def test_health_check_false_without_search_tool(monkeypatch):
    client = _client()

    async def fake_connect():
        client._search_tool = None
        return True

    monkeypatch.setattr(client, "connect", fake_connect)
    assert await client.health_check() is False


async def test_connect_returns_false_when_transport_unconfigured():
    # stdio transport with no MCP_SERVER_COMMAND => _open_transport raises =>
    # connect() degrades to False rather than propagating.
    client = _client(MCP_TRANSPORT="stdio", MCP_SERVER_COMMAND="")
    assert await client.connect() is False
    assert client._connected is False


async def test_connect_is_idempotent_once_connected():
    client = _client()
    client._connected = True
    # Already connected => returns True without touching transport/SDK.
    assert await client.connect() is True


async def test_aclose_resets_state():
    client = _client()
    client._connected = True
    client._session = _FakeSession([])
    await client.aclose()
    assert client._connected is False
    assert client._session is None
