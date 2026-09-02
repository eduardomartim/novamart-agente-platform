"""Portuguese and English, and the three ways offering both could go wrong.

**A catalogue drifts.** Two dictionaries edited by hand will not stay in step:
a key added to one, a sentence emptied in the other. `t()` falls back rather
than raising, which keeps a page readable and makes the drift invisible -- so
it is asserted here instead of noticed later by a reader.

**The reader loses their place.** The navigation radio's key *is* the session's
page, so if the options were the translated labels, switching language would
leave the stored value absent from the new options and drop the visitor back on
the first page mid-visit. Pages are keyed by slug for exactly that reason, and
the test below is what says so.

**The interface changes language and the answer does not.** The sentence a
visitor reads is written by the tool, not by the dashboard. An English page
answering in Portuguese is the failure this whole translation exists to remove,
and it is invisible to any test that only renders the chrome -- so the answers
are asserted too, in both languages, along with the fact that the *numbers* in
them do not move.

And one that must not change: `translate="no"` stays on the document in both
languages. Offering a language of our own is precisely the argument that makes
dropping it feel safe; the crash it prevents does not care what our reasoning
was.
"""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("streamlit")

import streamlit as st
from streamlit.testing.v1 import AppTest

from agent_platform.config import Settings
from agent_platform.i18n import LOCALES as BACKEND_LOCALES
from agent_platform.i18n import use_locale
from agent_platform.platform import AgentPlatform

DASHBOARD = Path(__file__).resolve().parents[2] / "dashboard"
if str(DASHBOARD) not in sys.path:
    sys.path.insert(0, str(DASHBOARD))

APP = DASHBOARD / "app.py"

PAGE_SLUGS = (
    "overview",
    "company",
    "orchestrator",
    "security",
    "architecture",
    "observability",
)


# ============================================================ the catalogues


def test_both_catalogues_hold_the_same_keys():
    """The drift that `t()`'s fallback would otherwise hide."""
    import i18n

    missing = i18n.missing_keys()
    assert missing == {locale: () for locale in i18n.LOCALES}, missing


def test_no_catalogue_entry_is_blank():
    """A blank label is invisible on screen and reads as a layout bug."""
    import i18n

    blank = i18n.blank_keys()
    assert blank == {locale: () for locale in i18n.LOCALES}, blank


def test_every_key_resolves_in_both_languages():
    """End to end through `t()`, not just through the dictionaries.

    A key present in both catalogues can still come back as the key itself if
    the lookup is wired wrongly, and that is what a reader would see.
    """
    import i18n
    from strings_pt import STRINGS as PT

    for locale in i18n.LOCALES:
        st.session_state[i18n.LOCALE_KEY] = locale
        for key in PT:
            value = i18n.t(key)
            assert value and value != key, f"{key!r} unresolved in {locale!r}"


def test_portuguese_is_the_default():
    """What a session that never chose gets, and every existing test with it."""
    import i18n

    st.session_state.pop(i18n.LOCALE_KEY, None)
    assert i18n.current_locale() == "pt"
    assert i18n.DEFAULT_LOCALE == "pt"


def test_an_unknown_locale_falls_back_rather_than_raising():
    import i18n

    st.session_state[i18n.LOCALE_KEY] = "fr"
    assert i18n.current_locale() == "pt"
    st.session_state.pop(i18n.LOCALE_KEY, None)


def test_the_two_languages_actually_differ():
    """Guards every test above: identical catalogues would satisfy them all."""
    from strings_en import STRINGS as EN
    from strings_pt import STRINGS as PT

    differing = [k for k in PT if PT[k] != EN[k]]
    assert len(differing) > len(PT) // 2, (
        "the English catalogue is largely a copy of the Portuguese one"
    )


# ====================================================== the mirrored content


def test_translated_collections_mirror_their_originals():
    """`demo_content` serves whole collections, not single labels.

    A scenario added to one language and forgotten in the other would show a
    reader a shorter list without any error, so length and shape are asserted.
    """
    import content_en
    import demo_content

    for name in sorted(demo_content._TRANSLATED):
        pt = getattr(demo_content, f"_PT_{name}")
        en = getattr(content_en, name)
        assert type(pt) is type(en), name
        if isinstance(pt, str):
            continue
        assert len(pt) == len(en), f"{name}: {len(pt)} pt vs {len(en)} en"
        for a, b in zip(pt, en, strict=True):
            if isinstance(a, dict):
                assert set(a) == set(b), f"{name}: field names differ"
            else:
                assert len(a) == len(b), f"{name}: tuple shape differs"


