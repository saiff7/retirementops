"""Success paths of the Zendesk writer and the two PlanAdmin lookups not covered elsewhere."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import Engine, text

from retirementops.config import DbRole
from retirementops.connectors.interfaces import StaleRecordError
from retirementops.connectors.planadmin_mock import PlanAdminMockReader
from retirementops.connectors.zendesk_mock import ZendeskMockWriter
from retirementops.domain.authz import TenantScope
from retirementops.domain.records import Participant
from retirementops.domain.taxonomy import Queue
from retirementops.seed.generator import Dataset

pytestmark = pytest.mark.integration
RoleEngine = Callable[[DbRole], Engine]
ALDER = TenantScope(frozenset({"TEN-ALDER"}))


def _ticket_state(owner_engine: Engine, ticket_id: str) -> dict[str, Any]:
    with owner_engine.connect() as conn:
        t = (
            conn.execute(
                text(
                    "SELECT status, version, assignee_queue FROM zendesk_mock.tickets "
                    "WHERE ticket_id = :t"
                ),
                {"t": ticket_id},
            )
            .mappings()
            .one()
        )
        comments = conn.execute(
            text("SELECT count(*) FROM zendesk_mock.ticket_comments WHERE ticket_id = :t"),
            {"t": ticket_id},
        ).scalar_one()
        events = (
            conn.execute(
                text(
                    "SELECT event_type FROM zendesk_mock.ticket_events WHERE ticket_id = :t ORDER BY event_id"
                ),
                {"t": ticket_id},
            )
            .scalars()
            .all()
        )
    return {**t, "comments": comments, "events": list(events)}


@pytest.fixture
def writer_ticket(dataset: Dataset) -> str:
    """An ordinary Alder ticket used only by these tests (not ZD-1001, which others assert on)."""
    labels = {lab["ticket_id"]: lab for lab in dataset.ticket_labels}
    return next(
        t["ticket_id"]
        for t in dataset.tables["zendesk_mock.tickets"]
        if t["tenant_id"] == "TEN-ALDER"
        and t["ticket_id"] not in {"ZD-1001", "ZD-1002"}
        and not labels[t["ticket_id"]]["flags"]
        and t["status"] == "new"
    )


# ---------------------------------------------------------------- Zendesk writer
def test_post_reply_replay_and_assignment(
    role_engine: RoleEngine, owner_engine: Engine, writer_ticket: str
) -> None:
    writer = ZendeskMockWriter(role_engine(DbRole.ZENDESK_WRITER))
    s0 = _ticket_state(owner_engine, writer_ticket)

    posted = writer.post_public_reply(
        ALDER,
        ticket_id=writer_ticket,
        body="Approved reply text.",
        idempotency_key="draft-1:v1:reply",
        expected_version=s0["version"],
    )
    s1 = _ticket_state(owner_engine, writer_ticket)
    assert not posted.replayed and posted.ticket_id == writer_ticket
    assert s1["comments"] == s0["comments"] + 1
    assert s1["version"] == s0["version"] + 1
    assert s1["events"][-1] == "comment.added"
    assert s1["status"] == s0["status"]  # posting never changes status
    with owner_engine.connect() as conn:
        author = conn.execute(
            text(
                "SELECT author_kind, public FROM zendesk_mock.ticket_comments WHERE comment_id = :c"
            ),
            {"c": posted.comment_id},
        ).one()
    assert tuple(author) == ("retirementops_service", True)

    # Idempotent replay: same key -> same comment, no second insert, no version bump.
    replay = writer.post_public_reply(
        ALDER,
        ticket_id=writer_ticket,
        body="Approved reply text.",
        idempotency_key="draft-1:v1:reply",
        expected_version=s0["version"],
    )
    assert replay.replayed and replay.comment_id == posted.comment_id
    assert _ticket_state(owner_engine, writer_ticket) == s1

    # Assignment with the current version.
    updated = writer.apply_assignment(
        ALDER,
        ticket_id=writer_ticket,
        queue=Queue.PAYROLL_OPERATIONS,
        expected_version=s1["version"],
    )
    s2 = _ticket_state(owner_engine, writer_ticket)
    assert (
        updated.assignee_queue is Queue.PAYROLL_OPERATIONS and updated.version == s1["version"] + 1
    )
    assert s2["assignee_queue"] == "PAYROLL_OPERATIONS" and s2["version"] == s1["version"] + 1
    assert s2["events"][-1] == "assignment.changed" and s2["status"] == s0["status"]


def test_writer_rejects_stale_versions_without_side_effects(
    role_engine: RoleEngine, owner_engine: Engine, writer_ticket: str
) -> None:
    writer = ZendeskMockWriter(role_engine(DbRole.ZENDESK_WRITER))
    before = _ticket_state(owner_engine, writer_ticket)
    with pytest.raises(StaleRecordError):
        writer.post_public_reply(
            ALDER,
            ticket_id=writer_ticket,
            body="x",
            idempotency_key="draft-2:v1:reply",
            expected_version=before["version"] - 1,
        )
    with pytest.raises(StaleRecordError):
        writer.apply_assignment(
            ALDER,
            ticket_id=writer_ticket,
            queue=Queue.HUMAN_TRIAGE,
            expected_version=before["version"] + 5,
        )
    assert _ticket_state(owner_engine, writer_ticket) == before


def test_writer_idempotency_key_bound_to_one_ticket_and_input_limits(
    role_engine: RoleEngine, owner_engine: Engine, writer_ticket: str, dataset: Dataset
) -> None:
    writer = ZendeskMockWriter(role_engine(DbRole.ZENDESK_WRITER))
    version = _ticket_state(owner_engine, writer_ticket)["version"]
    writer.post_public_reply(
        ALDER,
        ticket_id=writer_ticket,
        body="first",
        idempotency_key="draft-3:v1:reply",
        expected_version=version,
    )
    other = next(
        t["ticket_id"]
        for t in dataset.tables["zendesk_mock.tickets"]
        if t["tenant_id"] == "TEN-ALDER" and t["ticket_id"] != writer_ticket
    )
    with pytest.raises(ValueError, match="different ticket"):
        writer.post_public_reply(
            ALDER,
            ticket_id=other,
            body="first",
            idempotency_key="draft-3:v1:reply",
            expected_version=1,
        )
    for body, key in (
        ("", "draft-4:v1:reply"),
        ("x" * 20_001, "draft-4:v1:reply"),
        ("ok", "short"),
    ):
        with pytest.raises(ValueError):
            writer.post_public_reply(
                ALDER,
                ticket_id=writer_ticket,
                body=body,
                idempotency_key=key,
                expected_version=version + 1,
            )


# ---------------------------------------------------------------- PlanAdmin lookups
def test_get_tenant_is_tenant_scoped(role_engine: RoleEngine) -> None:
    reader = PlanAdminMockReader(role_engine(DbRole.PLANADMIN_READER))
    tenant = reader.get_tenant(ALDER, "TEN-ALDER")
    assert tenant is not None and tenant.display_name.endswith("(synthetic)")
    assert tenant.source.system == "planadmin_mock" and tenant.source.record_type == "tenant"
    assert reader.get_tenant(ALDER, "TEN-BIRCH") is None
    assert reader.get_tenant(TenantScope(frozenset()), "TEN-ALDER") is None


@pytest.mark.parametrize(
    ("tenants", "expected"),
    [
        ({"TEN-ALDER"}, ["PART-0005"]),
        ({"TEN-BIRCH"}, ["PART-0025"]),
        ({"TEN-ALDER", "TEN-BIRCH"}, ["PART-0005", "PART-0025"]),  # ambiguous across tenants
        ({"TEN-COBALT"}, []),
    ],
)
def test_find_by_employee_ref_resolves_only_within_tenants(
    role_engine: RoleEngine, tenants: set[str], expected: list[str]
) -> None:
    # E-40404 is deliberately shared by an Alder and a Birchway employee in the seed.
    reader = PlanAdminMockReader(role_engine(DbRole.PLANADMIN_READER))
    found = reader.find_participants_by_employee_ref(TenantScope(frozenset(tenants)), "E-40404")
    assert [p.participant_ref for p in found] == expected
    assert all(p.tenant_id in tenants for p in found)


def test_find_by_unknown_employee_ref_is_empty_and_dto_is_minimal(role_engine: RoleEngine) -> None:
    reader = PlanAdminMockReader(role_engine(DbRole.PLANADMIN_READER))
    assert reader.find_participants_by_employee_ref(ALDER, "E-99999") == []
    # The employer employee number is used for resolution but never returned (minimisation).
    assert "employer_employee_ref" not in Participant.__dataclass_fields__
