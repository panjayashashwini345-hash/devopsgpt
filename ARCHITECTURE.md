# DevOpsGPT — Architecture

DevOpsGPT is an **autonomous incident-investigation agent** that turns a plain-English
problem report into a root-cause analysis, a code fix, a Jira ticket, and a draft GitHub PR
— grounded in **Splunk** operational data and driven by a **pluggable LLM** through a
provider-neutral tool-calling loop.

This document covers the three things the hackathon requires:
1. **How the app interacts with Splunk** (MCP primary → REST fallback → mock).
2. **How AI models/agents are integrated** (provider abstraction + agent loop).
3. **Data flow** between services, APIs, and components.

---

## 1. System overview

```mermaid
flowchart TB
    User([👤 Engineer]) -->|"Checkout API is slow"| UI

    subgraph Edge["FastAPI service"]
        direction TB
        UI[Demo Chat UI<br/>SSE live steps]
        API["REST API<br/>POST /investigate<br/>POST /investigate/stream<br/>GET /healthz · /readyz · /config"]
        UI --> API
    end

    API --> Agent

    subgraph Core["Agent core (provider-neutral tool loop)"]
        direction TB
        Agent["Agent.stream()<br/>investigate → root cause →<br/>fix → ticket → PR"]
        Registry["Tool Registry<br/>(6 tools + write kill-switch)"]
        Agent <-->|"tool calls / results"| Registry
    end

    Agent <-->|"reasoning + tool-use"| LLM
    Registry --> Splunk
    Registry --> Actions

    subgraph LLM["LLM provider (pluggable)"]
        direction LR
        Claude[Claude<br/>Anthropic API]
        Hosted["Splunk Hosted Models<br/>gpt-oss-* (OpenAI-compat)"]
        MockLLM[Mock<br/>offline plan]
    end

    subgraph Splunk["Splunk client (auto-fallback)"]
        direction LR
        MCP["MCP Server<br/>(app 8047)"] --> REST["REST / SPL<br/>(:8089)"] --> MockS[Mock data]
    end

    subgraph Actions["Action adapters (live | mock)"]
        direction LR
        Jira["Jira REST<br/>create issue"]
        GitHub["GitHub REST<br/>open draft PR"]
    end

    Splunk -.->|logs · traces · deployments| SplunkPlatform[(Splunk Platform)]
    Jira -.-> JiraCloud[(Jira Cloud)]
    GitHub -.-> GitHubAPI[(GitHub)]
```

---

## 2. The investigation loop (data flow)

The agent runs a **provider-neutral tool-calling loop**. The same control flow drives Claude,
Splunk Hosted Models, or the offline mock — each provider only adapts the normalized
`ToolSpec` / `ToolCall` / `ToolResult` types to its native wire format.

```mermaid
sequenceDiagram
    autonumber
    participant U as Engineer
    participant A as Agent
    participant L as LLM Provider
    participant S as Splunk Client
    participant J as Jira
    participant G as GitHub

    U->>A: "Checkout API is slow"
    A->>L: start_conversation(system, tools)
    A->>L: send(question)

    loop until model stops requesting tools
        L-->>A: AssistantTurn(tool_calls)
        Note over A: stream THOUGHT + TOOL_CALL steps (SSE)
        A->>S: search_splunk_logs / search_traces
        S-->>A: SearchResult (errors, 5xx, latency)
        A->>S: correlate_deployments(service)
        S-->>A: recent deploys (v2.4.0 @ 13:55Z)
        A->>S: get_source_code(path)
        S-->>A: order.py (N+1 query) + proposed diff
        A->>J: create_jira_ticket(...)
        J-->>A: OPS-1234
        A->>G: create_github_pr(...)
        G-->>A: PR #42
        A->>L: submit_tool_results(results)
    end

    L-->>A: final Markdown (root cause + fix + severity)
    A-->>U: IncidentReport (SSE "report" event)
```

**Key:** the agent decides *which* tools to call and *when to stop* — it is not a fixed
script. The mock provider simulates a sensible plan so the loop is fully demonstrable offline.

---

## 3. Splunk interaction — capability discovery & graceful fallback

The Splunk client is the most resilient part of the system, by design (the exact Splunk MCP
Server tool names and deployment topology vary per environment).

