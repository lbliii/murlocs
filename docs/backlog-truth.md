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

The helper script mirrors the chirp backlog-truth tooling: stdlib-only through
Murlocs, AST-based, suitable for pre-commit or air-gapped laptops.

## Relationship to guidance proof anchors

| Kind | Subject | Example |
| --- | --- | --- |
| Proof anchor | Guidance invariant or registered check | `proof_contains = "pytest"` in `[checks.pytest]` |
| Acceptance anchor | Backlog work item | `acceptance = "pytest:issue(206)"` in `[[work_items]]` |

Both are offline string references resolved by deterministic repository scans.
Closure enforcement (#207), drift reconciliation (#208), and mutation strength
(#209) build on this model but are out of scope here.
