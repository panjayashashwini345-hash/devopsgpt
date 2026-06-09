"""Mock Splunk backend — bundled demo data, no network. Always healthy.

Does light keyword matching on the SPL so different queries surface relevant
slices of the canned dataset, making the offline demo feel responsive.
"""

from __future__ import annotations

from ..logging import get_logger
from ..models import Deployment, SearchResult, SplunkEvent
from . import mock_data

log = get_logger(__name__)


class MockSplunkClient:
    backend = "mock"

    async def health_check(self) -> bool:
        return True

    async def search(
        self,
        spl: str,
        *,
        earliest: str | None = None,
        latest: str | None = None,
        max_results: int = 200,
    ) -> SearchResult:
        lowered = spl.lower()
        events: list[SplunkEvent]

        if "trace" in lowered or "span" in lowered or "otel" in lowered:
            events = mock_data.MOCK_TRACE_EVENTS
        elif "deploy" in lowered:
            events = []  # deployments come via list_deployments
        else:
            events = mock_data.MOCK_LOG_EVENTS

        # Crude relevance filter: if the query names a service/endpoint, keep
        # only matching events.
        for needle in ("checkout", "/api/checkout", "503", "error"):
            if needle in lowered:
                filtered = [
                    e for e in events if needle in e.raw.lower() or needle in str(e.fields).lower()
                ]
                if filtered:
                    events = filtered
                break

        events = events[:max_results]
        log.info("mock.search", spl=spl, returned=len(events))
        return SearchResult(
            query=spl,
            backend=self.backend,
            events=list(events),
            earliest=earliest,
            latest=latest,
            stats={"event_count": len(events)},
        )

    async def list_deployments(
        self,
        service: str | None = None,
        *,
        earliest: str | None = None,
        latest: str | None = None,
    ) -> list[Deployment]:
        deps = mock_data.MOCK_DEPLOYMENTS
        if service:
            deps = [d for d in deps if d.service == service]
        return list(deps)

    async def aclose(self) -> None:  # nothing to release
        return None
