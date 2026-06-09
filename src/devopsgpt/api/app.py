"""FastAPI application factory.

Endpoints:
* ``GET  /``                  — minimal demo chat UI (static HTML).
* ``POST /investigate``       — run an investigation, return the full report.
* ``POST /investigate/stream``— Server-Sent Events: stream reasoning steps live,
                                 then a final ``report`` event.
* ``GET  /healthz``           — liveness (always 200 if the process is up).
* ``GET  /readyz``            — readiness (checks the Splunk backend).
* ``GET  /config``            — non-secret effective configuration (for the UI).

The long-lived :class:`Services` are built once at startup and closed on
shutdown via the lifespan context.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from importlib import resources

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from sse_starlette.sse import EventSourceResponse

from .. import __version__
from ..config import get_settings
from ..logging import configure_logging, get_logger
from ..models import IncidentReport, InvestigateRequest, StepType
from ..service import Services, build_services

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings)
    services = build_services(settings)
    app.state.services = services
    log.info("app.startup", version=__version__)
    try:
        yield
    finally:
        await services.aclose()
        log.info("app.shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)

    app = FastAPI(
        title="DevOpsGPT",
        version=__version__,
        summary="Autonomous engineering assistant on Splunk operational data.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def bind_request_id(request: Request, call_next):
        rid = request.headers.get("x-request-id") or _rand_id()
        structlog.contextvars.bind_contextvars(request_id=rid)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        response.headers["x-request-id"] = rid
        return response

    def services() -> Services:
        return app.state.services

    # ----- health --------------------------------------------------------
    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "version": __version__}

    @app.get("/readyz")
    async def readyz():
        svc = services()
        try:
            ready = await svc.splunk.health_check()
        except Exception:  # noqa: BLE001
            ready = False
        # The app is *usable* even if Splunk is down (mock fallback), so report
        # both: 200 always, with a backend-reachable flag.
        return {
            "status": "ready",
            "splunk_backend": svc.splunk.backend,
            "splunk_reachable": ready,
            "llm_provider": svc.provider.name,
        }

    @app.get("/config")
    async def config():
        s = services().settings
        return {
            "app_name": s.app_name,
            "llm_provider": services().provider.name,
            "splunk_mode": s.splunk_mode.value,
            "splunk_backend": services().splunk.backend,
            "jira_mode": s.effective_jira_mode().value,
            "github_mode": s.effective_github_mode().value,
            "write_actions_enabled": s.allow_write_actions,
            "version": __version__,
        }

    # ----- investigate (sync) -------------------------------------------
    @app.post("/investigate")
    async def investigate(req: InvestigateRequest):
        agent, _registry = services().new_agent()
        report = await agent.investigate(
            req.question, earliest=req.earliest, latest=req.latest
        )
        return JSONResponse(report.model_dump(mode="json"))

    # ----- investigate (SSE stream) -------------------------------------
    @app.post("/investigate/stream")
    async def investigate_stream(req: InvestigateRequest, request: Request):
        agent, _registry = services().new_agent()

        async def event_gen():
            async for step in agent.stream(
                req.question, earliest=req.earliest, latest=req.latest
            ):
                if await request.is_disconnected():
                    break
                if step.type is StepType.FINAL:
                    report = step.tool_output
                    payload = (
                        report.model_dump(mode="json")
                        if isinstance(report, IncidentReport)
                        else {"error": "investigation produced no report"}
                    )
                    yield {"event": "report", "data": json.dumps(payload, default=str)}
                else:
                    yield {
                        "event": "step",
                        "data": json.dumps(step.model_dump(mode="json"), default=str),
                    }
            yield {"event": "done", "data": "{}"}

        return EventSourceResponse(event_gen())

    # ----- demo UI -------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    async def index():
        return HTMLResponse(_load_index_html())

    return app


def _rand_id() -> str:
    # uuid4 hex without importing uuid at module top (keeps import light).
    import uuid

    return uuid.uuid4().hex[:12]


def _load_index_html() -> str:
    try:
        return resources.files("devopsgpt.api").joinpath("index.html").read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return "<h1>DevOpsGPT</h1><p>UI asset missing; use POST /investigate.</p>"


# Module-level app for `uvicorn devopsgpt.api.app:app`.
app = create_app()
