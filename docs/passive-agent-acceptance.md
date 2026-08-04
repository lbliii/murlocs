# No-prompt passive-agent acceptance harness

This harness is the empirical complement to adapter conformance. It evaluates a genuinely fresh
agent working from the repository it receives, rather than a fixture adapter or an agent given
Murlocs in its task request. The task request is retained only by the evaluator. Before the
session starts, the evaluator records a SHA-256 commitment of a private expected outcome; the
checked-in evidence contains neither the request nor a transcript, tool arguments, command text,
or user content.

`murlocs.passive_acceptance.validate_observations` is the deterministic replay boundary. CI
validates its strict versioned example document in
`tests/fixtures/passive-agent-acceptance/v1/example-observations.json`; each actual pilot adds an
equally strict, attributable evidence file. It reproduces the claimed contract, including journey
coverage and classification, but cannot reproduce model judgment or assert a host capability that
was not observed.

The first attributable fresh-session pilot is
`tests/fixtures/passive-agent-acceptance/v1/pilot-2026-08-03.json`. Its five private expectation
commitments were created before dispatch and its records were revealed only after each session
finished. It records the generated-guidance fallback honestly: the host supplied repository
guidance but no native lifecycle timing, so only provision-to-reveal wall-clock latency is present.

## Run protocol

For each journey, create a new agent session in an isolated disposable repository. Do not name
Murlocs in the task request. Capture the expectation commitment before starting the session, then
record only lifecycle event and operation names, typed outcome code/resolution/silence, elapsed
milliseconds, remediation facts, and the compact escalation facts. The agent must discover the
checked-in guidance without evaluator-provided product context.

`latency_ms.wall_clock` measures from disposable-worktree provisioning through blind reveal. It
captures comparable end-to-end latency even when the host does not expose per-operation timing;
those unavailable samples are represented as `null`, never as fabricated zero-duration calls.

| Journey | Required observation |
| --- | --- |
| Ordinary code-only work | Fresh discovery, healthy silent result, and no Murlocs-related user interruption. |
| Generated drift | Finding, deterministic repair, listed changed paths, and fresh successful revalidation. |
| Semantic local guidance | Evidence-backed proposal facts, no silent policy mutation, and the affected local scope. |
| Cross-scope global guidance | Evidence-backed proposal facts, no silent policy mutation, and the root/global scope. |
| Authority-required exception | Work remains blocked, exactly one compact decision packet, and the routed owner. |

The record can represent a failed journey as `engine`, `adapter`, `agent_judgment`, or
`host_capability`. That attribution is intentionally exclusive: a host which does not expose a
native lifecycle boundary is evidence of a host-capability limit, not an adapter or agent failure.
Do not turn a failed observation into a passing fixture.

## Evidence boundary

The checked-in example establishes the contract but is not an acceptance claim. A recorded pilot is
acceptance evidence, not a claim that every host is enforcing. In a host without repository-local
lifecycle hooks, `generated-guidance` is correctly recorded as the guidance fallback; this is not
a simulated adapter. A production host with native events must run the same fresh-agent journeys
before its stronger lifecycle claims are made. The existing adapter conformance suite remains the
CI test for adapter behavior, while this harness guards the real fresh-agent observation contract
and its privacy boundary.
