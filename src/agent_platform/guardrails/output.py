"""Output inspection, applied to every response before it reaches the user."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..security.pii import redact_pii
from ..security.sanitization import redact_home_paths
from ..security.secrets import redact_secrets

#: A response is blocked outright, rather than redacted, when it contains a
#: value the platform knows to be a live credential. Redaction is the right
#: answer for a credential-shaped string of unknown origin; a confirmed leak of
#: our own key is a failure that should be loud.
BLOCKED_MESSAGE = (
    "This response was withheld because it contained credential material. "
    "The incident has been recorded in the trace."
)


@dataclass(slots=True)
class OutputAssessment:
    text: str
    blocked: bool = False
    secret_kinds: list[str] = field(default_factory=list)
    pii_kinds: list[str] = field(default_factory=list)
    truncated: bool = False

    @property
    def redacted(self) -> bool:
        return bool(self.secret_kinds or self.pii_kinds)

    @property
    def findings(self) -> tuple[str, ...]:
        return tuple(
            [f"secret:{k}" for k in self.secret_kinds] + [f"pii:{k}" for k in self.pii_kinds]
        )


def secure_output(
    text: str,
    *,
    known_secrets: tuple[str, ...] = (),
    max_chars: int = 4000,
) -> OutputAssessment:
    """Run the output security pipeline.

    Order matters: the known-credential check runs first, because if a live key
    is present the response is discarded entirely and no partial content is
    returned.
    """
    if not text:
        return OutputAssessment(text="")

    for secret in known_secrets:
        if secret and len(secret) >= 8 and secret in text:
            return OutputAssessment(
                text=BLOCKED_MESSAGE,
                blocked=True,
                secret_kinds=["configured_credential"],
            )

    result, secret_kinds = redact_secrets(text)
    result, pii_kinds = redact_pii(result)
    # Local paths are not credentials, but disclosing the operating user's name
    # and directory layout in a user-facing response is gratuitous.
    result = redact_home_paths(result)

    truncated = False
    if len(result) > max_chars:
        result = result[:max_chars] + "...[truncated]"
        truncated = True

    return OutputAssessment(
        text=result,
        blocked=False,
        secret_kinds=secret_kinds,
        pii_kinds=pii_kinds,
        truncated=truncated,
    )


def validate_against_schema(payload: object, required_fields: tuple[str, ...]) -> tuple[bool, str]:
    """Deterministic structural check for agent outputs that must be objects."""
    if not isinstance(payload, dict):
        return False, f"expected an object, got {type(payload).__name__}"
    missing = [field_name for field_name in required_fields if field_name not in payload]
    if missing:
        return False, f"missing required field(s): {', '.join(missing)}"
    return True, ""
