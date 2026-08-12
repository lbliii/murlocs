# Guidance-friction observation schema

Status: schema and deterministic validation for [#132](https://github.com/lbliii/murlocs/issues/132).
Draft generation (`murlocs friction draft`, [#133](https://github.com/lbliii/murlocs/issues/133))
is intentionally out of scope here.

Agents can notice missing or harmful guidance, but free-form reflection is hard to review,
aggregate, or safely convert into a governed curation proposal. This document defines a
**versioned inert observation**: a reviewable record of guidance friction that never enters the
active manifest graph and never authenticates a decision.

```text
agent or human notices friction
        |
        v
.murlocs/friction/<id>.toml   (inert observation; schema v1)
        |
        +--> deterministic analysis (duplication, scope, stability,
        |    evidence gap, projected context cost)
        |
        v
optional later: explicit CLI write / curation propose  (#133+)
        |
        v
.murlocs/curation/<proposal-id>.toml  (still inert until owner acceptance)
```

Only an accepted curation apply mutates active sources. An observation is never an implicit
manifest fragment, layer, overlay, generated-map input, curation proposal, or authenticated
decision.

## Decision summary

- Store one versioned TOML (or equivalent structured) record per observation under
  `.murlocs/friction/`.
- Fix `record_kind = "observation"` so parsers can refuse proposal- or decision-shaped payloads.
- Capture path/scope, signal type, evidence references, bounded observed task cost, provenance, and
  an optional proposed resolution hint.
- Keep the record free of raw prompts, hidden reasoning, transcripts, and source-content capture.
- Reject unknown fields and unsupported schema versions visibly.
- Reject absolute paths, traversal (`..`), symlinks, and other unsafe references.
- Provide deterministic analysis helpers for duplication, scope, stability, evidence gaps, and
  projected context-byte cost. Analysis is advisory evidence only; it never writes guidance.

## Storage and schema

The initial format is `friction_schema_version = 1`. Repositories need no manifest migration and do
not need a `[friction]` section. Compilers continue to load only the root manifest and registered
layers, so an older Murlocs version cannot activate an observation.

An illustrative record is:

```toml
friction_schema_version = 1
record_kind = "observation"
id = "core-missing-path-rule"
signal = "missing" # missing, misleading, conflicting, repetitive, or overly_broad
path = "src/murlocs/paths.py"
scope = "core"
guidance_refs = [".murlocs/layers/core.toml"]
summary = "No operating rule names the shared repository path resolver."

[[evidence]]
kind = "file_anchor" # file_anchor, command, issue, pull_request, evaluation, or note
reference = "src/murlocs/paths.py#repo_path"
summary = "The shared resolver already enforces confinement."

[observed_cost]
metric = "active_context_bytes" # active_context_bytes, tool_calls, or files_inspected
value = 18432
bound = 24576

[provenance]
observer = "@contributor"
origin = "issue-132"
observed_at = "2026-08-12T15:00:00Z"

[proposed_resolution]
summary = "Add an operating rule that names repo_path for manifest-controlled paths."
intent_hint = "add" # add, replace, or remove
subject_kind_hint = "operating_rule"
```

`observer`, `origin`, and timestamps are unauthenticated audit attribution. They do not prove
identity, approval, or owner review. Git review, branch protection, and CODEOWNERS remain the
authority for any later decision. A friction observation must not carry acceptance, promotion, or
authenticated-decision fields; those belong to curation events after an explicit proposal.

### Required metadata

Every observation records:

- a repository-unique, path-safe `id` and `friction_schema_version`;
- `record_kind` fixed to `observation`;
- `signal`: `missing`, `misleading`, `conflicting`, `repetitive`, or `overly_broad`;
- a repository-relative `path` and optional guidance `scope` plus optional `guidance_refs`;
- a bounded human-readable `summary` (no embedded source prose requirement);
- at least one evidence item with `kind`, `reference`, and `summary`;
- `observed_cost` with a closed `metric`, non-negative `value`, and optional `bound`;
- `provenance` with `observer`, `origin`, and `observed_at`; and
- optional `proposed_resolution` with a summary and optional curation intent/subject hints.

### Distinctions

| Artifact | Authority | May mutate active guidance? |
| --- | --- | --- |
| Friction observation (`record_kind=observation`) | None; inert review evidence | No |
| Curation proposal (`.murlocs/curation/`) | Owner-attributed lifecycle after propose | Only after explicit accept + apply |
| Authenticated decision | External review / CODEOWNERS / branch protection | Outside Murlocs' local claim |

Parsers refuse observation payloads that look like curation proposals or decisions (for example
`intent`, `events`, `required_owners`, or `record_kind` other than `observation`) by unknown-field
or kind checks. Curation never reads `.murlocs/friction/` as a proposal source.

### Path and reference safety

All path-like fields (`path`, `guidance_refs`, evidence `file_anchor` references before `#`) must be
repository-relative. Absolute paths, `..` traversal, symlink traversal, and empty segments are
rejected. Validation binds checks to a repository root when one is supplied; syntactic rejection
happens even without filesystem access.

### Privacy and content bounds

The schema does not require and does not permit fields for:

- raw prompts or tool argument dumps;
- hidden reasoning / chain-of-thought; or
- verbatim source-content capture.

Summaries and evidence summaries are bounded strings. Evidence should point at governed artifacts
(issue ids, file anchors, evaluation ids) rather than embedding private transcripts.

## Deterministic analysis

Given one or more parsed observations (and an optional repository root), deterministic code may
report:

- **duplication** — exact matches on normalized signal, path, scope, and summary among peers, plus
  duplicate ids;
- **scope** — the addressed scope id and whether it is well-formed / present when a manifest is
  available;
- **stability** — whether referenced paths and guidance sources still exist under the repository
  root without following unsafe links;
- **evidence gap** — missing evidence, note-only evidence for structural signals, or empty/unsafe
  references; and
- **projected context cost** — the recorded `observed_cost` relative to its optional `bound`, plus a
  bounded projection derived from an optional resolution hint length (never a model call).

These findings are advisory. They do not authorize writes, curation promotion, or compile inputs.

## Compatibility and non-goals

- Repositories without `.murlocs/friction/` behave exactly as they do today.
- Schema version 1 is closed: unknown fields and other versions fail visibly.
- This issue does not implement draft generation, MCP/CLI draft surfaces, or auto-apply.
- Observations remain outside runtime agent context unless an explicit future command opts in.

## Verification

- Python model and validator: `src/murlocs/friction.py`
- Fixture corpus: `tests/fixtures/guidance-friction/v1/`
- Tests: `tests/test_friction.py` marked `@pytest.mark.issue(132)`
