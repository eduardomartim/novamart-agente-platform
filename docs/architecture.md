# Architecture

## The invariant

Every design decision below serves one property:

> A language model can *propose* an action. It has no path to *perform* one.

Everything else — the registry, the gateway, the capability matrix, the
fingerprinted confirmations — exists to make that true mechanically rather than
by convention.

## Request pipeline

```
run(user_input)
  │
  ├─ 1. rate limit .......... acquire() once per request; refusal never enters the graph
  ├─ 2. input security ...... normalise, size-check, signal injection/PII/secrets
  ├─ 3. graph ............... router → researcher → executor → validator
  │       │                             └─ answerer (when documents were retrieved)
  │       └─ per action: policy engine → tool gateway → tool
  ├─ 4. output security ..... secret block, PII mask, truncate
  └─ 5. persistence ......... sanitised events, cost rows, request digest
```

Rate limiting sits outside the graph deliberately: a refused request should cost
nothing, not even a model call to classify it.

## Layering

```
              platform.py  (composition root)
                    │
        ┌───────────┼────────────┐
        │           │            │
     agent/    orchestration/  evaluation/
        │           │
        └─────┬─────┘
              │
        tools/gateway.py
              │
        guardrails/          ← the decision layer
              │
   ┌──────────┼──────────┬───────────┐
   │          │          │           │
 tools/    cost/    observability/ security/
 models     │            │           │
   └────────┴────────────┴───────────┘
                    │
              persistence/
```

The one subtlety: `tools/gateway.py` sits *above* `guardrails/` (it consumes the
policy engine), while `tools/models.py` sits *below* it (the guardrails import
`ToolDefinition`). This is why `ToolGateway` is deliberately not re-exported from
`agent_platform.tools.__init__` — doing so made importing a tool type pull in the
whole policy stack and created an import cycle.

## Components

### Policy engine — `guardrails/policy.py`

The single decision authority. Returns a structured `PolicyDecision`
(`decision`, `risk_level`, `reason`, `violations`) plus the derived facts the
gateway needs: the resolved tool, schema-validated arguments and the risk
assessment.

Rules are evaluated in a deliberate order — cheapest and most structural first:

| Rule | Check | Outcome |
|---|---|---|
| `PL002` | Router proposed an action | DENY |
| `PL001` | Tool is not registered | DENY |
| `PL005` | Tool is CRITICAL | DENY |
| `PL003` | Agent fails allow-list or capability matrix | DENY |
| `PL004` | Arguments fail the schema | DENY |
| `PL006` | Arguments contain credential-shaped content | DENY |
| `PL005` | Risk *escalated* to CRITICAL | DENY |
| `PL008` | HIGH-risk action while injection signals present | DENY |
| `PL011` | Rate quota exhausted | DENY |
| `PL007` | Projected cost breaches a budget | DENY |
| `PL009` | Confirmation required and absent | REQUIRE_CONFIRMATION |
| `PL010` | Confirmation does not match the action | DENY |

`PL005` is checked against the tool's *base* risk before authorisation, so a
CRITICAL tool is reported as "blocked because it is CRITICAL" rather than the
weaker (also true) "this agent lacks the capability".

**Budget and rate limits are consulted, not duplicated.** Both blueprints
described these as separate layers; keeping the decision in one place is what
avoids two enforcement points drifting apart.

### Tool gateway — `tools/gateway.py`

Enforces, never decides. It asks the policy engine, records the verdict, and
either performs the call or does not. Separating decision from enforcement is
what lets the security suite assert that no path reaches a handler without a
matching `ALLOW`.

Execution is guarded by a `ContextVar`:

```python
@contextmanager
def gateway_execution() -> Iterator[str]:
    token = _GATEWAY_TOKEN.set(secrets.token_hex(8))
    try:
        yield
    finally:
        _GATEWAY_TOKEN.reset(token)
```

