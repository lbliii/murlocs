# Layered manifests

A single `.murlocs/manifest.toml` is the simplest authoring surface and remains fully
supported. As a network grows, editing one file makes every local guidance change a
repository-wide review event. Layered manifests keep a small root control plane and
compose ordered, owner-focused fragment files without changing the deterministic
`AGENTS.md` runtime contract.

## Control plane vs. layers

The root `.murlocs/manifest.toml` is always the base source. It owns the global control
plane and may declare an ordered `[[layers]]` set:

```toml
schema_version = 1
network = "Example"
protocol = ".murlocs/PROTOCOL.md"
max_active_bytes = 24576

pillars = ["Guidance is local and reviewable."]
search_policy = ["Read the root map before repository discovery."]
operating_rules = ["Read the applicable AGENTS.md chain before editing."]
stop_and_ask = ["A change crosses a scope boundary."]
done_criteria = ["Checks pass."]

[coverage]
roots = ["src"]
source_suffixes = [".py"]

[coverage.exemptions]

[policies]
require_scope_invariants = false

[[layers]]
id = "repo-base"
kind = "base"
path = ".murlocs/layers/base.toml"
owners = ["@platform"]

[[layers]]
id = "docs-domain"
kind = "domain"
path = ".murlocs/layers/docs.toml"
owners = ["@docs"]
```

Each layer declaration needs an `id`, a `kind` (`base`, `domain`, or `overlay`), and a
repository-relative `path`. An optional `owners` array names the guidance owners for that
source (used by the provenance block and CODEOWNERS validation).

Only the root manifest may set the control plane: `schema_version`, `network`, `protocol`,
`max_active_bytes`, `policies`, `layers`, and `owners`. A layer file that sets any of these
is rejected.

A layer file is a manifest fragment. It may contribute any of `pillars`, `search_policy`,
`operating_rules`, `stop_and_ask`, `done_criteria`, `coverage`, `checks`, `scopes`,
`invariants`, and `judgments`:

```toml
# .murlocs/layers/docs.toml
[[scopes]]
id = "docs"
path = "docs"
map = "docs/AGENTS.md"
point_of_view = "Documentation domain."
owns = ["docs"]
```

## Merge contract

Layers resolve into the existing canonical `Manifest` before `compile`, `check`, `diff`,
or `explain`. Resolution is deterministic:

- **List guidance** (`pillars`, `search_policy`, `operating_rules`, `stop_and_ask`,
  `done_criteria`, `coverage.roots`, `coverage.source_suffixes`) appends in layer order,
  then removes exact duplicates while keeping the first occurrence.
- **Scopes** are keyed by `id`. The first layer to declare an id owns it. A later layer may
  refine it only by opting in with `override = true`. An overlay may replace `point_of_view`
  and append `guardrails`, `edges`, and `owns`, but it may never change the immutable `path`
  or `map`. A duplicate scope without `override = true` is an error.
- **Invariants** are keyed by `id`. A duplicate is rejected unless the later declaration sets
  `override = true`, in which case it fully replaces the earlier claim.
- **Checks** merge by name with the same explicit-override rule.
- **Judgments** merge per scope; `advocate`, `do_not`, and `serves` append with deduplication.
- **Coverage exemptions** merge by path; the same path with a different reason across layers
  is a conflict.

Invalid or ambiguous composition — unknown fields, control-plane fields in a layer, unsafe or
escaping paths, duplicate layer ids, duplicate scopes/invariants/checks without override, or an
override that changes an output path — fails before any generated file is written.

## Ownership in the lockfile

`.murlocs/lock.json` records the ordered layer set and the content hash of every source. A
changed layer file, or a reordered layer set, is reported as drift by `murlocs check` even when
the root manifest itself is unchanged. Single-file manifests continue to work unchanged; their
lock records the one manifest source.

## Provenance and explanation

Generated maps for a layered network carry a provenance block naming the exact ordered source
layers that contributed to them and the owners who review each source. `murlocs explain PATH`
extends this with the effective-value trace, accepted overrides, and shadowed values. See
[Ownership provenance](#) in the generated maps and `murlocs explain --json` for the full
structured trace.
