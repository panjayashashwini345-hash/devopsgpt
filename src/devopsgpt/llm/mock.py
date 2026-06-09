"""Deterministic, offline mock LLM provider.

Drives the canonical "Checkout API is slow" investigation without any API key
so the whole pipeline can be demoed and tested offline. It walks a fixed plan,
emitting one realistic tool call per turn, then a final synthesized answer.

The plan adapts to whichever tools are actually registered: each step is gated
on its tool being present, so the mock stays valid if tools are added/removed.
"""

from __future__ import annotations

import json
from typing import Any

from .base import AssistantTurn, ToolCall, ToolResult, ToolSpec

# Ordered investigation plan: (tool_name, argument_factory, narration).
_PLAN: list[tuple[str, Any, str]] = [
    (
        "search_splunk_logs",
        lambda q: {"query": f'index=main "{_service(q)}" (error OR ERROR OR 5* OR slow)', "max_results": 50},
        "Searching application logs for errors and latency around the reported issue.",
    ),
    (
        "search_traces",
        lambda q: {"query": f'index=traces service={_service(q)} status=error', "max_results": 50},
        "Pulling distributed traces to see where time is spent on the slow path.",
    ),
    (
        "correlate_deployments",
        lambda q: {"service": _service(q)},
        "Correlating the latency onset with recent deployments.",
    ),
    (
        "get_source_code",
        lambda q: {"path": "checkout-service/src/checkout/order.py"},
        "Inspecting the code path implicated by the slow query and recent deploy.",
    ),
]


def _service(question: str) -> str:
    q = question.lower()
    if "checkout" in q:
        return "checkout-service"
    # Fall back to the first token that looks like a service name.
    for word in q.replace("/", " ").split():
        if word.endswith("-service") or word.endswith("-api"):
            return word
    return "checkout-service"


_FINAL_TEMPLATE = """\
## Root cause
A change shipped in **checkout-service v2.4.0** (commit `9f3c1a2`, deployed \
13:55Z) introduced an **N+1 query** in `load_order_items` — one \
`SELECT * FROM line_items WHERE order_id = ?` per line item (≈37 calls/request). \
Under load this exhausts the DB connection pool (20/20 active, ~4.9s waits), \
producing 503s and ~5s latency on `POST /api/checkout`.

## Evidence
- Logs: `DB connection pool exhausted` + `slow query` (820ms × 37 calls).
- Traces: `span.db.calls=37`, `span.db.total_ms≈4100` of 5012ms total.
- Deployment: latency onset aligns with v2.4.0 at 13:55Z.
- Source: per-item query inside the `for line in order.lines` loop.

## Suggested fix
Replace the per-item loop with a single batched `WHERE order_id IN (...)` query, \
eliminating the N+1 and relieving pool pressure.

## Severity
High — customer-facing checkout latency and 503s in production.
"""


class MockConversation:
    def __init__(self, tools: list[ToolSpec]):
        self._available = {t.name for t in tools}
        self._question = ""
        self._step = 0
        # Whether the agent asked us to also create ticket/PR (inferred from tools).
        self._can_ticket = "create_jira_ticket" in self._available
        self._can_pr = "create_github_pr" in self._available
        self._post_investigation_done = False

    async def send(self, user_message: str) -> AssistantTurn:
        self._question = user_message
        return self._next_turn()

    async def submit_tool_results(self, results: list[ToolResult]) -> AssistantTurn:
        return self._next_turn()

    def _next_turn(self) -> AssistantTurn:
        # Phase 1: investigation tools.
        while self._step < len(_PLAN):
            name, arg_factory, narration = _PLAN[self._step]
            self._step += 1
            if name in self._available:
                return AssistantTurn(
                    text=narration,
                    tool_calls=[
                        ToolCall(
                            id=f"mock-{name}-{self._step}",
                            name=name,
                            arguments=arg_factory(self._question),
                        )
                    ],
                    stop_reason="tool_use",
                )

        # Phase 2: actions (ticket then PR), one per turn.
        if self._can_ticket:
            self._can_ticket = False
            return AssistantTurn(
                text="Filing a Jira ticket to track the fix.",
                tool_calls=[
                    ToolCall(
                        id="mock-jira",
                        name="create_jira_ticket",
                        arguments={
                            "summary": "Checkout API latency: N+1 query in load_order_items (v2.4.0)",
                            "description": _FINAL_TEMPLATE,
                            "labels": ["incident", "performance", "checkout"],
                        },
                    )
                ],
                stop_reason="tool_use",
            )

        if self._can_pr:
            self._can_pr = False
            return AssistantTurn(
                text="Opening a draft PR with the batched-query fix.",
                tool_calls=[
                    ToolCall(
                        id="mock-pr",
                        name="create_github_pr",
                        arguments={
                            "title": "fix(checkout): batch line_items query to remove N+1",
                            "body": "Replaces the per-item query in `load_order_items` with a single "
                            "`WHERE order_id IN (...)` batch query.\n\nRoot cause: see linked ticket.",
                            "branch": "fix/checkout-n-plus-one",
                        },
                    )
                ],
                stop_reason="tool_use",
            )

        # Phase 3: final synthesis.
        return AssistantTurn(text=_FINAL_TEMPLATE, stop_reason="end")


class MockProvider:
    name = "mock"

    def start_conversation(self, *, system: str, tools: list[ToolSpec]) -> MockConversation:
        return MockConversation(tools)

    async def aclose(self) -> None:
        return None


def _dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)