`gateway_execution()` is opened in exactly one place in the codebase. Every tool
opens with `require_gateway()`, which raises unless that context is active. A
fresh token per call means a leaked token is useless, and `ContextVar` scoping
means authorisation cannot escape into another thread.

That last property needs one clarification, because the gateway does copy the
context. Tool handlers run on a `ThreadPoolExecutor`, and a worker thread starts
with an empty context: a `ContextVar` set by the caller is simply not visible
there. The gateway therefore submits `copy_context().run(...)` rather than the
handler directly, so that request-scoped state set before the call is reachable
inside it.

This does not widen the authorisation guarantee, and the reason is worth stating
rather than asserting. The copy is taken *before* `gateway_execution()` opens —
`gateway_execution()` is opened inside `_run_guarded`, on the worker, within the
copied context. So the copy never carries an active gateway token, and because a
copied context is a separate object, a token set inside it cannot leak back to
the caller. What the copy carries is ordinary request-scoped state; what it
cannot carry is permission to execute.

The copy is deliberately generic: the gateway copies the whole context and knows
nothing about what is in it. It was added after the retrieval provider, bound by
a `ContextVar` in the calling thread, turned out to be invisible on the worker —
hybrid retrieval silently degraded to lexical, and twenty-six offline tests
missed it because they exercised the retriever directly rather than through the
executor.

### Least privilege — `guardrails/authorization.py`

Two independent gates, both of which must pass:

| Agent | search | read | write | message | delete |
|---|---|---|---|---|---|
| router | – | – | – | – | – |
| researcher | yes | yes | – | – | – |
| executor | yes | yes | yes | yes | – |
| validator | – | yes | – | – | – |
| answerer | – | – | – | – | – |

The answerer holds nothing. It turns documents that retrieval already returned
into prose, and never needs to reach a tool to do it — so the row is empty for
the same reason the router's is.

`DELETE` is held by no role. It is unassigned on purpose, and a test asserts it
stays that way.

### Retrieval — `retrieval/`

Knowledge-base questions go through a retrieval layer rather than through the
knowledge base directly. It has two halves and one entry point.

**Lexical — `retrieval/lexical.py`.** SQLite FTS5 over the article corpus,
scored with `bm25(kb, 0.0, 2.0, 1.0)`: the document id is weighted zero, the
title twice the body. `tokenize='porter unicode61'` gives stemming and Unicode
folding. Queries are not passed through as text — `normalize_terms()` folds to
NFKD, keeps only `[a-z0-9]+` runs, and rebuilds the FTS5 expression from those
tokens, so no user string reaches the match syntax intact. Queries are capped at
500 characters and `top_k` at 5.

**Vector — `retrieval/index.py`.** A brute-force exact cosine scan over the
corpus embeddings, produced by `gemini-embedding-001` at 3072 dimensions. Exact,
not approximate: at this corpus size an ANN index would add a failure mode and
an accuracy loss to solve a problem that does not exist. The index is opened
read-only (`mode=ro`) and refuses to load on any of nine integrity conditions —
wrong format version, dimension mismatch, corpus fingerprint mismatch, truncated
payload, and so on. A stale index is a wrong answer, so it is treated as a
failure rather than as a degraded mode.

**Fusion — `retrieval/hybrid.py`.** Min-max normalisation of each ranking,
combined at equal weight. One property matters and is easy to get wrong: the
fused score is **not** a confidence signal. Min-max maps the best candidate in
any result set to 1.0, including a result set of uniformly bad candidates, so a
threshold on the fused score would be meaningless. Admission is therefore
decided on the raw cosine similarity *before* fusion, never after.

**Strategy — `retrieval/strategy.py`.** The single entry point, and where the
STUB/LIVE difference lives:

```
retrieve(query, top_k=3)
  │
  ├─ no provider bound  → BM25 only                       (STUB)
  └─ provider bound     → embed query
                          → cosine over the index
                          → drop candidates below MIN_COSINE_SIMILARITY
                          → fuse survivors with BM25      (LIVE)
```

