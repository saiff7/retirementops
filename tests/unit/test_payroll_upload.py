from __future__ import annotations

import pytest

from retirementops.domain.payroll_upload import (
    RejectionReason,
    UploadAccepted,
    UploadRejected,
    validate_payroll_csv,
)

HEADER = "participant_ref,pay_period_id,eligible_pay,employee_contribution,employer_contribution\n"
GOOD = (
    HEADER + "PART-0001,PP-2026-01,2000.00,100.00,50.00\nPART-0002,PP-2026-01,1500.50,75.03,0.00\n"
)


def check(data: str) -> UploadAccepted | UploadRejected:
    return validate_payroll_csv(data.encode(), expected_pay_period_id="PP-2026-01")


def test_valid_file_accepted() -> None:
    res = check(GOOD)
    assert isinstance(res, UploadAccepted)
    assert res.row_count == 2
    assert res.rows[1].employee_contribution.to_cents() == 7503


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (
            HEADER.strip() + ",ssn\nPART-0001,PP-2026-01,1.00,1.00,1.00,000-12-3456\n",
            RejectionReason.RESTRICTED_FIELD,
        ),
        (
            HEADER.strip() + ",bank_account\nPART-0001,PP-2026-01,1.00,1.00,1.00,x\n",
            RejectionReason.RESTRICTED_FIELD,
        ),
        (
            HEADER.strip() + ",employee_name\nPART-0001,PP-2026-01,1.00,1.00,1.00,x\n",
            RejectionReason.RESTRICTED_FIELD,
        ),
        (
            HEADER.strip() + ",notes\nPART-0001,PP-2026-01,1.00,1.00,1.00,000-12-3456\n",
            RejectionReason.RESTRICTED_VALUE_PATTERN,
        ),
        (
            HEADER + "PART-0001,PP-2026-01,1.00,1.00,123456789\n",
            RejectionReason.RESTRICTED_VALUE_PATTERN,
        ),
        (
            HEADER + "PART-0001,PP-2026-01,1.00,1.00,0001234567890123\n",
            RejectionReason.RESTRICTED_VALUE_PATTERN,
        ),
        ("participant,period,pay\nPART-0001,PP-2026-01,1.00\n", RejectionReason.BAD_HEADER),
        (HEADER + "PART-0001,PP-2026-01,12.5,1.00,1.00\n", RejectionReason.INVALID_ROW),
        (HEADER + "PART-0001,PP-2026-01,-1.00,1.00,1.00\n", RejectionReason.INVALID_ROW),
        (HEADER + "PART-1,PP-2026-01,1.00,1.00,1.00\n", RejectionReason.INVALID_ROW),
        (HEADER + "PART-0001,PP-2026-02,1.00,1.00,1.00\n", RejectionReason.WRONG_PAY_PERIOD),
        (
            HEADER + "PART-0001,PP-2026-01,1.00,1.00,1.00\nPART-0001,PP-2026-01,1.00,1.00,1.00\n",
            RejectionReason.DUPLICATE_PARTICIPANT,
        ),
        ("", RejectionReason.EMPTY),
    ],
)
def test_rejections(data: str, reason: RejectionReason) -> None:
    res = check(data)
    assert isinstance(res, UploadRejected)
    assert res.reason is reason


def test_rejection_detail_never_echoes_values() -> None:
    res = check(HEADER.strip() + ",notes\nPART-0001,PP-2026-01,1.00,1.00,1.00,000-12-3456\n")
    assert isinstance(res, UploadRejected)
    assert "000-12-3456" not in res.detail and "PART-0001" not in res.detail
    assert res.detail == "line 2, column 6"


def test_too_large_and_not_utf8() -> None:
    big = validate_payroll_csv(b"x" * 1_000_001, expected_pay_period_id="PP-2026-01")
    assert isinstance(big, UploadRejected) and big.reason is RejectionReason.TOO_LARGE
    bad = validate_payroll_csv(b"\xff\xfe", expected_pay_period_id="PP-2026-01")
    assert isinstance(bad, UploadRejected) and bad.reason is RejectionReason.NOT_UTF8
