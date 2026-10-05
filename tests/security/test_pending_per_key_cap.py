"""F-02: one visitor may not consume the whole pending-confirmation store.

The store has always been bounded -- ``MAX_PENDING_CONFIRMATIONS`` -- and that
bound has always been *global*. A red-team pass showed what a purely global
bound buys an attacker: fifty-four requests from one quota bucket, well inside
that bucket's own rate limit, filled every slot, and every other visitor's
high-risk action was refused before a human ever saw it. The confirmation
mechanism is the platform's most important safety control, and it could be
switched off by one visitor for the price of six minutes.

The fix is a second bound, per quota key, enforced by the same atomic primitive
as the first. These tests are written against behaviour -- put, observe,
confirm, expire, put again -- rather than against counters, because a counter
that agrees with itself proves nothing about what a visitor can do.

The concurrency test is the one that matters. A per-key cap written as "count,
then decide, then store" is correct in a single thread and wrong the moment two
requests from the same visitor arrive together, which is precisely when an
attacker sends them.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import replace

import pytest

from agent_platform.config import Settings
from agent_platform.state import LocalBackend, SharedPendingRegistry, SuspendedRun

# Both backends run the same assertions. Redis is exercised when one is
# reachable and skipped cleanly when it is not, exactly as test_shared_state
# does it -- the offline suite stays offline.
try:  # pragma: no cover - depends on a local Redis
    from tests.integration.test_shared_state import REDIS_UP, make_backend
except Exception:  # pragma: no cover
    REDIS_UP = False

    def make_backend(kind: str, prefix: str):
        return LocalBackend()


BACKENDS = ["local"] + (["redis"] if REDIS_UP else [])


@pytest.fixture
def unique() -> str:
    return f"f02{uuid.uuid4().hex[:12]}"


@pytest.fixture(params=BACKENDS)
def backend(request, unique):
    return make_backend(request.param, unique)


def run_of(request_id: str, owner: str) -> SuspendedRun:
    """A suspended run belonging to one quota key."""
    return SuspendedRun(
        request_id=request_id,
        trace_id=f"tr-{request_id}",
        user_input="update order ORD-1001",
        created_at=0.0,
        quota_key=owner,
    )


def registry(backend, unique, *, max_entries=50, max_per_key=0, ttl=900.0, clock=None):
    reg = SharedPendingRegistry(
        backend,
        max_entries=max_entries,
        ttl_seconds=ttl,
        namespace=unique,
        max_per_key=max_per_key,
    )
    if clock is not None:
        reg.clock = clock
    return reg


# ============================================================ A. the per-key cap


def test_one_quota_key_cannot_exceed_its_own_share(backend, unique):
    """The finding, stated as a test: two in, the third refused."""
    reg = registry(backend, unique, max_entries=50, max_per_key=2)

    assert reg.put("r1", run_of("r1", "visitor:attacker")) is True
    assert reg.put("r2", run_of("r2", "visitor:attacker")) is True
    assert reg.put("r3", run_of("r3", "visitor:attacker")) is False

    # Refusing is not evicting. What was already pending survives untouched.
    assert reg.get("r1") is not None
    assert reg.get("r2") is not None
    assert reg.get("r3") is None


# ======================================================= B. other keys unaffected


def test_a_second_visitor_is_unaffected_by_the_first_reaching_its_share(backend, unique):
    """The whole point of the fix: the attacker's cap is the attacker's."""
    reg = registry(backend, unique, max_entries=6, max_per_key=2)

    assert reg.put("a1", run_of("a1", "visitor:attacker")) is True
    assert reg.put("a2", run_of("a2", "visitor:attacker")) is True
    assert reg.put("a3", run_of("a3", "visitor:attacker")) is False

    assert reg.put("v1", run_of("v1", "visitor:victim")) is True
    assert reg.put("v2", run_of("v2", "visitor:victim")) is True


# ============================================================ C. the global cap


def test_the_global_cap_still_binds_across_many_keys(backend, unique):
    """The new bound is a second ceiling, never a replacement for the first."""
    reg = registry(backend, unique, max_entries=4, max_per_key=2)

    assert reg.put("k1", run_of("k1", "visitor:1")) is True
    assert reg.put("k2", run_of("k2", "visitor:2")) is True
    assert reg.put("k3", run_of("k3", "visitor:3")) is True
    assert reg.put("k4", run_of("k4", "visitor:4")) is True
    # Global cap reached: a fresh key with room in its own share is still refused.
    assert reg.put("k5", run_of("k5", "visitor:5")) is False
    assert len(reg) == 4


# =============================================================== D. concurrency


@pytest.mark.parametrize("attempts,cap", [(24, 2), (32, 5)])
def test_concurrent_requests_from_one_key_cannot_race_past_the_cap(
    backend, unique, attempts, cap
):
    """The test the fix exists to pass.

    A check-then-store written as two operations admits every thread that read
    the count before any of them wrote. Threads start together on a barrier so
    they genuinely overlap rather than serialising by accident.
    """
    reg = registry(backend, unique, max_entries=attempts * 2, max_per_key=cap)
    barrier = threading.Barrier(attempts)
    accepted: list[str] = []
    lock = threading.Lock()

    def attempt(i: int) -> None:
        barrier.wait()
        if reg.put(f"c{i}", run_of(f"c{i}", "visitor:attacker")):
            with lock:
                accepted.append(f"c{i}")

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(attempts)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(accepted) == cap, f"{len(accepted)} accepted, cap is {cap}"
    # And the store agrees with what the callers were told.
    assert sum(1 for key in accepted if reg.get(key) is not None) == cap


# ====================================================== E/F. capacity is released


def test_confirming_returns_the_slot_to_its_owner(backend, unique):
    reg = registry(backend, unique, max_entries=50, max_per_key=2)
    reg.put("r1", run_of("r1", "visitor:v"))
    reg.put("r2", run_of("r2", "visitor:v"))
    assert reg.put("r3", run_of("r3", "visitor:v")) is False

    assert reg.take("r1") is not None  # the confirm path consumes it
    assert reg.put("r3", run_of("r3", "visitor:v")) is True


def test_declining_or_cancelling_returns_the_slot_too(backend, unique):
    """Decline and cancel both reach the store through the same two verbs."""
    reg = registry(backend, unique, max_entries=50, max_per_key=1)

    reg.put("d1", run_of("d1", "visitor:v"))
    assert reg.put("d2", run_of("d2", "visitor:v")) is False
    reg.take("d1")  # a decline consumes the record exactly as an approval does
    assert reg.put("d2", run_of("d2", "visitor:v")) is True

    reg.discard("d2")  # cancel / cleanup path
    assert reg.put("d3", run_of("d3", "visitor:v")) is True


# ================================================================ G. TTL release


def test_expiry_releases_the_per_key_share(backend, unique):
    """No leak: a slot nobody ever confirmed comes back when it ages out."""
    now = [1000.0]
    reg = registry(
        backend, unique, max_entries=50, max_per_key=2, ttl=60.0, clock=lambda: now[0]
    )

    assert reg.put("e1", run_of("e1", "visitor:v")) is True
    assert reg.put("e2", run_of("e2", "visitor:v")) is True
    assert reg.put("e3", run_of("e3", "visitor:v")) is False

    now[0] += 61.0  # both age out, unconfirmed
    assert reg.put("e3", run_of("e3", "visitor:v")) is True
    assert reg.put("e4", run_of("e4", "visitor:v")) is True
    assert reg.put("e5", run_of("e5", "visitor:v")) is False


# ================================================= H. the two limits are separate


def test_each_limit_binds_independently(backend, unique):
    reg = registry(backend, unique, max_entries=3, max_per_key=2)

    assert reg.put("x1", run_of("x1", "visitor:a")) is True
    assert reg.put("x2", run_of("x2", "visitor:a")) is True
    assert reg.put("x3", run_of("x3", "visitor:a")) is False  # per-key bound
    assert reg.put("y1", run_of("y1", "visitor:b")) is True
    assert reg.put("y2", run_of("y2", "visitor:b")) is False  # global bound
    assert len(reg) == 3


def test_a_zero_per_key_limit_keeps_the_old_behaviour(backend, unique):
    """Opt-out is the previous behaviour exactly, so nothing silently changes."""
    reg = registry(backend, unique, max_entries=3, max_per_key=0)
    assert reg.put("z1", run_of("z1", "visitor:v")) is True
    assert reg.put("z2", run_of("z2", "visitor:v")) is True
    assert reg.put("z3", run_of("z3", "visitor:v")) is True
    assert reg.put("z4", run_of("z4", "visitor:v")) is False  # only the global cap


# =========================================================== I. CHAIN-01 regression


def test_rotating_quota_keys_cannot_starve_the_store_of_a_legitimate_visitor(
    backend, unique
):
    """CHAIN-01, as far as F-02 is responsible for it.

    F-01 is untouched and is not what this tests: the rotation is simulated by
    handing the registry different opaque quota keys, which is what an attacker
    able to mint buckets would achieve. What this proves is the half F-02 owns
    -- that *one* key can no longer take the store, so the chain's damage is
    bounded by how many keys an attacker can actually mint rather than by a
    single one being enough.
    """
    reg = registry(backend, unique, max_entries=8, max_per_key=2)

    # The attack as it actually arrives: a burst per bucket, not a tidy
    # round-robin. Interleaving keys would let the global cap alone produce the
    # same distribution and the test would pass against the vulnerable code.
    accepted = 0
    for bucket in range(20):
        for attempt in range(10):
            if reg.put(f"rot{bucket}-{attempt}", run_of(f"rot{bucket}-{attempt}",
                                                        f"visitor:{bucket}")):
                accepted += 1
    assert accepted == 8  # the global ceiling, not one visitor's appetite

    # No single key holds more than its share of what was admitted, and the
    # store is spread across several of them rather than captured by the first.
    holders: dict[str, int] = {}
    for bucket in range(20):
        for attempt in range(10):
            record = reg.get(f"rot{bucket}-{attempt}")
            if record is not None:
                holders[record.quota_key] = holders.get(record.quota_key, 0) + 1
    assert holders and max(holders.values()) <= 2
    assert len(holders) >= 4  # four buckets share it, not one taking all eight


def test_one_key_alone_cannot_fill_the_store_however_many_it_sends(backend, unique):
    """The finding's own proof of concept, inverted into an assertion."""
    reg = registry(backend, unique, max_entries=50, max_per_key=6)

    accepted = sum(
        1 for i in range(200) if reg.put(f"f{i}", run_of(f"f{i}", "visitor:attacker"))
    )
    assert accepted == 6
    assert len(reg) == 6  # 44 slots still free for everyone else
    assert reg.put("legit", run_of("legit", "visitor:victim")) is True


