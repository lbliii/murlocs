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
python -m murlocs.eval                 # print a comparison over illustrative recorded runs
python -m murlocs.eval ./eval-results  # also persist the summary and raw evidence as JSON
```

The bundled `import-graph` task ships with a small fixture repository whose import graph is
objectively checkable. Point the harness at your own recorded transcripts to evaluate real runs.

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
