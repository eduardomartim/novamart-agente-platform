# NovaMart

**A demonstration platform for governed multi-agent AI: specialised agents answer
business questions and propose actions, and a deterministic control plane —
policy engine, tool gateway, human approval and an audit trail — decides what
actually runs.**

The AI features run on **Google Gemini** through the official `google-genai` SDK.
Every number on the dashboard comes from **HDstore**, a fictional electronics
retailer. *HDstore is a fictional demonstration tenant and does not represent a
real customer;* all of its customers, orders and tickets are synthetic.

Python 3.12 · LangGraph · Google Gemini · Streamlit · Starlette · PostgreSQL ·
Redis · MCP · Docker · MIT licence

---

## Overview

Letting a language model call tools is easy; deciding *which* calls are allowed,
*who* must approve the risky ones and *what happened* afterwards is the hard part.
NovaMart is built around that second problem.

- **Specialised agents** split the work: a router classifies the request, a
  researcher gathers read-only facts, an answerer writes the reply, an executor
  proposes actions, and a validator checks the outcome.
- **The model never holds authority.** Agents only *propose* tool calls. A tool
  gateway is the single path to every tool, and it asks a deterministic policy
  engine before anything runs.
- **Sensitive actions stop for a person.** A write or a message is suspended
  until a human approves or declines exactly the action that was shown.
- **Everything is recorded.** Each step writes a sanitised, ordered event, which
  the dashboard, the HTTP API and the metrics endpoint all read.
- **The data is enterprise-shaped but simulated.** Nineteen tools — record
  lookups, analytics and three write/delete/message tools — operate on an
  in-memory HDstore dataset. No external system is ever contacted.

---

## Architecture

### Request flow

```
            user question
                  │
                  ▼
              Router  ── classifies: read-only question, action, or out of scope
            ┌─────┴──────────────┐
            ▼                    ▼
       Researcher            Executor ── proposes one action (tool + arguments)
            │                    │
            └────────┬───────────┘
                     ▼
               Tool Gateway ── the only path to a tool
                     │   asks
                     ▼
              Policy Engine ── 11 deterministic rules (PL001–PL011)
          ALLOW │  REQUIRE_CONFIRMATION │  DENY
                ▼             ▼            ▼
           tool runs   human approval   refused, recorded
                │       (suspend/resume)
                ▼
     Tool handler ── simulated HDstore data (in-process, or an MCP subprocess)
                │
                ▼
     Answerer / Validator ──► response

  every step ──► audit trail (sanitised events) ──► dashboard · API · /metrics
```

### Layers

| Layer | Responsibility | Where |
|---|---|---|
| Orchestration | LangGraph state machine: route, research, answer, execute, confirm, validate, respond | `orchestration/` |
| Agents | Router, Researcher, Executor, Validator, Answerer — prompts and structured decisions | `agent/` |
| Tool Gateway | Single execution path; every tool refuses to run outside it (`require_gateway`) | `tools/gateway.py`, `tools/execution.py` |
| Policy Engine | Risk-based ALLOW / REQUIRE_CONFIRMATION / DENY; no model is consulted | `guardrails/` |
| Authorization | Per-tool allow-list **and** an agent capability matrix; both must pass | `guardrails/authorization.py` |
| Human-in-the-loop | Graph `interrupt()`; resume is bound to the action's fingerprint | `platform.py`, `state/pending.py` |
| Audit trail | Ordered, sanitised events per request; cost ledger per provider | `observability/`, `persistence/`, `cost/` |
| Retrieval (RAG) | BM25 over SQLite FTS5 offline; BM25 + Gemini embeddings when live | `retrieval/` |
| Execution boundary | Optional MCP server process; each call carries a signed, single-use grant | `execution/`, `mcp_server/` |
| Interfaces | Streamlit dashboard (in-process), Starlette HTTP API, CLI | `dashboard/`, `api/`, `cli.py` |

---

## AI architecture

- **Provider.** `GeminiProvider` uses the official `google-genai` SDK (default
  model `gemini-3.5-flash-lite`, embeddings `gemini-embedding-001`). The key is
  read from the environment (`GEMINI_API_KEY`) and is never stored in the
  repository, placed in a prompt or written to a trace.
