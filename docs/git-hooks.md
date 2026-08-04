# Passive Git hooks

Murlocs can enforce its read-only lifecycle at the two Git boundaries that exist even when no
human is actively driving the coding session:

```bash
murlocs hook install
murlocs hook status
```

Installation is deliberately opt-in and conservative. It writes only the default `pre-commit`
and `pre-push` slots in the repository's common Git directory, and only when every selected slot
is absent or already contains the exact Murlocs-owned bytes. It refuses a configured
`core.hooksPath`, a linked worktree, an existing hook or hook manager, and a modified Murlocs hook.
It never replaces, wraps, or silently chains another manager. `murlocs hook uninstall` removes
only byte-exact Murlocs-owned files.

## Durable runners

The generated dispatcher pins one verified, absolute `murlocs` executable rather than resolving
`murlocs` from a later Git process's `PATH`. Installation asks only the selected runner for its
structured runtime identity, then records its semantic version, opaque package-content build ID,
and a SHA-256 digest of the runner file before it writes any hook. At execution, the dispatcher
passes the expected build ID back to `hook run`; a replacement at the same path and semantic
version fails closed before repository assessment. This makes a normal user-level tool install or
a packaged console script deterministic:

```bash
python -m pip install --user murlocs
murlocs hook install
```

By default, installation refuses a direct runner from a project virtual environment, including
`uv run murlocs hook install`; those environments are frequently recreated or removed. If an
organization deliberately owns a particular executable path for the hook lifetime, it may state
that contract explicitly:

```bash
murlocs hook install --runner /absolute/path/to/murlocs
```

The explicit path is still verified at install time and its final regular-file target is recorded.
No virtual environment path is inferred or silently retained. A missing or moved pinned runner makes the
hook fail closed with one repair instruction: run `murlocs hook install` again from the durable
installation. `murlocs hook status` never executes a runner named by mutable hook metadata: it
reports `installed`, `missing runner`, `runner changed` (the runner-file digest differs),
`legacy runner identity`, `modified`, `occupied`, or `absent`. The actual hook invocation is the
live build-ID check, so it also catches in-place package replacement when the launcher bytes did
not change.

Hooks installed by Murlocs before durable runner pinning are reported as `legacy`. Hooks with the
earlier pinned path-and-version metadata are reported as `legacy runner identity`. Both remain
byte-owned: `murlocs hook uninstall` removes their exact old bytes, while `murlocs hook install`
replaces those same bytes with the verified dispatcher. A legacy hook is never treated as an
unmanaged manager or stranded behind an unrepairable status. Malformed or extended dispatcher
metadata is intentionally `modified` and is never executed or replaced automatically.

Select one hook by repeating `--event`:

```bash
murlocs hook install --event pre-commit
murlocs hook uninstall --event pre-push
```

## What runs

`pre-commit` materializes the exact staged index. Unstaged worktree content is invisible. It runs
structured `check`, then structured `impact` with the staged delete/add path set. `check` findings
block; all impact findings remain advisory routing. A healthy run is silent and exits zero.
Managers may repeat `--path PATH` (including `--path=-dash.py`) to forward their staged filename
order. Each value must name an actually changed index path; duplicates are accepted as inert input,
while Murlocs still assesses the complete index delta so a filtered manager cannot hide a change.

`pre-push` strictly parses Git's bounded stdin as data and materializes each outgoing commit. It
runs a fresh pre-completion `check` and `impact` against those immutable commit views. Deletes,
renames, spaces, Unicode, leading dashes, and host-representable newline paths remain path values,
never shell syntax.

Both hooks have a single caller-owned deadline and fail closed on timeout, missing objects,
malformed Git responses, over-limit views, unsafe or colliding paths, and state/dependency races.
A staged or outgoing removal of an existing Murlocs manifest is treated as an explicit authority
boundary, not silent absence.

