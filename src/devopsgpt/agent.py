"""The DevOpsGPT agent core — the investigate -> root-cause -> fix -> ticket -> PR loop.

Drives a provider-neutral tool-calling conversation:

1. Send the user's question to the model.
2. While the model requests tools (and we're under the iteration/time budget):
   run each tool via the registry, stream a step, and feed results back.
3. When the model stops, parse its final Markdown into a structured
   :class:`IncidentReport`, attaching any ticket/PR side effects.

The loop is exposed two ways:
* :meth:`Agent.investigate` — awaitable, returns the full report.
* :meth:`Agent.stream` — async generator yielding :class:`AgentStep` events as
  they happen, then a final event carrying the report. The FastAPI layer wraps
  this for SSE.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import AsyncGenerator, Awaitable
from datetime import UTC, datetime

from .config import Settings
from .llm import AssistantTurn, LLMProvider, ToolResult
from .logging import get_logger
from .models import (
    AgentStep,
    Evidence,
    IncidentReport,
    Severity,
    StepType,
)
from .prompts import SYSTEM_PROMPT
from .tools import ToolRegistry

log = get_logger(__name__)


class Agent:
    def __init__(self, settings: Settings, provider: LLMProvider, registry: ToolRegistry) -> None:
        self._settings = settings
        self._provider = provider
        self._registry = registry

    async def investigate(
        self,
        question: str,
        *,
        earliest: str | None = None,
        latest: str | None = None,
        investigation_id: str | None = None,
    ) -> IncidentReport:
        report: IncidentReport | None = None
        async for step in self.stream(
            question, earliest=earliest, latest=latest, investigation_id=investigation_id
        ):
            if step.type is StepType.FINAL and isinstance(step.tool_output, IncidentReport):
                report = step.tool_output
        assert report is not None  # stream always emits a FINAL report
        return report

    async def stream(
        self,
        question: str,
        *,
        earliest: str | None = None,
        latest: str | None = None,
        investigation_id: str | None = None,
    ) -> AsyncGenerator[AgentStep, None]:
        inv_id = investigation_id or uuid.uuid4().hex[:12]
        started = datetime.now(UTC)
        steps: list[AgentStep] = []
        idx = 0

        def mk(step_type: StepType, summary: str, **kw) -> AgentStep:
            nonlocal idx
            step = AgentStep(index=idx, type=step_type, summary=summary, **kw)
            idx += 1
            steps.append(step)
            return step

        # Augment the question with operator-supplied time bounds for the model.
        framed = question
        if earliest or latest:
            framed += f"\n\n(Time window: earliest={earliest or 'default'}, latest={latest or 'default'})"

        conversation = self._provider.start_conversation(
            system=SYSTEM_PROMPT, tools=self._registry.specs()
        )

        deadline = asyncio.get_running_loop().time() + self._settings.agent_timeout_s

        try:
            turn = await self._call_llm(conversation.send(framed), deadline)
        except TimeoutError:
            log.warning(
                "agent.llm_timeout",
                phase="initial",
                provider=self._provider.name,
                timeout_s=self._settings.agent_timeout_s,
                investigation_id=inv_id,
            )
            yield mk(StepType.ERROR, "LLM call timed out; finalizing.", error="timeout")
            yield self._finalize(mk, inv_id, question, steps, started, error="LLM call timed out.")
            return
        except Exception as exc:  # noqa: BLE001
            yield mk(StepType.ERROR, f"LLM call failed: {exc}", error=str(exc))
            yield self._finalize(mk, inv_id, question, steps, started, error=str(exc))
            return

        iterations = 0
        final_text = ""
        while True:
            if turn.text.strip():
                yield mk(StepType.THOUGHT, turn.text.strip())

            if not turn.wants_tools:
                # The model concluded — this turn's text is the report.
                final_text = turn.text
                break

            if iterations >= self._settings.max_agent_iterations:
                yield mk(StepType.ERROR, "Reached max agent iterations; finalizing.")
                break
            if asyncio.get_running_loop().time() > deadline:
                yield mk(StepType.ERROR, "Investigation timed out; finalizing.")
                break
            iterations += 1

            # Run every requested tool and collect results.
            results: list[ToolResult] = []
            for call in turn.tool_calls:
                yield mk(
                    StepType.TOOL_CALL,
                    f"{call.name}({_brief(call.arguments)})",
                    tool_name=call.name,
                    tool_input=call.arguments,
                )
                try:
                    output = await self._registry.dispatch(call.name, call.arguments)
                    content = self._registry.serialize(output)
                    yield mk(
                        StepType.TOOL_RESULT,
                        f"{call.name} -> {_brief(output)}",
                        tool_name=call.name,
                        tool_output=output,
                    )
                    results.append(
                        ToolResult(tool_call_id=call.id, name=call.name, content=content)
                    )
                except Exception as exc:  # noqa: BLE001 - keep loop alive on tool error
                    yield mk(
                        StepType.ERROR,
                        f"{call.name} failed: {exc}",
                        tool_name=call.name,
                        error=str(exc),
                    )
                    results.append(
                        ToolResult(
                            tool_call_id=call.id,
                            name=call.name,
                            content=f'{{"error": {exc!r}}}',
                            is_error=True,
                        )
                    )

            try:
                turn = await self._call_llm(conversation.submit_tool_results(results), deadline)
            except TimeoutError:
                log.warning(
                    "agent.llm_timeout",
                    phase="tool_results",
                    provider=self._provider.name,
                    timeout_s=self._settings.agent_timeout_s,
                    investigation_id=inv_id,
                )
                yield mk(StepType.ERROR, "LLM call timed out; finalizing.", error="timeout")
                break
            except Exception as exc:  # noqa: BLE001
                yield mk(StepType.ERROR, f"LLM call failed: {exc}", error=str(exc))
                break

        yield self._finalize(mk, inv_id, question, steps, started, final_text=final_text)

    @staticmethod
    async def _call_llm(coro: Awaitable[AssistantTurn], deadline: float) -> AssistantTurn:
        """Await an LLM coroutine, bounded by the remaining time to ``deadline``.

        Enforces ``agent_timeout_s`` on the model call itself (raising
        :class:`TimeoutError`) so a hung provider can't run past the budget —
        the loop-level deadline check only fires *between* calls.
        """
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            # The caller already created the coroutine; close it so we don't
            # emit a "coroutine was never awaited" RuntimeWarning.
            close = getattr(coro, "close", None)
            if close is not None:
                close()
            raise TimeoutError("agent deadline exceeded before LLM call")
        return await asyncio.wait_for(coro, timeout=remaining)

    # ----- finalize ---------------------------------------------------------
    def _finalize(
        self,
        mk,
        inv_id: str,
        question: str,
        steps: list[AgentStep],
        started: datetime,
        *,
        final_text: str = "",
        error: str | None = None,
    ) -> AgentStep:
        finished = datetime.now(UTC)
        report = IncidentReport(
            investigation_id=inv_id,
            question=question,
            summary=_first_paragraph(final_text)
            if final_text
            else (error or "Investigation did not reach a conclusion (timeout or iteration limit)."),
            root_cause=_section(final_text, "Root cause"),
            severity=_parse_severity(final_text),
            confidence=_confidence(steps, final_text),
            evidence=_evidence_from_steps(steps),
            suggested_fix=_section(final_text, "Suggested fix"),
            proposed_diff=self._registry.last_proposed_diff,
            jira_ticket=self._registry.created_ticket,
            pull_request=self._registry.created_pr,
            steps=list(steps),
            llm_provider=self._provider.name,
            splunk_backend=self._registry._splunk.backend,
            started_at=started,
            finished_at=finished,
            elapsed_s=(finished - started).total_seconds(),
        )
        if self._registry.created_pr and not report.proposed_diff:
            report.proposed_diff = self._registry.created_pr.diff
        return mk(StepType.FINAL, "Investigation complete.", tool_output=report)


# --- parsing helpers -------------------------------------------------------
def _brief(value, limit: int = 160) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _first_paragraph(text: str) -> str:
    for block in text.split("\n\n"):
        cleaned = block.strip().lstrip("#").strip()
        if cleaned:
            return cleaned
    return text.strip()


def _section(text: str, heading: str) -> str:
    """Extract a ``## heading`` section body from the model's Markdown."""
    if not text:
        return ""
    pattern = re.compile(
        rf"#+\s*{re.escape(heading)}\s*\n(.*?)(?=\n#+\s|\Z)", re.IGNORECASE | re.DOTALL
    )
    m = pattern.search(text)
    return m.group(1).strip() if m else ""


