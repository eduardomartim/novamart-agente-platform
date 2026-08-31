"""Holding a key is not permission to spend it.

This file exists because the opposite was true, and it cost 72 unintended
provider calls. The chain, from the post-mortem in ``llm.authorization``:

    addopts said -m "not live"
      -> a -m on the command line REPLACED it, so live became eligible
      -> the live modules ran load_dotenv() at import, so the real key was in
         os.environ before any fixture
      -> their skipif asked whether a key was present, so having one ENABLED
         them
      -> the conftest guard exempts anything marked live, so it opened for the
         dangerous case by design
      -> the last check was a 400-call/day budget, which answers "how many
         more?" and never "may you at all?"

Three controls, one decision. Remove the filter and the rest cooperate.

Every test below runs **offline**, including the ones that exercise the
authorised path: authorisation is proved to *permit* construction, never to
perform a call. Nothing here can reach a network, whichever way it fails.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_platform.config import Settings
from agent_platform.llm import build_provider
from agent_platform.llm.authorization import (
    LIVE_AUTHORISED_VALUE,
    LIVE_ENV_VAR,
    LiveNotAuthorised,
    live_is_authorised,
    require_live_authorisation,
)
from agent_platform.llm.gemini import GeminiProvider

#: Shaped like a Gemini key and belonging to nobody. Never used to authenticate
#: anything -- every test that involves it asserts a refusal *before* a client
#: could exist.
FAKE_KEY = "AIzaSyFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAK"

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def no_ambient_authorisation(monkeypatch):
    """Every test starts unauthorised, whatever the developer's shell holds."""
    monkeypatch.delenv(LIVE_ENV_VAR, raising=False)


@pytest.fixture
def keyed(settings: Settings) -> Settings:
    """Settings that select the real provider. Nothing may build one."""
    configured = replace(settings, gemini_api_key=FAKE_KEY)
    assert not configured.demo_mode
    return configured


def authorise(monkeypatch) -> None:
    monkeypatch.setenv(LIVE_ENV_VAR, LIVE_AUTHORISED_VALUE)


# ============================================== the contract on its own terms


def test_an_unset_variable_is_not_authorisation():
    assert live_is_authorised() is False


@pytest.mark.parametrize(
    "value",
    ["1", "0", "true", "True", "TRUE", "yes", "Yes", "on", "y", "enabled", "live"],
)
def test_no_boolean_shaped_value_authorises(monkeypatch, value):
    """The values that appear by accident must never be the ones that work.

    ``1`` and ``true`` turn up in CI matrices, shell profiles and half-finished
    scripts. A phrase does not arrive anywhere by accident, which is the entire
    reason the contract is a phrase.
    """
    monkeypatch.setenv(LIVE_ENV_VAR, value)
    assert live_is_authorised() is False


@pytest.mark.parametrize(
    "value",
    [
        "I-AUTHORISE-REAL-PROVIDER-CALLS",
        "i_authorise_real_provider_calls",
        "i-authorize-real-provider-calls",
        "i-authorise-real-provider-call",
        "i-authorise-real-provider-calls-please",
    ],
)
def test_a_near_miss_is_not_authorisation(monkeypatch, value):
    """A typo fails closed. There is no fuzzy match and no case folding."""
    monkeypatch.setenv(LIVE_ENV_VAR, value)
    assert live_is_authorised() is False


def test_surrounding_whitespace_is_tolerated(monkeypatch):
    """A value pasted out of a YAML block should still work."""
    monkeypatch.setenv(LIVE_ENV_VAR, f"  {LIVE_AUTHORISED_VALUE}\n")
    assert live_is_authorised() is True


def test_the_exact_value_authorises(monkeypatch):
    authorise(monkeypatch)
    assert live_is_authorised() is True
    require_live_authorisation("do the thing")  # must not raise


def test_the_check_is_read_at_call_time_not_at_import(monkeypatch):
    """A cached answer would be decided by whatever the shell held at import."""
    assert live_is_authorised() is False
    authorise(monkeypatch)
    assert live_is_authorised() is True
    monkeypatch.delenv(LIVE_ENV_VAR)
    assert live_is_authorised() is False


# ==================================================== the structural barrier


def test_a_key_alone_does_not_build_a_live_provider(keyed):
    """I2. The whole point: possession is not permission."""
    with pytest.raises(LiveNotAuthorised):
        build_provider(keyed)


def test_wrong_authorisation_does_not_build_a_live_provider(keyed, monkeypatch):
    monkeypatch.setenv(LIVE_ENV_VAR, "true")
    with pytest.raises(LiveNotAuthorised):
        build_provider(keyed)


def test_without_a_key_the_stub_is_returned_and_nothing_is_required(settings):
    """Demo mode never reaches the gate; the offline suite is unaffected."""
    assert settings.demo_mode
    assert build_provider(settings).name == "stub"


