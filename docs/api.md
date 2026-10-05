# HTTP API

A transport in front of the existing platform. It adds no authority: every route
ends in a call to `AgentPlatform`, the same object the CLI drives, and there is
no path from HTTP to a tool that does not pass the policy engine and the
gateway.

## Running it

Every way of starting the API needs a credential first: with none configured it
**refuses to start** rather than serve unauthenticated (see *Authentication*).
Mint one -- the token is printed once and not stored -- and export the
configuration line it prints:

```bash
agent-platform auth new-key --principal local-dev --scope runs:write --scope confirm:write
export API_AUTH_KEYS="<the line it printed>"
```

### In a container

```bash
docker compose up --build
```

Serves on `http://localhost:8000`, offline, against the deterministic stub;
`compose.yaml` passes `API_AUTH_KEYS` through and refuses to start without it.
Or without compose:

```bash
docker build -t agent-platform:v2.6 .
docker run --rm -p 8000:8000 -e API_AUTH_KEYS agent-platform:v2.6
```

### From a checkout

```bash
pip install -e ".[api]"
python -m agent_platform.api
```

Running without authentication is possible and has to be typed out:
`API_AUTH_MODE=disabled`.

Listens on `127.0.0.1:8000`. `API_HOST` and `API_PORT` override that; everything
else is read by `Settings.from_env()` exactly as the CLI reads it. With no
`GEMINI_API_KEY` configured the API runs the deterministic stub, offline.

## The image

| | |
|---|---|
| Base | `python:3.12-slim-trixie` |
| Size | ~88 MB |
| User | `10001:10001`, non-root, no login shell |
| Entrypoint | `python -m agent_platform.api` |
| Port | `8000` |
| Writable path | `/var/lib/agent-platform` — nothing else |

Two stages. The builder turns the source tree into a wheel; the runtime installs
that wheel plus a hashed dependency set and carries no toolchain, no package
index client, and no source tree. The package is **installed, not copied**: a
copied checkout would hide packaging mistakes — a data file missing from the
wheel, a module outside the package — until some later environment had no
checkout to fall back on.

Dependencies come from `requirements-api.txt`, a fully hashed lock generated
with `uv pip compile` and resolved for `linux/x86_64`. Version ranges in
`pyproject.toml` describe what the project is *compatible* with; the lock is what
the image actually installs, so two builds a month apart contain the same code.
Regenerate it with:

```bash
uv pip compile pyproject.toml --extra api --extra redis --python-version 3.12 --python-platform x86_64-unknown-linux-gnu --generate-hashes -o requirements-api.txt
```

### Environment

| Variable | Default in image | Meaning |
|---|---|---|
| `API_HOST` | `0.0.0.0` | Bind address. `127.0.0.1` outside a container. |
| `API_PORT` | `8000` | Listen port. |
| `DATABASE_PATH` | `/var/lib/agent-platform/agent_platform.db` | Absolute, so SQLite cannot land somewhere unexpected if the working directory moves. |
| `KB_VECTOR_INDEX_PATH` | `/opt/agent-platform/kb_vectors.db` | The persisted vector index. |
| `GEMINI_API_KEY` | *unset* | Its absence selects the stub. |
| `LOG_LEVEL` | `info` | |
| `REDIS_URL` | *unset* | Empty means process-local state. Set it for shared state across replicas — see [state.md](state.md). Needs Redis 8 or Redis Stack. |

### STUB and LIVE

The image ships **no credential**, and that absence is what selects the
deterministic stub — so `docker compose up` is fully offline, calls no provider
and spends no quota.

`compose.yaml` pins `GEMINI_API_KEY: ""` rather than passing one through. That is
deliberate and was learned the hard way: an earlier version wrote
`${GEMINI_API_KEY:-}` meaning "only if the operator exported one", and Compose —
which reads the project's `.env` automatically for substitution — filled it in
from the developer's dotfile and started in LIVE mode against real quota. Pinning
it empty makes offline a property rather than an intention, and
`test_compose_cannot_be_talked_into_live_mode_by_a_dotfile` keeps it that way.

To use the real provider, do it visibly and outside compose, so that spending
quota is always an explicit act:

```bash
docker run --rm -p 8000:8000 -e GEMINI_API_KEY=... agent-platform:v2.1
```

The vector index is baked into the image because hybrid retrieval needs it. Demo
traffic never reads it — stub mode is BM25 over FTS5 — but an image lacking it
would fail only once a real provider was configured, which is the worst moment
to discover a missing file.

### Security posture

Verified in `tests/integration/test_container.py`, against a running container
rather than against the Dockerfile text:

