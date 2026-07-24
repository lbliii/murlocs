# Murlocs

**Raise the buried knowledge of your repository. Give it a chorus.**

Murlocs turns repository architecture and operating knowledge into a small, layered network of
`AGENTS.md` files that coding agents can actually follow—and that CI can verify. Each scoped map
speaks for one part of the codebase; the network explains how those parts depend on one another.

## Why a tool?

A prompt can help author guidance once. Murlocs owns the repeatable infrastructure around it:

- one versioned `.murlocs/manifest.toml` as the source of truth;
- deterministic compilation into standard, tool-agnostic `AGENTS.md` files;
- explicit scopes, cross-scope edges, invariants, proofs, and context budgets;
- coverage checks for source-bearing units;
- drift detection and safe ownership through `.murlocs/lock.json`;
- path-specific explanations without starting an agent or executing repository commands.

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
# Preview a write without changing files.
murlocs --dry-run compile
```

Murlocs also installs `mrr` as a short alias. It exposes the same commands and surfaces, so
`mrr check` is equivalent to `murlocs check` while its help and usage text retain the shorter name.

`murlocs init` refuses to overwrite an existing `AGENTS.md`. Migration is deliberately explicit:
read the existing guidance, represent it in the manifest, and only then hand ownership to Murlocs.

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
| `murlocs inventory` | Find guidance files, generators, proof debt, and ownership conflicts. |
| `murlocs import` | Translate legacy guidance into candidate TOML without adopting maps. |
| `murlocs diff` | Show semantic migration facts and rendered map patches. |
| `murlocs adopt` | Replace byte-current legacy maps after recoverable backup. |
| `murlocs prune` | Move legacy tooling into the active migration backup. |
| `murlocs rollback` | Restore the exact pre-adoption guidance network. |
| `murlocs check` | Validate schema, graph, proofs, coverage, budget, ownership, and drift. |
| `murlocs explain PATH` | Print the ordered scope, invariant, layer provenance, override, and budget trace for a path. |

Milo also provides `murlocs --mcp` (or `mrr --mcp`), `murlocs --llms-txt`, structured JSON output,
shell completions, and in-process typed dispatch. Only read-only `inventory`, `diff`, `check`, and
`explain` are agent-visible.

See [Architecture](docs/architecture.md) for trust boundaries and [Roadmap](docs/roadmap.md) for
the planned migration and ecosystem work.

## Status

Murlocs is an experimental v0.1 implementation. The manifest schema and generated format may change
before the first stable release.
