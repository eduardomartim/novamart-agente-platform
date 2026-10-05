# Release readiness

Every row carries evidence or it does not say PASS. Where something was not run,
the row says **NOT VALIDATED** and names the reason — a claim nobody checked is
worth less than an admitted gap.

Measured on the last full run of each gate. Docker was stopped for the whole of
V2.9 and V3.0, so everything needing a daemon carries the date of its last
successful run rather than a fresh one.

## Architecture

| Item | Status | Evidence |
|---|---|---|
| No agent executes a tool | **PASS** | `require_gateway()`; `tests/security/test_gateway_bypass.py` |
| Policy engine is the sole authority | **PASS** | PL001–PL011; `tests/unit/test_policy.py` |
| Execution boundary survives a process hop | **PASS** | signed grants; `tests/integration/test_mcp_boundary.py` |
| HTTP adds no authority | **PASS** | `tests/integration/test_api.py`, 52 tests |
| Repository contract, three implementations | **PASS** | `test_repository_conformance.py`, 17 × 3 |

## Security

| Item | Status | Evidence |
|---|---|---|
| Authentication enforced, fail-closed | **PASS** | `tests/security/test_api_auth.py`, 67 tests |
| `runs:write` separated from `confirm:write` | **PASS** | no `admin` scope exists; separation tests |
| Approver comes from the credential | **PASS** | `test_actor_attestation.py`, 9 tests |
| Per-principal quota, deployment ceiling intact | **PASS** | `test_principal_quota.py`, 15 tests |
| Prompt injection, egress, leakage, fencing | **PASS** | security suite, **727 tests** |
| Protected-file baseline | **PASS** | 12/12; `sha256sum -c PROTECTED.sha256` |
| No secret in the repository or its history | **PASS** | 0 key files, 0 PEM blocks outside detectors, `.env` absent from 0 commits |
| TLS at the edge | **PASS (static)** | 21 tests; live proofs **NOT VALIDATED** |
| Multi-tenancy / RLS | **N/A** | single-tenant by design; see Limitations |

## Live provider safety

| Item | Status | Evidence |
|---|---|---|
| A key alone does not authorise | **PASS** | `build_provider()` refuses; `test_live_gate.py` |
| Booleans are not authorisation | **PASS** | 11 parametrised refusals |
| The incident command is refused | **PASS** | `pytest -m "not docker"` exits 4 |
| Embeddings covered | **PASS** | conftest guard patches `embed_content` |
| No workflow can reach a provider | **PASS** | `ci_assert_no_live.py`, 9 variables + the platform's own answer |
| Calls made since the gate | **PASS** | ledger unchanged at `41 / 21 / 72` |

## Testing

| Item | Status | Evidence |
|---|---|---|
| Offline suite | **PASS** | 2218 selected; 2166 passed / 51 skipped (Docker, Redis, PostgreSQL absent; 1 intentional) on Windows, Python 3.12 -- the one remaining check compares the protected-file manifest with the last commit and passes once the change is committed |
| Security suite | **PASS** | 844 collected; part of the run above |
| Shuffled order, two seeds | **PASS** | 1493 × 2 on Linux |
| Container suite | **PASS** | 26 passed (last run before the daemon stopped) |
| Scale, ten replicas | **PASS** | `tests/integration/test_scale.py`, 9 tests |
| Load | **PASS** | `scripts/load_test.py`; see Performance |
| ruff / mypy strict | **PASS** | ruff clean across the repository; mypy strict clean, 91 source files |

The three Windows failures are `test_index_path_portability.py`. They install a
wheel and run a subprocess with `PYTHONPATH` narrowed to that install, so the
subprocess inherits only the interpreter's own site-packages. On this host the
project's interpreter is blocked by Smart App Control, so the runs use the base
interpreter, which has no `numpy`. The same three pass on Linux. **Environment,
not code** — and the test was deliberately not weakened to accommodate it.

## Dashboard

| Item | Status | Evidence |
|---|---|---|
| Six pages render, empty and seeded | **PASS** | `test_dashboard_pages.py`, `test_dashboard_states.py` |
| React `insertBefore` regression guarded | **PASS** | `test_dashboard_stability.py`, 10 tests: static patterns plus real widget ids across navigation |
| ALLOW / DENY / CONFIRM → approve and decline | **PASS** | driven end to end through `AppTest` and in the browser |
| Browser console clean | **PASS** | no errors, no warnings, after walking all six pages and running a blocked request |
| Responsive 1440 / 1366 / 1024 / 768 / 375 | **PASS** | measured: no horizontal overflow, no clipped element, sidebar ≤ 29% |
| Recruiter acceptance content | **PASS** | `test_recruiter_discovery.py`, `test_recruiter_experience.py` |

## Observability

| Item | Status | Evidence |
|---|---|---|
| Correlation IDs across replicas | **PASS** | `X-Request-ID`, honoured and sanitised |
| Structured JSON logs, allow-listed fields | **PASS** | `test_observability_leakage.py` |
| Prometheus exposition, help-text allow-list | **PASS** | a counter without help is not rendered |
| Per-step timing for one request | **PASS** | ordered event stream with `latency_ms`, indexed `(request_id, sequence)` |
| OpenTelemetry | **NOT IMPLEMENTED** | deliberate; see below |

