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
source (used by the provenance block and CODEOWNERS validation). The root source derives its
owners from the top-level manifest `owners` array rather than from a `[[layers]]` declaration.

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

Layer placement does not change the semantics of a subject. The five list-guidance collections and
checks render in the root map, so contributing one from a domain layer can affect every active
root-to-target guidance chain. Judgments render only in their named scope map. Scope and invariant
subjects are scope-addressed but can also alter the root network summary. Curation and impact use
rendered-map/active-chain effects for routing; `target_scope` is an address, not a confinement
boundary.

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

## Splitting a single-file manifest

When a repository outgrows one manifest, `split-layers` can move selected existing scopes into
owner-focused sources without asking a model to classify or rewrite guidance. The command accepts
only a single-file manifest. Each scope selection explicitly names its destination layer, kind,
and owners:

```bash
# Read-only preview: repeat each list-valued option once per value.
murlocs --dry-run split-layers \
  --scope core=core,domain,@core \
  --scope docs=docs,domain,@docs \
  --root-owner @platform \
  --root-owner @security

# Apply exactly the previewed mechanical split. Without --apply, it remains read-only.
murlocs split-layers \
  --scope core=core,domain,@core \
  --scope docs=docs,domain,@docs \
  --root-owner @platform \
  --root-owner @security \
  --apply
```

The planner moves each selected scope together with its keyed judgments and invariants. A check
used only by invariants moving to one layer follows those invariants. Shared or unreferenced checks
stay in the root by default; use `--check NAME=LAYER` to make a different explicit decision.
Coverage roots and exemptions wholly inside one selected scope move with it, while broad or shared
coverage stays in the root. Repeat `--check NAME=LAYER|root`,
`--coverage-root PATH=LAYER|root`, and `--coverage-exemption PATH=LAYER|root` for multiple explicit
assignments. Repeating the same assignment key is an error instead of silently choosing one value.
Source suffixes remain a root-level, shared interpretation rule.

Dry-run writes nothing and reports:

- the complete proposed root manifest and new layer files;
- every moved keyed value and every shared-control decision retained at the root;
- meaningful keyed semantic differences separately from collection-order-only changes;
- rendered map changes, including changes that consist only of provenance;
- active-context bytes before and after for every scope; and
- exact root and layer CODEOWNERS requirements when that policy is enabled.

Apply refuses any semantic change, unsafe or existing layer path, unmanaged or modified generated
map, stale plan, invalid proof or coverage state, missing owner, or unsatisfied CODEOWNERS rule.
All source files, generated maps, and the lockfile are staged before replacement. If any replacement
fails, already-replaced files are restored and newly created files are removed, so a failed split
does not leave a partially layered network.

## Ownership in the lockfile

`.murlocs/lock.json` records the ordered layer set and the content hash of every source. A
changed layer file, or a reordered layer set, is reported as drift by `murlocs check` even when
the root manifest itself is unchanged. Single-file manifests continue to work unchanged; their
lock records the one manifest source.

## Progressive rollout

You do not have to model the whole repository at once. Start with a root-only map, then add
selected directories independently with `murlocs add-scope`:

```bash
murlocs init --name "My Repository"
# Preview a scope for docs/ without writing anything.
murlocs --dry-run add-scope docs --owners @docs
# Add docs/ and a second directory, deferring an area that is not ready yet.
murlocs add-scope docs --owners @docs
murlocs add-scope fern \
  --owners @web \
  --owners @platform \
  --defer legacy="migrating in a later phase" \
  --defer examples="adopting in a later phase"
murlocs check
```

`add-scope` creates a domain layer for the requested path, registers it in the root layer
order, declares the scoped `AGENTS.md` output, and compiles. The CLI owns path validation and
writes: a dry run shows the proposed layer, map, manifest registration, and coverage effects; an
apply refuses to overwrite existing unmanaged or modified generated files and leaves the manifest
untouched if the rollout cannot proceed. Deferred source-bearing areas are recorded as reasoned
coverage exemptions so they stay visible as rollout gaps rather than silent omissions. Nested
paths such as `docs/api` produce the correct root-to-scope map chain.

