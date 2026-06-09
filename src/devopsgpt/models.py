"""Shared domain models — the contracts passed between layers.

Kept deliberately small and JSON-serializable so they flow cleanly from the
Splunk clients, through the agent, into the API responses, and out to Jira/GitHub.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Splunk search results
# ---------------------------------------------------------------------------
class SplunkEvent(BaseModel):
    """A single normalized event/row returned from a Splunk search."""

    raw: str = ""
    fields: dict[str, Any] = Field(default_factory=dict)
    timestamp: str | None = None
    source: str | None = None
    sourcetype: str | None = None
    index: str | None = None


class SearchResult(BaseModel):
    """Outcome of one SPL search, regardless of backend (MCP / REST / mock)."""

    query: str
    backend: str = "unknown"  # "mcp" | "rest" | "mock"
    events: list[SplunkEvent] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
    earliest: str | None = None
    latest: str | None = None
    truncated: bool = False
    error: str | None = None

    @property
    def count(self) -> int:
        return len(self.events)


# ---------------------------------------------------------------------------
# Deployment correlation
# ---------------------------------------------------------------------------
class Deployment(BaseModel):
    service: str
    version: str
    deployed_at: str
    commit: str | None = None
    author: str | None = None
    environment: str = "production"


# ---------------------------------------------------------------------------
# Actions: Jira & GitHub
# ---------------------------------------------------------------------------
class JiraTicket(BaseModel):
    key: str | None = None  # e.g. OPS-1234 (None until created)
    url: str | None = None
    summary: str = ""
    description: str = ""
    project_key: str = ""
    issue_type: str = "Bug"
    labels: list[str] = Field(default_factory=list)
    created: bool = False
    mocked: bool = False


class PullRequest(BaseModel):
    number: int | None = None
    url: str | None = None
    title: str = ""
    body: str = ""
    repo: str = ""
    head_branch: str = ""
    base_branch: str = "main"
    diff: str = ""  # unified diff / patch the agent proposes
    created: bool = False
    mocked: bool = False


# ---------------------------------------------------------------------------
# Agent reasoning trace
# ---------------------------------------------------------------------------
class StepType(str, Enum):
    THOUGHT = "thought"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    FINAL = "final"
    ERROR = "error"


class AgentStep(BaseModel):
    """One observable step in the agent loop — streamed to the UI."""

    index: int
    type: StepType
    summary: str
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_output: Any | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Final structured output
# ---------------------------------------------------------------------------
class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Evidence(BaseModel):
    """A citation linking the conclusion back to observed data."""

    source: str  # "splunk_logs" | "splunk_traces" | "deployment" | "source_code"
    detail: str
    query: str | None = None


class IncidentReport(BaseModel):
    """The agent's final deliverable for an investigation."""

    investigation_id: str
    question: str
    summary: str = ""
    root_cause: str = ""
    severity: Severity = Severity.MEDIUM
    confidence: float = 0.0  # 0..1
    evidence: list[Evidence] = Field(default_factory=list)
    suggested_fix: str = ""
    proposed_diff: str = ""
    jira_ticket: JiraTicket | None = None
    pull_request: PullRequest | None = None
    steps: list[AgentStep] = Field(default_factory=list)
    llm_provider: str = ""
    splunk_backend: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    elapsed_s: float | None = None


# ---------------------------------------------------------------------------
# API request
# ---------------------------------------------------------------------------
class InvestigateRequest(BaseModel):
    question: str = Field(..., min_length=3, examples=["Checkout API is slow"])
    earliest: str | None = None
    latest: str | None = None
    index: str | None = None
    create_ticket: bool = True
    open_pull_request: bool = True
