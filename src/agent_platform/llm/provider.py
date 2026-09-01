"""Provider-agnostic LLM interface.

The orchestrator and agents depend only on :class:`LLMProvider`. Adding
OpenAI or Anthropic support means adding one module here; nothing in
``agent/`` or ``orchestration/`` changes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class Purpose(StrEnum):
    """Why a model call is being made.

    Carried on every call so that traces and cost records can attribute spend
    to a specific step. Real providers do not change their behaviour based on
    it; the deterministic stub uses it to select a canned response shape.
    """

    ROUTE = "route"
    #: The researcher choosing which read-only lookup to request. Distinct from
    #: PROPOSE_ACTION so that cost and traces separate "deciding what to look
    #: up" from "deciding what to change".
    SELECT_TOOL = "select_tool"
    RESEARCH = "research"
    PROPOSE_ACTION = "propose_action"
    VALIDATE = "validate"
    RESPOND = "respond"
    JUDGE = "judge"


class LLMError(RuntimeError):
    """Base class for provider failures."""


class LLMTimeoutError(LLMError):
    """The provider did not respond within the configured timeout."""


class LLMUnavailableError(LLMError):
    """The provider could not be reached or is not configured."""


class LLMResponseError(LLMError):
    """The provider responded, but not in a usable form."""


class EmbedTask(StrEnum):
    """What an embedding is for.

    Retrieval embeddings are asymmetric: a stored document and the question
    asked of it are embedded differently, because a passage and a query are not
    the same kind of text. Carrying the distinction here means the retriever
    cannot accidentally embed a query as though it were a document, which
    quietly degrades every similarity score that follows.
    """

    DOCUMENT = "RETRIEVAL_DOCUMENT"
    QUERY = "RETRIEVAL_QUERY"


@dataclass(frozen=True, slots=True)
class Embedding:
    """One vector, plus what produced it.

    ``model`` is recorded because vectors from different models are not
    comparable. An index that forgets which model built it will happily return
    nonsense rather than an error, so the provenance travels with the vector.
    """

    vector: tuple[float, ...]
    provider: str
    model: str
    task: EmbedTask
    latency_ms: float = 0.0

    @property
    def dimensions(self) -> int:
        return len(self.vector)


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """A single completion plus the accounting metadata the platform needs."""

    text: str
    provider: str
    model: str
    purpose: Purpose
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    finish_reason: str | None = None
    #: True when token counts are approximations rather than provider-reported.
    tokens_estimated: bool = False

    # --- what it took to get this answer -----------------------------------
    #
    # ``latency_ms`` above is the *successful* attempt. When a provider retries
    # internally, everything before the attempt that worked -- the failures and
    # the backoff between them -- is not in that number and used to be in no
    # number at all: a call that spent thirty-six seconds losing to 503s and
    # then succeeded in eight hundred milliseconds was recorded as eight
    # hundred milliseconds. The three fields below carry the rest of the story
    # so the agent can record it.
    #
    # All three have defaults, so every existing construction keeps working and
    # a provider that never retries reports the truth by saying nothing.

    #: Physical attempts made, including the one that succeeded. 1 means no retry.
    attempts: int = 1
    #: Wall-clock across every attempt and every backoff, not just the last try.
    total_elapsed_ms: float = 0.0
    #: Why each failed attempt failed, oldest first, already scrubbed by the
    #: provider. Never carries a credential: see ``GeminiProvider._scrub``.
    retry_reasons: tuple[str, ...] = ()

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def retried(self) -> bool:
        return self.attempts > 1


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded retry with exponential backoff.

    Only applied to model calls, which are idempotent reads. Tool invocations
    are never retried automatically by the gateway, because the registry
    contains non-idempotent operations.
    """

    max_attempts: int = 3
    initial_backoff_seconds: float = 0.5
    backoff_multiplier: float = 2.0
    max_backoff_seconds: float = 8.0

    def backoff_for(self, attempt: int) -> float:
        delay = self.initial_backoff_seconds * (self.backoff_multiplier ** max(0, attempt - 1))
        return min(delay, self.max_backoff_seconds)


@runtime_checkable
class LLMProvider(Protocol):
    """Minimal surface every provider implementation must offer."""

    @property
    def name(self) -> str:
        """Short provider identifier recorded in traces, e.g. ``gemini``."""
        ...

    @property
    def model(self) -> str: ...

    def generate(
        self,
        prompt: str,
        *,
        purpose: Purpose,
        system: str | None = None,
        response_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
        timeout: float | None = None,
    ) -> LLMResponse: ...

    def embed(
        self,
        text: str,
        *,
        task: EmbedTask = EmbedTask.QUERY,
        timeout: float | None = None,
    ) -> Embedding:
        """Embed *text* as one vector.

        This verb exists so that nothing outside a provider ever needs to touch
        an SDK. An embedding is a *physical provider call* costing exactly what
        a completion costs against the daily quota, and the only way to keep it
        inside the budget is to make the budget-aware provider the sole route
        to it. A retriever reaching ``client.models.embed_content`` directly
        would spend quota the ceiling never sees.
        """
        ...


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)


def extract_json(text: str) -> dict[str, Any]:
    """Parse a JSON object from model output, tolerating markdown fences.

    Structured output is requested via ``response_schema`` wherever the
    provider supports it, so this is a fallback rather than the primary path.
    Raises :class:`LLMResponseError` when nothing parseable is present, so that
    a malformed response becomes a handled failure rather than a crash.
    """
    if not text or not text.strip():
        raise LLMResponseError("model returned empty output")

    candidates: list[str] = []
    fenced = _JSON_FENCE.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(text.strip())

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise LLMResponseError("model output did not contain a JSON object")


def estimate_tokens(text: str) -> int:
    """Rough token estimate used only when a provider reports no usage data.

    Approximately four characters per token. Any record derived from this is
    flagged ``tokens_estimated=True`` so the dashboard never presents an
    approximation as a measurement.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


@dataclass(slots=True)
class ProviderInfo:
    """Human-readable provider status, surfaced in the UI and README checks."""

    name: str
    model: str
    live: bool
    detail: str = ""
    extras: dict[str, str] = field(default_factory=dict)