The adapter uses raw `ls-files`, `ls-tree`, and ordered `cat-file` responses. It disables lazy
fetching, replacement objects, optional locks, pagers, checkout filters, external diff drivers,
and text conversion. It does not run models, use the network, execute hooks recursively, execute
manifest-registered checks, repair guidance, or write the repository. The structured lifecycle
response includes bounded entry/blob/Git-call counters for observability.

## Passive hot-path budgets

`benchmarks/passive_hot_path.py` is the repeatable resource suite and
`tests/test_passive_hot_path.py` is its CI gate. It exercises task-start discovery, explicit and
staged impact, healthy and drifted pre-commit, and pre-push completion gating over small, layered,
multi-domain, 65+ history, and 80+ map networks. Healthy code-only hooks and focused local impact
are measured independently; stale multi-source history uses the conservative #53 fallback.

| Budget | Cold | Warm | Structural CI limit |
| --- | ---: | ---: | --- |
| Latency | 8 s | 7 s | Broad watchdog only; it catches stalls, not micro-regressions. |
| Git subprocesses | — | — | At most 24 per operation (history attribution at most 3). |
| Files read | — | — | At most 512 raw-view entries/source reads. |
| Peak memory bound | — | — | At most 96 MiB (64 MiB raw-view cap plus margin). |

The structural limits are the primary regression threshold because they are deterministic across
machines. Latency is intentionally generous and is never the sole CI signal. The benchmark counts
each materialized staged/commit entry plus the unique manifest sources, generated maps, lock, and
proof files consumed by the child operations. Direct operations count their unique structural
inputs. Completion metrics are aggregated from the batch's per-commit activation results; a
missing, invalid, or incomplete result fails the benchmark.
Peak memory is enforced from the raw-view structural cap, rather than `tracemalloc`: check and
impact run in child interpreters, so a parent-only allocation sample would misrepresent total use.
The suite configures hooks, external diff, and text conversion to leave a sentinel if they run,
then asserts the sentinel is absent. Its registered check exits if executed, so every healthy measurement also
proves it stayed inert. It does not install a Murlocs hook, execute a registered check, invoke a
model, or open the network.

The interactive default is the `pre-commit` staged-index mode. It runs one `check` and one `impact`
and is the hot path represented by the budget. `pre-push` is a completion gate, not an interactive
default: it materializes and assesses every non-deletion outgoing commit, so its cost grows with
the update count. The installer selects both `pre-commit` and `pre-push` when `--event` is omitted;
“interactive default” here means the latency-sensitive commit boundary. Likewise, ambiguous
stale-source attribution is deliberately bounded but too
expensive for a latency-sensitive default: #53 searches at most 64 path-touching commits, reads at
most 1 MiB per blob and 8 MiB total, and falls back to conservative required routing on any limit,
malformed response, race, or unavailable Git capability.

## Existing hook managers

Keep the manager in control and add the runner explicitly. The runner consumes pre-push updates
from standard input and preserves its exit status.

Generic shell manager entries:

```sh
# pre-commit
murlocs hook run pre-commit

# pre-push (forward the manager's original stdin)
murlocs hook run pre-push --remote-name="$1" --remote-url="$2"
```

For `pre-commit`, use a local system hook so the exact index is assessed once rather than once per
filename:

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

Manager configuration remains user-owned. Murlocs reports occupied slots and does not edit that
configuration. Manager snippets deliberately retain the manager's own runner resolution policy;
install a durable user-level Murlocs tool (or use an explicitly owned absolute path) before adding
one to that configuration.

## Structured receipts

Use `--format json` on `murlocs hook run` to receive the version-1 activation response. The Git
adapter owns the opaque state token and its adapter/version/session scope. Only the actual impact
operation receives before/after dependency tokens. Operation receipts digest canonical structured
operation bytes; stale results are discarded. The outcome sidecar carries the closed typed policy
from `check` and advisory routing from `impact`; hooks never execute its actions.

Pre-push emits an `io.murlocs.hook-batch` envelope containing one activation response per outgoing
non-deletion update. Remote names and URLs are accepted only as inert metadata and never opened.
