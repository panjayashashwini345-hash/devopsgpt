"""Jira integration — create issues via the Jira Cloud REST v3 API, or mock.

Live mode authenticates with email + API token (HTTP Basic, the standard Jira
Cloud scheme) and posts to ``/rest/api/3/issue`` with an Atlassian Document
Format description. Mock mode fabricates a deterministic ticket key/url so the
agent loop completes offline.

A live failure degrades to a mock ticket (with ``error`` noted) rather than
raising, so one flaky integration never aborts an investigation.
"""

from __future__ import annotations

from typing import Protocol

import httpx

from ..config import IntegrationMode, Settings
from ..logging import get_logger
from ..models import JiraTicket

log = get_logger(__name__)


class JiraAdapter(Protocol):
    async def create_ticket(
        self, summary: str, description: str, labels: list[str] | None = None
    ) -> JiraTicket:
        ...

    async def aclose(self) -> None:
        ...


def _adf(description: str) -> dict:
    """Wrap plain text in minimal Atlassian Document Format."""
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": description[:30000]}]}
        ],
    }


class MockJiraAdapter:
    def __init__(self, settings: Settings) -> None:
        self._project = settings.jira_project_key
        self._issue_type = settings.jira_default_issue_type
        self._counter = 1000

    async def create_ticket(
        self, summary: str, description: str, labels: list[str] | None = None
    ) -> JiraTicket:
        self._counter += 1
        key = f"{self._project}-{self._counter}"
        log.info("jira.mock_create", key=key, summary=summary)
        return JiraTicket(
            key=key,
            url=f"https://jira.example.com/browse/{key}",
            summary=summary,
            description=description,
            project_key=self._project,
            issue_type=self._issue_type,
            labels=labels or [],
            created=True,
            mocked=True,
        )

    async def aclose(self) -> None:
        return None


class LiveJiraAdapter:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._project = settings.jira_project_key
        self._issue_type = settings.jira_default_issue_type
        self._client = httpx.AsyncClient(
            base_url=settings.jira_base_url.rstrip("/"),
            auth=(settings.jira_email, settings.jira_api_token),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=settings.http_timeout_s,
        )
        self._fallback = MockJiraAdapter(settings)

    async def create_ticket(
        self, summary: str, description: str, labels: list[str] | None = None
    ) -> JiraTicket:
        payload = {
            "fields": {
                "project": {"key": self._project},
                "summary": summary[:255],
                "description": _adf(description),
                "issuetype": {"name": self._issue_type},
                "labels": [_safe_label(label) for label in (labels or [])],
            }
        }
        try:
            resp = await self._client.post("/rest/api/3/issue", json=payload)
            resp.raise_for_status()
            data = resp.json()
            key = data["key"]
            base = self._settings.jira_base_url.rstrip("/")
            log.info("jira.live_create", key=key)
            return JiraTicket(
                key=key,
                url=f"{base}/browse/{key}",
                summary=summary,
                description=description,
                project_key=self._project,
                issue_type=self._issue_type,
                labels=labels or [],
                created=True,
                mocked=False,
            )
        except httpx.HTTPError as exc:
            log.warning("jira.live_create_failed", error=str(exc), fallback="mock")
            ticket = await self._fallback.create_ticket(summary, description, labels)
            return ticket

    async def aclose(self) -> None:
        await self._client.aclose()
        await self._fallback.aclose()


def _safe_label(label: str) -> str:
    # Jira labels cannot contain spaces.
    return label.strip().replace(" ", "_")


def build_jira_adapter(settings: Settings) -> JiraAdapter:
    if settings.effective_jira_mode() is IntegrationMode.LIVE:
        return LiveJiraAdapter(settings)
    if settings.jira_mode is IntegrationMode.LIVE:
        log.warning("jira.live_requested_without_creds", fallback="mock")
    return MockJiraAdapter(settings)
