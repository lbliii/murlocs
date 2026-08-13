# Multi-repository passive-loop pilot report

Issue: #68  
Protocol: [Multi-repository passive-loop pilot protocol](../passive-loop-pilot.md)  
Sheet contract: `io.murlocs.passive-loop-pilot` / schema 1  
Harness rehearsal sheet: `tests/fixtures/passive-loop-pilot/v1/example-sheet.json`  
Live cohort sheet (in progress): `tests/fixtures/passive-loop-pilot/v1/live-cohort-2026-08-12.json`

## Status

**Live cohort started (first review). Multi-week longitudinal execution is not complete.**

This report deliberately separates harness rehearsal, first-review executed rows, and the remaining
multi-week window. It does not claim that teams have kept the passive loop enabled for a finished
multi-week exit, and it does not close saga #54.

| Workstream | State | Evidence |
| --- | --- | --- |
| Pilot protocol, baselines, rollback, cadence, data handling | Executed in-repo | `docs/passive-loop-pilot.md` |
| Scoring / observation sheet schema | Executed in-repo | `src/murlocs/passive_loop_pilot.py` |
| Offline two-repository rehearsal sheet | Executed as `simulated` | `example-sheet.json` |
| Structural CI coverage (`@pytest.mark.issue(68)`) | Executed in-repo | `tests/test_passive_loop_pilot.py` |
| First-review live cohort (≥2 diverse repos) | Executed rows; window open | `live-cohort-2026-08-12.json` |
| Multi-week retained-integration exit | Planned follow-up | `live_execution_complete: false` |
| Saga #54 exit (integration retained + journeys green) | Blocked on finished live window | Acceptance journeys still pass via #67 fixtures |

## Repository profiles (live cohort)

| Axis | `cohort-murlocs-self` | `cohort-furatena-legacy` |
| --- | --- | --- |
| Size | `small` | `large` |
| Scope topology | `layered` | `multi-domain` |
| Guidance maturity | `mature` | `migrating` |
| Primary agent workflow | `cli-hooks` | `generated-guidance` |
| Observation status | `executed` | `executed` |

Public labels only. Private repository remotes, prompts, and transcripts stay out of the sheet.
Furatena was observed read-only via inventory in an isolated clone; its passive loop is not yet
enabled, which is recorded as a missed opportunity rather than a retained-integration success.

## First-review baselines and metrics

Murlocs self-host baselines (2026-08-12):

- guidance drift findings: `0`
- compiled `AGENTS.md` instruction bytes: `7607`
- measured hot-path warm samples (excluding cold outliers): median `25ms`, p95 `285ms`
- no-prompt acceptance fixture suite: still green
- retained integration: `true` for the self-host surface

Furatena first-review baselines:

- inventory instruction bytes across generated maps: `51553`
- native hot-path latency: `unavailable` (no Murlocs passive loop enabled yet)
- retained integration: `false` (inventory-only; enablement remains experimental)

False-positive routing on the self-host row is estimated from dogfood impact samples where some
code-path impacts escalated above a silence expectation (`0.15`). That estimate is a first-review
signal, not a final graduation number.

## Findings taxonomy (first review)

| Kind | Example id | Note |
| --- | --- | --- |
| Useful silence | `clean-check-and-dry-run` | Clean check and dry-run compile stay silent. |
| Useful intervention | `authority-on-owned-layer` | Owned-layer impact routes to `@lbliii`. |
| Noise | `code-path-impact-escalation` | Some code-path impacts escalate above silence expectation. |
| Missed opportunity | `passive-loop-not-enabled` | Migrating legacy network not yet on the passive loop. |

## Disable and rollback

No telemetry or hosted dependency is required. Sheet validation rejects `telemetry_required: true`
and requires `data_handling: repository-governed`.

First-review rollback drills:

- Murlocs disposable hook uninstall path recorded as safe (`hooks-uninstall`).
- Furatena remained `manifest-absent` for Murlocs (safe non-install / no writes).

## Recommendations (provisional after first review)

| Bucket | Items |
| --- | --- |
| Graduate | `no-prompt-acceptance-harness`, `self-hosting-dogfood-gate` |
| Change | `false-positive-routing-thresholds`, `code-path-impact-silence-expectations` |
| Remain experimental | `longitudinal-operator-feedback`, `furatena-passive-loop-enablement` |
| Remove | _(none)_ |

## Remaining live work

1. Enable the passive loop on the migrating second repository (or replace it with another enabled
   host) and keep weekly reviews for the multi-week window.
2. Collect additional operator feedback summary codes without storing transcripts.
3. Re-run no-prompt acceptance journeys at each review; keep `journeys_still_pass` honest.
4. Only set `live_execution_complete: true` / pilot `complete` after the window and retained
   integration criteria are met.
5. Only then consider saga #54 exit criteria.

Until that window finishes, issue #68 remains open: harness, first-review executed sheet, and this
report are published, but the longitudinal claim is not complete.
