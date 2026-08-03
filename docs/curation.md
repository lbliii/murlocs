# Governed guidance curation

Status: implemented repository-local curation lifecycle.

Murlocs needs to help maintainers resist stale, repetitive, and append-only guidance without
turning the compiler into a self-editing agent. This document defines a repository-local proposal
and decision lifecycle. It is deliberately separate from active manifest composition:

```text
agent or human observation
        |
        v
.murlocs/curation/<proposal-id>.toml  (inert review record)
        |
        +--> deterministic review report --> owner decision
                                           |
                                           v
                              transactional promotion or pruning
                                           |
                                           v
                      manifest/layer source --> compile --> AGENTS.md
```

Only the last line is an input to compilation. A curation record is never an implicit manifest
fragment, layer, overlay, or generated-map input.

## Decision summary

- Store one versioned TOML record per proposal under `.murlocs/curation/`.
- Keep the proposed payload and append-only lifecycle events in that inert record.
- Require an explicit, owner-attributed acceptance before an apply operation is eligible.
- Apply additions, replacements, and removals transactionally to an existing active source.
- Derive required owners from the target layer and optional CODEOWNERS policy; proposers do not
  choose who is authorized to accept their own proposal.
- Use deterministic code for parsing, targeting, duplication, composition, shadowing, byte-budget,
  stale-base, and ownership checks. Semantic critique may be offered by an optional agent skill,
  but it is advisory evidence only.
- Retain rejected, withdrawn, promoted, superseded, and pruned records outside runtime context.
- Feed proposal and guidance revisions into the separate recorded-run evaluation harness; never
  invoke a model from curation, compilation, or checking.

## Storage and schema

The initial format is `curation_schema_version = 1`. Existing repositories require no manifest
migration and do not need a `[curation]` section. The compiler continues to load only the root
manifest and its explicitly registered layers, so even an older Murlocs version cannot accidentally
activate a proposal.

An illustrative record is:

```toml
curation_schema_version = 1
id = "core-use-path-resolver"
intent = "replace" # add, replace, or remove
subject_kind = "operating_rule"
target_source = ".murlocs/layers/core.toml"
target_scope = "core"
target_key = "sha256:5c8..." # absent for an unkeyed addition
base_source_sha256 = "0bd..."
origin = "issue-314"
rationale = "The existing rule does not name the repository confinement helper."
proposer = "@contributor"
required_owners = ["@core-maintainers"]
required_scopes = ["core"]

[[evidence]]
kind = "file_anchor" # file_anchor, command, issue, pull_request, evaluation, or note
reference = "src/murlocs/paths.py#resolve_repo_path"
summary = "The shared resolver enforces the repository boundary."

[payload]
value = "Resolve every manifest-controlled path with resolve_repo_path."

[[events]]
state = "proposed"
actor = "@contributor"
at = "2026-08-03T14:00:00Z"
rationale = "Captured after a path-safety review."

[[events]]
state = "accepted"
actor = "@core-maintainer"
at = "2026-08-04T15:00:00Z"
rationale = "The replacement is narrower and retains the safety requirement."
review_ref = "https://github.example/pull/42"
```

Timestamps and actor names are audit metadata supplied by the caller; they are not used to make
compilation deterministic. Git review, branch protection, and CODEOWNERS remain the authority for
who actually approved a change. The local CLI can validate that an acceptance names a required
owner, but it cannot authenticate that identity and must not pretend otherwise.

### Required proposal metadata

Every proposal records:

- a repository-unique, path-safe `id` and schema version;
- `intent`: `add`, `replace`, or `remove`;
- the target active source, subject kind, optional scope, and stable target key;
- the target source hash observed when the proposal was created;
- origin, rationale, proposer, and required-owner and affected-scope snapshots;
- at least one evidence item, including a kind, reference, and human-readable summary;
- a typed payload for additions and replacements; removals identify the existing target and omit
  the payload; and
- an ordered event history beginning with `proposed`.

Subject kinds map to canonical manifest concepts: list guidance, scope, invariant, check, judgment,
and coverage exemption. Structured subjects use their existing canonical id or path as the target
key. Because list guidance has no authored id today, its key is a digest of the normalized existing
value. A replacement or removal must identify exactly one current subject; ambiguous targeting is
blocking rather than resolved heuristically.