The provider is bound with a `ContextVar` (`retrieval_provider()`), not passed
as an argument, because the call site is a tool handler whose signature belongs
to the tool contract. `MIN_COSINE_SIMILARITY = 0.67` is an **initial
calibration**, not a validated operating point: it was chosen from the
separation observed between answerable and unanswerable queries on a set of
**four** unanswerable questions. Four is enough to place a starting value and
not enough to defend it; it should be revisited against a larger unanswerable
set before it is treated as tuned.

An embedding failure is a failure. It propagates as a provider error and is
never silently downgraded to a lexical result — a degraded answer that looks
like a normal one is worse than an error, because nothing downstream can tell
the difference.

### Answering — `agent/answerer.py`, `agent/answer.py`

The answerer is the only agent that produces prose, and it is reached only when
retrieval returned documents. It holds no capabilities and no tool allow-list.

Its input is a fenced prompt: the question is wrapped as untrusted, each
document is fenced separately with its identifier. Its output is
schema-constrained, and one of five outcomes:

| Outcome | Meaning |
|---|---|
| `grounded` | An answer, with at least one citation that resolves |
| `insufficient_evidence` | The documents do not contain the answer |
| `unverifiable` | The model produced something that cannot be checked |
| `provider_error` | The model call failed |
| `resource_blocked` | A budget, limit or circuit breaker stopped the call |

The `Answer` type enforces this rather than trusting it: `grounded` requires at
least one citation and non-empty text, and every other outcome requires a reason
and *no* citations. Citations are validated against the documents that retrieval
actually returned — a model that cites a document it was never given does not
produce a grounded answer with a bad citation, it produces `unverifiable`.

The fencing is a **prompt-construction discipline, not a security boundary**.
The boundary is the policy engine and the capability matrix, both of which sit
below this layer and neither of which consults anything the model said.

### Confirmation — `orchestration/graph.py`

The blueprint's hardest requirement: a confirmation must not be forgeable by the
model.

1. The executor proposes; the policy engine returns `REQUIRE_CONFIRMATION`.
2. The graph routes to a dedicated `confirm` node whose **first** statement is
   `interrupt(...)`. Everything before it has already run and is not repeated on
   resume — so the proposal is not re-generated, and not re-billed, while a human
   thinks.
3. LangGraph persists state in the checkpointer and execution suspends.
4. The application resumes with `Command(resume={"approved": ..., "actor": ...})`.
5. **The action is rebuilt from platform-held state**, never from the resume
   payload. A `Confirmation` is constructed carrying a SHA-256 fingerprint of
   `{tool, arguments}`.
6. Policy is evaluated **again** — budget, rate limits and risk may all have
   changed while the request waited.
7. `PL010` rejects any confirmation whose fingerprint does not match.

The model never touches the resume channel, and even a tampered resume payload
cannot redirect an approval onto a different action.

A decline ends the request. It is not routed into the validator, because doing so
made the retry loop re-propose the same action and ask the reviewer again after
they had already said no.

### Loop bounds

Three independent stops:

- `MAX_RETRIES` (default 2) on the validator → executor edge;
- LangGraph's `recursion_limit` (default 25) on the graph as a whole;
- refusals are never retried — a denied or declined action goes straight to the
  response node, because retrying it cannot succeed and would spend budget
  re-learning that.

### Provider failures — `llm/errors.py`

A live provider fails in more ways than "worked" or "threw". It can return a
200 carrying no usable text at all. Each case is named and handled:

| Condition | Error | Handling |
|---|---|---|
| `SAFETY`, `PROHIBITED_CONTENT`, `BLOCKLIST`, `RECITATION` | `LLMSafetyBlockedError` | Generic refusal to the user; category recorded in the trace but not echoed |
| `MAX_TOKENS` with empty text | `LLMTruncatedError` | Reports thinking-token count and points at `GEMINI_THINKING_BUDGET=0` |
| Empty text, no known reason | `LLMEmptyResponseError` | Handled failure |
| 4xx | `LLMUnavailableError` | **Never retried** -- permanent, and retrying burns quota |
| 5xx, transport | retried with backoff | Bounded by `RetryPolicy` |

