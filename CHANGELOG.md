# Changelog

All notable changes to Murlocs are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the major version is
`0`, the manifest schema and generated map format may change between minor releases.

## [Unreleased]

### Added

- Intent-shaped read-only task commands `orient`, `review-changes`, and `finish`
  over the existing primitives, on the CLI, MCP, and discovery surfaces. They
  share one versioned `io.murlocs.task` composition envelope
  (`src/murlocs/task_commands.py`, specified in `docs/task-commands.md`) that
  classifies each composite action as blocking, authority-required,
  agent-action, or recommended; makes repository state, the exact Git view,
  correlation, and freshness dependencies explicit; keeps healthy output compact
  and silent-capable; fails visibly on an ambiguous or unavailable change view;
  and rejects a stale pre-edit completion receipt. The commands never execute a
  registered check or mutate repository state, and the granular `status`,
  `explain`, `impact`, and `check` surfaces are unchanged.
- Continuous-integration and release automation: a GitHub Pages docs site built
  from `docs/` with MkDocs Material (`pages.yml`, `mkdocs.yml`), release notes
  synced from `CHANGELOG.md` when a release is published (`release-notes.yml`,
  `scripts/changelog_extract.py`), and a lightweight changelog gate that asks
  behaviour-changing pull requests to update `CHANGELOG.md`, with a
  `skip-changelog` label escape hatch (`changelog.yml`).

### Changed

- Legacy `.stewards` import now accumulates a complete loss report for every
  unsupported field in one pass instead of fail-fasting on the first, and
  `inventory`/`import` name sidecar constructs (extra `.toml` files,
  `archetypes/`, `refs.py`) as explicit out-of-scope entries rather than
  omitting them silently. Invariant `proof_contains` is now translated alongside
  the other proof wiring fields.
- The `Documentation` project URL now points at the published Pages site,
  `https://lbliii.github.io/murlocs/`, instead of a Markdown blob on GitHub.

## [0.1.0] - 2026-08-05

First published release.

### Added

- Deterministic `init`, `compile`, `check`, `explain`, and `add-scope` commands over a versioned
  `.murlocs/manifest.toml` and a content-addressed `.murlocs/lock.json`.
- Scopes, typed edges, invariants, proof wiring, coverage, and active-context budgets.
- Composable `base`, `domain`, and `overlay` manifest layers with ownership and provenance, plus
  optional CODEOWNERS validation.
- Legacy `.stewards` migration: `inventory`, `import`, `diff`, `adopt`, `prune`, and `rollback`,
  with recoverable backups and byte-exact restoration.
- Changed-path impact reporting and deterministic guidance repair.
- Curation workflow for governing proposals before they change live guidance.
- Portable agent activation lifecycle, versioned outcome envelopes, and runtime build identity.
- GitHub Copilot and Claude Code host adapters with a shared conformance harness.
- Optional passive Git hooks that stay quiet when the network is healthy.
- Milo-backed typed CLI with MCP, `llms.txt` discovery, shell completions, and structured JSON
  output. Only read-only `inventory`, `diff`, `check`, `explain`, and `impact` are agent-visible.
- Optional `murlocs.eval` harness for scoring recorded agent runs. It is never imported by
  `compile` or `check`.

### Security

- Compilation refuses unmanaged files, modified generated maps, path escapes outside the repository
  root, symlinked map targets, and orphaned owned maps.
- Validation inspects the proof configuration of registered commands but never executes them.

[Unreleased]: https://github.com/lbliii/murlocs/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/lbliii/murlocs/releases/tag/v0.1.0
