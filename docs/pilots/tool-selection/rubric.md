# Tool-selection scoring rubric (v1)

Normative labels for scoring a recorded **first Murlocs action** against a frozen corpus
expectation. Key words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are used as in RFC 2119.

This rubric measures first-tool selection only. It does **not** score answer correctness,
efficiency, or later tool calls. Live model invocation and production telemetry are out of scope.

## Inputs

A scored arm supplies:

| Field | Meaning |
| --- | --- |
| `prompt_id` | Id of a corpus prompt |
| `tool_catalog_revision` | Must match the corpus `tool_catalog.revision` |
| `model.id` / `model.revision` | Versioned model pin for the recorded run |
| `agent_environment.id` / `agent_environment.revision` | Versioned agent/ADE pin for the recorded run |
| `selected_first_tool` | Inventory command name, or `none` |
| optional evidence | Rationale text or transcript pointer retained outside Murlocs core |

Unknown fields on ingestion documents **MUST** fail visibly (#125). This corpus package only
defines expectations and label semantics.

## Expectation kinds

Each corpus prompt declares exactly one `expectation_kind`:

| Kind | Meaning |
| --- | --- |
| `fixed` | One preferred first tool is frozen in `expected_first_tool` |
| `none` | The correct first action is **not** to call a Murlocs tool (`expected_first_tool` is `none`) |
| `ambiguous` | The prompt intentionally admits more than one first tool; do not force a single winner |

`acceptable_alternatives` is a frozen set of inventory names that are allowed without counting as a
miss. For `ambiguous` prompts, `expected_first_tool` **MAY** name a preferred tool for reporting,
but any member of `{expected_first_tool} ∪ acceptable_alternatives` is within the ambiguity set.

## Scoring labels

Exactly one label **MUST** be assigned per scored arm:

### `correct_first_tool`

`selected_first_tool` equals `expected_first_tool`, and `expectation_kind` is `fixed` or `none`.

### `acceptable_alternative`

`expectation_kind` is `fixed`, `selected_first_tool` is not the expected tool, and
`selected_first_tool` is in `acceptable_alternatives`.

### `unnecessary_call`

`expectation_kind` is `none` and `selected_first_tool` is any inventory command other than `none`.

### `missed_call`

A Murlocs call was expected (`expectation_kind` is `fixed` or `ambiguous`) and either:

- `selected_first_tool` is `none`, or
- `selected_first_tool` is outside `{expected_first_tool} ∪ acceptable_alternatives`

Wrong-tool selections on fixed prompts are reported as `missed_call` (the agent missed the
expected entry point), not as a separate sixth label.

### `ambiguous_prompt`

`expectation_kind` is `ambiguous` and `selected_first_tool` is inside the ambiguity set
`{expected_first_tool} ∪ acceptable_alternatives`.

Ambiguous prompts are scored separately so graduation decisions can report ambiguity rate without
collapsing it into “correct.” A preferred `expected_first_tool` on an ambiguous prompt is
informational only; selection of that preferred tool still receives `ambiguous_prompt`, not
`correct_first_tool`.

## Freeze and revision rules

1. Expected answers **MUST** be authored and committed before candidate discovery copy or tool
   descriptions are compared under this rubric.
2. A scoring experiment **MUST** pin corpus revision, tool-catalog revision, model, and agent
   environment. Changing any pin starts a new comparable series.
3. Editing a prompt’s expectation fields **MUST** bump `corpus_revision` (or ship a new
   `schema_version` when the document shape changes).
4. Inventory description churn without a corpus expectation change does **not** rewrite frozen
   answers; it is a candidate under evaluation, not a new ground truth.

## Category coverage

The v1 corpus **MUST** include at least one prompt in each category:

| Category | Ordinary-task intent |
| --- | --- |
| `orientation` | Before unfamiliar work on a path |
| `explanation` | Understand the guidance chain for a path |
| `change_review` | Route or review an explicit change set |
| `completion` | Obtain a fresh structural completion receipt |
| `friction` | Guidance friction / inert curation review without silent mutation |
| `migration` | Inventory, diff, or adoption-status before legacy migration |
| `no_murlocs_call` | Ordinary coding where no Murlocs tool is the right first action |

## Non-goals

- No production prompts or telemetry.
- No model invocation from Murlocs core.
- No full evaluation runner (see #125).
- No claim that a label implies semantic truth of guidance content.
