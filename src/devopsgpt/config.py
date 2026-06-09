"""Typed application configuration loaded from environment / ``.env``.

Design goals (driven by the integration research):

* **Nothing hard-fails when unconfigured.** With an empty environment the app
  boots in fully-mocked, offline mode so it can be demoed without Splunk, an
  LLM key, Jira, or GitHub.
* **Every uncertain value is a knob.** Splunk transport, ports, auth scheme,
  REST API version, hosted-model base URL, etc. are all env-driven with safe
  defaults rather than hardcoded.
* **Modes are explicit enums**, not magic strings scattered through the code.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProvider(str, Enum):
    """Which model drives the agent's reasoning loop."""

    CLAUDE = "claude"
    SPLUNK_HOSTED = "splunk-hosted"
    MOCK = "mock"


class SplunkMode(str, Enum):
    """How the agent reaches Splunk for searches."""

    AUTO = "auto"  # prefer MCP, fall back to REST, then mock
    MCP = "mcp"
    REST = "rest"
    MOCK = "mock"


class AuthScheme(str, Enum):
    """``Authorization`` header style for Splunk REST / MCP downstream calls."""

    BEARER = "bearer"  # Authorization: Bearer <jwt>
    SPLUNK = "splunk"  # Authorization: Splunk <session_key>
    BASIC = "basic"  # HTTP Basic username:password


class SearchMode(str, Enum):
    """Splunk search execution strategy."""

    ONESHOT = "oneshot"  # synchronous, results inline, no poll loop
    EXPORT = "export"  # streamed export endpoint
    ASYNC = "async"  # create job -> poll -> fetch results


class McpTransport(str, Enum):
    STDIO = "stdio"
    SSE = "sse"
    HTTP = "http"


class IntegrationMode(str, Enum):
    """Whether an action integration calls a real API or is stubbed."""

    MOCK = "mock"
    LIVE = "live"


def _split_csv(value: str | list[str] | None) -> list[str]:
    """Parse a comma-separated env string into a clean list."""
    if value is None:
        return []
    if isinstance(value, list):
        return [v.strip() for v in value if str(v).strip()]
    return [part.strip() for part in value.split(",") if part.strip()]


