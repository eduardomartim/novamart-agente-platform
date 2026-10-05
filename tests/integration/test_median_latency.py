"""The headline latency, and the population it is allowed to describe.

The observability page used to lead with a mean over every request ever
recorded. On this installation that number was 7613 ms, and it was true of
nothing: the deterministic stub answers in about 124 ms, a live Gemini call in
about 14 056 ms, and the mean sat in the empty space between two populations
two orders of magnitude apart. A median over the same mixed rows would not have
fixed it -- it would only have changed which population won, and on the data as
recorded it lands on 191 ms, which is the stub's number wearing a label that
claims to cover both.

So the headline filters by mode before it computes anything, and these tests are
mostly about the filter rather than about the arithmetic. The column it filters
on is `provider`, which the platform writes from `self.provider.name`, and the
mode it compares against is `provider_info.name`, which returns that same
string. That one identity is what keeps the badge on screen and the rows behind
the number from ever describing different things, so it is asserted here
directly rather than assumed.

Nothing here calls a provider.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent_platform.persistence.memory import InMemoryRepository
from agent_platform.persistence.repository import RequestRecord


def _app():
    path = Path(__file__).resolve().parents[2] / "dashboard"
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
    import app

    return app


@dataclass
class _Info:
    name: str


@dataclass
class _Stand_in:
    """The two attributes the headline reads, and nothing else."""

    provider_info: _Info
    repository: object


def _repository(*rows: tuple[str, float]) -> InMemoryRepository:
    repository = InMemoryRepository()
    repository.initialize()
    for index, (provider, latency) in enumerate(rows):
        repository.save_request(
            RequestRecord(
                request_id=f"req-{index}",
                trace_id=f"trace-{index}",
                input_digest="d",
                status="success",
                latency_ms=latency,
                provider=provider,
            )
        )
    return repository


STUB = [("stub", value) for value in (100.0, 120.0, 140.0)]
LIVE = [("gemini", value) for value in (9000.0, 14000.0, 20000.0)]


# ==================================================================== the filter


def test_the_stub_headline_counts_only_stub_requests():
    app = _app()
    repository = _repository(*STUB, *LIVE)

    median, samples = app._median_latency_for_mode(_Stand_in(_Info("stub"), repository))

    assert median == 120.0, "a live request reached the stub's median"
    assert samples == len(STUB)


def test_the_live_headline_counts_only_gemini_requests():
    app = _app()
    repository = _repository(*STUB, *LIVE)

    median, samples = app._median_latency_for_mode(
        _Stand_in(_Info("gemini"), repository)
    )

    assert median == 14000.0, "a stub request reached the live median"
    assert samples == len(LIVE)


def test_the_two_modes_never_agree_on_the_same_data():
    """The regression this exists to catch: a filter that quietly stops filtering.

    Both numbers coming out equal would mean the mode is being ignored, which is
    exactly the mixed population the change removed.
    """
    app = _app()
    repository = _repository(*STUB, *LIVE)

    stub, _ = app._median_latency_for_mode(_Stand_in(_Info("stub"), repository))
    live, _ = app._median_latency_for_mode(_Stand_in(_Info("gemini"), repository))

    assert stub != live


# ============================================================= the honest empty


def test_a_mode_with_too_few_requests_reports_nothing():
    app = _app()
    repository = _repository(*LIVE, ("stub", 100.0), ("stub", 120.0))

    assert app._median_latency_for_mode(_Stand_in(_Info("stub"), repository)) is None


def test_it_does_not_fall_back_to_the_other_mode():
    """`None` and not "the number we do have" -- the whole point of the change."""
    app = _app()
    repository = _repository(*LIVE)

    assert app._median_latency_for_mode(_Stand_in(_Info("stub"), repository)) is None


def test_a_request_without_a_latency_is_not_counted_as_zero():
    """A NULL is a missing measurement, and averaging it in as zero invents one.

    Only SQLite can hand back a null here -- `RequestRecord.latency_ms` defaults
    to 0.0 -- so the rows are supplied directly rather than through a repository
    that cannot produce them.
    """
    app = _app()

    class _NullLatencies:
        def recent_requests(self, limit: int = 50) -> list[dict]:
            return [
                {"provider": "stub", "latency_ms": None},
                {"provider": "stub", "latency_ms": None},
                {"provider": "stub", "latency_ms": None},
                {"provider": "gemini", "latency_ms": 14000.0},
            ]

    assert (
        app._median_latency_for_mode(_Stand_in(_Info("stub"), _NullLatencies())) is None
    )


# ================================================== the identity the filter needs


@pytest.mark.slow
def test_the_mode_on_screen_is_the_string_written_to_the_column(settings, tmp_path):
    """`provider_info.name` and `requests.provider` must be one value.

    If these ever diverge the filter silently matches nothing and every mode
    reports "--" while the database is full of rows.
    """
    from dataclasses import replace

    from agent_platform.platform import AgentPlatform

    platform = AgentPlatform(replace(settings, database_path=tmp_path / "mode.db"))
    try:
        platform.run("How many customers do we have?")
        recorded = platform.repository.recent_requests(limit=1)[0]["provider"]
        assert recorded == platform.provider_info.name == "stub"
    finally:
        platform.close()


# =========================================================== the label it carries


def test_both_label_keys_resolve_in_both_languages():
    _app()
    import i18n
    import streamlit as st

    keys = {
        "obs.median_latency": {"mode": "STUB"},
        "obs.median_latency_help": {"samples": 75, "mode": "STUB"},
        "obs.median_latency_none": {"minimum": 3, "mode": "STUB"},
    }
    for locale in i18n.LOCALES:
        st.session_state[i18n.LOCALE_KEY] = locale
        for key, fields in keys.items():
            value = i18n.t(key, **fields)
            assert value and value != key, f"{key!r} unresolved in {locale!r}"
            assert "{" not in value, f"{key!r} has an unfilled field in {locale!r}"
    st.session_state.pop(i18n.LOCALE_KEY, None)


def test_the_label_names_the_mode_it_measured():
    """A number this specific must say what it is a number of."""
    _app()
    import i18n
    import streamlit as st

    for locale in i18n.LOCALES:
        st.session_state[i18n.LOCALE_KEY] = locale
        assert "STUB" in i18n.t("obs.median_latency", mode="STUB")
        assert "GEMINI" in i18n.t("obs.median_latency", mode="GEMINI")
    st.session_state.pop(i18n.LOCALE_KEY, None)


def test_the_mean_it_replaced_is_gone_from_the_page():
    """The key and the reading, not just the label."""
    source = (
        Path(__file__).resolve().parents[2] / "dashboard" / "app.py"
    ).read_text(encoding="utf-8")

    assert "obs.avg_latency" not in source
    assert "avg_latency_ms" not in source
