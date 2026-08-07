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

Repeat `--path PATH` once per changed path. If a repository-relative path begins with `-`, use
the inline spelling `--path=-dash.py` so it cannot be mistaken for another terminal option.

The revision argument is passed as one revision expression to read-only `git diff --name-only`.
It can therefore also be `HEAD` when comparing the index and working tree with the current commit.
Every diff disables external diff drivers and text conversion, so repository configuration cannot
turn impact analysis into command execution. When explicit and Git-derived paths are both supplied,
Murlocs reports their union while retaining each path's provenance; an explicit synchronized source
does not lose its current semantic routing merely because another path also came from Git.

## Review policy v3

Every declared scope has exactly one status:

- `required`: a changed path is owned by the scope or names its generated map, a contributing
  guidance source, network review protocol, manual evidence, or registered-check configuration.
  A root control-plane manifest or review-protocol change requires every scope to be reviewed.
  A changed generated map requires every scope whose active root-to-target guidance chain contains
  that map. For guidance-source edits, Murlocs uses current rendered drift when available; a
  synchronized source that contains a root-level list or check contribution is still routed through
  every chain. Git revision comparison also detects root-render changes from removing the last
  global field, adding/removing scopes or invariants, moving an invariant between scopes, and
  changing the command-backed invariant ratio. An invariant statement-only edit remains local when
  the root summary is unchanged.
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

### Source annotation attachments

Declared source annotations add a deliberately narrow routing signal. An explicit `--path` to a
declared annotation-bearing source is **path-only** evidence: impact routes the declared invariant
and its source owners, but does not guess whether the marker was added, removed, or moved. This is
the mode used by the exact staged view in a hook.

With `--revision-range`, impact reads only the old manifest, its declared layers, and the finite
set of declared annotation source blobs. It uses `git cat-file` with `--no-lazy-fetch`, bounded
per-blob and aggregate byte budgets, a 10-second timeout, no replacement objects, and no Git diff,
text conversion, filters, hooks, or repository commands. The report can then describe these
attachment events with before/after file and line locations:

- `added`, `removed`, `moved`, or `duplicated` markers; and
- `declaration-changed` when a reviewed annotation declaration changes its invariant, scope,
  file, kind, version, or declaring owners.

These events are attachment changes only. They never assert that the invariant is stale, false, or
proven by a source comment. Renames are handled from the old and new path states, while deletion
is an attachment removal. A revision and explicit-path union retains both path-only and revision
evidence deterministically.

If the old revision is unavailable (including shallow history), a baseline manifest or declared
blob is missing or malformed, a path is unsafe, or an annotation source uses an unsupported form,
impact records `comparison: "uncertain"` and routes conservatively instead of returning an
annotation-bearing scope as unaffected. It does not scan repository history or undeclared source
paths to recover from that uncertainty.

The source file's layer kind or a curation record's `target_scope` is not semantic confinement.
Root list subjects (`pillars`, `search_policy`, `operating_rules`, `stop_and_ask`, and
`done_criteria`) and checks affect the root map and therefore every active chain. Scope-local
judgments retain focused routing. Exact curation routing is derived from the prospective rendered
map diff; changed-path routing is conservative when only a synchronized source path is available.

Workspace source routing first intersects drift with maps contributed by that source, rather than
attributing every dirty generated map to every source path. The compile lock's per-source hashes
identify which sources changed; a missing expected generated map is drift, not absence of evidence.
A source synchronized with the lock retains its global-list/check routing even when one of its
generated maps has unrelated output-only drift. When more than one source is stale, Murlocs uses a
Git blob matching the locked source hash, when available, to distinguish local-only edits from
root-render changes. That lookup considers at most 64 path-touching commits across current refs,
checks their raw blob sizes in one `git cat-file --batch-check`, then reads eligible content in one
`git cat-file --batch` call. Historical source blobs are limited to 1 MiB each and 8 MiB for the
complete candidate set. The three Git reads each have a 10-second timeout, disable lazy object
fetching through both `--no-lazy-fetch` and `GIT_NO_LAZY_FETCH`, disable optional locks, ignore
replacement objects, and do not invoke diff/textconv drivers, clean/smudge filters, or hooks. A Git
version that does not support the global no-lazy-fetch option exits before history or object access;
Murlocs does not retry without the option and instead uses conservative routing.
Each candidate is one complete object expression per line. Murlocs relies on Git's documented
ordered batch responses and requires the content response to repeat the exact ordered blob-OID and
size sequence from the metadata response. This preserves spaces, colons, glob characters, and
leading dashes in valid layer paths without interpreting them as batch metadata. LF, CR, and NUL
cannot be represented safely in this newline-delimited lookup; if such a source path reaches impact
analysis, Murlocs does not send it to Git and uses conservative routing.
If no safe baseline exists, it fails closed for ambiguous root drift and reports that the root map
cannot be attributed more narrowly. Non-Git repositories, missing objects, malformed batch output,
timeouts, over-limit blob sets, and a matching lock blob older than the 64-commit search window all
take this conservative path. This is routing evidence, not a claim that one source caused an
unrelated dirty map.

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

The additive `annotations` member makes the attachment evidence explicit:

- `comparison` is `not-requested`, `path-only`, `compared`, `compared-no-attachment-change`, or
  `uncertain`;
- `changes` contains stable attachment identifiers, event kinds, invariant/scope/owner routing,
  and before/after locations; and
- `uncertainty` records bounded baseline limitations without copying source text.

The terminal report renders the same comparison and events. Its outcome and hook/CI receipts keep
the same affected scopes, maps, owners, reasons, and advisory next action; no integration gains
authority to edit an annotation or decide semantic truth.

The command exits nonzero only for invalid input or an unreadable manifest or Git range. Consumers
choose whether `required` or `recommended` is informational or blocking; Murlocs does not silently
turn review routing into a merge policy.

The additive [`outcome` envelope](outcome-envelope.md) expresses both statuses as advisory.
`required` selects `authority_required` and names the affected scopes, maps, and owners;
`recommended` selects `agent_action`. This typed routing does not alter impact's exit code or grant
approval authority.

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

## pre-commit integration

The built-in passive runner validates the exact staged index and keeps impact routing advisory:

```yaml
repos:
  - repo: local
    hooks:
      - id: murlocs
        name: Validate Murlocs staged guidance
        entry: murlocs hook run pre-commit
        language: system
        pass_filenames: false
        always_run: true
```

See [Passive Git hooks](git-hooks.md) for the opt-in installer, existing-manager snippets, exact
Git-view boundary, lifecycle receipts, and failure behavior. Neither integration causes Murlocs to
write repository files or run the commands registered in the manifest.
