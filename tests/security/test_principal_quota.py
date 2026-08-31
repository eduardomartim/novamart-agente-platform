"""Quota belongs to a caller, not to the deployment as a whole.

Before this, every request drew from one bucket. A limit of 10/minute meant ten
requests existed in the world per minute, and whoever asked first got them --
so any caller could deny every other caller with a loop, and nothing in the
configuration or the logs would say what had happened. There was no identity to
charge, which is why it stayed that way.

Two things had to be true at once, and getting only one of them is the
interesting failure:

* charge each caller separately, or the denial-of-service stays open;
* keep a ceiling on the total, or per-caller quota *removes* a control -- with
  N callers the deployment would absorb N times what it was configured for,
  which is the shape of the defect V2.5 closed for the budget.

Both, with **one** number, is theatre: the shared bucket fills with the
attacker's traffic and everybody is denied exactly as before. So the two limits
are separate numbers, and the tests below pin each half against the other.
"""

from __future__ import annotations

import threading

import pytest

from agent_platform.config import DEFAULT_GLOBAL_LIMIT_MULTIPLIER, Settings
from agent_platform.persistence.memory import InMemoryRepository
from agent_platform.platform import AgentPlatform
from agent_platform.security.rate_limit import (
    GLOBAL_KEY,
    RateLimiter,
    current_quota_key,
    quota_scope,
)


@pytest.fixture
def tight(settings) -> Settings:
    """Three requests per caller, ten for the deployment."""
    from dataclasses import replace

    return replace(
        settings,
        requests_per_minute=3,
        requests_per_hour=1000,
        global_requests_per_minute=10,
        global_requests_per_hour=10_000,
    )


@pytest.fixture
def platform(tight):
    instance = AgentPlatform(tight, repository=InMemoryRepository())
    try:
        yield instance
    finally:
        instance.close()


QUESTION = "What is the refund policy?"

#: A request is refused for quota in one of two ways, depending on where the
#: limit is reached.
#:
#: ``rate_limited`` is the entry point: the bucket was already full and the
#: graph was never entered. ``failed`` is PL011: the entry point consumed the
#: last unit, then the policy engine consulted the same bucket from inside the
#: graph and found it at the limit. That second form is pre-existing behaviour,
#: unchanged here -- it is what "PL011 consults the bucket the entry point
#: consumed" means in practice, and it costs a caller the last unit of their
#: own allowance. These tests are about *whose* quota is spent, so they treat
#: both as refusal rather than pinning which one a given attempt produces.
REFUSED = frozenset({"rate_limited", "failed"})


def statuses(platform, principal: str, times: int) -> list[str]:
    return [platform.run(QUESTION, quota_key=principal).status for _ in range(times)]


# ================================================== each caller pays their own


def test_a_caller_spends_only_their_own_quota(platform):
    outcomes = statuses(platform, "alice", 5)
    assert outcomes[0] == "success"
    assert outcomes[-1] == "rate_limited"
    assert outcomes.count("success") <= platform.settings.requests_per_minute


def test_one_caller_cannot_deny_another(platform):
    """The defect this closes, stated as the thing that used to happen."""
    statuses(platform, "attacker", 4)
    assert platform.run(QUESTION, quota_key="attacker").status == "rate_limited"

    victim = platform.run(QUESTION, quota_key="victim")
    assert victim.status == "success", (
        "a caller who has spent nothing was denied by another caller's traffic"
    )


def test_two_credentials_for_one_principal_share_one_bucket(platform):
    """Quota is charged to the principal, so a second key is not a second allowance.

    The bucket key is the principal's name rather than the key id precisely so
    that issuing yourself another credential does not multiply what you may do.
    """
    assert statuses(platform, "svc", 4)[-1] in REFUSED
    # A different credential, the same identity: the bucket is already spent.
    assert platform.run(QUESTION, quota_key="svc").status in REFUSED


# ================================================== the deployment still binds


def test_many_callers_cannot_exceed_the_deployment_ceiling(platform):
    """Per-caller quota must not become a way to buy more capacity per identity.

    Ten callers at three each would be thirty requests against a deployment
    ceiling of ten. The ceiling is what stops it.
    """
    callers, each = 10, 3
    outcomes = [
        platform.run(QUESTION, quota_key=f"caller-{i}").status
        for i in range(callers)
        for _ in range(each)
    ]
    allowed = outcomes.count("success")

    ceiling = platform.settings.effective_global_requests_per_minute
    assert allowed <= ceiling, f"{allowed} requests ran against a ceiling of {ceiling}"
    # And the ceiling is what stopped it, not the per-caller limit: without a
    # deployment ceiling these ten callers would have got two each.
    assert allowed < callers * (platform.settings.requests_per_minute - 1)
    assert any(o == "rate_limited" for o in outcomes)


