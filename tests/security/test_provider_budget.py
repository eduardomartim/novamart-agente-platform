"""A daily ceiling on *physical* calls to the provider, enforced in the core.

Phase 1 put a budget in the dashboard. It closed the obvious hole -- a visitor
clicking Run -- but left two real gaps, both found by reading the code rather
than by theorising:

* **The CLI and any library caller bypassed it entirely.** The gate lived in a
  Streamlit page; nothing else passed through it.
* **It counted the wrong thing.** `llm_call` events are emitted only after a
  *successful* logical call, so a request that retried twice before succeeding
  recorded one event while spending three provider calls, and a request that
  failed outright recorded none while spending up to three. The dashboard
  budget therefore undercounts exactly when the platform is burning the most
  quota.

This budget sits at the one line every physical attempt passes through: the
call inside `GeminiProvider`'s retry loop. Retries, and the F14 thinking probe,
are attempts like any other and are charged like any other.

It can only *prevent* a call. It authorises nothing, and it takes no argument
from a caller claiming to be exempt -- an exemption is a bypass with better
manners.
"""

from __future__ import annotations

import ast
import inspect
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.genai import errors as genai_errors

from agent_platform.llm.budget import ProviderBudgetExhausted, SqliteProviderBudget
from agent_platform.llm.gemini import GeminiProvider
from agent_platform.llm.provider import (
    EmbedTask,
    LLMError,
    LLMProvider,
    LLMResponseError,
    Purpose,
    RetryPolicy,
)

FAKE_KEY = "AIzaSyD1234567890123456789012345678901c"


# ------------------------------------------------------------------ helpers


def _response(text: str = "{}"):
    return SimpleNamespace(
        text=text,
        candidates=[SimpleNamespace(finish_reason="STOP")],
        usage_metadata=SimpleNamespace(
            prompt_token_count=10, candidates_token_count=5, thoughts_token_count=0
        ),
    )


def _client_error(message: str, code: int = 400, status: str = "INVALID_ARGUMENT"):
    exc = genai_errors.ClientError.__new__(genai_errors.ClientError)
    Exception.__init__(exc, message)
    exc.code = code
    exc.status = status
    exc.message = message
    return exc


def _server_error(message: str = "backend unavailable"):
    exc = genai_errors.ServerError.__new__(genai_errors.ServerError)
    Exception.__init__(exc, message)
    return exc


class CountingClient:
    """Counts every physical generate_content invocation."""

    def __init__(self, outcomes):
        self.calls = 0
        self._outcomes = list(outcomes)
        self.models = self

    def generate_content(self, **kwargs):
        self.calls += 1
        outcome = self._outcomes.pop(0) if self._outcomes else _response()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def build(tmp_path, limit: int, outcomes=(), **kwargs):
    budget = SqliteProviderBudget(tmp_path / "budget.db", daily_limit=limit)
    provider = GeminiProvider(
        FAKE_KEY,
        "gemini-3.5-flash-lite",
        retry_policy=RetryPolicy(
            max_attempts=3,
            initial_backoff_seconds=0.0,
            backoff_multiplier=1.0,
            max_backoff_seconds=0.0,
        ),
        budget=budget,
        client=CountingClient(outcomes),
        **kwargs,
    )
    client = provider._client
    return provider, client, budget


def generate(provider):
    return provider.generate("hello", purpose=Purpose.ROUTE)


# --------------------------------------------------------- the basic ceiling


def test_a_call_below_the_limit_is_allowed(tmp_path):
    provider, client, budget = build(tmp_path, 5, [_response()])
    generate(provider)
    assert client.calls == 1
    assert budget.used() == 1


def test_at_the_limit_the_call_is_blocked(tmp_path):
    provider, client, _ = build(tmp_path, 1, [_response(), _response()])
    generate(provider)
    with pytest.raises(ProviderBudgetExhausted):
        generate(provider)
    assert client.calls == 1, "a blocked call still reached the provider"


def test_a_blocked_call_never_touches_the_provider(tmp_path):
    """The whole point: stop *before* spending quota, not after."""
    provider, client, _ = build(tmp_path, 0, [_response()])
    with pytest.raises(ProviderBudgetExhausted):
        generate(provider)
    assert client.calls == 0


# ------------------------------------------------------- retries are charged


def test_each_retry_consumes_budget(tmp_path):
    """A retry is a physical call. Three attempts cost three."""
    provider, client, budget = build(
        tmp_path, 10, [_server_error(), _server_error(), _response()]
    )
    generate(provider)
    assert client.calls == 3
    assert budget.used() == 3


def test_a_retry_cannot_push_past_the_limit(tmp_path):
    """Remaining 1: the first attempt runs, the retry is refused."""
    provider, client, budget = build(tmp_path, 1, [_server_error(), _response()])
    with pytest.raises(LLMError):
        generate(provider)
    assert client.calls == 1, "the retry was allowed to exceed the budget"
    assert budget.used() == 1