`target_scope` addresses the subject and makes the caller's intended context explicit. It does not
confine the rendered effect. In particular, `pillar`, `search_policy`, `operating_rule`,
`stop_and_ask`, and `done_criterion` modify root-map collections, even when their active source is a
domain layer. Since every scoped guidance chain includes the root map, those changes affect every
chain. Checks are also root-map guidance. Judgments are scope-local and affect only the named scope
map (plus descendant chains that consume it). Scope and invariant changes may alter both a scoped
map and the root network summary, so review uses the prospective rendered outputs rather than the
source layer's declared domain.

`required_owners` and `required_scopes` are snapshots for audit and review routing. For a valid
proposal, Murlocs renders
the prospective network, identifies every changed active guidance chain, and unions the owners of
the authored sources represented by those chains. Exact CODEOWNERS entries participate when that
policy is enabled. A truly local judgment therefore stays focused, while a domain-layer global list
addition routes every affected owner. Eligibility and the affected-chain calculation are
recalculated before acceptance and promotion, so an old proposal cannot bypass later source,
output, topology, or CODEOWNERS changes. Actor strings remain unauthenticated audit attribution;
the required-owner list is routing evidence, not proof that every named identity approved.

Active proposals recompute both snapshots before acceptance and apply. Terminal review treats the
recorded scope ids as effect evidence, then resolves their current consumers: a recorded root
effect reaches every current scope, while a local effect reaches the addressed scope and its
current descendants. Global list and check subjects always retain the all-chain safety floor.
Scope and invariant additions/removals also cannot be narrowed by deleting `root` from their
snapshot; structurally impossible routing evidence is reported and review fails closed. When
recorded owners that are still active would be omitted by an edited scope snapshot, terminal review
also reports the mismatch and uses all current chains.

Version-1 records created before `required_scopes` was introduced remain readable. An omitted field
is treated as legacy evidence: list, check, scope, and invariant records route conservatively to all
current scopes, while locally addressed subjects use their current scope chain. Terminal owner
output is informational because no terminal record is eligible for another write. As with actor
strings and the rest of the TOML record, snapshots are reviewable repository metadata rather than
authenticated identities or cryptographically sealed history; Git review and CODEOWNERS remain the
approval boundary.

## Lifecycle and operations

The current state is derived from the ordered events; it is not an independently editable field.
Valid transitions are:

```text
proposed --> accepted --> promoted --> superseded
    |            |
    |            +--> promoted (add or replace)
    |            +--> pruned (remove)
    |            +--> rejected (acceptance revoked before apply)
    +--> rejected
    +--> withdrawn
```

Terminal records remain in `.murlocs/curation/`. Moving old terminal records to a repository-local
archive may be added later, but deleting them is never part of promotion or pruning.

The command vocabulary is intentionally explicit:

- `murlocs curate propose` creates an inert record and never edits an active source.
- `murlocs curate review ID` is read-only and prints the current target, proposed result, evidence,
  required owners, deterministic findings, and active-context byte delta.
- `murlocs curate accept ID` or `reject ID` appends an attributed decision event. Acceptance does
  not edit guidance or generated maps.
- `murlocs curate withdraw ID` lets a proposer close an unpromoted proposal explicitly.
- `murlocs curate promote ID` applies an accepted add or replace to its target active source and
  appends the promotion event in one transaction.
- `murlocs curate prune ID` applies an accepted remove proposal and appends a pruning event in one
  transaction.
- `murlocs curate supersede OLD --with NEW` links a promoted record to the accepted replacement
  that supersedes it; it applies the replacement and records both sides in one transaction.
- `murlocs curate recover ID` explicitly previews or rolls back an interrupted one-record apply;
  use `--with NEW` for the two records in an interrupted supersession.

All mutating commands are CLI-only. `review` and `check` remain available on read-only agent
surfaces; accepting or applying a proposal therefore always requires an explicit local CLI action.
Hand-editing an event does not authenticate it: Murlocs validates lifecycle shape and current-owner
attribution, while repository review and CODEOWNERS provide actual identity and approval controls.