# ============================================== configuration follows the project


def test_the_default_share_is_derived_from_the_global_cap():
    settings = Settings.from_env(load_dotenv_file=False)
    assert settings.max_pending_confirmations == 50
    assert settings.effective_max_pending_per_quota_key == 6  # max(2, 50 // 8)


def test_the_share_never_derives_above_the_global_cap():
    """A tiny deployment must not derive a per-key share larger than the whole."""
    base = Settings.from_env(load_dotenv_file=False)
    for cap in (1, 2, 4, 8, 16, 50, 400):
        settings = replace(base, max_pending_confirmations=cap, max_pending_per_quota_key=0)
        assert 1 <= settings.effective_max_pending_per_quota_key <= cap


def test_an_explicit_share_is_honoured_and_still_bounded_by_the_global_cap():
    base = Settings.from_env(load_dotenv_file=False)
    settings = replace(base, max_pending_confirmations=50, max_pending_per_quota_key=3)
    assert settings.effective_max_pending_per_quota_key == 3
    settings = replace(base, max_pending_confirmations=4, max_pending_per_quota_key=99)
    assert settings.effective_max_pending_per_quota_key == 4


# ================================ the finding, end to end through the platform


def _suspending_platform(**over):
    """A platform whose model always proposes one high-risk write.

    Deterministic and offline: the provider is a fixture, not the stub, so the
    route to a suspension is fixed rather than depending on how a sentence is
    phrased.
    """
    import json
    from pathlib import Path

    from agent_platform.llm.provider import Embedding, EmbedTask, LLMResponse, Purpose
    from agent_platform.persistence.memory import InMemoryRepository
    from agent_platform.platform import AgentPlatform

    plan = {
        Purpose.ROUTE: json.dumps({"route": "executor", "reasoning": "write"}),
        Purpose.SELECT_TOOL: json.dumps(
            {"tool": "get_order", "arguments_json": '{"order_id":"ORD-1001"}'}
        ),
        Purpose.RESEARCH: "gathered",
        Purpose.PROPOSE_ACTION: json.dumps(
            {
                "tool": "update_record",
                "arguments_json": '{"record_id":"ORD-1001","field":"status","value":"x"}',
            }
        ),
        Purpose.VALIDATE: json.dumps({"approved": True, "reasons": []}),
        Purpose.RESPOND: "done",
    }

    class Provider:
        name = property(lambda self: "fixture")
        model = property(lambda self: "fixture-v1")

        def generate(self, prompt, *, purpose, system=None, response_schema=None,
                     temperature=0.0, timeout=None):
            return LLMResponse(
                text=plan.get(purpose, "{}"), provider="fixture", model="fixture-v1",
                purpose=purpose, input_tokens=1, output_tokens=1, latency_ms=0.1,
            )

        def embed(self, text, *, task=EmbedTask.QUERY, timeout=None):
            return Embedding(
                vector=(0.0,) * 8, provider="fixture", model="fixture-v1", task=task
            )

    settings = replace(
        Settings.from_env(load_dotenv_file=False),
        gemini_api_key=None,
        database_path=Path(":memory:"),
        api_auth_mode="disabled",
        api_auth_keys=None,
        requests_per_minute=10_000,
        requests_per_hour=100_000,
        global_requests_per_minute=100_000,
        global_requests_per_hour=1_000_000,
        **over,
    )
    return AgentPlatform(
        settings, repository=InMemoryRepository(), provider=Provider(), use_judge=False
    )


