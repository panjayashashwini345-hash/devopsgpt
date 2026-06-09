"""Bundled demo data so DevOpsGPT runs end-to-end with zero infrastructure.

This is the canonical "Checkout API is slow" scenario from the project brief:
a deploy of ``checkout-service`` introduces an N+1 DB query / connection-pool
exhaustion that shows up as latency + 5xx in logs and traces. The mock Splunk
backend, mock LLM, and tests all draw from here so the demo is coherent.
"""

from __future__ import annotations

from ..models import Deployment, SplunkEvent

# --- Logs ------------------------------------------------------------------
MOCK_LOG_EVENTS: list[SplunkEvent] = [
    SplunkEvent(
        raw='2026-06-05T14:02:11Z level=ERROR service=checkout-service msg="DB connection pool exhausted" pool_size=20 active=20 wait_ms=4900 endpoint=/api/checkout',
        timestamp="2026-06-05T14:02:11Z",
        sourcetype="checkout:app",
        index="main",
        source="/var/log/checkout/app.log",
        fields={
            "level": "ERROR",
            "service": "checkout-service",
            "endpoint": "/api/checkout",
            "wait_ms": 4900,
            "pool_size": 20,
            "active": 20,
        },
    ),
    SplunkEvent(
        raw='2026-06-05T14:02:09Z level=WARN service=checkout-service msg="slow query" query="SELECT * FROM line_items WHERE order_id=?" duration_ms=820 calls=37 endpoint=/api/checkout',
        timestamp="2026-06-05T14:02:09Z",
        sourcetype="checkout:app",
        index="main",
        source="/var/log/checkout/app.log",
        fields={
            "level": "WARN",
            "service": "checkout-service",
            "endpoint": "/api/checkout",
            "duration_ms": 820,
            "calls": 37,
        },
    ),
    SplunkEvent(
        raw='2026-06-05T14:01:58Z level=ERROR service=checkout-service status=503 msg="upstream timeout" endpoint=/api/checkout latency_ms=5012',
        timestamp="2026-06-05T14:01:58Z",
        sourcetype="checkout:access",
        index="main",
        source="/var/log/checkout/access.log",
        fields={
            "level": "ERROR",
            "service": "checkout-service",
            "endpoint": "/api/checkout",
            "status": 503,
            "latency_ms": 5012,
        },
    ),
]

# --- Traces ----------------------------------------------------------------
MOCK_TRACE_EVENTS: list[SplunkEvent] = [
    SplunkEvent(
        raw='trace_id=ab12cd34 service=checkout-service operation=POST /api/checkout duration_ms=5012 span.db.calls=37 span.db.total_ms=4100 status=error',
        timestamp="2026-06-05T14:01:58Z",
        sourcetype="otel:trace",
        index="traces",
        fields={
            "trace_id": "ab12cd34",
            "service": "checkout-service",
            "operation": "POST /api/checkout",
            "duration_ms": 5012,
            "span.db.calls": 37,
            "span.db.total_ms": 4100,
            "status": "error",
        },
    ),
    SplunkEvent(
        raw='trace_id=ef56gh78 service=checkout-service operation=POST /api/checkout duration_ms=4880 span.db.calls=35 span.db.total_ms=3980 status=error',
        timestamp="2026-06-05T14:01:40Z",
        sourcetype="otel:trace",
        index="traces",
        fields={
            "trace_id": "ef56gh78",
            "service": "checkout-service",
            "operation": "POST /api/checkout",
            "duration_ms": 4880,
            "span.db.calls": 35,
            "span.db.total_ms": 3980,
            "status": "error",
        },
    ),
]

# --- Deployments -----------------------------------------------------------
MOCK_DEPLOYMENTS: list[Deployment] = [
    Deployment(
        service="checkout-service",
        version="v2.4.0",
        deployed_at="2026-06-05T13:55:00Z",
        commit="9f3c1a2",
        author="dev@example.com",
        environment="production",
    ),
    Deployment(
        service="checkout-service",
        version="v2.3.9",
        deployed_at="2026-06-04T09:10:00Z",
        commit="1b7e9d0",
        author="dev@example.com",
        environment="production",
    ),
]

# --- Source code -----------------------------------------------------------
# The "offending" file the agent inspects. Note the per-item query inside the
# loop (classic N+1) introduced in v2.4.0.
MOCK_SOURCE_FILES: dict[str, str] = {
    "checkout-service/src/checkout/order.py": (
        "def load_order_items(order):\n"
        "    items = []\n"
        "    for line in order.lines:\n"
        "        # N+1: one query per line item, added in v2.4.0 (commit 9f3c1a2)\n"
        "        item = db.query(\n"
        '            "SELECT * FROM line_items WHERE order_id = ?", line.id\n'
        "        )\n"
        "        items.append(item)\n"
        "    return items\n"
    ),
}

# Proposed fix the mock agent emits — a single batched query.
MOCK_PROPOSED_DIFF = """\
--- a/checkout-service/src/checkout/order.py
+++ b/checkout-service/src/checkout/order.py
@@ def load_order_items(order):
-    items = []
-    for line in order.lines:
-        # N+1: one query per line item, added in v2.4.0 (commit 9f3c1a2)
-        item = db.query(
-            "SELECT * FROM line_items WHERE order_id = ?", line.id
-        )
-        items.append(item)
-    return items
+    # Batch fetch all line items in a single query to avoid N+1.
+    order_ids = [line.id for line in order.lines]
+    if not order_ids:
+        return []
+    placeholders = ",".join(["?"] * len(order_ids))
+    return db.query(
+        f"SELECT * FROM line_items WHERE order_id IN ({placeholders})",
+        *order_ids,
+    )
"""
