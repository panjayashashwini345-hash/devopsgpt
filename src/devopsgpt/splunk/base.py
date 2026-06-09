"""The ``SplunkClient`` protocol shared by every backend."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import Deployment, SearchResult


@runtime_checkable
class SplunkClient(Protocol):
    """Capability surface the agent depends on.

    Implementations must be safe to call concurrently and must never raise for
    an empty result — return a :class:`SearchResult` with ``error`` set instead,
    so the agent can reason about partial failures rather than crash.
    """

    @property
    def backend(self) -> str:
        """Short identifier for the active backend ("mcp" | "rest" | "mock")."""
        ...

    async def health_check(self) -> bool:
        """Return True if this backend is reachable/usable right now."""
        ...

    async def search(
        self,
        spl: str,
        *,
        earliest: str | None = None,
        latest: str | None = None,
        max_results: int = 200,
    ) -> SearchResult:
        """Run an SPL search and return normalized events."""
        ...

    async def list_deployments(
        self,
        service: str | None = None,
        *,
        earliest: str | None = None,
        latest: str | None = None,
    ) -> list[Deployment]:
        """Return recent deployments, optionally filtered by service.

        Backends without a dedicated deployments source derive these from a
        conventional SPL search (see :data:`DEPLOYMENT_SPL`).
        """
        ...

    async def aclose(self) -> None:
        """Release any held resources (HTTP clients, MCP sessions)."""
        ...


#: Conventional SPL used to surface deployment markers when no dedicated API
#: exists. Override via the agent prompt for your environment's data model.
DEPLOYMENT_SPL = (
    'search index=* (sourcetype=deploy OR source=*deploy* OR event_type=deployment) '
    "| sort - _time | head 25"
)
