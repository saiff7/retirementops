from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from retirementops.governance.audit import AuditEvent, AuditLog
from retirementops.governance.pseudonym import RAW_IDENTIFIER, Pseudonymiser
from retirementops.governance.rate_limit import AuditThrottle, TokenBucketLimiter
from tests.conftest import FakeClock


def test_token_bucket_burst_then_refill() -> None:
    clock = FakeClock()
    lim = TokenBucketLimiter(capacity=3, refill_per_second=1, clock=clock)
    assert [lim.allow("a") for _ in range(4)] == [True, True, True, False]
    assert lim.retry_after("a") == 1
    clock.advance(1)
    assert lim.allow("a") and not lim.allow("a")
    assert lim.allow("b")  # keys are independent


def test_has_capacity_does_not_consume() -> None:
    lim = TokenBucketLimiter(capacity=1, refill_per_second=0.1, clock=FakeClock())
    assert lim.has_capacity("a") and lim.has_capacity("a")
    assert lim.allow("a") and not lim.has_capacity("a")


def test_limiter_memory_is_bounded() -> None:
    lim = TokenBucketLimiter(capacity=1, refill_per_second=1, max_keys=100, clock=FakeClock())
    for i in range(10_000):
        lim.allow(f"k{i}")
    assert len(lim) == 100


def test_throttle_per_key_global_and_summary() -> None:
    clock = FakeClock()
    th = AuditThrottle(window_seconds=60, per_key=1, global_limit=2, clock=clock)
    assert th.admit("a").record and not th.admit("a").record  # per key
    assert th.admit("b").record and not th.admit("c").record  # global
    clock.advance(60)
    d = th.admit("c")
    assert d.record and d.summary == {"suppressed_events": 2, "distinct_sources": 2}
    assert th.admit("d").summary is None  # summary appears once per window


@pytest.mark.parametrize(
    "kw", [{"capacity": 0, "refill_per_second": 1}, {"capacity": 1, "refill_per_second": 0}]
)
def test_limiter_rejects_bad_config(kw: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        TokenBucketLimiter(**kw)  # type: ignore[arg-type]


def test_pseudonymiser_is_keyed_stable_and_deep() -> None:
    a, b = Pseudonymiser(b"k" * 16), Pseudonymiser(b"j" * 16)
    assert a.text("PART-0001") == a.text("PART-0001") != b.text("PART-0001")
    out = a.deep(
        {"route": "/x/PART-0001", "refs": ["E-40404", "ok"], "n": 3, "note": "ssn 000-12-3456"}
    )
    assert not RAW_IDENTIFIER.search(str(out))
    assert out["n"] == 3 and out["refs"][1] == "ok" and out["route"].startswith("/x/pref_")
    with pytest.raises(ValueError):
        Pseudonymiser(b"short")


def test_audit_log_refuses_raw_identifiers_before_touching_db() -> None:
    # Engine points nowhere: the guard must reject before any connection is attempted.
    log = AuditLog(create_engine("postgresql+psycopg://nobody@127.0.0.1:1/none"))
    for event in (
        AuditEvent("op.alex", "test.event", target_ref="PART-0001"),
        AuditEvent("op.alex", "test.event", details={"route": "/p/PART-0001"}),
        AuditEvent("op.alex", "test.event", correlation_id="E-40404"),
        AuditEvent("op.alex", "test.event", details={"note": "000-12-3456"}),
    ):
        with pytest.raises(ValueError, match="raw identifier"):
            log.append(event)
