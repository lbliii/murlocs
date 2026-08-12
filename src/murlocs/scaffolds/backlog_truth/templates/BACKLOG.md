# BACKLOG harness

> Process calendar — not compiled guidance.

This file is stamped by `murlocs scaffold backlog-truth` and is recorded as a
**non-compile / process** path. `murlocs compile` must never absorb it into
`AGENTS.md`. Link from maps if helpful; do not paste orchestrator prose into
managed maps.

## Purpose

Hold ephemeral board notes, wave ordering, and pointers to native tracker
issues. The tracker (GitHub/GitLab issues + sub-issues) remains authoritative
for hierarchy and state. This file is a human/agent scratchpad that must stay
outside the guidance-network compile inputs.

## Current focus

- _Replace this section with the active saga/epic links._
- Prefer issue numbers over checklists of child status.
- Record only pointers and wave notes; close work on the tracker.

## Operator cheat sheet

| Intent | Where |
| --- | --- |
| Open work | `.github/ISSUE_TEMPLATE/` (Saga / Epic / Investigation / Task) |
| Lifecycle rules | `docs/plan/issue-lifecycle.md` |
| Closure + labels | `docs/backlog-automation.md` |
| Acceptance anchors | `docs/backlog-truth.md` + `@pytest.mark.issue(N)` |
| Kit status | `murlocs scaffold status` / `murlocs check` |

## Non-goals

- Do not treat this file as a second issue tracker.
- Do not move this harness under a scope `map` or into `.murlocs/layers/`.
- Do not claim Task work from Investigation issues.