Two containment properties hold regardless of which fires:

1. **Unexpected exceptions are normalised at the trust boundary.**
   `BaseAgent._generate` converts anything the provider SDK raises into a typed
   `LLMUnavailableError`. This is done at the boundary rather than with a
   blanket catch further up, so provider misbehaviour becomes uniform while
   genuine bugs in our own graph nodes still surface.
2. **No failure can cause an action.** The provider fails *before* an action is
   proposed, so there is nothing for the gateway to execute. Asserted across
   every failure mode in `tests/security/test_provider_failure.py`.

Exception text is scrubbed before it can reach a trace or a user-visible
message: SDK errors can echo request context, and an error message must not
become the thing that leaks the credential.

### Resource limits — `security/resources.py`

Hard ceilings on one request, denominated in **calls, seconds and bytes** rather
than money. The distinction matters: the budget guard caps cost, and the
deterministic stub costs exactly `$0`, so under a purely monetary limit a
runaway loop is free and therefore unbounded.

**`ResourceGuard` is a precondition service, not an authorisation mechanism**,
and that is enforced structurally rather than by convention. It has one verb --
*stop*. Every method returns `None`, a counter or a truncation boolean, or it
raises; none returns a decision. A test pins the public surface and asserts it
contains no `evaluate`, `authorize` or `allow`. The policy engine holds no
reference to it. A request that clears every limit has been granted nothing; it
has merely not been stopped.

| Control | Enforced at | Ordering |
|---|---|---|
| LLM-call ceiling, deadline | `BaseAgent._generate` | before the provider call |
| Tool-call ceiling, deadline | `ToolGateway.submit` | **after** the policy ALLOW |
| Output byte ceiling | `ToolGateway._bound_output` | after execution, before context |

The tool-call charge sits deliberately *after* the policy decision, so it can
only ever subtract from what policy permitted and can never turn a `DENY` into
an execution.

Two details worth stating:

* **An oversized tool result is replaced, not truncated.** Half a JSON document
  is not a smaller document, it is a corrupt one, and handing a model a
  malformed fragment is worse than telling it the result was too large.
* **A resumed request does not get a fresh deadline.** Re-entering `begin()`
  does not restart the clock, so a request cannot win itself more time by being
  suspended for confirmation.

### Provider circuit breaker — `llm/circuit.py`

```
closed    -> calls flow; consecutive failures counted
open      -> fail fast until the cooldown elapses
half_open -> exactly one trial call admitted; success closes, failure reopens
```

Prevents retry storms against a provider already known to be failing. Like the
resource guard it can only *prevent* a call, never permit one, and an open
circuit produces the same outcome as any other provider failure: a recorded,
handled failure with no tool execution.

Deterministic by construction -- the clock is injectable, so cooldown behaviour
is tested without sleeping.

### Prompt egress — `guardrails/egress.py`

Sending a prompt to a hosted model hands text to a third party, which is a
different question from what may be *stored*. Two rules, applied to every
prompt on every provider:

**Credentials are always removed.** No task this platform performs needs one.

**PII is masked only where masking cannot break the work.** Blanket-masking
would be theatre: `send_email` needs a real recipient, so masking every address
would prevent the agent doing its job while still shipping the surrounding text
off-box. The rule is narrower and honest — mask the categories *no registered
tool consumes* (CPF, card, phone, IP), preserve the one that a tool acts on
(email).

The control applies to the stub as well as to Gemini. A control that engages in
only one mode is a control that gets discovered broken in the mode that matters.

### Provider selection, and permission — `llm/`

`build_provider()` decides which provider a caller gets, and since V2.7 it is
also where permission to use a real one is enforced. Those are two questions
that used to have one answer:

* a `GEMINI_API_KEY` **selects** the real provider — that has always been true;
* `AGENT_PLATFORM_LIVE` **authorises** using it — that is new, and separate.

