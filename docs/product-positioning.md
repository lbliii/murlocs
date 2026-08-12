# Murlocs product positioning

## Category

Murlocs is a **compiler for repository guidance**. It turns reviewed guidance
into the layered `AGENTS.md` files coding agents already discover, then checks
what applies, why it applies, and what a change may affect.

## Audience

Murlocs is for repository owners and platform teams whose coding agents need
to follow architecture and operating rules. It helps when a repository has
enough scope, history, or contributors that “just add an `AGENTS.md`” no
longer holds up.

## Problem

Ad hoc `AGENTS.md` files are easy to start and hard to keep current. They drift
from their reviewed source and each other, miss important scopes, hide who owns
a rule, and make it hard to show which instructions apply. Teams rediscover
context, make inconsistent changes, and spend reviews fixing what the agent
should have known.

## Promise

> Give every coding agent the right map—and prove the map is still current.

Murlocs keeps guidance in reviewed, owned layers, compiles it into portable
agent maps, and shows what applies to a path and what a change can affect.

## Product pillars

1. **Maps, not mandates.** Guidance stays local, layered by scope, and in
   portable files rather than a proprietary runtime.
2. **Deterministic compilation.** Reviewed layers compile into familiar,
   inspectable, repeatable `AGENTS.md` files.
3. **Evidence before confidence.** `check`, `explain`, and `impact` show
   coverage, provenance, and changed-path consequences instead of asking teams
   to assume them.
4. **Ownership at the boundary.** Rules live where they apply, with stewards
   and source material reviewers can evaluate.
5. **Agent judgment, deterministic core.** Agents help author and interpret
   guidance; deterministic code compiles, validates, and routes it.

## Proof points

- Standard layered `AGENTS.md` is the compiled interface, so agents can use
  repository-native guidance without a hosted service.
- Layers make scope, merge behavior, ownership, and provenance reviewable in
  version control. See [Layered manifests](layers.md).
- Deterministic checks catch manifest, coverage, and generated-file drift. They
  do not run a model, reach the network, or execute registered repository
  commands.
- `explain` shows why guidance applies; `impact` shows which maps and owners
  need review when paths change. See [Changed-path guidance impact](impact.md).
- Migration keeps a practical legacy boundary and supports gradual, reviewable
  adoption. See [Architecture](architecture.md) and [Primary user journeys](journeys.md).
- The project records pilot and evaluation evidence instead of making
  unmeasured adoption claims. See the
  [Chirp and Kida paired-pilot audit](pilots/chirp-kida.md),
  [Synthetic 91-map scale pilot](pilots/scale-network.md),
  [Multi-repository passive-loop pilot report](pilots/passive-loop-multi-repo.md), and
  [Guidance efficiency evaluation](evaluation.md).

## Boundaries and nonclaims

Murlocs verifies applicability, synchronization, structure, and evidence
wiring. It does not determine whether a rule is semantically true, complete,
or wise. People still author and review the repository’s operating knowledge.

The core `check` path does **not** call a model, access the network, or execute
registered repository commands. Murlocs is not a hosted service, a merge-policy
engine, or a replacement for CI and code review. Its authoring skill is
optional; the compiled maps and checks work without it.

## Message hierarchy

Use this order when explaining Murlocs:

1. **Outcome:** Give every coding agent the right map—and prove the map is still current.
2. **Category:** A compiler for repository guidance.
3. **Mechanism:** Review layers, compile standard `AGENTS.md`, then use
   deterministic `check`, `explain`, and `impact` to inspect and route guidance.
4. **Why now:** Ad hoc guidance drifts, leaves blind scopes, and has no
   evidence trail.
5. **Trust boundary:** Murlocs verifies applicability, synchronization,
   structure, and evidence wiring; it does not assert semantic truth or impose
   hosted policy.

## Terminology

| Term | Meaning |
| --- | --- |
| **Guidance network** | The scoped instructions, ownership, evidence, and relationships agents use to navigate a repository. |
| **Map** | A compiled `AGENTS.md` file that tells an agent what applies within a repository scope. |
| **Layer** | A reviewed source unit that contributes scoped guidance and metadata to compiled maps. |
| **Manifest** | The source configuration that declares layers, paths, and compilation semantics. |
| **Provenance** | The reviewable origin, ownership, and evidence behind a piece of guidance. |
| **Coverage** | Whether the repository’s relevant scopes have a reachable, applicable map. |
| **Impact** | The maps, rules, and owners a set of changed paths may affect. |

## Reusable descriptions

**One line:** Murlocs gives every coding agent the right repository map—and
proves the map is still current.

**Short:** Murlocs is a local-first compiler for repository guidance. It
compiles reviewed, owned layers into standard `AGENTS.md` files and checks what
applies, why it applies, and what a change affects.

**Long:** Murlocs helps repository owners turn architecture and operating
knowledge into a dependable guidance network for coding agents. Instead of
maintaining disconnected `AGENTS.md` files by hand, teams review scoped layers
and compile them into portable maps. Local checks, explanations, and
changed-path impact reports show scope, ownership, provenance, and drift—without
a hosted service, model call, or merge-policy dependency in core verification.

## Hero rationale

Present Murlocs as a field instrument for layered repository terrain, not a chat
interface or dashboard. Use an original, text-free subterranean field map:
cutaway strata, intersecting paths, and evidence markers. The screen-print or
riso field-guide style should use limited river teal, lichen, and clay ink. It
connects the “right map” promise to the tactile personal-project stack around
Chirp, Bengal, and Kida.
