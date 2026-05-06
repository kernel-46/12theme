"""PII redaction at the call edge.

We redact phone numbers, Aadhaar-like 12-digit blocks, email addresses,
and obvious bank-account-like sequences BEFORE the transcript is logged
or passed to downstream LLMs. This is the privacy gate the spec requires.
"""
import re
from typing import Tuple

# Indian mobile: 10 digits, optionally with +91 / 91 / 0 prefix.
_PHONE_RE = re.compile(r"\b(?:\+?91[\-\s]?|0)?[6-9]\d{9}\b")
# Aadhaar: 12 digits, often spaced 4-4-4
_AADHAAR_RE = re.compile(r"\b(\d{4}[\-\s]?\d{4}[\-\s]?\d{4})\b")
_EMAIL_RE = re.compile(r"\b[\w.\-]+@[\w\-]+\.[\w.\-]+\b")
_LONG_DIGIT_RE = re.compile(r"\b\d{9,18}\b")


def redact(text: str) -> Tuple[str, int]:
    """Return (redacted_text, count_redacted)."""
    if not text:
        return text, 0
    count = 0

    def _sub(pattern, replacement, s):
        nonlocal count
        new, n = pattern.subn(replacement, s)
        count += n
        return new

    out = _sub(_AADHAAR_RE, "[REDACTED_AADHAAR]", text)
    out = _sub(_PHONE_RE, "[REDACTED_PHONE]", out)
    out = _sub(_EMAIL_RE, "[REDACTED_EMAIL]", out)
    out = _sub(_LONG_DIGIT_RE, "[REDACTED_NUM]", out)
    return out, count
