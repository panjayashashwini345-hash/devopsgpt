"""End-to-end agent loop tests against the mock provider + mock backends."""

from __future__ import annotations

import asyncio

from devopsgpt.agent import Agent
from devopsgpt.config import Settings
from devopsgpt.llm import ToolSpec
from devopsgpt.models import IncidentReport, Severity, StepType
from devopsgpt.service import Services, build_services
from devopsgpt.tools import ToolRegistry


class _HangingProvider:
    """A provider whose first model call never returns — to exercise the
    agent's per-call timeout (H4 regression test)."""

    name = "hanging"

    def start_conversation(self, *, system: str, tools: list[ToolSpec]):
        return self

    async def send(self, user_message: str):
        await asyncio.sleep(3600)  # far longer than the test's deadline

    async def submit_tool_results(self, results):  # pragma: no cover - never reached
        await asyncio.sleep(3600)

    async def aclose(self):
        return None


async def test_llm_call_timeout_is_enforced():
    """A hung provider must be bounded by agent_timeout_s, not run forever."""
    s = Settings(
        _env_file=None,
        SPLUNK_MODE="mock",
        JIRA_MODE="mock",
        GITHUB_MODE="mock",
        DEVOPSGPT_AGENT_TIMEOUT_S=1,  # int seconds; wait_for uses the remaining time
    )
    svc = build_services(s)
    try:
        registry = ToolRegistry(s, svc.splunk, svc.jira, svc.github)
        agent = Agent(s, _HangingProvider(), registry)
        report = await asyncio.wait_for(agent.investigate("Checkout API is slow"), timeout=10)
        assert isinstance(report, IncidentReport)
        assert any(
            st.type is StepType.ERROR and "timed out" in st.summary.lower()
            for st in report.steps
        )
        assert report.root_cause == ""  # never reached a conclusion
    finally:
        await svc.aclose()


async def test_call_llm_past_deadline_does_not_leak_coroutine(recwarn):
    """When the deadline is already past, _call_llm must close the unused
    coroutine (no 'coroutine was never awaited' RuntimeWarning) and raise."""
    import asyncio

    awaited = False

    async def fake_send():
        nonlocal awaited
        awaited = True
        return "unused"

    coro = fake_send()
    loop = asyncio.get_running_loop()
    try:
        await Agent._call_llm(coro, deadline=loop.time() - 1)
        raise AssertionError("expected TimeoutError")
    except TimeoutError:
        pass
    assert awaited is False  # coroutine was never run
    # No "never awaited" warning should have been recorded.
    assert not any("never awaited" in str(w.message) for w in recwarn.list)


async def test_iteration_limit_finalizes_without_stale_conclusion():
    """A tight iteration cap forces an early break; the report must NOT claim a
    root cause it never reached (regression test for stale turn.text)."""
    s = Settings(
        _env_file=None,
        DEVOPSGPT_LLM_PROVIDER="mock",
        SPLUNK_MODE="mock",
        JIRA_MODE="mock",
        GITHUB_MODE="mock",
        DEVOPSGPT_MAX_AGENT_ITERATIONS=1,  # break after the first tool round
    )
    svc = build_services(s)
    try:
        agent, _ = svc.new_agent()
        report = await agent.investigate("Checkout API is slow")
        assert isinstance(report, IncidentReport)
        # Did not conclude => empty root cause and an explanatory summary.
        assert report.root_cause == ""
        assert "did not reach a conclusion" in report.summary.lower()
        # An ERROR step recorded the early stop.
        assert any(st.type is StepType.ERROR for st in report.steps)
    finally:
        await svc.aclose()


async def test_full_investigation_produces_report(services: Services):
    agent, _ = services.new_agent()
    report = await agent.investigate("Checkout API is slow")

    assert isinstance(report, IncidentReport)
    assert report.severity is Severity.HIGH
    assert report.root_cause and "N+1" in report.root_cause
    assert report.suggested_fix
    assert report.proposed_diff  # captured from get_source_code
    assert report.llm_provider == "mock"
    assert report.splunk_backend == "mock"
    assert 0 < report.confidence <= 1
    assert report.elapsed_s is not None


async def test_investigation_creates_ticket_and_pr(services: Services):
    agent, _ = services.new_agent()
    report = await agent.investigate("Checkout API is slow")
    assert report.jira_ticket is not None and report.jira_ticket.created
    assert report.pull_request is not None and report.pull_request.created


async def test_evidence_links_to_sources(services: Services):
    agent, _ = services.new_agent()
    report = await agent.investigate("Checkout API is slow")
    sources = {e.source for e in report.evidence}
    # The mock plan touches logs, traces, deployments and source.
    assert {"splunk_logs", "splunk_traces", "deployment", "source_code"} <= sources


async def test_stream_emits_ordered_steps_then_report(services: Services):
    agent, _ = services.new_agent()
    types: list[StepType] = []
    final: IncidentReport | None = None
    async for step in agent.stream("Checkout API is slow"):
        types.append(step.type)
        if step.type is StepType.FINAL:
            final = step.tool_output

    assert StepType.TOOL_CALL in types
    assert StepType.TOOL_RESULT in types
    assert types[-1] is StepType.FINAL  # FINAL is always last
    assert isinstance(final, IncidentReport)


async def test_write_killswitch_blocks_actions_end_to_end(mock_settings):
    from dataclasses import replace  # noqa: F401 - Settings isn't a dataclass

    from devopsgpt.config import Settings
    from devopsgpt.service import build_services

    s = Settings(
        _env_file=None,
        DEVOPSGPT_LLM_PROVIDER="mock",
        SPLUNK_MODE="mock",
        JIRA_MODE="mock",
        GITHUB_MODE="mock",
        DEVOPSGPT_ALLOW_WRITE_ACTIONS="false",
    )
    svc = build_services(s)
    try:
        agent, _ = svc.new_agent()
        report = await agent.investigate("Checkout API is slow")
        # Investigation still completes and finds the cause...
        assert report.root_cause
        # ...but no ticket/PR were actually created.
        assert report.jira_ticket is None
        assert report.pull_request is None
    finally:
        await svc.aclose()


async def test_per_request_isolation(services: Services):
    """Two investigations must not share side-effect state."""
    a1, _ = services.new_agent()
    r1 = await a1.investigate("Checkout API is slow")
    a2, _ = services.new_agent()
    r2 = await a2.investigate("Checkout API is slow")
    assert r1.investigation_id != r2.investigation_id
    # Distinct ticket keys prove fresh registries.
    assert r1.jira_ticket.key != r2.jira_ticket.key
