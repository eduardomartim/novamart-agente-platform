"""Detection and masking of personally identifiable information.

Scope is deliberately narrow and explicit. A demo platform that claimed general
PII coverage would be overstating what regular expressions can do; what this
module offers is reliable handling of a few well-defined formats, which is
enough to demonstrate the control point without pretending to be a DLP product.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PIIMatch:
    kind: str
    count: int


_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_CPF = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
#: The lookarounds matter. Without them this pattern matches an 11-digit run
#: *inside* any longer sequence of digits, so order numbers, record ids and key
#: material were all reported as phone numbers -- and rewritten by the
#: redactor, which also made sanitisation non-idempotent.
_PHONE_BR = re.compile(r"(?<!\d)(?:\+55\s?)?\(?\d{2}\)?\s?9?\d{4}[-\s]?\d{4}(?!\d)")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_CARD_CANDIDATE = re.compile(r"\b(?:\d[ \-]?){13,19}\b")


def _luhn_valid(digits: str) -> bool:
    """Standard Luhn checksum.

    Applied before reporting a card number so that long invoice or order numbers
    are not misreported as payment data.
    """
    if not digits.isdigit() or not 13 <= len(digits) <= 19:
        return False
    total = 0
    parity = len(digits) % 2
    for index, char in enumerate(digits):
        digit = int(char)
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _mask_email(match: re.Match[str]) -> str:
    local, _, domain = match.group(0).partition("@")
    head = local[0] if local else "?"
    return f"{head}***@{domain}"


def _redact_cards(text: str) -> tuple[str, int]:
    count = 0

    def _sub(match: re.Match[str]) -> str:
        nonlocal count
        raw = match.group(0)
        digits = re.sub(r"[ \-]", "", raw)
        if not _luhn_valid(digits):
            return raw
        count += 1
        return f"[PII:card:****{digits[-4:]}]"

    return _CARD_CANDIDATE.sub(_sub, text), count


def detect_pii(text: str) -> list[PIIMatch]:
    """Report which PII categories appear in *text* and how often."""
    if not text:
        return []
    matches: list[PIIMatch] = []
    _, card_count = _redact_cards(text)
    for kind, count in (
        ("email", len(_EMAIL.findall(text))),
        ("cpf", len(_CPF.findall(text))),
        ("phone", len(_PHONE_BR.findall(text))),
        ("ipv4", len(_IPV4.findall(text))),
        ("card", card_count),
    ):
        if count:
            matches.append(PIIMatch(kind=kind, count=count))
    return matches


#: Every category this module can mask.
ALL_PII_KINDS: frozenset[str] = frozenset({"card", "cpf", "email", "phone", "ipv4"})


def redact_pii(text: str, *, only: frozenset[str] | None = None) -> tuple[str, list[str]]:
    """Mask PII in *text*, returning the masked text and the kinds masked.

    Emails keep their domain and first character: support traces stay readable
    and debuggable while the identifying part of the address is removed.

    ``only`` restricts masking to a subset of categories. This exists for the
    prompt-egress path, where masking a value a tool legitimately needs would
    break the workflow rather than protect anyone -- see ``guardrails.egress``.
    """
    if not text:
        return text, []
    selected = ALL_PII_KINDS if only is None else (ALL_PII_KINDS & only)
    kinds: list[str] = []
    result = text

    if "card" in selected:
        result, card_count = _redact_cards(result)
        if card_count:
            kinds.append("card")
    if "cpf" in selected and _CPF.search(result):
        kinds.append("cpf")
        result = _CPF.sub("[PII:cpf]", result)
    if "email" in selected and _EMAIL.search(result):
        kinds.append("email")
        result = _EMAIL.sub(_mask_email, result)
    if "phone" in selected and _PHONE_BR.search(result):
        kinds.append("phone")
        result = _PHONE_BR.sub("[PII:phone]", result)
    if "ipv4" in selected and _IPV4.search(result):
        kinds.append("ipv4")
        result = _IPV4.sub("[PII:ipv4]", result)
    return result, kinds


