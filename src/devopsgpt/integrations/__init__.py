"""Action integrations — Jira and GitHub.

Each adapter exposes a mock implementation AND a live REST implementation behind
one interface, chosen by the effective mode (live only when mode=live AND creds
present, else mock). This lets the full agent loop run end-to-end offline while
the same code path drives real ticket/PR creation when configured.
"""

from __future__ import annotations

from .github import GitHubAdapter, build_github_adapter
from .jira import JiraAdapter, build_jira_adapter

__all__ = ["GitHubAdapter", "JiraAdapter", "build_github_adapter", "build_jira_adapter"]
