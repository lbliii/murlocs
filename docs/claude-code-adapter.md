# Claude Code adapter

Murlocs' second independent agent-host adapter is the repository-local Claude
Code hook integration. Its configuration is `.claude/settings.json`; the
installed `murlocs-claude-adapter` entry point accepts only the documented
hook payload. It never installs packages, changes user-level Claude settings,
writes repository state, runs a manifest-registered command, or accepts an
agent-supplied root, freshness token, or authority decision.

## Why Claude Code is an independent host

Claude Code has a separately implemented command-hook lifecycle. Its project
configuration is `.claude/settings.json`, rather than Copilot's
`.github/hooks/murlocs.json`; its hook input uses `session_id`, `tool_name`,
and `tool_input`; and it delivers decisions through `hookSpecificOutput`.
The adapter uses Claude Code's documented `SessionStart`, `PreToolUse`,
`PostToolUse`, and `Stop` seams. See Anthropic's [hooks reference](https://code.claude.com/docs/en/hooks).

| Portable event | GitHub Copilot adapter | Claude Code adapter | Guarantee |
| --- | --- | --- | --- |
| task-start | `sessionStart` | `SessionStart` | Host calls a read-only `check`; a finding is context, not approval. |
| prospective impact | `preToolUse` | `PreToolUse` for `Edit` and `Write` | Hook-backed context before the edit; v1 intentionally leaves the decision prompt-mediated. |
| post-edit | `postToolUse` | `PostToolUse` for `Edit` and `Write` | Host delivers compact `check` and `impact` feedback after a successful edit. |
| pre-commit | shell `preToolUse` | `PreToolUse` for `Bash` | The bridge recognizes a `git commit` invocation and calls the existing typed Git gate. |
| pre-completion | `agentStop` | `Stop` | Host can block and feed back missing or blocking evidence, but neither host provides an absolute terminal guarantee. |

Both integrations are hook-backed at their listed seams. The portable v1
prospective-impact scenario is prompt-mediated: although Claude Code's
`PreToolUse` API could deny an edit, this adapter only adds the compact finding
to context so one host's pre-edit policy does not silently revise the shared
contract. The prompt-mediated part is remediation, never a claim that it was
fixed or approved. Other Claude tools (including MCP edits and tools outside
`Edit` and `Write`) are unavailable to this adapter version. They retain the
generated-guidance, Git-hook, and CI fallback paths; the adapter reports no
false receipt for them.

## Read-only behavior and lifecycle limits

`SessionStart` runs typed `check`. `PreToolUse` runs prospective `impact` only
when Claude supplied an in-root edit path. `PostToolUse` runs `check` and
`impact` for the normalized in-root path. Parent escapes, out-of-root absolute
paths, NUL-containing paths, and symlink escapes never reach Murlocs operations. The Bash pre-commit
bridge shell-parses direct Git invocations, including global Git options and
shell segments; inert text such as `echo git commit` is not treated as a gate.

`Stop` obtains staged, unstaged, deleted, and untracked paths from NUL-delimited
Git output. The read-only queries disable external diff, text conversion, and
optional Git locks. It then runs fresh `check` and `impact`. Only a typed
blocking outcome or unavailable completion evidence blocks stopping. A
non-blocking advisory remains visible as a Claude Code `systemMessage` and does
not become host policy.

Claude Code can override its own Stop hook after eight consecutive blocks.
That is an explicit host boundary, not a passing completion receipt. The
portable Git hook and CI remain the enforceable fallback if an agent continues
to stop without resolving a finding. When `stop_hook_active` reports a repeated
continuation, the adapter includes that eight-block boundary in its reason.

An adapter failure caught while the process is still running uses the current
event's documented response shape: pre-commit and Stop fail closed, while
task-start, prospective impact, and post-edit add visible unavailable context.
A Claude Code timeout can terminate the command before it emits that response;
the host treats that missing result as non-blocking. Such a timeout is never a
passing receipt, and the Git hook and CI fallback remain required enforcement.

The production `ClaudeAdapterDriver` runs the shared v1 conformance suite under
the `claude-code-hooks` identity; it does not rewrite fixture-adapter results.
It verifies the same out-of-band trusted context, token freshness, outcome
envelopes, fallback declarations, and no-write behavior as the Copilot
adapter. Transport-level tests additionally exercise this production bridge's
Claude-shaped output, path confinement, newline-containing Git paths, advisory
completion behavior, failure responses, and command recognition. No
adapter-specific rule is added to Murlocs' authored layers.

## Install and remove

Install Murlocs into the project environment so
`murlocs-claude-adapter` is on the hook's `PATH`. Commit only
`.claude/settings.json` with the repository; the bridge does not change
`~/.claude/settings.json` or install itself. To disable it, use Claude Code's
documented hook controls. To remove it, delete only `.claude/settings.json`.
Generated `AGENTS.md`, `murlocs hook install`, and CI continue to work without
the adapter.
