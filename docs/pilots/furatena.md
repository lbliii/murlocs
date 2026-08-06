# Furatena migration pilot

Pilot date: 2026-08-06

This is the first fully executed Murlocs migration pilot. It re-runs the complete
`inventory → diff → import → check → adopt → prune → rollback` cycle against the live
furatena `.stewards` guidance network, resolves the known proof-anchor blocker, and captures real
before/after figures. All mutation ran in an isolated detached git worktree of furatena; furatena's
real working tree and remotes were never touched.

- Subject repository: `furatena` at revision `a4957446`
  ("Align init-dependent author tests with the friendly scaffold.")
- Murlocs build: `0.1.0`, editable/development (`sha256:d9b5895f...`), run from the
  `lbliii/quebec-v3` workspace via `uv run --project <workspace> murlocs ...`.
- Isolated worktree: `git worktree add --detach /tmp/furatena-pilot HEAD` (removed after the run).

## Baseline

furatena already compiles `AGENTS.md` maps from `.stewards/manifest.toml` and validates its network
with two non-executing maintenance checks. Both passed on the pilot revision before any change:

```text
python3 .stewards/verify.py --coverage
python3 .stewards/project.py --check
```

| Legacy check | Result |
| --- | --- |
| `verify.py --coverage` | `OK 30 stewards, 37 invariants (36 machine, 1 manual, 0 none).` |
| `project.py --check` | `OK all 30 maps current.` |

Murlocs `inventory` corroborated the network from the outside and classified ownership:

```text
found 31 instruction file(s)
legacy network: 30 scope(s), 37 invariant(s), 28 check(s), 20 proof-debt item(s)
```

Of the 31 instruction files, 30 are stewards-generated `AGENTS.md` maps and one is the durable,
hand-authored `CLAUDE.md`, correctly inventoried as `user`-owned.

## Structural parity

Read-only `diff --mode semantic` reproduced the prior rehearsal field-for-field. Before adding
anchors it reported the network shape plus the two expected finding classes:

```text
Furatena: 30 scope(s), 37 invariant(s), 28 check(s)
debt: missing-proof-anchor (20)
info: legacy-severity (37)
```

Every count matches the legacy verifier: 30 scopes, 37 invariants (36 machine / 1 manual / 0 none),
28 registered checks, 3 judgment sections. Severity is preserved, not reinterpreted: the
`legacy-severity` note records that furatena's `P0/P1/P2/P3` map to `critical/important/advisory/advisory`
for all 37 invariants. No unsupported-field losses were reported.

## Proof-anchor debt resolution

The one known blocker: Murlocs adoption treats a check without a `proof_contains` anchor as proof
debt. furatena leaves `proof_contains` unset on 20 of its 28 checks (it is optional in furatena's own
schema). `diff` named all 20 exactly:

`autodoc-suite`, `directives-suite`, `inventory-suite`, `migration-suite`, `references-suite`,
`sources-suite`, `schema-suite`, `cli-suite`, `cli-commands-suite`, `theme-suite`, `runtime-suite`,
`content-suite`, `docs-suite`, `benchmark-suite`, `changelog-draft`, `examples-suite`,
`github-suite`, `scripts-suite`, `eval-suite`, `fixtures-suite`.

Nineteen of these point `location` at a pytest file; the anchor was filled mechanically from the
first meaningful test function in that file. The twentieth, `changelog-draft`, is the same defect the
Chirp/Kida audit called out: its `location` pointed at `changelog.d/README.md`, contributor prose
that neither defines nor proves the `make changelog-draft` target. It was **retargeted** to
`Makefile` and anchored on the actual target definition `changelog-draft:` — the strongest of the 20
resolutions because the anchor now names the thing the command actually runs.

After adding the anchors, `verify.py --coverage` and `project.py --check` still passed, and
`diff --mode semantic` dropped the debt entirely:

```text
Furatena: 30 scope(s), 37 invariant(s), 28 check(s)
info: legacy-severity (37)
```

The full 20-line change is published alongside this doc as the upstream deliverable in
[`furatena-proof-anchors.diff`](furatena-proof-anchors.diff) (21 insertions, 1 deletion). It is a
diff to hand to furatena, not a pushed commit.

### Hand review: location is not proof

Each anchor is a valid proof anchor (the named test exists in the named file), but two weaknesses
were noted by hand:

1. **Structural**: every `*-suite` check's `invoke` runs several test files, while `location` and
   the anchor cover only the first test in the primary file. The anchor proves that file and test
   exist; it does not prove the whole suite. This is furatena's existing convention, preserved rather
   than expanded.
