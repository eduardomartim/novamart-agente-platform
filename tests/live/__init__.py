"""Live provider tests.

Every test here is marked ``live``. Two independent things must be true before
any of them makes a real call:

1. **Authorisation.** ``AGENT_PLATFORM_LIVE`` must carry the exact value
   documented in ``docs/live-verification.md``. Without it the collection gate
   in ``tests/conftest.py`` fails the run, and -- more importantly -- the
   structural barrier in ``agent_platform.llm.authorization`` refuses to build
   a real provider at all. That barrier sits below pytest, so no marker
   expression and no command-line flag reaches it.

2. **A key.** ``GEMINI_API_KEY`` selects the real provider. It does **not**
   authorise using it; those were the same fact until it cost 72 unintended
   calls.

Authorised, with a key::

    AGENT_PLATFORM_LIVE=<value> pytest -m live

Authorised, without a key: the tests skip. Unauthorised: the run fails loudly
and nothing executes.
"""
