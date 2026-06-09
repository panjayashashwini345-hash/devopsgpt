"""Shared normalization + sanitization helpers for the Splunk backends.

Centralizing these here keeps the REST and MCP clients DRY and — critically —
ensures the SPL sanitization in :func:`service_filter_clause` is applied
identically by every backend, so the injection defense can't drift between them.
"""

from __future__ import annotations

import re
from typing import Any

from ..logging import get_logger
from ..models import Deployment, SplunkEvent

log = get_logger(__name__)

# A service name is an identifier: letters, digits, and a few safe separators.
# Anything else is stripped before the value is placed inside an SPL string,
# which prevents an LLM- or user-supplied value from breaking out of the
# quoted term and injecting arbitrary SPL (CWE-89).
_SERVICE_ALLOWED = re.compile(r"[^A-Za-z0-9._\-/: ]")


def sanitize_service(service: str) -> str:
    """Return a version of ``service`` safe to embed in a quoted SPL term.

    Drops characters that could terminate the quoted string or introduce new
    SPL syntax (quotes, backslashes, pipes, brackets, etc.). The result is still
    a plausible service name; if sanitization empties it, returns ``"*"`` so the
    query stays valid (matches any service) rather than producing broken SPL.
    """
    cleaned = _SERVICE_ALLOWED.sub("", service).strip()
    if cleaned != service:
        # Surface the rewrite so a misconfigured service filter isn't silently
        # altered (debugging aid; not a hard failure).
        log.warning("splunk.service_sanitized", original=service, sanitized=cleaned or "*")
    return cleaned or "*"


def service_filter_clause(service: str) -> str:
    """Build the ``service="..."`` SPL fragment with a sanitized value."""
    return f'service="{sanitize_service(service)}"'


def row_to_event(row: dict[str, Any]) -> SplunkEvent:
    """Normalize a raw Splunk result row into a :class:`SplunkEvent`.

    Splunk-internal fields (prefixed with ``_``) are surfaced as dedicated
    attributes; everything else becomes a searchable ``fields`` entry.
    """
    return SplunkEvent(
        raw=str(row.get("_raw", "")),
        timestamp=row.get("_time"),
        source=row.get("source"),
        sourcetype=row.get("sourcetype"),
        index=row.get("index"),
        fields={k: v for k, v in row.items() if not k.startswith("_")},
    )


def events_to_deployments(
    events: list[SplunkEvent], service: str | None
) -> list[Deployment]:
    """Map deployment-marker events into :class:`Deployment` records.

    Shared by every backend so the field-mapping conventions stay consistent.
    """
    deployments: list[Deployment] = []
    for ev in events:
        f = ev.fields
        deployments.append(
            Deployment(
                service=str(f.get("service", service or "unknown")),
                version=str(f.get("version", f.get("build", "unknown"))),
                deployed_at=ev.timestamp or "",
                commit=f.get("commit") or f.get("git_sha"),
                author=f.get("author"),
                environment=str(f.get("environment", "production")),
            )
        )
    return deployments
