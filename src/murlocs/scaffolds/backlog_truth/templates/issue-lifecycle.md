# Plan: Issue lifecycle (process harness)

- **Status:** Scaffolded by `murlocs scaffold backlog-truth`
- **Compile:** This file is **outside** `murlocs compile`. Do not move it into a
  managed `AGENTS.md` map. Linking from maps is fine; embedding this harness in
  compiled guidance is not.
- **Complements:** `docs/backlog-automation.md`, `.github/ISSUE_TEMPLATE/`

## Why this matters

Large tasks are trees. A planner that also implements fills its context with
leaf detail and drifts. A worker that also designs re-decides questions already
settled elsewhere. Native tracker issues are the durable intent store; they must
be **specs that lower into owned leaves**, not tickets agents reinterpret.

## Principles

1. **Specs as prompts** — Issue bodies are the unit of work, not chat history.
2. **Planner never implements; worker never plans** — Design questions stay on
   saga / epic / investigation issues. Tasks execute frozen acceptance.
3. **One decision owner per subtree** — If two Tasks would decide the same
   question, collapse it into an Investigation (or ADR) first.
4. **Path scope** — Every Task names the paths it may touch; bind to guidance
   `owns` / `murlocs impact` when available.
5. **Machine exit criteria** — Prefer `@pytest.mark.issue(N)` (or an explicit
   `Acceptance #N: n/a (reason)`) over prose-only checkboxes.
6. **`ready` is the lease** — Workers start only on `ready` Tasks. Waiting on
   humans or deps uses `blocked` / `upstream-blocked`.
7. **Contract vs calendar** — Compiled `AGENTS.md` maps are contract law; this
   harness and `BACKLOG.md` are the court calendar. Keep them separate.

## Issue tree

```text
saga (north-star thread)
 └── epic (outcome + exit criteria)
      ├── investigation (freeze one decision — never claimable)
      └── task (owned paths + machine acceptance — claimable when ready)
```

| Kind | Label | Opens when | Closes when |
| --- | --- | --- | --- |
| **Saga** | `saga` | A multi-epic north star appears | Thread obsolete or absorbed |
| **Epic** | `epic` | Outcome and exit criteria are known | Exit criteria graded true |
| **Investigation** | `investigation` | Ambiguity would otherwise fork Tasks | Decision recorded; ADR linked if lasting |
| **Task** | (add `P*`) | Paths + acceptance are frozen | Machine acceptance passes + PR merged |

## Required fields by kind

### Saga

- North-star sentence
- Workstream / epic list (links, not a fake checklist of code)
- Release gates and success signal
- Architectural boundaries and **Not now**

### Epic

- Parent saga (descriptive; attach as sub-issue)
- Outcome, context, dependencies
- Exit criteria (gradable, cross-child)
- Not in scope

### Investigation

- Parent epic
- Question being frozen
- Options considered
- Decision + consequences (before close)
- What Tasks may assume after close
- ADR path when the decision outlives the epic

### Task

- Parent epic (descriptive; attach as sub-issue)
- Priority + execution state
- Path scope (allowlist)
- Outcome, immediate next action, scope, boundaries
- Required proof (`@pytest.mark.issue(N)` named)
- Acceptance criteria
- `ready` only when dependencies and approvals are satisfied

## Closure conventions

- Tasks: `Closes #N` / `Fixes #N` / `Resolves #N` with acceptance proof
- Epics/Sagas: `Advances-Epic: #N` — parents close when children and gates close
- Investigations close as decisions; implementation lives on separate Tasks
