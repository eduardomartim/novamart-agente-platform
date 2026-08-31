"""Shared fixtures.

Tests run against the in-memory repository and the deterministic stub provider,
so the suite needs no API key, no network and no temporary files, and produces
identical results on every run.
"""

from __future__ import annotations

import os
import random
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from decimal import Decimal

import pytest

from agent_platform.config import Settings
from agent_platform.cost.budget import BudgetGuard
from agent_platform.cost.tracker import CostTracker
from agent_platform.guardrails.policy import PolicyEngine
from agent_platform.llm.authorization import LIVE_ENV_VAR, live_is_authorised
from agent_platform.llm.stub import StubProvider
from agent_platform.models import AgentName, ProposedAction
from agent_platform.observability.tracing import Tracer
from agent_platform.persistence.memory import InMemoryRepository
from agent_platform.platform import AgentPlatform
from agent_platform.security.rate_limit import RateLimiter
from agent_platform.tools.fake_tools import reset_dataset
from agent_platform.tools.gateway import ToolGateway
from agent_platform.tools.registry import default_registry


@pytest.fixture(autouse=True)
def _clean_dataset() -> Iterator[None]:
    """Restore the simulated back-office data between tests."""
    reset_dataset()
    yield
    reset_dataset()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        gemini_api_key=None,
        gemini_model="deterministic-stub-v1",
        gemini_thinking_budget=0,
        gemini_max_output_tokens=2048,
        database_path=":unused:",  # type: ignore[arg-type]
        environment="test",
        log_level="INFO",
        max_retries=2,
        recursion_limit=25,
        llm_timeout=5.0,
        tool_timeout=5.0,
        requests_per_minute=1000,
        requests_per_hour=10000,
        daily_budget_usd=Decimal("1.00"),
        max_request_cost_usd=Decimal("0.05"),
        max_input_chars=8000,
        max_context_items=20,
        max_trace_payload_chars=500,
        # Hard resource ceilings. Production defaults; individual tests narrow
        # them with dataclasses.replace() rather than loosening them here.
        max_llm_calls_per_request=12,
        max_tool_calls_per_request=8,
        request_deadline_seconds=180.0,
        max_tool_output_bytes=32_768,
        max_pending_confirmations=50,
        confirmation_ttl_seconds=900.0,
        circuit_failure_threshold=5,
        circuit_cooldown_seconds=60.0,
    )


@pytest.fixture
def repository() -> InMemoryRepository:
    return InMemoryRepository()


@pytest.fixture
def registry():
    return default_registry()


@pytest.fixture
def tracer(repository: InMemoryRepository) -> Tracer:
    return Tracer(repository, request_id="req-test", trace_id="trace-test")


@pytest.fixture
def budget_guard(repository: InMemoryRepository, settings: Settings) -> BudgetGuard:
    return BudgetGuard(
        repository,
        daily_budget_usd=settings.daily_budget_usd,
        max_request_cost_usd=settings.max_request_cost_usd,
    )


@pytest.fixture
def rate_limiter(settings: Settings) -> RateLimiter:
    return RateLimiter(settings.requests_per_minute, settings.requests_per_hour)


@pytest.fixture
def policy_engine(registry, budget_guard, rate_limiter) -> PolicyEngine:
    return PolicyEngine(registry, budget_guard=budget_guard, rate_limiter=rate_limiter)


@pytest.fixture
def gateway(registry, policy_engine) -> Iterator[ToolGateway]:
    gw = ToolGateway(registry, policy_engine)
    yield gw
    gw.shutdown()


@pytest.fixture
def cost_tracker(repository: InMemoryRepository, budget_guard: BudgetGuard) -> CostTracker:
    return CostTracker(repository, budget_guard)


@pytest.fixture
def provider() -> StubProvider:
    return StubProvider()


@pytest.fixture
def platform(settings: Settings) -> Iterator[AgentPlatform]:
    """A fully wired platform backed by in-memory storage."""
    instance = AgentPlatform(settings, repository=InMemoryRepository())
    yield instance
    instance.close()


