"""Centralised configuration, loaded once from the environment.

Every tunable in the platform is resolved here so that no module reads
``os.environ`` on its own. Monetary values use :class:`~decimal.Decimal`
because budget accounting accumulates many small numbers, and binary floating
point drifts in exactly the direction that makes a budget guard unreliable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final

from dotenv import load_dotenv

DEFAULT_MODEL: Final[str] = "gemini-3.5-flash-lite"
"""Default model. Overridable via ``GEMINI_MODEL``.

Chosen for this workload specifically. Every model call the platform makes is a
short, schema-constrained decision -- pick a route from an enum, name a tool and
its arguments, approve or reject a result. Those are latency-sensitive and
reasoning-light, which is exactly the Flash-Lite profile. It has a free tier,
is current-generation, and is substantially cheaper per token than full Flash
for this kind of work.

The rate card and the date it was last verified live in exactly one place:
``cost.pricing.PRICING`` and ``cost.pricing.PRICING_VERIFIED_ON``. They are
deliberately not restated here. This module sits *below* ``cost`` in the import
graph -- ``llm`` imports ``config``, so importing ``cost.pricing`` from here is
a circular import -- which means any copy kept in this file could only be a
hand-maintained one, and would go stale silently the next time pricing is
re-verified.

Upgrade path, if routing quality ever proves insufficient: ``gemini-3.7-flash``.
Set ``GEMINI_MODEL``; no code change is required.
"""

DEFAULT_THINKING_BUDGET: Final[int] = 0
"""Thinking tokens allowed per call. ``0`` disables thinking, ``-1`` is automatic.

Disabled by default, and this is a correctness measure rather than a cost one.
Thinking tokens are billed and counted as *output*, so on a thinking model they
compete with ``max_output_tokens``. A call that spends its whole budget thinking
returns empty text with ``finish_reason=MAX_TOKENS`` -- which for a platform
that only ever asks for a few dozen tokens of JSON is a pure failure mode.
"""

DEFAULT_MAX_OUTPUT_TOKENS: Final[int] = 2048

# --- Hard resource limits ----------------------------------------------------
#
# These bound a single request in units that have nothing to do with money.
# The budget guard caps *cost*, which is the wrong instrument on its own: the
# deterministic stub costs exactly $0, so a runaway loop is free and therefore
# unbounded under a purely monetary limit. These are the model-independent
# stops.
#
# Every default below is justified against measured behaviour -- see
# docs/architecture.md. None of them is derivable from model output.

DEFAULT_MAX_LLM_CALLS_PER_REQUEST: Final[int] = 12
"""Measured: 2-3 model calls for a typical request, ~8 worst case with retries."""

DEFAULT_MAX_TOOL_CALLS_PER_REQUEST: Final[int] = 8
"""Measured: 1-2 tool calls typical, ~4 with the retry ceiling."""

DEFAULT_REQUEST_DEADLINE_SECONDS: Final[float] = 180.0
"""Wall-clock ceiling for one request.

Independent of the graph recursion limit, which bounds *steps* rather than
time: 15 steps at a 30s model timeout is still 7 minutes. Measured live
latency is ~53s typical and ~62s throttled, so this is roughly 3x worst
observed.
"""

DEFAULT_MAX_TOOL_OUTPUT_BYTES: Final[int] = 32_768
"""Ceiling on a single tool result before it can enter context or a trace.

``max_context_items`` bounds the item *count*, not the byte size, so one large
result could still inflate every subsequent prompt.
"""

DEFAULT_MAX_QUESTION_CHARS: Final[int] = 100
"""How long one question from a person may be, in characters.

Deliberately its own number rather than a smaller ``max_input_chars``. That one
is the *context* budget: it is what ``fence_context`` clips each retrieved field
to, so lowering it to bound a question would truncate knowledge-base documents
to the length of a question and leave the answerer citing fragments. Two limits
because there are two things being limited.

