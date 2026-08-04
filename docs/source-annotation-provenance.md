# Source annotation provenance

Version 1 exposes a reviewed source annotation only after the bounded resolver
has produced a complete valid binding. Provenance is attachment metadata, not a
claim that the source comment, invariant, or surrounding code is semantically
true. It never authorizes an agent action or executable instruction.

## Normalized record

Every read-only surface uses the same additive record, ordered by identifier,
source file, line, and invariant:

```json
{
  "id": "guidance.marker",
  "kind": "evidence",
  "version": "v1",
  "invariant": "guidance-stays-verified",
  "scope": "root",
  "file": "src/proof.py",
  "line": 12,
  "declaring_layer": "core",
  "owners": ["@platform"],
  "verification": "manual"
}
```

`file` and `line` are the resolver's normalized marker location. `declaring_layer`
and `owners` come from the reviewed manifest/layer declaration; single-file
manifests name `manifest` and may have an empty owners list. `verification` is
the invariant's existing verification mode. The record deliberately excludes
the source comment body, adjacent source, commands, URLs, and any semantic
interpretation.

## Surfaces and compatibility

Rendered `AGENTS.md` maps attach a concise evidence-provenance line beneath the
governed invariant. `check`, `explain`, `inventory`, and `status` expose the
same record in their human and structured results. `check` also carries it in
the additive `outcome.annotations` field. Milo derives terminal, in-process,
MCP, and discovery schemas from those typed outputs, so existing consumers may
ignore the new fields under the existing version-1 contracts.

The active-context byte calculation and rendered-map ownership lock include the
concise generated line. A repository that sets a small active-byte budget will
therefore receive the normal deterministic `budget` finding rather than a
silently truncated provenance record.

## Invalid and missing markers

If any declared annotation is missing, malformed, misplaced, excluded, or
otherwise invalid, resolver diagnostics appear as `annotation.*` findings.
No provenance record is emitted, no rendered map treats it as active evidence,
and no partial valid-binding set is retained. Inspect those findings with
`murlocs check`; do not repair source comments by treating their text as
instructions.

This provenance view does not make source annotations executable and does not
require agents to parse source comments at runtime. The resolver remains the
only bounded, local implementation of the v1 grammar and declaration relation.