2. **Trivial anchors** — three anchors name shallow unit assertions and are flagged weak:

   | Check | Anchor | Why weak |
   | --- | --- | --- |
   | `references-suite` | `test_longest_prefix_match` | Tests a small URL rewrite-table utility, not reference resolution. |
   | `sources-suite` | `test_registered_formats` | Asserts format-registry membership only. |
   | `theme-suite` | `test_home_uses_home_view` | Checks the home node resolves to the home view. |

   Two more are narrow but defensible: `migration-suite` (`test_lowers_jsx_block_to_directive`) and
   `benchmark-suite` (`test_harness_explains_gil_enabled_runtime`).

These are anchors of convenience: they satisfy the contract and pin a stable string, but a
maintainer should later repoint them at a headline contract test for each suite.

## Full migration cycle

Every command below ran with `--repo /tmp/furatena-pilot`; the salient outputs are reproduced inline
below.

```text
murlocs inventory
murlocs diff --mode semantic
murlocs import --output .murlocs/manifest.toml
murlocs check                     # pre-adoption
murlocs adopt
murlocs check                     # post-adoption
murlocs explain src/furatena/catalog/references
murlocs prune
murlocs rollback
```

| Stage | Result |
| --- | --- |
| `import` | `wrote .murlocs/manifest.toml` (48,314 bytes) and `.murlocs/PROTOCOL.md`; `info: legacy-severity (37)`. |
| `check` (pre-adoption) | Blocking, as expected: `lockfile is missing; run murlocs compile`. Maps are not yet Murlocs-owned. |
| `adopt` | `adopted 30 map(s)`. Original stewards maps backed up byte-identical under `.murlocs/backups/…/files/`. |
| `check` (post-adoption) | `murlocs check passed: 30 scope(s), 37 invariant(s), 28 check(s)`; coverage structurally complete (4 declared roots, no findings). |
| `explain` | Returned the full chain root → package → catalog → references, listing the `references-suite` anchored check. Active guidance 18,181/24,576 bytes. |
| `prune` | `pruned 6 legacy file(s)` (the `.stewards/` tooling moved into the migration backup). |
| `rollback` | `rolled back migration`. |

The representative `explain src/furatena/catalog/references` chain:

```text
[root]       AGENTS.md
[package]    src/furatena/AGENTS.md
[catalog]    src/furatena/catalog/AGENTS.md
[references] src/furatena/catalog/references/AGENTS.md
Focused checks: steward-tools, fast, contract, coverage, export, agent, release, references-suite
Active guidance: 18181/24576 bytes
```

No Murlocs command emitted a crash or traceback at any point in the cycle.

## Before / after figures

| Property | Legacy `.stewards` | Murlocs (adopted) |
| --- | ---: | ---: |
| Generated maps | 30 | 30 |
| Scopes | 30 | 30 |
| Invariants | 37 | 37 |
| — machine-backed | 36 | 36 |
| — manual | 1 | 1 |
| — none / unverified | 0 | 0 |
| Registered checks | 28 | 28 |
| Checks with proof anchor | 8 | 28 |
| Blocking proof-debt findings | 20 | 0 |
| Judgment sections | 3 | 3 |
| Total generated bytes | 51,553 | 53,470 |
| Largest active chain | — | 18,658 bytes (`src/furatena/catalog/sources`) |
| Active-chain budget | 24,576 | 24,576 |

The map counts, network shape, and invariant backing survive the migration exactly. Total generated
bytes rise ~3.7% (51,553 → 53,470) because Murlocs re-renders each map into its owned format with a
compact context-discipline contract; the largest resulting active chain (18,658 bytes) stays well
inside the 24,576-byte budget. The proof-anchor column is the substantive change: anchored checks go
from 8/28 to 28/28 and blocking proof debt from 20 to 0.

## Rollback verification

A SHA-256 snapshot of all 36 tracked instruction files (30 `AGENTS.md`, `CLAUDE.md`, and the five
`.stewards/` files) was taken immediately before adoption and again after rollback.

- **Byte-for-byte restored**: the two snapshots are identical for all 36 files.
- **`CLAUDE.md` untouched**: hash `f65328ed6d04bb…` before and after; `git diff -- CLAUDE.md` is
  empty against the pilot revision.
- The only tracked change remaining in the worktree after the full cycle is the intended
  `.stewards/manifest.toml` anchor addition (the deliverable diff); `.murlocs/` remains only as
  untracked migration state.

Adoption performed no implicit ownership transfer of `CLAUDE.md`, adoption backups preserved the
legacy maps exactly, and rollback restored the pre-adoption network in full.

## Result

The furatena pilot passes end to end. The complete migration cycle ran on live data with no crash,
the sole known blocker (missing proof anchors) is resolved by a 20-check diff — 3 anchors flagged
weak for later strengthening, one (`changelog-draft`) upgraded from decorative prose to a real
`Makefile` target proof — and rollback restored every instruction file byte-for-byte with
`CLAUDE.md` untouched. The upstream anchor change is delivered as
[`furatena-proof-anchors.diff`](furatena-proof-anchors.diff) for furatena to apply on its own branch.
