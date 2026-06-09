"""Config layer tests — defaults, mode resolution, derived helpers."""

from __future__ import annotations

from devopsgpt.config import AuthScheme, IntegrationMode, LLMProvider, Settings, SplunkMode


def test_defaults_are_fully_mockable():
    s = Settings(_env_file=None)
    # Out of the box everything should be safe/offline-capable.
    assert s.llm_provider is LLMProvider.MOCK
    assert s.splunk_mode is SplunkMode.AUTO
    assert s.effective_jira_mode() is IntegrationMode.MOCK
    assert s.effective_github_mode() is IntegrationMode.MOCK


def test_splunk_base_url_uses_mgmt_port():
    s = Settings(_env_file=None, SPLUNK_HOST="splunk.local", SPLUNK_MGMT_PORT=8089)
    assert s.splunk_base_url == "https://splunk.local:8089"


def test_cors_origin_list_parsing():
    assert Settings(_env_file=None, DEVOPSGPT_CORS_ORIGINS="*").cors_origin_list == ["*"]
    s = Settings(_env_file=None, DEVOPSGPT_CORS_ORIGINS="https://a.com, https://b.com")
    assert s.cors_origin_list == ["https://a.com", "https://b.com"]


def test_mcp_arg_list_parsing():
    s = Settings(_env_file=None, MCP_SERVER_ARGS="-m, splunk_mcp, --verbose")
    assert s.mcp_server_arg_list == ["-m", "splunk_mcp", "--verbose"]


def test_has_splunk_credentials_by_scheme():
    assert not Settings(_env_file=None, SPLUNK_AUTH_SCHEME="bearer").has_splunk_credentials
    assert Settings(_env_file=None, SPLUNK_AUTH_SCHEME="bearer", SPLUNK_TOKEN="t").has_splunk_credentials
    basic = Settings(
        _env_file=None, SPLUNK_AUTH_SCHEME="basic", SPLUNK_USERNAME="u", SPLUNK_PASSWORD="p"
    )
    assert basic.has_splunk_credentials


def test_effective_mode_requires_credentials():
    # mode=live but missing creds -> degrades to mock.
    s = Settings(_env_file=None, JIRA_MODE="live", GITHUB_MODE="live")
    assert s.effective_jira_mode() is IntegrationMode.MOCK
    assert s.effective_github_mode() is IntegrationMode.MOCK

    live = Settings(
        _env_file=None,
        JIRA_MODE="live",
        JIRA_BASE_URL="https://x.atlassian.net",
        JIRA_API_TOKEN="tok",
        GITHUB_MODE="live",
        GITHUB_TOKEN="ghp",
        GITHUB_REPO="o/r",
    )
    assert live.effective_jira_mode() is IntegrationMode.LIVE
    assert live.effective_github_mode() is IntegrationMode.LIVE


def test_auth_scheme_enum_values():
    assert AuthScheme.BEARER.value == "bearer"
    assert AuthScheme.SPLUNK.value == "splunk"
    assert AuthScheme.BASIC.value == "basic"
