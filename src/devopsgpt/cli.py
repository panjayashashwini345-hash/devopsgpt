"""``devopsgpt`` command-line interface.

Subcommands:
* ``serve``      — run the FastAPI app with uvicorn.
* ``investigate``— run a one-shot investigation from the terminal and print the
                   report (handy for demos / CI smoke tests, no server needed).
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from .config import get_settings
from .logging import configure_logging, get_logger
from .models import StepType
from .service import build_services

log = get_logger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="devopsgpt", description="DevOpsGPT CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the API server")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--reload", action="store_true")

    inv = sub.add_parser("investigate", help="Run a one-shot investigation")
    inv.add_argument("question", help="The problem to investigate")
    inv.add_argument("--earliest", default=None)
    inv.add_argument("--latest", default=None)
    inv.add_argument("--quiet", action="store_true", help="Only print the final report")

    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(settings)

    if args.command == "serve":
        import uvicorn

        uvicorn.run(
            "devopsgpt.api.app:app",
            host=args.host or settings.host,
            port=args.port or settings.port,
            reload=args.reload,
        )
        return 0

    if args.command == "investigate":
        return asyncio.run(_run_investigation(args))

    parser.print_help()
    return 1


async def _run_investigation(args) -> int:
    services = build_services()
    try:
        agent, _ = services.new_agent()
        async for step in agent.stream(args.question, earliest=args.earliest, latest=args.latest):
            if step.type is StepType.FINAL:
                report = step.tool_output
                _print_report(report)
            elif not args.quiet:
                _print_step(step)
    finally:
        await services.aclose()
    return 0


def _print_step(step) -> None:
    icon = {
        StepType.THOUGHT: "💭",
        StepType.TOOL_CALL: "🔧",
        StepType.TOOL_RESULT: "✅",
        StepType.ERROR: "❌",
    }.get(step.type, "•")
    print(f"  {icon} {step.summary}", file=sys.stderr)


def _print_report(report) -> None:
    print("\n" + "=" * 70)
    print(f"  INCIDENT REPORT  [{report.investigation_id}]")
    print("=" * 70)
    print(f"  Question   : {report.question}")
    print(f"  Severity   : {report.severity.value}   Confidence: {report.confidence:.0%}")
    print(f"  LLM/Splunk : {report.llm_provider} / {report.splunk_backend}")
    print(f"  Elapsed    : {report.elapsed_s:.1f}s")
    print("-" * 70)
    if report.root_cause:
        print("ROOT CAUSE\n" + report.root_cause + "\n")
    if report.suggested_fix:
        print("SUGGESTED FIX\n" + report.suggested_fix + "\n")
    if report.jira_ticket:
        tag = " (mock)" if report.jira_ticket.mocked else ""
        print(f"JIRA  : {report.jira_ticket.key}{tag}  {report.jira_ticket.url}")
    if report.pull_request:
        tag = " (mock)" if report.pull_request.mocked else ""
        print(f"PR    : #{report.pull_request.number}{tag}  {report.pull_request.url}")
    print("=" * 70)


if __name__ == "__main__":
    raise SystemExit(main())
