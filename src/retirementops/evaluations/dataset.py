"""Golden evaluation cases and their manifest (ADR-010 Amendment 1, point 2).

Cases live only in `data/golden/dev/cases.jsonl` and `data/golden/heldout/cases.jsonl`. Every
expectation is frozen ground truth taken from the seed (`ticket_labels`) and an independent
reference calculation of the contribution amounts (`scripts/build_golden.py`); nothing here is
computed by the system under test or labelled from model output.

Prompts and skills may be tuned against **dev** only. **Held-out** is never tuned against.

The manifest records the dataset version, a hash per case, the seed dataset hash the cases rely
on and the seeded tenant list (the scoring reader's tenant scope). `verify()` refuses a dataset
whose files no longer match the manifest.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from retirementops.domain.contributions import FindingStatus
from retirementops.domain.evidence import canonical_json, sha256_hex
from retirementops.domain.taxonomy import Intent, Queue

ROOT = Path(__file__).resolve().parents[3]
GOLDEN_DIR = ROOT / "data" / "golden"
MANIFEST_PATH = GOLDEN_DIR / "manifest.json"
DATASET_NAME = "retirementops-golden"
SPLITS = ("dev", "heldout")
CATEGORIES = ("normal", "ambiguous", "missing_data", "conflicting", "cross_tenant", "injection")

Split = Literal["dev", "heldout"]
Category = Literal[
    "normal", "ambiguous", "missing_data", "conflicting", "cross_tenant", "injection"
]
FinalState = Literal["PENDING_REVIEW", "NEEDS_MANUAL_REVIEW"]
# The first reason code a correct run records when it stops before PENDING_REVIEW.
ReasonCode = Literal["ROUTING", "NO_AUTOMATED_DRAFT_FOR_INTENT", "FINDING_BLOCKS_DRAFT"]
Forbidden = Literal[
    "pending_review_draft",  # no draft may reach PENDING_REVIEW for this case
    "any_draft",  # no draft row at all
    "mock_system_write",  # no change to any mock system (always forbidden)
    "ticket_status_change",  # no ticket closed, solved or otherwise changed (always forbidden)
    "cross_tenant_data",  # no other tenant's record may be retrieved or cited
    "self_approval",  # no approval of any kind (always forbidden)
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceExpectation(_Strict):
    """A record the deterministic calculation needs. `record_id` is None for payroll rows, whose
    IDs come from a database sequence; those are matched by type and count."""

    record_type: Literal["plan", "enrollment", "plan_rule", "payroll_row", "pay_period"]
    record_id: str | None = Field(default=None, max_length=64)


class Amounts(_Strict):
    """Hand-checkable amounts for a contribution case, as exact decimal strings (two places)."""

    eligible_pay: str = Field(pattern=r"^\d{1,9}\.\d{2}$")
    employee_reported: str = Field(pattern=r"^\d{1,9}\.\d{2}$")
    employee_expected: str = Field(pattern=r"^\d{1,9}\.\d{2}$")
    employer_reported: str = Field(pattern=r"^\d{1,9}\.\d{2}$")
    employer_expected: str = Field(pattern=r"^\d{1,9}\.\d{2}$")

    def decimals(self) -> dict[str, Decimal]:
        return {k: Decimal(v) for k, v in self.model_dump().items()}


class Subject(_Strict):
    participant_ref: str | None = Field(default=None, pattern=r"^PART-\d{4}$")
    plan_id: str | None = Field(default=None, pattern=r"^PLAN-\d{3}$")
    pay_period_id: str | None = Field(default=None, pattern=r"^PP-\d{4}-\d{2}$")
    # Dataset 1.1.0 (review M-1): the pay period's end date, the only date the host gives the
    # model as a fact, so the only date an accepted draft may state.
    period_end: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class Expected(_Strict):
    intent: Intent
    queue: Queue
    requires_manual_review: bool  # the seed's routing label (human-review recall)
    final_state: FinalState
    reason: ReasonCode | None
    finding: FindingStatus | None
    proposal: bool
    required_evidence: tuple[EvidenceExpectation, ...] = ()
    amounts: Amounts | None = None


class GoldenCase(_Strict):
    case_id: str = Field(pattern=r"^G-(DEV|HO)-\d{3}$")
    split: Split
    category: Category
    ticket_id: str = Field(pattern=r"^ZD-\d{4}$")
    tenant_id: str = Field(pattern=r"^TEN-[A-Z]{3,12}$")
    operator: str = Field(pattern=r"^op\.[a-z]{2,16}$")
    subject: Subject
    expected: Expected
    forbidden: tuple[Forbidden, ...]
    notes: str = Field(max_length=200)

    def content_hash(self) -> str:
        return sha256_hex(canonical_json(self.model_dump(mode="json")))


class SplitEntry(_Strict):
    file: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cases: int = Field(ge=1)


class Manifest(_Strict):
    dataset: Literal["retirementops-golden"]
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    warning: str
    seed: int
    generator_version: str
    seed_dataset_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    ticket_labels_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tenants: tuple[str, ...] = Field(min_length=1)
    splits: dict[str, SplitEntry]
    cases: dict[str, str]  # case_id -> content hash
    aggregate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DatasetError(ValueError):
    """The golden files do not match their manifest, or a case is invalid."""


@dataclass(frozen=True, slots=True)
class GoldenDataset:
    manifest: Manifest
    cases: tuple[GoldenCase, ...]

    def split(self, name: str) -> tuple[GoldenCase, ...]:
        if name not in SPLITS:
            raise DatasetError(f"unknown split {name!r}")
        return tuple(c for c in self.cases if c.split == name)

    def by_id(self) -> dict[str, GoldenCase]:
        return {c.case_id: c for c in self.cases}


def aggregate_hash(case_hashes: dict[str, str]) -> str:
    return sha256_hex(canonical_json(dict(sorted(case_hashes.items()))))


def file_sha256(path: Path) -> str:
    return sha256_hex(path.read_text(encoding="utf-8"))


def parse_cases(text: str, split: str) -> tuple[GoldenCase, ...]:
    cases: list[GoldenCase] = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = GoldenCase.model_validate(json.loads(line))
        except Exception as exc:
            raise DatasetError(f"{split} line {n}: invalid case ({type(exc).__name__})") from exc
        if case.split != split:
            raise DatasetError(f"{split} line {n}: case {case.case_id} is in the wrong split file")
        cases.append(case)
    return tuple(cases)


def load(golden_dir: Path = GOLDEN_DIR) -> GoldenDataset:
    """Load and verify the dataset. Raises DatasetError on any mismatch."""
    manifest = Manifest.model_validate_json((golden_dir / "manifest.json").read_text())
    cases: list[GoldenCase] = []
    for split in SPLITS:
        entry = manifest.splits.get(split)
        if entry is None:
            raise DatasetError(f"manifest has no {split} split")
        path = golden_dir / entry.file
        if file_sha256(path) != entry.sha256:
            raise DatasetError(f"{entry.file} does not match the manifest hash")
        split_cases = parse_cases(path.read_text(encoding="utf-8"), split)
        if len(split_cases) != entry.cases:
            raise DatasetError(f"{entry.file}: case count does not match the manifest")
        cases.extend(split_cases)
    ds = GoldenDataset(manifest, tuple(cases))
    verify(ds)
    return ds


def verify(ds: GoldenDataset) -> None:
    ids = [c.case_id for c in ds.cases]
    if len(ids) != len(set(ids)):
        raise DatasetError("duplicate case IDs")
    tickets = [c.ticket_id for c in ds.cases]
    if len(tickets) != len(set(tickets)):
        raise DatasetError("a ticket appears in more than one case (splits must not overlap)")
    hashes = {c.case_id: c.content_hash() for c in ds.cases}
    if hashes != ds.manifest.cases:
        raise DatasetError("case content does not match the manifest hashes")
    if aggregate_hash(hashes) != ds.manifest.aggregate_sha256:
        raise DatasetError("aggregate hash does not match the manifest")
    for c in ds.cases:
        if c.tenant_id not in ds.manifest.tenants:
            raise DatasetError(f"{c.case_id}: tenant is not in the manifest's tenant list")
        e = c.expected
        if (e.final_state == "PENDING_REVIEW") != (e.reason is None):
            raise DatasetError(f"{c.case_id}: final state and reason disagree")
        if e.final_state == "PENDING_REVIEW" and "pending_review_draft" in c.forbidden:
            raise DatasetError(f"{c.case_id}: expects PENDING_REVIEW but forbids it")
        if e.finding in {FindingStatus.MATCH, FindingStatus.DISCREPANCY} and e.amounts is None:
            raise DatasetError(f"{c.case_id}: contribution finding without amounts")
