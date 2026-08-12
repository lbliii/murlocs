# Documentation map

Murlocs compiles reviewed repository guidance into standard `AGENTS.md` maps,
then checks what applies and what a change may affect.

## Start

- [Primary user journeys](journeys.md) — bootstrap, roll out, migrate, and repair guidance.
- [Architecture](architecture.md) — how the compiler, maps, and trust boundaries fit together.
- [Layered manifests](layers.md) — define scopes, merge rules, and owners.
- [Product positioning](product-positioning.md) — the promise, proof, and language for explaining Murlocs.

## Operate

- [Outcome envelope](outcome-envelope.md) — read compact outcomes and repair guidance safely.
- [Changed-path guidance impact](impact.md) — use impact reports in review.
- [Adoption status and coverage](adoption.md) — track rollout and coverage.
- [Governed guidance curation](curation.md) — propose, review, and maintain guidance.
- [Passive Git hooks](git-hooks.md) — collect receipts during normal Git work.
- [Activation lifecycle](activation-lifecycle.md) — activate guidance in agent hosts and record evidence.
- [Runtime build identity](runtime-identity.md) — inspect version and installation provenance.
- [Source annotation authority model](source-annotation-authority.md) — decide which source-linked guidance is authoritative.
- [Roadmap](roadmap.md) — completed foundations and planned work.

## Integrate

- [Agent-host adapter contract](adapter-conformance.md) — requirements for host adapters.
- [Claude Code adapter](claude-code-adapter.md) — installation, behavior, and limits.
- [GitHub Copilot adapter](github-copilot-adapter.md) — lifecycle and authority behavior.
- [Source annotation contract v1](source-annotation-contract-v1.md) — the annotation grammar.
- [Source annotation resolver](source-annotation-resolver.md) — resolve source-linked markers.
- [Source annotation provenance](source-annotation-provenance.md) — record normalized provenance.

## Research and evidence

- [Layered repository intent (design RFC)](layered-intent.md) — outcome-oriented intent chains and task-frame semantics.
- [Guidance efficiency evaluation](evaluation.md) — compare and score recorded runs.
- [Agent tool-selection corpus and rubric](pilots/tool-selection/README.md) — frozen first-tool prompts and scoring labels (#124).
- [Chirp and Kida paired-pilot audit](pilots/chirp-kida.md) — a migration audit of two personal projects.
- [Furatena migration pilot](pilots/furatena.md) — first fully executed migration, with verified rollback.
- [Dori hard-parity pilot](pilots/dori.md) — adversarial parity audit of an exotic legacy manifest.
- [Synthetic 91-map scale pilot](pilots/scale-network.md) — measured scale and governance limits.
- [No-prompt passive-agent acceptance harness](passive-agent-acceptance.md) — acceptance protocol and evidence boundary.
- [Fresh-session passive-agent acceptance pilot](pilots/passive-agent-acceptance-2026-08-03.md) — recorded pilot results.
- [Textual evidence-marker dogfood experiment](pilots/textual-evidence-markers.md) — findings and recommendation.
- [Compact outcome blind/reveal](pilots/compact-outcome-blind-reveal.md) — test of outcome rendering.
