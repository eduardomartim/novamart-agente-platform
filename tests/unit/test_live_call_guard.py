"""The guard that keeps offline tests offline must itself be verified.

A safeguard nobody checks is indistinguishable from no safeguard. These assert
that the autouse fixture in conftest is actually armed during an ordinary test
run, and that it names the problem clearly when it fires.
"""

from __future__ import annotations

import pytest


def test_the_guard_is_armed_for_offline_tests():
    """Reaching the real SDK entry point must fail, loudly."""
    from google.genai import models as genai_models

    with pytest.raises(AssertionError) as caught:
        genai_models.Models.generate_content(
            object(), model="gemini-3.5-flash-lite", contents="ping", config=None
        )

    message = str(caught.value)
    assert "real Gemini API call" in message
    assert "stub" in message, "the failure should say how to fix it"


def test_the_guard_does_not_block_the_faked_client():
    """The provider unit tests substitute a fake; that must keep working."""
    from types import SimpleNamespace

    class FakeModels:
        def generate_content(self, **kwargs):
            return SimpleNamespace(text="ok", candidates=[], usage_metadata=None)

    # A fake never touches the guarded method, so it is unaffected.
    assert FakeModels().generate_content(model="x", contents="y", config=None).text == "ok"
