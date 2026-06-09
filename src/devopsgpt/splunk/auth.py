"""Splunk authentication header builder.

A single function turns the configured :class:`AuthScheme` into HTTP headers
(or httpx auth), so swapping bearer / splunk / basic is one config change and
the rest of the code never branches on auth style.
"""

from __future__ import annotations

import base64

from ..config import AuthScheme, Settings


def build_splunk_auth_headers(settings: Settings) -> dict[str, str]:
    """Return ``Authorization`` headers for Splunk REST per the configured scheme.

    Returns an empty dict if credentials are missing — callers decide whether
    that is fatal (it is not, in mock/auto modes).
    """
    scheme = settings.splunk_auth_scheme

    if scheme is AuthScheme.BEARER and settings.splunk_token:
        return {"Authorization": f"Bearer {settings.splunk_token}"}

    if scheme is AuthScheme.SPLUNK and settings.splunk_token:
        return {"Authorization": f"Splunk {settings.splunk_token}"}

    if scheme is AuthScheme.BASIC and settings.splunk_username and settings.splunk_password:
        raw = f"{settings.splunk_username}:{settings.splunk_password}".encode()
        return {"Authorization": f"Basic {base64.b64encode(raw).decode()}"}

    return {}


def splunk_mcp_env(settings: Settings) -> dict[str, str]:
    """Environment passed down to an stdio-launched Splunk MCP Server subprocess.

    The MCP server itself authenticates to Splunk; we forward connection details
    via its conventional env vars. Only non-empty values are included.
    """
    env = {
        "SPLUNK_HOST": settings.splunk_host,
        "SPLUNK_PORT": str(settings.splunk_mgmt_port),
        "SPLUNK_SCHEME": settings.splunk_scheme,
        "SPLUNK_VERIFY_SSL": "true" if settings.splunk_verify_ssl else "false",
        "SPLUNK_TOKEN": settings.splunk_token,
        "SPLUNK_USERNAME": settings.splunk_username,
        "SPLUNK_PASSWORD": settings.splunk_password,
    }
    return {k: v for k, v in env.items() if v}