def test_one_visitor_can_no_longer_deny_human_in_the_loop_to_everyone():
    """The red-team proof of concept, run against the platform, now refused.

    Before the per-caller share existed this loop suspended fifty runs and the
    victim's first request was refused. What it asserts now is the property
    that was missing: the attacker stops at their own share, and the store
    still has room for everybody else.
    """
    platform = _suspending_platform(max_pending_confirmations=50)
    try:
        share = platform.settings.effective_max_pending_per_quota_key
        suspended = sum(
            1
            for _ in range(200)
            if platform.run("update order ORD-1001", quota_key="visitor:attacker").status
            == "awaiting_confirmation"
        )
        assert suspended == share == 6

        victim = platform.run("update order ORD-1001", quota_key="visitor:victim")
        assert victim.status == "awaiting_confirmation"
        assert len(platform._pending) == share + 1
    finally:
        platform.close()


def test_approving_frees_the_requesters_share_not_the_approvers():
    """Release runs through the real confirm path, and is charged correctly.

    The approver is deliberately a different party from the requester, so this
    also pins that a reviewer's own share is untouched by reviewing.
    """
    platform = _suspending_platform(max_pending_confirmations=50, max_pending_per_quota_key=2)
    try:
        first = platform.run("update order ORD-1001", quota_key="visitor:v")
        second = platform.run("update order ORD-1001", quota_key="visitor:v")
        assert first.status == second.status == "awaiting_confirmation"
        assert platform.run("update order ORD-1001", quota_key="visitor:v").status == "failed"

        resolved = platform.confirm(first.request_id, approved=True, actor="ops")
        assert resolved.status == "success"

        again = platform.run("update order ORD-1001", quota_key="visitor:v")
        assert again.status == "awaiting_confirmation"
    finally:
        platform.close()


