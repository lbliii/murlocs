# Murlocs

**Raise the buried knowledge of your repository. Give it a chorus.**

Murlocs turns repository architecture and operating knowledge into a small, layered network of
`AGENTS.md` files that coding agents can actually follow—and that CI can verify. Each scoped map
speaks for one part of the codebase; the network explains how those parts depend on one another.

## Why a tool?

A prompt can help author guidance once. Murlocs owns the repeatable infrastructure around it:

- one versioned `.murlocs/manifest.toml` control plane with optional owner-focused layer sources;
- deterministic compilation into standard, tool-agnostic `AGENTS.md` files;
- explicit scopes, cross-scope edges, invariants, proofs, and context budgets;
- coverage checks for source-bearing units;
- drift detection and safe ownership through `.murlocs/lock.json`;
- path-specific explanations and changed-path review routing without starting an agent or
  executing repository commands.

The [portable agent activation lifecycle](docs/activation-lifecycle.md) defines how hosts,
generated guidance, Git hooks, and CI discover `.murlocs/manifest.toml` and invoke those read-only
operations at task boundaries. Its completion gate requires fresh integration-produced repository
evidence; an agent assertion alone is never a passing receipt.

Structured `check` and `impact` results include a versioned, read-only
[outcome envelope](docs/outcome-envelope.md) with stable findings and closed, typed routing actions.
It is additive: existing payload fields and exit codes remain unchanged, and review routing does
not become merge policy.

The Milo-backed CLI is deterministic infrastructure. The bundled `bootstrap-murlocs` skill is the
optional agent-assisted authoring layer for discovering architecture and drafting a truthful
manifest. Read-only commands are available as MCP tools and in `llms.txt`; write commands remain
CLI-only by default.

## Quick start

