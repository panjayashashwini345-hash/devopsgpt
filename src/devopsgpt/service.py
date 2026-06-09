"""Dependency assembly — wires settings into ready-to-use agents.

Centralizes construction + teardown of the long-lived components (LLM provider,
Splunk client, Jira/GitHub adapters) so both the API layer and the CLI share one
path. A fresh :class:`~devopsgpt.tools.ToolRegistry` + :class:`~devopsgpt.agent.Agent`
is minted per investigation so per-request side effects (created ticket/PR) never
leak between requests, while the expensive clients are reused.
"""

from __future__ import annotations

from dataclasses import dataclass

from .agent import Agent
from .config import Settings, get_settings
from .integrations import GitHubAdapter, JiraAdapter, build_github_adapter, build_jira_adapter
from .llm import LLMProvider, build_llm_provider
from .logging import get_logger
from .splunk import SplunkClient, build_splunk_client
from .tools import ToolRegistry

log = get_logger(__name__)


@dataclass
class Services:
    """Long-lived components for one app lifecycle."""

    settings: Settings
    provider: LLMProvider
    splunk: SplunkClient
    jira: JiraAdapter
    github: GitHubAdapter

    def new_agent(self) -> tuple[Agent, ToolRegistry]:
        """Mint a fresh agent + registry sharing the long-lived clients."""
        registry = ToolRegistry(self.settings, self.splunk, self.jira, self.github)
        return Agent(self.settings, self.provider, registry), registry

    async def aclose(self) -> None:
        for component in (self.github, self.jira, self.splunk, self.provider):
            if hasattr(component, "aclose"):
                try:
                    await component.aclose()
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "services.close_error",
                        component=type(component).__name__,
                        error=str(exc),
                    )


def build_services(settings: Settings | None = None) -> Services:
    """Construct the long-lived services."""
    settings = settings or get_settings()
    services = Services(
        settings=settings,
        provider=build_llm_provider(settings),
        splunk=build_splunk_client(settings),
        jira=build_jira_adapter(settings),
        github=build_github_adapter(settings),
    )
    log.info(
        "services.ready",
        llm_provider=services.provider.name,
        splunk_mode=settings.splunk_mode.value,
        jira_mode=settings.effective_jira_mode().value,
        github_mode=settings.effective_github_mode().value,
        write_actions=settings.allow_write_actions,
    )
    return services
