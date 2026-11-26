"""The documented routing table and the code must match exactly."""

import re

from retirementops.config import ROOT
from retirementops.domain.taxonomy import DEFAULT_ROUTING, Intent, Queue


def test_routing_table_matches_documentation() -> None:
    text = (ROOT / "docs" / "business-process-as-is-to-be.md").read_text()
    rows = re.findall(
        r"^\| `([A-Z_]+)` \| `([A-Z_]+)` \| (Spec §4|Project decision R-\d) \|$", text, re.M
    )
    documented = {Intent(i): Queue(q) for i, q, _ in rows}
    assert documented == DEFAULT_ROUTING
    assert {i for i, _, src in rows if src.startswith("Project")} == {
        "ELIGIBILITY_QUESTION",
        "PAYROLL_FILE_ISSUE",
        "GENERAL_INFORMATION",
    }
    assert set(DEFAULT_ROUTING) == set(Intent)  # every intent has a default route
