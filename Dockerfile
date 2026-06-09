# syntax=docker/dockerfile:1

# ---- builder: resolve deps with uv into a venv ----------------------------
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install dependencies first (cached layer), then the project.
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv venv /opt/venv && \
    VIRTUAL_ENV=/opt/venv uv pip install --no-cache .

# ---- runtime: slim image with just the venv + source ----------------------
FROM python:3.12-slim AS runtime

# Non-root user.
RUN useradd --create-home --uid 10001 appuser

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEVOPSGPT_HOST=0.0.0.0 \
    DEVOPSGPT_PORT=8000 \
    DEVOPSGPT_LOG_JSON=true

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app/src /app/src
COPY README.md LICENSE ./

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz').status==200 else 1)"

CMD ["uvicorn", "devopsgpt.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
