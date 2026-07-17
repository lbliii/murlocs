# Architecture

Kodama separates judgment from enforcement.

```text
agent-assisted discovery ──> .kodama/manifest.toml ──> deterministic compiler
                                      │                         │
                                      │                         ├──> AGENTS.md maps
                                      │                         └──> .kodama/lock.json
                                      └────────────────────────────> verifier / explain
```

## Trust boundaries

The bootstrap skill may inspect a repository and propose scopes, invariants, edges, and evidence.
Those proposals become reviewable only when written to the manifest. The CLI does not call a model,
use the network, or execute commands recorded in the manifest.

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

Generated maps are standard `AGENTS.md`, so compatible agents do not need Kodama installed at
runtime. Nested maps add local context; the root map carries network-wide rules and the review
protocol link.

## Verification semantics

`kodama check` establishes that the declared guidance is internally coherent and synchronized. It
does not establish that every architectural claim is true. Truth is made auditable through explicit
verification modes:

- `command`: names a registered command and verifies its configuration proof exists;
- `manual`: names a checked-in evidence file and textual anchor;
- `unknown`: records debt without pretending the claim is enforced.

This distinction prevents documentation aspirations from silently becoming enforcement claims.
