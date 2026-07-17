---
name: bootstrap-kodama
description: Inspect a repository and bootstrap or migrate a truthful Kodama guidance network. Use when asked to create repository agent guidance, convert an existing steward or AGENTS.md scheme, map architectural scopes and invariants, add coverage and proof wiring, or diagnose a failing `kodama check`.
---

# Bootstrap Kodama

Use agent judgment to discover and draft the repository model. Use the `kodama` CLI for deterministic
compilation and verification. Keep those responsibilities separate.

## Bootstrap workflow

1. Inspect the repository before proposing guidance.
   - Read existing `AGENTS.md`, `CLAUDE.md`, contributor docs, build configuration, test configuration,
     package boundaries, and CI definitions.
   - Identify source-bearing architectural units, not merely every directory.
   - Locate the exact files that prove build, lint, test, and boundary claims.
2. Preserve existing intent.
   - Treat existing instruction files as user-owned.
   - Classify each statement as a global rule, local rule, invariant, workflow, or background detail.
   - Do not run `kodama init` over an existing root `AGENTS.md`.
3. Draft `.kodama/manifest.toml`.
   - Keep scope IDs stable and paths repository-relative.
   - Add edges only when the dependency or coordination relationship is supported by code or docs.
   - Use `command` verification only when a registered check and its configuration proof exist.
   - Use `manual` with a real evidence file and anchor for reviewable textual claims.
   - Use `unknown` to expose debt; never upgrade an aspiration into an enforced fact.
   - Give every coverage exemption a concrete reason.
4. Compile and verify.
   - Run `kodama compile` only after reviewing the proposed manifest.
   - Run `kodama check`; resolve manifest, graph, proof, coverage, budget, ownership, and drift findings.
   - Run `kodama explain PATH` on representative files in each important scope.
   - Never execute commands registered in the manifest unless the user's task independently authorizes
     those commands.
5. Hand off clearly.
   - Summarize generated maps, important edges and invariants, reasoned exemptions, and remaining
     `unknown` verification debt.
   - Call out existing guidance that could not be migrated safely.

## Migration rules

Kodama deliberately refuses unmanaged or modified generated maps. Do not bypass that protection by
deleting, replacing, or relabeling files. Translate existing guidance into the manifest, show the
diff to the user when judgment is material, and use an explicit future adopt/import workflow when
the installed Kodama version provides one.

Keep generated maps concise. Put durable explanation in normal repository documentation and link
to it through evidence or the review protocol.