- **Routing and tool selection.** The router and the agents return structured,
  schema-validated decisions; agents are offered only the tools their role may use.
- **Context.** Retrieved records and documents are fenced as untrusted content,
  bounded in size, and checked for injection before they reach a prompt.
- **Answers.** The answerer only produces a grounded answer when it can cite what
  was retrieved; otherwise it says what it could not establish.
- **Sensitive actions.** A model can propose `update_record` or `send_email`, but
  the policy engine suspends it for human confirmation; `delete_record` is refused
  for every agent.
- **No key, no problem.** Without a key the platform runs a deterministic stub
  provider and says so in the UI, the CLI and every trace. Routing, the policy
  engine, the gateway, confirmation, the audit trail and retrieval all work
  offline; what changes is who writes the decisions and the prose.
- **Spending is opt-in.** A key selects Gemini but does not authorise using it:
  real calls also require `AGENT_PLATFORM_LIVE` in the environment of that one
  command (it is deliberately not read from `.env`), plus per-request and daily
  call budgets.

---

## Security & governance

Concrete controls, each covered by tests (see [threat model](docs/threat-model.md)):

- **Gateway enforcement** — every tool handler refuses a call that did not come
  through the gateway, so there is no side door from an agent to a tool.
- **Policy enforcement** — eleven deterministic rules over tool risk, arguments,
  input signals, budgets and rate limits. No security decision consults a model.
- **Tool permissions** — two independent gates (tool allow-list and capability
  matrix); the router and answerer hold no tools, and no role holds `delete`.
- **Confirmation flow** — approval is tied to a SHA-256 fingerprint of the exact
  tool and arguments, consumed atomically (a replayed approval fails), expires
  after 15 minutes, and each caller may hold only a share of pending approvals.
- **Replay protection on the execution boundary** — with `TOOL_TRANSPORT=mcp`,
  each tool call carries an HMAC-signed grant bound to the tool and arguments,
  valid for 30 seconds and usable once.
- **Audit trail** — every request writes ordered events (decision, tool, risk,
  rule ids, latency), including after a human confirmation resumes it.
- **Secret redaction** — credential patterns (API keys, bearer tokens, private
  keys, passwords inside connection strings) and the exact values of every
  configured credential are stripped from traces, logs and error messages.
- **Request limits** — HTTP bodies are capped while streaming (`413` above the
  limit); questions are capped at 100 characters; per-request ceilings on model
  calls, tool calls, output size and wall-clock time.
- **Rate limiting** — per caller (API principal or dashboard visitor), shared
  across replicas through Redis when configured.
- **Session isolation of demo data** — each dashboard visitor gets a private copy
  of the simulated records, so an approved update never changes what another
  visitor sees.
- **API authentication** — scoped bearer credentials stored as SHA-256 digests
  (`runs:write`, `confirm:write`, `metrics:read`); the API refuses to start
  without them.
- **Credential handling** — `.env` is git-ignored, `.env.example` carries only
  placeholders, and CI verifies a baseline of protected security files.

These are defences for a demonstration platform, not a certification; the
[threat model](docs/threat-model.md) lists the residual and accepted risks.

---

## Live architecture demo

The dashboard's landing page animates the architecture through ten scripted
workflows — refund, critical stock, support ticket, sales analysis, order
processing, financial operation, campaign, delivery problem, business query and
a blocked operation — showing work delegated to specialised agents, the policy
engine deciding, a human approval when required, the gateway executing and the
validator checking, with a live trace and the outcome of each scenario.

It is a **representation**, labelled as such: the scenes are scripted and make no
calls. The specialised agents drawn there (Support, Finance, Marketing…)
illustrate specialisation; the backend's five real agents are listed on the
Architecture page, and real requests run on the **Orchestrator** page against the
actual graph. The scene adapts to the screen: diagram beside the trace on wide
screens, full-width diagram on medium ones, and a vertical, readable flow on
phones.

The dashboard has six pages — Overview, Company (the HDstore data), Orchestrator,
Security, Architecture and Observability — in Portuguese and English.

---

## Tech stack

