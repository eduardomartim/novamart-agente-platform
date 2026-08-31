"""Authorisation to spend real provider quota.

Holding an API key is not permission to use it. That sentence is the whole
module, and it exists because the opposite was true here and cost 72 unintended
calls.

What happened
-------------
The suite kept itself offline with ``addopts = "-m 'not live'"``. A ``-m`` on
the command line **replaces** that value rather than combining with it -- pytest
stores one ``markexpr`` -- so ``pytest -m "not docker"`` silently made every
live test eligible. From there each remaining check waved the request through:

* the live modules call ``load_dotenv()`` at *import* time, so the developer's
  real key entered the process before any fixture ran;
* their ``skipif`` asks whether a key is present, which means possessing one
  **enables** the tests rather than gating them;
* the conftest guard that blocks real SDK calls exempts anything marked
  ``live`` -- by design, so it opened for exactly the dangerous case;
* the last check before the network was the provider budget, a ceiling of 400
  calls a day. It answers "how many more?", never "may you at all?", so it
  would have stopped call 401 and not call 1.

Three controls, one decision. Remove the filter and the rest cooperate.

What this is
------------
A separate fact from the key, checked at the one place every real provider is
built. It is deliberately **not** part of :class:`~agent_platform.config.Settings`
and is deliberately **not** read through ``python-dotenv``: a value that can
arrive from a ``.env`` file is a value that becomes ambient, which is precisely
how the key itself became sufficient. It is read from the process environment
and nowhere else, so it has to be supplied per invocation by someone who meant
it.

The value is a phrase rather than a flag. ``1``, ``true`` and ``yes`` are the
values that appear by accident in CI configuration, shell profiles and
half-finished scripts; a phrase does not.
"""

from __future__ import annotations

import os
from typing import Final

#: The variable that authorises real provider calls.
LIVE_ENV_VAR: Final[str] = "AGENT_PLATFORM_LIVE"

#: The one accepted value, matched exactly.
#:
#: Not a secret -- it stops accidents, not adversaries, and an operator who
#: cannot discover it cannot use the feature. It is still never written into a
#: ``.env`` file, a shell profile, a script or CI configuration, because the
#: point is that it is typed for one command and then gone. See
#: ``docs/live-verification.md``.
LIVE_AUTHORISED_VALUE: Final[str] = "i-authorise-real-provider-calls"


class LiveNotAuthorised(Exception):
    """A real provider was requested without authorisation to spend quota.

    Deliberately not an ``LLMError``. Provider errors describe a call that was
    attempted and went wrong; this describes a call that was never attempted,
    and code that handles provider failure must not swallow it as one.
    """


def live_is_authorised() -> bool:
    """Whether this process may make real provider calls.

    Read from ``os.environ`` directly on every call rather than cached at
    import: a cached answer would be decided by whatever the environment
    happened to hold when the module was first imported, which in a test run is
    a different moment from when the decision matters.
    """
    return os.environ.get(LIVE_ENV_VAR, "").strip() == LIVE_AUTHORISED_VALUE


def require_live_authorisation(action: str) -> None:
    """Raise unless this process is authorised to make real provider calls.

    ``action`` names what was about to happen, so an operator who hits this
    learns which path reached it -- building a provider, creating a client,
    rebuilding the vector index.

    The message names the variable and points at the documentation. It does not
    print the accepted value: a fix that can be copied out of a traceback is a
    fix that ends up pasted into a script, and this authorisation is worth
    exactly as much as the deliberation behind typing it. It carries no key, no
    environment dump and no configuration -- the caller learns that
    authorisation is missing and nothing about what else this process holds.
    """
    if live_is_authorised():
        return
    raise LiveNotAuthorised(
        f"refusing to {action}: real provider calls are not authorised in this "
        f"process. Holding an API key is not authorisation -- set {LIVE_ENV_VAR} "
        "for the single command that should spend quota. See "
        "docs/live-verification.md for the value and the reasoning."
    )


__all__ = [
    "LIVE_AUTHORISED_VALUE",
    "LIVE_ENV_VAR",
    "LiveNotAuthorised",
    "live_is_authorised",
    "require_live_authorisation",
]