def _parse_severity(text: str) -> Severity:
    body = _section(text, "Severity") or text
    lowered = body.lower()
    for sev in (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO):
        if sev.value in lowered:
            return sev
    return Severity.MEDIUM


def _confidence(steps: list[AgentStep], final_text: str) -> float:
    """Heuristic confidence: more distinct evidence sources + a real conclusion."""
    tool_results = {s.tool_name for s in steps if s.type is StepType.TOOL_RESULT}
    base = min(0.2 + 0.18 * len(tool_results), 0.9)
    if not _section(final_text, "Root cause"):
        base *= 0.5
    return round(base, 2)


_SOURCE_BY_TOOL = {
    "search_splunk_logs": "splunk_logs",
    "search_traces": "splunk_traces",
    "correlate_deployments": "deployment",
    "get_source_code": "source_code",
}


def _evidence_from_steps(steps: list[AgentStep]) -> list[Evidence]:
    evidence: list[Evidence] = []
    for s in steps:
        if s.type is StepType.TOOL_RESULT and s.tool_name in _SOURCE_BY_TOOL:
            query = None
            if isinstance(s.tool_output, dict):
                query = s.tool_output.get("query")
            evidence.append(
                Evidence(
                    source=_SOURCE_BY_TOOL[s.tool_name],
                    detail=s.summary,
                    query=query,
                )
            )
    return evidence