def test_declining_frees_the_share_too():
    platform = _suspending_platform(max_pending_confirmations=50, max_pending_per_quota_key=1)
    try:
        first = platform.run("update order ORD-1001", quota_key="visitor:v")
        assert first.status == "awaiting_confirmation"
        assert platform.run("update order ORD-1001", quota_key="visitor:v").status == "failed"

        declined = platform.confirm(first.request_id, approved=False, actor="ops")
        assert declined.status == "declined"

        assert (
            platform.run("update order ORD-1001", quota_key="visitor:v").status
            == "awaiting_confirmation"
        )
    finally:
        platform.close()


def test_the_quota_key_stays_out_of_traces_and_responses():
    """Internal accounting, and it stays internal.

    The key is an opaque bucket identifier rather than an address, but it is
    still the platform's business and not the caller's, so nothing that leaves
    the process should carry it.
    """
    import json
    from dataclasses import asdict

    from agent_platform.api.schemas import run_response_from

    platform = _suspending_platform(max_pending_confirmations=50)
    try:
        marker = "visitor:0123456789abcdef"
        result = platform.run("update order ORD-1001", quota_key=marker)
        assert result.status == "awaiting_confirmation"

        events = platform.repository.events_for_request(result.request_id)
        assert marker not in json.dumps(events, default=str)
        assert marker not in json.dumps(
            platform.repository.recent_requests(10), default=str
        )
        assert marker not in json.dumps(asdict(result), default=str)
        # And the shape that actually crosses the HTTP boundary.
        assert marker not in json.dumps(
            run_response_from(result).model_dump(mode="json"), default=str
        )
    finally:
        platform.close()


