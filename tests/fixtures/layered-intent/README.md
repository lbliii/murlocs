# Layered intent RFC fixtures

Illustrative TOML sketches for
[`docs/layered-intent.md`](../../../docs/layered-intent.md). They are **not**
parsed by Murlocs today (#153 is design-only; #154 owns the experimental model).

| Directory | Case |
| --- | --- |
| `valid/` | Root → domain specialization without flattening |
| `conflicting/` | Duplicate intent id without override |
| `stale/` | Intent bound to a missing scope |
| `overly-broad/` | Component restates the whole network outcome |
