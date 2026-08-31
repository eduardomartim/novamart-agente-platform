"""Input inspection.

Important framing, and the reason this module is not called "the injection
filter": prompt-injection detection is a *signal*, not a barrier. It is
expected to miss things. The platform stays safe when it does, because the
policy engine, the authorisation matrix and the tool gateway never consult
the model's intent -- they enforce on the action itself.

What this module contributes is (a) rejecting input that is malformed or
oversized, and (b) raising the risk level of actions taken while suspicious
input is in play.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Final

from ..security.pii import detect_pii
from ..security.secrets import find_secrets


@dataclass(frozen=True, slots=True)
class InputSignal:
    signal_id: str
    detail: str


@dataclass(slots=True)
class InputAssessment:
    """Structured findings for one piece of user input."""

    accepted: bool
    signals: list[InputSignal] = field(default_factory=list)
    normalized_input: str = ""
    rejection_reason: str | None = None

    @property
    def signal_ids(self) -> tuple[str, ...]:
        return tuple(s.signal_id for s in self.signals)

    @property
    def suspicious(self) -> bool:
        """True when anything injection-shaped was seen."""
        return any(s.signal_id.startswith("injection.") for s in self.signals)


#: Patterns are matched against normalised text. They cover the common shapes of
#: instruction-override attempts in English and Portuguese.
_INJECTION_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    (
        "injection.ignore_instructions",
        # Two branches, because word order differs by language. English puts the
        # qualifier first ("previous instructions"); Portuguese puts the noun
        # first ("regras anteriores"). A single ordering missed every
        # Portuguese phrasing.
        re.compile(
            r"(?i)\b(?:ignore|disregard|forget|esque[cç]a|esque[cç]am)\b"
            r"(?:"
            r"[^.\n]{0,40}\b(?:previous|prior|above|all|any|earlier)\b"
            r"[^.\n]{0,40}\b(?:instruction|prompt|rule|polic|direction)"
            r"|"
            r"[^.\n]{0,40}\b(?:instru[cç]|regra|pol[ií]tica|orienta[cç])"
            r"[^.\n]{0,40}\b(?:anterior|acima|pr[eé]via|todas)"
            r"|"
            r"[^.\n]{0,40}\b(?:todas|anterior)[^.\n]{0,40}"
            r"\b(?:instru[cç]|regra|pol[ií]tica)"
            r")"
        ),
    ),
    (
        "injection.role_override",
        re.compile(
            r"(?i)\b(?:you\s+are\s+now|from\s+now\s+on\s+you|act\s+as|pretend\s+to\s+be|"
            r"voc[eê]\s+agora\s+[eé]|aja\s+como)\b"
        ),
    ),
    (
        "injection.policy_override",
        re.compile(
            r"(?i)\b(?:disable|bypass|turn\s+off|override|skip|ignore|desative|ignore)\b"
            r"[^.\n]{0,30}\b(?:guardrail|policy|filter|safety|security|restriction|"
            r"pol[ií]tica|seguran[cç]a|restri[cç])"
        ),
    ),
    (
        "injection.system_prompt_probe",
        re.compile(
            r"(?i)\b(?:reveal|show|print|repeat|dump|what\s+(?:is|are))\b[^.\n]{0,30}"
            r"\b(?:system\s+prompt|initial\s+instruction|your\s+instruction|api\s+key|"
            r"prompt\s+do\s+sistema)"
        ),
    ),
    (
        "injection.developer_impersonation",
        re.compile(
            r"(?i)\b(?:i\s+am\s+(?:the\s+)?(?:developer|admin|administrator|owner)|"
            r"as\s+(?:the\s+)?(?:system|admin)|sou\s+o\s+(?:desenvolvedor|administrador))\b"
        ),
    ),
    (
        "injection.forged_confirmation",
        re.compile(
            r"(?i)(?:\bthe\s+user\s+(?:has\s+)?(?:already\s+)?"
            r"(?:confirmed|approved|authorized)\b"
            r"|\bconsider\s+(?:this|it)\s+(?:as\s+)?(?:confirmed|approved)\b"
            r"|\btreat\s+(?:this|it)?\s*as\s+(?:approved|confirmed|authorized)\b"
            r"|\balready\s+(?:been\s+)?(?:confirmed|approved)\b"
            r"|\bj[aá]\s+(?:foi\s+)?(?:confirmado|aprovado)\b)"
        ),
    ),
    (
        "injection.delimiter_spoofing",
        re.compile(r"(?i)(?:^|\n)\s*(?:###\s*)?(?:system|assistant)\s*[:>\]]"),
    ),
)

#: Characters used to hide instructions from a human reader while remaining
#: visible to the model.
_INVISIBLE = re.compile(r"[​-‏‪-‮⁠-⁤﻿]")


def fold_for_detection(text: str) -> str:
    """Aggressively fold text for *pattern matching only*.

    NFKC leaves combining marks in place, so a single mark inserted inside a
    keyword ("ign̄ore") breaks every pattern while remaining visually
    identical to a human reader. Decomposing and dropping the marks closes that
    evasion.

    This is deliberately **not** applied to ``normalized_input``. That is the
    text the agent actually reads, and folding it would strip legitimate
    Portuguese accents -- turning "não" into "nao" in real user content to
    defend against an attack that only concerns matching.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize(text: str) -> str:
    """Normalise input before matching.

    Unicode confusables and zero-width characters are a standard way to slip
    past naive pattern matching, so they are removed before any rule runs.
    """
    normalized = unicodedata.normalize("NFKC", text)
    normalized = _INVISIBLE.sub("", normalized)
    return normalized.replace("\r\n", "\n").replace("\r", "\n")


def assess_input(text: str, *, max_chars: int) -> InputAssessment:
    """Validate and inspect one user input."""
    if text is None or not text.strip():
        return InputAssessment(
            accepted=False, rejection_reason="input is empty", normalized_input=""
        )

    if len(text) > max_chars:
        return InputAssessment(
            accepted=False,
            rejection_reason=f"input exceeds {max_chars} characters (got {len(text)})",
            normalized_input=text[:max_chars],
        )

    normalized = normalize(text)
    # Patterns run against the folded form; everything downstream receives the
    # faithful NFKC text.
    folded = fold_for_detection(normalized)
    signals: list[InputSignal] = []

    if normalized != text:
        signals.append(
            InputSignal("input.normalized", "input contained invisible or non-canonical characters")
        )

    if folded != normalized:
        signals.append(
            InputSignal(
                "input.folded",
                "input contained combining marks that alter keyword matching",
            )
        )

    for signal_id, pattern in _INJECTION_PATTERNS:
        found = pattern.search(folded)
        if found:
            # The matched text is recorded truncated. It is attacker-controlled,
            # so it is stored only as evidence and never re-fed to a model.
            signals.append(InputSignal(signal_id, f"matched: {found.group(0)[:80]!r}"))

    for kind in find_secrets(normalized):
        signals.append(InputSignal(f"input.secret.{kind}", "credential-shaped content in input"))

    for pii_match in detect_pii(normalized):
        signals.append(
            InputSignal(
                f"input.pii.{pii_match.kind}", f"{pii_match.count} occurrence(s) in input"
            )
        )

    return InputAssessment(accepted=True, signals=signals, normalized_input=normalized)
