"""Gemini provider built on the ``google-genai`` SDK.

Verified against google-genai 2.20.0. Details that are easy to get wrong and
are handled explicitly here:

* ``types.HttpOptions.timeout`` is in **milliseconds**, not seconds.
* ``ClientError`` (4xx) is permanent and is never retried; only ``ServerError``
  (5xx) and transport errors are.
* A 200 response can still carry no usable text -- safety block, token ceiling,
  or thinking that consumed the whole output budget. Each is classified rather
  than being allowed to surface as a confusing JSON parse error.
* Exception text from the SDK is sanitised before it is allowed anywhere near a
  trace or a user-visible message.
"""

from __future__ import annotations

import time
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from ..security.sanitization import sanitize_text
from .authorization import require_live_authorisation
from .budget import ProviderBudget, ProviderBudgetExhausted
from .errors import (
    SAFETY_FINISH_REASONS,
    TRUNCATION_FINISH_REASONS,
    LLMEmptyResponseError,
    LLMSafetyBlockedError,
    LLMTruncatedError,
    normalize_finish_reason,
)
from .provider import (
    Embedding,
    EmbedTask,
    LLMResponse,
    LLMResponseError,
    LLMTimeoutError,
    LLMUnavailableError,
    Purpose,
    RetryPolicy,
    estimate_tokens,
)

PROVIDER_NAME = "gemini"

#: Embeddings come from a different model than completions -- a chat model
#: cannot produce them. Named separately so the two can be configured, priced
#: and reasoned about independently.
#:
#: NOT VERIFIED AGAINST THE LIVE API. No embedding call has been made from this
#: project. The name is taken from the SDK's published model list; if it is
#: wrong, the first live call fails with a 4xx that names the model, which is a
#: loud and cheap failure. It is a constructor argument so that correcting it
#: needs no code change.
DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"


def _may_be_a_thinking_rejection(exc: Exception) -> bool:
    """Is this error ambiguous enough to be worth one probe call?

    F14: the probe used to run on *any* 4xx raised while a thinking budget was
    attached. That was too broad in both directions.

    * It cost a second HTTP call on errors that were never ambiguous -- and on
      a 429 that second call is spent against the quota that is already
      exhausted, so the platform burns quota twice as fast exactly when it can
      least afford to.
    * Worse, it then set ``_thinking_supported = False`` for the life of the
      provider, degrading every later request because of an error that said
      nothing whatsoever about thinking.

    Only a bare ``400 INVALID_ARGUMENT`` is genuinely ambiguous: the live API
    rejects an unsupported thinking budget without naming the offending field,
    which is why the probe exists at all. A 429, a 403, or a 400 that names its
    own cause are all self-explanatory.
    """
    code = getattr(exc, "code", None)
    if code is not None and code != 400:
        return False

    status = getattr(exc, "status", None)
    if status is not None and status != "INVALID_ARGUMENT":
        return False

    # A 400 that names its own problem is not ambiguous either.
    message = str(getattr(exc, "message", "") or exc)
    return "API key not valid" not in message


