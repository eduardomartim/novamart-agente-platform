# Continuous integration

Two workflows, and one thing neither of them has.

```
Pull request / push to main          Nightly 03:00 UTC + dispatch
─────────────────────────────        ────────────────────────────
ruff                                 shuffled order, seed 4242
mypy strict                          shuffled order, seed 11
offline suite                        pip-audit
security suite                       kind: deploy + 23 HTTP proofs
live gate holds
kustomize
protected baseline                   Never, in any workflow
redis + postgres                     ─────────────────────────
image + container suite              live provider tests
```

## What no job has

No `GEMINI_API_KEY`. No `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`GOOGLE_API_KEY`, or any other provider credential. No
`AGENT_PLATFORM_LIVE`.

That is not a claim about how the YAML was written — a workflow file can be
read and believed, but an organisation-wide secret injected into every job
cannot be read from the workflow file at all. So it is checked at the moment it
matters: `scripts/ci_assert_no_live.py` runs at the top of every job that
executes tests, looks for nine provider variables and the authorisation
variable, and then asks the platform itself whether live calls are authorised.
A job that finds any of them refuses to continue.

**Live tests are never run by any workflow.** Not on a schedule, not on
dispatch, not at all. Running them is a manual act requiring both a key and a
separate authorisation; see [live-verification.md](live-verification.md).

## The live gate, in CI

One job exists purely to prove the protection still holds, and it re-enacts the
incident that motivated it.

The suite used to keep itself offline with `addopts = "-m 'not live'"` in
`pyproject.toml`. A `-m` on the command line **replaces** that value rather than
combining with it — pytest stores a single `markexpr` — so this command:

```bash
pytest -m "not docker"
```

silently made every live test eligible. From there each remaining check waved it
through: the live modules called `load_dotenv()` at *import* time, so the real
key was already in the process; their `skipif` asked whether a key was present,
which meant possessing one *enabled* them; the conftest guard exempted anything
marked `live`, so it opened for exactly the dangerous case; and the last check
before the network was a 400-call/day budget, which answers "how many more?" and
never "may you at all?".

Three controls, one decision. It cost **72 unintended provider calls**.

`scripts/ci_assert_incident_refused.py` runs that exact command in CI and
requires it to exit 4 — a usage error, nothing executed. It also checks the
complement: `-m "not live and not docker"` must still collect normally, because
a gate that fired on ordinary runs would be deleted by the first person it
inconvenienced.

The real protection is not in pytest. It is in `build_provider()`, the one
function every path to a real provider goes through, and it refuses above its
own lazy import of the SDK. No marker expression reaches it, and it covers the
CLI, the dashboard and `scripts/build_vector_index.py`, none of which are tests.

**Selecting a test is not the same as authorising a call.** Conflating the two
is what the incident was.

## Why `data/kb_vectors.db` is in the repository

`.gitignore` excludes `data/*.db` — local databases the demo produces — with one
explicit exception.

The vector index is a build input, not local state. The production `Dockerfile`
copies it, retrieval loads it, and its SHA-256 is pinned and checked in both the
container job and the cluster job. Excluding it would mean `docker build` cannot
work from a clean checkout.

And it cannot be regenerated in CI. The only way to rebuild it,
`scripts/build_vector_index.py`, calls a real embedding API — the stub refuses
to fabricate vectors rather than produce an index that looks valid and is not.
So leaving the file out would force CI to either fail or make live provider
calls, which is precisely what the live gate exists to prevent.

176 KB, deterministic, verified by corpus fingerprint at load time. Tracking it
is the choice that keeps CI honest.

## Jobs

| Job | Needs | Timeout | Notes |
|---|---|---|---|
| `lint` | — | 5 min | |
| `typecheck` | — | 10 min | installs every extra mypy is configured to check |
| `offline` | — | 20 min | ~1500 tests; JUnit artifact |
| `security` | — | 15 min | ~680 tests; JUnit artifact |
| `live-gate` | — | 15 min | the incident re-enactment |
| `manifests` | — | 5 min | renders, then greps for credential shapes |
| `protected-files` | — | 5 min | `sha256sum -c PROTECTED.sha256`, plus a count check |
| `integration` | — | 20 min | Redis 8 and Postgres 17 as services |
| `container` | lint, typecheck, offline, protected-files | 25 min | builds the real image |

Only `container` waits on anything: the image is worth building once the code
inside it is known to lint, typecheck, pass its own suite, and still match the
protected baseline. Everything else runs in parallel.

### The protected baseline

Twelve files carry a pinned SHA-256 in `PROTECTED.sha256`: the policy engine
and its rules, the gateway's execution path, the simulated dataset, the graph,
the execution grant, the MCP server, the repository contract and its SQLite
implementation, the shared-state backend, the production Dockerfile, and the
vector index. They are the files where a change is either a mistake or a
decision somebody has to make deliberately.

The manifest is in `sha256sum`'s own format, so the gate needs no script and no
interpreter. A second step asserts it still lists twelve entries, because
`sha256sum -c` reports success on a manifest that lists nothing at all.

It never restores anything. Re-pinning is a decision to record, not a file to
regenerate.

### A skip is not a pass

The integration suites skip cleanly when Redis or Postgres is unreachable. That
is right on a laptop and wrong in a job created to exercise those services:
pytest would report `skipped`, the job would go green, and nothing would have
been verified.

`scripts/ci_assert_services.py` runs first and fails loudly instead. It connects
the same way the suites do — same environment variables, same defaults — so a
pass means the suites will exercise the services rather than skip past them.

Redis 8 specifically, not 7: the graph checkpointer uses the query engine
(`FT.*`), which 8 bundles into core.

## Nightly

Three things belong on a schedule rather than a pull request.

**Shuffled order**, two fixed seeds. Order dependence is a real defect class — a
test that only passes because an earlier one left state behind is a false green.
Fixed seeds rather than random, so a failure can be replayed exactly.

**`pip-audit`** queries an advisory database, so it is slow and can turn red
without anything in this repository having changed. Useful on a schedule, noise
on a pull request. The JSON report is uploaded; it contains dependency names and
advisory ids, nothing else.

**The cluster.** `kind` is created, the image is built and loaded,
`scripts/k8s_up.sh` deploys, and `scripts/cluster_proofs.py` runs the 23 HTTP
proofs from V2.6 — public routes without a credential, `/metrics` protected,
`runs:write` separated from `confirm:write`, and the headline one: a high-risk
action suspended on replica A, approved on replica B, with the shared Postgres
audit record naming the *authenticated* principal rather than the name the
request body tried to inject.

Then `scripts/ci_assert_no_secret_in_logs.py` reads the pod logs and checks for
the exact token values in play — not for something that looks like a token. The
cluster is destroyed in an `if: always()` step: a leaked cluster is a leaked
runner.

## Secrets in the cluster job

Both cluster secrets are generated inside the job and piped straight to
`kubectl`. Nothing is written to the workspace, nothing appears in an `argv` —
which is visible to every process on the host — and nothing is uploaded as an
artifact.

What reaches the cluster is a table of **sha256 digests**. The tokens themselves
are written only to a temporary file at mode 600, outside the repository, for
the proofs to read.

## Running the same gates locally

```bash
make gates
```

Each target spells out `-m "not live and not docker"`. That is the point of the
`Makefile`: the convenient way to run the suite is also the way that cannot
select live tests by accident.

## Limitations

- **No deployment.** These workflows verify; they publish nothing. There is no
  registry, no environment, no release.
- **kind is not production.** One node, single-instance Redis and Postgres. It
  proves the manifests and the runtime behaviour, not availability.
- **`pip-audit` is point-in-time.** Nightly narrows the window; it does not
  close it.
- **The container job builds without a cache.** Correct and slow; a registry
  cache would need credentials this design deliberately withholds.
