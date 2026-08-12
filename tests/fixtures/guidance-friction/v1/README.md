# Guidance-friction observation fixtures (schema v1)

Valid and rejected observations for [#132](https://github.com/lbliii/murlocs/issues/132).

- `valid/` — parse successfully as inert `record_kind = "observation"` records.
- `rejected/` — must fail visibly (unsupported version, unknown fields, unsafe paths,
  forbidden content, wrong record kind, etc.).

These fixtures never activate guidance and never imply an authenticated decision.