- runs as uid `10001`; `site-packages` and `/opt/agent-platform` are not writable by it
- no `.env` anywhere in the image, and the developer's real key does not appear in the exported filesystem
- the vector index is `0444` and its directory `0755` — readable, never writable
- `compose.yaml` adds `read_only: true`, `cap_drop: ALL`, and `no-new-privileges`

**Vulnerability scan** (`docker scout`, base `python:3.12-slim-trixie`): 4 findings
remain — 2 CRITICAL, 2 HIGH — **all in `perl-base`, none in this project's Python
dependencies**. Debian publishes no fixed version, `docker scout recommendations`
reports the base image is already current, and `perl-base` is marked
`Essential: yes` so dpkg refuses to remove it. Forcing it out to lower a number
would trade real fragility for a cosmetic win, so these are recorded as accepted
rather than fixed. Three OpenSSL HIGH findings *were* fixed, by upgrading only
the packages with an available patch.

## Authentication

Every request that can spend money, execute a tool or approve an action carries
a credential. The API **refuses to start** without one configured -- the same
rule an unreachable `REDIS_URL` follows. A control that switches itself off when
its configuration is missing is off on the day it matters, and nothing in the
logs says so.

```
Authorization: Bearer ap_<key id>_<secret>
```

Mint one; the token is printed once and is not stored anywhere:

```bash
agent-platform auth new-key --principal ops-oncall --scope confirm:write
```

What goes into configuration is the line it prints alongside: a key id, a
principal, its scopes, and the **sha256 of the secret**. Nothing that
authenticates anybody is ever stored by the platform, committed, or baked into
an image.

### Why SHA-256 rather than argon2

Slow hashes exist to make brute force expensive against low-entropy secrets that
humans chose. These are 256 bits from `secrets`; no hash speed brings them into
reach. A KDF would add a dependency and put ~100ms of CPU behind every
*unauthenticated* request, which is a denial-of-service vector installed in the
name of security.

That argument holds only because the entropy is ours, so a secret shorter than
32 characters is refused at load time rather than accepted and reasoned about as
though the assumption applied to it.

### Scopes

| Scope | Grants |
|---|---|
| `runs:write` | Submit a request |
| `confirm:write` | Approve or decline a suspended action |
| `metrics:read` | Scrape `/metrics`, and see `/ready` in detail |

**None implies another, and there is no `admin` scope.** The moment one exists
every deployment uses it and the separation becomes decorative. Separating who
may *request* a high-risk action from who may *approve* it is the property this
platform exists to make executable; an identity that genuinely needs both
carries both, written on one line where a reviewer sees it.

### Who approved it

`POST /runs/{id}/confirm` used to accept an `actor` field in the body. It was
written into the `CONFIRMATION_RESOLVED` audit event beside the action
fingerprint, and nothing checked it -- so the record of which human approved a
high-risk action was a string the caller typed. That is worse than no record: an
absent trail is known to be absent.

**The field is gone. Sending it is now a 400.** The approver comes from the
credential. The `source` field, which already existed, is what tells a reader
how much the actor is worth: `api` is attested, `cli` is the local operator's
word.

### Refusals

| Situation | Status |
|---|---|
| No credential, or any bad credential | `401` + `WWW-Authenticate: Bearer` |
| Valid credential without the scope | `403` |
| Unknown path, no credential | `401` |
| Unknown path, valid credential | `403` |

Every `401` returns the same body whatever caused it. A verifier that explains
which check failed helps somebody iterate towards a working credential, and the
caller can do nothing differently for one cause versus another. The reason is
logged and counted as `agent_auth_refusals_total{reason}` for the operator; it
does not cross the network. Because an anonymous caller gets `401` for paths
that exist *and* paths that do not, the API cannot be mapped without a
credential.

Only the `Authorization` header is read. There is no query-parameter form and
there will not be one: URLs reach access logs and proxies.

### What this does not fix

