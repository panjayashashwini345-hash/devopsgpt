"""Splunk client tests — auth builder, mock backend, auto-fallback."""

from __future__ import annotations

from devopsgpt.config import Settings
from devopsgpt.splunk import build_splunk_client
from devopsgpt.splunk.auth import build_splunk_auth_headers, splunk_mcp_env
from devopsgpt.splunk.factory import AutoSplunkClient
from devopsgpt.splunk.mock_client import MockSplunkClient


def test_auth_header_bearer():
    s = Settings(_env_file=None, SPLUNK_AUTH_SCHEME="bearer", SPLUNK_TOKEN="abc")
    assert build_splunk_auth_headers(s) == {"Authorization": "Bearer abc"}


def test_auth_header_splunk_scheme():
    s = Settings(_env_file=None, SPLUNK_AUTH_SCHEME="splunk", SPLUNK_TOKEN="sk")
    assert build_splunk_auth_headers(s) == {"Authorization": "Splunk sk"}


def test_auth_header_basic():
    s = Settings(_env_file=None, SPLUNK_AUTH_SCHEME="basic", SPLUNK_USERNAME="u", SPLUNK_PASSWORD="p")
    headers = build_splunk_auth_headers(s)
    assert headers["Authorization"].startswith("Basic ")


def test_auth_header_empty_without_creds():
    assert build_splunk_auth_headers(Settings(_env_file=None, SPLUNK_AUTH_SCHEME="bearer")) == {}


def test_mcp_env_excludes_empty():
    s = Settings(_env_file=None, SPLUNK_HOST="h", SPLUNK_TOKEN="t")
    env = splunk_mcp_env(s)
    assert env["SPLUNK_HOST"] == "h"
    assert env["SPLUNK_TOKEN"] == "t"
    assert "SPLUNK_USERNAME" not in env  # empty -> dropped


async def test_mock_client_health_and_search():
    client = MockSplunkClient()
    assert await client.health_check() is True
    res = await client.search("index=main checkout error")
    assert res.backend == "mock"
    assert res.count > 0
    assert any("checkout" in e.raw.lower() for e in res.events)


async def test_mock_client_traces_branch():
    client = MockSplunkClient()
    res = await client.search("index=traces span status=error")
    assert res.count > 0
    assert all("trace_id" in e.fields for e in res.events)


async def test_mock_client_deployments_filtered():
    client = MockSplunkClient()
    deps = await client.list_deployments("checkout-service")
    assert deps and all(d.service == "checkout-service" for d in deps)


def test_factory_returns_mock_for_mock_mode():
    client = build_splunk_client(Settings(_env_file=None, SPLUNK_MODE="mock"))
    assert isinstance(client, MockSplunkClient)


def test_factory_returns_auto_for_auto_mode():
    client = build_splunk_client(Settings(_env_file=None, SPLUNK_MODE="auto"))
    assert isinstance(client, AutoSplunkClient)


async def test_auto_client_falls_back_to_mock_when_unconfigured():
    # No MCP command, no Splunk creds -> auto resolves to the mock backend.
    client = AutoSplunkClient(Settings(_env_file=None, SPLUNK_MODE="auto"))
    res = await client.search("index=main error")
    assert client.backend == "mock"
    assert res.count > 0
    await client.aclose()
