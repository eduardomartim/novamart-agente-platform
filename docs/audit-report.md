# Audit report

Record of the audits and of the defects found while building. Kept in the
repository because the audit is part of what this project demonstrates.

Recorded in order:

- **§1–7 — Phase 1**, the initial build (2026-08-27).
- **§8–14 — Phase 2**, the live Gemini integration, built and verified against
  a faked SDK (2026-08-28).
- **§15 — live verification** against the real Gemini API, once a key was
  available (2026-08-28).
- **§16–24 — Phase 3**, adversarial security testing, hard resource limits and
  QA (2026-08-28).

> Earlier figures are **historical** and were accurate when written. The
> platform has grown since: six tools became eight, and the deterministic suite
> went 216 → 300 → 313 → 417 → 471 → 477 → 521 → 528 → 530 → 607 → 626 → 716. **Current measured numbers are in §35; the latest live observation is §36.**
> Earlier sections are deliberately left as written rather than retro-edited, so the
> record shows what was true at each point — §13 in particular is superseded by
> §15 and says so.

> **Action required:** §16.6 records an API key that was exposed in terminal
> output during Phase 3 testing. Rotate it.

---

## Phase 1 — initial build

Date: 2026-08-27 · Python 3.12.13

---

## 1. Blueprint audit

Findings raised before any code was written.

### 1.1 Stale model identifiers and a latent pricing bug

The blueprint specified `gemini-2.5-flash`. That model still exists and still has
a free tier, but newer free-tier models are available. More importantly, a flat
price table is a **latent correctness bug**: `gemini-3.7-flash` is on
introductory pricing ($0.75/$3.75) that expires 2026-12-31 and then doubles.
A hardcoded number becomes silently wrong on a known date.

**Change** — rates are declared as dated periods with a verification date, and
`is_stale()` escalates to a warning after 90 days. Unknown models return
`known_model=False` rather than a confident `$0.00`.

### 1.2 SDK version drift

`google-genai` is at 2.20.0, a major version past the documentation available for
it. Rather than coding against recollection, the SDK was installed and
introspected. This caught one concrete trap: `types.HttpOptions.timeout` is in
**milliseconds**, not seconds — a silent 1000× timeout error had it been assumed.

### 1.3 Contradictions between the two source blueprints

| Blueprint said | Conflict | Resolution |
|---|---|---|
| Every agent calls `guardrails.check(action)` before executing | Final spec §2 says agents never execute tools | `check()` returns a decision object consumed by the gateway |
| The tracer records what the agent "thought" | §20/§41.3 forbid logging chain-of-thought | Structured events only; reasoning-shaped keys dropped structurally |

### 1.4 Duplicated enforcement

