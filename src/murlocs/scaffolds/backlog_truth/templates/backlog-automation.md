# Backlog-truth automation

> Make "done" a fact a machine derives, not a checkbox a human ticks.

**Issue templates:** `.github/ISSUE_TEMPLATE/` — use **Saga**, **Epic**,
**Investigation**, or **Task** when opening work. Templates link here; do not
duplicate scope in repo planning files.

**Process harness (outside compile):** `docs/plan/issue-lifecycle.md` and
`docs/plan/BACKLOG.md` are stamped by the kit and must never be absorbed into
compiled `AGENTS.md` maps. Link out from maps is fine; embedding orchestrator
prose in maps is not.

This machinery is opt-in, local-first, and incrementally adoptable — matching
Murlocs' safe path for existing repos. Nothing becomes a required check until
you enable it in branch protection.

## Hierarchy

```text
Saga → Epic → Investigation → Task
```

Hierarchy is expressed through **actual GitHub sub-issues**. Issue-body parent
fields and markdown `- [ ] #N` lists are descriptive only.

| Kind | Default labels | Role | Claimable? |
| --- | --- | --- | --- |
| **Saga** | `saga`, `roadmap` | Strategic thread | Never |
| **Epic** | `epic` | Outcome + exit criteria | Never |
| **Investigation** | `investigation`, `design` | Planner-owned decision freeze | Never |
| **Task** | add `P*` + domain | Worker-owned unit with proof | Only when `ready` |

`ready` is the **worker lease**. Never apply it to a saga, epic, or
investigation.

## Label taxonomy

See `.github/labels.yml` for the full set. Families:

| Family | Examples |
| --- | --- |
| Kind | `saga`, `epic`, `investigation`, `design` |
| Priority | `P0`–`P3` |
| Workflow | `ready`, `blocked`, `upstream-blocked`, `research`, `decision` |
| Automation | `merged-pending-close`, `acceptance-tracked`, `closure-candidate`, `needs-grooming`, `needs-decomposition` |

## Acceptance anchors

Tag the test(s) that prove a GitHub issue's acceptance criteria:

```python
import pytest

@pytest.mark.issue(210)
def test_scaffold_stamps_kit():
    ...
```

Declare the same anchor in `.murlocs/manifest.toml` when using Murlocs checks:

```toml
[[work_items]]
id = "210"
issue = 210
acceptance = "pytest:issue(210)"
```

See `docs/backlog-truth.md` for adapter details.

## Closure gate

`.github/workflows/issue-closure-gate.yml` runs
`scripts/check_closure_acceptance.py`. A PR whose body says `Closes #N` (or
`Fixes` / `Resolves`) fails unless:

1. An `@pytest.mark.issue(N)` test exists, or
2. The PR body declares `Acceptance #N: n/a (reason)`.

Advisory until you mark the check required in branch protection.

## Reconcile / drift

`.github/workflows/backlog-reconciliation.yml` runs
`scripts/reconcile_backlog.py`. The stamped script is a read-only stub that
keeps day-one wiring valid; full derive/apply behaviour lands with the
reconcile engine. Prefer continuous runs on merge once that engine is present.

## Day-one install

```bash
murlocs init --name "My Repository"
murlocs scaffold backlog-truth
murlocs scaffold status
murlocs check
```

Pieces are individually adoptable:

```bash
murlocs scaffold backlog-truth --only templates
murlocs scaffold backlog-truth --only workflows --only labels
```

Murlocs records installed pieces under `[kits.backlog_truth]` in
`.murlocs/manifest.toml` (and a receipt at `.murlocs/kits/backlog_truth.toml`)
so drift checks keep them current.
