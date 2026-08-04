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
| GitHub Copilot CLI and cloud agent | Repository `.github/hooks/*.json` supports `sessionStart`, `postToolUse`, and `agentStop`; `agentStop` can block and supply a continuation reason. | Command-hook timeouts are fail-open and the host eventually ends repeated stop blocks. | Selected: project-local start, post-edit, and completion seams. |
| OpenAI Codex | Generated repository `AGENTS.md` supports Murlocs discovery. | No documented repository-local start/post-edit/stop hook contract was found for required enforcement boundaries. | Not selected for v1; generated guidance and Git/CI fallbacks remain supported. |
| Git hooks and CI | Portable Murlocs fallbacks. | Not an active agent task-start surface. | Retained as fallbacks. |

The host facts are checked against GitHub's [hooks overview](https://docs.github.com/en/copilot/concepts/agents/hooks), [hooks reference](https://docs.github.com/en/copilot/reference/hooks-reference), and [repository hook configuration guidance](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/use-hooks). Recheck these external facts before relying on a newer Copilot release.

## Lifecycle and authority behavior

`sessionStart` discovers `.murlocs/manifest.toml` exactly and runs typed,
read-only `check`. A healthy result is silent; a finding is delivered as a
compact JSON outcome packet. `postToolUse` is restricted to documented
edit/create tool names and runs `check` then `impact` for Copilot's explicit
path. Before an edit/create tool runs, `preToolUse` provides prospective impact
and prevents the operation only when Murlocs returns a non-silent structured
outcome. `agentStop` derives ordinary Git changed paths, then runs fresh `check`
and `impact`. A blocking finding or unavailable impact path blocks completion
and returns the structured packet as the next-turn reason.

The adapter forwards typed remediation unchanged. An `authority_required`
finding contains `request_authority` with human authority; it is never treated
as approval. Copilot's `preToolUse` hook recognizes a `git commit` shell
invocation and delegates the exact-index gate to Murlocs' existing read-only
pre-commit integration. Other shell commands are allowed unchanged.

Copilot documents timeout fail-open behavior and a repeated-stop guard, so a
real Copilot CLI and cloud-agent pilot remains necessary external acceptance
evidence before claiming absolute completion enforcement. Git hooks and CI stay
the enforcing portable fallback.

## Install and remove

Install Murlocs into the project environment with the normal dependency tool;
the hook requires `murlocs-copilot-adapter` on that environment's `PATH`. Do
not use a global install for this integration. Commit the hook JSON with the
repository; the adapter never installs itself or changes a user-global setting.

To disable it temporarily, use Copilot's documented hook-disable control. To
remove it, delete only `.github/hooks/murlocs.json` and stop invoking the
project entry point. Generated `AGENTS.md`, the Murlocs CLI, `murlocs hook
install`, and CI do not depend on that file and continue to work.
