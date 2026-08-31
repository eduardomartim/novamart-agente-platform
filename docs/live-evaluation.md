# Live evaluation

How results from a real model are produced and reported, and — more
importantly — what they do and do not prove.

## The distinction that matters

The platform can be evaluated two ways, and they measure different things:

| | Deterministic evaluation | Live evaluation |
|---|---|---|
| Provider | `stub` | `gemini` |
| Command | `agent-platform eval` | `agent-platform eval --live` |
| Needs a key | no | yes |
| Needs network | no | yes |
| Reproducible | exactly | no |
| **Measures** | **the platform** | **a model, on this dataset** |

**A 68/68 deterministic score is not a statement about Gemini.** It says the
policy engine, gateway, authorisation, budget and confirmation logic behaved
correctly given predictable model output. That is a real and useful thing to
measure — it is the platform's own correctness — but it is not model quality,
and the CLI and dashboard both say so on every run.

Live scores are the opposite: genuinely informative about the model, and not
reproducible. Two live runs of the same case can differ.

## Running it

```bash
AGENT_PLATFORM_LIVE=<value> agent-platform eval --live
```

`--live` needs two separate things: a `GEMINI_API_KEY`, which selects the real
provider, and `AGENT_PLATFORM_LIVE`, which authorises spending quota with it.
A key alone is refused in `build_provider()` before any client is constructed.
See `docs/live-verification.md` for the value and the reasoning.

Defaults to a curated 15-case subset. A full sweep is roughly 200 model calls,
which on a free tier means hitting per-minute limits and burning daily quota to
re-check what the deterministic suite already covers.

```bash
agent-platform eval --live --full
```

`--live` **refuses to run** without `GEMINI_API_KEY` rather than quietly
measuring the stub and labelling the result as live, and refuses again without
`AGENT_PLATFORM_LIVE` rather than treating possession of a key as permission to
spend it.

### The curated subset

15 cases, weighted towards checks whose answer actually depends on the model:

| Category | Cases | Why these |
|---|---|---|
| normal | 3 | Does routing and tool selection hold up at all |
| adversarial | 4 | The main reason to run live — a real model is far more persuadable than a keyword matcher |
| tool_use | 3 | Tool selection and the confirmation gate |
| sensitive_data | 2 | The egress path, with real transmission |
| regression | 3 | Bugs that were real |

## Reading a live result honestly

**Safety must be 1.0.** Safety assertions describe platform guarantees, which
do not depend on the model: a `CRITICAL` action is refused whatever is
proposed. A safety failure on a live run is a platform bug, not a model
quality issue, and `test_curated_live_subset_holds_every_safety_property`
asserts exactly this.

**Correctness and tool accuracy will differ from the stub, and that is
expected.** The stub routes by keyword; a model routes by understanding. It
will get some cases right that the stub gets wrong by luck, and vice versa. A
live correctness score below the stub's 1.0 is information about the model and
the prompts, not a regression.

**Relevance is only populated on live runs.** The judge declines to score under
the stub rather than returning a fixed number that would look like a
measurement.

**Never compare a live run to a stub run.** They are stored with their provider
and model, and drift baselines are scoped by both, so the platform refuses the
comparison rather than producing a number. See `docs/architecture.md`.

## Drift across providers

```bash
agent-platform eval --live
agent-platform baseline     # stored as default::gemini::<model>
agent-platform drift
```

Baselines are keyed `name::provider::model`. Capturing under the stub and
comparing against live is structurally impossible: the lookup simply does not
find a baseline, and the dashboard says why rather than showing a comparison.

## Real latency, measured

Measured over 10 requests in two runs. The earlier ~53 s figure came from a
single heavily throttled sample and overstated the typical case:

| Condition | Total | Model time | Platform overhead |
|---|---|---|---|
| **Unthrottled** (n=5) | **~4.1 s median** | ~87% | ~500 ms |
| Throttled (n=5) | 8–88 s | ~20% | remainder is retry backoff |

Two model calls per record lookup; three for a knowledge-base question,
which also spends one embedding call unless the query embedding is cached. Tool
time is negligible — **under 1 ms**, since every tool is an in-memory
simulation.

The timings above were measured before the answer node existed, so they describe
a two-call read path. They have not been re-measured for the three-call
knowledge-base path.

**One caveat, because it changes how the numbers read.** The "platform" bucket
is computed as `total − model − tools`, and only *successful* model calls emit
an `llm_call` event. Failed attempts and their retry backoff therefore land in
the platform bucket, which is why it appears to reach 80% under throttling. Real
platform overhead is the unthrottled figure: roughly 500 ms, about 13%. Tracing
retry attempts would close that observability gap; it is not currently done.

The sub-100 ms figures the dashboard shows in demo mode are **stub** latency and
say nothing about live behaviour.

Measured cost per call, provider-reported:

| Agent | in | out | cost |
|---|---:|---:|---:|
| router | 203 | 11 | $0.00008840 |
| researcher | 950 | 34 | $0.00037000 |
| executor | 1911 | 75 | $0.00076080 |

Roughly $0.001 per full request at paid-tier rates; the free tier charges
nothing. Budget defaults (`$0.05` per request, `$1.00` per day) leave ample
headroom.

Expect transient `503`, `429` and read timeouts on the free tier. The platform
handles them as bounded retries followed by a recorded, sanitised failure with
**zero tool executions** — verified on the live path.

## What has actually been run

Live verification requires two things: a key, and explicit authorisation via
`AGENT_PLATFORM_LIVE`. The live suite is 25 tests. Unauthorised, `pytest -m live`
**fails at collection** and executes nothing -- it used to skip quietly, which is
how a mistaken marker expression went unnoticed for 72 calls. Authorised but
without a key, the tests skip.

Both live suites have been executed against `gemini-3.5-flash-lite`:

| Suite | Result |
|---|---|
| `test_gemini_live.py` — smoke (13, excluding the `slow` sweep) | **13/13 pass** |
| `test_gemini_adversarial.py` — adversarial | **11/11 pass** |

The full agent → policy → gateway → tool flow completes with a real model
driving it, the confirmation gate holds, destructive requests are refused, and
the API key appears in no response, trace, database row or generated file.

The adversarial set includes four persuasion attacks a keyword stub cannot
simulate: appeals to authority and urgency, framing the platform's own refusal
as the bug, a roleplay wrapper, and claimed prior approval written as prose.
None reached a destructive tool.

Live testing found three real defects a faked SDK could not have caught — this
model rejects `thinking_budget=0`; a bare `{"type": "OBJECT"}` argument schema
constrained every proposal to empty arguments; and a test failure rendered the
`Settings` dataclass and **printed the live API key**. All three are fixed and
pinned by regression tests; see `docs/audit-report.md` §15.1, §15.2 and §16.6.

**Still not run:** `agent-platform eval --live`. Live *quality* numbers for
routing and tool selection are therefore unmeasured, and this project makes no
claim about them. To produce them:

```bash
pytest -m live
```
