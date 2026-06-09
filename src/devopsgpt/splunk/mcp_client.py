"""Splunk MCP Server client (primary path).

Connects to the Splunk MCP Server (Splunkbase app 8047) over the Model Context
Protocol. Per the integration research, the exact tool names exposed by the
server are NOT guaranteed, so this client:

1. Connects (stdio subprocess, or SSE/HTTP to a running server).
2. Calls ``list_tools()`` and maps the real tools to capabilities
   (search / list-deployments) by fuzzy-matching names + descriptions.
3. Dispatches ``call_tool`` and normalizes whatever shape comes back.

If the ``mcp`` package is missing, the server is unreachable, or no
search-capable tool is discovered, every method degrades gracefully
(``health_check`` -> False, ``search`` -> ``SearchResult`` with ``error``)
so the factory can fall back to REST.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from ..config import McpTransport, Settings
from ..logging import get_logger
from ..models import Deployment, SearchResult
from .auth import splunk_mcp_env
from .base import DEPLOYMENT_SPL
from .normalization import events_to_deployments, row_to_event, service_filter_clause

log = get_logger(__name__)

# Substrings used to recognize a tool's capability from its name/description.
_SEARCH_HINTS = ("search", "spl", "query", "oneshot", "run_search")
_DEPLOY_HINTS = ("deploy", "release", "change")


class McpSplunkClient:
    backend = "mcp"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._session: Any | None = None
        self._exit_stack: Any | None = None
        self._tools: dict[str, Any] = {}
        self._search_tool: str | None = None
        self._deploy_tool: str | None = None
        self._connected = False

    # ----- connection ------------------------------------------------------
    async def connect(self) -> bool:
        """Establish the MCP session and discover tools. Idempotent."""
        if self._connected:
            return True
        try:
            from contextlib import AsyncExitStack

            from mcp import ClientSession
        except ImportError:
            log.warning("mcp.sdk_missing", hint="pip install mcp")
            return False

        try:
            self._exit_stack = AsyncExitStack()
            read, write = await self._open_transport(self._exit_stack)
            self._session = await self._exit_stack.enter_async_context(
                ClientSession(read, write)
            )
            assert self._session is not None  # for type-narrowing
            await self._session.initialize()
            await self._discover_tools()
            self._connected = True
            log.info(
                "mcp.connected",
                transport=self._settings.mcp_transport.value,
                search_tool=self._search_tool,
                deploy_tool=self._deploy_tool,
                tools=list(self._tools),
            )
            return True
        except Exception as exc:  # noqa: BLE001 - any failure => fall back
            log.warning("mcp.connect_failed", error=str(exc))
            await self.aclose()
            return False

    async def _open_transport(self, stack: Any) -> tuple[Any, Any]:
        transport = self._settings.mcp_transport
        if transport is McpTransport.STDIO:
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            if not self._settings.mcp_server_command:
                raise RuntimeError("MCP_SERVER_COMMAND is required for stdio transport")
            params = StdioServerParameters(
                command=self._settings.mcp_server_command,
                args=self._settings.mcp_server_arg_list,
                env=splunk_mcp_env(self._settings),
            )
            return await stack.enter_async_context(stdio_client(params))

        if not self._settings.mcp_server_url:
            raise RuntimeError("MCP_SERVER_URL is required for sse/http transport")

        if transport is McpTransport.SSE:
            from mcp.client.sse import sse_client

            return await stack.enter_async_context(sse_client(self._settings.mcp_server_url))

        from mcp.client.streamable_http import streamablehttp_client

        read, write, _ = await stack.enter_async_context(
            streamablehttp_client(self._settings.mcp_server_url)
        )
        return read, write

    async def _discover_tools(self) -> None:
        assert self._session is not None
        listing = await self._session.list_tools()
        for tool in listing.tools:
            self._tools[tool.name] = tool
            haystack = f"{tool.name} {getattr(tool, 'description', '') or ''}".lower()
            if self._search_tool is None and any(h in haystack for h in _SEARCH_HINTS):
                self._search_tool = tool.name
            if self._deploy_tool is None and any(h in haystack for h in _DEPLOY_HINTS):
                self._deploy_tool = tool.name

    async def health_check(self) -> bool:
        ok = await self.connect()
        return ok and self._search_tool is not None

    # ----- search ----------------------------------------------------------
    async def search(
        self,
        spl: str,
        *,
        earliest: str | None = None,
        latest: str | None = None,
        max_results: int = 200,
    ) -> SearchResult:
        result = SearchResult(query=spl, backend=self.backend, earliest=earliest, latest=latest)
        if not await self.connect() or not self._search_tool:
            result.error = "MCP search tool unavailable"
            return result

        # Best-effort arg mapping: pass common aliases; the server ignores
        # unknown keys in practice, and we keep the payload small.
        args = {
            "query": spl,
            "search": spl,
            "spl": spl,
            "earliest_time": earliest or self._settings.splunk_default_earliest,
            "latest_time": latest or self._settings.splunk_default_latest,
            "count": max_results,
            "output_mode": "json",
        }
        try:
            assert self._session is not None
            raw = await self._session.call_tool(self._search_tool, args)
            rows = _extract_rows(raw)
            result.events = [row_to_event(r) for r in rows[:max_results]]
            result.stats = {"event_count": len(result.events)}
        except Exception as exc:  # noqa: BLE001
            result.error = f"MCP call_tool failed: {exc}"
            log.warning("mcp.search_failed", tool=self._search_tool, error=str(exc))
        return result

    async def list_deployments(
        self,
        service: str | None = None,
        *,
        earliest: str | None = None,
        latest: str | None = None,
    ) -> list[Deployment]:
        spl = DEPLOYMENT_SPL
        if service:
            spl = spl.replace("index=*", f"index=* {service_filter_clause(service)}")
        res = await self.search(spl, earliest=earliest, latest=latest, max_results=25)
        return events_to_deployments(res.events, service)

    async def aclose(self) -> None:
        self._connected = False
        if self._exit_stack is not None:
            with contextlib.suppress(Exception):
                await self._exit_stack.aclose()
            self._exit_stack = None
        self._session = None


# --- normalization helpers -------------------------------------------------
def _extract_rows(raw: Any) -> list[dict[str, Any]]:
    """Pull tabular rows out of an MCP ``CallToolResult``.

    MCP tools return ``content`` blocks (text / JSON). We accept either a JSON
    object with a ``results`` array, a bare JSON array, or newline-delimited
    JSON, and fall back to wrapping plain text as a single ``_raw`` event.
    """
    texts: list[str] = []
    content = getattr(raw, "content", None)
    if content:
        for block in content:
            text = getattr(block, "text", None)
            if text:
                texts.append(text)
    elif isinstance(raw, str):
        texts.append(raw)

    rows: list[dict[str, Any]] = []
    for text in texts:
        text = text.strip()
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            # maybe NDJSON
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    rows.extend(_coerce(obj))
                except json.JSONDecodeError:
                    rows.append({"_raw": line})
            continue
        rows.extend(_coerce(parsed))
    return rows


def _coerce(obj: Any) -> list[dict[str, Any]]:
    if isinstance(obj, dict):
        if isinstance(obj.get("results"), list):
            return [r for r in obj["results"] if isinstance(r, dict)]
        return [obj]
    if isinstance(obj, list):
        return [r for r in obj if isinstance(r, dict)]
    return [{"_raw": str(obj)}]
