# Fresh-session passive-agent acceptance pilot — 2026-08-03

The five countable observations are the privacy-preserving records in
`tests/fixtures/passive-agent-acceptance/v1/pilot-2026-08-03.json`. Each was run in a newly
provisioned disposable worktree by a new agent session. The evaluator committed the private
expected result before dispatch, withheld worktree inspection until completion, and stored only
the resulting SHA-256 commitment. No task request, transcript, command argument, or user content
is retained.

| Journey | Result | Acceptance evidence |
| --- | --- | --- |
| Ordinary code-only | Pass | Guidance was discovered without a product cue; the agent completed a focused code change, and both fresh check and impact results were silent. |
| Generated drift | Pass | The agent used dry-run repair, repair, fresh check, and impact; it preserved the source update and repaired only the generated map and lockfile. |
| Semantic local guidance | Pass | The agent made no policy mutation and returned a proposal supported by local invariant, implementation, and regression-test evidence. |
| Cross-scope global guidance | Pass | The agent made no policy mutation and concluded that the existing source, documentation, and tests were stronger evidence than a duplicate global rule; it identified the bounded-field regression gap. |
| Authority-required exception | Pass | The agent made no mutation or approval assertion, and returned one compact packet routing the boundary move to `@lbliii`. |

All countable observations are `pass`; no engine, adapter, agent-judgment, or host-capability
failure was observed. The fallback host did not expose native lifecycle events or per-operation
timing, so the record captures provision-to-reveal wall-clock duration and marks individual
operation samples unavailable. This is a capability limitation, not evidence that a native adapter
enforced those boundaries.

An earlier supporting code-and-test session also discovered the guidance and did not interrupt the
user, but its changed owned paths yielded a non-silent internal review-routing impact outcome. Its
pre-session commitment is `8d841e0a99f477105218a074896a9d371c0ac86521356a8fe3669239ffcc8bb3`.
It is neither a failure nor a replacement for the countable ordinary journey: the later unaffected
code-only journey provides the stronger fully silent check-and-impact evidence.

## Excluded setup observation

One earlier drift setup directly edited a managed generated map. The fresh agent restored that map,
then ran check and impact. That is the safe behavior for modified managed output, but it is not the
generated source/output drift the acceptance journey requires. Its pre-session expectation
commitment is `2b7e99b02faa209a7c905963460fb037d5e1d58175a38893b76e8c118eb4921d`.

It is recorded as a harness-setup observation, not a failure attributed to the engine, adapter,
agent judgment, or host, and it is excluded from the five-session result. The corrected source
update plus stale generated output observation is the countable drift journey.