Characters, not bytes and not UTF-16 units: ``len()`` in Python counts code
points, so an accented letter costs one and so does an emoji. The browser's
``maxlength`` counts UTF-16 units and is therefore *stricter* for anything
outside the BMP -- it can refuse early, never late -- which is why this side
stays the authority.
"""

DEFAULT_MAX_CONTEXT_TOTAL_CHARS: Final[int] = DEFAULT_MAX_TOOL_OUTPUT_BYTES * 2
"""Ceiling on the whole assembled context, after fencing.

The existing limits bound the wrong things to bound a total. ``max_context_items``
counts items, ``fence_context``'s budget clips each field, and the gateway caps
each tool result -- so the formal worst case multiplied out to twenty items of
five documents of two fields of eight thousand characters, around 800k. Only the
shape of the graph kept it far from that, and shape is not a limit.

Two full-size tool results' worth. Today's graph gathers one, so this is
headroom rather than a reduction, and it is expressed in terms of
``max_tool_output_bytes`` because that is what actually fills a context.
"""

DEFAULT_MAX_PENDING_CONFIRMATIONS: Final[int] = 50
"""Ceiling on suspended requests held in memory awaiting a human decision."""

DEFAULT_PENDING_SHARE_DIVISOR: Final[int] = 8
"""How the per-caller share of the pending store is derived from the whole.

The ceiling above bounds the store; it does not say who may fill it. A red-team
pass showed what that omission is worth: one visitor, inside its own rate limit,
took all fifty slots in six minutes, and every other visitor's high-risk action
was then refused before a human ever saw it. Refusing rather than evicting is
the right call and is why nothing was *lost* -- but availability of the approval
mechanism is exactly what an attacker was able to remove.

So capacity is shared as well as bounded. An eighth leaves room for eight
concurrent callers at the derived value and is deliberately not generous: a
single request suspends once, so needing more than a handful at a time is
already unusual for one caller.
"""

DEFAULT_MIN_PENDING_PER_QUOTA_KEY: Final[int] = 2
"""Floor on a derived share, so a small deployment stays usable.

With a global cap of 8 the divisor alone would give 1, and a caller who
suspended one action could not raise a second while the first was being read.
Two is the smallest number that keeps the feature usable at any cap.
"""

DEFAULT_CONFIRMATION_TTL_SECONDS: Final[float] = 900.0
"""How long a suspended action stays resumable.

A 15-minute-old approval should not execute against state that may have moved
on. Expiry is checked at resume time and the action is refused, never run.
"""

DEFAULT_DEMO_REQUESTS_PER_MINUTE: Final[int] = 60
"""Per-caller ceiling when nothing sets one, which in practice means the demo.

It was 10, sized for a deployment. A visitor clicking through the dashboard's
own worked examples makes more requests than that in a minute, so the control
that exists to stop abuse was stopping the demonstration instead -- and it
surfaced as a refusal, which reads like the product is broken.

Sixty is still a limit and still demonstrable: it binds well before anything a
person can do by hand, and `tests/integration/test_scale.py` exercises the
limiter directly rather than relying on this number. Deployments set their own
value: `k8s/configmap.yaml` keeps 10, which is the figure that matters where
there is more than one caller.
"""

DEFAULT_GLOBAL_LIMIT_MULTIPLIER: Final[int] = 10
"""How much headroom the deployment-wide ceiling has over one caller's.

``REQUESTS_PER_MINUTE`` is a *fairness* control: what one caller may do. The
global ceiling is a *capacity* control: what the deployment will absorb in
total. While there was one caller they were the same number, and the difference
did not have to be named.

