# Source annotation authority model

Status: version 1.0, design and conformance baseline for issue #83. This document defines
authority and safe handling before Murlocs accepts a source-annotation parser or manifest field.
It does not define annotation grammar; [#84](https://github.com/lbliii/murlocs/issues/84) owns that
contract and its language corpus.

## Decision

Source files are candidate repository content, not guidance authority. An annotation-shaped comment
is inert unless a later, versioned implementation resolves its identifier through an explicitly
declared manifest or layer relationship. Even then, the source supplies only an attachment to a
declared location. The manifest or layer remains the sole source for guidance text, statement,
severity, scope, owner routing, and relationship kind.

Version 1 therefore has these non-negotiable semantics:

- An undeclared marker is inert. It cannot create guidance, change severity, grant ownership,
  suppress a finding, change a check, or execute a command.
- A marker is never agent-facing prose. A parser may retain a bounded identifier and a
  file-and-location attachment, but it must never copy surrounding source text into an `AGENTS.md`,
  outcome, prompt, command, or URL.
- Source attachment is evidence, not semantic proof. A present marker does not establish that an
  invariant is true, that a check passed, or that an actor is authorized.
- The current resolver parses only reviewed, finite declared-file relationships under contract v1.
  Undeclared source content remains ignored; parsing must not silently change this safety property.

## Authority and trust matrix

| Input or actor | Authority | Deterministic Murlocs guarantee | Not a guarantee |
| --- | --- | --- | --- |
| Manifest and registered layers | Declared guidance model after repository review | Parse, validate, render, and bind provenance deterministically | Authenticity, approval, or truth of a claim |
| Generated maps and lockfile | Derived output only | Ownership hashes and generated-file boundaries are checked | A generated map cannot authorize its own source or policy |
| Annotation-bearing source | Untrusted candidate attachment | It is ignored today; later it may provide only declared, bounded identifier/location evidence | Guidance, severity, ownership, suppression, commands, or approval |
| CODEOWNERS | Review-routing policy | Exact configured entries can be checked and reported | Identity authentication or proof of approval |
| Git refs, index, and review | Revision and review evidence | Explicit revisions can be compared; checks can report repository state | An authenticated owner decision from a commit, ref, or exit code |
| Hooks and CI | Enforcement or observation boundary selected by integration | Existing bounded hooks and lifecycle receipts preserve their stated contract | Permission to promote source text or bypass review |
| Trusted adapter | Out-of-band repository-view and freshness evidence | It can bind an operation to an exact snapshot, deadline, and receipt scope | Authority to reinterpret annotation text or mint human approval |
| Agent | Proposal and ordinary-edit participant | It cannot mint freshness evidence or approval through Murlocs | Authority to promote a marker or execute source content |
| Human owners and repository identity systems | Policy authorship and approval | Murlocs routes declared owners and records evidence | Local owner strings alone authenticate a human |

“Trusted adapter” means the host integration is trusted for the limited responsibilities defined in
[the activation lifecycle](activation-lifecycle.md), not that arbitrary content it reads is trusted
as instructions. Repository review and identity controls remain outside this local tool.

## Candidate/base behavior and freshness

When a future integration compares a candidate head to a base, it MUST use adapter-supplied,
revision-pinned snapshots. It MUST treat an added, removed, moved, or meaning-changed declared
attachment conservatively:

1. resolve base and head independently against their respective manifest/layer models;
2. route review to the union of declared affected scopes and owners from both views;
3. report the change as needing review until the repository's review policy accepts it; and
4. never let a head-only marker, deletion, or move lower severity, remove an owner, suppress a
   finding, or authorize an action before that review.

An attachment's *meaning* changes when its declared relationship, target, scope, or governing
manifest/layer meaning changes; byte-equal comments do not freeze those declarations. A base-only
attachment that disappears remains review-relevant, and a head-only attachment cannot become active
solely because it exists in the candidate branch.

Every observation, cache key, and result must bind the complete base/head identifiers, manifest and
layer digests, selected paths, parser-contract version, and adapter receipt scope. A missing,
expired, mismatched, force-pushed, or branch-raced input invalidates the observation and requires a
fresh read. No cache fallback may claim a clean attachment state. This extends the lifecycle's
existing stale-proof behavior; it does not define a universal Git snapshot algorithm.

## Content and path threat handling

The following rules are conformance expectations for any implementation that follows this model.
The resolver treats all of these inputs as unavailable rather than interpreted.

| Threat or ambiguous input | Required treatment |
| --- | --- |
| Free-form prose, instruction-like verbs, shell fragments, URLs, prompts, or credentials | Do not expose, interpolate, fetch, execute, or render them. Only a future grammar-approved identifier may be retained. |
| Visual spoofing, Unicode confusables, mixed scripts, case variants, or look-alike namespace text | Reject or report deterministically under the future grammar; never normalize a look-alike into authority. |
| Duplicate identifiers, copy/paste markers, and marker examples in strings, tests, or documentation | Never infer intent. A future parser must use syntax-aware comment boundaries and report declared duplicates; prose examples and string literals stay inert. |
| Generated, vendored, dependency, ignored, or submodule content | Exclude by default. Inclusion, if ever allowed, must be an explicit reviewed declaration and cannot raise authority; generated or vendor status must remain visible in findings. |
| Symlinks, path traversal, absolute paths, and filesystem races | Do not follow an annotation path outside the resolved repository view. Revalidate root confinement and snapshot identity before use; reject path escape and fail closed on race. |
| Non-text, undecodable, oversized, or parser-ambiguous input | Do not guess. Return a stable unavailable/malformed finding, retain no source prose, and leave guidance unchanged. |
| Malicious deletion, relocation, or rewrite on a candidate branch | Apply the conservative base/head rule above and route the union of affected owners. |

The future grammar must make comment recognition deterministic and language-wrapper-specific enough
to distinguish actual comments from quoted documentation and string data. This document intentionally
does not choose a grammar, Unicode normalization, wrapper list, or error taxonomy; doing so before
#84 would create a second competing contract.

## Resource limits, failure, and rollback

Discovery must be local, model-free, network-free, and bounded before it opens candidate files.
An implementation must cap selected files, bytes per file, total bytes, candidate markers, parser
work, path depth, and elapsed time. Limits and their stable finding codes belong to the versioned
parser contract, not host discretion. It must neither invoke registered checks nor follow external
links while discovering annotations.

On any limit, decode, ambiguity, path, stale-evidence, or adapter failure, the safe result is no
new attachment and no guidance mutation. The tool may return a typed uncertainty or blocking
finding according to the later contract, but it must not treat absence as approval, remove an
existing finding, or generate a partial advisory from source prose. Recovery means acquire a fresh
trusted view and rerun deterministically. Rollback of active guidance continues to use the existing
manifest/layer and transactional curation mechanisms; source markers themselves are never a
rollback authority.

## Required conformance evidence

`tests/fixtures/source-annotation-threats/` contains malicious and accidental marker-shaped source
content. `tests/test_annotation_authority.py` proves that current compilation does not copy any of
those tokens into generated agent guidance. When annotation discovery is added, that fixture must
remain and be extended with parser-contract corpus cases; a passing parse must still not expose
source prose.

An implementation claiming this model MUST also demonstrate, with deterministic tests, that it:

- keeps base/head results revision-pinned and rejects stale or raced evidence;
- reports duplicate, spoofed, malformed, path-escaped, generated, vendored, and resource-limited
  cases without interpreting their prose;
- applies the same authority result through `check`, `explain`, `impact`, hooks, and supported
  adapters; and
- preserves existing textual anchors until their separately specified compatibility path is
  implemented.

## Explicit non-goals

This model does not authenticate contributors, define an annotation language, parse source files,
grant a local owner string security meaning, prove behavior from an evidence marker, execute a
command, fetch a URL, add suppression/exemption directives, or make arbitrary source prose safe as
a prompt. It also does not replace Git review, branch protection, CODEOWNERS enforcement, CI, or
human judgment.