Repeat `--owners OWNER` and `--defer PATH=REASON` once per value. A deferred path may appear only
once; duplicate entries fail with the path named so competing reasons cannot overwrite each other.

When `validate_codeowners = true`, the dry run also prints every exact CODEOWNERS rule required by
the proposed layered manifest, including the root `.murlocs/manifest.toml` source. `--format json`
exposes these as `codeowners_requirements`, including the CODEOWNERS file, exact path, expected and
current owners, status, and whether the requirement is blocking. Status is one of `missing-file`,
`missing-entry`, `owner-mismatch`, or `satisfied`. Murlocs does not edit ownership policy: add or
correct the displayed rule, review that change, and then rerun `add-scope`. Applying with any
unsatisfied requirement fails before writing the layer, manifest registration, map, or lockfile.

## Ownership and provenance

Each layer declaration may name guidance `owners`. Generated maps for a layered network carry a
`## Provenance` block naming the exact ordered source layers that contributed to them, the file
a contributor should edit, and the owners who review each source. Reordering or changing a
contributing layer changes both the provenance block and the lock state.

Two opt-in policies make ownership enforceable while keeping the core local-first and CI-neutral:

```toml
[policies]
require_layer_owners = true   # every authored source must name at least one owner
validate_codeowners = true    # each source file must have an exact matching CODEOWNERS entry
```

For a layered network, the authored source set is the root manifest followed by its declared
layers. `require_layer_owners` fails `murlocs check` if the top-level `owners` array or any layer
declaration omits owners. `validate_codeowners` reads `.github/CODEOWNERS` (or `CODEOWNERS` /
`docs/CODEOWNERS`) and fails when any source file has no exact-path entry or when its CODEOWNERS
owners do not match the corresponding manifest metadata. Mismatch findings report expected and
actual owner sets.

Both policies default to off, so policy-disabled repositories keep working unchanged. They apply
only after a network declares layers: existing single-file manifests retain their prior behavior,
even if the policy keys are present. Murlocs treats owner strings as declared routing metadata; it
does not edit CODEOWNERS, authenticate an owner, or infer approval from the current GitHub identity.
Ownership problems are reported before compilation writes any generated file.

`murlocs explain PATH` extends provenance with the effective-value trace, accepted overrides, and
shadowed values; see `murlocs explain --json` for the full structured trace. Root-map provenance,
`explain`, and `impact` all report the root manifest source and its declared owners alongside the
other sources represented by that root map.

## Murlocs repository dogfood

Murlocs authors its own guidance as an owned layered network. The root manifest contains only the
network identity, context budget, policy switches, and ordered layer declarations. The authored
guidance lives in these sources:

| Layer | Kind | Responsibility |
| --- | --- | --- |
| `repo-base` | base | Global guidance, coverage, registered checks, and the root scope |
| `core` | domain | CLI and deterministic implementation guidance |
| `tests` | domain | Behavioral and safety-test guidance |
| `bootstrap-skill` | domain | Agent-assisted authoring guidance |

Every source currently names `@lbliii`, the repository maintainer, and has an exact entry in
`.github/CODEOWNERS`. Both `require_layer_owners` and `validate_codeowners` are enabled. A future
team split should change the owner in the layer declaration and its CODEOWNERS entry together.

The initial conversion exposed three workflow details worth retaining:

- `add-scope` remains the progressive-rollout command for a new scope. Existing scopes now use
  `split-layers`; the Murlocs repository conversion predates that planner and supplied the keyed and
  provenance comparison requirements it automates.
- Moving invariants beside their domain scopes can change their irrelevant global array order even
  when keyed invariant content and every rendered guidance section remain unchanged. Migration
  review compared keyed semantic content and then compared rendered maps without provenance; the
  only rendered additions were the expected provenance blocks.
- Root control-plane ownership is enforced like layer ownership: changing the top-level manifest
  owner and its exact CODEOWNERS entry is one reviewed change.

The root map names every source in its provenance because its network table summarizes every
scope. Scoped maps name only their domain contributor. Representative `explain` and `impact`
traces verify those relationships after compilation.
