# Live verification procedure

The deterministic suite runs anywhere, with no key and no network. This
document covers the part that cannot: the suites that need a real Gemini
response to mean anything.

It exists because "run the live tests" is not a safe instruction on its own.
Some of these tests consume quota, some must not run without a decision, and
— before the F12 guard — some could report success without having reached the
model at all.

---

## Authorisation: a key is not permission

A `GEMINI_API_KEY` selects the real provider. It does **not** authorise using
it. Real calls additionally require:

```
AGENT_PLATFORM_LIVE=i-authorise-real-provider-calls
```

Matched exactly. `1`, `true` and `yes` are refused deliberately — those are the
values that appear by accident in CI matrices, shell profiles and unfinished
scripts, and a phrase does not.

Supply it for the single command that should spend quota:

```bash
AGENT_PLATFORM_LIVE=i-authorise-real-provider-calls pytest -m live
```

**Never put it in `.env`, a shell profile, a script, or CI.** It is not read
through `python-dotenv` and setting it in a dotfile would not work anyway. That
is the design: a value that can arrive from a file becomes ambient, and ambient
is precisely how possessing a key came to mean permission to spend it.

### Where the barrier is

In `build_provider()` — the one function every path to a real provider goes
through: `AgentPlatform`, the CLI, the dashboard, `scripts/build_vector_index.py`
and the live fixtures. It refuses *above* its own lazy import of the SDK, so an
unauthorised process never loads the client library, let alone constructs a
client. `GeminiProvider.__init__` restates the refusal where the real client is
actually made, for anything that bypasses the factory.

It sits **below pytest on purpose**. No marker expression, no `-k`, no
`--noconftest` and no `-p no:…` reaches it, and it protects the callers that are
not tests at all.

### Why it exists

The suite used to keep itself offline with `addopts = "-m 'not live'"`. A `-m`
on the command line **replaces** that value rather than combining with it, so
`pytest -m "not docker"` silently made every live test eligible. From there each
remaining check waved the request through: the live modules called
`load_dotenv()` at *import* time so the real key was already in the process; their
`skipif` asked whether a key was present, so having one *enabled* them; the
conftest guard exempted anything marked `live`, opening for exactly the dangerous
case; and the last check before the network was a 400-call/day budget, which
answers "how many more?" and never "may you at all?".

Three controls, one decision. It cost **72 unintended calls**. When you mean
offline, write `-m "not live and not docker"`.

A second layer in `tests/conftest.py` fails collection when live tests are
selected without authorisation, so the refusal arrives early and explains the
`-m` trap. That layer is convenience; the barrier above is the protection.

---

## Before running anything

Verify the key by **presence and format only**. Never print its value, never
`repr()` an object that holds it, never derive a fingerprint from it.

```bash
python -c "import os,re;from dotenv import load_dotenv;load_dotenv();k=os.getenv('GEMINI_API_KEY','').strip();print('present:',bool(k));print('shape ok:',bool(re.fullmatch(r'(AIza[0-9A-Za-z_-]{35}|AQ\..{30,})',k)))"
```

Two key formats are valid. Google is retiring *standard* keys (`AIza…`) in
favour of *authorization* keys (`AQ.…`); all new keys from AI Studio use the
latter, and standard keys stop working in September 2026.

---

## The F12 guard: a live test may not pass without a live call

Most of these tests assert a **negative** — no destructive tool ran, no safety
block occurred, no prompt text was persisted. A provider outage satisfies every
one of them for free.

That is not a hypothetical. The adversarial suite once reported **11/11 passing
while the API returned nothing but quota errors**, and two tests accepted
`"failed"` as an explicit pass.

`require_live_model()` now checks for a successful `llm_call` event and
**skips with an explicit reason** rather than passing when the model was never
reached. A green live run is therefore evidence; a skipped one is honest.

**Never weaken this guard to make a run look complete.**

---

## Quota

| | |
|---|---|
| Limit | 500 requests / day / model (free tier) |
| Quota id | `GenerateRequestsPerDayPerProjectPerModel-FreeTier` |
| Scope | **per Google Cloud project, not per key** |
| Reset | midnight US/Pacific |

Rotating the key does **not** reset the quota. A `429 RESOURCE_EXHAUSTED`
carries a `retryDelay` of seconds, which is misleading for a per-day window.

A `429` also proves the key is *valid* — an invalid key returns
`400 API_KEY_INVALID` instead.

### Measured consumption

Counted by walking the same graph against the deterministic stub, not
estimated.

| Suite | Tests | Model calls |
|---|---|---|
| Smoke (`test_gemini_live.py`, non-slow) | 13 | ~23 |
| Adversarial (`test_gemini_adversarial.py`) | 11 | ~29 |
| **Both** | **24** | **~52** |

Retries can multiply this: `MAX_RETRIES=2` means a worst case near 3×, still
comfortably inside 500.

---

## What requires a real model, and what does not

**Genuinely need Gemini** — these are the reason the suite exists:

- `test_configured_model_responds`, `test_provider_reports_real_token_usage`,
  `test_structured_output_honours_the_schema`,
  `test_thinking_disabled_leaves_budget_for_output` — real provider behaviour:
  token accounting, schema-constrained output, thinking budget
- every adversarial case — a keyword stub cannot be *persuaded*; that is the
  whole point of running them against a real model
- `test_live_costs_are_recorded_against_the_real_model` — real token counts

**Do not need Gemini** — already covered deterministically offline:

- `test_model_is_in_the_rate_card` — pure configuration
- the authorisation, policy, fencing, confirmation, resource and concurrency
  properties, all pinned in `tests/security/`

**Must not run without explicit authorisation from the repository owner:**

| Suite | Cost | Marker |
|---|---|---|
| Curated 15-case subset | ~30 calls | `@pytest.mark.slow` |
| Full 68-case evaluation | ~136 calls | `agent-platform eval` |

---

## Procedure

Run in order. Stop at the first step that fails for a reason other than quota.

```bash
# 1. Availability. One call. Do not loop this to force a reset.
python -c "import os;from dotenv import load_dotenv;from google import genai;from google.genai import types;load_dotenv();c=genai.Client(api_key=os.getenv('GEMINI_API_KEY'));c.models.generate_content(model='gemini-3.5-flash-lite',contents='ping',config=types.GenerateContentConfig(max_output_tokens=8));print('quota available')"

# 2. Smoke: 13 tests, ~23 calls.
pytest tests/live/test_gemini_live.py -m "live and not slow" -q

# 3. Adversarial: 11 tests, ~29 calls.
pytest tests/live/test_gemini_adversarial.py -m live -q

# 4. Deterministic suite must still be green.
pytest

# 5. Gates.
ruff check . && mypy src/agent_platform && pip-audit
```

Sequencing note: the live tests share a per-day quota, so run smoke before
adversarial and read the first failure carefully. Within each file the tests
are independent — each builds its own platform and repository — so a failure
does not invalidate the ones that already ran.

---

## Reading the result

| Outcome | Meaning |
|---|---|
| **passed** | the model was reached and the property held |
| **skipped** (F12 reason) | the model was never reached — **not** a pass |
| **failed**, 429 in the error | quota, not a defect. Record it and stop the live round |
| **failed**, anything else | a real finding — reproduce, write a regression, fix |

If the only failures are quota, do **not** change code to mask them. Record the
state and wait for the reset.

---

## After a successful round

Audit for secret containment against the traces the run just produced —
responses, events, SQLite, files, logs, errors, and the rendered dashboard.
Search for fragments as well as the whole value, because a truncated key is
still a leak and truncation is exactly how one escapes a character clamp.

Report presence or absence only. Never print what was found.
