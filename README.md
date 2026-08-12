# Murlocs

[![PyPI](https://img.shields.io/pypi/v/murlocs.svg)](https://pypi.org/project/murlocs/)
[![CI](https://github.com/lbliii/murlocs/actions/workflows/ci.yml/badge.svg)](https://github.com/lbliii/murlocs/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.14+-blue.svg)](https://pypi.org/project/murlocs/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/lbliii/murlocs/blob/main/LICENSE)

![An illustrated cavern map connecting layered repository paths to one central field map.](https://raw.githubusercontent.com/lbliii/murlocs/main/docs/assets/murlocs-field-map-hero.webp)

**Give every coding agent the right map—and prove the map is still current.**

Murlocs turns your repository’s architecture and rules into layered `AGENTS.md` files, then checks
that they stay in sync.

## Your repository is a place. Agents need a map.

Useful context lives in source trees, old documents, CI recipes, review habits, and the people who
remember why a boundary exists. A long prompt describes some of it once; it cannot reliably say what
applies to *this path*, connect two areas, or prove the guidance matches its reviewed source.

Murlocs makes that knowledge local, versioned, and reviewable:

- one source of truth in `.murlocs/manifest.toml`, with optional owner-focused layers;
- familiar `AGENTS.md` maps at the root and where local rules differ;
- explicit invariants, proof references, cross-scope edges, and context budgets;
- checks for schema, ownership, drift, coverage, and declared evidence; and
- a safe path for repositories with existing guidance.

It is map infrastructure, not a hosted service or replacement for engineering judgment.

## Start in a minute

Murlocs requires Python 3.14+. [Milo](https://github.com/lbliii/milo-cli) powers its CLI, MCP, and
agent discovery. Install:

```bash
python -m pip install -e .

murlocs init --name "My Repository"
# Describe the repository you actually have in .murlocs/manifest.toml.
murlocs compile
murlocs check

# Ask what applies before working in a path or reviewing a change.
murlocs explain src/my_package/feature.py
murlocs impact --path src/my_package/feature.py
```

`mrr` is the same CLI.

## From generic instruction to a working map

| Before | With Murlocs |
| --- | --- |
| A single prompt or a pile of hand-maintained guidance files | A scoped, layered network of standard `AGENTS.md` files |
| Agents guess which rules apply to a file | `explain PATH` shows the ordered map, invariants, provenance, overrides, and budget trace |
| Reviewers rediscover what changed at a boundary | `impact` shows which guidance needs review for paths or a Git revision range |
| “Keep docs current” is a hope | `check` detects drift, ownership conflicts, missing proofs, and configured coverage gaps |
| Existing guidance is overwritten during a migration | Inventory, candidate import, explicit adoption, backup, and rollback keep the handoff recoverable |

## The core loop

```text
describe the repository  →  compile local maps  →  ask the map at work time  →  verify the map
       manifest/layers        AGENTS.md files        explain + impact            check
```

## What Murlocs verifies—and what it does not

Murlocs checks its manifest and graph, proof links, map ownership, generated-file drift, budgets,
and configured coverage. Command-backed invariants name registered checks; manual claims point to
textual evidence.

It does **not** run registered commands during `murlocs check`, decide a change is correct,
authenticate an actor, set merge policy, or turn an assertion into proof. A human or agent still
runs repository checks. No coverage roots means coverage is unconfigured—not evaluated.

## Use it when the codebase has more context than one prompt can hold

Murlocs fits when agents and contributors need to know:

- which rules apply to a directory;
- who owns a boundary;
- whether generated maps are current after a refactor; or
- how to add guidance without overwriting existing files.

For a tiny, stable project with one obvious convention, a concise hand-written `AGENTS.md` may be
enough. Murlocs earns its place as scopes, owners, and durable claims grow.

## Key workflows

### Author a map that reflects the real repository

Start with a root map. Split only where ownership or rules need a local scope. One manifest is
enough; `base`, `domain`, and `overlay` layers compose the same model. See [layered authoring](https://github.com/lbliii/murlocs/blob/main/docs/layers.md).

```bash
murlocs init --coverage-root src --coverage-root tests
murlocs add-scope src/my_package
murlocs compile
murlocs check
```

Coverage roots are inferred from the repository layout during `init` — typically `src/` and other
top-level directories that contain source files. Pass `--coverage-root` explicitly to override
inference or add additional roots.

### Stamp backlog-truth tracking (opt-in)

```bash
murlocs scaffold backlog-truth
murlocs scaffold status
```

Stamps Saga/Epic/Investigation/Task issue templates, label taxonomy, closure-gate and reconcile
workflow stubs, conventions, and process harness docs (`docs/plan/…`, outside compile). Pieces are
individually adoptable with `--only`. See [backlog-truth](https://github.com/lbliii/murlocs/blob/main/docs/backlog-truth.md).

### Understand a task before changing code

```bash
murlocs explain src/my_package/feature.py
murlocs impact --path src/my_package/feature.py --path docs/feature.md
```

`explain` returns the scope chain and its rules. `impact` shows what a changed path needs reviewed,
without starting an agent or running repository commands. See [changed-path impact](https://github.com/lbliii/murlocs/blob/main/docs/impact.md).

### Or use the task-shaped front door

```bash
murlocs orient src/my_package/feature.py
murlocs review-changes --path src/my_package/feature.py --path docs/feature.md
murlocs finish --staged
```

`orient` before unfamiliar work, `review-changes` over an explicit path or Git view, and `finish`
for a fresh completion receipt. These read-only commands compose `check`, `impact`, adoption, and
curation validation into one classified, freshness-bound envelope; they never execute a registered
check or change repository state. See
[intent-shaped task commands](https://github.com/lbliii/murlocs/blob/main/docs/task-commands.md).

### Change safely and repair only ordinary drift

```bash
murlocs --dry-run compile
murlocs check
murlocs --dry-run repair
murlocs repair
```

`compile` renders managed maps and updates the lockfile. After a preflighted plan, `repair` writes
only those maps and the lockfile. It refuses semantic findings, unmanaged output, and edited maps;
those need owner or agent work.

### Migrate deliberately; never silently take ownership

```bash
murlocs inventory
murlocs diff --mode semantic
murlocs import --from stewards --output .murlocs/manifest.toml
murlocs --dry-run adopt
murlocs adopt
murlocs prune
```

`init` never overwrites an existing `AGENTS.md`; `import` creates a candidate but never adopts it.
`adopt` accepts only byte-current legacy-generated maps and saves exact backups; `prune` moves
legacy tooling into that backup. `rollback` restores the pre-adoption network until maps are edited.
User-owned files such as `CLAUDE.md` are inventoried, never changed. See [primary user journeys](https://github.com/lbliii/murlocs/blob/main/docs/journeys.md).

## Integrations that preserve the boundary

Murlocs keeps `AGENTS.md` files portable and local-first. `murlocs --mcp` offers nine read-only
tools: `version`, `inventory`, `status`, `diff`, `check`, `explain`, `impact`, `curate review`, and
`curate check`. Writes stay CLI-only. `murlocs --llms-txt`, JSON, and typed dispatch support
discovery.

The [GitHub Copilot](https://github.com/lbliii/murlocs/blob/main/docs/github-copilot-adapter.md) and
[Claude Code](https://github.com/lbliii/murlocs/blob/main/docs/claude-code-adapter.md) adapters share the read-only lifecycle contract; the
[conformance harness](https://github.com/lbliii/murlocs/blob/main/docs/adapter-conformance.md) tests it. The
[host capability matrix](https://github.com/lbliii/murlocs/blob/main/docs/host-capability-matrix.md)
records native/adapted/tool-only/unknown tiers with evidence-gated defaults. Optional
[passive Git hooks](https://github.com/lbliii/murlocs/blob/main/docs/git-hooks.md) run checks at commit and push, stay quiet when healthy, and
do not replace existing managers or custom hook paths. See the
[activation lifecycle](https://github.com/lbliii/murlocs/blob/main/docs/activation-lifecycle.md) and [outcome envelope](https://github.com/lbliii/murlocs/blob/main/docs/outcome-envelope.md)
for portable receipts; integration output never sets merge policy.

## Runtime identity

`murlocs version --format json` reports a redacted build and installation identity for
integrations. See [runtime build identity](https://github.com/lbliii/murlocs/blob/main/docs/runtime-identity.md) for the contract and publisher
boundary.

## Find the right detail

| If you need to… | Start here |
| --- | --- |
| Understand concepts, trust boundaries, and compilation | [Architecture](https://github.com/lbliii/murlocs/blob/main/docs/architecture.md) |
| Adopt guidance progressively and configure coverage | [Adoption and coverage](https://github.com/lbliii/murlocs/blob/main/docs/adoption.md) |
| Plan bootstrap, rollout, migration, repair, or evaluation | [Primary user journeys](https://github.com/lbliii/murlocs/blob/main/docs/journeys.md) |
| Connect hosts, hooks, or CI | [Host capability matrix](https://github.com/lbliii/murlocs/blob/main/docs/host-capability-matrix.md), [Activation lifecycle](https://github.com/lbliii/murlocs/blob/main/docs/activation-lifecycle.md), and [Git hooks](https://github.com/lbliii/murlocs/blob/main/docs/git-hooks.md) |
| Govern proposals before changing live guidance | [Curation](https://github.com/lbliii/murlocs/blob/main/docs/curation.md) |
| Capture inert guidance-friction observations | [Guidance friction](https://github.com/lbliii/murlocs/blob/main/docs/guidance-friction.md) |
| Measure whether guidance improves recorded agent work | [Guidance efficiency evaluation](https://github.com/lbliii/murlocs/blob/main/docs/evaluation.md) |

## Project status and development

Murlocs is an experimental v0.1 implementation. The manifest schema and generated format may
change before the first stable release.

Development checks are intentionally separate from guidance verification:

```bash
ruff check .
pytest
milo verify src/murlocs/cli.py
murlocs check
murlocs impact --path README.md
```

The optional `bootstrap-murlocs` skill inventories a repository and drafts a truthful manifest
without treating hand-authored guidance as safely importable.
