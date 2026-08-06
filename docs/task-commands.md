# Intent-shaped task commands

This document specifies version 1 of the Murlocs task-command composition contract. The key words
**MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, and **MAY** are normative.

Task commands are a small task-language front door over existing Murlocs primitives. An agent uses
`orient` before unfamiliar work, `review-changes` to route its declared changes, and `finish` to
obtain a fresh structural completion receipt. They are read-only, deterministic, and compose the
granular `check`, `impact`, adoption, explanation, and curation-validation results instead of
forking their semantics.

The three commands share one envelope, defined here and implemented in
[`src/murlocs/task_commands.py`](https://github.com/lbliii/murlocs/blob/main/src/murlocs/task_commands.py).
The shared envelope is why the intent-shaped commands cannot drift from the primitives or weaken
lifecycle freshness.

## Non-goals

- Task commands do **not** reimplement finding, routing, proof, authority, or outcome semantics.
  Each composite action is derived from, and traceable to, a granular
  [`io.murlocs.outcome`](outcome-envelope.md) result or curation-validation finding.
- Task commands never execute a registered repository check, mutate repository state, compile,
  accept, promote, or repair guidance. They inherit the read-only guarantees of the
  [activation lifecycle](activation-lifecycle.md).
- Routing is a review signal. It is never presented as a semantic-truth or merge-policy decision.

## The shared composition envelope

Every task command returns one `io.murlocs.task`, `schema_version` 1 envelope with these fields:

| Field | Meaning |
| --- | --- |
| `contract`, `schema_version` | Exact string `io.murlocs.task` and integer `1`. |
| `command` | `orient`, `review-changes`, or `finish`. |
| `ok` | `false` only when the envelope is blocking. |
| `repository` | Repository `root`, `adoption_state` (from adoption status), and `manifest_present`. |
| `git_view` | The exact change view: `kind`, `revision_range`, resolved `paths`, `available`, and `detail`. |
| `freshness` | `lifecycle`, the observed `view_state_id`, a caller `receipt_state_id`, a `stale` flag, and the `dependencies` the result is bound to. |
| `correlation` | The caller `correlation_id`, carried unchanged; Murlocs never generates one. |
| `classification` | Counts of `blocking`, `authority_required`, `agent_action`, and `recommended` actions. |
| `actions` | One ordered next-action list; see classification below. |
| `receipts` | The granular `check` and `impact` outcome sidecars and the `curation` validation summary that the actions are derived from. |
| `status` | `pass`, `advisory`, or `blocking`. |
| `blocking`, `silent` | `blocking` is `status == "blocking"`; `silent` is `status == "pass"`. |
| `summary` | Compact deterministic human text. |

`orient` adds a `path` and an `orientation` section; `review-changes` adds the `review` impact
report; `finish` adds a `completion` section that names registered checks and records that they were
not executed.

### Action classification

Each action carries exactly one classification. The classification is derived deterministically from
the granular outcome finding it summarizes:

| Classification | Derivation | Meaning |
| --- | --- | --- |
| `blocking` | `check` `deterministic_repair`, a blocking curation finding, or a stale receipt | Repository guidance is structurally broken or the receipt is stale; completion cannot proceed. |
| `authority_required` | any `authority_required` finding | Owner review is required before the gated boundary. |
| `agent_action` | a blocking `agent_action` finding | The agent should inspect and resolve a proof, coverage, or check finding. |
| `recommended` | an advisory `agent_action` finding | A recommended review scope the agent may choose to inspect. |

Actions are ordered by classification (`blocking`, then `authority_required`, then `agent_action`,
then `recommended`) and then by source, so the most urgent action is always first. Every action's
`codes` are a subset of the codes present in the granular `receipts`, which makes each composite
action traceable to its source result.

The envelope is `blocking` when a `check` receipt is blocking, when a curation record is blocking, or
when a supplied receipt is stale. `impact` routing is advisory and never changes the exit code, so
`review-changes` preserves the stable `impact` exit semantics.

### Repository state, Git view, correlation, and freshness

- **Repository state** is reported explicitly. An **ambiguous** adoption state fails visibly rather
  than proceeding.
- **The Git view is always explicit.** `review-changes` and `finish` require exactly one of repeated
  `--path` values, `--staged`, `--working-tree`, or `--revision-range`. Selecting none or more than
  one fails visibly; the command never guesses which changes belong to the task. `orient` operates on
  a single path and uses no Git diff view.
- **Correlation** ids are validated and carried unchanged into the granular receipts.
- **Freshness** is explicit. `orient` and `review-changes` are `inspection` lifecycle; `finish` is
  `completion` lifecycle. A Git-backed view records the observed `view_state_id` and the freshness
  `dependencies`. `finish` MAY be given a `receipt_state_id` for an index-bound view; when it does not
  equal the freshly observed state the receipt is `stale`, which produces a blocking action. A stale
  pre-edit receipt therefore cannot satisfy completion.

### Compact and silent-capable output

Healthy output is compact. When `status` is `pass` the envelope is `silent`, so a silent host MAY
suppress presentation. The compact terminal rendering always states the repository and the exact Git
view.

## Versioning and backward compatibility

- The contract is `io.murlocs.task` at `schema_version` 1. An unsupported schema version MUST fail
  visibly; a consumer MUST NOT guess at its meaning.
- Consumers MUST ignore unknown envelope fields so that additive fields remain backward compatible
  within version 1. Removing a field, renaming a field, or changing a field's meaning is a breaking
  change that MUST increment `schema_version`.
- The embedded `receipts` use the versioned [`io.murlocs.outcome`](outcome-envelope.md) contract and
  are governed by its own versioning rules.
- Because task commands only compose existing results, the granular `status`, `explain`, `impact`,
  and `check` surfaces remain the authoritative, stable contracts. Task commands are a convenience
  layer and never a second, weaker definition of any of them.

## See also

- [Activation lifecycle](activation-lifecycle.md) — invocation, freshness, and authority boundaries.
- [Outcome envelope](outcome-envelope.md) — the versioned finding and action sidecar.
- [Changed-path guidance impact](impact.md) — the routing primitive behind `review-changes`.
- [Adoption status and coverage](adoption.md) — the repository-state primitive behind every command.
