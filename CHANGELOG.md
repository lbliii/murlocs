# Changelog

All notable changes to Murlocs are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the major version is
`0`, the manifest schema and generated map format may change between minor releases.

## [Unreleased]

### Added

- Add a versioned agent tool-selection corpus, rubric, and offline validator for first-tool measurement (#124).
- Versioned inert guidance-friction observation schema (`docs/guidance-friction.md`,
  `src/murlocs/friction.py`) for missing, misleading, conflicting, repetitive,
  and overly broad guidance, with path-safety validation, fixture corpus, and
  deterministic duplication/scope/stability/evidence-gap/context-cost analysis
  (#132). Observations do not auto-apply to guidance.
- Versioned host capability matrix with native/adapted/tool-only/unknown tiers,
  evidence-gated effective tiers, portable fallbacks, and checked-in Codex,
  Claude Code, Cursor, and GitHub Copilot (orchestrator) profiles (#139).
- Multi-repository passive-loop pilot protocol, offline observation-sheet
  validator, simulated two-repo harness fixture, and published report that
  separates executed in-repo work from planned live longitudinal execution
  (#68).
- Closure gate for pull requests that claim `Closes`/`Fixes`/`Resolves` `#N`:
  `scripts/check_closure_acceptance.py` plus
  `.github/workflows/closure-acceptance.yml` fail unless an offline acceptance
  anchor exists or the body declares `Acceptance #N: n/a (reason)`. Documented
  as advisory until required in branch protection (#207).
- Add acceptance-anchor strength checking so tautological `issue(N)` tests are rejected (#209).
- Adapter conformance invariant so continuous lifecycle surfaces never block or
  gate on empty change sets; Claude and Copilot adapters updated accordingly
  (#203).
- Deterministic generator and checked-in fixture for every agent-facing
  discovery surface (CLI, MCP, llms.txt, skills, generated guidance, docs) with
  drift tests (#120).
- Offline acceptance anchors for work items: `work_items` in manifest layers,
  pytest `@pytest.mark.issue(N)` discovery, and `issue_coverage.py` check
  integration (#206).
- Source annotation impact routing (policy v3) for attachment changes across
  paths and revisions, with CLI rendering and fixtures (#88).
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

### Fixed

- Fix invalid Python 3 multi-exception handlers left by #211 so acceptance, adapters, and impact import on 3.14 (#203, #120, #206).
- Retry ordinary Git revision reads without unsupported `--no-lazy-fetch` while keeping `GIT_NO_LAZY_FETCH` (#88).

- The Claude Code `pre-completion` Stop hook no longer blocks completion on a
  clean working tree. A turn that changed no files reported
  `MURLOCS_ACTIVATION_UNAVAILABLE` and was treated as a blocking gate, so the
  Stop hook re-fired every turn until the host's eight-block runaway guard
  tripped. It now stops cleanly when nothing changed, and a genuine blocking
  impact outcome gates at most once: when Claude Code is already replaying a
  Stop-hook continuation the gate downgrades to advisory context so it can
  never loop the agent (`src/murlocs/claude_adapter.py`).

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
