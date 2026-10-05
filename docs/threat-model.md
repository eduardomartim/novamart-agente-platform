# Threat model

Scope: the demo platform as it runs in this repository — a single process, a
local SQLite database, nineteen simulated tools, one optional external LLM provider,
and a single trusted local operator.

Explicitly **out of scope**: multi-tenancy, end-user identity (the platform
authenticates *services*, not people -- see docs/api.md), network
exposure, and the security of the Gemini API itself. Those are listed in the
residual-risk section rather than pretended away.

## Trust boundaries

```
   untrusted                     semi-trusted                trusted
 ┌────────────┐              ┌──────────────────┐      ┌──────────────────┐
 │ user input │─────────────▶│  model output    │─────▶│ policy engine    │
 │ tool output│              │ (a proposal)     │      │ gateway, registry│
 └────────────┘              └──────────────────┘      │ config, matrix   │
                                                       └──────────────────┘
```

The critical line is the second one. **Model output is semi-trusted data, never
an instruction.** It can name a tool and supply arguments; it cannot assert its
own risk level, its own authorisation, or that a human approved something.

## Threats

### T1 — Prompt injection

*An attacker embeds instructions in user input to make an agent take an
unauthorised action.*

- **Impact** High · **Likelihood** High
- **Mitigations**
  - Authorisation never consults intent. The capability matrix and tool
    allow-list are evaluated against the *proposed action*, so a successful
    injection still cannot reach a tool the agent does not hold.
  - Untrusted content is fenced in prompts (defence in depth, not a control).
  - Injection signals *escalate risk* (`PL008`) rather than being the barrier.
  - Unicode normalisation and zero-width stripping before matching.
  - Patterns cover English and Portuguese, including the noun-adjective ordering
    Portuguese uses ("regras anteriores").
- **Residual risk** Detection will miss novel phrasings. This is accepted and
  designed for: `test_authorisation_holds_when_detection_fails_completely`
  disables detection entirely and asserts the platform still refuses. The
  residual risk of *detection* failing is low; the residual risk of
  *authorisation* failing is what matters, and that path does not depend on
  detection.

### T2 — Unauthorised tool execution

*An agent invokes a tool it should not have, or bypasses the gateway.*

- **Impact** Critical · **Likelihood** Low
- **Mitigations**
  - `require_gateway()` in every tool implementation; direct calls raise.
  - Agents receive tool *descriptions*, never callables — `public_spec()`
    excludes the handler.
  - Two independent authorisation gates.
  - Static registry: no runtime-produced name can become an executable tool.
- **Residual risk** A future tool added without `require_gateway()` would be
  unguarded. Mitigated by `test_every_registered_tool_guards_itself`, which
  enumerates the registry and fails if a tool is added without a guard test.

### T3 — Privilege escalation via misconfiguration

*A tool's allow-list is edited to grant an agent more than intended.*

- **Impact** High · **Likelihood** Medium
- **Mitigations** The capability matrix is a second, independent gate. Adding
  `RESEARCHER` to `update_record`'s allow-list still fails, because the
  researcher holds no `WRITE_DATA` capability. Asserted by
  `test_authorisation_requires_both_gates`.
- **Residual risk** Editing *both* the allow-list and the matrix would succeed.
  That is a deliberate two-file change, visible in review.

### T4 — Forged or replayed confirmation

*The model claims a human approved a high-risk action, or an approval for a
benign action is reused for a dangerous one.*

- **Impact** Critical · **Likelihood** Medium
- **Mitigations**
  - Confirmations are constructed by the application, never parsed from model
    output. `ProposedAction` sets `extra="forbid"`, so a model cannot even
    include a `confirmed` field.
  - The resume channel is LangGraph's `Command(resume=...)`, which the model does
    not control.
  - Each confirmation is bound to a SHA-256 fingerprint of `{tool, arguments}`;
    `PL010` rejects a mismatch.
  - Policy is re-evaluated after resume.
- **Residual risk** Anyone who can call `platform.confirm()` can approve. In this
  demo that is the local operator, which is correct. Production needs
  authenticated approvers and an audit trail of who approved what.