Per-principal quota makes them different controls, and setting them to the same
value would defeat the point of having both -- one caller filling the shared
bucket would still deny every other, which is the denial-of-service that
per-principal quota exists to close. So the backstop sits an order of magnitude
above one caller's share: high enough to be a backstop rather than the binding
constraint, low enough to still stop an aggregate stampede. Override with
``GLOBAL_REQUESTS_PER_MINUTE`` / ``GLOBAL_REQUESTS_PER_HOUR``.
"""

DEFAULT_CIRCUIT_FAILURE_THRESHOLD: Final[int] = 5
"""Consecutive provider failures before the circuit opens."""

DEFAULT_CIRCUIT_COOLDOWN_SECONDS: Final[float] = 60.0
"""How long the circuit stays open before a single trial call is allowed."""


class ConfigError(ValueError):
    """Raised when an environment value is present but unusable."""


def _get_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def _get_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def _get_decimal(name: str, default: str) -> Decimal:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        raw = default
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ConfigError(f"{name} must be a decimal number, got {raw!r}") from exc
    if value < 0:
        raise ConfigError(f"{name} must not be negative, got {value}")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable runtime configuration."""

    #: repr=False is a security control, not cosmetics.
    #:
    #: F6: a dataclass repr includes every field, so any traceback, log line or
    #: debugger that rendered a Settings object printed the key in cleartext.
    #: This was found the hard way -- pytest assertion introspection dumped a
    #: fixture repr into test output and leaked a live key. Regression:
    #: test_settings_repr_never_contains_the_key.
    gemini_api_key: str | None = field(repr=False)
    gemini_model: str
    gemini_thinking_budget: int
    gemini_max_output_tokens: int
    database_path: Path
    environment: str
    log_level: str

    max_retries: int
    recursion_limit: int
    llm_timeout: float
    tool_timeout: float

    #: What **one caller** may do. Unchanged in meaning: while there was one
    #: caller this was also the deployment total, and for the CLI, the
    #: dashboard and the evaluator -- which all share the global bucket -- it
    #: still is.
    requests_per_minute: int
    requests_per_hour: int

    daily_budget_usd: Decimal
    max_request_cost_usd: Decimal

    max_input_chars: int
    max_context_items: int
    max_trace_payload_chars: int

    max_llm_calls_per_request: int
    max_tool_calls_per_request: int
    request_deadline_seconds: float
    max_tool_output_bytes: int
    max_pending_confirmations: int
    confirmation_ttl_seconds: float
    circuit_failure_threshold: int
    circuit_cooldown_seconds: float

    #: Where shared state lives. Empty means process-local state, which is the
    #: default and keeps single-process deployments and the offline suite
    #: working exactly as before. Set it and the platform switches the state
    #: that is semantically global -- pending confirmations, the provider-call
    #: ledger, the rate limiter, the graph checkpointer -- onto Redis.
    #:
    #: Held with ``repr=False`` for the same reason as the API key: a URL can
    #: carry a password, and a fixture repr in a traceback is exactly how a
    #: credential reaches a log.
    redis_url: str | None = field(default=None, repr=False)

    #: How tool calls reach their handlers.
    #:
    #: ``inprocess`` (default) calls the handler directly, which is what every
    #: earlier version did. ``mcp`` puts a real MCP server between the gateway
    #: and the tools, so the execution boundary becomes a process boundary and
    #: has to be proven rather than assumed.
    tool_transport: str = "inprocess"

    #: HMAC key for execution grants. Required by ``mcp`` transport and unused
    #: by ``inprocess``.
    #:
    #: ``repr=False`` for the same reason as the API key and the Redis URL: the
    #: way a secret reaches a log is a fixture repr in somebody's traceback.
    execution_grant_secret: str | None = field(default=None, repr=False)

    #: Durable storage. Empty means the SQLite file at ``database_path``, which
    #: is the default and what every earlier version used.
    #:
    #: Set it and requests, events and recorded spend move to PostgreSQL, shared
    #: by every replica. That matters for one reason above the others:
    #: ``BudgetGuard`` reads the day's spend from the repository, so a per-pod
    #: database means each replica allows the full daily budget on its own.
    #:
    #: ``repr=False`` -- a connection string carries a password.
    database_url: str | None = field(default=None, repr=False)

    #: What the **deployment** will absorb in total, across every caller. See
    #: ``DEFAULT_GLOBAL_LIMIT_MULTIPLIER`` for why this is a separate number
    #: rather than the one above.
    #:
    #: ``0`` means "not configured", and the effective value is then derived
    #: from the per-caller limit. Read them through
    #: :attr:`effective_global_requests_per_minute` rather than directly, so
    #: that a ``Settings`` built in a test does not silently end up with a
    #: deployment ceiling *below* one caller's share.
    global_requests_per_minute: int = 0
    global_requests_per_hour: int = 0

    #: How many suspended actions **one caller** may hold at once.
    #:
    #: The same shape as the pair above, and for the same reason: the store's
    #: global ceiling is a capacity control, and this is a fairness control. A
    #: capacity control on its own says how much there is, never who gets it,
    #: which is how one visitor came to hold all fifty slots while everyone
    #: else was refused.
    #:
    #: ``0`` means "not configured" and derives a share from
    #: :attr:`max_pending_confirmations`. Read it through
    #: :attr:`effective_max_pending_per_quota_key`, which also clamps an
    #: explicit value to the global cap -- a share larger than the whole store
    #: is not a share, and configuring one would silently restore the old
    #: behaviour under a name that suggests otherwise.
    max_pending_per_quota_key: int = 0

    #: ``enforced`` (the default) or ``disabled``.
    #:
    #: There is no third state and no implicit one. With no credentials
    #: configured the API **refuses to start** rather than serving open, for the
    #: same reason an unreachable ``REDIS_URL`` raises instead of falling back:
    #: a silent downgrade means an operator asked for one thing and got another,
    #: with nothing to say so. Running without authentication stays possible and
    #: has to be typed out.
    api_auth_mode: str = "enforced"

    #: The credential table, inline. See ``security.api_auth.parse_credentials``
    #: for the format. Holds sha256 digests rather than secrets, but held
    #: ``repr=False`` anyway -- there is no reason for a credential table to
    #: turn up in somebody's traceback.
    api_auth_keys: str | None = field(default=None, repr=False)

    #: The same table, read from a file. Preferred in Kubernetes, where it is a
    #: Secret mounted at ``0400``: environment variables are readable through
    #: ``/proc/<pid>/environ``, land in crash dumps, and are **inherited by
    #: child processes** -- and this platform spawns one when
    #: ``TOOL_TRANSPORT=mcp``.
    api_auth_keys_file: Path | None = None

    #: Ceiling on one question from a person, in characters. Separate from
    #: ``max_input_chars``, which is the context budget -- see
    #: :data:`DEFAULT_MAX_QUESTION_CHARS`.
    max_question_chars: int = DEFAULT_MAX_QUESTION_CHARS

    #: Ceiling on the assembled context handed to a model, in characters and
    #: after fencing. Complements the per-field and per-item limits above,
    #: which between them cannot bound a total. See
    #: :data:`DEFAULT_MAX_CONTEXT_TOTAL_CHARS`.
    max_context_total_chars: int = DEFAULT_MAX_CONTEXT_TOTAL_CHARS

    def __repr__(self) -> str:
        """Render without the credential, in any context that reprs Settings."""
        return (
            f"Settings(environment={self.environment!r}, "
            f"gemini_model={self.gemini_model!r}, "
            f"gemini_api_key={'<configured>' if self.gemini_api_key else None}, "
            f"demo_mode={self.demo_mode})"
        )

    __str__ = __repr__

    @property
    def effective_global_requests_per_minute(self) -> int:
        """The deployment ceiling actually enforced, per minute."""
        return self.global_requests_per_minute or (
            self.requests_per_minute * DEFAULT_GLOBAL_LIMIT_MULTIPLIER
        )

    @property
    def effective_global_requests_per_hour(self) -> int:
        """The deployment ceiling actually enforced, per hour."""
        return self.global_requests_per_hour or (
            self.requests_per_hour * DEFAULT_GLOBAL_LIMIT_MULTIPLIER
        )

    @property
    def effective_max_pending_per_quota_key(self) -> int:
        """How many suspended actions one caller may hold, as enforced.

        Clamped to the global cap in both directions. Deriving can only ever
        produce a value at or below it, but an operator setting the variable
        directly can overshoot, and a per-caller share above the whole store
        is the global cap wearing a different name.
        """
        configured = self.max_pending_per_quota_key or max(
            DEFAULT_MIN_PENDING_PER_QUOTA_KEY,
            self.max_pending_confirmations // DEFAULT_PENDING_SHARE_DIVISOR,
        )
        return max(1, min(configured, self.max_pending_confirmations))

    @property
    def demo_mode(self) -> bool:
        """True when no API key is configured and the stub provider is used.

        Demo mode never fabricates model output attributed to Gemini; the stub
        provider identifies itself as ``stub`` in every trace it produces.
        """
        return not self.gemini_api_key

    @classmethod
    def from_env(cls, *, load_dotenv_file: bool = True) -> Settings:
        if load_dotenv_file:
            load_dotenv(override=False)

        key = os.getenv("GEMINI_API_KEY", "").strip()

        return cls(
            gemini_api_key=key or None,
            gemini_model=os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_MODEL,
            gemini_thinking_budget=_get_int(
                "GEMINI_THINKING_BUDGET", DEFAULT_THINKING_BUDGET, minimum=-1
            ),
            gemini_max_output_tokens=_get_int(
                "GEMINI_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS, minimum=64
            ),
            database_path=Path(os.getenv("DATABASE_PATH", "").strip() or "data/agent_platform.db"),
            environment=os.getenv("ENVIRONMENT", "").strip() or "demo",
            log_level=(os.getenv("LOG_LEVEL", "").strip() or "INFO").upper(),
            max_retries=_get_int("MAX_RETRIES", 2),
            recursion_limit=_get_int("RECURSION_LIMIT", 15, minimum=1),
            llm_timeout=_get_float("LLM_TIMEOUT", 30.0, minimum=0.1),
            tool_timeout=_get_float("TOOL_TIMEOUT", 10.0, minimum=0.1),
            requests_per_minute=_get_int(
                "REQUESTS_PER_MINUTE", DEFAULT_DEMO_REQUESTS_PER_MINUTE, minimum=1
            ),
            requests_per_hour=_get_int("REQUESTS_PER_HOUR", 100, minimum=1),
            daily_budget_usd=_get_decimal("DAILY_BUDGET", "1.00"),
            max_request_cost_usd=_get_decimal("MAX_REQUEST_COST", "0.05"),
            max_input_chars=_get_int("MAX_INPUT_CHARS", 8000, minimum=1),
            max_context_items=_get_int("MAX_CONTEXT_ITEMS", 20, minimum=1),
            max_question_chars=_get_int(
                "MAX_QUESTION_CHARS", DEFAULT_MAX_QUESTION_CHARS, minimum=1
            ),
            max_context_total_chars=_get_int(
                "MAX_CONTEXT_TOTAL_CHARS",
                DEFAULT_MAX_CONTEXT_TOTAL_CHARS,
                minimum=256,
            ),
            max_trace_payload_chars=_get_int("MAX_TRACE_PAYLOAD_CHARS", 500, minimum=0),
            # Hard resource limits. Minimums are all >= 1 so a misconfigured
            # value cannot silently disable a limit entirely.
            max_llm_calls_per_request=_get_int(
                "MAX_LLM_CALLS_PER_REQUEST", DEFAULT_MAX_LLM_CALLS_PER_REQUEST, minimum=1
            ),
            max_tool_calls_per_request=_get_int(
                "MAX_TOOL_CALLS_PER_REQUEST", DEFAULT_MAX_TOOL_CALLS_PER_REQUEST, minimum=1
            ),
            request_deadline_seconds=_get_float(
                "REQUEST_DEADLINE_SECONDS", DEFAULT_REQUEST_DEADLINE_SECONDS, minimum=1.0
            ),
            max_tool_output_bytes=_get_int(
                "MAX_TOOL_OUTPUT_BYTES", DEFAULT_MAX_TOOL_OUTPUT_BYTES, minimum=256
            ),
            max_pending_confirmations=_get_int(
                "MAX_PENDING_CONFIRMATIONS", DEFAULT_MAX_PENDING_CONFIRMATIONS, minimum=1
            ),
            # ``minimum=0`` because 0 is the "derive it" value, exactly as it is
            # for the global rate ceilings above.
            max_pending_per_quota_key=_get_int("MAX_PENDING_CONFIRMATIONS_PER_KEY", 0),
            confirmation_ttl_seconds=_get_float(
                "CONFIRMATION_TTL_SECONDS", DEFAULT_CONFIRMATION_TTL_SECONDS, minimum=1.0
            ),
            circuit_failure_threshold=_get_int(
                "CIRCUIT_FAILURE_THRESHOLD", DEFAULT_CIRCUIT_FAILURE_THRESHOLD, minimum=1
            ),
            circuit_cooldown_seconds=_get_float(
                "CIRCUIT_COOLDOWN_SECONDS", DEFAULT_CIRCUIT_COOLDOWN_SECONDS, minimum=1.0
            ),
            redis_url=os.getenv("REDIS_URL", "").strip() or None,
            tool_transport=os.getenv("TOOL_TRANSPORT", "inprocess").strip().lower()
            or "inprocess",
            execution_grant_secret=os.getenv("EXECUTION_GRANT_SECRET", "").strip() or None,
            database_url=os.getenv("DATABASE_URL", "").strip() or None,
            global_requests_per_minute=_get_int("GLOBAL_REQUESTS_PER_MINUTE", 0),
            global_requests_per_hour=_get_int("GLOBAL_REQUESTS_PER_HOUR", 0),
            api_auth_mode=os.getenv("API_AUTH_MODE", "").strip().lower() or "enforced",
            api_auth_keys=os.getenv("API_AUTH_KEYS", "").strip() or None,
            api_auth_keys_file=(
                Path(keys_file)
                if (keys_file := os.getenv("API_AUTH_KEYS_FILE", "").strip())
                else None
            ),
        )

    def describe(self) -> dict[str, str]:
        """Configuration summary safe to display or log.

        The API key is reduced to a presence flag; its value never leaves this
        object.
        """
        return {
            "environment": self.environment,
            "gemini_model": self.gemini_model,
            "gemini_thinking_budget": str(self.gemini_thinking_budget),
            "gemini_max_output_tokens": str(self.gemini_max_output_tokens),
            "gemini_api_key": "configured" if self.gemini_api_key else "not configured",
            "tool_transport": self.tool_transport,
            "database_backend": "postgres" if self.database_url else "sqlite",
            "api_auth_mode": self.api_auth_mode,
            "execution_grant_secret": (
                "configured" if self.execution_grant_secret else "not configured"
            ),
            "demo_mode": str(self.demo_mode),
            "database_path": str(self.database_path),
            "max_retries": str(self.max_retries),
            "daily_budget_usd": str(self.daily_budget_usd),
            "max_request_cost_usd": str(self.max_request_cost_usd),
            "requests_per_minute": str(self.requests_per_minute),
            "requests_per_hour": str(self.requests_per_hour),
            "global_requests_per_minute": str(self.effective_global_requests_per_minute),
            "global_requests_per_hour": str(self.effective_global_requests_per_hour),
            "max_llm_calls_per_request": str(self.max_llm_calls_per_request),
            "max_tool_calls_per_request": str(self.max_tool_calls_per_request),
            "request_deadline_seconds": str(self.request_deadline_seconds),
            "max_tool_output_bytes": str(self.max_tool_output_bytes),
            "max_pending_confirmations": str(self.max_pending_confirmations),
            "max_pending_per_quota_key": str(self.effective_max_pending_per_quota_key),
            "confirmation_ttl_seconds": str(self.confirmation_ttl_seconds),
            "circuit_failure_threshold": str(self.circuit_failure_threshold),
            "circuit_cooldown_seconds": str(self.circuit_cooldown_seconds),
        }
