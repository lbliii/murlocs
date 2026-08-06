# Dori hard-parity pilot audit

Audit date: 2026-08-06
Dori revision: `af04d394` (branch `main`, clean working tree)
Murlocs build: `0.1.0` development (`sha256:d9b5895f71d0945662e417aa4249d26f88117e5eed20f8934f9bd6a4af260aff`)

This read-only audit runs Murlocs against `dori`, the deliberately adversarial hard-parity case.
Unlike Chirp, Kida, and Furatena, dori carries a 4,012-line `.stewards/manifest.toml` plus a set of
constructs the earlier pilots never exercised: a check taxonomy (`check.kind` / `check.proves`),
per-invariant proof anchors (`invariant.proof_contains`), a top-level `stop_and_ask` register, an
`@inv:` back-reference resolver (`refs.py`), a portability manifest (`kit.toml`), and reusable
`archetypes/`. The objective is not to migrate dori. It is to test the parity contract itself: does
Murlocs **fail visibly with an accurate loss report**, or does it silently drop semantics — or crash?

Nothing under `/Users/llane/Documents/gitlab/dori` was modified. No `adopt`, `prune`, or `rollback`
was run. The candidate was captured to workspace scratch, never into dori.

## Headline result

**The parity contract half-held.** Murlocs refused rather than emitting a lossy candidate — the
"fail visibly, never silently drop a field into the output" half held, because *no candidate was
produced at all* (a zero-byte file). But the "accurate loss report" half **failed**: every read-only
command (`inventory`, `diff`, `import`) aborts fail-fast on the **first** offending check with a
single-line error and characterizes nothing else. dori is exotic enough that Murlocs cannot even
inventory it.

Every command exits on the identical error:

```text
error: legacy check arch-isolation contains unsupported fields: kind, proves
```

`arch-isolation` is merely the first entry in the check table. All 61 checks carry `kind` and
`proves`, and (behind that wall) 118 invariants carry `proof_contains`, which the invariant
translator also rejects. The operator is told about **one** check and **two** fields, hiding an
incompatibility surface roughly two orders of magnitude larger.

## Baseline scale (legacy vs candidate)

Legacy figures are parsed directly from `.stewards/manifest.toml` at `af04d394` with stdlib
`tomllib`. Candidate figures come from `murlocs import` — which produced **nothing**.

| Construct | Legacy (dori @ af04d394) | Candidate (Murlocs import) |
| --- | ---: | ---: |
| Scopes (stewards) | 28 | 0 — not produced |
| Typed edges | 8 | 0 — not produced |
| Invariants | 255 | 0 — not produced |
| Registered checks | 61 | 0 — not produced |
| Judgment sections | 20 | 0 — not produced |
| Pillars | 5 | 0 — not produced |
| `stop_and_ask` entries | 8 | 0 — not produced |
| Candidate bytes on disk | n/a | 0 |

Legacy distributions (for context; none reached translation):

| Dimension | Distribution |
| --- | --- |
| Invariant severity | P0 = 26, P1 = 124, P2 = 86, P3 = 19 |
| Invariant verification | machine = 146, manual = 75, none = 34 |
| Invariants with `proof_contains` | 118 of 255 |
| Invariants with `evidence_file` + `anchor` | 181 of 255 |
| Check kinds | test = 49, script = 7, ci = 2, arch = 1, lint = 1, typecheck = 1 |
| Edge types | shares-contract = 3, depends-on = 2, serves = 2, consumes = 1 |

## Commands run

All commands invoke the current-branch build, not the global install. `--repo` targets dori
read-only; `import` was redirected to workspace scratch, never into dori.

```text
uv run --project <ws> murlocs inventory --repo <dori>
uv run --project <ws> murlocs inventory --repo <dori> --format json
uv run --project <ws> murlocs diff      --repo <dori> --mode semantic
uv run --project <ws> murlocs diff      --repo <dori> --mode semantic --format json
uv run --project <ws> murlocs import    --repo <dori> --from stewards           > <scratch>/dori-candidate.toml
uv run --project <ws> murlocs import    --repo <dori> --from stewards --format json
```

| Command | Exit | Output |
| --- | ---: | --- |
| `inventory` (plain) | non-zero | `error: legacy check arch-isolation contains unsupported fields: kind, proves` |
| `inventory --format json` | non-zero | `{"ok": false, "error": {"code": "MURLOCS_INVENTORY", "message": "legacy check arch-isolation contains unsupported fields: kind, proves"}}` |
| `diff --mode semantic` (plain) | non-zero | same message |
| `diff --mode semantic --format json` | non-zero | `code: MURLOCS_DIFF`, same message |
| `import` (plain → scratch) | 1 | 0-byte candidate; same message on stderr |
| `import --format json` | non-zero | `code: MURLOCS_IMPORT`, same message |

There was **no unhandled Python traceback**. The failures are clean `MurlocsError` exits — the tool
refuses cleanly. The defect is not a crash; it is the *shape and completeness* of the refusal.