### T5 — Destructive action

*An irreversible operation is performed.*

- **Impact** Critical · **Likelihood** Low
- **Mitigations** `CRITICAL` is refused for every agent with no confirmation
  path (`PL005`). No role holds `DELETE`. `delete_record` is registered
  specifically so the platform can be *seen* to refuse it rather than merely
  lacking it.
- **Residual risk** None in the demo — the tool only mutates an in-memory dict.

### T6 — Secret leakage

*A credential reaches the response, the trace, the state or the database.*

- **Impact** Critical · **Likelihood** Medium
- **Mitigations**
  - Pattern-based detection for common credential shapes, plus literal redaction
    of known-sensitive values (the configured key).
  - A response containing the configured credential is **discarded entirely**,
    not redacted — a confirmed leak of our own key should be loud.
  - Credentials in tool arguments escalate to CRITICAL and are denied (`PL006`).
  - Two independent sanitisation gates before persistence.
  - `Settings.describe()` reduces the key to a presence flag.
  - The key is never placed in a prompt or in `AgentState`.
  - Credentials embedded in a connection string (`scheme://user:pass@host`,
    including `redis://:pass@host`) are redacted by pattern, keeping the host
    readable; and **every** credential the process holds -- provider key, grant
    secret, `DATABASE_URL`, `REDIS_URL` and the passwords inside those URLs -- is
    redacted by value (F-03, closed).
  - The events a request writes *after* a human confirmation are redacted with
    the same secret list as the events before it. The resumed half used to get
    a tracer with no known secrets at all.
- **Residual risk** Novel credential formats will not match a pattern. The
  known-value redaction covers the platform's own secrets regardless of shape.
- **Found in Phase 3 (F6, critical):** every *deliberate* egress path was
  defended and the key still leaked -- through `repr(Settings)`. A dataclass
  repr includes every field, so any traceback, log line, debugger frame or test
  failure that rendered the settings object printed the credential. Discovered
  when pytest assertion introspection dumped a fixture into test output. Fixed
  with `field(repr=False)` plus an explicit `__repr__`; regression tests sweep
  the objects a traceback is most likely to render. The lesson is that
  incidental rendering paths need modelling as carefully as intentional ones.

### T7 — PII exposure

*Personal data is stored or transmitted unnecessarily.*

- **Impact** Medium · **Likelihood** High
- **Mitigations** Email/CPF/phone/IP/card detection with Luhn validation to cut
  false positives; masking preserves debuggability (`a***@example.com`); only an
  input digest is persisted; PII in an outbound message escalates risk.
- **Residual risk** Coverage is a handful of well-defined formats, not general
  DLP. The module says so rather than implying broader coverage.

### T8 — Excessive cost

*A loop or a hostile request drains the budget.*

- **Impact** Medium · **Likelihood** Medium
- **Mitigations** Per-request and daily budgets checked **before every model
  call**; exact integer accounting; retry ceiling; recursion limit; refusals not
  retried. The budget guard fails **closed** — an unreadable ledger reports the
  budget as fully consumed rather than as `$0` spent.
- **Residual risk** Cost is estimated from a rate card, so a provider price
  change makes projections wrong until the card is re-verified. Surfaced as a
  staleness warning after 90 days rather than failing silently.

### T9 — Denial of service by request volume

- **Impact** Medium · **Likelihood** Medium
- **Mitigations** Sliding-window per-minute and per-hour quotas, enforced before
  the graph is entered. The limiter fails **closed**.
- **Residual risk** Shared across replicas only when `REDIS_URL` is set
  (`SharedRateLimiter`); without it each process counts on its own. The public
  dashboard charges each visitor their own bucket, keyed on a salted digest of
  `X-Real-IP` -- see T16.

### T10 — Infinite loops

- **Impact** Medium · **Likelihood** Low
- **Mitigations** Retry ceiling, recursion limit, no retry on refusal, bounded
  context and error lists. Three independent stops.

### T11 — Poisoned context / malicious tool output

*A tool returns content designed to influence a later step.*