The check sits here rather than in the test runner because this function is the
one place every path passes through: `AgentPlatform`, the CLI, the dashboard,
`scripts/build_vector_index.py` and the live fixtures all arrive at it. It
refuses *above* its own lazy import of the SDK, so an unauthorised process does
not load the client library, let alone construct a client. `GeminiProvider`
restates the refusal where the real client is actually made, for anything that
bypasses the factory.

It is below pytest deliberately. The previous arrangement lived entirely inside
the test runner — a marker expression — and a `-m` on the command line replaces
the configured one rather than combining with it, which cost 72 unintended
calls. A control that a command-line flag can switch off is a control that will
be switched off by a command nobody read carefully. See
[live-verification.md](live-verification.md).

### Cost — `cost/`

The rate card is **dated**, not a flat dictionary:

```python
"gemini-3.7-flash": (
    Rate(Decimal("0.75"), Decimal("3.75"), date(2026, 1, 1), date(2026, 12, 31),
         note="introductory pricing"),
    Rate(Decimal("1.50"), Decimal("7.50"), date(2027, 1, 1), note="standard pricing"),
),
```

Introductory pricing that expires is a real trap for a hardcoded table: the
figure silently becomes wrong on a known date. Modelling periods means the
rollover happens correctly, and `pricing_notice()` surfaces the verification date
next to every cost figure, escalating to a stale warning after 90 days.

An unrecognised model yields `known_model=False` and an explicit "unknown"
rather than a confident `$0.00`.

Money is stored as **integer nano-USD**. Costs accumulate across thousands of
rows and binary floats drift in exactly the direction that makes a budget guard
unreliable; integers keep `SUM()` exact.

Budget is checked **before every model call**, not only before tool calls. Tools
here are free simulations; model calls are what cost money, and an agent that
loops without ever proposing a tool would otherwise run unmetered.

### Observability — `observability/`

Events carry `request_id`, `trace_id`, a per-request sequence number, agent,
tool, policy decision, risk level, rule ids, latency and status.

What is deliberately **not** recorded: prompts, completions, and anything
reasoning-shaped. `sanitization.REASONING_KEYS` drops keys like `reasoning` and
`chain_of_thought` outright, so the rule holds even if a future call site passes
them.

Only a SHA-256 digest of user input is stored. The full text is never needed to
operate the platform, and storing it would put arbitrary user content in the
database for no operational benefit.

Sanitisation runs at two independent points: in the tracer, and again in the
repository immediately before the write. It is idempotent, so the second pass
costs nothing and closes the hole if anything ever writes an event directly.

## Notable deviations from the source blueprints

| Blueprint said | Built instead | Why |
|---|---|---|
| `guardrails.check(action)` before each agent acts | A decision object consumed by the gateway | The final spec forbids agents executing tools at all; a boolean check at the agent has nothing to enforce |
| Trace what the agent "thought" | Structured events only | Contradicts the no-chain-of-thought rule; reasoning keys are now dropped structurally |
| Budget/rate limits as both policy rules and gateway steps | Policy consults them; one enforcement point | Two enforcement points drift apart |
| `tools/models.py` holds all shared types | `models.py` at the package root | Risk levels and decisions are used by guardrails, cost and observability, not just tools |
| `gemini-2.5-flash` | `gemini-3.5-flash`, configurable | Still valid but dated; newer free-tier models exist |
| Researcher always feeds the executor | Read-only routes end after research | Forcing a lookup through the executor made it invent a high-risk action for a question that only wanted an answer |
| Unscoped drift baselines | Baselines keyed `name::provider::model` | A stub baseline compared against live results reports a dramatic regression that describes neither; scoping makes the comparison structurally impossible |
| `gemini-3.5-flash` default | `gemini-3.5-flash-lite` | Every model call here is a short schema-constrained decision, which is the Flash-Lite profile; free tier, lower latency, quarter the paid-tier cost |
