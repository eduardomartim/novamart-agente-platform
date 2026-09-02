"""The dashboard's own language, and the one it asks the platform to answer in.

Two catalogues, one lookup, no dependency. `gettext` and `babel` both want a
compile step and a binary artefact in the tree; this is 200 strings in two
languages for a demonstration, and a dict is the honest size of the problem.

Three rules hold this together, and each of them exists because breaking it
produces a specific failure:

**Portuguese is the default.** A caller who never picks -- including every
existing test -- gets exactly what shipped before this module existed.

**A missing key returns the key, never an empty string.** A blank label is
invisible in a screenshot and looks like a layout bug; a visible `nav.company`
is obviously a missing translation. The parity test in
`tests/integration/test_i18n.py` is what stops either from reaching a reader,
but the fallback decides which failure you get if it ever slips through.

**A missing *translation* falls back to Portuguese, not to the key.** An
English catalogue that loses one entry should show one Portuguese sentence in
an otherwise English page, which is legible, rather than a bare identifier.
"""

from __future__ import annotations

from typing import Any, Final

import streamlit as st
from strings_en import STRINGS as _EN
from strings_pt import STRINGS as _PT

#: The two languages, and the order the selector shows them in.
LOCALES: Final[tuple[str, ...]] = ("pt", "en")

#: What a reader who has chosen nothing sees.
DEFAULT_LOCALE: Final[str] = "pt"

#: The session-state key the selector writes and `current_locale` reads. It is
#: also the widget's key, so the widget *is* the state -- the same arrangement
#: the navigation radio uses, and for the same reason: a separate copy is a
#: second thing that can disagree.
LOCALE_KEY: Final[str] = "locale"

#: Labels for the selector itself, which cannot be translated by `t()` without
#: circularity -- and should not be: a reader looking for their own language
#: needs to find it written in that language.
LOCALE_LABELS: Final[dict[str, str]] = {"pt": "Português", "en": "English"}

_CATALOGUES: Final[dict[str, dict[str, str]]] = {"pt": _PT, "en": _EN}


def current_locale() -> str:
    """The locale in force for this session, always one of :data:`LOCALES`.

    Reads session state directly rather than caching, because the selector can
    change it between two calls inside one rerun.
    """
    try:
        chosen = st.session_state.get(LOCALE_KEY, DEFAULT_LOCALE)
    except Exception:  # pragma: no cover - outside a session context
        return DEFAULT_LOCALE
    return chosen if chosen in LOCALES else DEFAULT_LOCALE


def t(key: str, **fields: Any) -> str:
    """The string for *key* in the current locale, with ``{}`` fields filled.

    Formatting failures return the unformatted string rather than raising: a
    label with a stray brace is a typo, and a typo must not take the page down
    with it.
    """
    catalogue = _CATALOGUES.get(current_locale(), _PT)
    text = catalogue.get(key) or _PT.get(key) or key
    if not fields:
        return text
    try:
        return text.format(**fields)
    except (KeyError, IndexError, ValueError):
        return text


def missing_keys() -> dict[str, tuple[str, ...]]:
    """Which keys each catalogue lacks, relative to the union of both.

    Exposed so a test can assert emptiness rather than reimplementing the
    comparison, and so the answer comes from the same lookup `t()` uses.
    """
    everything = set(_PT) | set(_EN)
    return {
        locale: tuple(sorted(everything - set(catalogue)))
        for locale, catalogue in _CATALOGUES.items()
    }


def blank_keys() -> dict[str, tuple[str, ...]]:
    """Keys present but empty, which `t()` would silently fall through."""
    return {
        locale: tuple(sorted(k for k, v in catalogue.items() if not str(v).strip()))
        for locale, catalogue in _CATALOGUES.items()
    }