Every command and its exact output are reproduced inline above; the run is reproducible read-only by
pointing `--repo` at dori @ `af04d394`.

## Per-construct parity

Classification legend: **translates** = supported by the importer (but *unreached* here, since the
run aborts before it); **refused (crash-halt)** = triggers a hard `MurlocsError` that stops the whole
migration; **unmodeled/invisible** = Murlocs has no concept of the construct and never names it in
any output.

| Construct | Legacy present | Candidate / importer result | Classification | Decision |
| --- | --- | --- | --- | --- |
| `check.kind` (taxonomy) | 61 checks | Rejected: not in `LEGACY_CHECK_FIELDS`; raises on `arch-isolation` | **Refused (crash-halt)** — visible but only 1 of 61 named | Add to loss report as unsupported; do not halt |
| `check.proves` (contract prose) | 61 checks | Same rejection, same line | **Refused (crash-halt)** | Report; consider preserving as check note |
| `check.invoke` / `check.location` | 61 checks | Supported fields | Translates (unreached) | Keep |
| `invariant.proof_contains` | 118 invariants | Rejected: not in `LEGACY_INVARIANT_FIELDS` — a **second wall** behind the checks | **Refused (crash-halt), unreached** | Support on invariants (already supported on checks) |
| `invariant` core fields (id, steward, statement, severity, verification, enforced_by, evidence_file, anchor) | 255 invariants | Supported | Translates (unreached) | Keep |
| `severity` P0/P1/P2/P3 | 255 invariants | Copied through raw at import; no mapping, no loss note | Would pass through verbatim (unreached) | Define explicit P-level mapping and report it |
| `steward` → scope (id, path, point_of_view, owns.code/tests/docs, edges) | 28 stewards | Supported, including typed edges | Translates (unreached) | Keep |
| Typed edges (consumes/shares-contract/depends-on/serves) | 8 edges | Supported | Translates (unreached) | Keep |
| `judgment.advocate` / `judgment.do_not` | 20 sections | Supported | Translates (unreached) | Keep |
| `pillars` | 5 | Supported top-level | Translates (unreached) | Keep |
| `stop_and_ask` | 8 | Supported top-level | Translates (unreached) | Keep |
| `archetypes/` (`python-infra.toml`, `gpu-ml-pipeline.toml`) | 2 files | Not part of `manifest.toml`; never read, never named | **Unmodeled/invisible** | Document as out-of-scope; at minimum inventory the files |
| `kit.toml` (portability inventory, `default_archetype`) | 1 file | Not read, never named | **Unmodeled/invisible** | Document as out-of-scope |
| `refs.py` + `@inv:` / `@no-inv:` back-reference system | 1 subsystem | Not read, never named | **Unmodeled/invisible** | Document as out-of-scope |
| `.stewards/*.md` (BOOTSTRAP, CI, PROTOCOL, SCHEMA) | 4 docs | `inventory` would list guidance files, but it crashes first | Invisible (inventory aborted) | Fix inventory robustness |

## Verdict

The parity contract's **anti-silent-drop guarantee held**: Murlocs did not emit a candidate that
quietly discarded fields. It refused, and it named a genuine incompatibility. No field was silently
written-away, because no candidate was written at all.

The parity contract's **accurate-loss-report guarantee failed**. On the hardest case, the loss
report degrades to a fail-fast single line naming one check and two of its fields. It:

- understates the true incompatibility (61 checks × `{kind, proves}`, plus 118 invariants ×
  `proof_contains` waiting behind the first wall) by roughly two orders of magnitude;
- gives the operator no map of total remediation work — fixing `arch-isolation` only exposes the
  next identical error, 60 more times, then a fresh wall on invariants;
- takes `inventory` down with it, so Murlocs cannot even list dori's guidance files, archetypes,
  kit, or ref-resolver — the exotic subsystems are entirely invisible rather than reported as
  out-of-scope.

**Single most important defect:** the importer is **fail-fast on the first unsupported field**
instead of accumulating a complete, enumerated loss report. `_reject_unknown` raises a `MurlocsError`
on the first offending construct, so a manifest this exotic can never be characterized in one pass.
The fix is to collect every unsupported field/construct across all checks, invariants, stewards, and
top-level keys into a single loss report and return it, rather than aborting on the first mismatch —
and to make `inventory` resilient to a manifest it cannot fully translate.

**Correct behavior to preserve:** the refusal is clean (no traceback) and truthful as far as it goes.
The importer already models pillars, `stop_and_ask`, judgments, typed edges, scopes, and the core
invariant fields, so once checks (`kind`/`proves`) and invariant `proof_contains` are handled and the
loss report is made cumulative, dori's *manifest data* is largely translatable. The genuinely
out-of-scope constructs — `archetypes/`, `kit.toml`, and the `@inv:` resolver in `refs.py` — should
be documented as unmodeled and, at minimum, surfaced by `inventory` rather than left invisible.
