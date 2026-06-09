# 🛠️ DevOpsGPT

> **An autonomous engineering assistant that analyzes Splunk logs, traces, and deployments to identify root causes, recommend fixes, and accelerate incident resolution.**

Built for the **Splunk Agentic Ops Hackathon** — tracks: **Observability** & **Platform / Developer Experience**.

Ask in plain English — *"Checkout API is slow"* — and DevOpsGPT runs an autonomous investigation: it searches Splunk logs and traces, correlates the onset with recent deployments, reads the implicated source code, pinpoints the root cause, proposes a code fix, then **files a Jira ticket and opens a draft GitHub PR**.

```
"Checkout API is slow"
   → searches Splunk logs (errors, 5xx, latency)
   → inspects distributed traces (where time is spent)
   → correlates with deploy v2.4.0 (commit 9f3c1a2)
   → reads order.py → finds N+1 query
   → root cause + fix → Jira ticket + draft PR
```

---

## ✨ Why it matters

Developers lose hours jumping between logs, traces, dashboards, deploy history, and source to debug production issues. DevOpsGPT collapses that loop into a single agentic workflow — **reducing debugging time from hours to minutes.**

## 🧠 How AI is used

A **provider-agnostic tool-calling agent** drives the whole investigation. The same loop runs on:

| Provider | Use |
| --- | --- |
| **Claude** (Anthropic) | Best agentic tool-use / reasoning |
| **Splunk Hosted Models** (`gpt-oss-120b` / `gpt-oss-20b` / `Foundation-Sec`) | OpenAI-compatible, on-platform inference |
| **Mock** | Deterministic, offline — drives the demo with zero keys |

The agent reaches Splunk through the **Splunk MCP Server** (Model Context Protocol) as the primary transport, with a **direct REST/SPL** fallback and a bundled **mock** backend so it always runs.

---

## 🏗️ Architecture

See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for the full diagram and data flow.

```
            ┌─────────────┐      ┌──────────────────────────────────────┐
  Browser   │  FastAPI    │      │              Agent core              │
  (SSE UI) ─┼─ /investigate ─────┼─ provider-neutral tool-calling loop   │
            │  /stream    │      │  investigate → root cause → fix →     │
            └─────────────┘      │  Jira ticket → GitHub PR              │
                                 └───────┬───────────────┬──────────────┘
                                         │ tools         │ LLM
                         ┌───────────────▼──┐     ┌──────▼─────────────────┐
                         │ Splunk client    │     │ LLM provider           │
                         │ MCP → REST → mock │     │ Claude | Hosted | Mock │
                         └───────────────────┘     └────────────────────────┘
                                         │ actions
                         ┌───────────────▼──────────────┐
                         │ Jira (REST) │ GitHub (REST)   │  (live or mock)
                         └──────────────────────────────┘
```

**Key design choices (all driven by resilience):**

- **Capability discovery, not hardcoding.** The MCP client calls `list_tools()` at runtime and maps the server's real tools to capabilities — so it survives whatever the Splunk MCP Server actually exposes.
- **Nothing hard-fails when unconfigured.** With an empty `.env`, the app boots fully mocked (Splunk + LLM + Jira + GitHub) and runs the canonical demo offline.
- **One configurable auth builder** (`bearer` / `splunk` / `basic`) for Splunk.
- **Graceful degradation everywhere** — a flaky MCP server falls back to REST; a failed live Jira/GitHub call falls back to a mock result with the error noted, so one integration never aborts an investigation.
- **Global write kill-switch** (`DEVOPSGPT_ALLOW_WRITE_ACTIONS=false`) — the agent plans ticket/PR payloads without calling out. Safe demos.

---

## 🚀 Quickstart (zero config, fully offline)

