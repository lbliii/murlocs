# Architecture

Murlocs separates judgment from enforcement.

```text
agent-assisted discovery ──> .murlocs/manifest.toml ──> deterministic compiler
                                      │                         │
                                      │                         ├──> AGENTS.md maps
                                      │                         └──> .murlocs/lock.json
                                      └────────────────────────────> verifier / explain
```

## Trust boundaries

The bootstrap skill may inspect a repository and propose scopes, invariants, edges, and evidence.
Those proposals become reviewable only when written to the manifest. The CLI does not call a model,
use the network, or execute commands recorded in the manifest. Milo derives terminal, programmatic,
MCP, and agent-discovery surfaces from the same typed handlers.

The lockfile records the exact hash of every generated map. Compilation will write a missing map or
replace an unchanged map it already owns. It refuses unmanaged files, modified generated files, path
escapes, and orphaned owned maps. This makes destructive migrations impossible by default.

## Compilation

Compilation is a pure projection of the manifest plus the tool version:

1. Parse and structurally validate the manifest.
2. Verify declared paths, edges, evidence anchors, coverage, and maximum active context budget.
3. Render all maps in memory.
4. Preflight ownership for every output.
5. Atomically replace managed outputs and write a content-addressed lockfile.

Generated maps are standard `AGENTS.md`, so compatible agents do not need Murlocs installed at
runtime. Nested maps add local context; the root map carries network-wide rules and the review
protocol link.

## Verification semantics

`murlocs check` establishes that the declared guidance is internally coherent and synchronized. It
does not establish that every architectural claim is true. Truth is made auditable through explicit
verification modes:

- `command`: names a registered command and verifies its configuration proof exists;
- `manual`: names a checked-in evidence file and textual anchor;
- `unknown`: records debt without pretending the claim is enforced.

This distinction prevents documentation aspirations from silently becoming enforcement claims.

## Legacy compatibility boundary

The v0.2a compatibility layer translates the known Chirp and Kida `.stewards` manifest dialect in
memory. It performs no repository writes and rejects unknown fields instead of dropping them. The
translation preserves search policy, typed ownership groups, advisory judgment, graph edges,
context budgets, and the original P0–P3 severity spelling. Verification modes map explicitly:
`machine` to `command`, `manual` to `manual`, and `none` to `unknown`.

P0, P1, P2, and P3 have canonical meanings of critical, important, advisory, and advisory,
respectively, while their source spelling remains visible. Registered checks without a
`proof_contains` anchor remain loadable for loss reporting but cause `murlocs check` to report
blocking proof debt. Validation may inspect repository-local paths named by commands, but it still
does not execute those commands.

Import, rendered diff, ownership adoption, pruning, and rollback are intentionally outside this
translation layer.

## Layered steward networks

A steward manifest that declares an ordered `[[layer]]` set is imported into the Murlocs layered
model rather than flattened. Layer order, kinds, owners, scope declarations, and explicit
`override` intent are preserved: each legacy layer file becomes a Murlocs layer fragment under
`.murlocs/layers/`, and the root steward manifest becomes the Murlocs control plane with matching
`[[layers]]` registrations. Unknown fields and unsupported merge behavior — for example an
`override` on a non-overlay layer — are refused or reported as blocking loss instead of being
silently dropped, and import refuses to write a candidate that carries blocking loss. `inventory`
and semantic `diff` report the layered structure and distinguish equivalent rendered guidance from
lost authoring and governance semantics. Adoption renders the effective legacy maps to detect
byte-current output and keeps the same recoverable-backup and modified-output protections; a
post-adoption edit blocks rollback rather than being overwritten.

## Migration transaction

`inventory` and `diff` are read-only and agent-visible. `import`, `adopt`, `prune`, and `rollback`
remain CLI-only. Import prints candidate TOML by default; writing a candidate requires an explicit
output path and does not claim any generated map.

Adoption requires a reviewed `.murlocs/manifest.toml`, a live legacy manifest, valid proof wiring,
and byte-for-byte current legacy maps. Before replacing a map, Murlocs copies its exact bytes into a
repository-local migration backup. It then writes Murlocs maps, a normal ownership lockfile, and an
active migration record. Unknown, unmanaged, stale, or manually modified legacy maps stop the whole
preflight before any map changes.

Prune moves `.stewards` into that same backup rather than deleting it. Rollback first verifies that
every adopted map still has its recorded hash, then restores original maps and the legacy directory
and removes the adoption lock when none existed before. A post-adoption edit therefore blocks
rollback instead of being overwritten. Candidate manifests, migration records, and backups remain
for review; cleanup is a separate future policy rather than an implicit destructive side effect.
