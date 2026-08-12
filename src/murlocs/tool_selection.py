"""Versioned agent tool-selection corpus loader and schema checks.

Validates ``tests/fixtures/tool-selection/v1/corpus.json`` against the frozen
contract used by issue #124. Unknown fields fail visibly. This module does not
score recorded runs (see #125) and never invokes a model.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

CONTRACT = "io.murlocs.tool-selection-corpus"
SCHEMA_VERSION = 1
NONE_TOOL = "none"

REQUIRED_CATEGORIES = frozenset(
    {
        "orientation",
        "explanation",
        "change_review",
        "completion",
        "friction",
        "migration",
        "no_murlocs_call",
    }
)
EXPECTATION_KINDS = frozenset({"fixed", "none", "ambiguous"})
PROMPT_MIN = 20
PROMPT_MAX = 30

_REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = _REPO_ROOT / "tests/fixtures/tool-selection/v1/corpus.json"
INVENTORY_FIXTURE_PATH = _REPO_ROOT / "tests/fixtures/agent-inventory/v1/inventory.json"

_DOCUMENT_KEYS = frozenset(
    {
        "contract",
        "schema_version",
        "corpus_revision",
        "rubric",
        "repository",
        "tool_catalog",
        "model",
        "agent_environment",
        "categories",
        "notes",
        "prompts",
    }
)
_REPOSITORY_KEYS = frozenset({"name", "url", "revision"})
_TOOL_CATALOG_KEYS = frozenset({"contract", "schema_version", "path", "revision"})
_VERSIONED_PIN_KEYS = frozenset({"id", "revision", "pin_policy"})
_PROMPT_KEYS = frozenset(
    {
        "id",
        "category",
        "prompt",
        "expectation_kind",
        "expected_first_tool",
        "acceptable_alternatives",
        "notes",
    }
)


class ToolSelectionCorpusError(ValueError):
    """The tool-selection corpus document is malformed or incomplete."""


def load_corpus(path: Path | None = None) -> dict[str, Any]:
    """Load and validate the versioned tool-selection corpus."""
    target = path or FIXTURE_PATH
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ToolSelectionCorpusError(f"cannot read corpus at {target}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ToolSelectionCorpusError(f"corpus JSON is invalid: {exc}") from exc
    return validate_corpus(payload, repo_root=_REPO_ROOT)


def validate_corpus(value: object, *, repo_root: Path | None = None) -> dict[str, Any]:
    """Validate a tool-selection corpus document; unknown fields fail."""
    root = repo_root or _REPO_ROOT
    document = _mapping(value, "corpus document")
    _exact_keys(document, _DOCUMENT_KEYS, "corpus document")

    if document["contract"] != CONTRACT:
        raise ToolSelectionCorpusError(f"unsupported contract {document['contract']!r}")
    if document["schema_version"] != SCHEMA_VERSION:
        raise ToolSelectionCorpusError(
            f"unsupported schema_version {document['schema_version']!r}; expected {SCHEMA_VERSION}"
        )
    _nonempty_str(document["corpus_revision"], "corpus_revision")
    _nonempty_str(document["rubric"], "rubric")

    repository = _mapping(document["repository"], "repository")
    _exact_keys(repository, _REPOSITORY_KEYS, "repository")
    for key in ("name", "url", "revision"):
        _nonempty_str(repository[key], f"repository.{key}")

    tool_catalog = _mapping(document["tool_catalog"], "tool_catalog")
    _exact_keys(tool_catalog, _TOOL_CATALOG_KEYS, "tool_catalog")
    if tool_catalog["contract"] != "io.murlocs.agent-inventory":
        raise ToolSelectionCorpusError("tool_catalog.contract must be io.murlocs.agent-inventory")
    if tool_catalog["schema_version"] != 1:
        raise ToolSelectionCorpusError("tool_catalog.schema_version must be 1")
    catalog_path = _nonempty_str(tool_catalog["path"], "tool_catalog.path")
    _nonempty_str(tool_catalog["revision"], "tool_catalog.revision")

    model = _mapping(document["model"], "model")
    _exact_keys(model, _VERSIONED_PIN_KEYS, "model")
    for key in ("id", "revision", "pin_policy"):
        _nonempty_str(model[key], f"model.{key}")

    environment = _mapping(document["agent_environment"], "agent_environment")
    _exact_keys(environment, _VERSIONED_PIN_KEYS, "agent_environment")
    for key in ("id", "revision", "pin_policy"):
        _nonempty_str(environment[key], f"agent_environment.{key}")

    categories = _string_list(document["categories"], "categories")
    if set(categories) != REQUIRED_CATEGORIES:
        missing = ", ".join(sorted(REQUIRED_CATEGORIES - set(categories)))
        extra = ", ".join(sorted(set(categories) - REQUIRED_CATEGORIES))
        detail = []
        if missing:
            detail.append(f"missing {missing}")
        if extra:
            detail.append(f"unknown {extra}")
        raise ToolSelectionCorpusError(
            "categories must match the required set: " + "; ".join(detail)
        )

    notes = document["notes"]
    if (
        not isinstance(notes, list)
        or not notes
        or not all(isinstance(item, str) and item for item in notes)
    ):
        raise ToolSelectionCorpusError("notes must be a nonempty list of strings")

    allowed_tools = _inventory_tool_names(root / catalog_path) | {NONE_TOOL}
    prompts = _list(document["prompts"], "prompts")
    if not PROMPT_MIN <= len(prompts) <= PROMPT_MAX:
        raise ToolSelectionCorpusError(
            f"corpus must contain {PROMPT_MIN}–{PROMPT_MAX} prompts; found {len(prompts)}"
        )

    seen_ids: set[str] = set()
    seen_categories: set[str] = set()
    for index, item in enumerate(prompts):
        prompt = _validate_prompt(item, allowed_tools=allowed_tools, index=index)
        if prompt["id"] in seen_ids:
            raise ToolSelectionCorpusError(f"duplicate prompt id {prompt['id']!r}")
        seen_ids.add(prompt["id"])
        seen_categories.add(prompt["category"])

    missing_categories = REQUIRED_CATEGORIES - seen_categories
    if missing_categories:
        raise ToolSelectionCorpusError(
            "prompts omit required categories: " + ", ".join(sorted(missing_categories))
        )

    rubric_path = root / document["rubric"]
    if not rubric_path.is_file():
        raise ToolSelectionCorpusError(f"rubric path does not exist: {document['rubric']}")

    return document


def score_first_tool(
    *,
    expectation_kind: str,
    expected_first_tool: str,
    acceptable_alternatives: Sequence[str],
    selected_first_tool: str,
) -> str:
    """Assign a rubric label for a recorded first tool selection.

    This helper encodes the #124 rubric for unit tests and for the future #125
    ingestion harness. It does not load evidence or invoke a model.
    """
    if expectation_kind not in EXPECTATION_KINDS:
        raise ToolSelectionCorpusError(f"unknown expectation_kind {expectation_kind!r}")
    if not isinstance(selected_first_tool, str) or not selected_first_tool:
        raise ToolSelectionCorpusError("selected_first_tool must be a nonempty string")

    allowed = {expected_first_tool, *acceptable_alternatives}
    if expectation_kind == "none":
        if selected_first_tool == NONE_TOOL:
            return "correct_first_tool"
        return "unnecessary_call"
    if expectation_kind == "ambiguous":
        if selected_first_tool in allowed:
            return "ambiguous_prompt"
        return "missed_call"
    # fixed
    if selected_first_tool == expected_first_tool:
        return "correct_first_tool"
    if selected_first_tool in acceptable_alternatives:
        return "acceptable_alternative"
    return "missed_call"


def _inventory_tool_names(path: Path) -> frozenset[str]:
    if not path.is_file():
        raise ToolSelectionCorpusError(f"tool catalog fixture missing: {path}")
    try:
        inventory = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ToolSelectionCorpusError(f"tool catalog JSON is invalid: {exc}") from exc
    if not isinstance(inventory, Mapping):
        raise ToolSelectionCorpusError("tool catalog must be a JSON object")
    registry = inventory.get("registry")
    if not isinstance(registry, Mapping):
        raise ToolSelectionCorpusError("tool catalog registry missing")
    commands = registry.get("commands")
    if not isinstance(commands, list):
        raise ToolSelectionCorpusError("tool catalog commands missing")
    names: set[str] = set()
    for entry in commands:
        if not isinstance(entry, Mapping):
            continue
        if entry.get("audience") != "agent":
            continue
        name = entry.get("name")
        if isinstance(name, str) and name:
            names.add(name)
    if not names:
        raise ToolSelectionCorpusError("tool catalog lists no agent-audience commands")
    return frozenset(names)


def _validate_prompt(
    value: object,
    *,
    allowed_tools: frozenset[str],
    index: int,
) -> dict[str, Any]:
    item = _mapping(value, f"prompts[{index}]")
    _exact_keys(item, _PROMPT_KEYS, f"prompts[{index}]")
    prompt_id = _nonempty_str(item["id"], f"prompts[{index}].id")
    category = _nonempty_str(item["category"], f"prompts[{index}].category")
    if category not in REQUIRED_CATEGORIES:
        raise ToolSelectionCorpusError(f"prompts[{index}] has unknown category {category!r}")
    prompt_text = _nonempty_str(item["prompt"], f"prompts[{index}].prompt")
    if len(prompt_text) < 12:
        raise ToolSelectionCorpusError(f"prompts[{index}] prompt text is too short")

    kind = _nonempty_str(item["expectation_kind"], f"prompts[{index}].expectation_kind")
    if kind not in EXPECTATION_KINDS:
        raise ToolSelectionCorpusError(f"prompts[{index}] has unknown expectation_kind {kind!r}")

    expected = _nonempty_str(item["expected_first_tool"], f"prompts[{index}].expected_first_tool")
    if expected not in allowed_tools:
        raise ToolSelectionCorpusError(
            f"prompts[{index}] expected_first_tool {expected!r} is not in the pinned tool catalog"
        )
    alternatives = _string_list(
        item["acceptable_alternatives"],
        f"prompts[{index}].acceptable_alternatives",
    )
    for alt in alternatives:
        if alt not in allowed_tools:
            raise ToolSelectionCorpusError(
                f"prompts[{index}] acceptable alternative {alt!r} is not in the pinned tool catalog"
            )
        if alt == expected:
            raise ToolSelectionCorpusError(
                f"prompts[{index}] acceptable_alternatives must not repeat expected_first_tool"
            )

    notes = item["notes"]
    if notes is not None and (not isinstance(notes, str) or not notes):
        raise ToolSelectionCorpusError(f"prompts[{index}].notes must be a string or null")

    if kind == "none":
        if expected != NONE_TOOL:
            raise ToolSelectionCorpusError(
                f"prompts[{index}] expectation_kind none requires expected_first_tool 'none'"
            )
        if category != "no_murlocs_call":
            raise ToolSelectionCorpusError(
                f"prompts[{index}] expectation_kind none must use category no_murlocs_call"
            )
        if alternatives:
            raise ToolSelectionCorpusError(
                f"prompts[{index}] expectation_kind none must not list acceptable_alternatives"
            )
    elif kind == "fixed":
        if expected == NONE_TOOL:
            raise ToolSelectionCorpusError(
                f"prompts[{index}] expectation_kind fixed cannot use expected_first_tool 'none'"
            )
        if category == "no_murlocs_call":
            raise ToolSelectionCorpusError(
                f"prompts[{index}] no_murlocs_call prompts must use expectation_kind none"
            )
    elif kind == "ambiguous":
        if not alternatives:
            raise ToolSelectionCorpusError(
                f"prompts[{index}] ambiguous prompts require nonempty acceptable_alternatives"
            )

    return {
        "id": prompt_id,
        "category": category,
        "prompt": prompt_text,
        "expectation_kind": kind,
        "expected_first_tool": expected,
        "acceptable_alternatives": alternatives,
        "notes": notes,
    }


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolSelectionCorpusError(f"{label} must be an object")
    return value


def _list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ToolSelectionCorpusError(f"{label} must be an array")
    return value


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    keys = frozenset(value)
    if keys != expected:
        raise ToolSelectionCorpusError(f"{label} has unknown or missing fields")


def _nonempty_str(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ToolSelectionCorpusError(f"{label} must be a nonempty string")
    return value


def _string_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ToolSelectionCorpusError(f"{label} must be a list of nonempty strings")
    if len(set(value)) != len(value):
        raise ToolSelectionCorpusError(f"{label} must not contain duplicates")
    return list(value)


def main(argv: list[str] | None = None) -> int:
    """CLI entry: validate the checked-in corpus and print a compact summary."""
    args = list(sys.argv[1:] if argv is None else argv)
    path = Path(args[0]) if args else FIXTURE_PATH
    try:
        corpus = load_corpus(path)
    except ToolSelectionCorpusError as exc:
        print(f"tool-selection corpus invalid: {exc}", file=sys.stderr)
        return 1
    categories = sorted({item["category"] for item in corpus["prompts"]})
    print(
        f"{corpus['contract']}@{corpus['corpus_revision']}: "
        f"{len(corpus['prompts'])} prompts covering {', '.join(categories)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