@pytest.fixture
def strict_budget_platform(settings: Settings) -> Iterator[AgentPlatform]:
    """A platform whose budget is already exhausted."""
    tight = replace(settings, daily_budget_usd=Decimal("0"), max_request_cost_usd=Decimal("0"))
    instance = AgentPlatform(tight, repository=InMemoryRepository())
    yield instance
    instance.close()



# ------------------------------------------------------------ API credentials


@dataclass(frozen=True)
class ApiCredentials:
    """Freshly minted credentials for driving the authenticated HTTP boundary.

    Minted per test rather than hard-coded so no secret exists in the
    repository, and so the tests exercise the real ``issue_token`` path instead
    of a fixture-shaped approximation of it.

    ``runner`` and ``approver`` are deliberately separate identities holding
    disjoint authority: that separation is the property under test in
    ``tests/security/test_api_auth.py``, and having a single all-powerful token
    in the fixtures would quietly remove it from everything else.
    """

    #: repr=False, for the reason Settings.gemini_api_key carries it.
    #:
    #: A failing test renders its fixtures into the traceback. When these
    #: tokens were plain fields, one container failure printed four working
    #: credentials into pytest output -- a fixture for testing credentials
    #: leaking credentials, which is F6 wearing a different hat. The values are
    #: ephemeral and the container is destroyed, and it is still the wrong
    #: default.
    keys: str = field(repr=False)
    runner: str = field(repr=False)
    approver: str = field(repr=False)
    scraper: str = field(repr=False)
    operator: str = field(repr=False)
    ids: dict[str, str] = field(repr=False)

    def __repr__(self) -> str:
        return f"ApiCredentials(principals={sorted(self.ids)})"

    __str__ = __repr__


@pytest.fixture
def api_credentials() -> ApiCredentials:
    from agent_platform.security.api_auth import issue_token

    minted = {
        "runner": ("runs:write",),
        "approver": ("confirm:write",),
        "scraper": ("metrics:read",),
        # One identity holding both, for the tests whose subject is not the
        # separation. It has to be typed out, which is the point.
        "operator": ("runs:write", "confirm:write"),
    }
    tokens: dict[str, str] = {}
    ids: dict[str, str] = {}
    lines: list[str] = []
    for name, scopes in minted.items():
        key_id, token, digest = issue_token()
        tokens[name] = token
        ids[name] = key_id
        lines.append(f"{key_id} {name} {','.join(scopes)} {digest}")

    return ApiCredentials(keys="\n".join(lines), ids=ids, **tokens)


def bearer(token: str) -> dict[str, str]:
    """Headers presenting a credential."""
    return {"Authorization": f"Bearer {token}"}

def action(tool: str, **arguments: object) -> ProposedAction:
    """Terse helper for building a proposed action in tests."""
    return ProposedAction(tool=tool, arguments=dict(arguments))


READ_ACTION = ("get_order", {"order_id": "ORD-1001"})
WRITE_ACTION = ("update_record", {"record_id": "ORD-1001", "field": "status", "value": "x"})
EMAIL_ACTION = ("send_email", {"to": "a@b.com", "subject": "s", "body": "b"})
DELETE_ACTION = ("delete_record", {"record_id": "ORD-1001"})

ALL_AGENTS = (
    AgentName.ROUTER,
    AgentName.RESEARCHER,
    AgentName.EXECUTOR,
    AgentName.VALIDATOR,
)


