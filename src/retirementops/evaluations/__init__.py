"""M4 evaluation suite (ADR-010 Amendment 1). SYNTHETIC DATA ONLY.

Modules: `dataset` (golden cases and manifest), `provision` (the `retirementops_eval` database),
`observe` (read-only readback as `svc_eval_reader`), `recording` (provider wrapper), `runner`,
`scorers`, `gates` and `report`. Scores are computed only against frozen golden expectations,
never against the system's own validator or routing output.
"""
