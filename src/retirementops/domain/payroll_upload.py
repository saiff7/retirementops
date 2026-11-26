"""Strict validation of synthetic payroll CSV uploads (data-classification.md §Controls 4).

Any problem rejects the WHOLE file and no rows are kept. Rejection details name the line and column
only and never echo cell values, because a rejected value may be restricted data.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from enum import StrEnum

from retirementops.domain.money import Money, parse_money

ALLOWED_HEADER = (
    "participant_ref",
    "pay_period_id",
    "eligible_pay",
    "employee_contribution",
    "employer_contribution",
)
MAX_BYTES = 1_000_000
MAX_ROWS = 5_000

# Header names that suggest restricted or prohibited fields.
_RESTRICTED_HEADER = re.compile(
    r"ssn|social|tax|tin\b|itin|bank|routing|account|iban|swift|dob|birth|name|address|email|phone",
    re.IGNORECASE,
)
# Value patterns that look like government IDs or bank account numbers, anywhere in the file.
_SSN_FORMATTED = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
_BARE_9_TO_17_DIGITS = re.compile(r"(?<![\d.])\d{9,17}(?![\d.])")
_PARTICIPANT = re.compile(r"^PART-\d{4}$")
_PAY_PERIOD = re.compile(r"^PP-\d{4}-\d{2}$")


class RejectionReason(StrEnum):
    TOO_LARGE = "TOO_LARGE"
    NOT_UTF8 = "NOT_UTF8"
    EMPTY = "EMPTY"
    RESTRICTED_FIELD = "RESTRICTED_FIELD"
    RESTRICTED_VALUE_PATTERN = "RESTRICTED_VALUE_PATTERN"
    BAD_HEADER = "BAD_HEADER"
    TOO_MANY_ROWS = "TOO_MANY_ROWS"
    INVALID_ROW = "INVALID_ROW"
    WRONG_PAY_PERIOD = "WRONG_PAY_PERIOD"
    DUPLICATE_PARTICIPANT = "DUPLICATE_PARTICIPANT"


@dataclass(frozen=True, slots=True)
class ParsedPayrollRow:
    participant_ref: str
    pay_period_id: str
    eligible_pay: Money
    employee_contribution: Money
    employer_contribution: Money


@dataclass(frozen=True, slots=True)
class UploadAccepted:
    sha256: str
    row_count: int
    rows: tuple[ParsedPayrollRow, ...]


@dataclass(frozen=True, slots=True)
class UploadRejected:
    sha256: str
    row_count: int
    reason: RejectionReason
    detail: str  # location only, never values


def validate_payroll_csv(
    data: bytes, *, expected_pay_period_id: str
) -> UploadAccepted | UploadRejected:
    digest = hashlib.sha256(data).hexdigest()

    def reject(reason: RejectionReason, detail: str, rows: int = 0) -> UploadRejected:
        return UploadRejected(sha256=digest, row_count=rows, reason=reason, detail=detail)

    if len(data) > MAX_BYTES:
        return reject(RejectionReason.TOO_LARGE, f"file exceeds {MAX_BYTES} bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return reject(RejectionReason.NOT_UTF8, "file is not UTF-8")

    records = list(csv.reader(io.StringIO(text, newline="")))
    records = [r for r in records if any(cell.strip() for cell in r)]
    if not records:
        return reject(RejectionReason.EMPTY, "file has no header")
    header, body = [h.strip() for h in records[0]], records[1:]
    n = len(body)

    for col, name in enumerate(header):
        if name not in ALLOWED_HEADER and _RESTRICTED_HEADER.search(name):
            return reject(RejectionReason.RESTRICTED_FIELD, f"header column {col + 1}", n)
    for line_no, record in enumerate(records, start=1):
        for col, cell in enumerate(record):
            if _SSN_FORMATTED.search(cell) or _BARE_9_TO_17_DIGITS.search(cell):
                return reject(
                    RejectionReason.RESTRICTED_VALUE_PATTERN, f"line {line_no}, column {col + 1}", n
                )
    if tuple(header) != ALLOWED_HEADER:
        return reject(
            RejectionReason.BAD_HEADER, "header must be exactly: " + ",".join(ALLOWED_HEADER), n
        )
    if n > MAX_ROWS:
        return reject(RejectionReason.TOO_MANY_ROWS, f"more than {MAX_ROWS} rows", n)

    parsed: list[ParsedPayrollRow] = []
    seen: set[str] = set()
    for line_no, record in enumerate(body, start=2):
        if len(record) != len(ALLOWED_HEADER):
            return reject(RejectionReason.INVALID_ROW, f"line {line_no}: wrong column count", n)
        ref, period, eligible, ee, er = (c.strip() for c in record)
        if not _PARTICIPANT.match(ref):
            return reject(RejectionReason.INVALID_ROW, f"line {line_no}, column 1", n)
        if not _PAY_PERIOD.match(period):
            return reject(RejectionReason.INVALID_ROW, f"line {line_no}, column 2", n)
        if period != expected_pay_period_id:
            return reject(RejectionReason.WRONG_PAY_PERIOD, f"line {line_no}, column 2", n)
        if ref in seen:
            return reject(RejectionReason.DUPLICATE_PARTICIPANT, f"line {line_no}, column 1", n)
        seen.add(ref)
        amounts: list[Money] = []
        for col, cell in enumerate((eligible, ee, er), start=3):
            try:
                m = parse_money(cell)
            except ValueError:
                return reject(RejectionReason.INVALID_ROW, f"line {line_no}, column {col}", n)
            if m.amount < 0:
                return reject(RejectionReason.INVALID_ROW, f"line {line_no}, column {col}", n)
            amounts.append(m)
        parsed.append(ParsedPayrollRow(ref, period, amounts[0], amounts[1], amounts[2]))
    return UploadAccepted(sha256=digest, row_count=n, rows=tuple(parsed))
