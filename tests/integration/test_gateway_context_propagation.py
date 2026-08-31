"""Request context must survive the gateway's thread pool.

The gateway runs every tool handler on a ``ThreadPoolExecutor``, and a worker
thread starts with a fresh, empty context: ContextVars propagate into asyncio
tasks but not across threads. Anything a request scopes with a ContextVar is
therefore invisible to the handler unless the context is copied onto the
worker explicitly.

That is not hypothetical. The 7G live gate spent four real calls discovering
it: the retrieval provider was bound in the calling thread, the search handler
ran in a pool worker that could not see it, and hybrid retrieval silently
degraded to lexical while reporting success. Twenty-six offline tests missed
it because every one of them called the retriever directly instead of through
the executor.

So these tests deliberately go the long way round -- gateway, thread pool,
handler, strategy -- because that is the only path where the bug lives.
"""

from __future__ import annotations

from contextvars import ContextVar

import pytest

from agent_platform.guardrails.policy import PolicyContext, PolicyEngine
from agent_platform.llm.provider import Embedding, EmbedTask
from agent_platform.llm.stub import StubProvider
from agent_platform.models import AgentName, ProposedAction
from agent_platform.observability.tracing import Tracer
from agent_platform.persistence.memory import InMemoryRepository
from agent_platform.retrieval import strategy
from agent_platform.tools.execution import is_inside_gateway
from agent_platform.tools.gateway import ToolGateway
from agent_platform.tools.registry import default_registry

_PROBE: ContextVar[str | None] = ContextVar("test_probe", default=None)


@pytest.fixture
def gateway():
    registry = default_registry()
    instance = ToolGateway(registry, PolicyEngine(registry))
    try:
        yield instance
    finally:
        instance.shutdown()


@pytest.fixture
def tracer():
    return Tracer(InMemoryRepository(), request_id="ctx", trace_id="ctx")


@pytest.fixture(autouse=True)
def clean_caches():
    strategy.reset_caches()
    yield
    strategy.reset_caches()


class CountingEmbedder(StubProvider):
    """Records whether the retrieval layer actually reached a provider."""

    def __init__(self, dimension: int = 3072) -> None:
        super().__init__()
        self.embed_calls = 0
        self._dimension = dimension

    def embed(self, text, *, task=EmbedTask.QUERY, timeout=None):
        self.embed_calls += 1
        # A deterministic non-zero vector. Its direction is irrelevant here --
        # the assertion is that this method was reached at all.
        return Embedding(
            vector=tuple(1.0 if i == 0 else 0.0 for i in range(self._dimension)),
            provider="counting",
            model="gemini-embedding-001",
            task=task,
        )


def run_search(gateway, tracer, query: str = "refund policy"):
    """Submit a search exactly as the researcher does."""
    action = ProposedAction(tool="search", arguments={"query": query})
    return gateway.submit(
        action,
        agent=AgentName.RESEARCHER,
        context=PolicyContext(
            request_id=tracer.request_id,
            agent=AgentName.RESEARCHER,
            action=action,
        ),
        tracer=tracer,
    )


# ======================================================= the generic property


def test_a_contextvar_set_by_the_caller_is_visible_to_the_handler(gateway, tracer):
    """The property in its simplest form, independent of retrieval.

    Asserted through the real gateway rather than a bare executor, so it fails
    if the propagation is ever removed from ``_invoke``.
    """
    seen: list[str | None] = []

    original = gateway._run_guarded

    def probing(tool, arguments):
        seen.append(_PROBE.get())
        return original(tool, arguments)

    gateway._run_guarded = probing  # type: ignore[method-assign]

    token = _PROBE.set("BOUND")
    try:
        run_search(gateway, tracer)
    finally:
        _PROBE.reset(token)

    assert seen == ["BOUND"], (
        "the caller's context did not reach the tool handler; a ContextVar "
        "bound per request is invisible inside the gateway's thread pool"
    )


def test_an_unbound_contextvar_is_still_unbound_in_the_handler(gateway, tracer):
    """The complement: propagation must copy the context, not invent a value."""
    seen: list[str | None] = []
    original = gateway._run_guarded

    def probing(tool, arguments):
        seen.append(_PROBE.get())
        return original(tool, arguments)

    gateway._run_guarded = probing  # type: ignore[method-assign]
    run_search(gateway, tracer)

    assert seen == [None]


