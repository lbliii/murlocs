# Host capability matrix

This document publishes version 1 of the Murlocs host support matrix. It answers
which agent hosts and orchestrators can use Murlocs, at which support tier, and
what portable fallback remains when a host feature is absent.

The machine-readable source of truth is
[`tests/fixtures/host-capability-matrix/v1/matrix.json`](../tests/fixtures/host-capability-matrix/v1/matrix.json).
Its shape is documented by
[`schema.json`](../tests/fixtures/host-capability-matrix/v1/schema.json).
`murlocs.host_capability.load_host_capability_matrix` validates the fixture and
resolves an **effective** tier. Updating claims is a reviewable fixture edit:
change evidence paths and `verification_date`, then re-run the loader tests.

## Support tiers

| Tier | Meaning |
| --- | --- |
| `native` | The host understands the portable activation lifecycle without a Murlocs adapter. Requires checked-in observed evidence. |
| `adapted` | A repository-local Murlocs adapter bridges host hooks or events onto the portable lifecycle. |
| `tool-only` | The host can consume generated guidance and/or call CLI/MCP tools, but has no verified lifecycle hook bridge. |
| `unknown` | Default when checked-in evidence is absent or stale. Never invent a stronger tier. |

`claimed_tier` is the author assertion in the fixture. `effective_tier` is what
callers must use: it becomes `unknown` when profile evidence is empty, a cited
path is missing from the repository, or `verification_date` is older than
`evidence_max_age_days` (180 days in v1).

## Claim basis

Every capability row separates documentation from conformance:

| `claim_basis` | Meaning |
| --- | --- |
| `documented` | Supported by checked-in documentation or adapter declarations. Not proof the live host enforced the seam. |
| `observed` | Backed by adapter code under test, conformance/transport tests, or a recorded pilot. |

A `documented` claim may justify `tool-only`. `native` and stronger lifecycle
enforcement claims require `observed` evidence and a fresh `verification_date`.

## Portable fallbacks

When a host feature is missing, Murlocs remains usable through repository-local
paths that do not depend on that host:

| Capability | Default portable fallback |
| --- | --- |
| Instruction discovery / scoping | Generated `AGENTS.md` maps |
| Refresh timing | Operator- or agent-initiated `murlocs check` / `impact` (CLI) |
| Size constraints | CLI/MCP tool results rather than host context injection |
| MCP | CLI entry points |
| Hooks / enforcement | `murlocs hook install` Git hooks, then CI |

Removing an adapter must leave generated guidance, the CLI, Git hooks, and CI
intact. See [activation lifecycle](activation-lifecycle.md),
[Git hooks](git-hooks.md), and [adapter conformance](adapter-conformance.md).

## Verified profiles

Resolved against the checked-in fixture. Re-load with a current `as_of` date
before treating a non-`unknown` tier as current.

| Host | Kind | Claimed | Effective when evidence fresh | Tested version | Verification date |
| --- | --- | --- | --- | --- | --- |
| OpenAI Codex | agent-host | `tool-only` | `tool-only` | repository `AGENTS.md` discovery (evaluated 2026-08-03) | 2026-08-03 |
| Claude Code | agent-host | `adapted` | `adapted` | `claude-code-hooks` adapter v1 | 2026-08-03 |
| Cursor | agent-host | `unknown` | `unknown` | not recorded | — |
| GitHub Copilot (CLI and cloud agent) | orchestrator | `adapted` | `adapted` | `github-copilot-hooks` adapter v1 | 2026-08-03 |

No profile claims `native`. Copilot cloud agent is the orchestrator row required
by issue #139; Claude Code and Codex cover interactive agent hosts; Cursor stays
`unknown` until host-specific evidence is checked in.

### Capability summary

| Host | Discovery / scoping | Refresh | Size | MCP | Hooks |
| --- | --- | --- | --- | --- | --- |
| Codex | Generated `AGENTS.md` (documented) | CLI/check/impact only | Repository budget only | Unwired; CLI portable | None; Git/CI fallback |
| Claude Code | Exact manifest discovery via adapter (observed) | SessionStart / tool / Stop seams | 9000-byte context packets | Not the activation path | `.claude/settings.json` bridge |
| Cursor | Portable `AGENTS.md` only; host specifics unknown | Unknown | Unknown | Unknown | Unknown; Git/CI fallback |
| GitHub Copilot | Exact manifest discovery via adapter (observed) | sessionStart / tool / agentStop | 9000-byte context packets | Not the activation path | `.github/hooks/murlocs.json` bridge |

### Limitations (condensed)

- **Codex:** No repository-local lifecycle hook contract was found during the
  2026-08-03 host evaluation recorded in
  [github-copilot-adapter.md](github-copilot-adapter.md).
- **Claude Code:** Prompt-mediated prospective impact; host timeouts and the
  eight-block Stop override remain fail-open. Details:
  [claude-code-adapter.md](claude-code-adapter.md).
- **Cursor:** Default `unknown`. Do not infer native or adapted support from
  generated maps alone.
- **GitHub Copilot:** Host timeouts and repeated-stop overrides are fail-open;
  Git hooks and CI remain required enforcement. Details:
  [github-copilot-adapter.md](github-copilot-adapter.md).

## Updating evidence

1. Edit `tests/fixtures/host-capability-matrix/v1/matrix.json` only.
2. Cite repository-relative evidence paths that already exist (adapter modules,
   docs, hook configs, tests, pilots).
3. Set `verification_date` to the day the evidence was reviewed.
4. Keep `claim_basis` honest: `observed` only when tests or pilots back the row.
5. Prefer `unknown` or `tool-only` over inventing `native`.
6. Run `pytest tests/test_host_capability_matrix.py` and refresh this document's
   summary table if profile identity or tiers change.

The loader never executes host binaries, never installs adapters, and never
treats agent chat as freshness evidence.