def _refuse_unauthorised_live(items) -> None:
    """Fail the run when live tests were selected without authorisation.

    A second layer, and only a second layer. The protection that matters lives
    in ``llm.authorization`` and is enforced inside ``build_provider``, below
    pytest entirely -- no flag reaches it and it covers the CLI, the dashboard
    and the index builder, none of which are tests. This hook exists so the
    failure arrives at collection with an explanation, instead of arriving
    later as a stack of provider errors.

    It **fails** rather than skips. Skipping is what a missing optional
    dependency deserves; selecting live tests without authorisation is a
    mistake about what the command was going to do, and a green run with a
    quiet skip line is how that mistake goes unnoticed. The 72-call incident
    began with a command whose author believed it was offline.
    """
    if live_is_authorised():
        return
    live_items = [item for item in items if item.get_closest_marker("live")]
    if not live_items:
        return
    raise pytest.UsageError(
        f"{len(live_items)} test(s) marked `live` were selected, but this "
        f"process is not authorised to make real provider calls.\n\n"
        "Nothing was executed. An API key is not authorisation: set "
        f"{LIVE_ENV_VAR} for the single command that should spend quota "
        "(see docs/live-verification.md).\n\n"
        "If you did not mean to select live tests, check your -m expression: "
        "a -m on the command line REPLACES the one in pyproject.toml, so "
        "`-m \"not docker\"` also drops `not live`. Use "
        "`-m \"not live and not docker\"`."
    )


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(session, config, items):
    """Refuse unauthorised live tests, then shuffle when a seed is set.

    ``trylast`` is load-bearing, not tidiness. A conftest hook runs *before*
    pytest's own marker deselection, so without it ``items`` still holds every
    live test even on a run that excluded them -- and the gate below would fail
    every ordinary offline run. Running last means it sees what was actually
    selected, which is the only list worth asking about.

    Order dependence is a real defect class -- a test that only passes because
    an earlier one left state behind is a false green. Running the suite in a
    different order is how that surfaces.

    Off by default, so ordinary runs stay reproducible; the seed is explicit so
    any failure can be replayed exactly:

        PYTEST_SHUFFLE_SEED=1234 python -m pytest
    """
    _refuse_unauthorised_live(items)

    seed = os.environ.get("PYTEST_SHUFFLE_SEED")
    if not seed:
        return
    rng = random.Random(seed)  # noqa: S311 - shuffling test order, not cryptography
    rng.shuffle(items)
    if hasattr(config, "_shuffle_seed_reported"):
        return
    config._shuffle_seed_reported = True
    print(f"\n[conftest] test order shuffled with seed {seed!r}")


@pytest.fixture(autouse=True)
def _forbid_real_provider_calls(request):
    """Make it impossible for an offline test to call the real Gemini API.

    A test that reaches the network is a test that costs money, consumes a
    daily quota shared with the live suites, and fails for reasons unrelated to
    the code under test. Discipline alone does not enforce this: a fixture that
    forgets to clear ``GEMINI_API_KEY`` silently promotes an offline test to a
    live one, and nothing in the output says so.

    Both SDK entry points are replaced with something that fails loudly.
    ``embed_content`` was missing here until V2.7: the guard covered generation
    and left the retrieval embedding path open, so an offline test that reached
    embeddings with a key configured would have made a real call with nothing
    to stop it.

    Client *construction* is deliberately left alone -- it makes no request, and
    ``GeminiProvider`` now refuses to build a real client without live
    authorisation anyway.

    Tests marked ``live`` are exempt **only when live calls are authorised**.
    The exemption used to depend on the marker alone, which meant the guard
    opened for precisely the tests that could spend money, decided by the same
    fact a command-line filter controls. Now it takes authorisation and the
    marker together.
    """
    if request.node.get_closest_marker("live") and live_is_authorised():
        yield
        return

    from google.genai import models as genai_models

    originals = {
        name: getattr(genai_models.Models, name)
        for name in ("generate_content", "embed_content")
    }

    def refuse(self, *args, **kwargs):
        raise AssertionError(
            "this offline test attempted a real Gemini API call. Build the "
            "platform with a stub (gemini_api_key=None) or mark the test "
            "`live` if a real call is genuinely intended."
        )

    for name in originals:
        setattr(genai_models.Models, name, refuse)
    try:
        yield
    finally:
        for name, original in originals.items():
            setattr(genai_models.Models, name, original)