- **Impact** Medium · **Likelihood** Low (tools are simulated)
- **Mitigations** Tool output is data. The validator scans it for credentials
  and rejects on a hit. Context is bounded by `max_context_items`. Later
  authorisation decisions do not read tool output.
- **Residual risk** Real tools returning attacker-controlled content would raise
  this materially. The mitigation shape — output is data, authorisation does not
  consult it — is the right one, but would need testing against real sources.

### T12 — Unsafe or malformed model output

- **Impact** Low · **Likelihood** Medium
- **Mitigations** Structured output via `response_schema` where supported;
  tolerant JSON extraction; malformed output raises a typed error handled as a
  normal failure path. The router falls back to the **read-only** researcher on
  any failure, so a routing error cannot cause a write.

### T13 — Prompt egress to a third party

*Text sent to a hosted model leaves the machine and is processed by a vendor.*

- **Impact** High · **Likelihood** Certain (it is how the system works)
- **Mitigations**
  - Credentials are stripped from every prompt before transmission,
    unconditionally, in `guardrails/egress.py`.
  - PII categories that no tool consumes (CPF, card, phone, IP) are masked.
    Email is deliberately preserved because `send_email` needs a real
    recipient -- masking it would break the workflow while still sending the
    surrounding text, which is worse than useless.
  - Only redaction *categories* are recorded in the trace, never values.
  - The control applies to the stub as well, so it cannot rot unnoticed in the
    mode nobody exercises.
  - Only a digest of user input is persisted, so the platform's own store is
    not a second copy of everything sent onward.
- **Residual risk** The remaining prompt content -- the user's question, order
  identifiers, an email address -- does reach the vendor. That is inherent to
  using a hosted model, is stated plainly in the README, and would need a
  self-hosted model to eliminate rather than mitigate.

### T14 — Provider failure as a bypass vector

*A timeout, safety block or malformed response causes the platform to fail
open rather than closed.*

- **Impact** Critical · **Likelihood** Medium (live providers fail routinely)
- **Mitigations**
  - Every provider failure mode is a typed, handled error.
  - Unexpected SDK exceptions are normalised at the trust boundary in
    `BaseAgent._generate` rather than by a blanket catch that would also
    swallow genuine platform bugs.
  - The failure happens *before* an action is proposed, so there is nothing for
    the gateway to execute.
  - Provider exception text is scrubbed before reaching a trace or a response,
    so an error message cannot become the credential-leak vector.
  - A bad or unknown route falls back to the **read-only** researcher, so a
    model failure cannot default to the path that changes things.
- **Residual risk** Low. Asserted across six failure modes and eight hostile
  output shapes in `tests/security/test_provider_failure.py`.
- **This was a real finding.** Before Phase 2, provider errors escaped
  `graph.invoke()` uncaught: the request crashed with a raw traceback, no
  record was persisted, and the unsanitised message reached the caller. The
  stub never fails, so it was invisible until the live path was built.

### T15 — Resource exhaustion and abuse

*A caller drives unbounded work through loops, retries, oversized payloads or
accumulated suspended requests.*

- **Impact** Medium · **Likelihood** Medium
- **Mitigations** Hard per-request ceilings in `security/resources.py`,
  denominated in calls, seconds and bytes rather than money -- the budget guard
  alone cannot bound a free provider, since the stub costs exactly `$0`:
  - `MAX_LLM_CALLS_PER_REQUEST` 12, `MAX_TOOL_CALLS_PER_REQUEST` 8
  - `REQUEST_DEADLINE_SECONDS` 180, independent of the step limit
  - `MAX_TOOL_OUTPUT_BYTES` 32768 (payload replaced, not truncated)
  - `MAX_PENDING_CONFIRMATIONS` 50 with a 900s TTL
  - `RECURSION_LIMIT` tightened 25 -> 15
  - a provider circuit breaker (5 failures, 60s cooldown) preventing retry
    storms
- **Critically, none of these can authorise anything.** `ResourceGuard` has one
  verb -- *stop*. Its public surface is asserted to contain no `evaluate`,
  `authorize` or `allow`, and the policy engine never consults it.
  - one caller may hold at most a share of the pending store
    (`MAX_PENDING_CONFIRMATIONS_PER_KEY`, derived 6 of 50), so one visitor
    cannot fill it and lock everyone else out of the approval step (F-02)
  - the HTTP API reads a request body with a byte ceiling while streaming it and
    answers `413` before buffering more (F-04, closed)