def test_one_request_cannot_consume_more_than_the_remaining_budget(tmp_path):
    provider, client, _ = build(
        tmp_path, 2, [_server_error(), _server_error(), _response()]
    )
    with pytest.raises(LLMError):
        generate(provider)
    assert client.calls == 2


# ------------------------------------------------------------ F14 preserved


def test_a_quota_error_does_not_trigger_the_thinking_probe(tmp_path):
    """F14 must survive: 429 is unambiguous, so it costs exactly one call."""
    provider, client, budget = build(
        tmp_path,
        10,
        [_client_error("429 quota", code=429, status="RESOURCE_EXHAUSTED")],
        thinking_budget=0,
    )
    with pytest.raises(LLMError):
        generate(provider)
    assert client.calls == 1
    assert budget.used() == 1


def test_a_named_400_does_not_trigger_the_thinking_probe(tmp_path):
    provider, client, _ = build(
        tmp_path,
        10,
        [_client_error("400 API key not valid", code=400)],
        thinking_budget=0,
    )
    with pytest.raises(LLMError):
        generate(provider)
    assert client.calls == 1


def test_the_ambiguous_400_probe_is_charged_like_any_other_call(tmp_path):
    """The legitimate probe still runs -- and still costs budget."""
    provider, client, budget = build(
        tmp_path,
        10,
        [
            _client_error("400 INVALID_ARGUMENT. Request contains an invalid argument."),
            _response(),
        ],
        thinking_budget=0,
    )
    generate(provider)
    assert client.calls == 2
    assert budget.used() == 2


def test_the_probe_is_refused_when_the_budget_cannot_afford_it(tmp_path):
    provider, client, _ = build(
        tmp_path,
        1,
        [
            _client_error("400 INVALID_ARGUMENT. Request contains an invalid argument."),
            _response(),
        ],
        thinking_budget=0,
    )
    with pytest.raises(LLMError):
        generate(provider)
    assert client.calls == 1


# --------------------------------------------------- shared across consumers


def test_the_budget_is_shared_between_provider_instances(tmp_path):
    """A CLI run and a dashboard run draw on the same daily allowance."""
    first, client_a, _ = build(tmp_path, 2, [_response()])
    generate(first)

    client_b = CountingClient([_response(), _response()])
    second = GeminiProvider(
        FAKE_KEY,
        "gemini-3.5-flash-lite",
        budget=SqliteProviderBudget(tmp_path / "budget.db", daily_limit=2),
        client=client_b,
    )

    generate(second)
    with pytest.raises(ProviderBudgetExhausted):
        generate(second)

    assert client_a.calls == 1
    assert client_b.calls == 1


def test_the_budget_survives_a_new_process(tmp_path):
    """Durable, not in-memory: a fresh object sees what was already spent."""
    _, _, budget = build(tmp_path, 5, [_response()])
    budget.try_consume()
    budget.try_consume()

    reopened = SqliteProviderBudget(tmp_path / "budget.db", daily_limit=5)
    assert reopened.used() == 2
    assert reopened.remaining() == 3


def test_a_new_day_restores_the_allowance(tmp_path):
    """The provider quota resets daily; so must the budget."""
    clock = {"day": "2026-08-29"}
    budget = SqliteProviderBudget(
        tmp_path / "budget.db", daily_limit=2, today=lambda: clock["day"]
    )
    assert budget.try_consume() is True
    assert budget.try_consume() is True
    assert budget.try_consume() is False

    clock["day"] = "2026-08-30"
    assert budget.try_consume() is True
    assert budget.used() == 1


# ------------------------------------------------------------- concurrency


def test_concurrent_consumers_never_over_admit(tmp_path):
    """The race that matters: two callers see 'one left' and both proceed."""
    limit = 20
    workers = 32
    budget = SqliteProviderBudget(tmp_path / "budget.db", daily_limit=limit)
    barrier = threading.Barrier(workers)
    granted: list[bool] = [False] * workers

    def worker(index: int) -> None:
        barrier.wait(timeout=30)
        granted[index] = budget.try_consume()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert sum(1 for g in granted if g) == limit
    assert budget.used() == limit


# -------------------------------------------------- distinguishable, and safe


def test_budget_exhaustion_is_not_mistaken_for_a_provider_failure(tmp_path):
    provider, _, _ = build(tmp_path, 0)
    with pytest.raises(ProviderBudgetExhausted) as caught:
        generate(provider)
    assert isinstance(caught.value, LLMError), "callers already handling LLMError"
    message = str(caught.value).lower()
    assert "budget" in message or "limit" in message
    assert "unavailable" not in message, "must not read as a provider outage"


