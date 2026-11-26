from __future__ import annotations

import json
import re
from collections import Counter

from retirementops.config import ROOT
from retirementops.domain.evidence import canonical_json, content_hash
from retirementops.domain.taxonomy import Intent
from retirementops.seed.generator import generate


def test_seed_is_reproducible() -> None:
    a, b = generate(42), generate(42)
    assert a.content_hash() == b.content_hash()
    assert content_hash(a.ticket_labels) == content_hash(b.ticket_labels)
    assert generate(43).content_hash() != a.content_hash()


def test_committed_manifest_matches_generator() -> None:
    """data/synthetic/* are generated artifacts; this fails if code and committed data drift."""
    ds = generate(42)
    committed = json.loads((ROOT / "data/synthetic/manifest.json").read_text())
    assert committed == json.loads(json.dumps(ds.manifest(), sort_keys=True))
    labels = (ROOT / "data/synthetic/ticket_labels.jsonl").read_text().splitlines()
    assert labels == [canonical_json(lab) for lab in ds.ticket_labels]


def test_seed_meets_spec_minimums() -> None:
    c = generate(42).counts()
    assert c["planadmin_mock.tenants"] >= 3
    assert c["planadmin_mock.plans"] >= 5
    assert c["planadmin_mock.participants"] >= 50
    assert c["payroll_mock.pay_periods"] >= 8
    assert c["payroll_mock.payroll_rows"] >= 300
    assert 100 <= c["zendesk_mock.tickets"] <= 150


def test_every_intent_and_difficult_case_is_present() -> None:
    ds = generate(42)
    intents = Counter(lab["intent"] for lab in ds.ticket_labels)
    assert set(intents) == {i.value for i in Intent}
    flags = {f.split(":")[0] for lab in ds.ticket_labels for f in lab["flags"]}
    assert flags >= {
        "PROMPT_INJECTION",
        "CROSS_TENANT_REFERENCE",
        "AMBIGUOUS_PARTICIPANT",
        "UNSUPPORTED_ACTION",
        "CONTAINS_RESTRICTED_PATTERN",
        "DUPLICATE_OF",
    }
    statuses = {c["expected_status"] for c in ds.cases}
    assert statuses == {
        "MATCH",
        "DISCREPANCY",
        "MISSING_PAYROLL_ROW",
        "MULTIPLE_PAYROLL_ROWS",
        "NOT_ENROLLED",
        "ENROLLMENT_CONFLICT",
        "RULE_MISSING",
        "RULE_CONFLICT",
    }
    assert {"ONE_CENT_UNDER", "ONE_CENT_OVER"} <= {c["case"] for c in ds.cases}


def test_no_realistic_pii_in_seed() -> None:
    blob = canonical_json(generate(42).tables)
    emails = set(re.findall(r"[\w.+-]+@[\w.-]+", blob))
    assert emails and all(e.endswith(".example.test") for e in emails)
    ssn_like = set(re.findall(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)", blob))
    assert ssn_like and all(s.startswith("000-") for s in ssn_like)  # never-issued area number
    for table in ("planadmin_mock.participants", "payroll_mock.payroll_rows"):
        for row in generate(42).tables[table]:
            assert not {"name", "dob", "ssn", "address", "email"} & set(row)


def test_cross_tenant_labels_are_truthful() -> None:
    ds = generate(42)
    tenant_of = {
        p["participant_ref"]: p["tenant_id"] for p in ds.tables["planadmin_mock.participants"]
    }
    emp = {
        (p["tenant_id"], p["employer_employee_ref"])
        for p in ds.tables["planadmin_mock.participants"]
    }
    tickets = {t["ticket_id"]: t for t in ds.tables["zendesk_mock.tickets"]}
    for lab in ds.ticket_labels:
        if "CROSS_TENANT_REFERENCE" not in lab["flags"]:
            continue
        body = tickets[lab["ticket_id"]]["body"]
        refs = re.findall(r"PART-\d{4}", body)
        foreign = [r for r in refs if tenant_of[r] != lab["tenant_id"]]
        e_refs = [e for e in re.findall(r"E-\d{5}", body) if (lab["tenant_id"], e) not in emp]
        assert foreign or e_refs, lab["ticket_id"]
