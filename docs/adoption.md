# Adoption status and coverage

`murlocs status` is a read-only orientation command for an unfamiliar or partially migrated
repository. It inventories checked-in files, reads Murlocs and migration metadata, and validates a
present manifest. It does not write files, execute registered commands, transfer ownership, or
judge whether repository guidance is semantically true.

Use `murlocs status --format json` or the MCP tool for a structured result. State, blocker, evidence,
and action `id` values are stable integration identifiers. Every classification includes concrete
file evidence. Recommended commands are either read-only or use the global `--dry-run` safeguard;
actions that require human judgment set `review_required`.

## Lifecycle states

| State | Evidence | Typical next safe action |
| --- | --- | --- |
| `uninitialized` | No instruction network or Murlocs manifest under the repository root | Preview `murlocs --dry-run init --repo .` |
| `user_owned` | Instruction files exist without a Murlocs manifest | Inventory and review ownership |
| `legacy_detected` | `.stewards/manifest.toml` exists without a Murlocs manifest | Run `murlocs diff` |
| `candidate_manifest` | `.murlocs/manifest.toml` exists without an ownership lock | Validate or compare the candidate before compile/adoption |
| `migration_adopted` | An active migration record has status `adopted` | Preview prune and rollback |
| `migration_pruned` | An active migration record has status `pruned` | Check the managed network and preview rollback |
| `managed_synchronized` | Manifest and lock are present with no deterministic validation findings | Explain guidance for the next target path |
| `managed_invalid` | A managed network has schema, proof, coverage, ownership, budget, lock, or drift findings | Inspect `murlocs check` output |
| `ambiguous` | Evidence conflicts or cannot be classified safely | Inventory and manually review before any write |

Impossible combinations take precedence over otherwise plausible classifications. Examples include
an ownership lock without a manifest, a migration record without a manifest, an unreadable migration
status, or simultaneous managed and legacy networks without an active migration. Murlocs reports
these as `ambiguous`; it does not choose an owner heuristically.

The structured result always includes `semantic_correctness: "not_evaluated"`. A synchronized map
is byte-current with its reviewed source contract, not proof that its architectural statements are
true.

## Coverage states

Coverage roots are an explicit manifest contract. Murlocs never turns directory guesses into
governed coverage automatically.

`murlocs init` accepts repeated repository-relative roots:

```bash
murlocs init --coverage-root src --coverage-root tests
```

The `coverage.state` in init and check output is one of:

- `unconfigured`: no roots were declared and no repository source coverage was evaluated;
- `structurally_incomplete`: declared roots have at least one coverage finding; or
- `structurally_complete`: declared roots have no structural coverage findings.

Structured output also includes `evaluated`, which is `false` for the unconfigured state and `true`
when declared roots were evaluated.

An existing manifest may intentionally retain `roots = []`; this remains a passing compatibility
state, but human and structured output call it `unconfigured`. Teams can opt in later by editing the
reviewed manifest, adding exact paths under `[coverage].roots`, and resolving the resulting findings
before compilation.