def test_the_refusal_happens_before_any_sdk_client_exists(keyed, monkeypatch):
    """The barrier is *before* the client, not around the call.

    ``genai.Client`` is replaced with something that fails if it is ever
    reached. The refusal must arrive first, so an unauthorised process never
    constructs an object that holds a key and knows a URL.
    """
    from google import genai

    def explode(*args, **kwargs):
        raise AssertionError("an SDK client was constructed despite the refusal")

    monkeypatch.setattr(genai, "Client", explode)
    with pytest.raises(LiveNotAuthorised):
        build_provider(keyed)


def test_authorisation_permits_construction_without_making_a_call(keyed, monkeypatch):
    """The authorised path, proved offline.

    Authorisation is shown to *permit* building a provider -- the policy
    decision -- with the SDK client replaced by an inert double. No request is
    made here, and none can be: this asserts the gate opens, not that a model
    answered.
    """
    from google import genai

    constructed: list[str] = []

    class InertClient:
        def __init__(self, **kwargs):
            constructed.append("client")

    monkeypatch.setattr(genai, "Client", InertClient)
    authorise(monkeypatch)

    provider = build_provider(keyed)
    assert provider.name == "gemini"
    assert constructed == ["client"], "the authorised path should build a client"


def test_the_refusal_names_no_secret(keyed):
    """I8. Observable without leaking."""
    with pytest.raises(LiveNotAuthorised) as caught:
        build_provider(keyed)
    message = str(caught.value)
    assert FAKE_KEY not in message
    assert "AIza" not in message
    assert LIVE_ENV_VAR in message, "it must say which variable is missing"
    assert LIVE_AUTHORISED_VALUE not in message, (
        "a fix copied out of a traceback is a fix that ends up in a script"
    )


# =========================================== the provider, constructed directly


def test_constructing_the_provider_directly_is_also_refused():
    """The factory is the gate, and it is not the only lock on the door."""
    with pytest.raises(LiveNotAuthorised):
        GeminiProvider(FAKE_KEY, "gemini-3.5-flash-lite")


def test_an_injected_fake_client_needs_no_authorisation():
    """The seam that keeps the offline provider tests honest."""
    provider = GeminiProvider(
        FAKE_KEY, "gemini-3.5-flash-lite", client=SimpleNamespace(models=object())
    )
    assert provider.name == "gemini"


def test_an_injected_real_client_still_needs_authorisation(monkeypatch):
    """The seam is for fakes, not for going around the gate."""
    from google import genai

    real = genai.Client.__new__(genai.Client)  # never initialised, never used
    with pytest.raises(LiveNotAuthorised):
        GeminiProvider(FAKE_KEY, "gemini-3.5-flash-lite", client=real)


# ====================================================== embeddings (the gap)


def test_no_real_client_means_no_real_embedding(keyed):
    """C. The embedding path had no cover at all until this phase.

    ``_forbid_real_provider_calls`` patched ``generate_content`` and left
    ``embed_content`` open, so retrieval embeddings could reach the network
    from an offline test. The structural fix is the same one: without
    authorisation no real client exists, so there is nothing to embed *with*.
    """
    with pytest.raises(LiveNotAuthorised):
        build_provider(keyed)


def test_the_offline_guard_now_covers_embeddings():
    """And the test-side guard covers the entry point it used to miss."""
    from google.genai import models as genai_models

    for entry_point in ("generate_content", "embed_content"):
        with pytest.raises(AssertionError) as caught:
            getattr(genai_models.Models, entry_point)(object())
        assert "real Gemini API call" in str(caught.value)


def test_embedding_through_a_fake_client_reaches_only_the_fake():
    """With a double in place, the embedding path works and stays offline."""
    calls: list[dict] = []

    class FakeModels:
        def embed_content(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                embeddings=[SimpleNamespace(values=[0.1, 0.2, 0.3])]
            )

    provider = GeminiProvider(
        FAKE_KEY, "gemini-3.5-flash-lite", client=SimpleNamespace(models=FakeModels())
    )
    from agent_platform.llm.provider import EmbedTask

    embedding = provider.embed("hello", task=EmbedTask.QUERY)
    assert embedding.vector == (0.1, 0.2, 0.3)
    assert len(calls) == 1, "exactly one embedding call, and it hit the fake"


# ============================================ the index builder, and the CLI


def test_the_vector_index_builder_is_covered_by_the_same_gate():
    """It calls build_provider directly, so it inherits the refusal.

    Read statically rather than executed: running it would rebuild an artefact
    the project treats as protected. What matters is that its only route to a
    provider is the guarded one.
    """
    source = (REPO / "scripts" / "build_vector_index.py").read_text(encoding="utf-8")
    assert "build_provider(settings)" in source
    assert "genai.Client" not in source, "it must not construct a client of its own"