### Creating and reviewing a proposal

Proposal creation is CLI-only and requires caller-supplied attribution, time, and one evidence
item. It snapshots the active source hash and current required owners. A dry-run renders the exact
record and complete prospective report without creating `.murlocs/curation/`:

```bash
murlocs --dry-run curate propose core-path-rule \
  --intent add \
  --subject-kind operating_rule \
  --target-source .murlocs/layers/core.toml \
  --target-scope core \
  --origin issue-314 \
  --rationale "Make the repository confinement helper explicit." \
  --proposer @contributor \
  --evidence-kind file_anchor \
  --evidence-reference src/murlocs/paths.py#repo_path \
  --evidence-summary "The shared helper rejects repository path escapes." \
  --at 2026-08-03T14:00:00Z \
  --value "Resolve manifest-controlled paths with repo_path."

murlocs curate review core-path-rule
murlocs --dry-run curate accept core-path-rule \
  --actor @core-maintainer \
  --at 2026-08-04T15:00:00Z \
  --rationale "Reviewed against current source and ownership." \
  --review-ref pull-request-42
murlocs curate accept core-path-rule \
  --actor @core-maintainer \
  --at 2026-08-04T15:00:00Z \
  --rationale "Reviewed against current source and ownership." \
  --review-ref pull-request-42
murlocs --dry-run curate promote core-path-rule \
  --actor @core-maintainer \
  --at 2026-08-04T16:00:00Z \
  --rationale "Apply the accepted source change." \
  --review-ref pull-request-42
murlocs curate promote core-path-rule \
  --actor @core-maintainer \
  --at 2026-08-04T16:00:00Z \
  --rationale "Apply the accepted source change." \
  --review-ref pull-request-42
murlocs compile
murlocs curate check
```

`review` and `check` are read-only on terminal, programmatic, MCP, and discovery surfaces. Use
`--format json` for stable structured output. Human and structured reports carry the same proposal,
owner, affected-scope, decision, evidence, before/after, hash, conflict, affected-chain,
byte-budget, and validation facts.

### Version-1 subject and payload shapes

All records reject unknown fields. `add` and `replace` require `[payload]`; `remove` omits it.
Unkeyed list additions omit `target_key`; list replacements and removals use
`sha256:<64 lowercase hex characters>` for the normalized current value.

| Subject kind | Stable target and payload |
| --- | --- |
| `pillar`, `search_policy`, `operating_rule`, `stop_and_ask`, `done_criterion` | `[payload] value = "..."`; replacement/removal keys are content digests. |
| `scope` | `target_key` is the scope id; payload uses the canonical scope fields. Replacement must preserve the current `path` and `map`, which are immutable scope identity. |
| `invariant` | `target_key` is the invariant id; payload uses the canonical invariant fields. |
| `check` | `target_key` is the check name; payload contains `invoke`, `location`, and optional proof metadata. |
| `judgment` | `target_key` is `SCOPE.advocate`, `SCOPE.do_not`, or `SCOPE.serves`; payload contains `values`. |
| `coverage_exemption` | `target_key` is the repository-relative path; payload contains `reason`. |

Structured payloads can be supplied to `propose` with `--payload-json`. Repository-confined target
selection is exact: `target_source` must name the root manifest or a currently registered layer.
Proposal ids must be lowercase path-safe ids and the CLI refuses to replace an existing record.

All writes remain CLI-only. `review` may be exposed read-only through Milo's programmatic, MCP, and
discovery surfaces. Every write supports dry-run before apply.

### Transaction and conflict rules

Promotion, supersession, and pruning preflight the entire prospective canonical model in memory.
They refuse to write if:

- the record is not in the required state or its event sequence is invalid;
- the target source is not an explicitly active manifest or layer source;
- the current source hash differs from `base_source_sha256`;
- the target subject is missing, ambiguous, or no longer matches its recorded digest;
- current required owners differ from, or are not satisfied by, the acceptance record;
- the prospective model fails schema, graph, evidence, ownership, coverage, composition, or budget
  validation;
- any generated output is unmanaged or modified; or
- the operation would escape the repository boundary.

