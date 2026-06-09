"""API tests using FastAPI's in-process TestClient (no socket bind)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from devopsgpt.api.app import create_app


@pytest.fixture
def client(monkeypatch):
    # Force mock everything regardless of host env / .env file.
    for key, val in {
        "DEVOPSGPT_LLM_PROVIDER": "mock",
        "SPLUNK_MODE": "mock",
        "JIRA_MODE": "mock",
        "GITHUB_MODE": "mock",
        "DEVOPSGPT_LOG_LEVEL": "WARNING",
    }.items():
        monkeypatch.setenv(key, val)
    # get_settings is cached; clear it so the env above takes effect.
    from devopsgpt.config import get_settings

    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_readyz_reports_backend(client):
    r = client.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["splunk_backend"] == "mock"
    assert body["llm_provider"] == "mock"


def test_config_is_non_secret(client):
    body = client.get("/config").json()
    assert body["write_actions_enabled"] in (True, False)
    # No secrets should leak.
    assert "token" not in json.dumps(body).lower()


def test_index_serves_ui(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "DevOpsGPT" in r.text


def test_investigate_returns_report(client):
    r = client.post("/investigate", json={"question": "Checkout API is slow"})
    assert r.status_code == 200
    body = r.json()
    assert body["severity"] == "high"
    assert body["jira_ticket"]["key"].startswith("OPS-")
    assert body["pull_request"]["url"]
    assert len(body["steps"]) > 0


def test_investigate_validates_short_question(client):
    r = client.post("/investigate", json={"question": "x"})
    assert r.status_code == 422  # min_length=3


def test_stream_emits_steps_and_report(client):
    with client.stream(
        "POST", "/investigate/stream", json={"question": "Checkout API is slow"}
    ) as s:
        counts: dict[str, int] = {}
        for line in s.iter_lines():
            if line.startswith("event:"):
                ev = line.split(":", 1)[1].strip()
                counts[ev] = counts.get(ev, 0) + 1
    assert counts.get("step", 0) > 0
    assert counts.get("report", 0) == 1
    assert counts.get("done", 0) == 1


def test_request_id_header_roundtrips(client):
    r = client.get("/healthz", headers={"x-request-id": "test-123"})
    assert r.headers["x-request-id"] == "test-123"
