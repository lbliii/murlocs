# Outcome envelope v1

`murlocs check` and `murlocs impact` include an additive `outcome` object in structured output.
The envelope gives terminal callers, typed programmatic callers, MCP hosts, hooks, and CI the same
stable classification without changing the commands' existing fields or exit codes. Plain output
uses the same deterministic `summary`; a passing result remains quiet-capable.

The version identifier is `{"contract":"io.murlocs.outcome","schema_version":1}`. Consumers must
reject an unsupported contract or schema version and duplicate JSON members. Unknown fields outside
the closed action objects are ignored for forward compatibility. Checked-in passing,
deterministic-repair, agent-action, authority-required, and malformed examples live under
`tests/fixtures/outcome-envelope/v1/`.

## Classification

The envelope contains a stable aggregate `code`, `status`, `severity`, `blocking`, and
`resolution_class`, plus ordered `findings` and `next_actions`. Every finding carries evidence,
source operation and codes, source paths, affected scopes/maps/owners, and exactly one action id
matching its own resolution:

| Resolution | Typed action | Authority | Meaning |
| --- | --- | --- | --- |
| `pass` | none | none | No finding. |
| `deterministic_repair` | `compile_managed_guidance` | integration | All check findings are safely preflighted managed-output drift. The envelope does not execute the repair. |
| `agent_action` | `inspect_findings` | agent | Inspect the named evidence and affected guidance. |
| `authority_required` | `request_authority` | human | Route the exact scopes, maps, and owners; this is not authentication or approval. |

Actions are a closed allowlist of operation, object-valued arguments, effect, and authority. They
never contain command, argv, or shell strings and never copy registered-check invocation text.
Version 1 `check` and `impact` envelopes are read-only, so `change.repository_state_changed` is
always false and `change.paths` is empty. A deterministic repair may only be performed by a
separate integration with explicit authority; the integration must then obtain fresh lifecycle
evidence.

`check` findings retain their existing exit code 1 and are blocking. `impact` remains a routing
report with exit code 0 for valid input. Both `recommended` and `required` impact findings are
advisory in the envelope: `required` selects the authority route, but repository or merge policy—not
Murlocs—decides whether that route blocks work.

## Compact agent rendering and phase decisions

`render_compact_outcome` derives a small active-agent presentation directly from the parsed
envelope; it never reinterprets prose or exposes a command. Passing outcomes render as an empty
string, so a host can remain silent. Non-passing output names status/effect, affected scopes,
required owners, the lifecycle gate when authority is required, and one safe next action. It omits
repeated finding evidence, invariant text, checks, layers, and provenance. Generated fallback
guidance recommends compact output for active agents; integrations should retain structured JSON.

The additive `decision` member keeps outcome schema version 1 compatible. It deliberately records
`task_authorization: not_attested` and `agent_acknowledgement: not_recorded` by default: Murlocs
neither authenticates an actor nor accepts a local acknowledgement or chat claim as approval. A
trusted adapter may attest task authorization separately from owner review, but Murlocs never
grants it. An authority-required outcome says `implementation: may_continue`, defaults to a
`merge` gate, and
lists the exact required owners derived from its request-authority action. A trusted adapter may
select the applicable `commit`, `push`, `merge`, or `completion` gate while reconciling evidence;
agent-callable surfaces cannot set it.

An adapter can call `reconcile_external_authority_evidence` after it has independently validated a
repository or review-provider integration. The evidence must match the bound adapter id/version/
session, the current opaque state token, and all required owners. The resulting evidence names its
adapter provenance, review id, reviewed state, and owners. Missing, changed, stale, or mismatched
evidence always returns the decision to `unresolved`. This function is not exposed through the
terminal, programmatic, or MCP agent surfaces, and parsing a JSON claim alone never authenticates
it. Parsing an `externally_satisfied` decision nevertheless rejects evidence unless its integration
token source, adapter scope, current state id, and required owners agree with the envelope. Compact
output says exactly whose review gates the boundary; when valid external evidence satisfies it, the
output says the boundary may proceed only while that evidence remains valid.

The independent [compact outcome blind/reveal](pilots/compact-outcome-blind-reveal.md) confirmed the
continue/gate/owner interpretation and supplied the wording findings locked by the golden fixture.

## Correlation and trusted tokens

Callers may pass `--correlation-id ID` (or the same typed programmatic/MCP input) to `check` and
`impact`. Murlocs validates and echoes it but never generates one. The agent-facing commands do not
accept state or dependency tokens.

An adapter may bind an already-equal correlation id and its own opaque `state_id`, plus an optional
impact-local `dependency_id`, exactly once with `bind_integration_tokens`. Binding also requires the
closed integration-only `token_scope` object: adapter id, adapter version, and session id. Unbound
CLI and MCP output uses `token_scope: null`; neither surface accepts it as input. These tokens are
trusted integration receipts scoped to that adapter/session; they are not portable repository
hashes and must never be accepted from an active agent. `merge_outcomes` requires a common
correlation, state, token source, token scope, and Murlocs version, permits the impact side to
contribute the one dependency token, rejects conflicting findings, and deterministically unions
arguments for identical allowlisted actions.

For every action, its codes, scopes, maps, and owners must exactly equal the deterministic union of
the findings that reference it. Extra or missing routing values are invalid, so a consumer never
trusts an action that redirects a finding to unrelated owners or maps.

The activation lifecycle remains the authority for invocation, freshness, receipts, and repository
blocking. Its outer `outcome` member may carry this object, but the sidecar cannot override lifecycle
receipt validation, check-derived repository blocking, cache decisions, silence, or writes.
For a multi-operation event, a merged envelope uses `source.operation: aggregate`; its finding
provenance must be a subset of the event's exact validated receipt operations, its exit status must
match those receipts, and its impact dependency must match the trusted adapter context.

## Compatibility and integrations

Existing top-level structured fields and success/failure exit codes are unchanged; `outcome` is
additive. A handled failure retains `{"ok":false,"error":...}` and adds a blocking failure envelope.
Terminal, programmatic, MCP, and discovery schemas all come from the same typed command registry.

Hooks and CI should persist the structured output and apply their own policy to it. For example:

```bash
murlocs check --correlation-id "$RUN_ID" --format json >murlocs-check.json
murlocs impact --correlation-id "$RUN_ID" --revision-range HEAD --format json \
  >murlocs-impact.json
```

Treat both files as untrusted until an integration validates the versioned envelopes and binds its
own fresh state evidence. Do not infer trusted tokens from their contents, and do not execute an
action by translating arbitrary envelope data into a command.

## Executing an authorized repair

`compile_managed_guidance` is an allowlisted integration action, not an instruction to run arbitrary
shell text. An integration that has explicit write authority may invoke `murlocs repair`; the
command accepts only a preflight-safe set of `drift` and `lock` findings, names every changed map
and lockfile path, and reports whether Git callers must re-stage and re-run their gate. Its
`--dry-run` update list, including before/after SHA-256 values, is identical to the subsequent
stable apply. Semantic, unmanaged, or modified-output findings keep their read-only agent or
authority envelope and are never repaired automatically.

Repair occurs outside the lifecycle operation itself. An integration must obtain a new post-edit
assessment and fresh cache-forbidden pre-completion receipts after repair. Interrupted multi-file
repairs retain a checked journal and are handled with `murlocs repair --recover`; recovery only
finalizes an already-complete plan or restores exact pre-repair bytes.