- **Residual risk** Without `REDIS_URL` the counters, circuit and pending
  confirmations are process-local and do not survive a restart.

### T16 — The public dashboard

*Anyone on the internet can open the dashboard, ask questions, and approve the
simulated actions it proposes.*

- **Impact** Low (nothing real can be changed) · **Likelihood** High
- **Mitigations**
  - Per-visitor quota: a salted digest of `X-Real-IP` (the one header Railway's
    edge writes) names each visitor's rate-limit and pending-confirmation
    bucket. The raw address is never stored, logged or rendered;
    `X-Forwarded-For` is ignored.
  - Per-visitor data: each browser session gets its own copy of the simulated
    HDstore records, so an update one visitor approves changes what *they* read
    next and nothing anyone else sees. It used to be one copy per process.
  - Every question is capped at 100 characters on every entry point.
- **Residual risk** `X-Real-IP` is only trustworthy behind Railway's edge; behind
  another proxy the trust boundary in `dashboard/app.py` must be revisited. A
  visitor with many addresses gets many buckets.

## Phase 4 findings

Four disclosure findings, all reproduced before being fixed (details in
§25 of `audit-report.md`):

- **F7 (Medium)** — tool exception text carried absolute local paths into the
  user-facing response. Credentials were already stripped there; paths were
  not. Fixed by `redact_home_paths()` and by sanitising exceptions at the
  gateway rather than only at the response boundary.
- **F8 (Medium)** — a combining mark inside a keyword ("ign̄ore") evaded every
  injection pattern while looking identical to a reader. Detection now folds
  to NFKD; the text the agent reads stays NFKC so Portuguese accents survive.
- **F9 (Medium)** — Streamlit renders unhandled exceptions itself, outside
  `secure_output`, so a dashboard traceback disclosed the operating user's
  name and directory layout. Closed in `.streamlit/config.toml`, which is a
  security control and not a preference file.
- **F10 (Low)** — the rate limiter's failure reason reached the caller before
  the output pipeline ran. Sanitised at the source, keeping the exception type.

F7, F9 and F10 are the same lesson from three directions: a single downstream
redaction gate is not enough when some paths do not cross it.

### Phase 4, live round

- **F11 (Medium)** — the credential detector matched only the legacy `AIza`
  key format. Google now issues `AQ.` authorization keys by default and will
  reject standard keys entirely by September 2026, so the format every new key
  uses went unredacted. The configured key was never exposed — it is redacted
  by value — but credentials of unknown origin were not caught. Both formats
  are now matched.
- **F12 (Medium)** — live security tests assert negatives, which a provider
  outage satisfies for free. The adversarial suite reported 11/11 green while
  the API returned nothing but quota errors. Such tests now skip rather than
  pass when no model call succeeded.

- **F13 (Medium)** — tool output reached the executor and the judge unfenced,
  while user input in the same prompt was fenced. In a real deployment tool
  output is equally attacker-influenceable, so the attacker held the more
  privileged channel. Now wrapped in a distinct `UNTRUSTED_TOOL_OUTPUT` fence.
  Defence in depth: the policy engine remains the only authority, asserted
  separately so the two claims stay distinguishable.

## Accepted risks

These are real and deliberately not addressed, because the project is a demo:

1. **No transport security for credentials.** Authentication is enforced as of
   V2.6 -- a bearer credential with separate `runs:write` and `confirm:write`
   scopes, and the approver in the audit record now comes from the credential
   rather than from the request body. What remains accepted is that there is no
   TLS, so the token relies on the network being trusted, which is why the
   Kubernetes Service is not published.
2. **Process-local state by default.** Rate limits and pending confirmations are
   shared across replicas only when `REDIS_URL` is configured; the default
   deployment keeps them in one process, and they do not survive a restart.
