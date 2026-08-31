# Shared state

The platform runs in one of two modes, and it always reports which.

**local** — the default, when `REDIS_URL` is unset. State lives in the process.
Correct for one process, and what the offline suite and the demo run in.

**shared** — when `REDIS_URL` is set. The state that is semantically global
moves to Redis, so a confirmation suspended on one replica can be approved on
another and a limit configured once means the same thing however many replicas
exist.

```bash
# local (default)
python -m agent_platform.api

# shared
REDIS_URL=redis://localhost:6379/0 python -m agent_platform.api

# or the whole thing, with Redis alongside
docker compose up --build
```

## Why anything had to move

With one process, "the pending confirmations" and "the rate-limiter window" are
just dictionaries. With two, each replica has its own — so a limit of 60/minute
silently becomes 120, a provider budget of 400 calls becomes 800, and a
confirmation created on replica A cannot be approved on replica B because the
graph it belongs to only exists in A's memory.

None of that announces itself. The configuration still says 60, and the logs
still say the limiter is working.

## What moved, and what did not

| State | Mode | Why |
|---|---|---|
| Pending confirmations | **shared** | A confirmation must be resumable, and single-use, across replicas |
| Graph checkpoint | **shared** | Holds *where* a suspended run reached; without it the pending record has nothing to resume |
| Provider-call ledger | **shared** | A daily ceiling that multiplies by replica count is not a ceiling |
| Rate-limiter window | **shared** | Same reason. Keyed per principal since V2.6, plus a separate deployment-wide bucket |
| Per-request cost accumulator | local | Scoped to one request, and a request never spans processes |
| Daily money spend | **repository** | Durable, and shared once `DATABASE_URL` points at Postgres. With per-pod SQLite it was durable but *not* shared, which is the defect V2.5 closed |
| `require_gateway()` ContextVar | **local, deliberately** | Marks the dynamic extent of an authorised call. It is a property of *this* call stack; sharing it would be meaningless and dangerous |
| Retriever / vector index | local | Derived from the corpus — rebuilding is cheaper than coordinating |
| Circuit breaker | local | Still per-replica. See limitations |
| Query-embedding cache | local | Only affects cost, never correctness |

## How the guarantees are kept

Three operations are read-modify-write races, and each is one scripted
operation rather than a Python `if` around a `get`:

- **Single-use consumption** — a scripted get-and-delete. Two replicas racing to
  approve the same confirmation both call it; exactly one is handed the record.
- **Budget charge** — the read and the increment are one operation, so "both saw
  one left" cannot happen.
- **Rate-limit acquire** — the count and the append are one operation.

Written the naive way these are correct on one replica and wrong on two. That is
not a theoretical claim: reverting each to read-then-write makes the concurrency
tests fail, at **20 threads consuming one confirmation** and **22 provider calls
granted against a limit of 10**.

### Expiry, and telling "expired" from "never existed"

The platform distinguishes an aged-out confirmation (409) from one that never
existed (404). A key the backend has already evicted cannot tell those apart, so
a record physically outlives its own logical expiry — long enough to answer the
question, then reclaimed.

### Resuming without serialising a live object

The stored record is four identifiers and four counters:

```
request_id, trace_id, user_input, created_at,
llm_calls, tool_calls, tool_output_bytes, elapsed_seconds
```

Nothing else. The graph, the tracer, the provider, the tool registry and the
gateway are all rebuilt from configuration on the resuming replica, because they
are derived state — and because pickling them would serialise an open repository
handle and a set of tool handlers along with them.

The counters travel so a resumed run does not win itself a fresh allowance.
`ResourceGuard.begin()` already refuses to restart a re-entered request's
deadline within one process; carrying the counters extends that rule across a
process boundary, where the originals are not in memory to preserve.

## What is now stored outside the process

**Shared mode puts the user's request text in Redis**, with a TTL, for as long
as a confirmation is resumable. This is a deliberate change of posture and worth
stating plainly, because V1 stores only a *digest* of user input in its database
on the grounds that the full text is never needed to operate the platform.

For a suspended run it is needed: the graph state contains the request, and the
resuming replica has to rebuild the input assessment from it. The consequences
are bounded rather than eliminated:

- it expires with the confirmation, and is not written to the durable database;
- Redis publishes no port in `compose.yaml` and is reachable only on the
  internal network;
- raw `tool_result`, `validation` and gathered `context` are **not** in the
  pending record — a test pins its exact field set so nothing joins it quietly;
- no credential is written: a test scans the whole keyspace for credential
  shapes after real traffic.

The graph checkpoint, which is a separate mechanism, does contain execution
state. Treat a Redis holding this platform's state as holding request data.

## Durable storage: PostgreSQL

Redis holds what is ephemeral and TTL-bounded. Postgres holds what has to
outlive it: requests, events, evaluation runs, drift baselines, and the recorded
spend the budget guard reads.

```bash
DATABASE_URL=postgresql://user:pass@host:5432/agentplatform python -m agent_platform.api
```

Empty means SQLite at `DATABASE_PATH`, which is the default and what the offline
suite runs on.

### The defect this closed