class GeminiProvider:
    """Live provider. Instantiating it requires an API key."""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        timeout_seconds: float = 30.0,
        retry_policy: RetryPolicy | None = None,
        max_output_tokens: int = 2048,
        thinking_budget: int = 0,
        budget: ProviderBudget | None = None,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        client: Any | None = None,
    ) -> None:
        if not api_key:
            raise LLMUnavailableError(
                "GeminiProvider requires an API key; use StubProvider for demo mode"
            )
        self._api_key = api_key
        self._model = model
        self._embedding_model = embedding_model
        self._timeout_ms = max(1, int(timeout_seconds * 1000))
        self._retry = retry_policy or RetryPolicy()
        self._max_output_tokens = max_output_tokens
        self._thinking_budget = thinking_budget
        #: Daily ceiling on *physical* calls. Charged per attempt inside
        #: the retry loop, because that is where the calls actually happen.
        self._budget = budget
        #: Set to False after the API rejects the thinking field, so an
        #: unsupported model degrades to one wasted call rather than failing
        #: every request.
        self._thinking_supported = True
        if client is None:
            # Second layer, and the one that satisfies "blocked before the SDK
            # client exists". ``build_provider`` already refuses, but this class
            # is also constructed directly -- by tests, and by anything that
            # bypasses the factory -- so the guarantee is restated where the
            # real client is actually made.
            require_live_authorisation("create a Gemini API client")
            try:
                self._client = genai.Client(api_key=api_key)
            except Exception as exc:
                raise LLMUnavailableError(
                    f"could not initialise Gemini client: {self._scrub(exc)}"
                ) from exc
        else:
            # The injection seam exists for fakes. A *real* client handed in
            # through it still needs authorisation, or the seam would be the
            # way around the gate rather than a way to test without one.
            if isinstance(client, genai.Client):
                require_live_authorisation("use an injected Gemini API client")
            self._client = client

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @property
    def model(self) -> str:
        return self._model

    # ------------------------------------------------------------------ safety

    def _scrub(self, exc: object) -> str:
        """Render an SDK exception as text that is safe to store or display.

        SDK errors can echo request context. The configured key is redacted by
        value and the result is passed through the standard sanitiser, so a
        provider error can never become the thing that leaks the credential.
        """
        text, _ = sanitize_text(str(exc), max_chars=300, known_secrets=(self._api_key,))
        return text

    # ------------------------------------------------------------------ config

    def _build_config(
        self,
        *,
        system: str | None,
        response_schema: dict[str, Any] | None,
        temperature: float,
        include_thinking: bool,
    ) -> types.GenerateContentConfig:
        kwargs: dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": self._max_output_tokens,
            "http_options": types.HttpOptions(timeout=self._timeout_ms),
        }
        if system:
            kwargs["system_instruction"] = system
        if response_schema is not None:
            # Structured output: the model is constrained to the schema rather
            # than being asked politely to emit JSON.
            kwargs["response_mime_type"] = "application/json"
            kwargs["response_schema"] = response_schema
        if include_thinking:
            kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=self._thinking_budget
            )
        return types.GenerateContentConfig(**kwargs)

    # ------------------------------------------------------------------- usage

    @staticmethod
    def _usage(response: Any, prompt: str, text: str) -> tuple[int, int, int, bool]:
        """Read provider-reported usage.

        Returns ``(input, output, thoughts, estimated)``. Thinking tokens are
        billed as output, so they are added to the output total; omitting them
        would understate cost.
        """
        usage = getattr(response, "usage_metadata", None)
        if usage is not None and getattr(usage, "prompt_token_count", None) is not None:
            thoughts = getattr(usage, "thoughts_token_count", None) or 0
            output = (getattr(usage, "candidates_token_count", None) or 0) + thoughts
            return int(usage.prompt_token_count), int(output), int(thoughts), False
        return estimate_tokens(prompt), estimate_tokens(text), 0, True

    def _validate_response(
        self, response: Any, text: str, output_tokens: int, thought_tokens: int
    ) -> None:
        """Reject a 200 that carries no usable content.

        Each branch raises a distinct, handled error so the caller can respond
        appropriately and the trace records what actually happened.
        """
        candidates = getattr(response, "candidates", None) or []
        finish = normalize_finish_reason(
            getattr(candidates[0], "finish_reason", None) if candidates else None
        )

        if finish in SAFETY_FINISH_REASONS:
            raise LLMSafetyBlockedError(finish)

        if text.strip():
            return

        if finish in TRUNCATION_FINISH_REASONS:
            raise LLMTruncatedError(output_tokens, thought_tokens)

        raise LLMEmptyResponseError(
            f"provider returned no text (finish_reason={finish or 'unknown'})"
        )

    # ---------------------------------------------------------------- generate

    def generate(
        self,
        prompt: str,
        *,
        purpose: Purpose,
        system: str | None = None,
        response_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
        timeout: float | None = None,
    ) -> LLMResponse:
        last_error: Exception | None = None
        # Outside the loop on purpose. `started` below is reset per attempt
        # and measures the try that worked; this one measures what the caller
        # actually waited, backoff included.
        call_started = time.perf_counter()
        failures: list[str] = []
        # Summed across attempts: the ledger is charged once per physical
        # attempt, and each charge can block independently.
        budget_wait = 0.0

        for attempt in range(1, self._retry.max_attempts + 1):
            config = self._build_config(
                system=system,
                response_schema=response_schema,
                temperature=temperature,
                include_thinking=self._thinking_supported,
            )
            # Charged per *physical* attempt, not per logical call. A retry
            # spends quota exactly like a first try, so it is charged exactly
            # like one -- and refused when the day's allowance cannot afford
            # it. Checked here, immediately before the SDK call, so nothing can
            # slip between the decision and the request.
            if self._budget is not None:
                # Timed, not changed. The ledger runs a BEGIN IMMEDIATE
                # transaction that blocks while another process holds it,
                # and it sits *before* the stopwatch below -- so until now
                # the wait was spent inside this method and reported by no
                # field at all. Two recorded requests showed a thirty-second
                # gap between an agent starting and its model call, with the
                # call itself taking under two seconds; whether the ledger
                # is the cause is exactly what this measures.
                budget_started = time.perf_counter()
                granted = self._budget.try_consume()
                budget_wait += (time.perf_counter() - budget_started) * 1000.0
                if not granted:
                    raise ProviderBudgetExhausted(
                        "the daily provider-call budget for this deployment "
                        "is spent; no further calls will be made today"
                    )

            started = time.perf_counter()
            try:
                response = self._client.models.generate_content(
                    model=self._model, contents=prompt, config=config
                )
            except genai_errors.ClientError as exc:
                if self._thinking_supported and _may_be_a_thinking_rejection(exc):
                    # A 4xx on an attempt that carried a thinking budget is most
                    # likely the model refusing that budget. Verified against the
                    # live API: gemini-3.5-flash-lite rejects thinking_budget=0
                    # with a bare "400 INVALID_ARGUMENT. Request contains an
                    # invalid argument" -- the offending field is *not* named, so
                    # matching on the message text is not possible.
                    #
                    # Retry once without the thinking config and remember the
                    # result. This masks nothing: if the request was malformed
                    # for any other reason it fails again on the retry and is
                    # raised then. The cost of disambiguating is one call, once
                    # per provider instance.
                    self._thinking_supported = False
                    last_error = exc
                    failures.append(self._scrub(exc))
                    continue
                # Every other 4xx -- quota, permissions, a named bad key, or a
                # 400 that says what was wrong. Retrying cannot help and would
                # burn quota, so it is raised on the first response.
                raise LLMUnavailableError(
                    f"Gemini rejected the request: {self._scrub(exc)}"
                ) from exc
            except genai_errors.ServerError as exc:
                last_error = exc
                failures.append(self._scrub(exc))
            except TimeoutError as exc:
                last_error = exc
                failures.append(self._scrub(exc))
            except Exception as exc:
                last_error = exc
                failures.append(self._scrub(exc))
            else:
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                text = response.text or ""
                input_tokens, output_tokens, thought_tokens, estimated = self._usage(
                    response, prompt, text
                )
                # Raises on safety blocks, truncation and empty responses.
                self._validate_response(response, text, output_tokens, thought_tokens)

                candidates = getattr(response, "candidates", None) or []
                finish = normalize_finish_reason(
                    getattr(candidates[0], "finish_reason", None) if candidates else None
                )
                return LLMResponse(
                    text=text,
                    provider=self.name,
                    model=self._model,
                    purpose=purpose,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=elapsed_ms,
                    finish_reason=finish or None,
                    tokens_estimated=estimated,
                    attempts=attempt,
                    total_elapsed_ms=(time.perf_counter() - call_started) * 1000.0,
                    retry_reasons=tuple(failures),
                    budget_wait_ms=budget_wait,
                )

            if attempt < self._retry.max_attempts:
                time.sleep(self._retry.backoff_for(attempt))

        message = (
            f"Gemini call failed after {self._retry.max_attempts} attempts: "
            f"{self._scrub(last_error)}"
        )
        if isinstance(last_error, TimeoutError):
            raise LLMTimeoutError(message) from last_error
        raise LLMUnavailableError(message) from last_error

    # --------------------------------------------------------------- embedding

    def embed(
        self,
        text: str,
        *,
        task: EmbedTask = EmbedTask.QUERY,
        timeout: float | None = None,
    ) -> Embedding:
        """Embed *text*, charging the budget for every physical attempt.

        Deliberately the same shape as :meth:`generate`: one retry loop, the
        budget charged inside it immediately before the SDK call, and no way
        for an attempt to reach the network without being counted first. An
        embedding costs the same quota as a completion, so it is bounded the
        same way and by the same counter.

        The duplication with ``generate`` is accepted rather than factored out.
        A shared helper would put the budget check one call away from the SDK
        call it guards, and the whole property being defended here is that
        nothing sits between the two.
        """
        if not text.strip():
            raise LLMResponseError("cannot embed empty text")

        timeout_ms = max(1, int(timeout * 1000)) if timeout is not None else self._timeout_ms
        last_error: Exception | None = None

        for attempt in range(1, self._retry.max_attempts + 1):
            # Charged per *physical* attempt, before the call, exactly as in
            # generate(). A retried embedding spends quota twice and is
            # therefore charged twice.
            if self._budget is not None and not self._budget.try_consume():
                raise ProviderBudgetExhausted(
                    "the daily provider-call budget for this deployment is "
                    "spent; no further calls will be made today"
                )

            started = time.perf_counter()
            try:
                response = self._client.models.embed_content(
                    model=self._embedding_model,
                    contents=text,
                    config=types.EmbedContentConfig(
                        task_type=task.value,
                        http_options=types.HttpOptions(timeout=timeout_ms),
                    ),
                )
            except genai_errors.ClientError as exc:
                # A 4xx is a rejection, not a fault: retrying spends quota to
                # be told the same thing again.
                raise LLMUnavailableError(
                    f"Gemini rejected the embedding request: {self._scrub(exc)}"
                ) from exc
            except genai_errors.ServerError as exc:
                last_error = exc
            except TimeoutError as exc:
                last_error = exc
            except Exception as exc:
                last_error = exc
            else:
                embeddings = getattr(response, "embeddings", None) or []
                values = getattr(embeddings[0], "values", None) if embeddings else None
                if not values:
                    raise LLMResponseError(
                        "Gemini returned no embedding values for the request"
                    )
                return Embedding(
                    vector=tuple(float(v) for v in values),
                    provider=self.name,
                    model=self._embedding_model,
                    task=task,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                )

            if attempt < self._retry.max_attempts:
                time.sleep(self._retry.backoff_for(attempt))

        message = (
            f"Gemini embedding failed after {self._retry.max_attempts} attempts: "
            f"{self._scrub(last_error)}"
        )
        if isinstance(last_error, TimeoutError):
            raise LLMTimeoutError(message) from last_error
        raise LLMUnavailableError(message) from last_error
