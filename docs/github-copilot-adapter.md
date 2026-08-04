# GitHub Copilot adapter

Murlocs' first production agent-host adapter is the repository-local GitHub
Copilot hook adapter. Its checked-in configuration is
`.github/hooks/murlocs.json`; the installed `murlocs-copilot-adapter` entry
point handles its hook payloads. It never changes user-level Copilot settings,
installs packages, writes repository state, runs a manifest-registered command,
or treats an agent message as freshness or approval evidence.

## Why GitHub Copilot first

We evaluated these documented surfaces on 2026-08-03.

| Candidate | Verified lifecycle surface | Limitation | Decision |
| --- | --- | --- | --- |
| GitHub Copilot CLI and cloud agent | Repository `.github/hooks/*.json` supports `sessionStart`, `preToolUse`, `postToolUse`, and `agentStop`; `agentStop` can block and supply a continuation reason. | `preToolUse` command errors are fail-closed, but hook timeouts are fail-open; errors on the other events are logged and skipped; the host overrides an eighth repeated stop block. | Selected: project-local start, prospective-impact, post-edit, and completion seams. |
| OpenAI Codex | Generated repository `AGENTS.md` supports Murlocs discovery. | No documented repository-local start/post-edit/stop hook contract was found for required enforcement boundaries. | Not selected for v1; generated guidance and Git/CI fallbacks remain supported. |
| Git hooks and CI | Portable Murlocs fallbacks. | Not an active agent task-start surface. | Retained as fallbacks. |

The host facts are checked against GitHub's [hooks overview](https://docs.github.com/en/copilot/concepts/agents/hooks), [hooks reference](https://docs.github.com/en/copilot/reference/hooks-reference), and [repository hook configuration guidance](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/use-hooks). Recheck these external facts before relying on a newer Copilot release.

## Lifecycle and authority behavior

`sessionStart` discovers `.murlocs/manifest.toml` exactly and runs typed,
read-only `check`. A healthy result is silent; a finding is delivered as a
compact JSON outcome packet. `postToolUse` is restricted to documented
edit/create tool names and runs `check` then `impact` for Copilot's explicit
path. Repository-confined absolute and relative tool paths are normalized;
parent escapes, NULs, and paths resolving through an outward symlink are
rejected. Before an edit/create tool runs, `preToolUse` provides prospective impact
and prevents the operation only when Murlocs returns a non-silent structured
outcome. `agentStop` derives staged, unstaged, deleted, and untracked paths from
NUL-delimited Git plumbing with external diff and text conversion disabled,
then runs fresh `check` and `impact`. A blocking finding or unavailable impact
path blocks completion and returns the structured packet as the next-turn
reason.

The adapter forwards typed remediation unchanged. An `authority_required`
finding contains `request_authority` with human authority; it is never treated
as approval. Copilot's `preToolUse` hook recognizes direct Git commit commands,
including command chains, an absolute Git executable, and Git global options
such as `-C`, then delegates to Murlocs' existing read-only exact-index gate.
Inert argument text such as `echo git commit` and unrelated shell commands do
not run the gate.

## Proof and host failure boundary

`CopilotAdapterDriver` translates the portable `io.murlocs.adapter` requests
and trusted conformance context under the same adapter identity and lifecycle
operation map used by the GitHub hook bridge. The complete #64 suite runs that
production driver; it does not subclass the reference fixture or rewrite an id.
Separate black-box tests drive the production `handle()` path through all
configured GitHub events, verify typed operation order and paths, and exercise
hostile path, symlink, untracked-file, NUL-delimited-name, runtime-failure, and
repeated-stop cases.

Copilot's host behavior remains a boundary the repository cannot strengthen:

- A command `preToolUse` crash or nonzero exit denies the tool, but a host
  timeout is fail-open and falls through to normal permission handling.
- Command errors and timeouts on `sessionStart`, `postToolUse`, and `agentStop`
  are logged and skipped. A valid adapter result can inject context or block;
  an adapter process failure cannot.
- `stop_hook_active` identifies a continuation forced by an earlier stop block.
  Murlocs keeps a still-blocking result visible, but Copilot overrides after
  eight consecutive blocks.

Consequently a real Copilot CLI and cloud-agent pilot remains external
acceptance evidence before claiming absolute host enforcement. Git hooks and CI
stay the enforcing portable fallback.

## Install and remove

Install Murlocs into the project environment with the normal dependency tool;
the hook requires `murlocs-copilot-adapter` on that environment's `PATH`. Do
not use a global install for this integration. Commit the hook JSON with the
repository; the adapter never installs itself or changes a user-global setting.

To disable it temporarily, use Copilot's documented hook-disable control. To
remove it, delete only `.github/hooks/murlocs.json` and stop invoking the
project entry point. Generated `AGENTS.md`, the Murlocs CLI, `murlocs hook
install`, and CI do not depend on that file and continue to work.