## Kubernetes

| Item | Status | Evidence |
|---|---|---|
| Manifests render | **PASS** | `kubectl kustomize k8s/` |
| Deployment, probes, securityContext, resources | **PASS** | non-root 10001, read-only rootfs, all caps dropped |
| NetworkPolicy default-deny | **PASS** | plus an explicit rule for the ingress controller |
| Version label accurate | **PASS** | corrected 2.5 → 3.0 in V3.0 |
| Deploy + rollout + 23 HTTP proofs | **NOT VALIDATED** | last passed 23/23 in V2.7; Docker stopped since |
| HTTPS proofs | **NOT VALIDATED** | `scripts/tls_proofs.py` written, never run |
| HA | **N/A** | single-instance by design; see Limitations |

## CI/CD

| Item | Status | Evidence |
|---|---|---|
| Workflows written and audited | **PASS** | 9 PR jobs + 3 nightly, timeouts, `contents: read` |
| No provider credential in any job | **PASS** | asserted at job start, not claimed in YAML |
| Protected baseline gated | **PASS** | `protected-files` job |
| Executed by a real runner | **NOT VALIDATED** | no remote exists; deliberate until after V3.0 |

## Data and persistence

| Item | Status | Evidence |
|---|---|---|
| Ledger exact under concurrency | **PASS** | 10 replicas, 200 requests, 200 rows, no duplicate ids |
| Money shared across replicas | **PASS** | `test_scale.py` |
| Schema evolution | **DOCUMENTED LIMITATION** | idempotent DDL; no versioned migrations — see below |

## Performance

Measured with the stub, so these describe this machine and the platform's
concurrency behaviour, not model latency.

| Run | Result |
|---|---|
| 4 replicas, 16 workers, 200 requests | 20.9 req/s · p50 233 ms · p95 2999 ms · p99 5565 ms · 200/200 success |
| 10 replicas, 20 workers, 200 requests | 21.6 req/s · p50 231 ms · p95 3713 ms · p99 6174 ms · 200/200 success |

The ledger reconciled exactly in both: 200 rows for 200 requests, no duplicate
ids. The long tail is SQLite write contention under concurrent writers — the
deployment that matters uses Postgres, which the container and cluster gates
exercise.

## Reproducibility

| Item | Status | Evidence |
|---|---|---|
| Suite needs no network, key or Docker | **PASS** | offline by default |
| Vector index tracked and pinned | **PASS** | `034f53c4…`, checked in two CI jobs |
| Line endings pinned | **PASS** | `.gitattributes`; a clone cannot change a protected hash |
| Local commands match CI | **PASS** | `make gates` runs what a PR runs |

---

## Deliberately not implemented

**OpenTelemetry.** The platform already records, per request, an ordered stream
of steps with per-step timings, persisted and indexed by `(request_id,
sequence)`, plus a Prometheus duration histogram and correlation IDs that
survive across replicas. Running one request produces twelve timed steps naming
each agent, the model calls, the policy decision and the tool call — which is
the question tracing answers. OTel's distinctive value is correlating across
*services*; this is one service, so the spans would be intra-process and would
duplicate the event stream. Adopting it would add two runtime dependencies and
either an external collector the demo must not require, or a console exporter
that produces noise. Out of scope, and recorded as a decision rather than an
omission.

**Alembic.** It is built on SQLAlchemy, and this project has no SQLAlchemy: the
repository layer is a hand-written `Protocol` with three implementations over
raw `sqlite3` and `psycopg`. Adopting Alembic would mean adding an ORM as a
dependency purely to manage migrations for a schema that has never changed.
`initialize()` runs idempotent DDL, which is honest for a schema with one
version and stops being honest the first time a column moves. The extension
point is the `Repository` protocol; the decision to add versioned migrations
belongs to whoever changes the schema first.

**Redis and PostgreSQL high availability.** Single instances with volumes.
Production would use a managed service or an operator with replication and
failover. Building that here would be constructing an operations platform to
demonstrate an agent platform.

**Sharing the circuit breaker and the embedding cache.** Both remain
per-replica, and `tests/integration/test_scale.py` asserts it rather than
leaving it to prose. The breaker means a failing provider is retried once per
replica before all of them open — wasteful, not incorrect. The cache affects
cost, never correctness. Coordinating either would cost more than it buys.

## Limitations, stated plainly

- **Single-tenant.** Scopes are not tenants. Budget, limits and data are global;
  there is no tenant concept anywhere in the model.
- **The TLS certificate is self-signed**, for a hostname that does not exist,
  on a local cluster. Production would use a real CA.
- **kind is not production.** One node, single-instance backing services.
- **The budget pre-check is not atomic.** Overshoot is the budget plus at most
  one in-flight call per replica. The ceiling that actually binds spending — the
  physical provider-call ledger — has been atomic since V2.2.
- **Credential rotation needs a rolling restart.** The table is read at start-up.
- **`pip-audit` is point-in-time.**
- **No workflow has been executed by a runner.** Every command was validated
  locally; that the YAML runs on GitHub is the one claim this project cannot yet
  make.
