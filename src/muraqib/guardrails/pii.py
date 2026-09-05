"""PII detection and redaction applied BEFORE any text reaches a model.

A compliance assessor that ships a client's personal data to a third-party
model API while assessing them on cross-border transfer controls is not a
credible tool. Redaction runs on every untrusted string on the outbound path.

Deliberate design notes:

* Redaction is **reversible within the process only**: each match is replaced
  with a stable placeholder and the mapping is held in memory for the run so
  findings stay readable to the human reviewer. The mapping is never written to
  the audit ledger, never logged, and never sent to a model.
* Government ID and payment-card patterns are redacted and their values are
  never retained, not even in the in-memory map.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

_NEVER_RETAIN = {"CARD", "GOV_ID", "IBAN", "SECRET"}


def _luhn(digits: str) -> bool:
    nums = [int(c) for c in digits if c.isdigit()]
    if len(nums) < 13:
        return False
    checksum, parity = 0, len(nums) % 2
    for i, n in enumerate(nums):
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        checksum += n
    return checksum % 10 == 0


_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")),
    (
        "SECRET",
        re.compile(
            r"\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,})\b"
        ),
    ),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")),
    # Written so the final character is always a digit; the old form
    # "(?:\d[ -]?){13,19}" also swallowed the separator after the last digit,
    # so "card 4111 1111 1111 1111 on file" redacted as "[CARD:..]on file".
    ("CARD", re.compile(r"\b\d(?:[ -]?\d){12,18}\b")),
    (
        "GOV_ID",
        re.compile(r"\b(?:\d{3}-\d{2}-\d{4}|\d{4}\s?\d{4}\s?\d{4}|784-?\d{4}-?\d{7}-?\d)\b"),
    ),
    (
        # The trailing guard rejects a following word character and a following
        # ".digit" (an IP address or a decimal, not a phone number) - but it must
        # allow a sentence-ending period. Rejecting "." outright let
        # "+971 50 123 4567." through unmasked, which a runtime DLP run caught.
        "PHONE",
        re.compile(
            r"(?<![\w.])\+?\d{1,3}[-.\s]?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}(?!\w|\.\d)"
        ),
    ),
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
]


@dataclass(slots=True)
class RedactionResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)
    mapping: dict[str, str] = field(default_factory=dict)  # placeholder -> original

    @property
    def redacted_any(self) -> bool:
        return bool(self.counts)

    def restore(self, text: str) -> str:
        for placeholder, original in self.mapping.items():
            text = text.replace(placeholder, original)
        return text

    def as_event(self, location: str) -> dict[str, object]:
        return {"type": "pii_redaction", "location": location, "counts": dict(self.counts)}


class PIIRedactor:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def redact(self, text: str) -> RedactionResult:
        if not self.enabled or not text:
            return RedactionResult(text=text or "")
        counts: dict[str, int] = {}
        mapping: dict[str, str] = {}
        out = text

        for kind, pattern in _RULES:

            def _sub(match: re.Match[str], kind: str = kind) -> str:
                value = match.group(0)
                if kind == "CARD":
                    digits = re.sub(r"\D", "", value)
                    if not (13 <= len(digits) <= 19 and _luhn(digits)):
                        return value
                if kind == "IP" and any(int(o) > 255 for o in value.split(".")):
                    return value
                token = hashlib.sha256(value.encode()).hexdigest()[:8]
                placeholder = f"[{kind}:{token}]"
                counts[kind] = counts.get(kind, 0) + 1
                if kind not in _NEVER_RETAIN:
                    mapping[placeholder] = value
                return placeholder

            out = pattern.sub(_sub, out)

        return RedactionResult(text=out, counts=counts, mapping=mapping)
