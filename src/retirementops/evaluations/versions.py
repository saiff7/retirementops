"""Versions of the evaluation's own logic, recorded in every run's metadata.

`SCORER_VERSION`:
- `eval-scorers/1`: M4 Stages 4-8 (2026-11-27).
- `eval-scorers/2`: final-review fixes. M-1: draft dates are scored against the golden
  period-end date. M-2: a cited or retrieved record counts as correct evidence, or as coverage,
  only if the record itself belongs to the case's ticket, participant, plan and pay period.

Results scored under different scorer or dataset versions are not comparable; baselines say so
instead of reporting regressions.
"""

SCORER_VERSION = "eval-scorers/2"
