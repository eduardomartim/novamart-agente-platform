#!/usr/bin/env python
"""Assert that this process cannot make real provider calls.

Run at the top of every CI job that executes tests. It is cheap, and it turns
"CI has no provider credentials" from a claim about how the workflow was
written into something checked at the moment it matters.

A workflow file can be read and believed. An organisation-wide secret injected
into every job cannot be read from the workflow file at all -- which is exactly
the case this exists to catch.

Exit 0 when the environment is safe, 1 otherwise. No value is ever printed:
the report names variables, never contents.
"""

from __future__ import annotations

import os
import sys

# Credentials for any provider this project could plausibly be pointed at. The
# platform only speaks to Gemini today; the rest are here because a key that
# should not be in CI should not be in CI whatever its vendor.
PROVIDER_VARIABLES = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "COHERE_API_KEY",
    "MISTRAL_API_KEY",
    "HUGGINGFACEHUB_API_TOKEN",
)

AUTHORISATION_VARIABLE = "AGENT_PLATFORM_LIVE"


def main() -> int:
    problems: list[str] = []

    for name in PROVIDER_VARIABLES:
        if os.environ.get(name, "").strip():
            problems.append(f"{name} is set; CI must not hold a provider credential")

    if os.environ.get(AUTHORISATION_VARIABLE, "").strip():
        problems.append(
            f"{AUTHORISATION_VARIABLE} is set; live calls must never be authorised "
            "in an automated run"
        )

    # And the platform's own answer, which is the one that actually governs
    # behaviour. Checking the variables above without checking this would
    # verify the inputs and not the decision.
    sys.path.insert(0, "src")
    try:
        from agent_platform.llm.authorization import live_is_authorised
    except ImportError as exc:  # pragma: no cover - a broken checkout
        print(f"could not import the authorisation module: {type(exc).__name__}")
        return 1

    if live_is_authorised():
        problems.append("the platform reports that live calls ARE authorised")

    if problems:
        print("REFUSING TO CONTINUE:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("ok: no provider credential, and live calls are not authorised")
    print(f"    checked {len(PROVIDER_VARIABLES)} provider variables "
          f"and {AUTHORISATION_VARIABLE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