def _example_pairs() -> list[tuple[str, str]]:
    """Each example question in both languages, paired."""
    import content_en
    import demo_content

    pairs: list[tuple[str, str]] = []
    for name in ("READ_ONLY_EXAMPLES", "ACTION_EXAMPLES", "SECURITY_EXAMPLES"):
        pt = getattr(demo_content, f"_PT_{name}")
        en = getattr(content_en, name)
        pairs += [(e, p) for (p, _), (e, _) in zip(pt, en, strict=True)]
    pairs += [
        (e["ask"], p["ask"])
        for p, e in zip(demo_content._PT_SCENARIOS, content_en.SCENARIOS, strict=True)
    ]
    return pairs


@pytest.mark.parametrize(("english", "portuguese"), _example_pairs())
def test_a_translated_example_demonstrates_the_same_thing(english, portuguese):
    """The question *is* the input to the router, so translating it is a
    behavioural change and not a cosmetic one.

    An example rendered in Portuguese that took a different route, reached a
    different tool, or ended in a different outcome would not be the same
    example -- it would be a second demo wearing the first one's caption. The
    Portuguese wordings were chosen against this check rather than translated
    and hoped for: three of the first drafts routed elsewhere and were
    rewritten until they matched.

    Asserted against the stub, which is what decides the route offline and what
    every visitor without a provider actually meets.
    """
    from agent_platform.llm.stub import StubProvider

    assert StubProvider._choose_route(english) == StubProvider._choose_route(portuguese), (
        f"{portuguese!r} takes a different route from {english!r}"
    )
    assert StubProvider._choose_tool(english)[0] == StubProvider._choose_tool(portuguese)[0], (
        f"{portuguese!r} reaches a different tool from {english!r}"
    )


def test_the_identifiers_inside_the_examples_are_untouched():
    """The record ids are data, not prose. A translated ORD-1001 finds nothing."""
    import re

    pattern = re.compile(r"(?:ORD|CUS|TKT)-[0-9]+")
    for english, portuguese in _example_pairs():
        assert set(pattern.findall(english)) == set(pattern.findall(portuguese)), (
            portuguese
        )


def test_demo_content_serves_the_locale_in_force():
    import demo_content
    import i18n

    st.session_state[i18n.LOCALE_KEY] = "pt"
    assert demo_content.SCENARIOS[0]["title"] == demo_content._PT_SCENARIOS[0]["title"]
    st.session_state[i18n.LOCALE_KEY] = "en"
    assert demo_content.SCENARIOS[0]["title"] != demo_content._PT_SCENARIOS[0]["title"]
    st.session_state.pop(i18n.LOCALE_KEY, None)


def test_an_unknown_attribute_still_raises():
    """The dispatch must not swallow typos into silence."""
    import demo_content

    with pytest.raises(AttributeError):
        _ = demo_content.NO_SUCH_COLLECTION


# ================================================== the answers follow the UI


@pytest.fixture(scope="module")
def platform(tmp_path_factory):
    tuned = replace(
        Settings.from_env(load_dotenv_file=False),
        database_path=tmp_path_factory.mktemp("i18n") / "i.db",
        requests_per_minute=500,
        requests_per_hour=5000,
        global_requests_per_minute=5000,
        global_requests_per_hour=50000,
    )
    instance = AgentPlatform(tuned)
    try:
        yield instance
    finally:
        instance.close()


@pytest.mark.slow
@pytest.mark.parametrize(
    "question",
    [
        "How many customers do we have?",
        "What is the most expensive product?",
        "How much have we sold in total?",
        "How many tickets are open?",
    ],
)
def test_the_same_question_answers_in_the_chosen_language(platform, question):
    """The failure this exists to remove: an English page answering in
    Portuguese."""
    answers = {}
    for locale in BACKEND_LOCALES:
        with use_locale(locale):
            answers[locale] = platform.run(question).response or ""

    assert answers["pt"] and answers["en"]
    assert answers["pt"] != answers["en"], f"{question!r} answered identically"


@pytest.mark.slow
def test_the_numbers_do_not_move_between_languages(platform):
    """Translation must not become a second source of truth.

    The words differ; the figures are the dataset's and cannot.
    """
    import re

    question = "How much have we sold in total?"
    digits = {}
    for locale in BACKEND_LOCALES:
        with use_locale(locale):
            answer = platform.run(question).response or ""
        digits[locale] = re.findall(r"\d[\d.,]*", answer)

    assert digits["pt"] == digits["en"], digits