# ===================================== the unattributed bucket, and who is in it


def test_callers_with_no_quota_key_share_one_bucket_and_are_still_capped():
    """The CLI, the evaluator and an auth-disabled API all land in one bucket.

    Capping it is the conservative reading and matches what ``visitor_key``
    already does when it can identify nobody: one shared bucket "can only refuse
    more than intended, never less". Exempting it would be the opposite -- a
    caller that reaches the global bucket by omission would silently get no
    per-caller ceiling at all, which is the failure mode this codebase refuses
    everywhere else.
    """
    from agent_platform.security.rate_limit import GLOBAL_KEY

    platform = _suspending_platform(max_pending_confirmations=50)
    try:
        share = platform.settings.effective_max_pending_per_quota_key
        # No quota_key at all: exactly what cli.py and the evaluator pass.
        held = []
        for _ in range(share + 4):
            result = platform.run("update order ORD-1001")
            if result.status == "awaiting_confirmation":
                held.append(result.request_id)
        assert len(held) == share

        # Charged to the global bucket by name, not merely counted somewhere.
        assert {platform._pending.get(r).quota_key for r in held} == {GLOBAL_KEY}

        # An identified visitor is unaffected by the unattributed bucket's use.
        visitor = platform.run("update order ORD-1001", quota_key="visitor:v")
        assert visitor.status == "awaiting_confirmation"
        assert platform._pending.get(visitor.request_id).quota_key == "visitor:v"
    finally:
        platform.close()


def test_the_evaluation_dataset_fits_inside_the_unattributed_share():
    """A guard on a relationship that is currently exact, and silent if broken.

    ``Evaluator`` calls ``platform.run(case.input)`` with no quota key, so every
    case draws on the global bucket, and a case with ``expect_confirmation``
    leaves its suspension behind -- the evaluator asserts the status and never
    approves it. With the default cap those cases and the derived share are both
    six. One more confirmation case would therefore be refused for capacity and
    reported as an evaluation failure, with nothing pointing at the real cause.

    If this fails, the dataset outgrew the share. Either raise
    ``MAX_PENDING_CONFIRMATIONS_PER_KEY`` for evaluation runs, or have the
    evaluator resolve the confirmations it raises. Do not quietly widen the
    security ceiling to fit a dataset.
    """
    import json
    from pathlib import Path

    dataset = Path("src/agent_platform/evaluation/datasets/tool_use.json")
    cases = json.loads(dataset.read_text(encoding="utf-8"))
    if isinstance(cases, dict):
        cases = cases.get("cases", [])
    expecting = sum(1 for case in cases if case.get("expect_confirmation"))

    share = Settings.from_env(load_dotenv_file=False).effective_max_pending_per_quota_key
    assert expecting <= share, (
        f"{expecting} evaluation cases leave a suspension behind but one caller "
        f"may hold {share}; the {share + 1}th would be refused for capacity and "
        "look like an evaluation failure"
    )