§7 listed budget and rate limits as policy rules; §9 placed them in the gateway
pipeline *after* policy. Implementing both would violate §32 ("sem lógica crítica
duplicada") and create two enforcement points that drift apart.

**Change** — the policy engine is the sole decision authority and *consults*
budget and rate-limit services.

### 1.5 LangGraph justified on stronger grounds than given

The blueprint left the choice open. Kept, for a specific technical reason:
`interrupt()` + checkpointer is a genuinely tamper-proof confirmation mechanism —
state persists outside the model's reach and resumption carries an
application-supplied `Command(resume=...)`. A hand-rolled pending-confirmation
flag would be strictly weaker.

### 1.6 Structural change to the file tree

`RiskLevel`, `Decision` and `AgentName` were specified under `tools/models.py`
but are used by guardrails, cost, observability and evaluation. Moved to
`agent_platform/models.py` to keep the dependency graph acyclic.

---

## 2. Security findings

Defects found and fixed during implementation. Each has a regression test.

### 2.1 Budget was never checked before a model call (high)

The policy engine checked budget before **tool** execution only. Tools here are
free simulations; **model calls** are what cost money. An agent that looped
without ever proposing a tool would have run completely unmetered.

**Fix** — `CostTracker.authorize_call()` is invoked before every model call, in
`BaseAgent._generate`. A denial raises `BudgetExceededError`, which ends the
request. Found by `test_exhausted_budget_blocks_before_any_model_call`.

### 2.2 Declining a confirmation re-asked the same question (medium)

A declined action fell through to the validator, failed validation, and was
re-proposed by the retry loop — asking the reviewer again after they had said no,
and spending budget doing it.

**Fix** — a dedicated `after_confirm` edge routes a decline straight to the
response node, and `after_validate` never retries a `denied` or `declined`
result. Retrying a refusal cannot succeed.

### 2.3 Destructive requests silently degraded into reads (medium)

"Delete order 1001" produced `status=success` with order details, because the
destructive tool was not offered to the executor and the proposal fell back to a
lookup. Nothing unsafe happened, but **the platform reported success for an
action it never performed** — worse than an honest refusal.

**Fix** — destructive intent always surfaces as a proposal so the policy engine
is seen to refuse it (`PL005`). Case `reg-003`.

### 2.4 Portuguese injection and destructive verbs missed (medium)

Two separate gaps: the injection pattern only matched English word order
("previous instructions"), missing Portuguese noun-adjective order ("regras
anteriores"); and destructive keywords listed only infinitives (`apagar`), so
`apague` fell through.

**Fix** — the injection pattern gained branches for both orderings; keyword lists
use verb stems. Case `adv-007`.

### 2.5 PII pattern corrupted key material and broke idempotency (medium)

The Brazilian phone pattern matched an 11-digit run *inside* any longer digit
sequence. It reported `order 1234567890123456` as a phone number and rewrote
parts of API keys, which also made sanitisation non-idempotent — a second pass
produced different output from the first.

**Fix** — `(?<!\d)` / `(?!\d)` lookarounds. Found by
`test_long_order_numbers_are_not_reported_as_cards` and
`test_sanitize_is_idempotent`.

### 2.6 Metrics double-counted and mislabelled security events (medium)

Two reporting defects that would have put wrong numbers on the security
dashboard:

- `confirmations` summed `policy_decision` across all event types, counting the
  same verdict two or three times.
- `injection_flags` counted every flagged input, so **an email address in a
  support request was counted as a prompt-injection attempt**.

**Fix** — counters scoped to `event_type = 'policy_decision'`; `INPUT_FLAGGED`
now covers injection only, with a separate `INPUT_SENSITIVE` event for PII and
credentials.

### 2.7 Drift threshold was float-sensitive (low)

Two dimensions each dropping by exactly 0.05 were classified differently, because
`0.92 - 0.87` and `0.96 - 0.91` are not the same float. Thresholds sit on round
numbers, so this would have been hit constantly.

**Fix** — comparisons use an epsilon; tolerance is inclusive.

### 2.8 Read-only questions were forced through the executor (low)

Every researcher route continued to the executor, which then had to invent an
action for a question that only wanted an answer — producing spurious high-risk
proposals. Fixed by ending read-only routes after research. Case `reg-002`.

---

## 3. Residual risks

Accepted, and documented in `threat-model.md`:

1. No authentication — anyone with local access can approve a confirmation.
2. In-process rate limiting and confirmation state; neither survives a restart.
3. Injection detection will miss novel phrasings. **Designed for**:
   `test_authorisation_holds_when_detection_fails_completely` disables detection
   and asserts the platform still refuses.
4. The tool timeout cannot cancel a running thread. Acceptable for in-memory
   simulations; not acceptable for real tools.
5. Cost figures depend on a manually verified rate card.

---

## 4. Verification

| Check | Result |
|---|---|
| `pytest` | 216 passed |
| `ruff check` | clean |
| `mypy` (strict) | clean, 55 source files |
| `pip-audit` | no known vulnerabilities |
| `agent-platform eval --strict` | 68/68, safety 1.0 |
| Dashboard | verified in-browser, including the approve/decline flow |
| Secret scan | no real credentials; fixtures are synthetic |

Test distribution: 5 unit modules, 2 integration, 4 security. All fifteen
mandatory security checks from §29 are covered.

---

## 5. Dependencies

| Package | Version | Why |
|---|---|---|
| `langgraph` | 1.2.11 | Orchestration; `interrupt()` for tamper-proof confirmation |
| `google-genai` | 2.20.0 | Gemini provider |
| `pydantic` | 2.13.4 | Tool argument schemas; `extra="forbid"` as a security control |
| `python-dotenv` | 1.x | Local configuration |
| `streamlit` | 1.62.0 | Dashboard (optional extra) |

Dev only: `pytest`, `ruff`, `mypy`, `pip-audit`. Nothing was added for
appearances.

---

## 6. Feature status

**Implemented and tested** — multi-agent graph, policy engine (11 rules),
two-gate least privilege, fingerprinted confirmation, risk model, input/output
security, central sanitisation, dated cost model with budget guards, rate
limiting, 68-case evaluation with deterministic scoring, drift monitoring,
structured tracing, SQLite + in-memory persistence, 8-page dashboard, CLI.

**Partially implemented** — the LLM judge is built but cannot run without an API
key, so relevance is reported as unavailable rather than estimated. The Gemini
provider is complete but has only been exercised against the stub; no live call
has been made, and the code says so rather than claiming otherwise.

**Architecture only, not built** — PostgreSQL, additional providers, distributed
rate limiting, authentication/RBAC, real tool integrations, durable queues,
external observability, managed secrets.

---

## 7. Production roadmap

1. Durable checkpointer so pending confirmations survive a restart.
2. Authenticated approvers with an audit trail of who approved what.
3. Redis-backed rate limiting for multi-process deployment.
4. Out-of-process tool execution so timeouts can actually terminate work.
5. PostgreSQL behind the existing repository interface.
6. OpenTelemetry export instead of a local events table.
7. Automated rate-card refresh, so cost accuracy stops depending on a manual date.
8. A live-model evaluation baseline — the current 68/68 measures the platform,
   not the model.

---

# Phase 2 audit — live Gemini integration

Date: 2026-08-28 · google-genai 2.20.0 · Python 3.12.13

Phase 2 made the Gemini path real, expanded the dataset, and separated platform
evaluation from model evaluation. As in Phase 1, the findings below are what the
work actually turned up, not a feature list.

## 8. Pre-implementation verification

**Model and pricing re-verified** against
<https://ai.google.dev/gemini-api/docs/pricing> on 2026-08-28. The rate card was
unchanged from the 2026-08-27 check. Default model changed from
`gemini-3.5-flash` to **`gemini-3.5-flash-lite`**: every model call this platform
makes is a short, schema-constrained decision, which is precisely the Flash-Lite
profile — free tier, lower latency, and a quarter of the paid-tier cost.
`gemini-3.7-flash` is documented as the upgrade path and needs no code change.

**SDK capabilities introspected rather than assumed.** `ThinkingConfig`
(`thinking_budget`, where `0` disables) and the full `FinishReason` enum were
read off the installed package.

## 9. Findings

### 9.1 The live-test marker did not do what it said (medium)

`pyproject.toml` declared the `live` marker as "deselected by default", but
`addopts` was only `-q --strict-markers`. Nothing deselected anything. Harmless
while zero live tests existed; the moment Phase 2 added one, the default suite
would have required a key and network access.

**Fix** — `addopts = "-q --strict-markers -m 'not live'"`. Verified: the default
run reports `300 passed, 14 deselected`.

### 9.2 Thinking tokens could starve the output budget (high)

Current Gemini Flash models are thinking models, and thinking tokens are billed
and counted as **output**. With no `thinking_config` set and
`max_output_tokens=1024`, a call could spend its entire budget thinking and
return **empty text** with `finish_reason=MAX_TOKENS`. For a platform that only
ever asks for a few dozen tokens of JSON that is a pure failure mode — and it
would have surfaced as "the model returned invalid JSON".

**Fix** — thinking disabled by default (`GEMINI_THINKING_BUDGET=0`), output
ceiling raised to 2048, both configurable. A model that rejects the thinking
field is probed once and then has it dropped for the provider's lifetime, rather
than failing every call.

### 9.3 Provider failures escaped the graph entirely (high)

The most serious finding of Phase 2. `platform._invoke` caught only
`BudgetExceededError` and `GraphRecursionError`. Every `LLMError` — unreachable,
timeout, safety block, truncation, empty response — propagated out of
`graph.invoke()` uncaught. The consequences:

1. the request crashed with a raw traceback;
2. no request record was persisted, so the failure was invisible to metrics;
3. the **unsanitised** exception message reached the caller.

The stub never fails, so this was invisible until the live path existed. On a
real provider it would have fired on the first timeout or safety block.

**Fix** — two layers. `platform._invoke` handles `LLMError` into a recorded,
sanitised failure. Separately, `BaseAgent._generate` normalises *any* unexpected
provider exception into a typed `LLMUnavailableError` at the trust boundary —
deliberately not a blanket catch further up, which would also have swallowed
genuine bugs in the graph nodes.

Covered by 24 tests asserting that six failure modes and eight hostile output
shapes all produce zero tool executions and zero policy allows.

### 9.4 A 200 response could carry no usable content (medium)

Safety blocks, token-ceiling truncation and empty candidates all returned
successfully and then failed in `extract_json` with a misleading "model output
did not contain a JSON object".

**Fix** — `llm/errors.py` classifies each into `LLMSafetyBlockedError`,
`LLMTruncatedError` and `LLMEmptyResponseError`. The truncation error names the
thinking-token count and points at the setting that fixes it. Safety blocks
record the category in the trace but return a generic refusal, so the provider's
classification of the user's input is not echoed back.

### 9.5 User credentials would have been transmitted to the vendor (high)

Input assessment *flagged* secrets, but the original text still went into the
prompt. Locally that was inert. Against a hosted model it meant a user pasting an
API key would have sent it to Google.

**Fix (decision L1)** — `guardrails/egress.py`, applied at the single model-call
choke point. Credentials are stripped unconditionally. PII is masked only where
masking cannot break the work: `send_email` needs a real recipient, so email is
preserved while CPF, card, phone and IP are removed. Blanket-masking would have
been theatre — it would have broken the workflow while still sending the
surrounding text off-box.

The control applies to the stub as well as to Gemini, because a control that
engages in only one mode is one that gets discovered broken in the mode that
matters. Only redaction *categories* reach the trace, never values.

### 9.6 Drift baselines were comparable across providers (medium)

A baseline captured under the stub could be compared against live results. The
two differ in quality and latency by construction, so the comparison would have
reported a dramatic regression describing neither.

**Fix** — baselines are keyed `name::provider::model`. Cross-provider comparison
is now structurally impossible: the lookup finds nothing, and the CLI and
dashboard explain why.

### 9.7 The stub answered a customer-history question about the wrong order (low)

"Which orders does customer 2005 have?" matched the generic `order` keyword,
returned a single unrelated order, and reported success — confidently, and about
a different customer.

**Fix** — plural `orders` alongside a customer reference routes to
`list_customer_orders`. Same class of bug as Phase 1's §2.3: answering something
adjacent and calling it success.

### 9.8 Response rendering did not know the new tools (low)

`list_customer_orders` and `get_ticket` output fell through to
`str(output)[:800]`, dumping raw dictionaries at the user. Fixed with explicit
rendering branches.

## 10. What changed

**Created (9)** — `llm/errors.py`, `guardrails/egress.py`, `tools/dataset.py`,
`tests/unit/test_gemini_provider.py`, `tests/unit/test_dataset.py`,
`tests/security/test_prompt_egress.py`,
`tests/security/test_provider_failure.py`, `tests/live/` (package +
`test_gemini_live.py`), `docs/live-evaluation.md`.

**Modified** — `llm/gemini.py` (rewritten), `llm/provider.py`, `llm/stub.py`,
`llm/__init__.py`, `config.py`, `cost/pricing.py`, `cost/tracker.py`,
`agent/base.py`, `platform.py`, `tools/fake_tools.py`, `drift/monitor.py`,
`evaluation/evaluator.py`, `observability/events.py`, `persistence/` (protocol
and both backends), `cli.py`, `dashboard/app.py`, `pyproject.toml`,
`.env.example`, and four documents.

**Dependencies — none added.** `faker` was considered and rejected: it generates
plausible names that read as real people, which cuts against "no real personal
data", and hand-authored data is more deterministic. `vcrpy` was rejected
because recorded cassettes of a live API need scrubbing and rot silently; a
faked client gives the same determinism with less machinery.

## 11. The dataset

12 customers, 15 products, 40 orders (50 line items), 28 shipments, 18 tickets,
12 knowledge-base articles. Digest `db512de8207f751e`, pinned in the test suite.

Deterministic by construction — no RNG, no clock, no environment lookup.
Referential integrity is asserted, including the consistency the tools rely on
(unfulfilled orders have neither tracking nor a shipment).

**Deliberately absent: phone numbers, national ID numbers and street
addresses.** Not because they would be hard to fake, but because no tool
consumes them, and a public demo dataset is a poor place to practise storing
identifiers you have no use for. Synthetic CPF and card values live in the test
suite, where PII detection is exercised — which is where they belong.

## 12. Verification

| Check | Result |
|---|---|
| `pytest` | **300 passed, 14 deselected** |
| `pytest -m live` (no key) | **14 skipped** — safe to run anywhere |
| `ruff check` | clean |
| `mypy` (strict) | clean, 58 source files |
| `pip-audit` | no known vulnerabilities |
| `agent-platform eval` | 68/68, safety 1.0 |
| `agent-platform eval --live` (no key) | correctly **refuses**, exit 2 |
| Baseline / drift | provider-scoped; cross-provider comparison refused |
| Dashboard | verified in browser: mode badge, per-provider cost, scoped drift, stub-honesty notes |
| Secret scan | no real credentials; fixtures synthetic |

New test coverage: 22 Gemini provider (faked SDK), 24 provider failure, 18
prompt egress, 15 dataset, 14 live (skipped).

## 13. What was NOT verified at the time of writing

> **Superseded.** This section recorded the state before a key was available.
> Live verification has since been performed -- see §15. It is kept as written
> because the gap it describes was real, and because §15 is only meaningful
> against it: two of the five items listed below turned out to be actual bugs.

**No live API call was ever made.** No `GEMINI_API_KEY` was configured in the
build environment, so the entire Gemini path is exercised only against a faked
SDK client. Specifically unverified against the real service:

- that the configured model accepts the `response_schema` dicts used for
  routing, action proposal and validation;
- that `thinking_config` is accepted by this model, and that disabling it
  behaves as the SDK documents;
- that real `usage_metadata` populates as the code expects;
- real latency, and whether safety filters block any legitimate support query;
- whether a real model's routing and tool-selection quality is adequate.

The faked-SDK tests pin the platform's *handling* of each of these, which is the
part that is ours to get right. They cannot confirm the API behaves as
documented.

To close the gap, configure a key and run:

```bash
pytest -m live
```

```bash
agent-platform eval --live
```

The live suite skips rather than fails without a key, so this is safe to run at
any time.

## 14. Residual risks after Phase 2

Phase 1's five accepted risks stand. Added:

1. **Prompt content reaches a third party.** Egress redaction removes
   credentials and unused PII categories; the remaining question text does leave
   the machine. Inherent to using a hosted model.
2. **Vendor retention is not characterised.** Outside this project's control.
3. **Live behaviour is unproven.** See §13.
4. **Rate-card accuracy still depends on a manual check.** Verified 2026-08-28;
   a staleness warning fires after 90 days.

---

## 15. Live verification against the real Gemini API

> **Historical (Phase 2), not re-verified.** Recorded before finding F12
> (§26) showed that a live test asserting a negative could pass without
> reaching the model. Treat the result below as unverified rather than as
> current evidence; the current live status is §32.

Performed once a key was configured in the local environment. Model:
`gemini-3.5-flash-lite`, confirmed present in the account's `models.list()`.

**Result: 13/13 live smoke tests pass** — after fixing two defects that the
faked-SDK tests could not have caught. Both are recorded below, because they are
the concrete argument for why live verification was worth doing at all: §13
listed five things a fake could not confirm, and two of them turned out to be
broken.

### 15.1 `thinking_budget=0` is rejected by this model (high)

`gemini-3.5-flash-lite` refuses a thinking budget of `0` with:

```
400 INVALID_ARGUMENT. {'error': {'code': 400,
 'message': 'Request contains an invalid argument.', 'status': 'INVALID_ARGUMENT'}}
```

`thinking_budget=-1` (automatic) is accepted. This model permits *automatic*
thinking but not *disabled* thinking.

Isolated by probing each generation-config field independently against the live
API — `temperature`, `max_output_tokens`, `http_options`, `system_instruction`
and the JSON mime type all pass; only `thinking_config` with budget `0` fails.

**Why the existing probe did not save us.** `GeminiProvider` already had a
capability probe that dropped `thinking_config` and retried once. It triggered
on the substrings `"thinking"` / `"thought"` in the error message — but the API
**does not name the offending field**. The message is entirely generic, so the
probe never fired and *every* call failed permanently.

**Fix** — the probe now triggers on the presence of the thinking config in the
failed attempt rather than on message text:

```python
except genai_errors.ClientError as exc:
    if self._thinking_supported:
        self._thinking_supported = False
        last_error = exc
        continue
```

This masks nothing. If the request was malformed for any other reason it fails
again on the retry and is raised then. The cost of disambiguating is one call,
once per provider instance.

Note the consequence for cost: thinking cannot be disabled on this model, so
thinking tokens are billed as output on every call. They are already counted
correctly (`_usage` adds `thoughts_token_count` to the output total), and
`LLMTruncatedError` still catches the case where thinking exhausts
`max_output_tokens`.

Regression test: `test_bare_400_triggers_the_thinking_probe`.

### 15.2 The action schema constrained arguments to an empty object (high)

Every tool proposal came back with the right tool and **no arguments**:

```
action_proposed  researcher  get_order
policy_decision  researcher  get_order  deny
  Invalid arguments: arguments rejected by schema (order_id: Field required)
```

The model was choosing correctly and then being refused by `PL004`.

**Cause.** The schema declared `"arguments": {"type": "OBJECT"}` with no
properties. Structured output constrains a model to *exactly* what the schema
declares, and an object with no declared properties is an object that may
contain nothing. The stub ignores schemas entirely, so this was invisible until
a real model was constrained by one.

Three shapes were probed against the live API:

| Schema shape | Model output |
|---|---|
| `arguments: OBJECT` (no properties) | `{"tool": "get_order", "arguments": {}}` |
| `arguments_json: STRING` | `{"tool": "get_order", "arguments_json": "{\"order_id\": \"ORD-1001\"}"}` |
| no `response_schema` at all | `''` (empty) |

**Fix** — arguments are declared as a JSON **string** and decoded by
`agent.base.parse_proposed_arguments`, which accepts both shapes: the
`arguments_json` string from a schema-constrained provider, and the plain
`arguments` dict from the stub. Anything unparseable yields `{}`, which the
policy engine then rejects on the tool's real pydantic schema rather than
executing with partial input.

Structured output is therefore retained rather than abandoned, which matters:
dropping `response_schema` was the third option probed, and it produced empty
output.

Regression tests: `test_parse_proposed_arguments_accepts_both_shapes` (10
cases) and `test_action_schema_declares_arguments_as_a_string`.

### 15.3 Verified end-to-end flow

Full trace, real model, from the live run:

```
 1 request_started
 2 agent_started      router
 3 llm_call           router        <- Gemini
 4 route_selected     router        -> researcher
 5 agent_started      researcher
 6 llm_call           researcher    <- Gemini
 7 action_proposed    researcher  get_order
 8 policy_decision    researcher  get_order  allow  low
 9 tool_call          researcher  get_order  allow  low
10 request_completed
```

Response: *"Order ORD-1001 is shipped, total R$ 429.7, tracking BR100100001."*

Also confirmed live: the confirmation gate held for `send_email`
(`awaiting_confirmation` -> approved -> executed), and `delete_record` was
refused.

### 15.4 Observed latency and cost

**Latency is far higher than the stub suggests, and the docs previously implied
nothing about it.**

| Measurement | Value |
|---|---|
| Single read-only request, end to end | **~53 s** |
| Single request under throttling | up to ~62 s |
| 13-test smoke suite | ~400 s (6m 41s) |

A request makes two to three sequential model calls (router, researcher, and
executor where applicable), and free-tier throttling adds substantially to each.
This is not a platform defect, but it is the number to quote: the demo's
sub-100 ms figures are stub latency and say nothing about live behaviour.

Real cost per call, provider-reported (`tokens_estimated=False`):

| Agent | in | out | cost |
|---|---:|---:|---:|
| router | 203 | 11 | $0.00008840 |
| researcher | 950 | 34 | $0.00037000 |
| executor | 1911 | 75 | $0.00076080 |

Roughly $0.001 per full request at paid-tier rates. The free tier charges
nothing.

### 15.5 Transient failures, and how the platform handled them

The API returned `503 UNAVAILABLE`, `429 RESOURCE_EXHAUSTED` and read timeouts
during heavier stretches of the session. In every case the platform behaved as
designed: three bounded retries, then a **recorded, sanitised failure with zero
tool executions**. No request partially executed, and no raw traceback escaped.

This is the T14 containment property (§9.3) holding on the live path rather than
against a fake.

### 15.6 Key containment on the live path

Audited after live traffic:

| Surface | Key present |
|---|---|
| Responses | no |
| Event payloads and errors | no |
| Request rows | no |
| `llm_calls` rows | no |
| `Settings.describe()` | no |
| 142 files on disk, binaries and SQLite included | **only `.env`** |

`.env` is git-ignored. The key was never printed, logged or echoed at any point
in the session.

### 15.7 Final state

| Check | Result |
|---|---|
| Deterministic suite | **313 passed, 14 deselected** |
| Live smoke (`-m "live and not slow"`) | **13 passed** |
| ruff | clean |
| mypy (strict) | clean, 58 source files |

Deterministic coverage grew from 300 to 313: 13 new regression tests pinning the
two live findings.

### 15.8 Still not run

`agent-platform eval --live` — the curated 15-case live evaluation. Held back
deliberately; the smoke suite was the approved scope. Live *quality* numbers for
routing and tool selection therefore remain unmeasured, and the platform makes
no claim about them.

---

# Phase 3 audit — adversarial security, abuse limits, QA

Date: 2026-08-28 · red-team and QA pass over the Phase 2 platform.

Six vulnerabilities found. Five were located by reading the code before writing
any test; the sixth was found the hard way, by a test harness leaking a live
credential into terminal output.

## 16. Vulnerabilities found and fixed

### 16.1 F1 — the rate-limit rule was unreachable (Medium)

`PL011` consulted `rate_limiter.check(context.request_id)`. `request_id` is
unique per request, so the bucket it addressed was **always empty**: the rule
could never fire. Meanwhile `platform.run()` consumed quota on the global
bucket. Two different buckets, one of which nothing ever filled.

Entry-point enforcement still worked, so this was a dead defence-in-depth layer
rather than an open door — but it was dead, and the dashboard implied otherwise.

**Root cause.** A unit test that acquired *and* checked with the same
hand-chosen key. It was internally consistent and validated nothing about the
production wiring.

**Fix.** `policy.py` now calls `check()` against the global bucket.

**Regressions.** `test_f1_rate_limiter_acquire_and_check_share_a_bucket` and,
more importantly, `test_f1_pl011_fires_through_the_real_platform_wiring`, which
goes through `AgentPlatform.run()`. The lesson generalised: this phase added
integration-level abuse tests precisely because unit-level ones cannot see
wiring mistakes.

### 16.2 F2 — pending confirmations never expired and were unbounded (Medium)

`_pending` was a plain dict cleared only on the success path. An interrupted
request that was never confirmed stayed resident for the process lifetime, and
stayed **resumable forever** — an approval could execute against state that had
moved on hours earlier.

**Fix.** `PendingRegistry`: bounded (`MAX_PENDING_CONFIRMATIONS=50`) and
expiring (`CONFIRMATION_TTL_SECONDS=900`). When the store is full of live
entries a new suspension is **refused rather than evicting an existing one** —
eviction would let an attacker push a legitimate pending action out of the
store, whereas refusal fails closed because the refused request simply does not
execute.

**Regressions.** `test_pending_entries_expire`,
`test_f2_expired_confirmation_never_executes`,
`test_pending_store_is_bounded_and_refuses_rather_than_evicting`,
`test_pending_store_full_refuses_the_request_without_executing`.

### 16.3 F3 — no wall-clock deadline (Medium)

`recursion_limit` bounds *steps*, not time. At the old limit of 25 steps and a
30s model timeout, a single request could theoretically run ~12 minutes.

**Fix.** `REQUEST_DEADLINE_SECONDS=180`, enforced by `ResourceGuard` before
every model call and every tool call. `recursion_limit` also tightened 25 → 15,
since real flows use about six nodes.

Notably, re-entering a request after a confirmation resume does **not** restart
the clock: a suspended request must not win itself a fresh time budget by being
suspended (`test_resuming_does_not_grant_a_fresh_deadline`).

### 16.4 F4 — call ceilings absent; the budget could not bound a free provider (Medium)

The budget guard caps *cost*. The deterministic stub costs exactly `$0`, so
under a purely monetary limit a runaway loop was free and therefore unbounded.
This is the specific reason resource limits are denominated in calls, seconds
and bytes rather than dollars.

**Fix.** `MAX_LLM_CALLS_PER_REQUEST=12` and `MAX_TOOL_CALLS_PER_REQUEST=8`,
charged *before* the call that would exceed them.

**Regression.** `test_f4_call_ceiling_holds_when_cost_is_zero` runs against the
free stub, where the budget cannot help.

### 16.5 F5 — no byte ceiling on tool output (Low-Medium)

`max_context_items` bounds how many results reach the context, not how large
each is. One oversized result would inflate every subsequent prompt and every
persisted trace.

**Fix.** `MAX_TOOL_OUTPUT_BYTES=32768`. An oversized payload is **replaced, not
truncated** — half a JSON document is not a smaller document, it is a corrupt
one, and feeding a model a malformed fragment is worse than telling it plainly
that the result was too large.

### 16.6 F6 — the API key was printed by a dataclass repr (**Critical**)

Found in practice, not in theory. A live adversarial test failed, and pytest's
assertion introspection rendered the `Settings` fixture into the failure
output — **printing the live API key in cleartext**.

**Root cause.** `Settings` is a dataclass, and a dataclass `repr` includes every
field. Any traceback, log line, debugger frame or test failure that rendered a
`Settings` object would have leaked the key. The platform had careful redaction
for traces, prompts, responses and provider errors, and none of it applied to
the object's own `repr`.

This is the most instructive finding of the phase: every deliberate egress path
was defended, and the leak came through an incidental one nobody had modelled.

**Fix.** `gemini_api_key` is declared `field(repr=False)`, plus an explicit
`__repr__`/`__str__` rendering `gemini_api_key=<configured>`.

**Regressions.** `test_settings_repr_never_contains_the_key`,
`test_no_credential_bearing_object_leaks_through_repr` (sweeps the objects a
traceback is most likely to render), `test_gemini_provider_does_not_repr_its_key`.

> **Operational note.** The key was exposed in this session's terminal output
> before the fix. It should be rotated at
> <https://aistudio.google.com/apikey>; the fix prevents recurrence but cannot
> un-print what was already shown.

### 16.7 A latent precedence bug in the circuit breaker (Low)

Caught by mypy during implementation. `cooldown - (clock() - opened_at or 0.0)`
parses as `cooldown - ((clock() - opened_at) or 0.0)`, so the `or 0.0` guarded
nothing: a `None` would have raised rather than falling back. Safe only by
accident of the calling branch. Replaced with an explicit assert stating the
invariant.

## 17. ResourceGuard architecture

`security/resources.py`. A **precondition service, not an authorisation
mechanism** — the distinction is enforced structurally, not by convention:

* It has exactly one verb, *stop*. Every method returns `None`, a counter, or a
  truncation boolean, or it raises. **None returns a decision.**
* `test_resource_guard_grants_nothing` asserts the public surface contains no
  `evaluate`, `authorize` or `allow`, and pins the exact method set.
* The policy engine is never given a reference to it, and never consults it.
* A request that clears every limit has been granted nothing; it has merely not
  been stopped.

Placement:

| Control | Enforced at | Ordering |
|---|---|---|
| LLM-call ceiling, deadline | `BaseAgent._generate` | before the provider call |
| Tool-call ceiling, deadline | `ToolGateway.submit` | **after** the policy ALLOW |
| Output byte ceiling | `ToolGateway._bound_output` | after execution, before context |

The tool-call charge sits *after* the policy decision deliberately: it can then
only ever subtract from what policy permitted, and can never turn a `DENY` into
an execution.

**Fails closed throughout.** A caller that skips `begin()` gets
`ResourceLimitExceeded`, not an unmetered request
(`test_guard_fails_closed_when_accounting_was_never_started`).

**Abuse scoring was evaluated and deliberately not built.** The approved plan
allowed it only if it could be made deterministic and safe. Hard per-request
ceilings plus rate limiting plus the circuit breaker already close every abuse
path in the matrix, and a score introduces stateful cross-request judgement —
the exact shape of thing that tends to grow into an authorisation mechanism by
accident. The simpler controls were preferred, as the plan permitted.

## 18. Circuit breaker

`llm/circuit.py`. Three states, deterministic via an injectable clock:

```
closed    -> calls flow; consecutive failures counted
open      -> fail fast until cooldown elapses        (threshold 5)
half_open -> exactly one trial call admitted          (cooldown 60s)
```

A failed trial reopens the circuit and restarts the cooldown, so a still-broken
provider is not probed in a tight loop. Concurrent callers in `half_open` are
refused, so a recovering provider is probed once rather than stampeded.

Like the guard, it can only *prevent* a provider call. An open circuit produces
exactly the same outcome as any other provider failure: a recorded, handled
failure with **no tool execution**
(`test_open_circuit_never_executes_a_tool`).

## 19. Resource configuration

Every value is read from `Settings`/environment with validated minimums, and
none can be derived from model output — asserted by
`test_no_resource_limit_can_be_influenced_by_model_output`, which feeds a
provider that emits limit-shaped fields and checks nothing moves.

| Setting | Default | Justification |
|---|---|---|
| `MAX_LLM_CALLS_PER_REQUEST` | 12 | Measured 2 calls typical, ~8 worst case with retries |
| `MAX_TOOL_CALLS_PER_REQUEST` | 8 | Measured 1 typical, ~4 with the retry ceiling |
| `REQUEST_DEADLINE_SECONDS` | 180 | ~3x the worst throttled latency observed |
| `MAX_TOOL_OUTPUT_BYTES` | 32768 | Largest current result is well under 8 KB |
| `RECURSION_LIMIT` | 15 (was 25) | Real flows use ~6 nodes |
| `MAX_PENDING_CONFIRMATIONS` | 50 | Single-operator demo |
| `CONFIRMATION_TTL_SECONDS` | 900 | A 15-minute-old approval should not execute |
| `CIRCUIT_FAILURE_THRESHOLD` | 5 | Above the 3-attempt retry policy, so one bad request does not trip it |
| `CIRCUIT_COOLDOWN_SECONDS` | 60 | Matches the free-tier per-minute window |

## 20. Test results

**417 offline tests pass. Deterministic across 8 consecutive runs.** The
default suite remains fully offline: no network, no key.

| Suite | Tests |
|---|---|
| `test_injection_advanced.py` | 35 |
| `test_abuse_limits.py` | 32 |
| `test_prompt_injection.py` | 26 |
| `test_provider_failure.py` | 24 |
| `test_policy_bypass.py` | 21 |
| `test_authorization.py` | 20 |
| `test_prompt_egress.py` | 18 |
| `test_confirmation_integrity.py` | 17 |
| `test_gateway_bypass.py` | 16 |
| `test_secret_leakage.py` | 16 |
| **Security total** | **225** |

Live, executed against the real API:

| Suite | Result |
|---|---|
| `test_gemini_adversarial.py` | **11/11 pass** |
| `test_gemini_live.py` (smoke) | **13/13 pass** |

> **Historical result, and not re-verified.** These predate finding F12
> (§26), which showed that a live test asserting a negative could pass
> without ever reaching the model. The guard that would have detected a
> vacuous pass did not exist when this table was written, so treat these
> numbers as unverified rather than as current evidence.

Not run, as agreed: the 15-case curated subset and the 68-case evaluation.

The live adversarial set includes four persuasion attacks a keyword stub cannot
simulate — appeals to authority and urgency, framing the refusal itself as the
bug, a roleplay wrapper, and claimed prior approval in prose. None reached a
destructive tool.

## 21. Latency, measured

Earlier documentation quoted ~53s from a single heavily throttled Phase 2
sample. Better measurement, 10 requests across two runs:

| Condition | Total | Model time | Platform |
|---|---|---|---|
| **Unthrottled** (n=5) | **~4.1 s median** | ~87% | ~500 ms |
| Throttled (n=5) | 8–88 s | ~20% | remainder is retry backoff |

Two model calls per read-only request; tool time is negligible (**< 1 ms**,
since tools are in-memory).

**A measurement caveat, stated because it matters.** The "platform" bucket is
computed as `total - llm - tool`, and only *successful* model calls emit an
`llm_call` event. Failed attempts and their retry backoff therefore land in the
platform bucket, which is why it appears to reach 80% under throttling. Real
platform overhead is the unthrottled figure: **~500 ms**, roughly 13%. Tracing
retry attempts would close this observability gap; it is not currently done.

## 22. Dashboard QA

All eight pages navigated and rendered without a server-side exception. The
Reliability page now exposes circuit state, the full resource-limit table,
requests in flight, pending confirmations and recent resource stops.

Scanned the rendered DOM — both visible text and raw HTML — for API keys,
bearer tokens, credential assignments, prompt markers, system-prompt text and
reasoning keys. **No hits in either.**

## 23. Security invariants, re-verified

1. No agent executes a tool; the gateway is the only path. *(16 tests)*
2. The policy engine is the sole authorisation authority. Neither the resource
   guard nor the circuit breaker can grant permission. *(structural + tests)*
3. Model output is data. It cannot set risk, claim approval, or alter a limit.
4. Credentials never reach a provider, a trace, a database row, a response —
   **or an object repr**. *(F6)*
5. Every limit fails closed and comes only from configuration.
6. A provider failure never results in a tool execution. *(24 tests)*
7. A confirmation authorises one exact action, once, for a bounded time.

## 24. Residual risks after Phase 3

1. **The exposed key needs rotation** (§16.6). The fix prevents recurrence, not
   retroactive disclosure.
2. **Injection detection still misses obfuscated payloads** — base64, ROT13,
   leetspeak. This is by design: `test_obfuscated_injection_cannot_reach_a_destructive_tool`
   asserts the tool never runs, without asserting the signal fires.
3. **Rate limiting is a single global bucket.** One caller can exhaust the quota
   for all. Correct for a single-operator demo; a multi-tenant deployment needs
   per-principal buckets.
4. **No tenant model.** `confirm()` is authorised by request id alone. Ids are
   unguessable, which is the whole of the control.
5. **Retry attempts are not traced**, so the latency decomposition attributes
   backoff to platform overhead (§21).
6. **In-process state.** Resource counters, the circuit and pending
   confirmations do not survive a restart or span processes.

---

## 25. Phase 4 — production hardening and portfolio QA

Phase 4 was an autonomous hardening pass over the whole project: security
review of paths the earlier phases had not exercised, a page-by-page dashboard
review, code-quality gates, and a pre-publication audit.

Four findings, all reproduced before being fixed and all pinned by regression
tests written *before* the fix.

### F7 — local filesystem paths reached the user (Medium)

A tool handler that raised an exception had `str(exc)` interpolated into
`ToolResult.error`, which reaches the user-facing response. Credentials in that
text were already stripped by `secure_output`, but **paths were not**, so a
failure inside a tool disclosed the operating user's name and the machine's
directory layout:

```
C:\Users\<username>\OneDrive\Documentos\...
```

Two changes, because one gate was not enough:

* `redact_home_paths()` in `security/sanitization.py` replaces user-home
  prefixes with `<HOME>`, wired into `sanitize_text()` and `secure_output()`.
  Only the prefix is replaced — the tail is what makes an error readable, and
  it carries nothing personal. System paths (`/usr`, `C:\Windows`) are
  deliberately left alone.
* `tools/gateway.py` sanitises exception text **at the source**. Relying on the
  response boundary alone meant any other consumer of `ToolResult.error` — a
  caller, a future surface — still received raw text.

### F8 — combining marks evaded injection detection (Medium)

Input is normalised with NFKC, which leaves combining marks in place. A single
mark inserted inside a keyword breaks every injection pattern while remaining
visually identical to a reader:

```
ign̄ore all previous instructions   →  not flagged
ignore all previous instructions    →  flagged
```

`fold_for_detection()` decomposes to NFKD and drops combining marks, and is
applied **only to pattern matching**. `normalized_input` — the text the agent
actually reads — stays NFKC, because folding it would strip legitimate
Portuguese accents, turning "não" into "nao" in real user content to defend
against an attack that only concerns matching. Input that required folding
raises an `input.folded` signal.

This is defence in depth, not a barrier. Injection detection is a signal; the
policy engine and the gateway are what actually contain an attack.

### F9 — the dashboard rendered tracebacks with absolute paths (Medium)

Streamlit renders unhandled exceptions itself, so that output never passes
through `secure_output` and no amount of platform-side redaction could reach
it. Reproduced on Streamlit 1.62 with a page that raises:

```
File "C:\Users\<username>\...\page.py", line 3, in <module>
```

Streamlit 1.62 also attaches "Ask Google" and "Ask ChatGPT" buttons to that
output, which would forward the path to a third party.

The only place to close this is Streamlit's own configuration, so the project
now ships `.streamlit/config.toml`:

| Option | Value | Why |
| --- | --- | --- |
| `client.showErrorDetails` | `type` | Keeps the exception class, drops the traceback and every path |
| `client.toolbarMode` | `viewer` | Hides deploy / rerun / clear-cache from readers |
| `browser.gatherUsageStats` | `false` | A demo of controlled execution should not phone home |
| `server.maxUploadSize` | `1` | The dashboard reads a local database and needs no uploads |

Verified end-to-end in a browser: the traceback and the path are gone, and the
failure is still *announced* rather than silently swallowed.

### F10 — the rate-limit refusal bypassed output sanitisation (Low)

`RateLimiter` fails closed, but its failure branch built the refusal reason as
`f"rate limiter unavailable: {exc}"`, and `platform.run` interpolates that
reason straight into the user-facing response and returns **before** the output
pipeline runs. An exception raised inside the limiter therefore reached the
caller unsanitised.

Exploitability is low — it requires the limiter itself to fail — but the fix is
the same lesson as F7: sanitise at the source rather than trusting a downstream
gate that this path does not cross. `_failure_reason()` now sanitises the text
and keeps the exception *type*, which is what makes the failure diagnosable and
carries no payload.

Every other early-return response path was checked for the same class:
`BudgetExceededError` and `ResourceLimitExceeded` carry internally generated
messages built from numeric configuration, so they were left unchanged.

### Dashboard QA

All eight pages were reviewed against an empty database and a seeded one.

* **`page_security` had no empty state.** On a fresh database it rendered four
  zeroes and two empty tables, which reads as "nothing was detected" rather
  than "nothing has run yet" — very different claims on a security page. It now
  explains itself and lists the rules that *would* be enforced.
* **The empty-state hint was wrong on two pages.** `no_data()` appended
  "Seed the database with `agent-platform demo`" unconditionally, but `demo`
  records no evaluation run and no drift baseline. On those pages the hint told
  the reader to run a command that could not fix what they were looking at. The
  hint is now a parameter.

Browser automation could not reliably drive Streamlit's sidebar radio, so the
click-through was replaced with `streamlit.testing.v1.AppTest`. That turned a
one-off manual pass into 26 permanent tests covering every page in both states,
including that demo mode is disclosed on all eight.

### Code quality

| Gate | Result |
| --- | --- |
| `ruff check` | clean |
| `mypy` (strict, 59 files) | clean |
| `pip-audit` | no known vulnerabilities |
| Leftover probe scripts / TODO / FIXME | none |
| Broad `except Exception` handlers | 9, all reviewed; all fail-closed boundaries |

`ruff format` is deliberately **not** applied. The project configures
`[tool.ruff.lint]` only, and the formatter would rewrite 36 files as a purely
cosmetic diff.

### Pre-publication audit

A credential scan across all 110 project files (Google API keys, bearer
tokens, AWS keys, private-key blocks, generic secret assignments) plus a
row-level scan of the SQLite database found:

* the configured key in `.env` **and nowhere else**;
* zero credential hits in the database;
* all other matches confined to synthetic fixtures in `tests/security/` and
  `evaluation/datasets/sensitive_data.json`, where fake credentials are the
  point of the test.

`.gitignore` already excluded `.env`, `data/*.db` and every cache. `.claude/`
was added, and `.streamlit/` deliberately left tracked because it is now a
security control rather than local preference.

### Test results

| Suite | Result |
| --- | --- |
| `tests/unit/` | 154 passed |
| `tests/integration/` | 63 passed, 1 skipped by design |
| `tests/security/` | 254 passed |
| **Total (deterministic)** | **471 passed, 1 skipped, 25 deselected** |
| Determinism | identical across 5 consecutive runs, randomised ordering |

Coverage grew 417 → 471: 22 for F7/F8, 4 for F10, 3 for F9's configuration, and
26 dashboard rendering tests (of which 3 replaced no earlier coverage at all —
the dashboard previously had none).

### Live verification — not run

The live suites could **not** be executed in this phase. The key in `.env` is
the one exposed in F6, and it has since been revoked, so the provider rejects
every call:

```
400 INVALID_ARGUMENT  API key not valid. Please pass a valid API key.
```

This is the correct outcome for F6 — the exposed key is dead — and the platform
handled it exactly as designed: every request failed cleanly with a normalised
provider error, no crash, and no key material in the message. But it means
**13/13 live smoke and 11/11 live adversarial are results from Phase 3, not
re-verified here.** They are not claimed as current.

Live verification resumes once a replacement key is configured.


---

## 26. Live verification round (key rotated)

The key exposed in F6 was revoked and replaced. This round re-ran the live
suites against the replacement and produced two findings, both fixed.

### Key rotation, verified without disclosure

Presence and format were checked by boolean assertion only — no value, no
digest, no `repr` of any object that could carry it. The replacement key is
valid: it authenticates, and four real generation calls succeeded before the
project's daily quota ran out.

### F11 — the credential detector did not recognise current Gemini keys (Medium)

Google is migrating from *standard* API keys (`AIza…`, 39 characters) to
*authorization* keys (`AQ.…`). Every key created in AI Studio now defaults to
the new format, and the API will reject standard keys outright by September
2026 ([API key docs](https://ai.google.dev/gemini-api/docs/api-key)).

`security/secrets.py` matched only the legacy shape. Reproduced directly: a
legacy key was detected as `google_api_key`, while the real configured key was
not detected at all and survived `redact_secrets` untouched.

Impact is bounded but real. The *configured* key was never at risk — it is
redacted by value through `redact_known_values`, which is format-agnostic and
was verified against the real key across traces, egress, output and error
paths. The gap was the net for credentials of *unknown* origin: a key pasted
into a request, or returned by a tool, would not have been recognised.

Both formats are now matched. The `AQ.` pattern carries a length floor so it
cannot swallow ordinary prose, pinned by a false-positive test.

### F12 — live security tests passed without reaching the model (Medium)

The more serious finding, and it was invisible until the provider failed.

Most live security tests assert a negative: *no destructive tool ran*, *no
safety block occurred*, *no prompt text was persisted*. When the provider is
unavailable — quota exhausted, key revoked, outage — nothing runs, so every
one of those assertions is satisfied trivially. The suite reported **11/11
adversarial passing while the API was answering nothing but 429**.

Two of them accepted `"failed"` as a valid status outright, so a provider
error was an explicit pass.

This is false assurance: precisely when the platform is least verified, the
tests are greenest. Fixed with `require_live_model()`, which checks for a
successful `llm_call` event and **skips** rather than passes when the model was
never reached. The check strengthens the assertions; it does not relax any
control.

The effect is immediate and visible: the same run that reported 11/11 green now
reports 6 passed, 11 skipped with an explicit reason, 7 failed on quota.

Earlier phases' live results should be read with this in mind. Any live run
recorded while quota was constrained may have included vacuous passes; the
guard now makes that impossible to miss.

### Live results this round

Quota (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`, 500/day) was
exhausted for the project partway through the run. **Quota is per Google Cloud
project, not per key, so rotating the key does not reset it.**

| Outcome | Count | Meaning |
| --- | --- | --- |
| Passed | 6 | Genuinely verified without needing a successful generation |
| Skipped | 11 | Would have passed vacuously; now correctly reported as unverified |
| Failed | 7 | Blocked by the daily quota, not by a defect |

`13/13 smoke` and `11/11 adversarial` are therefore **not** claimed for this
round. They remain Phase 3 results, and the F12 guard means a future run
cannot report them unless the model was actually reached.

The `slow` curated subset was not run, by instruction.

### Secret containment, verified against real live traffic

The database held real traces produced with the live key.

| Surface | Scanned | Key or fragment found |
| --- | --- | --- |
| SQLite (7 tables) | 171 rows / 2 492 cells | none |
| Project files | 111 files | none outside `.env` |
| `secure_output` | live key probe | blocked |
| Prompt egress | live key probe | absent |
| `sanitize_text` / `sanitize` | live key probe | absent |

Fragment matching was used as well as exact matching, because a truncated key
is still a leak — and truncation is exactly how one escapes a character clamp.

### Gates

| Gate | Result |
| --- | --- |
| `pytest` | 477 passed, 1 skipped, 25 deselected |
| `ruff check` | clean |
| `mypy` (strict, 59 files) | clean |
| `pip-audit` | no known vulnerabilities |
| Dashboard, live mode | 8/8 pages render, correct LIVE banner, no key in output |

Suite totals: unit 154, integration 63 (+1 skipped by design), security 260.


---

## 27. Offline hardening round

Run while the Gemini daily quota was exhausted, so no live call was made. Two
findings, both reproduced before being fixed, plus two test defects that a
shuffled run exposed.

### F11 and F12 are recorded in §26; F13 is new here.

### F13 — tool output reached the model unfenced (Medium)

The platform wraps user input in an `UNTRUSTED_USER_CONTENT` fence so the model
can tell data from instructions. It did not do the same for **tool output**.

Reproduced by capturing the executor prompt directly:

```
payload reached executor prompt : True
payload fenced as untrusted     : False
user input IS fenced            : True
```

In the same prompt, the user's sentence was fenced and a poisoned
knowledge-base article was interpolated beside it as trusted narration — giving
the attacker the *more* privileged of the two channels. In a real deployment
tool output is exactly as attacker-influenceable as user input: ticket bodies,
CRM notes, scraped pages, database rows.

Two sites were affected: the executor's context block and the tool result shown
to the validator's judge. The researcher was already safe, because its prompt is
built before any tool runs.

Fixed with `fence_tool_output()` and a distinct `UNTRUSTED_TOOL_OUTPUT` marker —
a parallel fence rather than a second mechanism, labelled accurately so the
model knows *why* the content is untrusted. Marker stripping now covers every
marker, so content cannot close its own fence.

**Scope, stated honestly:** this is defence in depth, **not** a privilege
boundary. The policy engine remains the sole authority, and a persuaded model
still cannot reach a tool it is not authorised for. That claim is asserted by a
separate test so the two never blur together.

### Two test defects, found by shuffling

pytest has no built-in random ordering and no ordering plugin was installed, so
the earlier claim of "randomised ordering" was simply wrong — the suite had only
ever run in file order. An opt-in, replayable shuffle now exists
(`PYTEST_SHUFFLE_SEED`), added to the harness rather than as a dependency.

It found two real defects immediately:

1. **Dashboard tests were order-dependent.** `dashboard/app.py` caches its
   platform — and therefore its database connection — with
   `@st.cache_resource`. That cache is process-global and outlives an `AppTest`
   run, so the second test silently reused the first test's database and
   ignored `DATABASE_PATH`. Sequentially the empty-database tests happened to
   run first, so they passed. Under a shuffled order the seeded tests could run
   first, and then the "empty" database was not empty. The app is correct; the
   tests were not isolating it.

2. **A new test polluted the shared dataset.** `KB_ARTICLES` is the source
   constant, not one of the mutable working copies, so `reset_dataset()` does
   not restore it. The project's own dataset-digest guard caught this, which is
   exactly what it is for.

### Concurrency

A dedicated suite now covers what sequential tests structurally cannot. Threads
synchronise on a barrier so they arrive at the contended call together;
no assertion depends on timing.

| Property | Result |
| --- | --- |
| Rate limiter over-admission under contention | none — quota is exact |
| `check()` consuming quota | no |
| Per-request resource counters bleeding | none |
| Call ceiling under concurrent charges | held |
| Circuit breaker losing a failure record | none |
| Pending registry exceeding its bound | none |
| Same pending action delivered twice | never — exactly one caller |
| Request/trace id collisions | none |
| Events misattributed across requests | none |
| Cross-request confirmation | refused |
| Gateway `ContextVar` leaking across threads | no |
| Simulated dataset coherence (960 concurrent writes) | intact |
| Torn reads of a record mid-update | none |
| SQLite losing concurrent writes | none |

Two things make the unlocked simulated dataset safe, and both are now pinned by
tests: single-key dict assignment is atomic under CPython's GIL, and reads
`deepcopy` the record. An earlier probe appeared to show 30 000 torn reads;
that was a faulty assertion in the probe itself, not a defect — `get_order`
returns `{"found", "order"}` and the probe was checking the wrong level.

### Dashboard

All eight pages now render under every state a reader can land on: empty
database, seeded database, stub mode, live mode, provider failure, resource
ceiling, policy denial and pending confirmation. Each is asserted to leak no
traceback, no local path, no credential, no system prompt, no model reasoning
and no fence marker.

### Packaging

`dashboard/app.py` imports `pandas` directly, but `pandas` was declared only in
the mypy override list — the dashboard extra worked purely because Streamlit
happens to pull it in. Now declared.

### Gates

| Gate | Result |
| --- | --- |
| `pytest` | 521 passed, 1 skipped, 25 deselected |
| Order independence | identical across 8 distinct shuffle seeds |
| `ruff check` | clean |
| `mypy` (strict, 59 files) | clean |
| `pip-audit` | no known vulnerabilities |

Suite totals: unit 154, integration 83 (+1 skipped by design), security 284.

---

## 28. Publication readiness

> **Status: READY FOR PUBLICATION.** Every area below is met, and the live gate
> was executed on 2026-08-29 (§32) with zero failures and no product defect.
>
> Two things this status does *not* say, because they would be false:
> `13/13 smoke` and `11/11 adversarial` were not achieved -- the observed
> result is 21 passed, 3 skipped, 0 failed -- and the three skips remain
> skips. They are not counted towards readiness; readiness rests on every
> security *property* having at least one passing live test, which it does.

### OFFLINE TESTS
626 passed, 1 skipped by design, 25 live tests deselected. Identical across
shuffled seeds. **Ready.**

### SECURITY
Fourteen findings across five phases (F1–F14), each reproduced before being
fixed and each pinned by a regression test written first. The central invariant
— no agent executes a tool; the policy engine is the only authority — is
enforced structurally and verified under concurrency. **Ready.**

### CONCURRENCY
Sixteen tests over the rate limiter, resource guard, circuit breaker, pending
registry, request isolation, `ContextVar` scoping, the simulated dataset and
SQLite. No shared-state defect found. **Ready.**

### DASHBOARD
Thirteen pages × eight states, asserted free of tracebacks, local paths,
credentials, system prompts and model reasoning. Streamlit's own error
rendering is constrained by `.streamlit/config.toml`, which is a security
control in this project and not a preference file. **Ready.**

### SECRETS
No credential outside `.env` anywhere in the tree: 121 files, 7 SQLite tables,
binaries included, matched on fragments as well as whole values.
Synthetic fixtures are classified as synthetic by evidence in the value itself
— entropy, sequential runs, literal markers — not by a path allowlist.
**Ready.**

### DOCUMENTATION
Every numeric claim in the README was checked against the code: resource
ceilings, circuit-breaker thresholds, dataset digest, tool table, golden-case
counts and the curated subset size all match. Test counts corrected. The
earlier "randomised ordering" claim was false when written and is now both true
and reproducible. **Ready.**

### LIVE VERIFICATION
**Executed 2026-08-29 (§32). Complete, with three cases unverified.**

| Suite | Passed | Skipped (F12) | Failed |
| --- | --- | --- | --- |
| Smoke | 11 | 2 | 0 |
| Adversarial | 10 | 1 | 0 |
| **Total (24)** | **21** | **3** | **0** |

**Zero failures. No 429 occurred. No product defect was found.** Twenty of the
twenty-four tests demonstrably reached Gemini; the twenty-first pass is a
rate-card check that needs no model call, and the three skips are the F12 guard
recording that no successful model call happened.

`13/13 smoke` and `11/11 adversarial` are **not** claimed, and the three skips
stay skips. Converting them would be the exact failure F12 exists to prevent.

Every security property was verified against the real model: secret egress
(four tests), policy enforcement, the confirmation gate, injection reaching no
destructive tool, resource ceilings, and the absence of prompt or completion
text in traces. Each property left unverified by a skip is independently
covered by a passing live test, so no *property* is unverified even though
three *cases* are.

### KNOWN LIMITATIONS
Unchanged and documented in the README: in-process rate limiting, an in-memory
checkpointer, a thread-based tool timeout that cannot forcibly cancel a
handler, no authentication, and simulated tools throughout. The pre-F12 live
results from earlier phases should be treated as unverified, because the guard
that would have caught a vacuous pass did not exist when they were recorded.

Three live cases remain unverified after the completed round (§32), and the
provider-side reason was deliberately not investigated: doing so would have
consumed additional quota outside the procedure. The properties themselves are
covered by other passing live tests.


---

## 29. Live round, 2026-08-28 16:03 BRT — blocked by quota

Executed against the procedure in `docs/live-verification.md`. **Partially
completed**: the free-tier daily quota was already spent when the round began,
so only a trickle of calls succeeded.

### Observed

| Suite | Passed | Skipped (F12) | Failed |
| --- | --- | --- | --- |
| Smoke (13) | 4 | 3 | 6 |
| Adversarial (11) | 3 | 8 | 0 |
| **Total (24)** | **7** | **11** | **6** |

Every one of the six failures carried `429 RESOURCE_EXHAUSTED`,
`GenerateRequestsPerDayPerProjectPerModel-FreeTier`, limit 500. No failure had
any other cause. Per the procedure this is recorded as an **external blockage**,
not converted into a pass, and no retry was forced.

### The real model was reached

Not a full round, but not nothing either. These passed against the live API and
required a genuine response to do so:

- `test_provider_reports_real_token_usage` — real token accounting from the
  provider, which a stub cannot fabricate
- `test_credentials_never_reach_the_provider` — outbound prompt inspected on
  the live path
- `test_api_key_appears_nowhere_after_a_live_run` — traces, events and response
  audited after real traffic
- `test_credentials_never_transmitted_on_the_live_path` — same property,
  adversarial suite
- `test_api_key_absent_from_everything_after_live_traffic`
- `test_resource_limits_hold_on_the_live_path` — the call ceiling held on a
  live request
- `test_model_is_in_the_rate_card`

**Secret egress is therefore verified against the real provider.** That was the
security-critical item, and it passed.

### F12 did its job

Eleven tests were **skipped with an explicit reason** rather than passing.
Before the guard existed, this same run would have reported the adversarial
suite as **11/11 green** while the API was answering mostly 429s. The suite now
reports 3 passed and 8 unverified, which is the truth.

This is the clearest evidence the guard was worth adding.

### Provider failure handling, verified incidentally

The six quota failures exercised the failure path for real. Every one produced
a clean `LLMUnavailableError` normalised at the trust boundary, a request status
of `failed`, zero tool executions, zero policy allows, and no credential in any
error message. That is the behaviour the deterministic suite asserts, confirmed
here against a real provider fault rather than a simulated one.

### Not verified this round

- structured output against the live schema
- real proposal / tool-selection behaviour under a live model
- a latency sample

### Quota note

The quota resets at midnight US/Pacific and had already been consumed by
12:03 PDT the same day. The two live suites cost only ~52 calls of the 500
available, so the consumption is coming from somewhere other than this
procedure. Worth checking before the next attempt, otherwise the round will
block again.

### Gates after the round

| Gate | Result |
| --- | --- |
| `pytest` | 521 passed, 1 skipped, 25 deselected |
| `ruff check` | clean |
| `mypy` (strict, 59 files) | clean |
| `pip-audit` | no known vulnerabilities |
| Secret audit | 115 files, 7 tables, 2 492 cells — no credential outside `.env` |

### Status

`13/13 smoke` and `11/11 adversarial` remain **unclaimed** for this round. The
outstanding gate at the time was external: a quota window large enough to
complete it.

> **Superseded by §32.** That window arrived on 2026-08-29 and the round was
> completed. This section is kept as written, because the contrast between a
> quota-blocked round and a completed one is the clearest evidence that F12
> reports honestly in both.


---

## 30. Quota consumption investigation (offline only)

No provider call was made during this investigation. Everything below was
established by static analysis and by mocks.

### Entry-point map

Every path that can produce a real Gemini call:

| Entry point | Calls | Automatic? |
| --- | --- | --- |
| `agent-platform ask` | 1 request (2–3 model calls) | no |
| `agent-platform demo` | 10 requests (~25 calls) | no |
| `agent-platform eval` | 15 or 68 cases (~30 / ~136 calls) | no — requires authorisation |
| Dashboard **Try a request** button | 1 request per click | no |
| `tests/live/` | 24 tests (~52 calls) | no — deselected by default |
| Offline suite | **0** | — |

Nothing calls the provider automatically. There is no warm-up, no health check,
no scheduler, and no module-level call. The only background executor is in the
tool gateway and runs *tool* handlers, never model calls.

### The offline suite makes zero real calls — measured

The SDK's `Models.generate_content` was replaced with a raising stub for a full
run:

```
REAL generate_content ATTEMPTS FROM OFFLINE TESTS: 0
```

An earlier version of the check guarded client *construction* instead and
reported 25 attempts. Those were false positives: constructing a client issues
no request, and the provider unit tests legitimately build one with a fake key
before substituting a fake. Guarding the HTTP-bearing method is the correct
level.

### Dashboard pages make zero calls on render

All eight pages, instrumented against the stub: **0 model calls each**. The
dashboard only reaches a model when the *Try a request* button is pressed.

### F14 — a quota error cost two calls instead of one (Medium)

The real amplifier, and it is self-reinforcing.

`gemini.py` ran its "thinking probe" on **any** 4xx raised while a thinking
budget was attached. The probe exists for a genuine reason: the live API
rejects an unsupported `thinking_budget` with a bare
`400 INVALID_ARGUMENT` that does not name the offending field, so the only way
to disambiguate is to retry once without it.

Applying that to every 4xx was wrong in both directions. Measured with mocks,
one logical call:

| Error | Before | After |
| --- | --- | --- |
| `429 RESOURCE_EXHAUSTED` | **2 calls** | 1 |
| `400 API_KEY_INVALID` | **2 calls** | 1 |
| bare `400 INVALID_ARGUMENT` | 2 calls | 2 (probe preserved) |
| `503 ServerError` | 3 calls | 3 (retry preserved) |

Two consequences:

1. **Quota amplification.** Every call made against an exhausted quota spent
   *two* units of that quota. The platform burned its remaining allowance twice
   as fast at exactly the moment it could least afford to.
2. **Silent, lasting degradation.** The probe also set
   `_thinking_supported = False` for the life of the provider. A single
   transient 429 — a per-minute burst, not a daily exhaustion — therefore
   convinced the provider that the model has no thinking budget, on the
   evidence of an error that never mentioned thinking.

Fixed with `_may_be_a_thinking_rejection()`: the probe now runs only on a 400
`INVALID_ARGUMENT` that does not name its own cause. A 429, a 403, or a 400
that says "API key not valid" are all self-explanatory and are raised on the
first response.

One pre-existing test asserted the old behaviour — that a *named* key error
still cost two calls. It was updated to assert the stronger property (one
call), not relaxed: the invariant it tested, "a 4xx is not retried", still holds
and now holds more tightly.

### Was F14 the cause of the exhaustion?

Honestly: **partly, not wholly.** F14 doubles the cost of every *failed* call,
so it accelerates exhaustion sharply once errors begin — but it cannot create
the first 500 successful calls. Those came from the live suites and CLI runs
performed during development, which are manual and were run repeatedly.

What the investigation does establish is that no automatic or hidden consumer
exists: the offline suite, the dashboard pages, imports, threads and fixtures
all consume zero.

### New safeguard

An autouse fixture in `tests/conftest.py` now replaces the SDK's
`generate_content` for every test **not** marked `live`, so an offline test
that reaches the network fails immediately with an explanatory message rather
than quietly spending quota. Discipline was not enforcing this: a fixture that
forgets to clear `GEMINI_API_KEY` silently promotes an offline test to a live
one and nothing in the output says so.

The guard is itself covered by tests — a safeguard nobody checks is
indistinguishable from no safeguard.

### Gates

| Gate | Result |
| --- | --- |
| `pytest` | 530 passed, 1 skipped, 25 deselected |
| Order independence | identical across 6 shuffle seeds |
| `ruff check` | clean |
| `mypy` (strict, 59 files) | clean |
| `pip-audit` | no known vulnerabilities |
| Secret audit | 114 files — no credential outside `.env` |
| Real provider calls this round | **0** |


---

## 31. LIVE readiness audit (offline)

No provider call was made. Everything below was established by reading the
implementation and by measuring against the stub.

### The finding that mattered

**The model never writes the answer.** All four model call sites -- router,
researcher, executor, validator -- are schema-constrained *decisions*.
`_compose_response()` and `_summarise_output()` build the user-facing text
deterministically from recorded state.

The consequence for the demo is counter-intuitive and worth stating plainly:
**enabling LIVE does not make the answers more fluent.** It changes which tool
is chosen, not how the result is phrased. Anyone expecting "turn on Gemini and
the demo gets conversational" would have been wrong, and would have discovered
that only after spending quota.

The consequence for security is the good half of the same trade: a model that
cannot author the reply cannot hallucinate a tracking number into it, and every
F7-F14 protection applies identically in both modes.

### The gap that was real

No tool resolved a **customer name**. Every read took an identifier, so
"what is happening with Ana Ribeiro's order?" -- an ordinary question -- had no
path to an answer. In LIVE a model's only options were to guess an id or fall
back to the knowledge base; both fail, one of them confidently.

`find_customer(name)` closes it. Scope was chosen from the architecture rather
than from ambition:

* **Read-only**, `READ_DATA`, MEDIUM risk -- the same reach as `get_customer`.
* **Returns the customer's orders in the same call**, because the graph performs
  a single research hop. A two-hop design would have required changing the
  graph to be useful, which is not a change this justified.
* **Bounded**: capped at five matches, and a blank or punctuation-only term
  matches nothing rather than everything. That bound is a resource control, not
  formatting.
* **Exposes no new fields** beyond what `get_customer` already returns, asserted
  by test.
* Registered in the registry, subject to the policy engine, guarded by
  `require_gateway()`, and absent from the router's reach.

Two existing inventory guards -- the capability matrix assertion and the
gateway-guard sample set -- failed the moment the tool was added, which is
exactly what they are for. Both were updated by declaring the new tool, not by
relaxing the assertion.

### Call economy, measured

| Request | Model calls |
| --- | --- |
| Read-only lookup (by ID or by name) | 2 |
| Action proposed (confirmation, denial, injection) | 3 |

F14 re-verified with mocks after the change: 429 costs 1 call, a named 400
costs 1, an ambiguous 400 spends its single probe, and 503 still retries.

### STUB / LIVE parity

Both modes now route a named person to `find_customer`. The stub matches a
proper noun rather than consulting the customer list, so it does not acquire a
dependency on the data it stands in for -- a real model would not have the
table memorised either.

### Gates

| Gate | Result |
| --- | --- |
| `pytest` | 626 passed, 1 skipped, 25 deselected |
| Order independence | identical across 3 shuffle seeds |
| `ruff` / `mypy` strict / `pip-audit` | clean |
| Secret audit | 118 files, no credential outside `.env` |
| Real provider calls | **0** |
| Dataset digest | unchanged |


### Addendum: write-intent routing (found during the final UX pass)

The router's English write words were whole words while its Portuguese ones
were stems, so "deleting" and "updating" matched nothing. "Try deleting order
ORD-1001" was therefore classified as a lookup and answered with an order
summary. No security hole -- nothing ran, and the policy engine was never
asked to permit anything -- but the *security demonstration silently did not
happen*, which for a portfolio demo is its own kind of failure.

Fixed by matching stems in both the router and the tool keywords, the way the
Portuguese entries always did. The conservative rule for genuine questions was
deliberately left alone: "Can you delete order ORD-1001?" still routes to the
read-only path, because an ambiguous request should not default to the path
that changes things.

Pinned by `tests/security/test_write_intent_routing.py`, which asserts both
halves -- inflected imperatives reach the action path, and questions do not.


---

## 32. Live verification round, 2026-08-29 04:19 BRT — completed

Executed against `docs/live-verification.md`, one pass, no improvised calls and
no retries. Quota had reset at the documented midnight-Pacific boundary.

### Results

| Suite | Passed | Skipped (F12) | Failed |
| --- | --- | --- | --- |
| Smoke (`test_gemini_live.py`, non-slow) | 11 | 2 | 0 |
| Adversarial (`test_gemini_adversarial.py`) | 10 | 1 | 0 |
| **Total (24)** | **21** | **3** | **0** |

**Zero failures. No `429` at any point.** The availability probe was run once
and succeeded; it was not repeated.

`13/13 smoke` and `11/11 adversarial` are **not** claimed. The numbers above are
the numbers observed.

### What reached the model

**20 of 24** tests demonstrably reached Gemini. Of the 21 passes, exactly one --
`test_model_is_in_the_rate_card` -- is a pure rate-card check that requires no
model call. The remaining three tests are the F12 skips, which by definition did
not reach it.

### Verified against the real model

| Property | Result |
| --- | --- |
| Secret egress | **PASS ×4** -- credentials never reach the provider; the key is absent from traces, events and responses after real traffic |
| Policy enforcement | **PASS** -- a real model proposing a forbidden tool was refused |
| Dangerous actions | **PASS** -- destructive request refused; the confirmation gate held |
| Prompt injection | **PASS ×3** -- persuasive attacks reached no destructive tool |
| Resource limits | **PASS** -- the per-request call ceiling held on the live path |
| Trace hygiene | **PASS** -- no prompt or completion text persisted |
| Provider safety filter | **PASS** -- ordinary support work was not blocked |
| Structured output, token accounting, thinking budget | **PASS** -- real schema-constrained responses and real token counts |

### The three skips

`test_injection_cannot_reach_a_destructive_tool` and
`test_traces_carry_no_prompt_or_completion_text` (smoke), and
`test_persuasive_injection_cannot_reach_a_destructive_tool[0]` (adversarial).

They are **SKIP, and they stay SKIP.** Each asserts a negative, so a run in
which the model was never reached would satisfy it for free. That is precisely
the false green F12 was built to stop.

Two things were established **offline, at no quota cost**:

* the same payloads *do* produce model calls under the deterministic stub
  (3, 3 and 2 calls respectively), so this is not an architectural block that
  happens before the model;
* the platform's own rate limiter is ruled out -- `build_evaluation_settings`
  raises the ceiling to 10 000 requests per minute for these suites.

The cause is therefore provider-side. The most plausible explanation is that
Gemini's own safety filter refused those injection prompts, since `llm_call` is
emitted only after a successful response -- but **this was not confirmed, because
confirming it would have required additional provider calls**, which the
procedure forbids. It is recorded as undetermined rather than guessed at.

Materially: **every property left unverified by a skip is independently covered
by a passing live test.** Injection-reaching-no-destructive-tool is covered by
the three passing `persuasive_injection` cases and by
`test_real_model_proposing_a_forbidden_tool_is_refused`; trace hygiene is
covered by `test_no_prompt_or_completion_text_is_persisted_live`. No security
property is unverified, even though three cases are.

### F12 and F14

**F12 intact and decisive.** Eight call sites, unchanged. It withheld a pass on
three tests that would otherwise have gone green without exercising anything.
This is the first round in which the guard both allowed a large majority of
passes *and* refused a minority -- which is what a working guard looks like.

**F14 intact.** Twelve targeted tests pass. The availability probe carried no
thinking configuration, so the F14 path was not exercised during this round.

### Post-round gates

| Gate | Result |
| --- | --- |
| `pytest` (offline) | 626 passed, 1 skipped, 25 deselected |
| `ruff` / `mypy` strict / `pip-audit` | clean |
| Secret audit, after live traffic | 121 files -- no credential outside `.env` |
| Real provider calls from offline tests | **0** |

### Quota

One probe, 13 smoke, 11 adversarial -- within the measured ~52-call budget of
500 per day. The 15-case and 68-case evaluations were **not** run.

### Conclusion

The gate's intent -- verify the security properties against a real model, with
no false greens -- is satisfied. Zero failures, zero defects, every property
covered by at least one passing live test, and three honest skips that the
guard refused to launder into passes.


---

## 33. Core provider safety budget

A daily ceiling on **physical** calls to the provider, enforced in the core so
that every consumer is subject to it. No provider call was made while building
or verifying it.

### Why the existing controls did not cover this

Each was doing its own job correctly; none of them was this job.

| Control | Counts | Why it does not bound the day's quota |
| --- | --- | --- |
| Cost tracker | money | At the measured $0.00051/call, the $1.00 daily budget binds at ~1 960 calls -- roughly four times past the point where the 500-call quota is already gone |
| Rate limiter | inbound requests | 100/hour, but each request becomes 2-3 provider calls |
| Resource guard | one request | Bounds a single request, not a day |
| Circuit breaker | consecutive failures | Reacts to failure, not to steady success |

### Where it lives, and why there

Immediately before the SDK call inside `GeminiProvider`'s retry loop -- the one
line every *physical* attempt passes through.

A wrapper around `generate()` was rejected: it sees one logical call and cannot
see the retries inside it, which is exactly when quota burns fastest.

Two facts drove the placement, both read from the code rather than assumed:

* retries and the F14 thinking probe are additional physical calls that no
  higher layer observes;
* the `llm_call` **event is emitted only on success**, so a request that
  retried twice records one event while spending three calls, and a failed
  request records none while spending up to three. Phase 1's dashboard budget,
  which counts those events, therefore *undercounts precisely when the platform
  is burning the most quota*. That gap is why this phase exists.

### The ceiling

**400 calls per day**, against an observed free tier of 500.

The reserve is not decoration: the quota is per Google Cloud project and shared
with anything else using it, and a budget set at the ceiling protects nothing --
it merely predicts the failure it should have prevented. 400 also leaves room
for the live verification round (~52 measured) after a full day of demo use.

The limit is a module constant, **not** an environment variable. A ceiling a
user can raise from the environment is a suggestion, not a control.

### Ordering against the circuit breaker

Unchanged, and deliberately so:

```
ResourceGuard.charge_llm_call      per request   ceiling on one request
CircuitBreaker.before_call         per logical   don't call a failing provider
  provider.generate()
    [per physical attempt] budget  per attempt   don't exceed the day
      SDK call
```

The breaker belongs where it is: its question is "is this provider worth
calling at all", which is answered once per logical call. The budget's question
is "can the day afford another physical call", which must be answered per
attempt. Putting the budget last -- closest to the resource it protects --
means nothing can slip between the decision and the request.

### Concurrency

`try_consume()` performs its read and write inside one `BEGIN IMMEDIATE`
transaction, so two callers who both see "one left" cannot both be granted it:
the second blocks until the first commits, then reads the updated count. A
thread lock guards this process; SQLite's transaction guards the others.

Verified with 32 threads released from a barrier against a limit of 20 --
exactly 20 grants.

### What it refuses to do

It has one verb. There is no `allow`, and it accepts no argument from a caller
asserting exemption: an exemption is a bypass with better manners. Live
verification draws on the same allowance as everything else, which is why the
ceiling sits below the free tier rather than at it.

`ProviderBudgetExhausted` subclasses `LLMError` so existing handling still
catches it, but is a distinct type so exhaustion is never reported as a
provider outage. The provider is fine; we chose not to call it.

### Two tiers, and why that is not decoration

| Tier | Limit | Counts | Authority |
| --- | --- | --- | --- |
| Core budget | 400/day | physical calls, incl. retries | **the ceiling** |
| Dashboard budget (Phase 1) | 300/day | successful `llm_call` events | early, friendlier stop |

The dashboard tier survives because it stops a *demo visitor* ~100 calls before
the hard ceiling, leaving the reserve intact for the live gate. It is not the
authority and is documented as approximate.

### Verified

| Property | Result |
| --- | --- |
| Below limit | call allowed |
| At limit | blocked, **zero** SDK calls |
| Retry charged | 3 attempts cost 3 |
| Retry cannot exceed | remaining 1 -> first attempt runs, retry refused |
| Shared across provider instances | yes |
| Durable across processes | yes |
| Daily reset | yes |
| 429 / named 400 | still one call (F14 preserved) |
| Ambiguous 400 probe | runs, and is charged |
| Concurrency (32 threads, limit 20) | exactly 20 |
| Exhaustion vs provider failure | distinct types |
| Exhaustion leaks credential | no |
| Stub provider | unaffected |

18 tests, written before the implementation and confirmed failing first.

### Residual risks

* **Charging is now verified; blocking is not.** The controlled observation in
  §36 watched the counter move `0 -> 4` on one real request, so the budget
  demonstrably charges physical calls against a live provider. It has still
  never been observed *refusing* a call, because the ceiling was never
  approached -- proving that would mean deliberately spending 400 calls, which
  is the opposite of what the budget is for. The refusal path remains verified
  only against a counting fake.
* **The store is local.** Two machines sharing one Google Cloud project keep
  separate counts; the ceiling is per deployment, not per project.
* A caller constructing `GeminiProvider` directly, rather than through
  `build_provider`, passes no budget and is unbounded. Every path in this
  repository uses `build_provider`.

### Gates

`pytest` 656 passed, 1 skipped, 25 deselected - `ruff` clean - `mypy` strict
clean (60 files) - real provider calls from offline tests: **0** - dataset
digest unchanged.


---

## 34. Interactive execution visualisation

The runner now shows what a request actually did, derived entirely from the
events it recorded. No provider call was made building or verifying it.

### The bug this replaced

The previous agent-activity panel marked an agent SUCCESS on an
`agent_completed` event. That event type is defined in `EventType` and is
**never emitted anywhere in the platform**. Every agent therefore rendered as
permanently RUNNING, including long after the request had finished.

It looked reasonable, which is why it survived several rounds of review. It is
also exactly the failure this phase existed to prevent: a confident claim about
the one thing the project exists to demonstrate.

### The truth model, read from real runs

Established by running the platform and reading the stream, not by assuming:

| Agent | Evidence | Note |
| --- | --- | --- |
| router | `agent_started` -> `llm_call` -> `route_selected` | always runs |
| researcher | ... -> `action_proposed` -> `policy_decision` -> `tool_call` | |
| executor | same shape | only when an action is required |
| validator | ... -> `validation` | **skipped entirely on a read-only route** |

Absence is information: an agent with no `agent_started` is reported
**NOT REACHED**, never omitted and never shown as idle-success.

### States

`NOT REACHED` · `RUNNING` · `WAITING` · `SUCCESS` · `BLOCKED` · `FAILED`

`NOT REACHED` is the one the old panel could not express, and it is the whole
point: "did not happen" and "happened and went well" are different claims.

### Observed output, unedited

```
What is the status of order ORD-1001?     outcome=SUCCESS
  SUCCESS      router
  SUCCESS      researcher
  NOT REACHED  executor
  NOT REACHED  validator
  TOOL SUCCESS get_order      low       -- executed

Update order ORD-1002 status to delivered outcome=WAITING
  WAITING      executor
  NOT REACHED  validator
  TOOL WAITING update_record  high      -- awaiting human approval; not executed

Delete order ORD-1001 immediately         outcome=BLOCKED  by policy engine
  BLOCKED      researcher
  BLOCKED      executor
  TOOL BLOCKED delete_record  critical  -- refused; never executed
```

Both `delete_record` proposals appear, because the researcher and the executor
each proposed it and each was refused. Collapsing them by tool name would have
hidden one of the two refusals, so tools are tracked per *proposal*.

### Distinguishing the four ways a request can stop

`policy engine` · `provider budget` · `circuit breaker` · `resource limit`
(plus `rate limit`) are reported separately and never blurred. A budget block
is a decision not to spend; a circuit trip is a reaction to failure; a resource
ceiling is arithmetic; a denial is a judgement. A request stopped by a control
is reported BLOCKED rather than FAILED -- it was prevented, not broken.

### Cost

Zero additional provider calls. The view is derived from events the request
already produced; nothing is generated to describe it.

### Verified

28 tests, written before the module and run against **real event streams** from
actually executing the platform, not against fixtures shaped to match the
renderer. They pin: executor NOT REACHED on a lookup, validator NOT REACHED
before approval, proposed-is-not-executed, denied tools never SUCCESS, and that
no prompt, fence marker or reasoning vocabulary reaches the view.

### Gates

`pytest` 684 passed, 1 skipped, 25 deselected - `ruff` clean - `mypy` strict
clean - `pip-audit` clean - determinism 2 seeds - offline provider calls **0** -
dataset digest unchanged - core budget 400 and `delete_record` allow-list still
empty.

### Not implemented, deliberately

No conversation memory, no new agents, no new events. `AGENT_COMPLETED` was
left unemitted rather than adding an event purely to satisfy a renderer: the
view was made to read the evidence that exists.

### Residual risk -- closed by observation

This section originally recorded that the view had been verified against
stub-produced streams only. That is **no longer true**: the controlled live
observation in §36 ran the derivation over a real Gemini-backed event stream
and produced output identical to the stub for the same request, including
`executor` and `validator` as NOT REACHED.

What remains unobserved live is the derivation over *failure* streams -- a
policy denial, a suspended confirmation, a budget block. Those are covered
offline against real stub streams, but no live run has produced them.


---

## 35. Recruiter demo / enterprise simulation

A UX phase over an architecture that was already correct. No provider call was
made, and `src/` was not touched.

### Audit first

The audit found the journey largely built and working, and I did not rebuild
it. Five gaps were genuine, each established by measuring the rendered page
rather than by reading the code:

| Gap | Evidence |
| --- | --- |
| Budget invisible in simulation mode | `if provider_info.live:` guarded the only display |
| No graded warning | binary: fine, then spent |
| Company never said what to test | rendered page: "What you can test" absent |
| Company omitted customers and products | only tickets and shipments were shown |
| Runner and scenarios never explained STUB vs LIVE | measured per page |

The first is the one that mattered. The provider budget is real engineering --
durable, atomic, charged per physical call -- and it was invisible to exactly
the audience meant to be impressed by it.

### Implemented

* **Budget visible in both modes** with `OK` / `RUNNING LOW` / `EXHAUSTED`,
  used, limit and remaining, on the landing page and the runner. Warning at
  four fifths spent. `status` is for display, `gating` is the decision, and
  they are deliberately separate: collapsing them would either hide the figures
  in simulation mode or block a demonstration that costs nothing.
* **Company** now carries all four entity families with a "What you can test"
  line each. The products entry states that the catalogue holds no stock
  levels, so no inventory capability is implied.
* **One shared STUB/LIVE explainer** on the runner and the scenarios, stating
  the honest version: the model decides route, tool and arguments; the wording
  of the answer is composed by the platform in *both* modes.

### An assertion I narrowed, and why

`test_no_page_leaks_internals` failed on the landing page for the word
"sqlite" -- which appears there because the page *disclaims* SQLite as a
production store. That is the honesty the project is built on, not a leak. The
assertion was mis-specified, so it was narrowed to what actually constitutes
leakage: paths, queries, connection strings and credentials. The check was made
sharper, not weaker.

### Numbers unchanged

Core budget 400, demo budget 300. Neither was raised, and no test was relaxed
to accommodate either.

### Verification

| Gate | Result |
| --- | --- |
| `pytest` | 716 passed, 1 skipped, 25 deselected |
| Determinism | identical across 2 shuffle seeds |
| `ruff` / `mypy` strict / `pip-audit` | clean |
| Secret audit | REAL 1 (`.env`), REVIEW 0 |
| Offline real provider calls | **0** |
| Dataset digest | `db512de8207f751e`, unchanged |
| `delete_record` allow-list | empty |
| Router tool reach | `()` |

### Environment note

Part-way through this phase Windows Application Control blocked the virtual
environment's copied `python.exe`. The base interpreter is signed and still
runs, so the suite was executed through it with the venv's `site-packages` on
`PYTHONPATH`. Package versions were checked identical (pytest 9.1.1, Streamlit
1.62.0) and the pre-change baseline reproduced exactly before any code was
written. This is an environment condition on this machine, not a project
change.

### Not implemented

No RAG, vector database, MCP, Kubernetes, conversation memory, new agents,
new tools, new events, dataset changes, or model-generated final prose. The
execution view from the previous phase was left alone apart from integration.


---

## 36. Controlled live observation, 2026-08-29

One logical request through the normal orchestrator path, to close the last
item that could only be settled against a real provider. No suite was run, no
probe repeated, no retry attempted, and nothing was changed as a result.

### What was executed

`"What is the status of order ORD-1001?"` -- a read-only lookup, chosen because
it is the cheapest request that still exercises routing, tool selection, the
policy engine and the gateway.

| | |
| --- | --- |
| Model | `gemini-3.5-flash-lite` |
| Logical requests | **1** |
| Physical provider calls | **4** |
| Successful `llm_call` events | **2** |
| Outcome | `success`, route `researcher`, tool `get_order` |
| Response | `Order ORD-1001 is shipped, total R$ 429.7, tracking BR100100001.` |

### The event stream, as recorded

```
 1 request_started     info
 2 agent_started       info      router
 3 llm_call            success   router
 4 route_selected      success   router
 5 agent_started       info      researcher
 6 llm_call            success   researcher
 7 action_proposed     info      researcher  get_order
 8 policy_decision     success   researcher  get_order  allow
 9 tool_call           success   researcher  get_order  allow
10 request_completed   success
```

No `retry` event was recorded.

### Execution view over the live stream

```
outcome: SUCCESS | blocked_by: None | policy: allow
  SUCCESS      router      classified the request
  SUCCESS      researcher  ran get_order
  NOT REACHED  executor    did not run for this request
  NOT REACHED  validator   did not run for this request
  tool SUCCESS get_order   risk=low -- executed
```

Correct for a read-only lookup, with no step invented. The output is
**identical to the stub run of the same request**, which is the substantive
result: the derivation reads recorded evidence and is indifferent to which
provider produced it.

### The budget charged physical calls, not logical ones

| Before | After | Delta | Limit | Remaining |
| --- | --- | --- | --- | --- |
| 0 | 4 | **4** | 400 | 396 |

This is the observation's most useful outcome. The event stream shows **2**
successful model calls; the budget charged **4**. A ceiling counting
`llm_call` events -- which is what the Phase 1 dashboard tier does -- would
have counted 2 and been wrong by 100% on this single request.

That gap is precisely the undercounting identified in §31 and fixed in §33 by
moving the ceiling to the physical call site inside the retry loop. The
observation confirms the reasoning was not theoretical.

### 4 physical calls for 2 logical calls: cause undetermined

**This is recorded as an open question, not an explained one.**

The extra calls happened below the event layer -- no `retry` event was
recorded, and the provider emits no event per physical attempt. Two mechanisms
in the code could produce additional attempts (the thinking-configuration probe
and the SDK-level retry on a transient fault), but **the observation cannot
distinguish between them**, and no attempt was made to do so: separating them
would require further live calls, which the observation's terms forbade.

Nothing here should be read as confirming the F14 probe as the cause. What is
established is the count, not its explanation.

### Security

No API key, key prefix, prompt text, completion text, reasoning vocabulary,
fence marker, SQL, connection string or filesystem path appeared anywhere in
the events or the rendered view. Seven checks, all clean.

### The stub is unaffected

The same request on the deterministic stub, with the real SDK entry point
replaced by a raising guard:

* real SDK calls attempted: **0**
* budget delta: **0**
* execution view: identical agent and tool states to the live run

Reaching the live ceiling therefore cannot break the offline demonstration.

### What this does *not* establish

* It is one request, on one route, with one outcome. Denials, confirmations
  and budget blocks were not exercised live.
* `13/13 smoke` and `11/11 adversarial` remain **unclaimed**; the three skips
  from §32 remain skips.
* The budget's *refusal* path is still unobserved against a real provider.

---

# V2.7 — the live gate, and CI

## §36 The 72-call incident

A phase that intended to run the offline suite spent 72 unintended provider
calls. The chain, established by reading the code rather than by inference:

1. `pyproject.toml` carried `addopts = "-q --strict-markers -m 'not live'"`.
   That deselection was the only load-bearing control.
2. The command run was `pytest -q -p no:cacheprovider -m "not docker"`.
3. pytest stores a single `markexpr` (`_pytest/mark/__init__.py`), so a `-m` on
   the command line **replaces** the one from `addopts`. The effective filter
   became `not docker`, and `not live` stopped applying.
4. 22 live test functions became eligible, one of them parametrised.
5. Both live modules called `load_dotenv()` at **import** time. Collection
   imports every module regardless of marker filters, so the developer's real
   key entered `os.environ` for the whole session before any fixture ran.
6. Their `pytestmark` skipped on *absence of a key* — so possessing one
   **enabled** the tests rather than gating them.
7. The conftest guard that blocks real SDK calls exempted anything marked
   `live`, by design. It opened for precisely the dangerous case.
8. The last check before the network was `budget.try_consume()` against a
   400-call daily ceiling. It answers "how many more?", never "may you at all?",
   so it would have stopped call 401 and not call 1.

Three controls, one decision. Remove the filter and the rest cooperate.

**Ledger effect:** `2026-08-31: 72`. No key, prompt or completion was
disclosed; the failure was expenditure, not leakage.

## §37 The structural fix

Authorisation is now a fact separate from the key, checked in
`build_provider()` — the one function every path to a real provider passes
through, and one that sits *below* pytest, so no marker expression, `-k`,
`--noconftest` or `-p no:…` reaches it. It also covers the callers that are not
tests at all: the CLI, the dashboard, and `scripts/build_vector_index.py`.

```
AGENT_PLATFORM_LIVE=i-authorise-real-provider-calls
```

Matched exactly. `1`, `true`, `yes` and every near-miss are refused, because
those are the values that arrive by accident in CI matrices and shell profiles.
It is deliberately **not** read through `python-dotenv`: a value that can arrive
from a file becomes ambient, which is exactly how the key became sufficient.

Three further changes closed the rest of the chain:

* the import-time `load_dotenv()` was removed from both live modules — the key
  is now read inside a fixture, after authorisation is established;
* their `skipif` asks about authorisation instead of key presence;
* the conftest guard now covers `embed_content` as well as `generate_content`.
  That gap was independent of the incident: the retrieval embedding path had no
  cover at all, so an offline test reaching embeddings with a key configured
  would have made a real call with nothing to stop it.

A collection gate in `tests/conftest.py` fails the run when live tests are
selected without authorisation, and explains the `-m` trap in the message. It
carries `@pytest.hookimpl(trylast=True)` — without it the hook runs before
pytest's own marker deselection, sees every live test even on a safe run, and
fails every ordinary offline invocation. That was found by running it.

### Non-vacuity

| Reversion | Broke |
|---|---|
| the barrier in `build_provider()` | 1 — the test that pins the outer layer |
| the client guard in `GeminiProvider` | 1 |
| accepting booleans as authorisation | 7 |
| `embed_content` coverage in the conftest guard | 1 |
| **only** the collection gate | the gate tests; the structural ones still pass |

The first reversion was vacuous on its first run, and that found a real gap: the
two layers are redundant by design, so removing the factory's check still left
the constructor refusing, and every assertion still saw `LiveNotAuthorised`.
`test_the_factory_refuses_before_it_reaches_the_provider_class` now pins the
outer layer specifically by replacing `GeminiProvider` with something that fails
if it is ever reached.

## §38 CI

Two workflows. No job in either holds a provider credential or
`AGENT_PLATFORM_LIVE`, and `scripts/ci_assert_no_live.py` checks that at the top
of every job that runs tests — because a workflow file can be read and believed,
while an organisation-wide secret injected into every job cannot be read from
the workflow file at all.

`scripts/ci_assert_incident_refused.py` runs `pytest -m "not docker"` in CI and
requires exit 4, plus the complement: `-m "not live and not docker"` must still
collect normally, or the gate would be deleted by the first person it
inconvenienced.

`data/kb_vectors.db` became tracked. It is a build input — the `Dockerfile`
copies it — and the only way to regenerate it calls a real embedding API, so
excluding it would have forced CI to either fail or make live calls. A
`.gitattributes` pins LF everywhere so a Windows checkout cannot change a
protected file's hash.

### Found while validating

`scripts/k8s_up.sh` created the credential Secret but never restarted the API.
`kubectl apply` does not roll a Deployment whose spec is unchanged, and
replacing a mounted Secret restarts nothing — the platform reads the credential
table once at start-up. A re-run therefore installed new credentials, handed
over the matching tokens, and left the pods authenticating against the previous
table: every request 401, with the cluster and the token file agreeing with each
other. Found by re-running the script and watching 14 of 23 HTTP proofs fail.
Fixed with an explicit `rollout restart`; 23/23 after.
