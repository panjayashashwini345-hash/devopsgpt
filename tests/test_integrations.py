"""Integration adapter tests — mock + live(-with-mocked-HTTP) Jira & GitHub.

The live adapters are exercised against a mocked httpx transport (respx) to
prove the request-building logic without a real Jira/GitHub.
"""

from __future__ import annotations

import httpx
import respx

from devopsgpt.config import Settings
from devopsgpt.integrations import build_github_adapter, build_jira_adapter
from devopsgpt.integrations.github import LiveGitHubAdapter, MockGitHubAdapter
from devopsgpt.integrations.jira import LiveJiraAdapter, MockJiraAdapter


# ---- Jira -----------------------------------------------------------------
async def test_jira_mock_creates_ticket():
    adapter = MockJiraAdapter(Settings(_env_file=None, JIRA_PROJECT_KEY="OPS"))
    ticket = await adapter.create_ticket("Sum", "Desc", ["a b"])
    assert ticket.created and ticket.mocked
    assert ticket.key.startswith("OPS-")
    assert ticket.url


def test_jira_factory_degrades_without_creds():
    adapter = build_jira_adapter(Settings(_env_file=None, JIRA_MODE="live"))
    assert isinstance(adapter, MockJiraAdapter)


async def test_jira_live_builds_correct_request():
    s = Settings(
        _env_file=None,
        JIRA_MODE="live",
        JIRA_BASE_URL="https://acme.atlassian.net",
        JIRA_EMAIL="me@acme.com",
        JIRA_API_TOKEN="tok",
        JIRA_PROJECT_KEY="OPS",
    )
    adapter = build_jira_adapter(s)
    assert isinstance(adapter, LiveJiraAdapter)
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://acme.atlassian.net/rest/api/3/issue").mock(
            return_value=httpx.Response(201, json={"key": "OPS-7"})
        )
        ticket = await adapter.create_ticket("Latency", "Root cause...", ["incident", "perf team"])
        assert ticket.key == "OPS-7"
        assert not ticket.mocked
        sent = route.calls.last.request
        body = sent.read().decode()
        assert '"key": "OPS"' in body or '"key":"OPS"' in body
        # labels must be space-stripped for Jira
        assert "perf_team" in body
    await adapter.aclose()


async def test_jira_live_falls_back_on_http_error():
    s = Settings(
        _env_file=None,
        JIRA_MODE="live",
        JIRA_BASE_URL="https://acme.atlassian.net",
        JIRA_API_TOKEN="tok",
    )
    adapter = build_jira_adapter(s)
    with respx.mock:
        respx.post("https://acme.atlassian.net/rest/api/3/issue").mock(
            return_value=httpx.Response(500, text="boom")
        )
        ticket = await adapter.create_ticket("S", "D")
        assert ticket.mocked  # degraded gracefully
    await adapter.aclose()


# ---- GitHub ---------------------------------------------------------------
async def test_github_mock_creates_pr():
    adapter = MockGitHubAdapter(Settings(_env_file=None, GITHUB_REPO="o/r"))
    pr = await adapter.create_pull_request("t", "b", "feat/x")
    assert pr.created and pr.mocked
    assert pr.number and pr.url.endswith(str(pr.number))


def test_github_factory_degrades_without_creds():
    adapter = build_github_adapter(Settings(_env_file=None, GITHUB_MODE="live"))
    assert isinstance(adapter, MockGitHubAdapter)


async def test_github_live_full_pr_flow():
    s = Settings(
        _env_file=None,
        GITHUB_MODE="live",
        GITHUB_TOKEN="ghp",
        GITHUB_REPO="acme/checkout",
        GITHUB_DEFAULT_BASE_BRANCH="main",
    )
    adapter = build_github_adapter(s)
    assert isinstance(adapter, LiveGitHubAdapter)
    base = "https://api.github.com/repos/acme/checkout"
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{base}/git/ref/heads/main").mock(
            return_value=httpx.Response(200, json={"object": {"sha": "deadbeef"}})
        )
        mock.post(f"{base}/git/refs").mock(return_value=httpx.Response(201, json={}))
        mock.post(f"{base}/pulls").mock(
            return_value=httpx.Response(
                201, json={"number": 99, "html_url": "https://github.com/acme/checkout/pull/99"}
            )
        )
        pr = await adapter.create_pull_request("fix", "body", "fix/x", diff="--- a\n+++ b")
        assert pr.number == 99 and not pr.mocked
        assert "Proposed diff" in pr.body  # diff attached
    await adapter.aclose()