@pytest.mark.slow
def test_a_refusal_is_refused_in_both_languages(platform):
    """The honest refusal is the sentence a visitor is most likely to meet."""
    question = "How many orders were refunded?"
    seen = {}
    for locale in BACKEND_LOCALES:
        with use_locale(locale):
            result = platform.run(question)
        assert result.status == "declined", locale
        seen[locale] = result.response or ""

    assert "não consigo" in seen["pt"].lower()
    assert "cannot answer" in seen["en"].lower()


def test_the_backend_default_is_portuguese():
    """Every caller that never chose -- the CLI, the API, the MCP server."""
    from agent_platform.i18n import DEFAULT_LOCALE, current_locale

    assert DEFAULT_LOCALE == "pt"
    assert current_locale() == "pt"


# ============================================ the page survives the switch


def _app(tmp_path, locale: str | None = None, slug: str | None = None) -> AppTest:
    """A dashboard session, optionally already on a locale and a page.

    Both are seeded through session state rather than through the widget
    proxies. `AppTest`'s radio proxy resolves `format_func` outside the script's
    session context, where `current_locale()` cannot see the chosen locale --
    so it renders the Portuguese label and then looks for it among the English
    options. Writing the state directly is what the widget would have written
    anyway: the slug.
    """
    saved = {n: os.environ.get(n) for n in ("GEMINI_API_KEY", "DATABASE_PATH")}
    os.environ["GEMINI_API_KEY"] = ""
    os.environ["DATABASE_PATH"] = str(tmp_path / "i18n.db")
    st.cache_resource.clear()
    try:
        app = AppTest.from_file(str(APP), default_timeout=180)
        if locale:
            app.session_state["locale"] = locale
        if slug:
            app.session_state["nav"] = slug
        app.run()
        return app
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@pytest.mark.slow
@pytest.mark.parametrize("slug", PAGE_SLUGS)
def test_the_page_survives_a_language_switch(slug, tmp_path):
    """The reader stays where they were.

    Keyed by label instead of slug, `nav` would hold a word absent from the new
    options and the guard in `main()` would quietly reset it to the first page.
    """
    app = _app(tmp_path, slug=slug)
    assert app.session_state["nav"] == slug

    app.session_state["locale"] = "en"
    app.run()

    assert app.session_state["nav"] == slug, (
        f"switching language moved the reader off {slug}"
    )
    assert not app.exception


@pytest.mark.slow
@pytest.mark.parametrize("locale", ["pt", "en"])
@pytest.mark.parametrize("slug", PAGE_SLUGS)
def test_every_page_renders_in_both_languages(slug, locale, tmp_path):
    app = _app(tmp_path, locale, slug)
    assert not app.exception, f"{slug} raised in {locale}"

    body = " ".join(str(getattr(e, "value", "")) for e in app.main)
    assert body.strip(), f"{slug} rendered nothing in {locale}"
    # An unresolved key reaches the page as the key itself.
    assert "nav." not in body and "overview." not in body, (
        f"{slug} shows a raw catalogue key in {locale}"
    )


@pytest.mark.slow
def test_the_navigation_options_are_slugs_not_labels(tmp_path):
    """The property the switch test depends on, asserted directly."""
    import app as dashboard_app

    _app(tmp_path)
    assert set(dashboard_app.PAGES) == set(PAGE_SLUGS)
    assert all(slug.isascii() and slug.islower() for slug in dashboard_app.PAGES)


# ================================ translation stays refused in both languages


def test_translate_no_is_unconditional():
    """The protection that offering a language of our own makes tempting to
    drop.

    A reader on the English page can still ask Chrome for a third language, and
    that is the same DOM rewrite and the same `removeChild` crash. `lang` may
    follow the locale; `translate="no"` may not.
    """
    import ast

    source = APP.read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_declare_language"
    )
    body = ast.dump(fn)

    assert "translate" in body and "'no'" in body.replace('"no"', "'no'"), (
        "the dashboard no longer refuses machine translation"
    )
    # Refused outside any conditional: no `if` may stand between the function
    # and that attribute.
    assert not [n for n in ast.walk(fn) if isinstance(n, ast.If)], (
        "translate='no' has become conditional"
    )


@pytest.mark.slow
@pytest.mark.parametrize("locale", ["pt", "en"])
def test_the_language_is_declared_in_both_locales(locale, tmp_path):
    """`lang` is set from the locale, and the script that sets it still runs."""
    app = _app(tmp_path, locale)
    assert not app.exception
    import app as dashboard_app

    assert dashboard_app.current_locale() in {"pt", "en"}
