# Kodama

Kodama turns repository architecture and operating knowledge into a small, layered network of
`AGENTS.md` files that coding agents can actually follow—and that CI can verify.

The name comes from the place-bound spirits of Japanese folklore: each map watches over one part
of the codebase, while the network describes how those parts depend on one another.

## Why a tool?

A prompt can help author guidance once. Kodama owns the repeatable infrastructure around it:

- one versioned `.kodama/manifest.toml` as the source of truth;
- deterministic compilation into standard, tool-agnostic `AGENTS.md` files;
- explicit scopes, cross-scope edges, invariants, proofs, and context budgets;
- coverage checks for source-bearing units;
- drift detection and safe ownership through `.kodama/lock.json`;
- path-specific explanations without starting an agent or executing repository commands.

The CLI is deterministic infrastructure. The bundled `bootstrap-kodama` skill is the optional
agent-assisted authoring layer for discovering architecture and drafting a truthful manifest.

## Quick start

Kodama requires Python 3.11 or newer and has no runtime dependencies.

```bash
python -m pip install -e .
kodama init --name "My Repository"
# Edit .kodama/manifest.toml to describe the actual repository.
kodama compile
kodama check
kodama explain src/my_package/feature.py
```

`kodama init` refuses to overwrite an existing `AGENTS.md`. Migration is deliberately explicit:
read the existing guidance, represent it in the manifest, and only then hand ownership to Kodama.

## The model

The manifest uses plain infrastructure terms even though the project has a mythological name:

- **scope**: a repository region with one generated map;
- **edge**: a typed dependency or coordination boundary between scopes;
- **invariant**: a claim that must remain true;
- **check**: a command registration whose location and identifying proof are verified;
- **coverage**: source-bearing units that require either a map or a reasoned exemption.

An invariant is `command`, `manual`, or `unknown`. Kodama validates that command-backed claims name
a registered check and that manual claims point to real textual evidence. It never executes a
registered command during `check`; command execution remains an explicit human or agent decision.

## Commands

| Command | Purpose |
| --- | --- |
| `kodama init` | Create a starter manifest and protocol, then compile the first root map. |
| `kodama compile` | Render managed maps and update the content-addressed lockfile. |
| `kodama check` | Validate schema, graph, proofs, coverage, budget, ownership, and drift. |
| `kodama explain PATH` | Print the ordered scope and invariant chain that governs a path. |

See [Architecture](docs/architecture.md) for trust boundaries and [Roadmap](docs/roadmap.md) for
the planned migration and ecosystem work.

## Status

Kodama is an experimental v0.1 implementation. The manifest schema and generated format may change
before the first stable release.
