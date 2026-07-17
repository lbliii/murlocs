# Roadmap

Murlocs is developed against personal repositories first. Vendor-specific repositories may consume
the result later, but they do not define the core model or its acceptance criteria.

## v0.1 — portable core (complete)

- Deterministic `init`, `compile`, `check`, and `explain` commands.
- Versioned TOML manifest and content-addressed ownership lockfile.
- Scopes, typed edges, invariants, proof wiring, coverage, and context budgets.
- Thin `bootstrap-murlocs` authoring skill.
- Milo-backed CLI, MCP, agent-readable discovery, and machine-readable command results.
- GitHub Actions reference workflow.

## v0.2 — personal repository migration

Chirp and Kida are the paired pilots. Their existing `.stewards` networks are mature enough to test
semantic preservation, proof integrity, context discipline, and safe ownership transfer. See the
[paired-pilot audit](pilots/chirp-kida.md) for the baseline and discovered gaps.

### v0.2a — migration parity contract

- Specify a lossless mapping from the legacy `.stewards` dialect to the Murlocs schema.
- Preserve search policy, advisory judgment, typed ownership, review triggers, and generated-map
  context discipline instead of flattening them into generic prose.
- Define explicit severity and verification mappings without strengthening unproved claims.
- Validate local paths named by registered commands and recursively detect uncovered source domains.
- Add compact Chirp- and Kida-derived fixtures that lock the shared migration contract without
  copying either repository's complete manifest into Murlocs.

### v0.2b — safe import and adoption

- Add read-only `murlocs inventory` for existing `AGENTS.md`, `CLAUDE.md`, `.stewards`, checks, and
  ownership conflicts.
- Add `murlocs import --from stewards` to produce a candidate manifest and a machine-readable loss
  report without taking ownership of existing maps.
- Add semantic and rendered `murlocs diff` output.
- Add explicit adopt, prune, and rollback operations with dry-run support and lockfile-backed
  ownership boundaries.
- Never overwrite or relabel an existing instruction file as an implicit side effect of import.

### v0.2c — paired pilot migrations

- Migrate Chirp's 34 maps, 50 invariants, and 36 registered checks with no lost scope, edge,
  ownership category, or review behavior.
- Migrate Kida's 24 maps, 49 invariants, and 29 registered checks to the same contract.
- Surface missing proof anchors as migration debt: do not silently treat a command location as proof
  that the command enforces an invariant.
- Run both legacy verifiers before adoption and Murlocs validation plus representative `explain`
  chains afterward.
- Perform adoption only in clean, isolated worktrees and retain a tested rollback path.

### v0.2d — adapters and integration

- Render adapters for tools that do not consume `AGENTS.md`, while preserving user-owned files by
  default.
- GitLab CI and pre-commit reference integrations.
- Changed-path reporting that identifies affected scopes and focused registered checks.

## v0.3 — ecosystem

- Manifest schema publication and compatibility policy.
- Reusable organizational policy packs with local override rules.
- Cross-repository dependency contracts without a persistent service.
- Optional evidence-freshness policies and cross-repository impact reporting.

Murlocs should remain local-first and CI-neutral. A hosted control plane is not a prerequisite for
useful, trustworthy repository guidance.
