"""Agent tool registry.

Bridges the LLM's normalized tool calls to concrete capabilities (Splunk search,
deployment correlation, source lookup, Jira, GitHub). Each tool is declared once
with a JSON-Schema parameter spec (advertised to the model) and an async handler.

The registry:
* exposes :meth:`specs` for the provider,
* dispatches a :class:`ToolCall` to its handler via :meth:`dispatch`,
* tracks side effects (created ticket / PR) for the final report,
* honors the global ``allow_write_actions`` kill-switch — when off, write tools
  return the *planned* payload without calling out.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from .config import Settings
from .integrations import GitHubAdapter, JiraAdapter
from .llm import ToolSpec
from .logging import get_logger
from .models import Deployment, JiraTicket, PullRequest, SearchResult
from .splunk import SplunkClient
from .splunk.mock_data import MOCK_PROPOSED_DIFF, MOCK_SOURCE_FILES

log = get_logger(__name__)

Handler = Callable[[dict[str, Any]], Awaitable[Any]]


class Tool:
    def __init__(self, spec: ToolSpec, handler: Handler, *, writes: bool = False) -> None:
        self.spec = spec
        self.handler = handler
        self.writes = writes


def _obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


class ToolRegistry:
    def __init__(
        self,
        settings: Settings,
        splunk: SplunkClient,
        jira: JiraAdapter,
        github: GitHubAdapter,
    ) -> None:
        self._settings = settings
        self._splunk = splunk
        self._jira = jira
        self._github = github
        self._tools: dict[str, Tool] = {}

        # Side effects captured for the final report.
        self.created_ticket: JiraTicket | None = None
        self.created_pr: PullRequest | None = None
        self.last_proposed_diff: str = ""

        self._register_all()

    # ----- registration ----------------------------------------------------
    def _register_all(self) -> None:
        self._add(
            "search_splunk_logs",
            "Run an SPL search against Splunk logs and return matching events. "
            "Use to find errors, warnings, status codes, and latency for a service.",
            _obj(
                {
                    "query": {"type": "string", "description": "SPL query, e.g. index=main error"},
                    "earliest": {"type": "string", "description": "Splunk time modifier, e.g. -24h"},
                    "latest": {"type": "string"},
                    "max_results": {"type": "integer", "default": 100},
                },
                required=["query"],
            ),
            self._h_search_logs,
        )
        self._add(
            "search_traces",
            "Search distributed traces/spans in Splunk to find where request time "
            "is spent and which operations error. Returns trace events.",
            _obj(
                {
                    "query": {"type": "string", "description": "SPL over the traces index"},
                    "earliest": {"type": "string"},
                    "latest": {"type": "string"},
                    "max_results": {"type": "integer", "default": 100},
                },
                required=["query"],
            ),
            self._h_search_traces,
        )
        self._add(
            "correlate_deployments",
            "List recent deployments/releases for a service so you can correlate an "
            "incident's onset with a specific version/commit.",
            _obj(
                {
                    "service": {"type": "string", "description": "Service name, e.g. checkout-service"},
                    "earliest": {"type": "string"},
                    "latest": {"type": "string"},
                }
            ),
            self._h_correlate_deployments,
        )
        self._add(
            "get_source_code",
            "Fetch the contents of a source file implicated in the incident so you "
            "can identify the offending code and propose a fix.",
            _obj(
                {"path": {"type": "string", "description": "Repo-relative file path"}},
                required=["path"],
            ),
            self._h_get_source,
        )
        self._add(
            "create_jira_ticket",
            "Create a Jira ticket to track remediation of the incident. Provide a "
            "concise summary and a detailed description (root cause + fix).",
            _obj(
                {
                    "summary": {"type": "string"},
                    "description": {"type": "string"},
                    "labels": {"type": "array", "items": {"type": "string"}},
                },
                required=["summary", "description"],
            ),
            self._h_create_jira,
            writes=True,
        )
        self._add(
            "create_github_pr",
            "Open a draft GitHub pull request with the proposed code fix. Provide a "
            "title, body, and the branch name to create.",
            _obj(
                {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "branch": {"type": "string", "description": "New branch name"},
                    "file_path": {"type": "string", "description": "File to modify (optional)"},
                    "file_content": {"type": "string", "description": "Full new file contents (optional)"},
                },
                required=["title", "body", "branch"],
            ),
            self._h_create_pr,
            writes=True,
        )

    def _add(self, name: str, desc: str, params: dict, handler: Handler, *, writes: bool = False):
        self._tools[name] = Tool(ToolSpec(name=name, description=desc, parameters=params), handler, writes=writes)

    # ----- public API -------------------------------------------------------
    def specs(self) -> list[ToolSpec]:
        return [t.spec for t in self._tools.values()]

    async def dispatch(self, name: str, arguments: dict[str, Any]) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            return {"error": f"unknown tool: {name}"}
        if tool.writes and not self._settings.allow_write_actions:
            log.info("tool.write_suppressed", tool=name)
            return {
                "suppressed": True,
                "reason": "write actions disabled (DEVOPSGPT_ALLOW_WRITE_ACTIONS=false)",
                "planned_arguments": arguments,
            }
        return await tool.handler(arguments)

    @staticmethod
    def serialize(value: Any) -> str:
        """Render a tool result as compact JSON for the model."""
        if hasattr(value, "model_dump"):
            value = value.model_dump()
        return json.dumps(value, default=str)

    # ----- handlers ---------------------------------------------------------
    async def _h_search_logs(self, args: dict[str, Any]) -> dict[str, Any]:
        res: SearchResult = await self._splunk.search(
            args["query"],
            earliest=args.get("earliest"),
            latest=args.get("latest"),
            max_results=int(args.get("max_results", 100)),
        )
        return self._summarize_search(res)

    async def _h_search_traces(self, args: dict[str, Any]) -> dict[str, Any]:
        res = await self._splunk.search(
            args["query"],
            earliest=args.get("earliest"),
            latest=args.get("latest"),
            max_results=int(args.get("max_results", 100)),
        )
        return self._summarize_search(res)

    async def _h_correlate_deployments(self, args: dict[str, Any]) -> dict[str, Any]:
        deps: list[Deployment] = await self._splunk.list_deployments(
            args.get("service"), earliest=args.get("earliest"), latest=args.get("latest")
        )
        return {"deployments": [d.model_dump() for d in deps], "count": len(deps)}

    async def _h_get_source(self, args: dict[str, Any]) -> dict[str, Any]:
        path = args["path"]
        content = MOCK_SOURCE_FILES.get(path)
        if content is None:
            # Best-effort: match by basename so the agent isn't blocked on exact paths.
            for known, body in MOCK_SOURCE_FILES.items():
                if known.endswith(path) or path.endswith(known.split("/")[-1]):
                    content, path = body, known
                    break
        if content is None:
            return {"path": path, "found": False, "content": ""}
        self.last_proposed_diff = MOCK_PROPOSED_DIFF
        return {"path": path, "found": True, "content": content}

    async def _h_create_jira(self, args: dict[str, Any]) -> dict[str, Any]:
        ticket = await self._jira.create_ticket(
            summary=args["summary"],
            description=args["description"],
            labels=args.get("labels"),
        )
        self.created_ticket = ticket
        return ticket.model_dump()

    async def _h_create_pr(self, args: dict[str, Any]) -> dict[str, Any]:
        pr = await self._github.create_pull_request(
            title=args["title"],
            body=args["body"],
            branch=args["branch"],
            file_path=args.get("file_path"),
            file_content=args.get("file_content"),
            diff=self.last_proposed_diff,
        )
        self.created_pr = pr
        return pr.model_dump()

    # ----- helpers ----------------------------------------------------------
    @staticmethod
    def _summarize_search(res: SearchResult) -> dict[str, Any]:
        # Keep token usage sane: cap events handed back to the model.
        sample = [
            {"raw": e.raw, "timestamp": e.timestamp, "fields": e.fields}
            for e in res.events[:25]
        ]
        return {
            "query": res.query,
            "backend": res.backend,
            "count": res.count,
            "truncated": res.truncated,
            "error": res.error,
            "events": sample,
        }
