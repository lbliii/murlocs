# Source annotation resolver

Version 1 adds an optional, reviewed `annotation` table to an invariant. It is
additive: `evidence_file` and `anchor` keep their existing textual-evidence
meaning and are never converted, deduplicated, or removed by annotation support.

```toml
[[invariants]]
id = "parser-contract"
scope = "core"
statement = "The parser keeps its public grammar stable."
severity = "important"
verification = "manual"
evidence_file = "docs/grammar.md"
anchor = "Public grammar"
annotation = { id = "parser.contract", kind = "evidence", file = "src/parser.py", version = "v1" }
```

The declaration, not the source file, owns the invariant's statement, severity,
scope, and verification.  The resolver reads only those explicitly named,
repository-local files.  It returns an inert identifier plus a normalized file and
physical line location; it never retains a source snippet, interprets surrounding
prose, runs a command, invokes a model, or fetches a network resource.

The v1 resolver accepts only the wrappers and exact grammar in
[the annotation contract](source-annotation-contract-v1.md). It rejects path
escape, symlink, generated/vendor, non-file, oversize, and undecodable candidates
before scanning. Resolution is bounded to 256 declared files, 16 path components,
64 KiB per file, 4 MiB total input, 1,024 candidate markers, and two seconds.

Layered manifests compose annotation declarations through the existing invariant
identity/override rule. A later invariant override replaces the complete reviewed
declaration. Single-file manifests without `annotation` remain byte-for-byte
compatible with their existing maps and lockfiles.

This resolver is deliberately not yet a `check`, `explain`, rendered-map, impact,
or transport surface. Those validation, provenance, and lifecycle integrations are
separate changes. A successful attachment is inspectable evidence only; it does
not establish semantic truth or authorize an action.
