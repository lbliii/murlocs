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
```

The bundled `import-graph` task ships with a small fixture repository whose import graph is
objectively checkable. The demonstration is never selected implicitly: production use must provide
both `--task` and `--runs`. `--output` is optional; without it the command prints the comparison but
does not write a result.

## Version 1 ingestion format

Task definitions are TOML. They pin the prompt and repository revision and define objective facts
that the answer must contain. Fact alternatives are matched case-insensitively; the threshold is a
fraction from `0` through `1`. A task id is also its result filename, so version 1 restricts it to
1–128 ASCII letters, digits, dots, underscores, or hyphens; it must begin and end with a letter or
digit and cannot contain `..`.

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