| Area | Technologies |
|---|---|
| Language | Python 3.12 |
| AI | Google Gemini (`google-genai` SDK), LangGraph |
| Validation | Pydantic |
| Interfaces | Streamlit + Altair (dashboard), Starlette + Uvicorn (HTTP API), CLI |
| Storage | SQLite (default, FTS5 for retrieval), PostgreSQL (optional, `psycopg`) |
| Shared state | Redis 8 / Redis Stack (optional, multi-replica) |
| Execution boundary | Model Context Protocol (MCP) Python SDK |
| Observability | Structured JSON logs, Prometheus exposition format |
| Containers | Docker, Docker Compose, Kubernetes manifests |
| Deployment manifests | Railway (dashboard), Vercel (API) |
| Quality | pytest, ruff, mypy, Playwright (layout checks), GitHub Actions |

---

## Project structure

```
.
├── src/agent_platform/
│   ├── agent/           router, researcher, executor, validator, answerer
│   ├── orchestration/   LangGraph graph, typed state, routing
│   ├── guardrails/      policy engine, rules, authorization, input/output/egress checks
│   ├── tools/           registry, gateway, simulated and analytics tools, dataset
│   ├── retrieval/       BM25 (FTS5), vector index, hybrid fusion
│   ├── llm/             Gemini provider, deterministic stub, live gate, circuit breaker
│   ├── security/        secrets, PII, sanitisation, rate limits, resource limits, API auth
│   ├── observability/   events, tracing, metrics, logging
│   ├── persistence/     SQLite, PostgreSQL, in-memory repositories
│   ├── state/           Redis-backed shared state
│   ├── execution/       MCP transport, signed grants
│   ├── mcp_server/      MCP server for isolated tool execution
│   ├── evaluation/      evaluator and golden datasets
│   ├── api/             HTTP API
│   └── platform.py      composition root
├── dashboard/           Streamlit app, PT/EN catalogues, live architecture scene
├── api/                 Vercel entrypoint
├── tests/               unit, integration, security, live, visual
├── docs/                architecture, threat model, API, deploy, evaluation, …
├── k8s/                 Kubernetes manifests
├── scripts/             CI and maintenance scripts
├── data/kb_vectors.db   vector index of the fictional knowledge base
├── Dockerfile           API image
├── Dockerfile.dashboard dashboard image
├── compose.yaml         local API + Redis + PostgreSQL
├── railway.toml         dashboard deployment manifest
└── vercel.json          API deployment manifest
```

---

## Getting started

Requires Python 3.12 (`>=3.11` is accepted).

```bash
git clone <this-repository-url>
cd <repository-folder>
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dashboard]"
cp .env.example .env
streamlit run dashboard/app.py
```

Open <http://localhost:8501>. With no key configured, everything runs offline on
the deterministic stub.

### Using Google Gemini

1. Create a key at <https://aistudio.google.com/apikey>.
2. Put it in your local `.env` (never commit it):

   ```
   GEMINI_API_KEY=your_gemini_api_key_here
   ```

3. Authorise real calls for the command you run — this is intentionally not
   read from `.env`:

   ```bash
   AGENT_PLATFORM_LIVE=i-authorise-real-provider-calls streamlit run dashboard/app.py
   ```

   PowerShell: `$env:AGENT_PLATFORM_LIVE="i-authorise-real-provider-calls"`, then
   `streamlit run dashboard/app.py`.

### Other entry points

```bash
pip install -e ".[dev,dashboard,api,redis,mcp,postgres]"   # everything CI installs
agent-platform demo                                         # seed representative traffic
agent-platform ask "What is the status of order ORD-1001?"
agent-platform eval                                         # offline evaluation
```

The HTTP API needs a credential before it starts — see [docs/api.md](docs/api.md).

---

## Environment variables