def test_budget_exhaustion_never_names_the_credential(tmp_path):
    provider, _, _ = build(tmp_path, 0)
    with pytest.raises(ProviderBudgetExhausted) as caught:
        generate(provider)
    text = str(caught.value)
    assert FAKE_KEY not in text
    assert "AIza" not in text


def test_exhaustion_is_deterministic(tmp_path):
    """Same state, same answer -- no flapping."""
    budget = SqliteProviderBudget(tmp_path / "budget.db", daily_limit=3)
    results = [budget.try_consume() for _ in range(6)]
    assert results == [True, True, True, False, False, False]


def test_the_stub_provider_is_unaffected(settings):
    """The stub contacts nothing, so there is no quota to protect."""
    from agent_platform.llm.stub import StubProvider

    provider = StubProvider()
    for _ in range(50):
        provider.generate("What is the status of order ORD-1001?", purpose=Purpose.ROUTE)


# ===========================================================================
# Embeddings (7B)
#
# google-genai exposes Models.embed_content. Until this section existed, the
# budget guarded exactly one SDK entry point -- generate_content -- and the
# LLMProvider protocol had no embed verb at all. Reaching
# client.models.embed_content directly was therefore not merely possible, it
# was the *only* way to embed anything, and it spent real quota that the
# 400/day ceiling never saw.
#
# Reproduced before the fix against a fake SDK: 1 physical embed_content call,
# 0 budget charges.
# ===========================================================================


class _Embedding:
    def __init__(self, values):
        self.values = values


class _EmbedResponse:
    def __init__(self, values=(0.1, 0.2, 0.3)):
        self.embeddings = [_Embedding(list(values))]


class EmbeddingClient(CountingClient):
    """Counts physical calls to both SDK entry points, separately."""

    def __init__(self, outcomes=(), embed_outcomes=()):
        super().__init__(outcomes)
        self.embed_calls = 0
        self._embed_outcomes = list(embed_outcomes)

    def embed_content(self, **kwargs):
        self.embed_calls += 1
        outcome = self._embed_outcomes.pop(0) if self._embed_outcomes else _EmbedResponse()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def build_embedding(tmp_path, limit: int, embed_outcomes=(), **kwargs):
    provider, _, budget = build(tmp_path, limit, **kwargs)
    client = EmbeddingClient(embed_outcomes=embed_outcomes)
    provider._client = client
    return provider, client, budget


# -------------------------------------------------- the structural tripwire


def _sdk_call_sites(tree):
    """Every call reaching ``self._client...``, with its enclosing function."""
    enclosing = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for child in ast.walk(node):
                enclosing[id(child)] = node.name

    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        parts = []
        cur = node.func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if not (isinstance(cur, ast.Name) and cur.id == "self"):
            continue
        parts.append("self")
        chain = ".".join(reversed(parts))
        if chain.startswith("self._client."):
            sites.append((chain, node.lineno, enclosing.get(id(node), "<module>")))
    return sites


