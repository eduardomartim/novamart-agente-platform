"""What may leave the trust boundary in a prompt.

Sending a prompt to a hosted model hands text to a third party. That is a
different question from what may be *stored*, so it gets its own control point
rather than reusing the persistence sanitiser.

Two rules, applied to every prompt regardless of provider:

**Credentials are always removed.** No task this platform performs needs a
credential, so there is never a reason for one to reach a provider. This is
unconditional.

**PII is masked only where masking cannot break the work.** Blanket-masking
would be theatre: the platform's own ``send_email`` tool needs a real recipient
address, so masking every email would simply prevent the agent from doing the
job while still having sent the surrounding text off-box. The rule applied here
is narrower and honest -- mask the categories that *no registered tool
consumes*, and leave the ones a tool legitimately acts on.

The same treatment applies to the stub provider even though it is local. A
control that only engages in one mode is a control that gets discovered broken
in the mode that matters.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..security.pii import redact_pii
from ..security.secrets import redact_known_values, redact_secrets

#: PII categories that no tool in the registry accepts as an argument.
#: Masking these cannot break a legitimate workflow.
MASKED_PII_KINDS: frozenset[str] = frozenset({"cpf", "card", "phone", "ipv4"})

#: Categories deliberately preserved, with the reason.
#:   email -> ``send_email.to`` requires a real recipient address.
PRESERVED_PII_KINDS: frozenset[str] = frozenset({"email"})


@dataclass(frozen=True, slots=True)
class EgressResult:
    """Prompt text cleared for egress, plus what was removed.

    ``secret_kinds`` and ``pii_kinds`` are *category names only*. The values
    themselves are never carried on this object, so it is safe to put straight
    into a trace.
    """

    text: str
    secret_kinds: tuple[str, ...] = ()
    pii_kinds: tuple[str, ...] = ()

    @property
    def redacted(self) -> bool:
        return bool(self.secret_kinds or self.pii_kinds)

    @property
    def metadata(self) -> dict[str, list[str] | bool]:
        """Structured summary for the trace. Contains no sensitive values."""
        return {
            "redacted": self.redacted,
            "secret_kinds": list(self.secret_kinds),
            "pii_kinds": list(self.pii_kinds),
        }


def prepare_for_egress(text: str, *, known_secrets: tuple[str, ...] = ()) -> EgressResult:
    """Clear *text* for transmission to a model provider."""
    if not text:
        return EgressResult(text=text)

    result = redact_known_values(text, known_secrets) if known_secrets else text
    result, secret_kinds = redact_secrets(result)
    result, pii_kinds = redact_pii(result, only=MASKED_PII_KINDS)

    return EgressResult(
        text=result,
        secret_kinds=tuple(secret_kinds),
        pii_kinds=tuple(pii_kinds),
    )
