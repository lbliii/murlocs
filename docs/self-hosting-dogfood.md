# Self-hosting dogfood protocol

This protocol is the structured pre-pilot for issue #97. It asks whether Murlocs supplies
novel repository understanding, stays silent during healthy work, creates noise, or gives an
unfamiliar agent a clear next action — using genuine maintenance and product tasks on the
Murlocs repository itself.

`murlocs.dogfood.validate_dogfood_sheet` is the deterministic offline boundary. CI validates the
versioned tranche sheet in `tests/fixtures/self-hosting-dogfood/v1/`. Sheets retain SHA-256
expectation commitments and safe evidence tokens only. Prompt text and transcripts stay private.

## Task design

Run at least five genuine tasks in fresh sessions. Do not mention Murlocs in the task prompt.
Before each session, record privately:

- expected scopes and owners;
- likely gate;
- desired silence or intervention.

Commit that private expectation with `private_expectation_commitment` and store only the digest
in the sheet.

Suggested theme coverage (not every theme is required in one tranche):

| Theme | Intent |
| --- | --- |
| `ordinary-hook-cli` | Behavior-preserving maintenance should stay quiet. |
| `installed-vs-active` | Acceptance behavior tracks the active environment. |
| `dry-run-noop` | Clean-tree dry-run tells the truth and writes nothing. |
| `hook-install-failure` | Unresolved runners fail closed with a recoverable path. |
| `compact-outcomes` | Compact authority packets remain understandable. |
| `authority-semantics` | Owned guidance changes escalate to named owners. |
| `ordinary-observer` | Ordinary docs/code work can complete without product cue. |
| `deterministic-repair` | Mechanical drift remediation stays explicit and safe. |

## Blind and revealed interviews

1. Blind interview happens before the agent inspects the repository again.
2. Revealed interview asks when Murlocs was discovered, what changed, what was novel, what was
   noisy, and whether the agent would keep it enabled.
3. Cross-check the agent account against hook, CLI, CI, latency, rerun, repair, escalation, and
   bypass evidence without storing transcripts.

## Classification and causes

Each task is classified as `useful-silence`, `useful-intervention`, `noise`, `miss`, or `mixed`.
Causes use one family: generated-guidance, hook, engine, adapter, agent-judgment, packaging, or
repository-process.

## Exit criteria

A complete tranche must:

- retain healthy hooks unless a documented rollback condition fires;
- leave no unresolved false blocker;
- observe at least one deterministic intervention and one authority-required journey;
- recommend whether to proceed to the formal multi-repository pilot (#68);
- prefer actionability, concision, reliability, and measurement fixes over new policy primitives.

## Evidence boundary

| Artifact | Role |
| --- | --- |
| `tests/fixtures/self-hosting-dogfood/v1/tranche-*.json` | Privacy-preserving countable sheet |
| `docs/pilots/self-hosting-dogfood-*.md` | Published narrative without prompts |
| Private operator notes | Expectation text and transcripts (never checked in) |