class Settings(BaseSettings):
    """Application settings. Field env names map 1:1 to ``.env.example``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ----- App -------------------------------------------------------------
    app_name: str = Field(default="DevOpsGPT", alias="DEVOPSGPT_APP_NAME")
    log_level: str = Field(default="INFO", alias="DEVOPSGPT_LOG_LEVEL")
    log_json: bool = Field(default=False, alias="DEVOPSGPT_LOG_JSON")
    host: str = Field(default="0.0.0.0", alias="DEVOPSGPT_HOST")
    port: int = Field(default=8000, alias="DEVOPSGPT_PORT")
    cors_origins: str = Field(default="*", alias="DEVOPSGPT_CORS_ORIGINS")
    allow_write_actions: bool = Field(default=True, alias="DEVOPSGPT_ALLOW_WRITE_ACTIONS")
    max_agent_iterations: int = Field(default=12, alias="DEVOPSGPT_MAX_AGENT_ITERATIONS")
    agent_timeout_s: int = Field(default=120, alias="DEVOPSGPT_AGENT_TIMEOUT_S")

    # ----- LLM provider ----------------------------------------------------
    llm_provider: LLMProvider = Field(default=LLMProvider.MOCK, alias="DEVOPSGPT_LLM_PROVIDER")

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    claude_model: str = Field(default="claude-opus-4-8", alias="DEVOPSGPT_CLAUDE_MODEL")
    claude_max_tokens: int = Field(default=4096, alias="DEVOPSGPT_CLAUDE_MAX_TOKENS")

    hosted_models_base_url: str = Field(default="", alias="HOSTED_MODELS_BASE_URL")
    hosted_models_api_key: str = Field(default="", alias="HOSTED_MODELS_API_KEY")
    hosted_models_default_model: str = Field(
        default="gpt-oss-20b", alias="HOSTED_MODELS_DEFAULT_MODEL"
    )
    hosted_models_openai_compat: bool = Field(default=True, alias="HOSTED_MODELS_OPENAI_COMPAT")

    # ----- Splunk ----------------------------------------------------------
    splunk_mode: SplunkMode = Field(default=SplunkMode.AUTO, alias="SPLUNK_MODE")
    splunk_host: str = Field(default="localhost", alias="SPLUNK_HOST")
    splunk_mgmt_port: int = Field(default=8089, alias="SPLUNK_MGMT_PORT")
    splunk_scheme: str = Field(default="https", alias="SPLUNK_SCHEME")
    splunk_verify_ssl: bool = Field(default=True, alias="SPLUNK_VERIFY_SSL")
    splunk_auth_scheme: AuthScheme = Field(default=AuthScheme.BEARER, alias="SPLUNK_AUTH_SCHEME")
    splunk_token: str = Field(default="", alias="SPLUNK_TOKEN")
    splunk_username: str = Field(default="", alias="SPLUNK_USERNAME")
    splunk_password: str = Field(default="", alias="SPLUNK_PASSWORD")

    splunk_search_mode: SearchMode = Field(default=SearchMode.ONESHOT, alias="SPLUNK_SEARCH_MODE")
    splunk_rest_api_version: str = Field(default="v1", alias="SPLUNK_REST_API_VERSION")
    splunk_default_earliest: str = Field(default="-24h", alias="SPLUNK_DEFAULT_EARLIEST")
    splunk_default_latest: str = Field(default="now", alias="SPLUNK_DEFAULT_LATEST")
    splunk_default_index: str = Field(default="main", alias="SPLUNK_DEFAULT_INDEX")

    # ----- Splunk MCP Server ----------------------------------------------
    mcp_transport: McpTransport = Field(default=McpTransport.STDIO, alias="MCP_TRANSPORT")
    mcp_server_command: str = Field(default="", alias="MCP_SERVER_COMMAND")
    mcp_server_args: str = Field(default="", alias="MCP_SERVER_ARGS")
    mcp_server_url: str = Field(default="", alias="MCP_SERVER_URL")

    # ----- Jira ------------------------------------------------------------
    jira_mode: IntegrationMode = Field(default=IntegrationMode.MOCK, alias="JIRA_MODE")
    jira_base_url: str = Field(default="", alias="JIRA_BASE_URL")
    jira_email: str = Field(default="", alias="JIRA_EMAIL")
    jira_api_token: str = Field(default="", alias="JIRA_API_TOKEN")
    jira_project_key: str = Field(default="OPS", alias="JIRA_PROJECT_KEY")
    jira_default_issue_type: str = Field(default="Bug", alias="JIRA_DEFAULT_ISSUE_TYPE")

    # ----- GitHub ----------------------------------------------------------
    github_mode: IntegrationMode = Field(default=IntegrationMode.MOCK, alias="GITHUB_MODE")
    github_token: str = Field(default="", alias="GITHUB_TOKEN")
    github_repo: str = Field(default="", alias="GITHUB_REPO")
    github_default_base_branch: str = Field(default="main", alias="GITHUB_DEFAULT_BASE_BRANCH")
    github_api_url: str = Field(default="https://api.github.com", alias="GITHUB_API_URL")

    # ----- HTTP resilience knobs ------------------------------------------
    http_timeout_s: float = Field(default=30.0, alias="HTTP_TIMEOUT_S")
    poll_interval_s: float = Field(default=1.0, alias="POLL_INTERVAL_S")
    max_poll_s: float = Field(default=300.0, alias="MAX_POLL_S")

    # ----- Validators ------------------------------------------------------
    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, v: str) -> str:
        return v.upper()

    # ----- Derived helpers -------------------------------------------------
    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return _split_csv(self.cors_origins)

    @property
    def mcp_server_arg_list(self) -> list[str]:
        return _split_csv(self.mcp_server_args)

    @property
    def splunk_base_url(self) -> str:
        return f"{self.splunk_scheme}://{self.splunk_host}:{self.splunk_mgmt_port}"

    @property
    def has_splunk_credentials(self) -> bool:
        if self.splunk_auth_scheme is AuthScheme.BASIC:
            return bool(self.splunk_username and self.splunk_password)
        return bool(self.splunk_token)

    @property
    def hosted_models_enabled(self) -> bool:
        return bool(self.hosted_models_base_url)

    def effective_jira_mode(self) -> IntegrationMode:
        """Live only if mode=live AND the minimum credentials exist."""
        if self.jira_mode is IntegrationMode.LIVE and self.jira_base_url and self.jira_api_token:
            return IntegrationMode.LIVE
        return IntegrationMode.MOCK

    def effective_github_mode(self) -> IntegrationMode:
        if self.github_mode is IntegrationMode.LIVE and self.github_token and self.github_repo:
            return IntegrationMode.LIVE
        return IntegrationMode.MOCK


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide cached :class:`Settings` instance."""
    return Settings()