Murlocs requires Python 3.14 or newer and uses
[Milo](https://github.com/lbliii/milo-cli) for typed CLI, MCP, and agent-discovery surfaces.

```bash
python -m pip install -e .
murlocs init --name "My Repository"
# Edit .murlocs/manifest.toml to describe the actual repository.
murlocs compile
murlocs check
murlocs explain src/my_package/feature.py
murlocs impact --path src/my_package/feature.py
murlocs curate check
# Preview a write without changing files.
murlocs --dry-run compile
```

Murlocs also installs `mrr` as a short alias. It exposes the same commands and surfaces, so
`mrr check` is equivalent to `murlocs check` while its help and usage text retain the shorter name.

`murlocs init` refuses to overwrite an existing `AGENTS.md`. Migration is deliberately explicit:
read the existing guidance, represent it in the manifest, and only then hand ownership to Murlocs.

The bundled `bootstrap-murlocs` skill inventories the repository before writing and selects one of
four routes: greenfield initialization, recognized steward migration, review of unmanaged guidance,
or maintenance of an existing Murlocs network. It never treats hand-authored guidance as safely
importable or bypasses generated-file ownership checks.

The starter manifest leaves source coverage unconfigured for compatibility. The init and check
results say so explicitly: a passing check with no declared coverage roots did not evaluate source
coverage. To opt in during initialization, repeat `--coverage-root` as needed:

```bash
murlocs init --coverage-root src --coverage-root tests
```

Initialization records exactly those roots; it does not infer them. If a declared root contains
source-bearing units without local maps or reasoned exemptions, initialization still creates the
starter network and reports `structurally_incomplete`. Resolve those findings with reviewed scopes
or exemptions before the next compile. Existing manifests with `roots = []` remain valid and are
reported as `unconfigured` rather than failed.

## Migrating a legacy steward network

Migration separates inspection, candidate creation, ownership transfer, and cleanup:

```bash
murlocs inventory
murlocs diff --mode semantic
murlocs import --from stewards --output .murlocs/manifest.toml
# Review the candidate and resolve every proof-debt finding.
murlocs --dry-run adopt
murlocs adopt
murlocs prune
```

`import` never adopts existing maps. `adopt` accepts only byte-current legacy-generated maps and
stores their exact contents under `.murlocs/backups/` before replacement. `prune` moves the legacy
`.stewards` directory into that backup. Until adopted maps are edited, `murlocs rollback` restores
the pre-adoption instruction network byte-for-byte. User-owned files such as `CLAUDE.md` are only
inventoried and are never changed by this workflow.

## The model

The manifest uses plain infrastructure terms even though the project has a mythological name:

- **scope**: a repository region with one generated map;
- **edge**: a typed dependency or coordination boundary between scopes;
- **invariant**: a claim that must remain true;
- **check**: a command registration whose location and identifying proof are verified;
- **coverage**: source-bearing units that require either a map or a reasoned exemption.

An invariant is `command`, `manual`, or `unknown`. Murlocs validates that command-backed claims name
a registered check and that manual claims point to real textual evidence. It never executes a
registered command during `check`; command execution remains an explicit human or agent decision.

## Layered authoring

A single manifest is the simplest surface. As a network grows, the root manifest can declare an
ordered set of owner-focused layer files (`base`, `domain`, and `overlay` kinds) that compose
deterministically into the same canonical model. Single-file manifests keep working unchanged. See
[Layered manifests](docs/layers.md) for the schema and merge contract.

## Commands

| Command | Purpose |
| --- | --- |
| `murlocs init` | Create a starter manifest and protocol, then compile the first root map. |
| `murlocs compile` | Render managed maps and update the content-addressed lockfile. |
| `murlocs add-scope PATH` | Introduce a scoped guidance layer for a selected directory. |
| `murlocs inventory` | Find guidance files, generators, proof debt, and ownership conflicts. |
| `murlocs status` | Classify repository adoption state from concrete evidence and recommend safe next actions. |
| `murlocs import` | Translate legacy guidance into candidate TOML without adopting maps. |
| `murlocs diff` | Show semantic migration facts and rendered map patches. |
| `murlocs adopt` | Replace byte-current legacy maps after recoverable backup. |
| `murlocs prune` | Move legacy tooling into the active migration backup. |
| `murlocs rollback` | Restore the exact pre-adoption guidance network. |
| `murlocs check` | Validate schema, graph, proofs, coverage, budget, ownership, and drift. |
| `murlocs explain PATH` | Print the ordered scope, invariant, layer provenance, override, and budget trace for a path. |
| `murlocs impact` | Classify guidance review impact for repeated `--path` values and/or a Git `--revision-range`. |
| `murlocs curate propose ID` | Create a versioned inert proposal under `.murlocs/curation/`; never edit active guidance. |
| `murlocs curate review ID` | Report the deterministic prospective change, ownership, conflicts, affected chains, and budget delta. |
| `murlocs curate check` | Validate every inert proposal and its current target without changing files. |
| `murlocs curate accept ID` / `reject ID` / `withdraw ID` | Append an attributed decision without editing active guidance. |
| `murlocs curate promote ID` | Transactionally apply an accepted addition or replacement to its exact active source. |
| `murlocs curate supersede OLD --with NEW` | Apply an accepted replacement and link both lifecycle records. |
| `murlocs curate prune ID` | Transactionally apply an accepted removal. |
| `murlocs curate recover ID [--with ID]` | Explicitly preview or recover one exact interrupted apply; crash journals are never trusted automatically. |

Milo also provides `murlocs --mcp` (or `mrr --mcp`), `murlocs --llms-txt`, structured JSON output,
shell completions, and in-process typed dispatch. Only read-only `status`, `inventory`, `diff`,
`check`, `explain`, `impact`, `curate review`, and `curate check` are agent-visible. Every curation
write remains CLI-only and supports `--dry-run`. Actor values are audit attribution, not identities
authenticated by Murlocs. Curation owner routing follows every prospectively changed guidance chain;
`target_scope` addresses a subject but does not confine root-level list guidance. See
[Adoption status and coverage](docs/adoption.md) for the lifecycle contract and
[Changed-path impact](docs/impact.md) for the review policy, stable output, and CI and pre-commit
examples.

The optional, separate `murlocs.eval` harness measures whether scoped guidance actually helps agents
search less while staying correct. It ingests versioned task and recorded-run files with
`python -m murlocs.eval --task TASK.toml --runs RUNS.json`; the bundled data is available only via
the explicit `--demo` flag. See [Guidance efficiency evaluation](docs/evaluation.md); it never runs
as part of `compile` or `check` and never invokes a model.

The [synthetic 91-map scale pilot](docs/pilots/scale-network.md) records deterministic compilation,
ownership and failure behavior, focused review fan-out, measured runtime and memory, and a
correctness-gated scripted evaluation without copying guidance from a private repository.

For release-level recipes spanning bootstrap, progressive rollout, migration and rollback,
ordinary drift repair, and recorded evaluation, see [Primary user journeys](docs/journeys.md).

For governed curation experiments, `python -m murlocs.eval --longitudinal SERIES.json` joins actual
curation records to revision-pinned before/after runs. It reports lifecycle, guidance-growth, and
correctness-gated search/action trends while preserving raw evidence. These are correlations for
owner review, never causal proof or authority to promote guidance.

The [governed curation workflow](docs/curation.md) lets agents and humans propose additions,
replacements, and removals without allowing unapproved candidates into compiled guidance. The
implemented lifecycle provides inert record creation, deterministic review, current-owner
decisions, and recoverable source-plus-record apply transactions. Apply never runs a model,
registered check, or implicit compile. See [Architecture](docs/architecture.md) for trust boundaries and
[Roadmap](docs/roadmap.md) for planned ecosystem work.

## Status

Murlocs is an experimental v0.1 implementation. The manifest schema and generated format may change
before the first stable release.