# =============================== the specific failure the live gate exposed


def test_the_retrieval_provider_reaches_the_search_handler(gateway, tracer):
    """gateway -> ThreadPoolExecutor -> search -> strategy -> provider.embed.

    This is the exact path the 7G live gate ran. Before the fix, ``embed_calls``
    was 0 and the search silently returned BM25 results while reporting
    success.
    """
    provider = CountingEmbedder()

    with strategy.retrieval_provider(provider):
        outcome = run_search(gateway, tracer)

    assert outcome.result.ok, f"the search failed: {outcome.result.error}"
    assert provider.embed_calls >= 1, (
        "the search handler never reached the provider: hybrid retrieval "
        "silently degraded to lexical inside the gateway's thread pool"
    )


def test_the_strategy_reports_hybrid_inside_the_handler(gateway, tracer):
    """Complementary to the call count: the handler's own view of the world.

    Observed from inside ``_run_guarded``, which is the worker thread itself.
    """
    seen: list[str] = []
    original = gateway._run_guarded

    def observing(tool, arguments):
        seen.append(strategy.active_strategy())
        return original(tool, arguments)

    gateway._run_guarded = observing  # type: ignore[method-assign]

    with strategy.retrieval_provider(CountingEmbedder()):
        run_search(gateway, tracer)

    assert seen == ["hybrid"], f"the worker thread saw {seen}, not hybrid"


def test_the_strategy_reports_lexical_when_nothing_is_bound(gateway, tracer):
    seen: list[str] = []
    original = gateway._run_guarded

    def observing(tool, arguments):
        seen.append(strategy.active_strategy())
        return original(tool, arguments)

    gateway._run_guarded = observing  # type: ignore[method-assign]
    run_search(gateway, tracer)

    assert seen == ["lexical"]


def test_no_provider_bound_still_means_lexical_in_the_handler(gateway, tracer):
    """Propagation must not turn "unbound" into "bound"."""
    provider = CountingEmbedder()
    outcome = run_search(gateway, tracer)

    assert outcome.result.ok
    assert provider.embed_calls == 0


# ============================================ existing guarantees preserved


def test_the_gateway_token_does_not_leak_back_to_the_caller(gateway, tracer):
    """Copying the context must not weaken the execution guard.

    ``gateway_execution()`` opens inside ``_run_guarded``, which now runs in a
    *copied* context -- so its token is set there and cannot escape into the
    caller's context. That is stricter than before, not looser.
    """
    assert is_inside_gateway() is False
    run_search(gateway, tracer)
    assert is_inside_gateway() is False


def test_concurrent_requests_do_not_share_a_bound_provider(gateway, tracer):
    """Two threads, two contexts, two different answers."""
    import threading

    seen: dict[str, int] = {}

    def worker(name: str, provider) -> None:
        local_tracer = Tracer(InMemoryRepository(), request_id=name, trace_id=name)
        if provider is None:
            run_search(gateway, local_tracer)
            seen[name] = 0
        else:
            with strategy.retrieval_provider(provider):
                run_search(gateway, local_tracer)
            seen[name] = provider.embed_calls

    bound = CountingEmbedder()
    threads = [
        threading.Thread(target=worker, args=("bound", bound)),
        threading.Thread(target=worker, args=("unbound", None)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert seen["bound"] >= 1, "the bound request did not reach its provider"
    assert seen["unbound"] == 0, "an unbound request used another request's provider"


def test_the_search_contract_is_unchanged_through_the_gateway(gateway, tracer):
    outcome = run_search(gateway, tracer)
    assert set(outcome.result.output) == {
        "query",
        "result_count",
        "total_matches",
        "results",
    }


def test_the_gateway_still_reports_a_handler_failure_as_an_error(gateway, tracer):
    """The copied context must not swallow an exception raised on the worker."""
    original = gateway._run_guarded

    def failing(tool, arguments):
        raise RuntimeError("handler exploded")

    gateway._run_guarded = failing  # type: ignore[method-assign]
    try:
        outcome = run_search(gateway, tracer)
    finally:
        gateway._run_guarded = original  # type: ignore[method-assign]

    assert not outcome.result.ok
    assert outcome.result.status == "error"
    assert "RuntimeError" in (outcome.result.error or "")