`BudgetGuard` reads the day's spend from the repository. Until V2.5 that
repository was a per-pod SQLite file, so **each replica saw only its own spend
and each allowed the full daily budget**. Two replicas meant twice the
configured ceiling; three meant three times. Nothing said so — the configuration
still read one number.

**Validated in the cluster**, not just in tests: replica A recorded $1.00 of
spend, and replica B — a different pod that had spent nothing — read the same
ledger and refused the next call with *"daily budget exceeded: projected
$1.100000 > limit $1.000000"*.

### What it did not close

`check()` is still read-then-decide. Two replicas checking in the same instant
can both see room and both proceed. The defect changed shape rather than
vanishing:

| | Before | After |
|---|---|---|
| Overshoot with N replicas | **N × the daily budget** | the budget + at most one in-flight call per replica |

That residual is accepted deliberately. The monetary budget is an estimate-based
pre-check reconciled afterwards by `record_spend`, and the ceiling that actually
binds spending — the physical provider-call ledger — has been atomic since V2.2.
Serialising every model call behind a lock to tighten an estimate would cost
more than it buys. `test_the_residual_race_is_bounded` pins the behaviour rather
than pretending the guard is atomic.

### Three implementations, one contract

`Repository` is a `Protocol` of 18 methods with three implementations: in-memory,
SQLite and Postgres. `tests/integration/test_repository_conformance.py` runs one
body of assertions against **all three** — three implementations that were never
checked against the same expectations are three behaviours wearing one name.

The schema is a translation of the SQLite one, not a redesign, and three choices
are deliberate: money stays integer nano-USD (`BIGINT`) so `SUM()` is exact;
timestamps stay ISO-8601 `TEXT` because `spend_since` compares lexicographically
and that ordering is chronological; `blocked` and `estimated` become `BOOLEAN`,
the one place the native type is plainly better.

### Failure and limitations

A configured `DATABASE_URL` that cannot be reached **raises** rather than falling
back to SQLite — a silent downgrade would hand back the per-replica budget with
nothing to indicate it. The connection string carries a password, so it is held
`repr=False`, registered for log redaction, and never appears in an error.

- **One Postgres instance with a volume. Not HA.** Production would use a
  managed service or an operator with replication and failover.
- **No versioned migrations.** `initialize()` runs idempotent DDL, matching the
  SQLite implementation. Adding Alembic is a separate decision.
- The circuit breaker and the embedding cache remain per-replica.

## Failure behaviour

A configured `REDIS_URL` that cannot be reached **raises** rather than falling
back to local state. Silently degrading would mean an operator asked for one
shared limit and got N independent ones, with nothing to say so.

The rate limiter fails **closed**: if the backend is unreachable the request is
denied. An unreachable limiter that admits traffic is not a degraded limiter, it
is no limiter, and it fails invisibly exactly when load is highest.

## Requirements

Shared mode needs the `redis` extra and **Redis 8 or Redis Stack** — the graph
checkpointer uses the query engine (`FT.*`), which Redis 8 bundles into core. On
a bare `redis:7` it fails at startup with `unknown command 'FT.INFO'`.

```bash
pip install -e ".[api,redis]"
```

## Running the tests

The offline suite needs nothing. The shared-state tests skip cleanly when no
Redis is reachable, and use one when there is:

```bash
docker run -d --name ap-redis -p 16379:6379 redis:8-alpine
python -m pytest tests/integration/test_shared_state.py
```

Override the URL with `TEST_REDIS_URL`. Most assertions run against **both**
backends from one body of test code, because two implementations behind one
interface that were never checked against the same expectations are two
behaviours wearing one name.

## Limitations

- **The circuit breaker is still per-replica.** Each learns a provider outage
  independently, so a failing provider is retried once per replica before all of
  them open. Wasteful, not incorrect — and asserted in
  `tests/integration/test_scale.py` rather than left to this paragraph.
- **Verified at ten replicas, not just two.** Ten instances over one database,
  driven concurrently: 200 requests produced 200 ledger rows with no duplicate
  ids, every status was one the platform defines, and every replica agreed on
  total spend. See `scripts/load_test.py` and `docs/release-readiness.md`.
- **Single-tenant.** Rate limits, budget and data are global; there is no tenant
  concept anywhere.
- **Authentication is enforced** as of V2.6: the HTTP API refuses to start
  without credentials, and approving a suspended action needs `confirm:write`.
  See `docs/api.md`. There is still no TLS, so the bearer token relies on the
  network being trusted.
- **No Redis authentication in `compose.yaml`.** A password written into a file
  beside the code is not a password. What protects it there is that it is not
  reachable from outside the compose network. Production supplies a credential
  through a secret and sets `REDIS_URL` accordingly.
- Horizontal scaling is now *possible*. It is not yet *demonstrated* under load,
  and no orchestrator, service mesh or autoscaler exists here.

MCP and Kubernetes were not part of the phase this document was written for.
Both exist now: the execution boundary moved behind a real MCP server in V2.3
(see [execution-boundary.md](execution-boundary.md)) and the platform runs on
Kubernetes as of V2.4 (see [kubernetes.md](kubernetes.md)).