3. **`.env` secrets.** Adequate locally; production needs a managed store.
   Note that as of V2.7 a key found in `.env` no longer authorises anything on
   its own: real provider calls require `AGENT_PLATFORM_LIVE`, which is
   deliberately not read from `.env` at all.
4. **Transport security is the platform's.** The dashboard image binds
   `0.0.0.0` inside its container and relies on Railway's edge for TLS; run
   anywhere else, it is plain HTTP.
5. **Third-party trust.** The Gemini API and the dependency tree are trusted;
   `pip-audit` runs clean but is a point-in-time check.
6. **Accidental spend, addressed in V2.7.** Previously the only thing standing
   between an ordinary test run and the real API was a marker expression, and a
   `-m` on the command line replaces the configured one rather than combining
   with it. `pytest -m "not docker"` therefore made every live test eligible and
   spent 72 unintended calls; every remaining check then cooperated, because all
   of them keyed off the same marker and the last one was a quantity ceiling
   rather than a permission check. Real calls now require `AGENT_PLATFORM_LIVE`,
   enforced in `build_provider()` below pytest and shared by the CLI, the
   dashboard and the index builder. Possessing a key is not authorisation.
7. **Vendor data handling.** Prompt content that survives egress redaction is
   subject to the provider's retention and training policy, which this project
   does not control and does not attempt to characterise.

## Verification

The mandatory checks map to tests as follows:

| Threat | Test |
|---|---|
| T1 | `tests/security/test_prompt_injection.py` (18 tests) |
| T2 | `test_direct_tool_invocation_is_refused`, `test_every_registered_tool_guards_itself` |
| T3 | `test_authorisation_requires_both_gates`, `test_no_agent_holds_delete` |
| T4 | `test_confirmation_for_another_action_is_rejected`, `test_model_cannot_smuggle_approval_fields_into_a_proposal` |
| T5 | `test_critical_tool_is_blocked_for_every_agent`, `test_critical_cannot_be_unlocked_by_confirmation` |
| T6 | `tests/security/test_secret_leakage.py` (9 tests) |
| T7 | `tests/unit/test_security.py` PII section |
| T8 | `test_exhausted_budget_blocks_before_any_model_call`, `test_budget_guard_fails_closed_when_ledger_unreadable` |
| T9 | `test_rate_limited_request_never_enters_the_graph`, `test_rate_limiter_fails_closed_on_internal_error` |
| T10 | `test_validate_stops_at_the_retry_ceiling`, `test_refusals_are_never_retried` |
| T13 | `tests/security/test_prompt_egress.py` (18 tests), plus `test_credentials_never_reach_the_provider` on the live path |
| T14 | `tests/security/test_provider_failure.py` (24 tests) |
| T15 | `tests/security/test_abuse_limits.py`, `test_pending_per_key_cap.py`, `tests/integration/test_api_body_limit.py` |
| T16 | `tests/integration/test_visitor_quota.py`, `tests/security/test_visitor_dataset_isolation.py` |
| T6 (resumed trace, URIs) | `tests/security/test_resumed_audit_trail.py`, `tests/security/test_credential_redaction.py` |
| F6 | `test_settings_repr_never_contains_the_key`, `test_no_credential_bearing_object_leaks_through_repr` |
| F7 | `tests/security/test_error_leakage.py` — home-path redaction and gateway exception sanitisation |
| F8 | `test_combining_marks_do_not_evade_injection_detection`, `test_normalised_input_preserves_legitimate_accents` |
| F9 | `tests/security/test_dashboard_disclosure.py` — Streamlit renders tracebacks outside `secure_output` |
| F10 | `test_rate_limiter_failure_reason_carries_no_credential`, `test_rate_limiter_failure_reason_carries_no_home_path` |
| F11 | `test_authorization_key_is_detected`, `test_authorization_key_is_redacted`, `test_legacy_key_is_still_detected` |
| F12 | `require_live_model()` in both live suites — a negative assertion may not pass when the model was never reached |
| F13 | `tests/security/test_untrusted_fencing.py` — tool output is fenced like user input |
| F14 | `test_quota_error_does_not_trigger_the_thinking_probe`, `test_quota_error_does_not_disable_thinking_support` |
