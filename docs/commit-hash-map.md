# Commit hash map (email rewrite before publication)

Before the first push on 2026-11-27, the owner chose to replace the personal email address in every
commit's author and committer fields with the GitHub noreply address. Only those two fields
changed: every commit's file tree is byte-identical (checked commit by commit), and names, dates
and messages are unchanged. Because commit hashes include those fields, every hash changed.

Documentation and evaluation report metadata written before the rewrite (for example `git_sha` in
`evals/reports/*/metadata.json`, and commit references in `CURRENT_STATUS.md`, `HANDOFF.md` and
`DECISIONS.md`) still cite the **original** hashes. Use this table to find the published commit.

| Original | Published | Subject |
|---|---|---|
| `ac9e36b` | `0c7632a` | M0 + M1: design docs and synthetic business backend |
| `91f959b` | `e77ecb8` | M2: add hardened service operations MCP server |
| `f4d3349` | `63c25ee` | Docs: finalize M2 project handoff |
| `3d281b6` | `5328047` | Docs: correct project status and handoff |
| `b37d9c5` | `5984a6e` | Docs: record documentation commit 3d281b6 |
| `1515ed2` | `3f14da2` | M3 stages 1-5: validator, run tokens, trace storage, skills, providers |
| `d7a9a63` | `b92a1d8` | M3 stage 6: orchestrator, host resolution and review fixes |
| `3ab1238` | `dcb17a5` | M3 stage 7: add Claude Code developer skills |
| `a674e35` | `0cc9812` | docs: mark M3 fully complete |
| `3670c4e` | `04ec7dd` | docs: approve M4 evaluation design |
| `b469a13` | `8a74cbd` | docs: record M4 Stage 0 approval |
| `db6b6ca` | `7bb517c` | M4 stage 1: golden dataset, manifest and integrity tests |
| `c47170f` | `e076983` | M4 stage 2: eval database provisioning and SELECT-only scoring reader |
| `a484171` | `ae3b797` | M4 stage 3: recording wrapper, read-only observer and case runner |
| `774b6cf` | `94c904e` | M4 stage 4: deterministic scorers for the spec section 12 metrics |
| `9d22fe2` | `a18b11d` | M4 stage 5: safety tier, ADR-010 gates and gate-trip tests |
| `9806087` | `acd1414` | M4 stage 6: quality tier reports, qwen3:8b vs qwen3:14b |
| `b11a4c3` | `8cd1c88` | M4 stage 7: reproducible reports, baselines and regression detection |
| `9aa0c95` | `2b15b3d` | M4 stage 8: CI safety gate and deliberate-break demonstration |
| `e838c73` | `7e594d9` | docs: record M4 implementation handoff |
| `eb9e69a` | `d8d4980` | M4: score draft dates and record-level evidence ownership (scorer v2) |
| `5db205f` | `586571e` | M4: re-evaluate under eval-scorers/2 and re-baseline |
