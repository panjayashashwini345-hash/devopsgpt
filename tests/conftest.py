"""Shared pytest fixtures. The whole suite runs offline against mocks."""

from __future__ import annotations

import pytest

from devopsgpt.config import Settings
from devopsgpt.service import Services, build_services


@pytest.fixture
def mock_settings() -> Settings:
    """Settings forced into fully-mocked mode regardless of the host env."""
    return Settings(
        DEVOPSGPT_LLM_PROVIDER="mock",
        SPLUNK_MODE="mock",
        JIRA_MODE="mock",
        GITHUB_MODE="mock",
        DEVOPSGPT_ALLOW_WRITE_ACTIONS="true",
        DEVOPSGPT_LOG_LEVEL="WARNING",
    )


@pytest.fixture
async def services(mock_settings: Settings):
    svc: Services = build_services(mock_settings)
    yield svc
    await svc.aclose()
