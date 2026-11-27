"""Server-side draft validator (spec §4 step 10; threat model T-07, T-08). Pure: no I/O, no model.

A draft passes only if every checkable claim in its body is backed by host-fetched ground truth:
- every money token is exactly a `format_money()` value from the finding (no other spelling);
- every percentage and date token is a value from the finding or the resolved pay period;
- only the resolved ticket, participant, plan and pay period identifiers appear; employee refs and
  tenant IDs never do;
- it claims no action the system cannot take (corrected, refunded, closed, sent ...);
- it does not contradict the finding, and the finding does not block drafting.

Problems carry a code and a value-free detail, so reports are safe to store and audit. A failing
report sends the draft to NEEDS_MANUAL_REVIEW; it never blocks storing the draft for a human.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from retirementops.domain.contributions import BLOCKING_STATUSES, ContributionFinding, FindingStatus
from retirementops.domain.money import Money, format_money, format_percent
from retirementops.domain.records import PayPeriod

VALIDATOR_VERSION = "draft-validator/1"
MAX_BODY_CHARS = 10_000


class Problem(StrEnum):
    EMPTY_BODY = "EMPTY_BODY"
    BODY_TOO_LONG = "BODY_TOO_LONG"
    FINDING_BLOCKS_DRAFT = "FINDING_BLOCKS_DRAFT"
    UNSUPPORTED_AMOUNT = "UNSUPPORTED_AMOUNT"
    NONCANONICAL_AMOUNT = "NONCANONICAL_AMOUNT"
    UNSUPPORTED_PERCENT = "UNSUPPORTED_PERCENT"
    UNSUPPORTED_DATE = "UNSUPPORTED_DATE"
    NONCANONICAL_DATE = "NONCANONICAL_DATE"
    FOREIGN_PARTICIPANT = "FOREIGN_PARTICIPANT"
    EMPLOYEE_REF_PRESENT = "EMPLOYEE_REF_PRESENT"
    FOREIGN_PLAN = "FOREIGN_PLAN"
    FOREIGN_PAY_PERIOD = "FOREIGN_PAY_PERIOD"
    FOREIGN_TICKET = "FOREIGN_TICKET"
    TENANT_ID_PRESENT = "TENANT_ID_PRESENT"
    UNSUPPORTED_ACTION_CLAIM = "UNSUPPORTED_ACTION_CLAIM"
    CONTRADICTS_FINDING = "CONTRADICTS_FINDING"
    EVIDENCE_MIXED_SUBJECTS = "EVIDENCE_MIXED_SUBJECTS"
    EVIDENCE_INCOMPLETE = "EVIDENCE_INCOMPLETE"
    EVIDENCE_SUBJECT_UNRESOLVED = "EVIDENCE_SUBJECT_UNRESOLVED"
    TICKET_CONTAINS_RESTRICTED_PATTERN = "TICKET_CONTAINS_RESTRICTED_PATTERN"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: Problem
    detail: str  # never contains values from the body

    def as_reason(self) -> str:
        return f"{self.code.value}: {self.detail}"


@dataclass(frozen=True, slots=True)
class ValidationReport:
    passed: bool
    issues: tuple[ValidationIssue, ...]
    validator_version: str = VALIDATOR_VERSION

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(i.as_reason() for i in self.issues)

    def with_issues(self, extra: Iterable[ValidationIssue]) -> ValidationReport:
        issues = self.issues + tuple(extra)
        return ValidationReport(not issues, issues, self.validator_version)


@dataclass(frozen=True, slots=True)
class CitedRecord:
    """A verified evidence reference plus the subject it belongs to (from the server's own read)."""

    system: str
    record_type: str
    record_id: str
    content_hash: str
    participant_ref: str | None = None
    plan_id: str | None = None
    pay_period_id: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceSubject:
    """The single participant / plan / pay period the cited evidence is about, if it is one."""

    participant_ref: str | None
    plan_id: str | None
    pay_period_id: str | None
    issues: tuple[ValidationIssue, ...]


def evidence_subject(cited: Iterable[CitedRecord]) -> EvidenceSubject:
    """Cited records must all concern one participant, one plan and one pay period (T-08)."""
    records = list(cited)
    issues: list[ValidationIssue] = []
    picked: list[str | None] = []
    for what, values in (
        ("participant", {c.participant_ref for c in records if c.participant_ref}),
        ("plan", {c.plan_id for c in records if c.plan_id}),
        ("pay period", {c.pay_period_id for c in records if c.pay_period_id}),
    ):
        if len(values) > 1:
            issues.append(
                ValidationIssue(
                    Problem.EVIDENCE_MIXED_SUBJECTS,
                    f"evidence concerns {len(values)} different {what}s; a reply covers one",
                )
            )
            picked.append(None)
        else:
            picked.append(next(iter(values), None))
    return EvidenceSubject(picked[0], picked[1], picked[2], tuple(issues))


def evidence_completeness(
    cited: Iterable[CitedRecord], finding: ContributionFinding
) -> list[ValidationIssue]:
    """Every record the finding relied on must be cited, unchanged, so a reviewer sees all of it."""
    have = {(c.system, c.record_type, c.record_id, c.content_hash) for c in cited}
    missing = sum(
        1
        for r in finding.evidence_refs
        if (r.system, r.record_type, r.record_id, r.content_hash) not in have
    )
    if not missing:
        return []
    return [
        ValidationIssue(
            Problem.EVIDENCE_INCOMPLETE,
            f"{missing} record(s) used by the calculation are not cited",
        )
    ]


@dataclass(frozen=True, slots=True)
class GroundTruth:
    """Everything a draft may assert, built by the host from server-fetched records."""

    ticket_id: str
    participant_refs: frozenset[str]
    plan_ids: frozenset[str]
    pay_period_ids: frozenset[str]
    amounts: frozenset[str]  # format_money() strings
    percents: frozenset[str]  # format_percent() strings
    dates: frozenset[str]  # ISO dates
    finding_status: FindingStatus | None


def _money_strings(values: Iterable[Money]) -> set[str]:
    out: set[str] = set()
    for m in values:
        out.add(format_money(m))
        out.add(format_money(Money(abs(m.amount), m.currency)))  # "short by $1.00"
    return out


def build_ground_truth(
    *,
    ticket_id: str,
    finding: ContributionFinding | None = None,
    pay_periods: Iterable[PayPeriod] = (),
    participant_refs: Iterable[str] = (),
    plan_ids: Iterable[str] = (),
) -> GroundTruth:
    refs, plans, periods = set(participant_refs), set(plan_ids), set()
    money: list[Money] = []
    percents: set[str] = set()
    dates: set[str] = set()
    if finding is not None:
        refs.add(finding.participant_ref)
        plans.add(finding.plan_id)
        periods.add(finding.pay_period_id)
        dates.add(finding.determination_date)
        if finding.eligible_pay is not None:
            money.append(finding.eligible_pay)
        if finding.effective_deferral_pct is not None:
            percents.add(format_percent(finding.effective_deferral_pct))
        for c in finding.components:
            money += [c.expected, c.reported, c.variance]
    for p in pay_periods:
        periods.add(p.pay_period_id)
        dates |= {p.period_start.isoformat(), p.period_end.isoformat(), p.pay_date.isoformat()}
    return GroundTruth(
        ticket_id=ticket_id,
        participant_refs=frozenset(refs),
        plan_ids=frozenset(plans),
        pay_period_ids=frozenset(periods),
        amounts=frozenset(_money_strings(money)),
        percents=frozenset(percents),
        dates=frozenset(dates),
        finding_status=finding.status if finding is not None else None,
    )


# ---------------------------------------------------------------- token patterns
_MONEY = re.compile(r"-?\$\s?-?\d[\d,]*(?:\.\d+)?")
_MONEY_CANONICAL = re.compile(r"^-?\$\d{1,3}(?:,\d{3})*\.\d{2}$")
_CURRENCY_WORD = re.compile(r"(?i)\b\d[\d,]*(?:\.\d+)?\s*(?:usd|dollars?|cents?)\b")
_PERCENT = re.compile(r"(?<![\w.])\d+(?:\.\d+)?\s?%")
_BARE_DECIMAL = re.compile(r"(?<![\w.,])\d[\d,]*\.\d+(?![\w.])")
_ISO_DATE = re.compile(r"(?<![\w-])\d{4}-\d{2}-\d{2}(?![\w-])")
_MONTHS = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
_TEXT_DATE = re.compile(
    rf"(?i)\b(?:{_MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?\b|\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})\b"
)
_SLASH_DATE = re.compile(r"(?<![\w/])\d{1,2}/\d{1,2}/\d{2,4}(?![\w/])")
_PARTICIPANT = re.compile(r"PART-\d{4}")
_EMPLOYEE = re.compile(r"(?<![A-Za-z0-9])E-\d{5}")
_PLAN = re.compile(r"PLAN-\d{3}")
_PERIOD = re.compile(r"PP-\d{4}-\d{2}")
_TICKET = re.compile(r"ZD-\d{4,6}")
_TENANT = re.compile(r"TEN-[A-Z0-9]{2,12}")

_DONE = (
    r"corrected|updated|changed|adjusted|fixed|refunded|reversed|processed|deposited|credited"
    r"|reassigned|closed|solved|resolved|approved|sent|submitted|amended"
)
_ACTION_CLAIMS = (
    re.compile(rf"(?i)\b(?:we|i)\s*(?:have|'ve|had)?\s+(?:already\s+)?(?:{_DONE})\b"),
    re.compile(rf"(?i)\b(?:has|have|had)\s+(?:now\s+|already\s+)?been\s+(?:{_DONE})\b"),
    re.compile(
        r"(?i)\b(?:ticket|case|request|issue|contribution|amount|payroll|record|account|deferral"
        rf"|enrollment|match)s?\s+(?:was|were|(?:is|are)\s+now)\s+(?:{_DONE})\b"
    ),
    re.compile(
        r"(?i)\b(?:refund|credit|deposit|payment|correction|adjustment|true-up)s?\s+"
        r"(?:has been|have been|was|were|will be|is being)\s+"
        r"(?:issued|made|sent|processed|applied|scheduled|posted)\b"
    ),
    re.compile(r"(?i)\bwe\s+will\s+(?:correct|refund|adjust|change|update|credit|deposit)\b"),
)
_SAYS_NO_DISCREPANCY = re.compile(
    r"(?i)\bno\s+(?:discrepancy|variance|difference|error)\b"
    r"|\b(?:amounts?|contributions?|payroll)\s+(?:match|matches|matched|(?:is|are|was|were)\s+correct)\b"
)
_SAYS_DISCREPANCY = re.compile(
    r"(?i)\b(?:we\s+found|there\s+(?:is|was)|we\s+identified|identified)\s+an?\s+"
    r"(?:discrepancy|variance|difference|shortfall|overpayment|underpayment|error)\b"
)


def _blank(text: str, pattern: re.Pattern[str]) -> tuple[str, list[str]]:
    """Return text with every match replaced by spaces (so later scans skip it), plus the matches."""
    found = [m.group(0) for m in pattern.finditer(text)]
    return pattern.sub(lambda m: " " * len(m.group(0)), text), found


def _ids(
    issues: list[ValidationIssue],
    body: str,
    pattern: re.Pattern[str],
    allowed: frozenset[str],
    code: Problem,
    what: str,
) -> None:
    bad = sum(1 for m in pattern.finditer(body) if m.group(0) not in allowed)
    if bad:
        issues.append(ValidationIssue(code, f"{bad} {what} reference(s) outside the resolved set"))


def validate_draft(body: str, truth: GroundTruth) -> ValidationReport:
    if not isinstance(body, str):
        raise TypeError("body must be a string")
    issues: list[ValidationIssue] = []

    def add(code: Problem, detail: str) -> None:
        issues.append(ValidationIssue(code, detail))

    if not body.strip():
        add(Problem.EMPTY_BODY, "draft body is empty")
    if len(body) > MAX_BODY_CHARS:
        add(Problem.BODY_TOO_LONG, f"draft body exceeds {MAX_BODY_CHARS} characters")
    if truth.finding_status is not None and truth.finding_status in BLOCKING_STATUSES:
        add(Problem.FINDING_BLOCKS_DRAFT, f"finding {truth.finding_status.value} needs a human")

    # Identifiers (scanned on the original body).
    _ids(
        issues,
        body,
        _PARTICIPANT,
        truth.participant_refs,
        Problem.FOREIGN_PARTICIPANT,
        "participant",
    )
    _ids(issues, body, _PLAN, truth.plan_ids, Problem.FOREIGN_PLAN, "plan")
    _ids(issues, body, _PERIOD, truth.pay_period_ids, Problem.FOREIGN_PAY_PERIOD, "pay period")
    _ids(issues, body, _TICKET, frozenset({truth.ticket_id}), Problem.FOREIGN_TICKET, "ticket")
    if _EMPLOYEE.search(body):
        add(Problem.EMPLOYEE_REF_PRESENT, "employer employee references must not appear in a reply")
    if _TENANT.search(body):
        add(Problem.TENANT_ID_PRESENT, "internal tenant IDs must not appear in a reply")

    # Numbers: blank out identifiers and dates first so their digits are not read as amounts.
    rest = body
    for pat in (_PARTICIPANT, _EMPLOYEE, _PLAN, _PERIOD, _TICKET, _TENANT):
        rest, _ = _blank(rest, pat)
    rest, iso_dates = _blank(rest, _ISO_DATE)
    unsupported_dates = sum(1 for d in iso_dates if d not in truth.dates)
    if unsupported_dates:
        add(Problem.UNSUPPORTED_DATE, f"{unsupported_dates} date(s) not in the evidence")
    rest, text_dates = _blank(rest, _TEXT_DATE)
    rest, slash_dates = _blank(rest, _SLASH_DATE)
    if text_dates or slash_dates:
        add(Problem.NONCANONICAL_DATE, "dates must be written as YYYY-MM-DD from the evidence")

    rest, money = _blank(rest, _MONEY)
    noncanonical = [m for m in money if not _MONEY_CANONICAL.match(m)]
    unsupported = [m for m in money if _MONEY_CANONICAL.match(m) and m not in truth.amounts]
    rest, words = _blank(rest, _CURRENCY_WORD)
    rest, pct = _blank(rest, _PERCENT)
    rest, bare = _blank(rest, _BARE_DECIMAL)
    if noncanonical or words or bare:
        n = len(noncanonical) + len(words) + len(bare)
        add(
            Problem.NONCANONICAL_AMOUNT, f"{n} amount(s) not written in the standard $1,234.56 form"
        )
    if unsupported:
        add(Problem.UNSUPPORTED_AMOUNT, f"{len(unsupported)} amount(s) not produced by the finding")
    bad_pct = sum(1 for p in pct if p.replace(" ", "") not in truth.percents)
    if bad_pct:
        add(Problem.UNSUPPORTED_PERCENT, f"{bad_pct} percentage(s) not produced by the finding")

    claims = sum(1 for pat in _ACTION_CLAIMS if pat.search(body))
    if claims:
        add(
            Problem.UNSUPPORTED_ACTION_CLAIM,
            "the draft claims or promises an action the system cannot take",
        )
    if truth.finding_status is FindingStatus.DISCREPANCY and _SAYS_NO_DISCREPANCY.search(body):
        add(
            Problem.CONTRADICTS_FINDING, "the finding is a discrepancy but the draft says it is not"
        )
    if truth.finding_status is FindingStatus.MATCH and _SAYS_DISCREPANCY.search(body):
        add(
            Problem.CONTRADICTS_FINDING,
            "the finding is a match but the draft reports a discrepancy",
        )

    return ValidationReport(not issues, tuple(issues))
