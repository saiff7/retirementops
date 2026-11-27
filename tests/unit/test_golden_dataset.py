"""M4 Stage 1: the golden dataset is strict, versioned, tamper-evident and agrees with the seed."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest
from pydantic import ValidationError

from retirementops.domain.contributions import (
    expected_employee_contribution,
    expected_employer_match,
)
from retirementops.domain.money import Money
from retirementops.evaluations import dataset as gd
from retirementops.seed.generator import generate

ROOT = Path(__file__).resolve().parents[2]
# Pin: a case cannot change without bumping the dataset version (and adding its pin here).
PINNED_AGGREGATES = {
    "1.0.0": "8a6c6e8d5295d208c6c47f6c35a5458e0f8410db36a0f2f6b3f0f179da2e5f1c",
}


def _builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_golden", ROOT / "scripts/build_golden.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def golden() -> gd.GoldenDataset:
    return gd.load()


@pytest.fixture(scope="module")
def seed():  # type: ignore[no-untyped-def]
    return generate(42)


def test_dataset_loads_and_verifies(golden: gd.GoldenDataset) -> None:
    assert len(golden.cases) == 30
    assert len(golden.split("dev")) == 20 and len(golden.split("heldout")) == 10


def test_committed_files_match_the_builder() -> None:
    files = _builder().build()
    for rel, text in files.items():
        assert (gd.GOLDEN_DIR / rel).read_text() == text, rel


def test_version_is_pinned_to_content(golden: gd.GoldenDataset) -> None:
    m = golden.manifest
    assert PINNED_AGGREGATES.get(m.version) == m.aggregate_sha256, (
        "golden cases changed: bump DATASET_VERSION in scripts/build_golden.py and pin it here"
    )


def test_splits_do_not_overlap(golden: gd.GoldenDataset) -> None:
    dev = {c.ticket_id for c in golden.split("dev")}
    held = {c.ticket_id for c in golden.split("heldout")}
    assert dev and held and not dev & held


def test_every_category_is_covered_in_both_splits(golden: gd.GoldenDataset) -> None:
    for split in gd.SPLITS:
        assert {c.category for c in golden.split(split)} == set(gd.CATEGORIES), split


def test_cases_agree_with_the_seed_labels(golden: gd.GoldenDataset) -> None:
    labels = {
        lab["ticket_id"]: lab
        for lab in map(json.loads, (ROOT / "data/synthetic/ticket_labels.jsonl").open())
    }
    for c in golden.cases:
        lab = labels[c.ticket_id]
        e = c.expected
        assert e.intent.value == lab["intent"], c.case_id
        assert e.queue.value == lab["expected_queue"], c.case_id
        assert e.requires_manual_review is lab["requires_manual_review"], c.case_id
        assert c.tenant_id == lab["tenant_id"], c.case_id
        assert (c.subject.participant_ref, c.subject.plan_id, c.subject.pay_period_id) == (
            lab["participant_ref"],
            lab["plan_id"],
            lab["pay_period_id"],
        )
        if e.finding is not None:
            assert e.finding.value == lab["expected_finding"], c.case_id
        if e.final_state == "PENDING_REVIEW":
            assert lab["intent"] == "CONTRIBUTION_DISCREPANCY" and not lab["requires_manual_review"]


def test_manifest_matches_the_seed(golden: gd.GoldenDataset, seed) -> None:  # type: ignore[no-untyped-def]
    m, sm = golden.manifest, json.loads((ROOT / "data/synthetic/manifest.json").read_text())
    assert m.seed == sm["seed"] == 42
    assert m.seed_dataset_hash == sm["dataset_hash"]
    assert m.ticket_labels_hash == sm["ticket_labels_hash"]
    assert list(m.tenants) == sorted(t["tenant_id"] for t in seed.tables["planadmin_mock.tenants"])
    assert set(m.tenants) == {"TEN-ALDER", "TEN-BIRCH", "TEN-COBALT"}


def test_reference_amounts_agree_with_the_domain_calculation(
    golden: gd.GoldenDataset,
    seed,  # type: ignore[no-untyped-def]
) -> None:
    """Two independent implementations (builder reference vs domain/contributions.py) agree."""
    t = seed.tables
    checked = 0
    for c in golden.cases:
        a = c.expected.amounts
        if a is None:
            continue
        s = c.subject
        enr = next(
            e
            for e in t["planadmin_mock.enrollments"]
            if e["enrollment_id"]
            in {r.record_id for r in c.expected.required_evidence if r.record_type == "enrollment"}
        )
        rules = {
            r["contribution_type"]: r
            for r in t["planadmin_mock.plan_rules"]
            if r["rule_id"]
            in {x.record_id for x in c.expected.required_evidence if x.record_type == "plan_rule"}
        }
        assert s.participant_ref == enr["participant_ref"]
        pay = Money(Decimal(a.eligible_pay))
        ee = expected_employee_contribution(
            pay, enr["deferral_pct"], rules["EMPLOYEE_DEFERRAL"]["max_deferral_pct"]
        )
        er = expected_employer_match(
            pay,
            enr["deferral_pct"],
            rules["EMPLOYEE_DEFERRAL"]["max_deferral_pct"],
            rules["EMPLOYER_MATCH"]["match_rate"],
            rules["EMPLOYER_MATCH"]["match_cap_pct"],
        )
        assert ee.amount == Decimal(a.employee_expected), c.case_id
        assert er.amount == Decimal(a.employer_expected), c.case_id
        checked += 1
    assert checked == 5  # every clean contribution case in the set


def test_one_cent_case_is_present(golden: gd.GoldenDataset) -> None:
    one_cent = [
        c
        for c in golden.cases
        if c.expected.amounts
        and abs(
            Decimal(c.expected.amounts.employee_reported)
            - Decimal(c.expected.amounts.employee_expected)
        )
        == Decimal("0.01")
    ]
    assert one_cent and all(
        c.expected.finding and c.expected.finding.value == "DISCREPANCY" for c in one_cent
    )


def test_schema_rejects_extra_fields_and_bad_values(golden: gd.GoldenDataset) -> None:
    raw = golden.cases[0].model_dump(mode="json")
    with pytest.raises(ValidationError):
        gd.GoldenCase.model_validate({**raw, "unexpected": 1})
    with pytest.raises(ValidationError):
        gd.GoldenCase.model_validate({**raw, "split": "train"})
    with pytest.raises(ValidationError):
        gd.GoldenCase.model_validate(
            {**raw, "expected": {**raw["expected"], "intent": "SOMETHING_ELSE"}}
        )
    with pytest.raises(ValidationError):
        gd.GoldenCase.model_validate({**raw, "notes": "x" * 201})


def test_tampered_case_is_detected(tmp_path: Path) -> None:
    copy = tmp_path / "golden"
    shutil.copytree(gd.GOLDEN_DIR, copy)
    path = copy / "dev/cases.jsonl"
    path.write_text(
        path.read_text().replace('"queue":"PAYROLL_OPERATIONS"', '"queue":"HUMAN_TRIAGE"', 1)
    )
    with pytest.raises(gd.DatasetError):
        gd.load(copy)


def test_tampered_case_with_recomputed_file_hash_is_detected(tmp_path: Path) -> None:
    """Even if someone also updates the file hash, the per-case hashes still catch the edit."""
    copy = tmp_path / "golden"
    shutil.copytree(gd.GOLDEN_DIR, copy)
    path = copy / "dev/cases.jsonl"
    path.write_text(
        path.read_text().replace('"queue":"PAYROLL_OPERATIONS"', '"queue":"HUMAN_TRIAGE"', 1)
    )
    m = json.loads((copy / "manifest.json").read_text())
    m["splits"]["dev"]["sha256"] = gd.file_sha256(path)
    (copy / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(gd.DatasetError, match="manifest hashes"):
        gd.load(copy)


def test_case_in_the_wrong_split_file_is_rejected(golden: gd.GoldenDataset) -> None:
    line = json.dumps(golden.split("dev")[0].model_dump(mode="json"))
    with pytest.raises(gd.DatasetError, match="wrong split"):
        gd.parse_cases(line, "heldout")


def test_case_files_contain_no_ticket_text(golden: gd.GoldenDataset, seed) -> None:  # type: ignore[no-untyped-def]
    text = "".join((gd.GOLDEN_DIR / f"{s}/cases.jsonl").read_text() for s in gd.SPLITS)
    tickets = {t["ticket_id"]: t for t in seed.tables["zendesk_mock.tickets"]}
    for c in golden.cases:
        t = tickets[c.ticket_id]
        assert t["subject"] not in text and t["body"][:40] not in text, c.case_id
    assert re.search(r"\bE-\d", text) is None  # no employer employee refs