Requires **Python ≥ 3.11** and [`uv`](https://docs.astral.sh/uv/).

```bash
# 1. install
uv venv && uv pip install -e ".[dev]"

# 2. run a one-shot investigation in the terminal (no server, no keys)
uv run devopsgpt investigate "Checkout API is slow"

# 3. or run the web UI + API
uv run devopsgpt serve
#   → open http://localhost:8000  and click an example
```

Out of the box `DEVOPSGPT_LLM_PROVIDER=mock` and `SPLUNK_MODE=auto` (which falls back to the bundled dataset), so you get the full investigate → root-cause → ticket → PR loop with **no credentials**.

### Run with Docker

```bash
docker compose up --build
# → http://localhost:8000
```

---

## ⚙️ Configuration

Copy `.env.example` to `.env` and set only what you need. Everything is optional.

### Switch on Claude

```dotenv
DEVOPSGPT_LLM_PROVIDER=claude
ANTHROPIC_API_KEY=sk-ant-...
DEVOPSGPT_CLAUDE_MODEL=claude-opus-4-8
```

### Switch on Splunk Hosted Models

```dotenv
DEVOPSGPT_LLM_PROVIDER=splunk-hosted
HOSTED_MODELS_BASE_URL=https://<your-splunk-hosted-models-endpoint>
HOSTED_MODELS_API_KEY=...
HOSTED_MODELS_DEFAULT_MODEL=gpt-oss-120b
```

### Connect to real Splunk (MCP primary, REST fallback)

```dotenv
SPLUNK_MODE=auto                 # auto | mcp | rest | mock
SPLUNK_HOST=your-splunk-host
SPLUNK_MGMT_PORT=8089            # management port, NOT the 8000 web UI
SPLUNK_AUTH_SCHEME=bearer        # bearer | splunk | basic
SPLUNK_TOKEN=<your-splunk-token>
SPLUNK_VERIFY_SSL=false          # for self-signed lab certs

# Splunk MCP Server (Splunkbase app 8047)
MCP_TRANSPORT=stdio              # stdio | sse | http
MCP_SERVER_COMMAND=splunk-mcp-server
MCP_SERVER_ARGS=--some,--args
# or, for a running server:
# MCP_TRANSPORT=sse
# MCP_SERVER_URL=http://localhost:8050/sse
```

> The exact tool names / launch command of the Splunk MCP Server are environment-specific. DevOpsGPT discovers tools at runtime, so you only need to point it at the server — it logs the resolved tools at startup.

### Enable real Jira & GitHub actions

```dotenv
DEVOPSGPT_ALLOW_WRITE_ACTIONS=true

JIRA_MODE=live
JIRA_BASE_URL=https://your-org.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=...
JIRA_PROJECT_KEY=OPS

GITHUB_MODE=live
GITHUB_TOKEN=ghp_...
GITHUB_REPO=your-org/checkout-service
```

If `*_MODE=live` but credentials are missing, the adapter automatically degrades to mock and logs a warning — it never crashes.

---

## 🔌 API

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/investigate` | Run an investigation, return the full `IncidentReport` JSON. |
| `POST` | `/investigate/stream` | Server-Sent Events: live reasoning steps, then a final `report` event. |
| `GET` | `/healthz` | Liveness. |
| `GET` | `/readyz` | Readiness + which Splunk backend/LLM resolved. |
| `GET` | `/config` | Non-secret effective configuration. |
| `GET` | `/` | Demo chat UI. |

```bash
curl -s localhost:8000/investigate \
  -H 'content-type: application/json' \
  -d '{"question":"Checkout API is slow"}' | jq
```

---

## 🧪 Tests

```bash
uv run pytest            # full suite, runs entirely offline against mocks
uv run ruff check .      # lint
uv run mypy src          # types
```

---

## 📁 Project layout

```
src/devopsgpt/
├── config.py            # typed settings; every uncertainty is an env knob
├── logging.py           # structlog setup
├── models.py            # shared domain models (SearchResult, IncidentReport, …)
├── prompts.py           # agent system prompt
├── agent.py             # the investigate→fix→ticket→PR tool-calling loop
├── tools.py             # tool registry bridging LLM ↔ Splunk/Jira/GitHub
├── service.py           # dependency assembly + lifecycle
├── cli.py               # `devopsgpt serve` / `investigate`
├── llm/                 # provider abstraction: claude, hosted, mock
├── splunk/              # client abstraction: mcp, rest, mock + auto-fallback
└── api/                 # FastAPI app + SSE + demo UI
tests/                   # offline test suite
```

---

## 📜 License

[Apache-2.0](./LICENSE).

> Splunk, the Splunk logo, and related marks are trademarks of Splunk LLC. This project is an independent hackathon entry and is not affiliated with or endorsed by Splunk.