```mermaid
flowchart TB
    Q[search SPL] --> Auto{SPLUNK_MODE}
    Auto -->|mcp / auto| TryMCP[MCP client]
    Auto -->|rest| TryREST
    Auto -->|mock| UseMock

    TryMCP -->|"connect() +<br/>list_tools()"| Discover{search-capable<br/>tool found?}
    Discover -->|yes| CallMCP["call_tool(name, args)<br/>normalize content blocks"]
    Discover -->|no / unreachable| TryREST[REST / SPL client]

    TryREST -->|"creds present?"| RESTmode{search mode}
    RESTmode -->|oneshot| OneShot["POST /services/search/jobs<br/>exec_mode=oneshot"]
    RESTmode -->|export| Export["POST .../jobs/export<br/>(streamed)"]
    RESTmode -->|async| Async["create → poll dispatchState → results"]
    TryREST -->|"no creds / error"| UseMock[Mock dataset]

    CallMCP --> Result[SearchResult]
    OneShot --> Result
    Export --> Result
    Async --> Result
    UseMock --> Result
```

**Design properties:**

- **Capability discovery, not hardcoding.** The MCP client calls `list_tools()` and maps the
  server's *real* tools to capabilities (search / deployments) by name+description hints, so
  it survives whatever Splunk MCP Server (app 8047) actually exposes. Resolved tools are
  logged once at startup.
- **One configurable auth builder** (`bearer` / `splunk` / `basic`) — `Authorization` header
  style is a single config switch (`SPLUNK_AUTH_SCHEME`), not branched throughout the code.
- **Never raises on empty/failed search** — errors surface as `SearchResult.error` so the
  agent reasons about partial failure instead of crashing.
- **`auto` mode** probes MCP → REST → mock on first use and caches the first healthy backend.

---

## 4. AI integration — provider abstraction

```mermaid
classDiagram
    class LLMProvider {
        <<protocol>>
        +name: str
        +start_conversation(system, tools) Conversation
        +aclose()
    }
    class Conversation {
        <<protocol>>
        +send(user_message) AssistantTurn
        +submit_tool_results(results) AssistantTurn
    }
    class ClaudeProvider {
        Anthropic Messages API
        native tool_use blocks
    }
    class SplunkHostedProvider {
        OpenAI-compatible /v1/chat/completions
        gpt-oss-120b / 20b / Foundation-Sec
    }
    class MockProvider {
        deterministic offline plan
    }

    LLMProvider <|.. ClaudeProvider
    LLMProvider <|.. SplunkHostedProvider
    LLMProvider <|.. MockProvider
    LLMProvider ..> Conversation : creates
```

The agent depends only on the `LLMProvider` / `Conversation` protocols and the normalized
tool types (`ToolSpec`, `ToolCall`, `ToolResult`, `AssistantTurn`). Swapping the brain is a
single env var (`DEVOPSGPT_LLM_PROVIDER`), and a failed provider init degrades to mock rather
than crashing startup.

---

## 5. Component / module map

| Layer | Module | Responsibility |
| --- | --- | --- |
| **API** | `api/app.py`, `api/index.html` | FastAPI app, SSE streaming, health, demo UI |
| **Agent** | `agent.py`, `prompts.py` | The tool-calling investigation loop + report synthesis |
| **Tools** | `tools.py` | Bridges LLM tool calls ↔ Splunk/Jira/GitHub; write kill-switch |
| **LLM** | `llm/{base,claude,hosted,mock,factory}.py` | Provider abstraction |
| **Splunk** | `splunk/{base,mcp_client,rest_client,mock_client,factory,auth}.py` | Search backends + auto-fallback |
| **Actions** | `integrations/{jira,github}.py` | Live + mock ticket/PR adapters |
| **Core** | `config.py`, `models.py`, `logging.py`, `service.py` | Settings, domain models, structured logs, DI assembly |
| **Entry** | `cli.py` | `devopsgpt serve` / `investigate` |

---

## 6. Resilience & safety summary

| Concern | Mechanism |
| --- | --- |
| Unconfigured environment | Boots fully mocked (Splunk + LLM + Jira + GitHub); never hard-fails |
| Flaky Splunk MCP server | Auto-falls back to REST, then mock |
| Missing live creds with `*_MODE=live` | Adapter degrades to mock + logs a warning |
| Runaway agent | `max_agent_iterations` + `agent_timeout_s` bounds |
| Accidental writes during a demo | `DEVOPSGPT_ALLOW_WRITE_ACTIONS=false` plans actions without executing |
| Per-request state leakage | Fresh tool registry + agent per investigation; clients shared |
| Secret leakage | `/config` exposes only non-secret effective settings |
