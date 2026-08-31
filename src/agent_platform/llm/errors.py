"""Provider failure taxonomy.

A live provider fails in more ways than "it worked" or "it threw". It can
return a 200 with empty text because the model spent its whole output budget
thinking, or because a safety filter stopped it, or because it hit the token
ceiling mid-object. Previously all of those surfaced as an unhelpful "model
output did not contain a JSON object".

Naming them lets each one be handled, traced and tested distinctly. Every error
here is a *handled failure*: callers turn them into refusals or safe fallbacks,
and none of them can result in a tool being executed.
"""

from __future__ import annotations

from .provider import LLMError


class LLMSafetyBlockedError(LLMError):
    """The provider refused to answer on safety grounds.

    The blocking category is recorded in the trace but is deliberately not
    echoed to the user: it describes the input as the provider classified it,
    and repeating that back is both unhelpful and a small information leak.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"provider blocked the response ({reason})")
        self.reason = reason


class LLMTruncatedError(LLMError):
    """The response hit the output token ceiling before completing.

    On a thinking model this usually means thinking consumed the budget. The
    fix is configuration (``GEMINI_THINKING_BUDGET=0``), not a retry, so this
    is raised rather than retried.
    """

    def __init__(self, output_tokens: int, thought_tokens: int) -> None:
        detail = f"output truncated at {output_tokens} tokens"
        if thought_tokens:
            detail += (
                f", of which {thought_tokens} were thinking tokens; "
                "consider setting GEMINI_THINKING_BUDGET=0"
            )
        super().__init__(detail)
        self.output_tokens = output_tokens
        self.thought_tokens = thought_tokens


class LLMEmptyResponseError(LLMError):
    """The provider returned no usable text for a reason we cannot classify."""


#: ``finish_reason`` values that mean the model stopped for a content-policy
#: reason rather than because it finished.
SAFETY_FINISH_REASONS: frozenset[str] = frozenset(
    {
        "SAFETY",
        "PROHIBITED_CONTENT",
        "BLOCKLIST",
        "RECITATION",
        "IMAGE_SAFETY",
        "IMAGE_PROHIBITED_CONTENT",
        "IMAGE_RECITATION",
    }
)

#: ``finish_reason`` values meaning the output was cut short.
TRUNCATION_FINISH_REASONS: frozenset[str] = frozenset({"MAX_TOKENS"})


def normalize_finish_reason(value: object) -> str:
    """Render a ``FinishReason`` enum or string as a bare uppercase name.

    The SDK may hand back an enum, a string, or ``FinishReason.STOP``-style
    repr depending on version and backend, so this is deliberately tolerant.
    """
    if value is None:
        return ""
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name.upper()
    text = str(value)
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text.strip().upper()