All optional for the offline demo. Values below are placeholders.

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY=your_gemini_api_key_here` | Enables the Gemini provider |
| `GEMINI_MODEL=gemini-3.5-flash-lite` | Model override |
| `AGENT_PLATFORM_LIVE` | Authorises real provider calls; set per command, never in `.env` |
| `DATABASE_PATH=data/agent_platform.db` | Local SQLite file |
| `DATABASE_URL=postgresql://user:password@host:5432/db` | Use PostgreSQL instead of SQLite |
| `REDIS_URL=redis://host:6379/0` | Share limits, approvals and checkpoints across replicas (Redis 8+/Stack) |
| `API_AUTH_KEYS` | API credential table (create with `agent-platform auth new-key`) |
| `TOOL_TRANSPORT=mcp` + `EXECUTION_GRANT_SECRET` | Run tools behind the MCP boundary |
| `VISITOR_ID_SALT` | Salt for the dashboard's per-visitor quota key |

The complete list, with defaults, is in [`.env.example`](.env.example).

---

## Testing

| Check | Result |
|---|---|
| Offline test suite | 2,218 tests selected (2,243 collected; 25 live tests deselected by design): 2,166 passed, 51 skipped (50 because Docker, Redis or PostgreSQL were unavailable on the test machine, 1 by design); the CI workflow provides those services |
| Security tests | 844 adversarial tests (prompt injection, policy bypass, gateway bypass, confirmation replay, secret leakage, abuse limits, API auth, …) |
| Offline evaluation | 68/68 cases passed · safety 1.0000 · tool accuracy 1.0000 · correctness 0.9875 |
| Layout check | 6/6 viewports (375 → 1440 px): no horizontal scroll, no overlap, readable text, stable live-demo height |
| Lint | ruff clean |

The offline evaluation measures the platform against a deterministic stub; it says
nothing about a language model's quality. Live tests exist (`pytest -m live`) and
need both a key and the live authorisation.

```bash
pytest                       # offline suite
npm run visual               # browser layout check against a running dashboard
```

---

## Deployment

Two independent services from one repository — **neither is deployed yet**:

- **Dashboard → Railway**, built from `Dockerfile.dashboard` (selected in
  `railway.toml`), health check `/_stcore/health`.
- **API → Vercel**, serving `api/index.py`; `.vercelignore` limits the upload to
  the API's own files.
- **Docker / Compose / Kubernetes** for running the API with Redis and PostgreSQL
  locally.

Details, variables and caveats: [docs/deploy.md](docs/deploy.md).

---

## Engineering decisions

- **Separation of proposal and authority.** Models propose; deterministic code
  decides. Changing the model cannot change what the platform permits.
- **One gateway.** A single execution path makes "every tool call was checked"
  a structural property instead of a convention.
- **Policy as data.** Rules are enumerable, ordered and printed by
  `agent-platform rules`, so a reviewer can read exactly what is enforced.
- **Approval bound to the action.** A confirmation authorises one fingerprinted
  tool call, once — not "whatever the agent does next".
- **Auditability first.** Events are sanitised before storage and ordered per
  request, so the trace can be shown to anyone who can see the dashboard.
- **Fail closed.** An unreachable rate limiter, budget ledger or credential
  configuration refuses requests instead of silently degrading.
- **Offline by default.** The full control plane is exercisable without a key,
  which keeps the test suite deterministic and free.

---

## Limitations

- A portfolio and demonstration project: the tools are simulated and the HDstore
  data is fictional.
- Single-tenant: API scopes separate roles, not organisations; there are no end
  users, directory or tenant isolation beyond per-visitor demo data.
- Without Redis, rate limits and pending approvals are per process; PostgreSQL
  migrations are not implemented (the schema is created on connect).
- Hybrid retrieval and live answers need a Gemini key; the offline evaluation
  measures the platform, not a model.
- The in-process tool timeout cannot cancel a running Python thread; the MCP
  transport supports stdio only.
- Not yet deployed; the CI workflow is defined but has not run on GitHub.

---

## Further reading

[Technical reference](docs/technical-reference.md) ·
[architecture](docs/architecture.md) · [threat model](docs/threat-model.md) ·
[API](docs/api.md) · [deploy](docs/deploy.md) · [evaluation](docs/evaluation.md) ·
[execution boundary](docs/execution-boundary.md) · [state](docs/state.md) ·
[audit report](docs/audit-report.md)

## License

MIT — see [LICENSE](LICENSE).

## Author

Eduardo Martim
