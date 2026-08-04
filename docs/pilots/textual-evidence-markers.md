# Textual evidence-marker dogfood experiment

Issue #82 tests the existing manual-proof contract with a source-local, namespaced marker. It does
not add annotation parsing, a schema field, a source scan, or a directive kind.

## Setup

The real `core.no-command-execution` invariant continues to get its statement, severity, scope,
and owner from the layered manifest. Its manual proof remains `docs/architecture.md`; only its
existing `anchor` value changed from an incidental sentence fragment to
`murlocs:evidence no-command-execution`. The matching source marker is a standalone HTML comment:

```html
<!-- murlocs:evidence no-command-execution -->
```

The marker contains only the reserved prefix and invariant id. The generated core map receives the
manifest's normal `Evidence: file contains anchor` summary; it copies no arbitrary prose from the
source comment.

## Before and after capture

Commands were run from `9f376d3` before the edit and again after compilation. Both runs used the
same representative core path and evidence file.

| Operation | Before | After |
| --- | --- | --- |
| `murlocs --dry-run compile` | five managed outputs unchanged | only the core map and lockfile differ; rerun reports all five outputs unchanged |
| `murlocs check` | passes: 4 scopes, 8 invariants, 3 checks | same pass result |
| `murlocs explain src/murlocs/verify.py` | root + core active context: 5,316 bytes | same scopes/invariants, 5,332 bytes |
| `murlocs impact --path docs/architecture.md` | 2 required, 2 recommended; core is required because it is proof for `no-command-execution` | same counts, routing, and reason |

`impact` was already correctly file-level: it names `docs/architecture.md` as proof for the exact
invariant. The marker makes the source itself easier to audit with a unique identifier, but does
not make impact noisier or change its routing. This is deliberately a dogfood result, not a claim
that substring anchors identify individual locations.

## Fixture observations

`tests/fixtures/textual-evidence-markers/` and
`tests/test_textual_evidence_markers.py` use independent repositories and the unchanged manual
proof fields.

| Fixture | Result under current model | What it demonstrates |
| --- | --- | --- |
| `presence` | `check` passes | The declared file contains the exact marker. |
| `deletion` | `check` fails | Removing the marker from the declared file is detected. |
| `movement` | `check` fails | Moving it to another file without updating `evidence_file` is detected. |
| `duplication` | `compile` and `check` pass | A substring match cannot detect two identical markers. |

The model also cannot report a line, distinguish comment syntax, establish uniqueness, or detect a
move when the old copy remains. It only tests whether the declared file contains the declared byte
sequence. Those limitations are now explicit regression evidence rather than inferred behavior.

## Cost and recommendation

Authoring required two deliberate edits: replacing one 21-byte anchor value in the authored layer
with a 37-byte marker and adding one 46-byte source comment. The generated core-map and active-
context deltas are therefore exactly +16 bytes; the source comment itself is not copied into
generated guidance.

**Recommendation: proceed, with a bounded first-class contract.** The namespaced marker gives
authors and reviewers a stable, grep-friendly linkage while preserving the manifest as authority.
The duplicate and location blind spots justify a later parser-backed contract only if owner review
needs uniqueness or precise locations; this experiment intentionally does not add it.
