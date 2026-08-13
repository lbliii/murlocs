# Self-hosting dogfood tranche — 2026-08-12

Issue: #97  
Protocol: [Self-hosting dogfood protocol](../self-hosting-dogfood.md)  
Sheet contract: `io.murlocs.self-hosting-dogfood` / schema 1  
Checked-in sheet: `tests/fixtures/self-hosting-dogfood/v1/tranche-2026-08-12.json`

## Status

**Structured self-hosting tranche complete. Proceed to the live multi-repository passive-loop
pilot (#68).**

Healthy hooks and check/impact surfaces remained enabled. No unresolved false blocker remained.
The sheet records seven fresh-session tasks with private expectation commitments. Privacy is
preserved: no prompts or transcripts are retained.

## Task results

| Task id | Theme | Classification | Cause | Evidence (safe codes) |
| --- | --- | --- | --- | --- |
| `hook-cli-refactor-76` | ordinary-hook-cli | useful-silence | repository-process | Merged PR #76 behavior-preserving hook CLI extraction |
| `dry-run-clean-tree` | dry-run-noop | useful-silence | engine | `murlocs -n compile` reported unchanged maps / no writes |
| `hook-runner-install-fail-closed` | hook-install-failure | useful-intervention | hook | Hook install tests fail closed for unresolved runners (#96) |
| `compact-outcome-blind-reveal` | compact-outcomes | mixed | adapter | Prior blind/reveal retained integration; wording defects already change-tracked |
| `authority-layer-impact` | authority-semantics | useful-intervention | engine | Impact on `.murlocs/layers/core.toml` → authority-required `@lbliii`, may continue |
| `ordinary-docs-index` | ordinary-observer | useful-silence | agent-judgment | Fresh worktree agent fixed docs index gap; keep-enabled true |
| `modified-map-repair-boundary` | deterministic-repair | mixed | engine | Compile refuses modified generated maps; repair does not silent-overwrite |

## Blind / revealed summary

Discovery codes: Agents maps were present; CLI appeared during ordinary checks; no product cue in
task prompts.

Novelty codes: owned-layer authority routing, dry-run truthfulness, fail-closed hook install.

Noise codes: some code-path impact results escalate when a quieter silence expectation was held;
modified-map repair expectations need clearer agent-facing actionability.

Keep-enabled codes: retain hooks, retain check/impact, prefer actionability fixes before new
policy primitives.

## Cross-check

Hook, CLI, CI, latency, rerun, repair, and escalation evidence were cross-checked against the
sheet. No bypass of fail-closed install or generated-map overwrite protections was observed.

## Recommendations into the passive-loop program

| Bucket | Items |
| --- | --- |
| Feed #62 | Keep deterministic repair boundaries explicit for modified generated maps |
| Feed #63 | Continue hot-path budget watch during live cohort reviews |
| Feed #65 | Compact-outcome wording fixes already landed; keep in regression fixtures |
| Feed #67 | Fresh-session acceptance journeys remain the countable harness |
| Feed #68 | Proceed to live multi-repo cohort; measure false-positive impact routing |
| Defer | New policy primitives motivated only by dogfood noise samples |
| Remove | _(none)_ |

## Exit judgment

Murlocs is useful enough on its own repository to proceed to the formal multi-repository
passive-loop pilot. Prefer measurement and actionability fixes over expanding guidance semantics.
