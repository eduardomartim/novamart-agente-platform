"""Which language the answer is written in, for the length of one request.

The dashboard can be read in Portuguese or English. The sentences a visitor
reads are not written by the dashboard, though: the tools write them. A tool
returns a ``summary`` and ``graph._summarise_output`` renders it verbatim, so
translating only the interface would produce an English page answering in
Portuguese -- worse than not offering the choice at all.

So the locale has to reach the tools, and it cannot travel as an argument.
Tool arguments are proposed by a language model and validated against a
schema; a locale is not something the model decides, and adding it to every
signature would put a presentation concern into the contract the policy engine
checks.

A ``ContextVar`` is the mechanism already used for the execution gateway, for
the same reason: it is scoped to the dynamic extent of one call, it does not
leak across threads or tasks, and it needs no cooperation from the code in
between. The dashboard sets it around ``platform.run()``; the tools read it
when they compose their sentence.

Portuguese is the default, and deliberately so. It is what the demo shipped
with, what every existing test asserts, and what a caller who never heard of
this module gets -- including the CLI, the API and the MCP server, none of
which select a locale.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Final

#: The two languages the demo is written in. Not a general i18n framework:
#: adding a third means writing a third catalogue by hand, which is the honest
#: cost and is why the tuple is closed.
LOCALES: Final[tuple[str, ...]] = ("pt", "en")

#: What a caller who never sets a locale gets.
DEFAULT_LOCALE: Final[str] = "pt"

_LOCALE: ContextVar[str] = ContextVar("agent_platform_locale", default=DEFAULT_LOCALE)


def current_locale() -> str:
    """The locale in force, always one of :data:`LOCALES`."""
    value = _LOCALE.get()
    return value if value in LOCALES else DEFAULT_LOCALE


@contextmanager
def use_locale(locale: str) -> Iterator[str]:
    """Run a block with *locale* in force, restoring the previous one after.

    An unknown locale falls back to the default rather than raising. This is a
    presentation setting: a request answered in the wrong language is a flaw,
    but a request that fails because of one is a worse flaw, and this code sits
    on the path of every answer.
    """
    chosen = locale if locale in LOCALES else DEFAULT_LOCALE
    token = _LOCALE.set(chosen)
    try:
        yield chosen
    finally:
        _LOCALE.reset(token)


def pick(pt: str, en: str) -> str:
    """The Portuguese or the English string, whichever the locale asks for.

    Deliberately positional and deliberately two arguments. Every call site
    then shows both languages on the screen at once, which is what stops one
    of them from silently going stale when a sentence is edited.
    """
    return en if current_locale() == "en" else pt
