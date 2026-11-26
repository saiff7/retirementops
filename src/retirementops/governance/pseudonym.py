"""Keyed pseudonyms for identifiers that must never be stored or logged raw.

Participant refs (PART-nnnn), employer employee refs (E-nnnnn) and client network addresses are
replaced by HMAC-SHA256 pseudonyms. Equal inputs give equal pseudonyms under the same key, so events
can still be correlated. Anyone holding the key can recompute a pseudonym for a known ref (that is the
intended investigation path), but the raw value is never written.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping
from typing import Any

PARTICIPANT_REF = re.compile(r"PART-\d{4}")
EMPLOYEE_REF = re.compile(r"E-\d{5}")
SSN_LIKE = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
# Unformatted 9-digit runs (possible SSN/TIN). Excludes digits inside decimals or longer numbers.
NINE_DIGITS = re.compile(r"(?<![\d.])\d{9}(?![\d.])")
REDACTED_ID = "[REDACTED_ID]"


def contains_restricted(text: str) -> bool:
    """True if text contains an SSN-like value (formatted or a bare 9-digit run)."""
    return bool(SSN_LIKE.search(text) or NINE_DIGITS.search(text))


def mask_restricted(text: str) -> str:
    return NINE_DIGITS.sub(REDACTED_ID, SSN_LIKE.sub("[REDACTED_SSN]", text))


# Anything matching this must never reach durable audit storage in raw form.
RAW_IDENTIFIER = re.compile(r"PART-\d{4}|(?<![A-Za-z0-9])E-\d{5}|(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")


class Pseudonymiser:
    def __init__(self, key: bytes, *, length: int = 16) -> None:
        if len(key) < 16:
            raise ValueError("pseudonym key must be at least 16 bytes")
        self._key, self._len = key, length

    def token(self, prefix: str, value: str) -> str:
        digest = hmac.new(self._key, value.encode(), hashlib.sha256).hexdigest()
        return f"{prefix}_{digest[: self._len]}"

    def text(self, value: str) -> str:
        value = PARTICIPANT_REF.sub(lambda m: self.token("pref", m.group(0)), value)
        value = EMPLOYEE_REF.sub(lambda m: self.token("eref", m.group(0)), value)
        return SSN_LIKE.sub("[REDACTED_SSN]", value)

    def deep(self, value: Any) -> Any:
        """Pseudonymise every string inside nested mappings / sequences."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, Mapping):
            return {k: self.deep(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.deep(v) for v in value]
        return value
