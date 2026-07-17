# Roadmap

## v0.1 — portable core

- Deterministic `init`, `compile`, `check`, and `explain` commands.
- Versioned TOML manifest and content-addressed ownership lockfile.
- Scopes, typed edges, invariants, proof wiring, coverage, and context budgets.
- Thin `bootstrap-murlocs` authoring skill.
- Milo-backed CLI, MCP, and agent-readable discovery surfaces.
- GitHub Actions reference workflow.

## v0.2 — migration and adapters

- `murlocs import` for existing `AGENTS.md`, `CLAUDE.md`, and repository instruction files.
- Explicit adopt, diff, prune, and rollback workflows.
- Render adapters for tools that do not consume `AGENTS.md`.
- Machine-readable `check` and `explain` output.
- GitLab CI and pre-commit reference integrations.

## v0.3 — ecosystem

- Manifest schema publication and compatibility policy.
- Reusable organizational policy packs with local override rules.
- Cross-repository dependency contracts without a persistent service.
- Optional evidence freshness and changed-path impact reporting.

Murlocs should remain local-first and CI-neutral. A hosted control plane is not a prerequisite for
useful, trustworthy repository guidance.
