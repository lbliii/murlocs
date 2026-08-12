# Multi-repository passive-loop pilot report

Issue: #68  
Protocol: [Multi-repository passive-loop pilot protocol](../passive-loop-pilot.md)  
Sheet contract: `io.murlocs.passive-loop-pilot` / schema 1  
Checked-in sheet: `tests/fixtures/passive-loop-pilot/v1/example-sheet.json`

## Status

**Harness and protocol published. Live multi-repository longitudinal execution is not complete.**

This report deliberately separates what is checked in and verified offline from what still requires
real host time in at least two materially different repositories. It does not claim that teams have
kept the passive loop enabled for a multi-week window, and it does not close saga #54.

| Workstream | State | Evidence |
| --- | --- | --- |
| Pilot protocol, baselines, rollback, cadence, data handling | Executed in-repo | `docs/passive-loop-pilot.md` |
| Scoring / observation sheet schema | Executed in-repo | `src/murlocs/passive_loop_pilot.py` |
| Offline two-repository rehearsal sheet | Executed as `simulated` | `tests/fixtures/passive-loop-pilot/v1/example-sheet.json` |
| Structural CI coverage (`@pytest.mark.issue(68)`) | Executed in-repo | `tests/test_passive_loop_pilot.py` |
| Live multi-week observations in ≥2 repos | Planned follow-up | No executed sheet yet |
| Operator feedback panel and retained-integration decision | Planned follow-up | Sheet fields present; live responses absent |
| Saga #54 exit (integration retained + journeys green) | Blocked on live cohort | Acceptance journeys remain separately proven by #67 |

## Repository profiles (selected for the live cohort)

The rehearsal sheet and the planned live cohort use two profiles that differ on every required axis:

| Axis | Fixture / planned repo A | Fixture / planned repo B |
| --- | --- | --- |
| Size | `small` | `large` |
| Scope topology | `layered` | `multi-domain` |
| Guidance maturity | `mature` | `bootstrap` |
| Primary agent workflow | `cli-hooks` | `generated-guidance` |

These profiles are compatible with the existing Chirp/Kida-scale mature networks and the broader
synthetic or early-adoption networks already used elsewhere in the pilots directory. The live run
must still bind concrete repositories, record pre-activation baselines, and keep private names out
of the public sheet.

## Baselines and metrics (sheet shape)

Every repository row must capture the baseline fields and report metrics required by issue #68:

Baselines: guidance drift, instruction size, agent remediation, routing accuracy, human
intervention.

Reported metrics: hot-path latency, false-positive routing, missed findings, deterministic repair
rate, agent-resolution rate, authority-escalation rate, retained integration, operator feedback.

The checked-in example fills those fields with **simulated** values so CI can replay validation. It
marks latency as `simulated` or `unavailable` and sets `operator_feedback.responses` to `0`. Those
numbers are harness placeholders, not measured pilot outcomes.

## Findings taxonomy (rehearsal examples)

The rehearsal sheet includes one concrete example of each required finding kind. These are
illustrative labels derived from known harness and host-capability limits, not live operator diary
entries:

| Kind | Example id | Note |
| --- | --- | --- |
| Useful silence | `ordinary-code-silent-pass` | Healthy code-only path stays silent. |
| Useful intervention | `generated-drift-repair` | Deterministic repair clears drift. |
| Noise | `over-broad-impact-routing` | Ambiguous path sets can over-route owners. |
| Missed opportunity | `host-timing-unavailable` | Fallback hosts may lack native latency samples. |

A live report must replace these with attributable executed examples from the cohort repositories.

## Disable and rollback

No telemetry or hosted dependency is required. Sheet validation rejects `telemetry_required: true`
and requires `data_handling: repository-governed`.

Safe disable/rollback paths are documented for Claude, Copilot, Git hooks, and generated-guidance
fallback in the protocol and adapter docs. The rehearsal sheet records exercised safe rollback via
`hooks-uninstall` and `manifest-absent` so the contract forces that field to be explicit. Live
pilots must exercise the disable path that matches each host before exit review.

## Provisional recommendations

Until live execution completes, recommendations remain provisional and biased toward keeping the
harness durable while withholding graduation of longitudinal claims:

| Bucket | Provisional items |
| --- | --- |
| Graduate | `no-prompt-acceptance-harness` (already evidenced by #67 / fresh-session pilot) |
| Change | `false-positive-routing-thresholds` (needs live noise samples) |
| Remain experimental | `longitudinal-operator-feedback` |
| Remove | _(none yet)_ |

Do not treat this table as a product graduation decision. A completed live sheet may move items
after measured retained-integration and journey evidence exists.

## Remaining live work

1. Activate the protocol in two selected repositories that match the diversity axes.
2. Record pre-activation baselines and weekly/biweekly reviews in repository-governed notes.
3. Collect executed observation rows, including operator feedback summary codes.
4. Re-run no-prompt acceptance journeys and confirm retained integration.
5. Publish an executed sheet beside this report and update the recommendation buckets.
6. Only then consider saga #54 exit criteria.

Until those steps finish, issue #68 remains only partially satisfied by checked-in protocol,
harness, fixtures, and this honest report.