On success, the CLI replaces the active source and lifecycle record as one recoverable transaction.
It stages exact before and after images in `.murlocs/curation/.transaction`, then rechecks the
source, records, generated maps, lock, proof files, every supported CODEOWNERS location, and coverage
topology immediately before the first replacement. An ordinary in-process failure rolls back only
from the trusted in-memory plan that created the transaction.

Compilation remains a separate explicit operation. This keeps promotion review focused on
the source-of-truth change and retains existing generated-file preflight behavior. A future
`--compile` convenience may compose the two transactions only if it provides equivalent rollback
and ownership guarantees.

Dry-run builds the same plan and unified patches without creating or recovering a transaction. A
journal left by a process crash is untrusted repository input: no later proposal, decision, apply,
check, or compile operation uses its images to write files automatically. Both `murlocs check` and
`murlocs curate check` report it without changing it, and compilation is blocked so a temporarily
mixed source-and-record state cannot become generated guidance.

Recovery authority comes only from an explicit `murlocs curate recover ID` invocation. Recovery
requires the journal to name exactly one currently active source plus the selected proposal record,
or exactly two selected records for supersession. It rejects duplicate targets, path escapes,
symlinks, unknown schema fields or versions, unexpected bytes, and every other target shape.
Journal before/after images are never used as writable recovery content, even when their hashes and
target paths are internally consistent. Cleanup without source writes is allowed only when the
current record lifecycle and source digest independently prove the transaction fully applied or
unapplied. A partially applied addition can be rolled back only by deterministically removing that
accepted record's exact subject from the current source and proving the reconstruction equals
`base_source_sha256`. Partial replacements, removals, and supersessions that cannot be reconstructed
from current lifecycle semantics fail closed for manual remediation.

`murlocs --dry-run curate recover ID` shows any semantically derived rollback patch first. The apply
form rechecks the journal identity and current source bytes before writing; a fully applied or
unapplied transaction only has its journal removed. Repository content alone can therefore block
for inspection but cannot authorize an attacker-supplied before image. Curation never invokes
registered checks, a model, or `compile` implicitly.

## Deterministic review report

Human and structured review output must agree and include:

- proposal id, state, intent, subject kind, target source, scope, and target key;
- proposer, current and recorded required owners and scopes, recorded decisions, origin,
  rationale, and evidence;
- a before/after representation and whether the operation is an addition, replacement, or removal;
- the current and proposed source hashes and any stale-base conflict;
- exact duplicate and key-collision findings across the effective model;
- composition findings, including values that would be shadowed or become newly active under the
  existing layer merge rules;
- every affected root-to-target guidance chain;
- current and prospective active bytes for each affected chain, the signed byte delta, and the
  configured maximum; and
- all ordinary validation findings that the prospective model would produce.

Exact duplication means equality after the same normalization already used by manifest
composition. A deterministic shadow finding means that merge order or an explicit override makes a
value inactive; it is not a claim that two differently worded statements mean the same thing.
Potential paraphrase, contradiction, verbosity, or architectural staleness detection requires
judgment and therefore remains outside the core.

## Trust boundary

Agents and humans may:

- discover facts, draft proposals, suggest replacements, and attach evidence;
- identify possible paraphrases, contradictions, or stale claims;
- recommend a decision; and
- run explicit repository-authorized checks to gather evidence.

The deterministic core may:

- parse and validate records and event transitions;
- derive targets and required owners from checked-in configuration;
- calculate exact duplication, structural shadowing, affected guidance chains, source hashes, and
  byte budgets;
- render prospective diffs and refuse conflicted or unsafe transactions; and
- apply an already accepted operation when explicitly invoked.

The core never calls a model, decides whether a semantic claim is true, authenticates a claimed
human identity, executes a registered check, or silently promotes, rewrites, or deletes guidance.
An optional curation skill can improve proposal quality, but its output enters through the same
inert record and owner review path as any other proposal.

## Addition, replacement, supersession, and pruning

An `add` asserts that no active subject is being displaced. An exact duplicate, structured-key
collision, or deterministically shadowed addition is blocking because accepting it would encourage
append-only growth without adding active guidance.

