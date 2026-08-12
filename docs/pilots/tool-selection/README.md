# Agent tool-selection corpus (v1)

Versioned ordinary-task prompts and a frozen first-tool scoring rubric for measuring whether
agents select the right Murlocs entry point. This is the #124 deliverable under epic #117
(saga #115). Recorded-run ingestion and baseline scoring belong to #125 / #126; this package does
**not** invoke a model or collect production telemetry.

## Layout

| Path | Role |
| --- | --- |
| [`rubric.md`](rubric.md) | Normative scoring labels and freeze rules |
| [`corpus.json`](../../../tests/fixtures/tool-selection/v1/corpus.json) | Versioned prompt corpus with expected answers |
| [`tool_selection.py`](../../../src/murlocs/tool_selection.py) | Strict corpus loader (unknown fields fail) |

## Pins

Every corpus document versions:

- **repository** — synthetic evaluation repository identity and revision label
- **tool_catalog** — path and revision of the checked-in [`io.murlocs.agent-inventory`](../../../tests/fixtures/agent-inventory/v1/inventory.json) snapshot
- **model** — corpus-level pin policy; concrete model id/revision are required on recorded runs (#125)
- **agent_environment** — corpus-level pin policy; concrete environment id/revision are required on recorded runs

Expected first tools and alternatives are drawn from agent-audience inventory names plus the
sentinel `none` (no Murlocs call).

## Freeze rule

Expected answers in `corpus.json` are fixed **before** candidate tool descriptions, discovery
copy, or composite-command wording are evaluated. Changing inventory descriptions must not rewrite
corpus expectations in the same change set as a scoring experiment.

## Validate

```bash
uv run python -m murlocs.tool_selection
# or
uv run pytest tests/test_tool_selection_corpus.py -q
```
