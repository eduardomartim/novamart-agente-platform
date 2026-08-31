"""LLM provider abstraction and its implementations."""

from __future__ import annotations

from pathlib import Path

from ..config import Settings
from .authorization import (
    LiveNotAuthorised,
    live_is_authorised,
    require_live_authorisation,
)
from .errors import (
    LLMEmptyResponseError,
    LLMSafetyBlockedError,
    LLMTruncatedError,
)
from .provider import (
    Embedding,
    EmbedTask,
    LLMError,
    LLMProvider,
    LLMResponse,
    LLMResponseError,
    LLMTimeoutError,
    LLMUnavailableError,
    ProviderInfo,
    Purpose,
    RetryPolicy,
    extract_json,
)
from .stub import StubProvider

__all__ = [
    "EmbedTask",
    "Embedding",
    "LLMEmptyResponseError",
    "LLMError",
    "LLMProvider",
    "LLMResponse",
    "LLMResponseError",
    "LLMSafetyBlockedError",
    "LLMTimeoutError",
    "LLMTruncatedError",
    "LLMUnavailableError",
    "LiveNotAuthorised",
    "ProviderInfo",
    "Purpose",
    "RetryPolicy",
    "StubProvider",
    "build_provider",
    "describe_provider",
    "extract_json",
    "live_is_authorised",
    "require_live_authorisation",
]


def build_provider(settings: Settings) -> LLMProvider:
    """Select a provider from configuration.

    Falls back to the deterministic stub when no API key is present. The
    fallback is explicit and visible: the returned provider reports
    ``name == "stub"``, and every trace and cost row it produces carries that
    name, so demo output is never mistaken for live model output.

    **This is the gate.** Every path that reaches a real provider passes
    through this function -- ``AgentPlatform`` builds its provider here, the
    CLI and the dashboard build theirs through ``AgentPlatform``, the vector
    index builder calls it directly, and so does the live test suite. That is
    what makes one check here worth more than any number of checks inside
    pytest: it sits *below* the test runner, so no command-line flag reaches
    it, and it covers the callers that are not tests at all.

    The check runs before the lazy imports below, so an unauthorised process
    does not even load the SDK, let alone construct a client.
    """
    if settings.demo_mode:
        return StubProvider()

    # A key selects the provider; it does not authorise using it. Those were
    # the same fact until they cost 72 unintended calls -- see
    # ``llm.authorization`` for the chain.
    require_live_authorisation("build a live provider")

    # Imported lazily so that demo mode does not pay the SDK import cost and so
    # that a broken optional dependency cannot break demo mode.
    from .budget import SqliteProviderBudget
    from .gemini import GeminiProvider

    assert settings.gemini_api_key is not None  # guaranteed by demo_mode check
    # Every consumer -- dashboard, CLI, library caller -- builds its provider
    # here, so wiring the budget at this single point is what makes the ceiling
    # apply to all of them. It is stored beside the database so the count is
    # shared across processes.
    # `database_path` is typed Path but callers may pass a string sentinel,
    # so coerce rather than assume.
    # SQLite is already atomic and already cross-process -- it charges inside a
    # BEGIN IMMEDIATE transaction. What it cannot do is span replicas that do
    # not share a filesystem, which is why a shared backend takes over when one
    # is configured. Same protocol either way, so nothing downstream changes.
    from .budget import ProviderBudget

    budget: ProviderBudget
    if settings.redis_url:
        from ..state import SharedProviderBudget, build_shared_state
        from .budget import DEFAULT_DAILY_LIMIT

        budget = SharedProviderBudget(
            build_shared_state(settings.redis_url).backend,
            daily_limit=DEFAULT_DAILY_LIMIT,
        )
    else:
        budget = SqliteProviderBudget(
            Path(settings.database_path).parent / "provider_budget.db"
        )
    return GeminiProvider(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        timeout_seconds=settings.llm_timeout,
        retry_policy=RetryPolicy(max_attempts=settings.max_retries + 1),
        max_output_tokens=settings.gemini_max_output_tokens,
        thinking_budget=settings.gemini_thinking_budget,
        budget=budget,
    )


def describe_provider(provider: LLMProvider, settings: Settings) -> ProviderInfo:
    """Build a status summary for the UI. Never includes the API key."""
    live = provider.name != "stub"
    return ProviderInfo(
        name=provider.name,
        model=provider.model,
        live=live,
        detail=(
            f"Live calls to {provider.model} via the Gemini API."
            if live
            else "No GEMINI_API_KEY configured. Running the deterministic stub provider; "
            "no language model is being called."
        ),
        extras={"environment": settings.environment},
    )
