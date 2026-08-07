from __future__ import annotations

import re

_ESCALATION_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\btalk to (a )?(human|person|agent|someone)\b",
        r"\bspeak (to|with) (a )?(human|person|agent|someone)\b",
        r"\bcustomer service\b",
        r"\bhuman support\b",
        r"\breal (person|human)\b",
        r"\b(payment|billing) dispute\b",
        r"\bcharged (twice|wrong|incorrectly)\b",
        r"\b(unauthorized|fraudulent) (charge|transaction|payment)\b",
        r"\bscam(med)?\b",
        r"\bfraud\b",
        r"\baccount (is |was )?(blocked|banned|suspended|locked)\b",
        r"\bcan'?t (log ?in|sign ?in|access my account)\b",
        r"\bdamaged (item|product|package)\b",
        r"\bwrong item\b",
        r"\bnever (arrived|received|got it)\b",
        r"\brefund (denied|rejected|not received)\b",
        r"\bthis (isn'?t|is not) helping\b",
        r"\bnot helpful\b",
        r"\bstill (not|didn'?t) (work|working|resolved)\b",
        r"\bfrustrat(ed|ing)\b",
        r"\bcomplaint\b",
        r"\blegal action\b",
        r"\breport (this|you|odos)\b",
    )
]


def detect_escalation(user_message: str, reply_text: str = "") -> bool:
    """Independent, deterministic safety net for escalating to human support.

    Does not depend on the LLM correctly setting escalated_to_support in its own
    structured output — a smaller/free-tier model can miss this, and the plain-text
    streamed reply path doesn't ask the model for it at all. Callers should OR this
    with any LLM-reported signal rather than replace it, so the LLM can still
    escalate for reasons this keyword list doesn't cover.
    """
    combined = f"{user_message}\n{reply_text}"
    return any(pattern.search(combined) for pattern in _ESCALATION_PATTERNS)
