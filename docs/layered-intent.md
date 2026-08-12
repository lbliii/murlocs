# Layered repository intent (design RFC)

Status: design RFC for [#153](https://github.com/lbliii/murlocs/issues/153), child of epic
[#152](https://github.com/lbliii/murlocs/issues/152). This document specifies the layered intent
contract and the compact task frame it produces. It does **not** implement a parser, compiler,
resolver, or permanent schema (#154 and later). Illustrative TOML below is a design sketch only.

The permanent schema-version decision is deferred until human and agent pilots report
([#157](https://github.com/lbliii/murlocs/issues/157), [#159](https://github.com/lbliii/murlocs/issues/159),
graduation gate [#160](https://github.com/lbliii/murlocs/issues/160)).

Representative fixtures live under
[`tests/fixtures/layered-intent/`](../tests/fixtures/layered-intent/).

## Problem

Repository guidance often accumulates individually reasonable commands whose applicability,
precedence, and tradeoffs are unclear when several maps are active. Before adding schema, Murlocs
needs a precise contract for **intent** as an outcome-oriented, advisory layer — distinct from hard
constraints, invariants, and verification claims — that composes across scopes without flattening
into one prose blob.

## Working model

```text
user task
  + applicable intent chain
  + hard constraints
  + relevant evidence
  = task frame
```

Only the applicable root-to-local intent chain enters active agent context. Hard constraints,
invariants, and registered checks remain separate planes with their existing authority.

## Terms

These terms are the closed vocabulary for layered intent. Implementations must not silently rename
or merge them.

### `intent`

An owner-authored, scoped advisory statement of what a scope is trying to achieve and how it
contributes to its parent. Intent guides judgment. It is never a hard constraint, never a
verification claim, and never agent-activatable without owner review.

An intent record is identified by a stable `id` and bound to exactly one guidance scope (or the
root network). It carries the fields below and optional provenance metadata.

### `outcome`

The durable result the scope exists to produce for its consumers (humans, agents, or dependent
scopes). An outcome is stated as a present-tense achievement, not as a list of implementation
steps. Example: “Typed CLI, MCP, and discovery surfaces stay in lockstep from one registry.”

### `contribution`

How this scope’s outcome advances or specializes its parent’s outcome. Contribution is the edge
label in the intent chain: it must name the parent relationship without restating the parent’s
entire outcome. Root intent has no parent contribution; its contribution field is omitted or
explicitly `null`.

### `success`

Observable conditions under which the intent is considered met for ordinary work. Success may
point at existing verification surfaces (registered checks, evidence anchors, review gates) but
does not itself assert that those surfaces currently pass. Success is advisory framing, not a
`murlocs check` finding.

### `priorities`

An ordered list of tradeoffs the scope prefers when multiple valid implementations exist. Earlier
entries outrank later ones within the same intent record. Priorities never override hard
constraints or critical invariants; they only rank advisory choices inside the allowed space.

### `non_goals`

Explicit exclusions: work the scope must not pursue even when adjacent or tempting. Non-goals are
advisory boundaries for judgment. They do not replace `do_not` judgments, stop-and-ask rules, or
invariants; when those hard or reviewed surfaces already forbid a behavior, intent must not
duplicate them as the sole enforcement mechanism.

## Layer specialization

Intent specializes along the same ownership grain as layered manifests, without collapsing layers
into one concatenated prose blob.

| Layer kind | Role in the intent chain | Specialization rule |
| --- | --- | --- |
| **Root** | Network-wide outcome and default tradeoffs | Declares the product/network outcome. May omit `contribution`. Sets default priorities and non-goals that children refine. |
| **Domain** | Outcome for a coherent ownership area (for example `core`, `tests`, `docs`) | Must state `contribution` to root (or an explicit parent domain). Narrows priorities and non-goals; may not silently delete a parent non-goal without `override`. |
| **Package** | Outcome for a deployable or importable unit inside a domain | Contributes to its domain. Adds package-local success signals and tradeoffs. |
| **Component** | Outcome for a focused subsystem inside a package | Contributes to its package. Keeps the shortest local outcome; prefers pointers to parent priorities over restating them. |

### No flattening

A resolved intent chain is an ordered sequence of intact records:

```text
[root intent] → [domain intent] → [package intent] → [component intent]
```

Each record retains its own `id`, scope binding, owners, fields, and source path. Renderers and
explain surfaces MUST present the chain as discrete nodes (or equivalent structured objects). They
MUST NOT merge field text into a single undifferentiated paragraph that erases layer boundaries,
ownership, or provenance.

Active context may include a compact rendering of the chain, but compactness is achieved by
budgeting and summarization rules — not by destroying structure.

## Interaction with other planes

| Plane | Authority relative to intent | Interaction |
| --- | --- | --- |
| **User task** | Highest task-local objective | The task selects which path (and therefore which intent chain) applies. Intent frames how to pursue the task; it does not rewrite the user’s stated goal. When task and local intent appear to conflict, agents MUST surface the tension and stop-and-ask rather than silently preferring either side. |
| **Repository intent chain** | Advisory outcome context for the path | Supplies outcome, contribution, success framing, priorities, and non_goals for judgment. |
| **Local intent** | Narrowest applicable record in the chain | Specializes parents; never invents authority outside its scope. |
| **Hard constraints** | Binding limits (policy, security, path confinement, ownership refusal rules, lifecycle gates) | Always dominate intent. Intent MUST NOT be used to soften or bypass them. |
| **Invariants** | Reviewed architectural claims with severity and verification mode | Remain separately declared and verified. Intent may *reference* an invariant as a success signal; it must not restate the invariant as if intent verification proved it. |
| **Checks** | Registered commands and proof wiring | Remain the only command-shaped verification surface. Intent success may name a check; Murlocs still never executes that check while validating intent structure. |
| **Judgments / guardrails / stop-and-ask / done criteria** | Existing advisory and process guidance | Stay on their current planes. Intent is additive outcome framing, not a replacement for judgments. |
| **Evidence** | Checked-in anchors, receipts, impact routing | Enters the task frame as relevant evidence for the path; intent does not mint evidence. |

### Task frame

A **task frame** is the compact bundle an agent (or human) should load for a path-bound task:

1. the user task statement (external to the manifest);
2. the applicable intent chain (root → … → local), intact and ordered;
3. the hard constraints and critical/important invariants already selected by ordinary map
   activation for that path;
4. pointers to relevant evidence and registered checks named by success fields or the active maps.

The task frame is a semantic composition contract. Encoding it as a new on-disk artifact is out of
scope for this RFC; #155 covers compact rendering across explain, diff, and impact.

## Inheritance, override, provenance, ownership, ambiguity

### Inheritance

Child intent inherits parent outcome context by chain position. Field inheritance is **explicit
specialization**, not silent textual merge:

- Children MUST supply their own `outcome` and `contribution`.
- `priorities` and `non_goals` append specialization by default: child entries refine the effective
  ordered view after parents, with exact-duplicate suppression keeping the first occurrence.
- A child MUST NOT remove or invert a parent priority or non-goal unless it sets an explicit
  override (below).

### Explicit override

Following layered-manifest practice, a later intent may replace an earlier keyed intent only with
`override = true` (name illustrative). Override rules:

- Override is full replacement of that intent identity’s advisory fields, not a stealth partial
  edit of parent prose.
- Override MUST preserve the bound scope path/map identity; changing path or map via override is a
  structural error.
- Overlay-kind sources may refine priorities and non_goals; they may not invent a parallel root
  outcome for the same network without a distinct intent id and owner review.
- Agents may propose overrides through curation-shaped records later (#161); they cannot activate
  them.

### Provenance

Every intent record MUST name:

- the source layer or manifest path that declared it;
- the owning scope id;
- the reviewing `owners` for that source (inherited from layer/manifest ownership when omitted).

Resolved chains expose per-node provenance. Compact agent output may abbreviate provenance but
MUST keep enough identity to route review (scope id + owners + source path).

### Ownership

Owners of the declaring layer or root manifest own the intent text. Intent cannot transfer
ownership of code, maps, or checks. CODEOWNERS and layer `owners` remain the review-routing
authority; intent owners are the same review surface unless a future graduation decision says
otherwise.

### Structural ambiguity behavior

Deterministic validation fails closed on structural ambiguity. Examples that MUST reject before
any activation or render of intent:

| Ambiguity | Required behavior |
| --- | --- |
| Duplicate intent `id` without `override` | Reject with a finding naming both sources |
| Two applicable intents at the same scope depth with no deterministic order | Reject; require layer order, explicit parent, or override |
| Missing parent reference / broken contribution target | Reject |
| Cycle in parent/contribution links | Reject |
| Intent bound to an unknown scope id | Reject |
| Override that changes immutable scope path/map | Reject |
| Unknown fields under a chosen experimental schema | Reject (strict) once a parser exists; this RFC does not ship one |
| Intent present but repository declares no experimental opt-in (when an opt-in exists) | Reject or ignore per the experimental model’s compatibility rule (#154); absence of all intent remains byte-compatible |

Semantic disagreement between natural-language outcomes (for example two priorities that a human
would call contradictory but that share no structural conflict) is **out of scope** for
deterministic validation. Surface such tension through owners and pilots, not automatic NL
agreement detection.

## Deterministic validation: establishes vs excludes

### What deterministic validation CAN establish

Once an experimental model exists (#154), validation may establish only:

- well-formed structure and closed field sets;
- identity uniqueness, ordering, and explicit-override legality;
- scope binding and parent-link integrity (no missing refs, cycles, or illegal cross-layer
  mutation);
- provenance completeness and owner routing metadata presence;
- separate active-intent byte budgets and overflow policy application;
- that repositories with no intent declarations retain byte-identical load/render behavior for
  existing maps and lockfiles.

### What deterministic validation MUST NOT claim

Validation MUST NOT claim:

- that outcomes are true, wise, or currently achieved;
- that priorities are the right tradeoffs;
- that natural-language objectives agree or conflict;
- that success conditions are satisfied;
- that an agent followed intent;
- that intent is approval, authorization, or proof;
- that invariants or checks passed because intent named them.

This matches the architecture rule that `murlocs check` establishes internal coherence, not
semantic truth of architectural claims.

## Active-intent byte accounting

Intent has a **separate** budget from map active-context bytes (`max_active_bytes`).

| Budget | Measures | Default posture |
| --- | --- | --- |
| Map active bytes | Generated `AGENTS.md` chain for a path | Existing `max_active_bytes` control plane |
| Active-intent bytes | UTF-8 bytes of the applicable intent chain’s compact rendering (or raw declared fields if no compact form yet) | Separate limit, root-declared when intent is opted in |

Illustrative control-plane field (name not final):

```toml
# Root control plane only — illustrative, not shipped schema.
max_active_intent_bytes = 4096
```

### Overflow policy

When the applicable intent chain exceeds `max_active_intent_bytes`:

1. **Do not** silently truncate mid-field or mid-record.
2. Emit a stable structural finding (illustrative code: `intent.budget-overflow`).
3. Prefer failing closed for compile/check of intent-enabled networks: overflowing intent is not
   activated into agent context.
4. Optional future soft mode (post-graduation only, if pilots demand it): drop **deepest**
   component/package nodes first while retaining root and domain, and mark the frame
   `intent_truncated=true` with the omitted ids — never invent summarized prose that was not
   declared.

Until pilots complete, the RFC selects fail-closed overflow as the default experimental policy.
Map `max_active_bytes` accounting MUST remain unchanged and independent: intent overflow must not
steal map budget, and map overflow must not be “fixed” by omitting intent.

## Byte-compatible absence

Repositories that declare **no** intent:

- load, compile, check, explain, diff, and impact exactly as today;
- produce byte-identical generated maps and lockfile hashes for the same inputs and tool version;
- incur zero active-intent bytes;
- must not require new control-plane fields.

Presence of documentation or fixtures about intent in the Murlocs repository itself does not
activate intent in consumer repositories.

## Schema version deferral

This RFC deliberately does **not** mint a permanent `schema_version` bump or a frozen
`io.murlocs.intent` contract id. Experimental representation choices belong to #154 and remain
behind an opt-in until [#160](https://github.com/lbliii/murlocs/issues/160) records a graduation
decision informed by human (#157) and agent (#159) pilots.

## Examples

Fixtures mirror these cases under `tests/fixtures/layered-intent/`.

### Valid chain

Root outcome specialized by domain and package without erasing parents:

See `tests/fixtures/layered-intent/valid/root-to-package.toml` for the full root → domain →
package sketch. Condensed form:

```toml
[[intent]]
id = "network"
scope = "root"
outcome = "Repository guidance stays local, layered, and reviewable."
success = ["Owners can explain the governing outcome for a path."]
priorities = ["Prefer scoped maps over global commandments.", "Keep active context modest."]
non_goals = ["Hosted control plane as a prerequisite."]

[[intent]]
id = "core-domain"
scope = "core"
contribution = "Implements the product contract as deterministic CLI behavior."
outcome = "Parsing, rendering, and verification remain model-free and local-first."
success = ["Registered checks and invariants stay separately enforceable."]
priorities = ["Fail closed on structural ambiguity."]
non_goals = ["Executing registered checks during guidance validation."]
```

### Conflicting (structural)

Two domain intents claim the same `id` without override — deterministic reject:

See `tests/fixtures/layered-intent/conflicting/duplicate-id.toml`. Condensed form:

```toml
[[intent]]
id = "core-domain"
scope = "core"
outcome = "Keep the CLI thin."
contribution = "Specializes root toward CLI UX."

[[intent]]
id = "core-domain"
scope = "core"
outcome = "Keep the CLI exhaustive."
contribution = "Specializes root toward complete surfacing."
# missing override = true → structural conflict
```

### Stale

Intent that names a removed scope or parent contribution target — deterministic reject; also the
canonical “stale” review case when success points at a deleted evidence anchor (reported as
structural/missing reference, not as semantic falsehood):

See `tests/fixtures/layered-intent/stale/missing-scope.toml`. Condensed form:

```toml
[[intent]]
id = "legacy-payments"
scope = "payments"  # scope id no longer in the manifest
contribution = "Collects revenue paths for the root product outcome."
outcome = "Payment adapters share one audited interface."
success = ["evidence:docs/payments.md#shared-interface"]
```

### Overly broad

A component intent that restates the entire network outcome and piles unbounded priorities —
validatable as budget/shape risk; owners should reject in review even if structure parses:

See `tests/fixtures/layered-intent/overly-broad/component-restates-network.toml`. Condensed form:

```toml
[[intent]]
id = "button-widget"
scope = "ui-button"
contribution = "Everything the company does."
outcome = "Make the whole product successful, delightful, secure, fast, and globally compliant."
priorities = [
  "Delight",
  "Security",
  "Performance",
  "Accessibility",
  "Internationalization",
  "Revenue",
  "Hire more owners",
]
non_goals = []
```

Overly broad intent is primarily a **curation and pilot** concern. Deterministic validation may
enforce byte budgets and field presence; it does not auto-decide that an outcome is “too vague.”

## Rejected alternatives

| Alternative | Why rejected |
| --- | --- |
| Flatten all active intent into one prose section in `AGENTS.md` | Erases layer boundaries, ownership, and contribution edges; recreates commandment soup. |
| Treat intent as invariants or checks | Confuses advisory outcomes with enforceable claims and invites fake proof. |
| Auto-detect natural-language conflicts with a model | Violates local-first deterministic core; deferred forever as a non-goal of #153. |
| Encode decision history / ADR streams inside intent | Out of scope; intent is current outcome framing, not an archaeology log. |
| Let agents activate intent they proposed | Breaks owner authority; proposals may exist later only through inert curation. |
| Share a single byte budget with maps | Couples unrelated failure modes; intent overflow would silently displace hard guidance or vice versa. |
| Ship a permanent schema version in this RFC | Premature before human and agent pilots (#160). |
| Replace task commands (#118) with intent | Intent should enrich orient/review/finish frames, not fork their envelope. |

## Non-goals (this RFC)

- Implementing the parser or compiler.
- Automatically deciding whether natural-language objectives agree.
- Encoding implementation decision history.
- Shipping experimental types or resolver behavior (#154).
- Permanent schema graduation (#160) or curation productization (#161).

## Relationship to existing docs

- [Layered manifests](layers.md) — merge, override, ownership, and provenance patterns intent reuses.
- [Architecture](architecture.md) — judgment vs enforcement; check ≠ semantic truth.
- [Intent-shaped task commands](task-commands.md) — task-language front door that should eventually
  consume effective intent without forking outcome semantics.
- [Outcome envelope](outcome-envelope.md) — structured command results; unrelated naming collision
  with intent’s `outcome` field; keep the namespaces distinct in implementations.
- [Governed guidance curation](curation.md) — future proposal plane for intent changes after
  graduation.

## Done means for #153

- This RFC defines the terms, layer specialization, plane interactions, inheritance/override/
  provenance/ownership/ambiguity rules, validation boundary, separate byte accounting and
  overflow policy, absence compatibility, examples, rejected alternatives, and schema deferral.
- A documentation contract test asserts the RFC’s presence and required sections.
- No experimental intent model is implemented here.
