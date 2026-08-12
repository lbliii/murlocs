# Backlog-truth acceptance anchors

Backlog-truth treats "done" as a derived fact: a work item is proven only when
an executable acceptance test exists and (in later tasks) passes. Murlocs already
binds guidance claims to proof via manifest invariants and `proof_contains`
anchors. Acceptance anchors are the same mechanism aimed at *work items* instead
of *guidance claims*.

## Manifest model

A work item may declare an offline-discoverable acceptance anchor:

```toml
[[work_items]]
id = "206"
issue = 206
acceptance = "pytest:issue(206)"
```

- `id` — stable work-item identifier within the manifest network.
- `issue` — optional GitHub issue number for human-readable diagnostics.
- `acceptance` — `adapter:reference` string resolved without network access.

During `murlocs check`, every declared anchor is resolved against discovered
tests. A missing test produces an `[acceptance-anchor]` finding.

## Pluggable adapters

The adapter seam keeps discovery language-agnostic. Each adapter maps a
reference string to executable test locations under configured coverage roots.

| Adapter | Reference example | Discovery rule (offline) |
| --- | --- | --- |
| `pytest` | `issue(206)` | AST scan for `@pytest.mark.issue(206)` on functions, classes, or module-level `pytestmark` in `test_*.py` files |
| `jest` *(planned)* | `issue(206)` | Test name or tag convention, e.g. `describe('issue #206')` or `@issue 206` in docblocks — not implemented yet |
| `go` *(planned)* | `issue(206)` | Test function naming, e.g. `TestIssue206_*` in `*_test.go` files — not implemented yet |

Register future adapters in `murlocs.acceptance._ADAPTERS` and document their
reference grammar here before enabling them in manifests.

### Pytest marker (shipped)

Register the marker in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
markers = [
  "issue: associate a test with the GitHub issue(s) whose acceptance criteria it proves",
]
```

Mark acceptance tests:

```python
@pytest.mark.issue(206)
def test_work_item_acceptance():
    ...
```

Class-level and module-level markers propagate to every test in the scope,
matching pytest's ordinary marker inheritance.

## Offline discovery

Discovery never calls GitHub and never executes tests.

- **Library API:** `murlocs.acceptance.collect_pytest_issue_tests(root, test_roots)`
- **Check integration:** `murlocs.acceptance.acceptance_anchor_findings(manifest)`
- **CLI helper:** `python scripts/issue_coverage.py --issue 206`
- **Closure gate:** `python scripts/check_closure_acceptance.py` (see below)

The helper scripts mirror the chirp backlog-truth tooling: stdlib-only through
Murlocs, AST-based, suitable for pre-commit or air-gapped laptops.

## Acceptance strength (mutation / revert)

Presence of an acceptance test is necessary but not sufficient. A tautological
test (`assert True`) can satisfy discovery while proving nothing about the
implementation. Strength checking (issue #209) adds a coarse mutation signal:

1. Run the linked `issue(N)` tests on the clean tree — they must **pass**.
2. Temporarily revert the changed implementation paths to their pre-change
   baseline snapshots.
3. Re-run the same tests — they must **fail**.
4. Restore the working tree.

If the tests still pass after the revert, the anchor is weak and is rejected.

- **Library API:** `murlocs.acceptance.verify_acceptance_strength(root, issue, baseline_snapshots=...)`
- **CLI helper:** `python scripts/check_acceptance_strength.py --issue N --git-base ORIGIN/main --path src/...`

Scope stays on acceptance anchors only: the helper runs the linked `issue(N)`
node ids, never the whole suite. Full mutation testing remains out of scope.
Unit tests inject a runner callback so the strength contract stays deterministic
and offline-friendly without requiring network access.

```bash
# Example: strength-check Closes #209 against pre-change acceptance.py
python scripts/check_acceptance_strength.py --issue 209 \
  --git-base origin/main --path src/murlocs/acceptance.py
```

## Relationship to guidance proof anchors

| Kind | Subject | Example |
| --- | --- | --- |
| Proof anchor | Guidance invariant or registered check | `proof_contains = "pytest"` in `[checks.pytest]` |
| Acceptance anchor | Backlog work item | `acceptance = "pytest:issue(206)"` in `[[work_items]]` |

Both are offline string references resolved by deterministic repository scans.
Drift reconciliation (#208) and mutation strength (#209) build on this model
but are out of scope here.

## Closure gate (#207)

A pull request that claims `Closes` / `Fixes` / `Resolves` `#N` fails the
closure-acceptance check unless:

1. an offline acceptance anchor exists for `#N` (today:
   `@pytest.mark.issue(N)` under configured test roots), **or**
2. the PR body declares an explicit exemption:

   ```text
   Acceptance #N: n/a (reason)
   ```

The gate turns "done" into a derived fact at merge time. It does **not** verify
anchor *strength* (that is #209) and does **not** auto-close issues (that is
#208).

### How to run

```bash
# Local / CI: PR body via env, file, or stdin
PR_BODY='Closes #207' python scripts/check_closure_acceptance.py
python scripts/check_closure_acceptance.py --body-file /tmp/pr-body.md
```

- **Library API:** `murlocs.acceptance.evaluate_closure_acceptance(body, root)`
- **Script:** `scripts/check_closure_acceptance.py` (stdlib through Murlocs, exit 0/1)
- **Workflow:** `.github/workflows/closure-acceptance.yml` runs on `pull_request`
  and feeds `${{ github.event.pull_request.body }}`

### Branch protection (advisory → required)

The workflow is **advisory until it is a required status check**. To make it
binding:

1. Merge a PR that adds `.github/workflows/closure-acceptance.yml`.
2. In GitHub → **Settings → Branches → Branch protection rules** for `main`,
   enable **Require status checks to pass before merging**.
3. Add the check named **Closure claims have acceptance anchors** (job name
   from the workflow) to the required list.

Until step 3, the job still runs and reports failures on the PR Checks tab, but
merge is not blocked. Keep the workflow required once the dogfood PR for #207
has proven the fail/pass paths above.
Closure enforcement (#207) and drift reconciliation (#208) build on discovery;
mutation strength (#209) is the faithfulness gate documented above.
