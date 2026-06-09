"""Structured logging setup using ``structlog``.

Pretty console output in dev; line-delimited JSON in prod (``DEVOPSGPT_LOG_JSON=true``).
A ``request_id`` / ``investigation_id`` can be bound via contextvars so every log
line within a request is correlated.
"""

from __future__ import annotations

import logging
import sys

import structlog

from .config import Settings

_configured = False


def configure_logging(settings: Settings) -> None:
    """Configure stdlib + structlog once for the process."""
    global _configured
    if _configured:
        return

    level = getattr(logging, settings.log_level, logging.INFO)

    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.log_json:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[*shared_processors, structlog.processors.format_exc_info, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging (uvicorn, httpx, etc.) through the same level.
    logging.basicConfig(level=level, stream=sys.stderr, format="%(message)s")
    for noisy in ("httpx", "httpcore", "anthropic", "openai"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))

    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger."""
    return structlog.get_logger(name)