def _guard_lines(tree, function_name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.name != function_name:
                continue
            return [
                child.lineno
                for child in ast.walk(node)
                if isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "try_consume"
            ]
    return []


def _unguarded_sdk_calls(source: str) -> list[str]:
    tree = ast.parse(source)
    sites = _sdk_call_sites(tree)
    assert sites, "no SDK call sites found -- the detector itself is broken"
    return [
        f"{chain} at line {lineno} in {func}()"
        for chain, lineno, func in sites
        if not [g for g in _guard_lines(tree, func) if g < lineno]
    ]


GEMINI_SOURCE = Path(str(inspect.getsourcefile(GeminiProvider)))

UNGUARDED_EMBED = (
    "\n"
    "    def embed_unsafely(self, text: str):\n"
    "        return self._client.models.embed_content("
    "model=self._model, contents=text)\n"
)


def test_every_sdk_call_site_charges_the_budget_first():
    """A behavioural test only covers paths someone thought to exercise.

    This one reads the module. A new SDK entry point added without a charge
    fails here even if no test ever calls it -- which is the actual failure
    mode, since nobody writes a test for the bypass they did not notice.
    """
    unguarded = _unguarded_sdk_calls(GEMINI_SOURCE.read_text(encoding="utf-8"))
    assert not unguarded, (
        "these SDK call sites spend provider quota without charging the "
        "budget:\n  " + "\n  ".join(unguarded)
    )


def test_the_tripwire_actually_catches_an_unguarded_call():
    """Proof the detector is not passing merely because it finds nothing.

    Appends the naive embed() a developer would write with no budget in mind
    to a copy held in memory. The real module is never written to.
    """
    naive = GEMINI_SOURCE.read_text(encoding="utf-8").rstrip() + "\n" + UNGUARDED_EMBED
    unguarded = _unguarded_sdk_calls(naive)
    assert any("embed_unsafely" in entry for entry in unguarded), (
        "the tripwire failed to notice an unguarded SDK call"
    )


# ------------------------------------------------------- the contract exists


def test_the_provider_protocol_offers_an_embed_verb():
    """Without it, embedding code has no route to the SDK but around the budget."""
    assert hasattr(LLMProvider, "embed")
    assert hasattr(GeminiProvider, "embed")


# ---------------------------------------------------------- charging behaviour


def test_an_embedding_is_charged_before_it_reaches_the_sdk(tmp_path):
    provider, client, budget = build_embedding(tmp_path, limit=10)
    result = provider.embed("what is the refund policy")

    assert client.embed_calls == 1
    assert budget.used() == 1
    assert result.dimensions == 3
    assert result.task is EmbedTask.QUERY


def test_a_failed_embedding_still_spends_its_charge(tmp_path):
    """Quota is spent by the attempt. The server does not refund a 500."""
    provider, client, budget = build_embedding(
        tmp_path, limit=10, embed_outcomes=[_server_error()] * 3
    )
    with pytest.raises(LLMError):
        provider.embed("anything")

    assert client.embed_calls == 3
    assert budget.used() == 3


def test_every_retried_attempt_is_charged(tmp_path):
    provider, client, budget = build_embedding(
        tmp_path,
        limit=10,
        embed_outcomes=[_server_error(), _server_error(), _EmbedResponse()],
    )
    provider.embed("anything")

    assert client.embed_calls == 3
    assert budget.used() == 3, "a retry spends quota and must be charged like one"


def test_an_exhausted_budget_stops_the_call_reaching_the_sdk(tmp_path):
    provider, client, budget = build_embedding(tmp_path, limit=0)
    with pytest.raises(ProviderBudgetExhausted):
        provider.embed("anything")

    assert client.embed_calls == 0, "the SDK was reached despite an exhausted budget"
    assert budget.used() == 0


def test_the_budget_binds_across_retries_not_just_the_first_attempt(tmp_path):
    """One charge left, three attempts wanted: the second must be refused."""
    provider, client, budget = build_embedding(
        tmp_path, limit=1, embed_outcomes=[_server_error()] * 3
    )
    with pytest.raises(ProviderBudgetExhausted):
        provider.embed("anything")

    assert client.embed_calls == 1
    assert budget.used() == 1


def test_a_rejected_embedding_is_not_retried(tmp_path):
    """A 4xx means the request was wrong; asking again buys the same answer."""
    provider, client, budget = build_embedding(
        tmp_path, limit=10, embed_outcomes=[_client_error("bad model")]
    )
    with pytest.raises(LLMError):
        provider.embed("anything")

    assert client.embed_calls == 1
    assert budget.used() == 1


def test_generation_and_embedding_draw_on_the_same_counter(tmp_path):
    """One quota, one ceiling. Two verbs must not each get their own 400."""
    provider, _, budget = build(tmp_path, limit=10)

    provider._client = CountingClient([])
    provider.generate("hi", purpose=Purpose.ROUTE)
    assert budget.used() == 1

    provider._client = EmbeddingClient()
    provider.embed("hi")
    assert budget.used() == 2, "embedding did not share the generation budget"


def test_embedding_cannot_exceed_the_days_allowance(tmp_path):
    provider, client, budget = build_embedding(tmp_path, limit=3)
    for _ in range(3):
        provider.embed("q")
    with pytest.raises(ProviderBudgetExhausted):
        provider.embed("q")

    assert client.embed_calls == 3
    assert budget.used() == 3
    assert budget.remaining() == 0


def test_empty_text_is_refused_without_spending_a_charge(tmp_path):
    """Nothing to embed is a caller bug, not a reason to spend quota."""
    provider, client, budget = build_embedding(tmp_path, limit=10)
    with pytest.raises(LLMResponseError):
        provider.embed("   ")

    assert client.embed_calls == 0
    assert budget.used() == 0


# -------------------------------------------------------------- the stub tier


def test_the_stub_refuses_to_embed_rather_than_faking_a_vector():
    """A fake vector would make offline retrieval look semantic while ranking
    at random, and every number derived from it would be a fabrication."""
    from agent_platform.llm.stub import StubProvider

    provider = StubProvider()
    assert hasattr(StubProvider, "embed")
    with pytest.raises(NotImplementedError, match="stub"):
        provider.embed("anything")


def test_the_stub_has_no_embedding_sdk_to_reach():
    from agent_platform.llm import stub as stub_module

    source = Path(str(inspect.getsourcefile(stub_module))).read_text(encoding="utf-8")
    assert "embed_content" not in source
    assert "genai" not in source