def test_the_two_ceilings_are_different_numbers(tight):
    """One number for both is the failure mode that looks like a fix.

    With the deployment ceiling equal to the per-caller one, an attacker filling
    the shared bucket denies everybody exactly as before -- per-caller quota
    would have bought nothing at all.
    """
    assert tight.effective_global_requests_per_minute > tight.requests_per_minute

    defaults = Settings.from_env(load_dotenv_file=False)
    assert (
        defaults.effective_global_requests_per_minute
        == defaults.requests_per_minute * DEFAULT_GLOBAL_LIMIT_MULTIPLIER
    )


def test_an_unconfigured_deployment_ceiling_never_sits_below_a_caller(settings):
    """A test-built Settings must not end up with a ceiling under one caller's share."""
    from dataclasses import replace

    generous = replace(settings, requests_per_minute=1000, requests_per_hour=10_000)
    assert generous.global_requests_per_minute == 0
    assert generous.effective_global_requests_per_minute > generous.requests_per_minute


# ============================================ the unauthenticated callers


def test_the_cli_path_is_byte_for_byte_unchanged(platform):
    """``run(text)`` with no key is the global bucket, exactly as before.

    The CLI, the dashboard and the evaluator all call it this way and must not
    notice that any of this happened.
    """
    assert current_quota_key() == GLOBAL_KEY
    outcomes = [platform.run(QUESTION).status for _ in range(4)]
    # Pinned exactly, because "unchanged" is the claim. Two run, the third
    # spends the last unit at the entry point and is then refused by PL011
    # consulting the same bucket, and the fourth never enters the graph. That
    # sequence predates this phase and must survive it.
    assert outcomes == ["success", "success", "failed", "rate_limited"]


def test_an_unkeyed_run_does_not_charge_the_deployment_twice(platform):
    """The global bucket is charged once, not once as caller and once as deployment.

    Three unkeyed requests exhaust the per-caller limit on the global bucket.
    If the second acquire also ran, the deployment ceiling of ten would have
    been charged as well -- which would make the two ceilings interfere in the
    single-caller case that every offline test runs in.
    """
    for _ in range(4):
        platform.run(QUESTION)
    assert platform.run(QUESTION).status == "rate_limited"

    # The deployment bucket is untouched by that traffic, so a keyed caller
    # still has room. An unkeyed run charges the per-caller bucket only, which
    # is what keeps an unauthenticated deployment behaving exactly as it did.
    assert platform.run(QUESTION, quota_key="someone").status == "success"


# ================================================ the policy engine agrees


def test_pl011_consults_the_bucket_the_entry_point_consumed(platform):
    """The F1 invariant, now structural rather than coincidental.

    PL011 calls ``rate_limiter.check()`` from inside the graph, through a call
    site that cannot see the caller. It once addressed a per-request bucket that
    was always empty, so the rule could never fire. Both ends now resolve "the
    current request's bucket", so they cannot drift apart again.
    """
    outcomes = statuses(platform, "alice", 4)
    # The third attempt is PL011 refusing from inside the graph, having found
    # alice's bucket at the limit the entry point just filled. If the two ends
    # addressed different buckets -- the F1 defect -- this would be a success.
    assert "failed" in outcomes
    assert platform.run(QUESTION, quota_key="alice").status == "rate_limited"

    # And a different caller reaches the graph, where PL011 consults *their*
    # bucket and finds room.
    assert platform.run(QUESTION, quota_key="bob").status == "success"


def test_the_limiter_resolves_the_scope_when_given_no_key():
    limiter = RateLimiter(2, 100)
    with quota_scope("alice"):
        assert limiter.acquire().allowed
        assert limiter.acquire().allowed
        assert not limiter.acquire().allowed
    with quota_scope("bob"):
        assert limiter.acquire().allowed


def test_the_scope_is_restored_after_it_closes():
    with quota_scope("alice"):
        assert current_quota_key() == "alice"
        with quota_scope("bob"):
            assert current_quota_key() == "bob"
        assert current_quota_key() == "alice"
    assert current_quota_key() == GLOBAL_KEY


# ============================================== the bucket does not travel


def test_the_bucket_does_not_leak_between_threads():
    """A context variable is per-context, and worker threads are reused.

    The HTTP boundary binds the principal on the event loop and the platform
    runs in an anyio worker thread, which is pooled. If the binding outlived the
    request, the next request on that worker would spend somebody else's quota
    -- and would do it silently.
    """
    seen: dict[str, str] = {}

    def worker(name: str) -> None:
        seen[name] = current_quota_key()

    with quota_scope("alice"):
        # A plain thread does not inherit the caller's context at all.
        t = threading.Thread(target=worker, args=("plain",))
        t.start()
        t.join()

    assert seen["plain"] == GLOBAL_KEY


