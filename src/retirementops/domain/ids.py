"""Deterministic identifiers shared by the payroll mock and the seed generator."""

from __future__ import annotations

import hashlib


def payroll_file_id(tenant_id: str, plan_id: str, pay_period_id: str, file_sha256: str) -> str:
    digest = hashlib.sha256(f"{tenant_id}|{plan_id}|{pay_period_id}|{file_sha256}".encode())
    return "PF-" + digest.hexdigest()[:12].upper()
