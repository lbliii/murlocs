# Changed-path guidance impact

`murlocs impact` answers a routing question: which guidance owners should inspect a repository
change? It does not decide whether a claim is true, rewrite guidance, call a model, or execute a
registered check. `murlocs check` continues to report structural validity and drift; `impact`
reports possible semantic review need.

Pass one or more explicit paths, a Git revision range, or both. Inputs are normalized,
deduplicated, and sorted before classification:

```bash
murlocs impact --path src/api/service.py --path docs/api.md
murlocs impact --revision-range origin/main...HEAD
murlocs impact --revision-range origin/main...HEAD --format json
```

The revision argument is passed as one revision expression to read-only `git diff --name-only`.
It can therefore also be `HEAD` when comparing the index and working tree with the current commit.
When explicit and Git-derived paths are both supplied, Murlocs reports their union.

## Review policy v1

Every declared scope has exactly one status:

- `required`: a changed path is owned by the scope or names its generated map, a contributing
  guidance source, network review protocol, manual evidence, or registered-check configuration.
  A root control-plane manifest or review-protocol change requires every scope to be reviewed.
- `recommended`: a changed path falls inside the nearest non-root scope without matching declared
  ownership, or a required scope is connected to this scope by one incoming or outgoing declared
  edge. Edge propagation is deliberately one hop so a connected graph does not turn every change
  into a global review event.
- `unaffected`: no declared relationship associates the path with the scope.

Required always wins over recommended. Path containment is segment-aware, so `src/api-old` does
not match `src/api`. The root scope is not used as a catch-all path match; it is affected only by
declared ownership and the other explicit relationships above.

An affected status means “review this guidance in light of the change,” not “this guidance is
stale” or “this invariant is false.” Semantic truth remains a human or agent judgment backed by
the evidence and checks named in the report.

## Structured output

`--format json` emits schema version 1. Scope entries are sorted by id, reasons and owners are
sorted, and changed-path input order does not affect the result. Every scope entry includes:

- its `required`, `recommended`, or `unaffected` status and reasons;
- the root-to-target generated-map chain;
- contributing source layers and owners;
- invariants with their verification mode, evidence, and anchors;
- focused registered-check metadata, without running its command;
- incoming and outgoing declared edges; and
- the repository review-protocol path.

The command exits nonzero only for invalid input or an unreadable manifest or Git range. Consumers
choose whether `required` or `recommended` is informational or blocking; Murlocs does not silently
turn review routing into a merge policy.

## GitHub Actions reference

This job preserves the complete report as an artifact and writes a concise summary. A repository
can add a later policy step if required review must block merging.

```yaml
name: guidance-impact

on:
  pull_request:

jobs:
  report:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.14"
      - run: python -m pip install murlocs
      - name: Report guidance review impact
        run: |
          murlocs --output-file murlocs-impact.json impact \
            --revision-range "${{ github.event.pull_request.base.sha }}...${{ github.sha }}" \
            --format json
          python -c 'import json; d=json.load(open("murlocs-impact.json")); print(d["summary"])'
      - uses: actions/upload-artifact@v4
        with:
          name: murlocs-impact
          path: murlocs-impact.json
```

## pre-commit reference

The hook compares staged and working-tree content with `HEAD`, prints the human report, and leaves
the repository's own review policy to decide whether a result should block the commit:

```yaml
repos:
  - repo: local
    hooks:
      - id: murlocs-impact
        name: Report Murlocs guidance impact
        entry: murlocs impact --revision-range HEAD
        language: system
        pass_filenames: false
        always_run: true
```

Neither integration causes Murlocs to write repository files or run the commands registered in the
manifest.
