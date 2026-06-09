"""Splunk client abstraction.

One ``SplunkClient`` interface, three implementations:

* :class:`~devopsgpt.splunk.mock_client.MockSplunkClient` — bundled demo data,
  no network. Always works.
* :class:`~devopsgpt.splunk.rest_client.RestSplunkClient` — Splunk REST search
  jobs API (oneshot / export / async), token or basic auth.
* :class:`~devopsgpt.splunk.mcp_client.McpSplunkClient` — Splunk MCP Server via
  the Model Context Protocol, discovering tools at runtime.

:func:`build_splunk_client` selects/auto-falls-back per :class:`SplunkMode`.
"""

from __future__ import annotations

from .base import SplunkClient
from .factory import build_splunk_client

__all__ = ["SplunkClient", "build_splunk_client"]
