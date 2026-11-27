"""Server-side verification of evidence references cited by a draft.

Every cited record is re-fetched through its connector, scoped to the TICKET's tenant only, and its
current content hash is compared with the cited one. That proves:
- the record exists (callers cannot invent evidence);
- it belongs to the ticket's tenant (evidence from another tenant is indistinguishable from missing);
- it has not changed since it was read (stale or altered evidence is refused).
Policy snippets must also be in force today, and the caller must hold the read scope for each cited
record type (REQUIRED_SCOPE). Failure reasons name the index and a code, never values.
`verify` returns each record with the participant, plan and pay period it belongs to (read from the
server's own copy, never from the caller), so the M3 validator can check that the evidence concerns
one subject and covers the whole calculation.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from retirementops.connectors.interfaces import (
    PayrollReader,
    PlanAdminReader,
    PolicyKbReader,
    TicketReader,
)
from retirementops.domain.authz import RunBinding, Scope, TenantScope
from retirementops.domain.draft_validator import CitedRecord
from retirementops.domain.evidence import SourceRef
from retirementops.services.errors import InvalidRequest

# (participant_ref, plan_id, pay_period_id) of a verified record.
_Owner = tuple[str | None, str | None, str | None]
_NO_OWNER: _Owner = (None, None, None)

MAX_EVIDENCE_REFS = 50
SUPPORTED: dict[tuple[str, str], str] = {
    ("zendesk_mock", "ticket"): "ticket",
    ("planadmin_mock", "plan"): "plan",
    ("planadmin_mock", "plan_rule"): "plan_rule",
    ("planadmin_mock", "participant"): "participant",
    ("planadmin_mock", "enrollment"): "enrollment",
    ("payroll_mock", "pay_period"): "pay_period",
    ("payroll_mock", "payroll_row"): "payroll_row",
    ("servicekb_mock", "policy_snippet"): "policy_snippet",
}
# M2 review M-1: citing a record requires the same read scope as reading it directly, so the
# verifier cannot be used to probe data the caller may not read.
REQUIRED_SCOPE: dict[str, Scope] = {
    "ticket": Scope.TICKETS_READ,
    "plan": Scope.PLANS_READ,
    "plan_rule": Scope.PLANS_READ,
    "participant": Scope.PLANS_READ,
    "enrollment": Scope.PLANS_READ,
    "pay_period": Scope.PAYROLL_READ,
    "payroll_row": Scope.PAYROLL_READ,
    "policy_snippet": Scope.TICKETS_READ,
}
NOT_VISIBLE = "not found in this ticket's tenant, not readable by this caller, or not in force"
_PAYROLL_ROW_ID = re.compile(r"^(PF-[0-9A-F]{12})#([0-9]{1,18})$")


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    system: str
    record_type: str
    record_id: str
    content_hash: str

    def as_dict(self) -> dict[str, str]:
        return {
            "system": self.system,
            "record_type": self.record_type,
            "record_id": self.record_id,
            "content_hash": self.content_hash,
        }


class EvidenceVerifier:
    def __init__(
        self,
        *,
        tickets: TicketReader,
        planadmin: PlanAdminReader,
        payroll: PayrollReader,
        policies: PolicyKbReader,
    ) -> None:
        self._tickets, self._planadmin = tickets, planadmin
        self._payroll, self._policies = payroll, policies

    def _current(
        self, ref: EvidenceRef, scope: TenantScope, today: date
    ) -> tuple[SourceRef, _Owner] | None:
        kind, rid = SUPPORTED[(ref.system, ref.record_type)], ref.record_id
        if kind == "ticket":
            t = self._tickets.get_ticket(scope, rid)
            return (t.source, _NO_OWNER) if t else None
        if kind == "plan":
            p = self._planadmin.get_plan(scope, rid)
            return (p.source, (None, p.plan_id, None)) if p else None
        if kind == "plan_rule":
            r = self._planadmin.get_plan_rule(scope, rid)
            return (r.source, (None, r.plan_id, None)) if r else None
        if kind == "participant":
            pt = self._planadmin.get_participant(scope, rid)
            return (pt.source, (pt.participant_ref, None, None)) if pt else None
        if kind == "enrollment":
            e = self._planadmin.get_enrollment(scope, rid)
            return (e.source, (e.participant_ref, e.plan_id, None)) if e else None
        if kind == "pay_period":
            pp = self._payroll.get_pay_period(rid)
            return (pp.source, (None, None, pp.pay_period_id)) if pp else None
        if kind == "payroll_row":
            m = _PAYROLL_ROW_ID.match(rid)
            if not m:
                return None
            row = self._payroll.get_payroll_row(scope, m.group(1), int(m.group(2)))
            if row is None:
                return None
            return row.source, (row.participant_ref, row.plan_id, row.pay_period_id)
        snippet = self._policies.get_snippet(rid)  # policy_snippet
        if snippet and snippet.is_effective_on(today):
            return snippet.source, _NO_OWNER
        return None

    @staticmethod
    def require_readable(refs: Sequence[EvidenceRef], caller_scopes: frozenset[Scope]) -> None:
        """Refuse refs whose record type the caller cannot read, without any lookup (M-1, N-1).

        Runs before idempotency replay and before verification, so every path gives the same
        answer for the same unreadable evidence, whether or not a matching draft exists.
        Unsupported types are left to `verify`.
        """
        problems = [
            f"evidence[{i}]: {NOT_VISIBLE}"
            for i, ref in enumerate(refs)
            if (ref.system, ref.record_type) in SUPPORTED
            and REQUIRED_SCOPE[SUPPORTED[(ref.system, ref.record_type)]] not in caller_scopes
        ]
        if problems:
            raise InvalidRequest("evidence rejected: " + "; ".join(problems))

    def verify(
        self,
        refs: Sequence[EvidenceRef],
        *,
        tenant_id: str,
        today: date,
        caller_scopes: frozenset[Scope],
        binding: RunBinding | None = None,
    ) -> list[CitedRecord]:
        if len(refs) > MAX_EVIDENCE_REFS:
            raise InvalidRequest(f"at most {MAX_EVIDENCE_REFS} evidence references")
        scope = TenantScope(frozenset({tenant_id}))  # the ticket's tenant only
        problems: list[str] = []
        cited: list[CitedRecord] = []
        seen: set[tuple[str, str, str]] = set()
        for i, ref in enumerate(refs):
            key = (ref.system, ref.record_type, ref.record_id)
            if key in seen:
                problems.append(f"evidence[{i}]: duplicate")
                continue
            seen.add(key)
            if (ref.system, ref.record_type) not in SUPPORTED:
                problems.append(f"evidence[{i}]: unsupported record type")
                continue
            kind = SUPPORTED[(ref.system, ref.record_type)]
            if REQUIRED_SCOPE[kind] not in caller_scopes:
                # Same answer as "not found", and no lookup at all: no existence oracle.
                problems.append(f"evidence[{i}]: {NOT_VISIBLE}")
                continue
            current = self._current(ref, scope, today)
            if current is None:
                problems.append(f"evidence[{i}]: {NOT_VISIBLE}")
                continue
            source, (participant_ref, plan_id, pay_period_id) = current
            if binding is not None and not _within(
                binding, ref, participant_ref, plan_id, pay_period_id
            ):
                # An agent run may cite only its own ticket and host-resolved subjects (T-04).
                problems.append(f"evidence[{i}]: {NOT_VISIBLE}")
                continue
            if source.content_hash != ref.content_hash:
                problems.append(f"evidence[{i}]: content hash does not match current record")
                continue
            cited.append(
                CitedRecord(
                    ref.system,
                    ref.record_type,
                    ref.record_id,
                    ref.content_hash,
                    participant_ref,
                    plan_id,
                    pay_period_id,
                )
            )
        if problems:
            raise InvalidRequest("evidence rejected: " + "; ".join(problems))
        return cited


def _within(
    binding: RunBinding,
    ref: EvidenceRef,
    participant_ref: str | None,
    plan_id: str | None,
    pay_period_id: str | None,
) -> bool:
    if ref.record_type == "ticket" and ref.record_id != binding.ticket_id:
        return False
    return (
        (participant_ref is None or participant_ref in binding.participant_refs)
        and (plan_id is None or plan_id in binding.plan_ids)
        and (pay_period_id is None or pay_period_id in binding.pay_period_ids)
    )
