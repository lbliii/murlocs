# Multi-repository passive-loop pilot protocol

This protocol is the longitudinal complement to the
[no-prompt passive-agent acceptance harness](passive-agent-acceptance.md). Acceptance journeys prove
that a fresh agent can discover Murlocs and complete the five countable scenarios once. Issue #68
asks whether teams keep the passive loop enabled across ordinary multi-week work in at least two
materially different repositories, and whether the product improves guidance health without creating
noise.

`murlocs.passive_loop_pilot.validate_pilot_sheet` is the deterministic offline boundary. CI
validates the versioned example sheet in
`tests/fixtures/passive-loop-pilot/v1/example-sheet.json`. That example is a harness rehearsal with
`observation_status: simulated`. It is not a live pilot claim. A completed live pilot adds an
equally strict attributable sheet with executed observations only.

## Repository selection

Select at least two repositories that differ on every axis below. Do not count near-clones of one
topology as two pilots.

| Axis | Allowed values | Why it matters |
| --- | --- | --- |
| Size | `small`, `medium`, `large` | Hot-path cost and instruction budget behave differently at scale. |
| Scope topology | `shallow`, `layered`, `multi-domain` | Routing fan-out and silence depend on graph shape. |
| Guidance maturity | `bootstrap`, `migrating`, `mature` | Drift, remediation, and operator trust differ by maturity. |
| Primary agent workflow | `cli-hooks`, `copilot`, `claude`, `generated-guidance`, `mixed-host` | Lifecycle timing and disable/rollback paths are host-specific. |

Candidate profiles for a first live cohort (not yet executed here):

1. A compact mature layered network with Git hooks or a native adapter as the primary workflow.
2. A broader multi-domain or migrating network whose host exposes only generated-guidance fallback
   or a second independent adapter.

Public labels in the sheet use safe tokens. Private repository names, prompts, and transcripts stay
out of the checked-in evidence.

## Baseline before activation

Before enabling the passive loop in a pilot repository, capture and commit a baseline row:

| Baseline field | Meaning |
| --- | --- |
| `guidance_drift_findings` | Count of open drift/ownership/budget findings at activation. |
| `instruction_bytes` | Active-context or compiled instruction bytes used as the size proxy. |
| `agent_remediation_events` | Recent agent remediation actions that touched guidance or maps. |
| `routing_accuracy` | Fraction of sampled impact routings judged correct, or `null` if unmeasured. |
| `human_interventions` | Human interruptions attributed to Murlocs in the sampling window. |

Also record the rollback conditions, review cadence, and data-handling rule before activation. The
sheet refuses any pilot that requires telemetry or a hosted dependency; collected data remains
explicit and repository-governed.

## Review cadence and rollback

Default review cadence is weekly for the first two weeks, then biweekly until exit. Each review
checks:

- no-prompt acceptance journeys still pass on the harness fixtures;
- hot-path latency and subprocess budgets remain acceptable;
- false-positive routing and noise examples have owners;
- retained-integration intent for the next period;
- whether rollback should fire.

Rollback or disable is safe when any of these fire:

- hot-path latency or subprocess budget breach that operators will not accept;
- false-positive routing above the pilot's pre-agreed threshold;
- an operator requests disable;
- the no-prompt acceptance journeys regress.

Disable paths already documented for adapters and hooks remain the recovery mechanism:

- Claude: documented hook controls, or remove `.claude/settings.json`;
- Copilot: documented hook-disable control, or remove `.github/hooks/murlocs.json`;
- Git hooks: `murlocs hook` uninstall / stop installing repository hooks;
- generated-guidance fallback: absence of `.murlocs/manifest.toml` yields activation absence without
  writes.

The sheet records whether rollback was exercised and which method applied. An exercised rollback
must be recorded as safe.

## Observation sheet

Each repository row carries profile, baseline, metrics, findings, rollback facts, and
`observation_status` of `simulated` or `executed`. Required metrics:

| Metric | Meaning |
| --- | --- |
| `hot_path_latency_ms` | Median and p95 with basis `measured`, `simulated`, or `unavailable`. |
| `false_positive_routing` | Share of sampled routings judged unnecessary or wrong. |
| `missed_findings` | Share of sampled missed guidance problems. |
| `deterministic_repair_rate` | Share of mechanical-drift cases repaired and revalidated. |
| `agent_resolution_rate` | Share of semantic findings resolved by the active agent. |
| `authority_escalation_rate` | Share of sessions that correctly escalated to owners. |
| `retained_integration` | Whether the team kept the integration enabled at the sample point. |
| `operator_feedback` | Bounded response count and safe summary codes only. |

Findings across the pilot must include concrete examples of:

- `useful-silence`
- `useful-intervention`
- `noise`
- `missed-opportunity`

Recommendations use four buckets: `graduate`, `change`, `remain_experimental`, and `remove`.

## Evidence boundary

| Artifact | Role |
| --- | --- |
| `tests/fixtures/passive-loop-pilot/v1/example-sheet.json` | Schema and scoring rehearsal. Not live evidence. |
| `docs/pilots/passive-loop-multi-repo.md` | Published report. Separates executed harness work from planned live execution. |
| Future executed sheet | Only after two live repositories finish the protocol. |

`acceptance.live_execution_complete` may be true only when every counted repository is `executed`.
A `harness-only` pilot must keep that field false. Do not invent measured success for hosts or
repositories that were not observed.

Saga #54 closes only when a live cohort keeps the passive workflow enabled and the no-prompt
acceptance journeys continue to pass. This protocol alone does not close that saga.