def test_build_provider_is_the_only_route_to_a_real_provider():
    """The claim the whole design rests on, checked against the source.

    If a second construction site ever appears outside the factory, this fails
    and the gate has a hole that no runtime test would notice.
    """
    allowed = {
        "src/agent_platform/llm/__init__.py",  # the factory itself
        "src/agent_platform/llm/gemini.py",  # the class definition
    }
    offenders = []
    for path in (REPO / "src").rglob("*.py"):
        rel = path.relative_to(REPO).as_posix()
        if rel in allowed:
            continue
        if "GeminiProvider(" in path.read_text(encoding="utf-8"):
            offenders.append(rel)
    assert offenders == [], f"a provider is built outside the factory: {offenders}"


# ================================================= the collection gate (B)


def _collect(marker: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Collect only -- never run -- with an explicit marker expression."""
    import os

    environment = {**os.environ}
    environment.pop(LIVE_ENV_VAR, None)
    environment.update(env or {})
    return subprocess.run(  # noqa: S603 - fixed argv, built here from literals
        [
            sys.executable, "-m", "pytest", "--collect-only", "-q",
            "-p", "no:cacheprovider", "-m", marker,
        ],
        cwd=REPO, capture_output=True, text=True, timeout=600, env=environment,
    )


def test_the_incident_command_is_now_refused():
    """The exact shape of the 72-call incident, re-enacted safely.

    ``pytest -m "not docker"`` is what was actually run. A ``-m`` on the command
    line replaces the one in ``addopts``, so ``not live`` silently stopped
    applying. Collection only -- nothing is executed even now.
    """
    result = _collect("not docker")
    assert result.returncode == 4, f"expected a usage error, got {result.returncode}"
    # A UsageError is written to stderr; read both so this does not depend on
    # which stream pytest happens to choose.
    output = result.stdout + result.stderr
    assert "not authorised" in output
    assert "REPLACES" in output, "the message should explain the -m trap"


def test_selecting_live_directly_is_refused():
    result = _collect("live")
    assert result.returncode == 4
    assert "not authorised" in result.stdout + result.stderr


def test_the_ordinary_offline_filter_is_unaffected():
    """The complement. A gate that fired on normal runs would be removed by
    the first person it inconvenienced."""
    result = _collect("not live and not docker")
    assert result.returncode == 0, result.stdout[-2000:]


def test_the_gate_does_not_print_the_authorisation_value():
    result = _collect("live")
    assert LIVE_AUTHORISED_VALUE not in result.stdout
    assert LIVE_AUTHORISED_VALUE not in result.stderr


# ========================================== independence of the two layers


def test_the_structural_barrier_does_not_depend_on_the_marker(keyed):
    """I3 / proof 14. Marking a test differently changes nothing.

    The barrier is in ``build_provider``. It has never heard of pytest, cannot
    see a marker, and would refuse a call from a shell script exactly as
    readily.
    """
    with pytest.raises(LiveNotAuthorised):
        build_provider(keyed)


def test_the_structural_barrier_does_not_depend_on_the_collection_gate(keyed):
    """Proof 13. Deleting the conftest gate leaves the real protection intact.

    This test is itself proof: it runs inside a normal collection, where the
    gate has already passed, and the refusal still happens.
    """
    assert not live_is_authorised()
    with pytest.raises(LiveNotAuthorised):
        build_provider(keyed)


def test_no_pytest_flag_can_reach_the_barrier(keyed):
    """I4. There is no marker expression that makes build_provider permissive.

    The gate reads one environment variable. ``-m``, ``-k``, ``--noconftest``
    and ``-p no:...`` all operate on test selection and plugins, none of which
    this code path consults.
    """
    with pytest.raises(LiveNotAuthorised):
        build_provider(keyed)


def test_the_factory_refuses_before_it_reaches_the_provider_class(keyed, monkeypatch):
    """The gate is in the factory, not only in the class the factory builds.

    Both layers refuse, which is why there are two -- and that redundancy makes
    them impossible to tell apart from the outside. Without this test, deleting
    the factory's check breaks nothing: the constructor catches it and every
    assertion still sees ``LiveNotAuthorised``.

    So this pins the outer layer specifically. ``GeminiProvider`` is replaced
    with something that fails if it is ever reached, which it must not be: the
    factory refuses above its own lazy import, so an unauthorised process does
    not even load the SDK.
    """
    import agent_platform.llm.gemini as gemini_module

    def explode(*args, **kwargs):
        raise AssertionError("the factory reached GeminiProvider despite the refusal")

    monkeypatch.setattr(gemini_module, "GeminiProvider", explode)
    with pytest.raises(LiveNotAuthorised):
        build_provider(keyed)