A `replace` names the exact active subject and supplies its successor. Review shows the removed and
added bytes separately as well as the net active-context delta. Promotion records the predecessor
digest in the event so history can be reconstructed even after the source changes.

`supersede` is a replacement with an explicit relationship between two curation records. It is used
when earlier promoted guidance has a known proposal id; legacy guidance can still be replaced by
target digest without inventing a historical record.

`remove` is the proposal intent for pruning. The explicit `prune` operation applies only an accepted
removal and records why the guidance is no longer active. Budget pressure by itself may motivate a
proposal, but it never authorizes automatic deletion.

## Audit retention

Curation records are checked-in decision evidence, not runtime instructions. Rejection and
withdrawal keep the original proposal and rationale. Promotion, supersession, and pruning keep the
canonical before and after subject digests, exact source digests before and after apply, decision
attribution, related proposal id when applicable, and review reference. Records must not embed full
private transcripts or secrets; evidence references should point to appropriately governed
artifacts.

Git history is useful corroborating evidence but is not the only representation of the lifecycle:
the record must explain the decision when viewed in a checkout. Retention cleanup, if introduced,
must be an explicit policy and operation, and must preserve a compact tombstone plus content digest.

## Evaluation hooks

The curation record and review result expose stable fields that the separate evaluation harness can
join to recorded runs:

- proposal id, intent, state, and event times;
- repository, source, and compiled-guidance revisions before and after promotion;
- affected scopes and guidance chains;
- active bytes before, after, and delta; and
- linked evaluation evidence ids.

This supports longitudinal comparisons of correctness, files and lines inspected, tool actions,
active guidance bytes, proposal volume, acceptance rate, replacement-to-addition ratio, and time to
decision. Correctness remains the gate before efficiency comparisons. Evaluation consumes recorded
runs and curation metadata; curation never runs a model or treats an evaluation score as permission
to promote or prune.

The separate evaluation harness accepts a versioned longitudinal link manifest that cross-validates
these curation facts against issue #15's recorded-run inputs. It remains read-only and reports
correlation for owner review, never causal proof or permission to apply a proposal.

## Compatibility and rollout

- Repositories without `.murlocs/curation/` behave exactly as they do today.
- Existing schema-version-1 manifests and single-file or layered networks require no migration.
- Compilers ignore curation records because they are not registered sources. New curation commands
  validate the directory independently; malformed records may fail `murlocs curate check` without
  changing ordinary `compile` semantics.
- The first implementation should remain opt-in and may introduce `murlocs curate check` in CI.
- A later manifest policy may require clean curation validation or owner decisions, but enabling it
  must be explicit and cannot activate proposal payloads.
- Legacy active guidance has no synthetic proposal history. Its first replacement or removal uses
  a current content digest as the target and begins the audit trail at that decision.

## Rejected alternatives

**Load candidates as disabled layers.** Rejected because a flag, merge bug, or compatibility change
could accidentally place unapproved content in generated maps. Physical separation from the source
graph is a stronger boundary.

**Let an agent rewrite AGENTS.md or active layers directly.** Rejected because it bypasses owner
review, weakens generated-file ownership, and makes rejection and supersession unauditable.

**Automatically prune when the context budget is exceeded.** Rejected because byte pressure cannot
determine which semantic claim should survive.

**Use embeddings or model classification in `check`.** Rejected because results would not be
portable, reproducible, or suitable for deterministic enforcement. An optional skill may attach
those suggestions as non-authoritative evidence.

## Implementation slices

The accepted design is intentionally split into independently testable work:

1. [Issue #25](https://github.com/lbliii/murlocs/issues/25) defines the versioned record parser,
   lifecycle validation, inert storage boundary, and deterministic review report.
2. [Issue #26](https://github.com/lbliii/murlocs/issues/26) adds repository-confined acceptance,
   rejection, withdrawal, promotion, supersession, and pruning transactions with owner and
   stale-base enforcement.
3. [Issue #27](https://github.com/lbliii/murlocs/issues/27) extends recorded-run evaluation with
   longitudinal curation and guidance-growth summaries joined to issue #15's external ingestion.

Each slice must preserve the model-free compiler, generated-file ownership protections, and the
rule that no proposal affects active output before an explicit owner decision and apply operation.
