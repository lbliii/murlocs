# Guidance efficiency evaluation

`murlocs check` verifies deterministic structure, safety, drift, proofs, coverage, and context
bytes. It does not measure whether a guidance network actually helps a coding agent find the right
architecture with less search and fewer actions. The optional `murlocs.eval` harness adds a
repeatable methodology for that question. It is deliberately **separate from the deterministic
core**: `compile` and `check` never import it, and it never executes registered commands or mutates
a repository.

## What it compares

For one task on one repository revision, the harness compares three guidance arms:

- **no-guidance** — the agent works with no repository guidance;
- **inline-dump** — the agent is given one large, unscoped guidance blob;
- **murlocs** — the agent is given the compiled, scoped root-to-target `AGENTS.md` chain.

## How it scores

The harness scores **recorded runs**, not a live model. Each run supplies the agent's final answer
and evidence (files inspected, lines inspected, tool calls, executable steps, and the guidance text
placed in front of it). This keeps results auditable and reproducible.

Correctness comes first. A task defines objectively checkable expected facts; a run passes only if
its answer satisfies the correctness threshold. **A run cannot receive an efficiency score unless it
first meets that threshold** — a cheaper but wrong arm never wins.

Efficiency, reported only for correct runs, covers files inspected, lines inspected, tool calls,
executable steps, active guidance bytes, and an estimated prompt-token count (`bytes / 4`). Every
metric ships with a definition, and each result records the model, ADE, prompt, repository revision,
guidance revision, and metric definitions. Raw evidence is preserved alongside a compact comparison
summary.

## Running it

```bash
# Explicitly run the bundled demonstration.
python -m murlocs.eval --demo
python -m murlocs.eval --demo --output ./eval-results

# Score records captured during a repository pilot.
python -m murlocs.eval \
  --task ./evaluation/import-graph.toml \
  --runs ./evaluation/import-graph-runs.json \
  --output ./eval-results

# Join curation history to pinned before/after recorded runs.
python -m murlocs.eval \
  --longitudinal ./evaluation/curation-series.json \
  --output ./eval-results
```

The bundled `import-graph` task ships with a small fixture repository whose import graph is
objectively checkable. The demonstration is never selected implicitly: production use must provide
both `--task` and `--runs`. `--output` is optional; without it the command prints the comparison but
does not write a result. Result writes atomically replace their output-directory entry instead of
following an existing symlink or hardlink, so a linked file outside that directory is not modified.

## Version 1 ingestion format

Task definitions are TOML. They pin the prompt and repository revision and define objective facts
that the answer must contain. Fact alternatives are matched case-insensitively; the threshold is a
fraction greater than `0` and at most `1`. A task id is also its result filename, so version 1
restricts it to
1–128 ASCII letters, digits, dots, underscores, or hyphens; it must begin and end with a letter or
digit and cannot contain `..`. At least one expected fact and a positive threshold are required;
neither an empty fact set nor a zero threshold can make an otherwise wrong recorded answer pass
correctness vacuously.

```toml
schema_version = 1
id = "import-graph"
prompt = "Which internal modules does src/app/service.py depend on?"
target_path = "src/app/service.py"
repository_revision = "a1b2c3d"
correctness_threshold = 1.0

[[expected_facts]]
id = "depends-on-render"
description = "service.py imports the render module."
any_of = ["app.render", "render.py"]
```

Recorded runs are JSON. The top-level task id and repository revision must match the task file.
Version 1 requires exactly one record for each supported arm: `no-guidance`, `inline-dump`, and
`murlocs`. Counts must be non-negative integers. `guidance_revision` should be `none`, the inline
dump's content revision, or the Murlocs lock/content revision as appropriate.

```json
{
  "schema_version": 1,
  "task_id": "import-graph",
  "repository_revision": "a1b2c3d",
  "runs": [
    {
      "arm": "no-guidance",
      "model": "model-name-and-version",
      "ade": "agent-environment-and-version",
      "guidance_revision": "none",
      "answer": "The final recorded answer.",
      "guidance_text": "",
      "evidence": {
        "files_inspected": 8,
        "lines_inspected": 420,
        "tool_calls": 15,
        "executable_steps": 31,
        "transcript": ["search imports", "read src/app/service.py"]
      }
    }
  ]
}
```

The abbreviated example shows one record; a valid file contains all three arms. Duplicate or
unknown arms, missing fields, unsupported schema versions, negative counts, and task or revision
mismatches fail with an actionable error before any output is written. Unknown fields are rejected
so schema evolution remains explicit through `schema_version`.

## Longitudinal curation outcomes

`--longitudinal` accepts a versioned JSON link manifest. It references checked-in curation TOML
records and ordinary task/run files instead of copying or weakening either schema. Each proposal
link supplies facts that curation schema version 1 does not store: repository and compiled-guidance
revisions, affected scope/chain snapshots, and active bytes for each chain. Source revisions come
from the lifecycle and must match the record's base and apply-event hashes.

When a curation record has `target_scope`, that scope must appear in the supplied affected-chain
snapshots. Other affected scopes and chain identities are versioned evidence supplied by the pilot,
not independently reconstructed truth: schema version 1 does not retain the historical manifest
checkout needed to recompute them. The evaluator does cross-check each snapshot's exact byte count
against the linked Murlocs run's guidance text and preserves the supplied evidence for review.

Every observation names a proposal, `before` or `after` phase, affected scope, exact guidance
chain, source revision, task file, and recorded-run file. Murlocs rejects the series before
producing a summary when:

- a proposal, related supersession proposal, before/after observation, or referenced file is
  missing;
- a proposal or observation identity is duplicated and therefore ambiguous;
- the source, repository, or Murlocs guidance revision differs from the selected lifecycle phase;
- an observation relabels a scope or chain not declared by that proposal;
- before/after task definitions, models, or agent environments are incompatible; or
- the Murlocs run's exact guidance bytes differ from the affected-chain byte snapshot.

Version 1 is deliberately a **single-source linear series**, not a general revision graph. Applied
proposals are ordered by their apply-event times. Repository, source, and guidance revisions must
connect exactly from each after boundary to the next before boundary. Byte counts for every
overlapping affected scope/chain must also connect. Equal apply times, cross-source series, branch
joins, disconnected histories, and byte jumps are ambiguous and rejected instead of being added
into a misleading cumulative total.

A supersession is one transaction: the old proposal's terminal `superseded` event and the new
replacement's `promoted` event must link each other and agree on subject/source digests, timestamp,
actor, rationale, and review reference. Because the replacement apply event is bound to the
declared revision boundary, this also binds the old terminal audit to that same boundary.

Rejected, withdrawn, proposed, and accepted-but-unapplied records do not advance the history or add
an active-byte delta. Their before revisions must match the active boundary at their proposal time.
A canonical recorded-run evidence fingerprint is attributed once by default, so path aliases,
hardlinks, or copied JSON cannot double-count one snapshot. The only reuse allowed is the identical
after snapshot of one applied proposal as the before snapshot of the immediately adjacent applied
proposal, with the same revision, task, scope, and chain. Result evidence retains both run-only and
task-plus-run fingerprints.

References are safe paths relative to the link manifest. Absolute paths, parent traversal,
symlinks, unknown fields, and unsupported versions are rejected. Loading and analysis are
read-only. As with single-revision evaluation, `--output` is an explicit request to write only the
deterministic result artifact.

An abbreviated proposal and observation look like:

```json
{
  "schema_version": 1,
  "series_id": "curation-pilot-1",
  "proposals": [
    {
      "record": "curation/replace-core-rule.toml",
      "revisions": {
        "repository_before": "repo-a",
        "repository_after": "repo-b",
        "source_before": "<record base source sha256>",
        "source_after": "<apply-event source sha256>",
        "guidance_before": "lock-a",
        "guidance_after": "lock-b"
      },
      "affected_chains": [
        {
          "scope": "core",
          "chain": ["root", "core"],
          "active_bytes_before": 4200,
          "active_bytes_after": 3980
        }
      ]
    }
  ],
  "observations": [
    {
      "proposal_id": "replace-core-rule",
      "phase": "before",
      "scope": "core",
      "chain": ["root", "core"],
      "source_revision": "<record base source sha256>",
      "task": "tasks/import-before.toml",
      "runs": "runs/import-before.json"
    }
  ]
}
```

Applied proposals require matching before and after observations for every task/scope/chain
identity. Rejected, withdrawn, proposed, or accepted-but-unapplied records require a before
observation and cannot claim an after promotion revision.

The deterministic result distinguishes proposal state and intent, accepted proposals, applied
additions and replacements, supersessions, rejections, and pruning. Its active-byte timeline shows
per-chain before/after/delta snapshots, acceptance rate, replacement-to-addition ratio, and seconds
from proposal to the first acceptance, rejection, or withdrawal. RFC 3339 event times with offsets
are required so decision latency is portable.

Before/after search and action deltas are emitted only when both Murlocs runs pass the same task's
correctness threshold. If either side fails, both sides' efficiency values, every efficiency delta,
and all efficiency aggregates are withheld from the comparison. Raw task definitions, run records,
transcripts, lifecycle events, revisions, affected chains, and byte snapshots remain in
`raw_evidence`, with metric definitions beside them, so reviewers can independently reproduce the
summary.

## Pilot workflow

1. Pin the repository revision and write a task with facts that can be verified without model
   judgment.
2. Run the same task with the same model and agent environment under each arm. Run collection stays
   outside Murlocs; do not ask the scoring command to invoke an agent.
3. Record the final answer, exact active guidance, guidance revision, search/action counts, and an
   audit transcript in the JSON file.
4. Run `python -m murlocs.eval --task ... --runs ... --output ...` and review the JSON artifact.

The result uses `schema_version = 1` and preserves the complete task and recorded runs alongside
per-arm correctness, gated efficiency scores, aggregate winner, and metric definitions. JSON keys
are sorted so identical inputs produce byte-identical output.

## What the measurements can and cannot establish

They **can** show, for a specific task, revision, and model, how much search and how many actions
each guidance arm required, and whether each arm reached a correct answer. Alongside active guidance
bytes and estimated tokens, that makes the search-versus-context trade-off concrete instead of
anecdotal.

They **cannot** establish that guidance improves correctness in general, that one arm is better
across tasks or models, or that lower cost is always preferable when it sacrifices coverage. The
token estimate is a heuristic, not a billed count, and a single fixture is an existence proof, not a
benchmark. Treat results as evidence about a concrete case, and grow the task set before drawing
broad conclusions.

Longitudinal correlation is not a causal claim. A better recorded outcome after a proposal does not
show that the proposal caused the change, will help another task, model, or repository, or should be
promoted elsewhere. Evaluation remains evidence for owner judgment and never accepts, promotes,
supersedes, or prunes a proposal automatically. The evaluator is not imported by `compile`,
`check`, or curation apply operations, does not invoke a model, and does not execute registered
checks.
