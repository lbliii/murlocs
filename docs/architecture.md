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

<!-- murlocs:evidence no-command-execution -->

The bootstrap skill may inspect a repository and propose scopes, invariants, edges, and evidence.
Those proposals become reviewable only when written to the manifest. The CLI does not call a model,
use the network, or execute commands recorded in the manifest. Changed-path impact reporting reads
Git metadata only when given an explicit revision expression and reports review need without
asserting that guidance is semantically stale. Milo derives terminal, programmatic, MCP, and
agent-discovery surfaces from the same typed handlers.

Guidance maintenance has a second, deliberately inert proposal plane. Versioned records under
`.murlocs/curation/` may contain candidate additions, replacements, or removals, but the compiler
never loads that directory. An owner decision and a separate transactional apply operation are
required before a proposal changes an active manifest or layer. See
[Governed guidance curation](curation.md) for the lifecycle, deterministic review checks, and trust
boundary.

Passive activation remains outside the compiler and curation write plane. The
[portable agent activation lifecycle](activation-lifecycle.md) defines read-only task boundaries,
integration-produced freshness receipts, cache invalidation, and generated-guidance/hook/CI
fallbacks. It never converts an agent claim, Git event, or Murlocs finding into authenticated
approval.

Source annotations remain outside the compiler until their contract is implemented. The
[source annotation authority model](source-annotation-authority.md) makes candidate source comments
inert, confines their future role to declared evidence attachment, and specifies conservative
base/head and trusted-adapter behavior. It does not make source prose agent guidance or approval.

The [adapter conformance harness](adapter-conformance.md) tests that boundary through isolated
repositories and opaque, adapter-scoped state and impact-dependency tokens. It validates observable
freshness and mutation behavior rather than defining one Git or filesystem snapshot algorithm.

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

Coverage has a separate explicit state. An empty `coverage.roots` list is `unconfigured`, not a
successful scan of the repository. With declared roots, the result is `structurally_complete` only
when no structural coverage findings remain; this still makes no claim about the semantic truth or
usefulness of the resulting guidance.

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


## Concurrency and thread safety

Murlocs is designed and tested as a one-shot CLI. Hosts that embed it as a
library under a threaded server -- for example the `--mcp` surface or the
Claude and Copilot adapters dispatching read-only handlers concurrently --
should treat its two halves differently.

Read-only, in-memory operations are reentrant and safe to run concurrently.
Loading (`load_manifest`), layer resolution and merge (`layers.compose`,
`layers.resolve_manifest`), validation (`verify.validate`), in-memory rendering,
and source-annotation resolution hold no shared mutable state: there are no
module-level mutable globals, no call-time memoization or caches, and
`compose` builds a fresh result while treating the fragments it reads as
read-only (it shallow-copies each incoming entry before rebinding keys, and
never mutates a caller's nested lists or dicts in place). A `Manifest` is an
immutable value object rebuilt per load, so a single instance may be shared
read-only across threads. Its `dict` fields (`checks`, `coverage_exemptions`,
`scope_layers`, `invariant_layers`) are plain mutable dictionaries, so a host
must not mutate them while other threads read; treat a shared manifest as
frozen.

The write path is not thread-safe and must not run concurrently against the
same repository. Every generated output goes through `atomic._write`, which
derives file modes via a process-wide `os.umask(0)`-then-restore
read-modify-write: two threads writing at once can observe each other's
temporary `0` umask and create files with the wrong permissions. Compilation
(`render.prepare_manifest` / `render.compile_manifest`) also performs unguarded
check-then-act on shared repository files -- it reads the lockfile, compares
hashes, unlinks orphaned maps, then replaces outputs -- with no lock protecting
the sequence. There is no in-process writer lock.

Supported integration: embed Murlocs in-process for read-only guidance queries
under a threaded host, sharing manifests as immutable values. Serialize any
mutating operation behind a single writer, or shell out to the `murlocs` CLI so
each compilation runs in its own process. Do not dispatch concurrent
compilations in-process until the umask read-modify-write and the compile
check-then-act sequence are guarded.
