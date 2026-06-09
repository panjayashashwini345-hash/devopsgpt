"""Factory + auto-fallback wrapper for Splunk clients.

``build_splunk_client`` honors :class:`SplunkMode`:

* ``mock`` — always the mock backend.
* ``rest`` — REST only.
* ``mcp``  — MCP only.
* ``auto`` — an :class:`AutoSplunkClient` that probes MCP first, then REST, then
  mock, on first use and per-call if a backend goes unhealthy.

The wrapper resolves its delegate lazily on first call so app startup never
blocks on a network probe.
"""

from __future__ import annotations

import asyncio

from ..config import Settings, SplunkMode
from ..logging import get_logger
from ..models import Deployment, SearchResult
from .base import SplunkClient
from .mcp_client import McpSplunkClient
from .mock_client import MockSplunkClient
from .rest_client import RestSplunkClient

log = get_logger(__name__)


class AutoSplunkClient:
    """Tries MCP -> REST -> mock, caching the first healthy delegate."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._delegate: SplunkClient | None = None
        self._resolve_lock = asyncio.Lock()
        self._candidates: list[SplunkClient] = [
            McpSplunkClient(settings),
            RestSplunkClient(settings),
            MockSplunkClient(),
        ]

    @property
    def backend(self) -> str:
        return self._delegate.backend if self._delegate else "auto"

    async def _resolve(self) -> SplunkClient:
        if self._delegate is not None:
            return self._delegate
        # Serialize concurrent first-use probing so we don't run redundant
        # health checks / open duplicate connections under load.
        async with self._resolve_lock:
            if self._delegate is not None:
                return self._delegate
            for candidate in self._candidates:
                try:
                    if await candidate.health_check():
                        self._delegate = candidate
                        log.info("splunk.auto_selected", backend=candidate.backend)
                        return candidate
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "splunk.candidate_failed", backend=candidate.backend, error=str(exc)
                    )
            # Guaranteed fallback.
            self._delegate = self._candidates[-1]
            log.info("splunk.auto_selected", backend=self._delegate.backend, note="forced mock")
            return self._delegate

    async def health_check(self) -> bool:
        return await (await self._resolve()).health_check()

    async def search(self, spl: str, **kwargs) -> SearchResult:
        return await (await self._resolve()).search(spl, **kwargs)

    async def list_deployments(self, service: str | None = None, **kwargs) -> list[Deployment]:
        return await (await self._resolve()).list_deployments(service, **kwargs)

    async def aclose(self) -> None:
        import contextlib

        for candidate in self._candidates:
            with contextlib.suppress(Exception):
                await candidate.aclose()


def build_splunk_client(settings: Settings) -> SplunkClient:
    """Construct the Splunk client for the configured mode."""
    mode = settings.splunk_mode
    if mode is SplunkMode.MOCK:
        return MockSplunkClient()
    if mode is SplunkMode.REST:
        return RestSplunkClient(settings)
    if mode is SplunkMode.MCP:
        return McpSplunkClient(settings)
    return AutoSplunkClient(settings)
