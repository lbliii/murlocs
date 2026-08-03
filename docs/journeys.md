# Primary user journeys

These recipes are the release-level path through Murlocs' primary jobs. The commands and their
safety guarantees are exercised against disposable, committed Git repositories by
`tests/test_acceptance_journeys.py`; focused command documentation remains in the linked topic
guides.

## Bootstrap a repository

Declare coverage deliberately, review the generated model, and add maps until coverage is
structurally complete. Previewing initialization and compilation must not change files or Git
status.

```bash
murlocs --dry-run init --coverage-root src --coverage-root tests
murlocs init --coverage-root src --coverage-root tests
murlocs add-scope src/app --owners @app --owners @platform
murlocs --dry-run compile
murlocs compile
murlocs check --format json
murlocs explain src/app/core.py --format json
```

See [Adoption status and coverage](adoption.md) for the distinction between structural coverage
and semantic truth.

## Roll out owned scopes progressively

`init` establishes the root scope. Add a domain and then a nested domain, assigning owners and
recording any intentionally deferred source area. With CODEOWNERS validation enabled, the preview
reports the exact entry an owner must approve; apply fails without partial writes until it exists.
For a single owner, the short form remains
`murlocs --dry-run add-scope src/app --owners @app`; repeat each option when there are several
owners or deferrals.

```bash
murlocs --dry-run add-scope src/app \
  --owners @app --owners @platform \
  --defer 'legacy=migrating later' --defer 'examples=adopting later'
# Review and add: /.murlocs/layers/src-app.toml @app
murlocs add-scope src/app \
  --owners @app --owners @platform \
  --defer 'legacy=migrating later' --defer 'examples=adopting later'
murlocs --dry-run add-scope src/app/api --owners @api
# Review and add: /.murlocs/layers/src-app-api.toml @api
murlocs add-scope src/app/api --owners @api
murlocs check
```

See [Layered manifests](layers.md) for ownership policy and composition semantics.

## Adopt legacy guidance with rollback

Inventory and both diff modes are read-only. Import creates a candidate without taking ownership;
resolve every reported proof-debt item before adoption. Preview adoption, pruning, and rollback,
then retain the migration backup until rollback has been exercised. Rollback refuses to overwrite
a post-adoption map edit.

```bash
murlocs inventory
murlocs diff --mode semantic
murlocs diff --mode rendered
murlocs import --from stewards --output .murlocs/manifest.toml
# Review the candidate and resolve proof debt.
murlocs --dry-run adopt
murlocs adopt
murlocs check
murlocs explain src/app.py
murlocs --dry-run prune
murlocs prune
murlocs --dry-run rollback
murlocs rollback
```

## Repair ordinary drift safely

Edit the manifest or layer source, never a generated map. `check` distinguishes source drift from
output drift. After reviewing source changes, compile; if a generated map was edited, restore its
last managed bytes before compiling because Murlocs will not overwrite it.

```bash
murlocs check --format json
murlocs --dry-run compile
murlocs compile
murlocs check
```

## Score recorded evaluation runs

Capture all three arms outside the scoring command, then ingest the versioned task and run files.
The evaluator gates efficiency on correctness, preserves raw evidence in deterministic JSON, and
does not mutate the target repository or invoke a model.

```bash
python -m murlocs.eval --task evaluation/task.toml --runs evaluation/runs.json
python -m murlocs.eval \
  --task evaluation/task.toml \
  --runs evaluation/runs.json \
  --output evaluation/results
```

See [Guidance efficiency evaluation](evaluation.md) for the version 1 schemas and collection
methodology.

## Activate Murlocs around agent work

An integration first tests the exact `.murlocs/manifest.toml` presence signal. A healthy task-start
may remain silent. Forecast intended paths before editing, reassess actual paths after editing, and
obtain fresh `check` plus `impact` receipts after the last mutation before completion:

```bash
murlocs check --format json
murlocs impact --path src/app/core.py --format json
# Edit, then repeat with every actual task path.
murlocs check --format json
murlocs impact --path src/app/core.py --format json
```

These commands illustrate the typed read-only operations, not a shell-based adapter protocol.
Generated guidance is the prompt-mediated fallback; Git hooks and CI can enforce the same lifecycle
when a host has no native events. See [Portable agent activation lifecycle](activation-lifecycle.md)
for the normative request/response, caching, timeout, and completion-evidence contract.
