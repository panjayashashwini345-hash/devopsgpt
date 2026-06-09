"""Splunk REST search client (fallback path).

Talks to the Splunk management API (default port 8089). Supports three search
strategies selected by :class:`SearchMode`:

* ``oneshot`` — ``POST /services/search/jobs`` with ``exec_mode=oneshot``;
  results returned inline, no polling. Default — simplest and demo-friendly.
* ``export``  — ``POST /services/search/jobs/export``; streamed results.
* ``async``   — create job -> poll ``dispatchState`` -> fetch ``/results``.

Endpoint paths honor ``SPLUNK_REST_API_VERSION`` (``v1`` -> ``/services``,
``v2`` -> ``/servicesNS`` style ``/services/search/v2/jobs``). Auth header is
built once from the configured scheme.

All network errors are caught and surfaced as ``SearchResult.error`` so the
agent/factory can fall back rather than crash.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from ..config import SearchMode, Settings
from ..logging import get_logger
from ..models import Deployment, SearchResult
from .auth import build_splunk_auth_headers
from .base import DEPLOYMENT_SPL
from .normalization import events_to_deployments, row_to_event, service_filter_clause

log = get_logger(__name__)


def _normalize_spl(spl: str) -> str:
    """Splunk requires non-generating searches to begin with ``search``."""
    stripped = spl.strip()
    if stripped.startswith("|") or stripped.lower().startswith("search "):
        return stripped
    return f"search {stripped}"


class RestSplunkClient:
    backend = "rest"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._jobs_path = self._build_jobs_path(settings)
        headers = {"Accept": "application/json", **build_splunk_auth_headers(settings)}
        self._client = httpx.AsyncClient(
            base_url=settings.splunk_base_url,
            headers=headers,
            verify=settings.splunk_verify_ssl,
            timeout=settings.http_timeout_s,
        )

    @staticmethod
    def _build_jobs_path(settings: Settings) -> str:
        if settings.splunk_rest_api_version.lower() == "v2":
            return "/services/search/v2/jobs"
        return "/services/search/jobs"

    # ----- health ----------------------------------------------------------
    async def health_check(self) -> bool:
        if not self._settings.has_splunk_credentials:
            return False
        try:
            resp = await self._client.get(
                "/services/server/info", params={"output_mode": "json"}
            )
            return resp.status_code == 200
        except httpx.HTTPError as exc:
            log.warning("rest.health_check_failed", error=str(exc))
            return False

    # ----- search ----------------------------------------------------------
    async def search(
        self,
        spl: str,
        *,
        earliest: str | None = None,
        latest: str | None = None,
        max_results: int = 200,
    ) -> SearchResult:
        normalized = _normalize_spl(spl)
        earliest = earliest or self._settings.splunk_default_earliest
        latest = latest or self._settings.splunk_default_latest
        result = SearchResult(query=normalized, backend=self.backend, earliest=earliest, latest=latest)

        try:
            mode = self._settings.splunk_search_mode
            if mode is SearchMode.ONESHOT:
                rows = await self._search_oneshot(normalized, earliest, latest, max_results)
            elif mode is SearchMode.EXPORT:
                rows = await self._search_export(normalized, earliest, latest, max_results)
            else:
                rows = await self._search_async(normalized, earliest, latest, max_results)
            result.events = [row_to_event(r) for r in rows]
            result.truncated = len(rows) >= max_results
            result.stats = {"event_count": len(rows)}
        except httpx.HTTPStatusError as exc:
            result.error = f"HTTP {exc.response.status_code}: {exc.response.text[:300]}"
            log.warning("rest.search_http_error", error=result.error)
        except httpx.HTTPError as exc:
            result.error = str(exc)
            log.warning("rest.search_error", error=result.error)
        return result

    async def _search_oneshot(
        self, spl: str, earliest: str, latest: str, max_results: int
    ) -> list[dict[str, Any]]:
        resp = await self._client.post(
            self._jobs_path,
            data={
                "search": spl,
                "exec_mode": "oneshot",
                "output_mode": "json",
                "earliest_time": earliest,
                "latest_time": latest,
                "count": max_results,
            },
        )
        resp.raise_for_status()
        return resp.json().get("results", [])

    async def _search_export(
        self, spl: str, earliest: str, latest: str, max_results: int
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        async with self._client.stream(
            "POST",
            f"{self._jobs_path}/export",
            data={
                "search": spl,
                "output_mode": "json",
                "earliest_time": earliest,
                "latest_time": latest,
            },
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                row = payload.get("result")
                if row:
                    rows.append(row)
                if len(rows) >= max_results:
                    break
        return rows

    async def _search_async(
        self, spl: str, earliest: str, latest: str, max_results: int
    ) -> list[dict[str, Any]]:
        # 1) create job
        resp = await self._client.post(
            self._jobs_path,
            data={
                "search": spl,
                "output_mode": "json",
                "earliest_time": earliest,
                "latest_time": latest,
            },
        )
        resp.raise_for_status()
        sid = resp.json()["sid"]

        # 2) poll until done
        deadline = self._settings.max_poll_s
        waited = 0.0
        while waited < deadline:
            status = await self._client.get(
                f"{self._jobs_path}/{sid}", params={"output_mode": "json"}
            )
            status.raise_for_status()
            entry = status.json()["entry"][0]["content"]
            if entry.get("isDone"):
                break
            if entry.get("dispatchState") == "FAILED":
                raise httpx.HTTPError(f"search job {sid} FAILED")
            await asyncio.sleep(self._settings.poll_interval_s)
            waited += self._settings.poll_interval_s

        # 3) fetch results
        results = await self._client.get(
            f"{self._jobs_path}/{sid}/results",
            params={"output_mode": "json", "count": max_results},
        )
        results.raise_for_status()
        return results.json().get("results", [])

    # ----- deployments ------------------------------------------------------
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
        await self._client.aclose()