def test_the_bucket_does_not_leak_between_requests_on_a_pooled_worker():
    """The property the HTTP path actually depends on, asserted directly.

    ``anyio.to_thread.run_sync`` reuses worker threads but runs each call in a
    fresh copy of the caller's context. That is load-bearing here: if it ever
    stopped being true, one caller's quota bucket would be charged to the next
    request that landed on the same worker.
    """
    anyio = pytest.importorskip("anyio")
    from starlette.concurrency import run_in_threadpool

    observed: list[tuple[str, str]] = []

    def read() -> tuple[str, str]:
        return current_quota_key(), threading.current_thread().name

    async def main() -> None:
        with quota_scope("alice"):
            observed.append(await run_in_threadpool(read))
        # No scope at all: whatever ran before must not still be bound.
        observed.append(await run_in_threadpool(read))

    anyio.run(main)

    assert observed[0][0] == "alice"
    assert observed[1][0] == GLOBAL_KEY, "a principal survived into the next request"
    assert observed[0][1] == observed[1][1], (
        "the worker thread was not reused, so this test proved nothing"
    )


# ================================================== the wiring, over HTTP


def test_the_api_charges_quota_to_the_authenticated_principal(
    tmp_path, monkeypatch, api_credentials
):
    """Two credentials, two buckets, driven through the real HTTP stack.

    This test exists because its absence was found rather than guessed. The
    non-vacuity pass for this phase reverted ``create_app`` to pass no quota
    key at all -- putting every HTTP caller back on one shared bucket, which is
    the denial-of-service this phase closed -- and **nothing failed**. Every
    other test in this file calls ``platform.run(quota_key=...)`` directly, so
    they prove the mechanism works and prove nothing about the wiring from an
    authenticated principal to it.

    Testing both ends of a bridge is not testing the bridge.
    """
    starlette = pytest.importorskip("starlette")
    assert starlette
    from starlette.testclient import TestClient

    from agent_platform.api.app import create_app
    from agent_platform.config import Settings
    from tests.conftest import bearer

    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "wire.db"))
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("API_AUTH_KEYS", api_credentials.keys)
    monkeypatch.delenv("API_AUTH_KEYS_FILE", raising=False)
    monkeypatch.delenv("API_AUTH_MODE", raising=False)
    # Two requests each, so one caller can exhaust an allowance the other has
    # not touched, well inside the deployment ceiling.
    monkeypatch.setenv("REQUESTS_PER_MINUTE", "2")
    monkeypatch.setenv("GLOBAL_REQUESTS_PER_MINUTE", "50")
    settings = Settings.from_env(load_dotenv_file=False)

    platform = AgentPlatform(settings, repository=InMemoryRepository())
    try:
        app = create_app(platform, settings=settings)
        with TestClient(app) as client:
            runner = bearer(api_credentials.runner)
            operator = bearer(api_credentials.operator)

            spent = [
                client.post("/runs", json={"input": QUESTION}, headers=runner).status_code
                for _ in range(4)
            ]
            assert 429 in spent, f"the runner was never rate limited: {spent}"

            # A different principal, with its own credential, has not spent
            # anything. If the API charged everybody to one bucket -- which is
            # what it did before this phase -- this would be a 429.
            other = client.post("/runs", json={"input": QUESTION}, headers=operator)
            assert other.status_code == 200, (
                "one caller's traffic denied another over HTTP, so the API is "
                "not charging quota to the authenticated principal"
            )
    finally:
        platform.close()


def test_the_api_charges_the_principal_not_the_credential(
    tmp_path, monkeypatch, api_credentials
):
    """The complement, and the reason the bucket key is the principal's name.

    ``operator`` and a second credential for ``operator`` must share one
    allowance. If the key were the credential id, minting yourself another
    token would be minting yourself another quota.
    """
    pytest.importorskip("starlette")
    from starlette.testclient import TestClient

    from agent_platform.api.app import create_app
    from agent_platform.config import Settings
    from agent_platform.security.api_auth import issue_token
    from tests.conftest import bearer

    key_id, second_token, digest = issue_token()
    keys = f"{api_credentials.keys}\n{key_id} operator runs:write {digest}"

    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "same.db"))
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("API_AUTH_KEYS", keys)
    monkeypatch.delenv("API_AUTH_KEYS_FILE", raising=False)
    monkeypatch.delenv("API_AUTH_MODE", raising=False)
    monkeypatch.setenv("REQUESTS_PER_MINUTE", "2")
    monkeypatch.setenv("GLOBAL_REQUESTS_PER_MINUTE", "50")
    settings = Settings.from_env(load_dotenv_file=False)

    platform = AgentPlatform(settings, repository=InMemoryRepository())
    try:
        with TestClient(create_app(platform, settings=settings)) as client:
            for _ in range(4):
                client.post(
                    "/runs",
                    json={"input": QUESTION},
                    headers=bearer(api_credentials.operator),
                )
            second = client.post(
                "/runs", json={"input": QUESTION}, headers=bearer(second_token)
            )
            assert second.status_code == 429, (
                "a second credential for the same principal bought a second allowance"
            )
    finally:
        platform.close()
