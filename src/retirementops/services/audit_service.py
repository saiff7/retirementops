"""Service wrapper for audit writes. Every field is pseudonymised before it reaches the log.

Participant refs (PART-nnnn) and employer employee refs (E-nnnnn) become keyed pseudonyms, and
SSN-like values are redacted. `governance.audit.AuditLog` independently refuses any raw identifier,
so a missed call site fails loudly instead of storing personal data.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from retirementops.governance.audit import AuditEvent, AuditLog, ChainReport
from retirementops.governance.pseudonym import Pseudonymiser
from retirementops.services.context import RequestContext


class AuditService:
    def __init__(self, log: AuditLog, pseudonymiser: Pseudonymiser) -> None:
        self._log, self._p = log, pseudonymiser

    def pseudonym(self, prefix: str, value: str) -> str:
        return self._p.token(prefix, value)

    def record(
        self,
        ctx: RequestContext | None,
        event_type: str,
        *,
        target_ref: str | None = None,
        tenant_id: str | None = None,
        details: Mapping[str, Any] | None = None,
        anonymous_correlation_id: str | None = None,
        actor_override: str | None = None,
    ) -> str:
        principal = ctx.principal if ctx else None
        correlation = ctx.correlation_id if ctx else anonymous_correlation_id
        actor = principal.principal_id if principal else (actor_override or "anonymous")
        return self._log.append(
            AuditEvent(
                actor_principal=self._p.text(actor),
                actor_role=principal.primary_role if principal else None,
                tenant_id=tenant_id,
                correlation_id=self._p.text(correlation) if correlation else None,
                event_type=event_type,
                target_ref=self._p.text(target_ref) if target_ref else None,
                details=self._p.deep(dict(details or {})),
            )
        )

    def verify(self) -> ChainReport:
        return self._log.verify()
