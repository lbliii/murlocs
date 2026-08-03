---
name: bootstrap-murlocs
description: Inspect a repository and bootstrap or migrate a truthful Murlocs guidance network. Use when asked to create repository agent guidance, convert an existing steward or AGENTS.md scheme, map architectural scopes and invariants, add coverage and proof wiring, or diagnose a failing `murlocs check`.
---

# Bootstrap Murlocs

Use agent judgment to discover and draft the repository model. Use the `murlocs` CLI for deterministic
compilation and verification. Keep those responsibilities separate.

## Classify before writing

1. Inspect contributor docs, build and test configuration, package boundaries, CI definitions, and
   the applicable instruction files.
2. Run `murlocs inventory` before any initializing or migration write.
3. Choose exactly one route from the inventory and repository evidence:
   - **Existing Murlocs network:** `.murlocs/manifest.toml` exists.
   - **Recognized steward network:** `.stewards/manifest.toml` is reported as legacy stewards and no
     Murlocs manifest exists.
   - **Unmanaged guidance:** an `AGENTS.md`, `CLAUDE.md`, or Copilot instruction file exists but is not
     part of a recognized steward network or managed Murlocs network.
   - **Greenfield:** no Murlocs manifest, recognized steward network, or conflicting root
     `AGENTS.md` exists.
4. Stop if states conflict. Do not use file deletion, replacement, or generator-marker changes to
   make a repository appear greenfield or managed.

## Route: greenfield

1. Verify again that a root `AGENTS.md` does not exist; `murlocs init` must not overwrite one.
2. Run `murlocs init --name "REPOSITORY NAME"`.
3. Inspect source-bearing architectural units, then edit the generated `.murlocs/manifest.toml`.
   Configure coverage roots explicitly; an empty starter list does not prove repository coverage.
4. Keep scope IDs stable and paths repository-relative. Add edges only when code or docs support the
   relationship. Give every coverage exemption a concrete reason.
5. Use `command` verification only with a registered check and configuration proof. Use `manual`
   with real textual evidence. Use `unknown` to expose proof debt.
6. Review the manifest, run `murlocs compile`, then complete the shared verification workflow.

## Route: recognized steward migration

Treat inspection, candidate creation, ownership transfer, cleanup, and recovery as separate gates:

1. Run `murlocs inventory` and record the legacy map count, layered ownership, conflicts, and proof
   debt.
2. Run both `murlocs diff --mode semantic` and `murlocs diff --mode rendered`. Review semantic loss
   even when rendered maps look equivalent.
3. Run `murlocs import --from stewards --output .murlocs/manifest.toml`. Import creates a candidate;
   it does not adopt existing maps.
4. Review the candidate and resolve every blocking finding. Preserve unresolved, non-blocking claims
   as `unknown` proof debt instead of inventing enforcement.
5. Run `murlocs --dry-run adopt`, review the exact map set, then run `murlocs adopt` only with
   authorization to transfer ownership. Adoption requires byte-current legacy maps and creates a
   recoverable backup.
6. Run `murlocs check` and `murlocs explain PATH` for representative files in every important scope.
7. Run `murlocs --dry-run prune`, review the legacy files to archive, then run `murlocs prune` only
   when cleanup is authorized.
8. Run `murlocs --dry-run rollback` to verify the recovery plan. Run an actual rollback drill only
   when explicitly authorized; it restores the pre-adoption network and ends the managed state, so
   adopting again requires repeating the reviewed adoption gate.

## Route: unmanaged guidance

Treat every unrecognized or hand-authored instruction file as user-owned. Inventory and classify its
statements as global rules, local rules, invariants, workflows, or background detail, then propose a
manifest and a path-by-path reconciliation plan. Do not describe `import --from stewards`, `adopt`,
or another automatic transfer as safe for these files. Stop before compilation or ownership transfer
until the user reviews the semantic mapping and chooses how to reconcile each conflicting map.

## Route: existing Murlocs network

Do not initialize or import. Read the root manifest and only the layers relevant to the requested
paths. If `.murlocs/migration.json` exists, identify its current state before proposing another
migration action. Run `murlocs check`, inspect representative paths with `murlocs explain PATH`, and
make reviewed changes through the manifest or its declared layers. Compile only after reviewing those
source changes.

## Shared verification

- Run `murlocs check`; resolve manifest, graph, proof, coverage, budget, ownership, and drift findings.
- Run `murlocs explain PATH` on representative files in every important scope.
- Never execute commands registered in the manifest unless the user's task independently authorizes
  those commands.
- Keep generated maps concise. Put durable explanation in normal repository documentation and link
  it through evidence or the review protocol.

## Handoff

Report the route taken, generated map paths, important edges and invariants, coverage exemptions,
unknown proof debt, and guidance that remains user-owned. For migration work, also report the backup
path and state, whether prune and rollback were only previewed or executed, and the next safe action.

Murlocs deliberately refuses unmanaged or modified generated maps. Never delete, replace, relabel,
or edit generated output to bypass that protection.