**The API itself does not terminate TLS.** A bearer token over plain HTTP is a
token in the clear, so TLS belongs at the edge: the local Kubernetes setup
terminates it at an Ingress (see [kubernetes.md](kubernetes.md#tls-locally)),
and Vercel terminates it for the serverless deployment
([deploy.md](deploy.md)). Run bare, on a published port, it is plain HTTP. The platform also remains
single-tenant: scopes are not tenants, and budget and data stay global.

Credentials are read at start-up, so rotation is add-then-remove followed by a
rolling restart.

## Endpoints

| Method | Path | Scope |
|---|---|---|
| `GET` | `/health` | public |
| `GET` | `/ready` | public; detail needs `metrics:read` |
| `GET` | `/metrics` | `metrics:read` |
| `POST` | `/runs` | `runs:write` |
| `POST` | `/runs/{request_id}/confirm` | `confirm:write` |

`/health` and `/ready` stay public because a kubelet probe sends no credential.
`/ready` answers an anonymous caller with the verdict and the auth mode only:
the provider name and the circuit state are free reconnaissance otherwise, and
a probe needs neither.

The route table and the middleware's path matcher are built from **one**
literal, so a route cannot exist without a declared scope -- adding one without
deciding is a `TypeError` at import. This matters because Starlette runs
middleware *before* routing, so the matcher cannot ask which route is about to
handle a request and has to resolve the path itself. An unresolved path is
denied rather than waved through; treating it as unprotected would be a bypass,
and `POST /runs/x/confirm/` -- which Starlette answers with a 307 to the
canonical path -- is exactly the shape it would take.

There is deliberately no `GET /runs/{id}`. Execution is synchronous, so the
result is returned by `POST /runs` itself; a read endpoint would only re-serve
state the caller already has. It becomes necessary if execution ever moves
behind a queue, which it has not.

### `POST /runs`

```json
{ "input": "What is the status of order ORD-1001?" }
```

```json
{
  "request_id": "req-4f1c...",
  "trace_id": "trace-9ab3...",
  "status": "success",
  "response": "Order ORD-1001 is shipped, total R$ 429.7, tracking BR100100001.",
  "route": "researcher",
  "provider": "stub",
  "latency_ms": 12.4,
  "retry_count": 0,
  "errors": [],
  "policy_decision": null,
  "awaiting_confirmation": null
}
```

`status` is the platform's own vocabulary, passed through unchanged: `success`,
`blocked`, `declined`, `denied`, `failed`, `awaiting_confirmation`, `rejected`,
`rate_limited`, `expired`.

Two fields the platform produces are deliberately **not** returned.
`tool_result` is raw tool output captured before the output-security layer masks
PII and blocks secrets — `response` is the field that has been through that gate.
`validation` is an internal second-opinion record with no meaning to a caller.

### `POST /runs/{request_id}/confirm`

```json
{ "approved": true, "actor": "operator" }
```

The body carries the decision and nothing else. Which action it applies to is
rebuilt by the platform from its own state, so approving one action cannot
execute another, and a body that names a tool is rejected.

## Status codes

The rule: **the HTTP status describes the fate of the HTTP request; the body's
`status` describes the fate of the agent run.**

| Code | When |
|---|---|
| `200` | The platform ran and produced a result — including a policy denial, which is the platform working as designed, and including a provider failure, which was recorded and executed no tool. |
| `400` | The body never became a request: malformed JSON, missing or mistyped `input`, unknown fields. |
| `404` | No suspended action with that id — it never existed, or was already consumed. |
| `409` | The suspended action aged out before it was confirmed. |
| `422` | The platform's input-security layer refused the input. |
| `429` | Rate limited. |
| `500` | An unexpected error, or a platform status the mapping does not know. |
| `503` | `/ready` only: this instance cannot take work. |

`422` and `429` are the two places a platform outcome becomes a non-200: both are
refusals made *at the door*, before any agent work, and HTTP has an exact
equivalent for each.

## Health and readiness

`/health` checks that the process is running and nothing else — no database, no
provider, no registry. A liveness probe that checks dependencies turns a
dependency blip into a simultaneous restart of every replica, converting a
degradation into an outage.

`/ready` checks what is instance-local and actionable: the repository answers and
the tool registry is populated. Provider health and budget are **reported in the
body but do not gate readiness**, because every replica shares one provider —
failing readiness on an open circuit would pull them all out at once and replace
informative, governed responses with an unreachable service.

## Local and shared state

The platform runs process-local by default, and moves the state that is
semantically global onto Redis when `REDIS_URL` is set — pending confirmations,
the graph checkpointer, the provider-call ledger and the rate-limiter window.
**[docs/state.md](state.md)** covers what moves, what deliberately does not, and
what that means for request data.

The following remain process-local in *both* modes:

- the circuit breaker — each replica learns a provider outage independently
- the query-embedding cache — affects cost, never correctness
- the retriever and vector index — derived from the corpus
- the per-request cost accumulator — a request never spans processes

In shared mode a confirmation suspended on one replica can be approved on
another, and the provider budget and rate limit are enforced once across all of
them. What is not yet demonstrated is that behaviour *under load*: horizontal
scaling is possible, not proven.

There is no message queue, no external database, no service mesh and no
orchestrator in this document's scope. Both arrived later: MCP in V2.3, and
Kubernetes with an HPA in V2.4.

## Request size

Bodies are read with a byte ceiling, `MAX_BODY_BYTES` (397,312 bytes), enforced
while the body streams in: a declared `Content-Length` over it is refused
without reading, and a chunked body is refused at the first chunk that crosses
it. Either way the answer is `413 payload_too_large`, and at most the ceiling
plus one chunk is ever held. The number is derived from the schema's own input
limit with every character JSON-escaped, so it can never refuse a body the
schema would accept.
