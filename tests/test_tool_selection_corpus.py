from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from murlocs.tool_selection import (
    CONTRACT,
    FIXTURE_PATH,
    NONE_TOOL,
    PROMPT_MAX,
    PROMPT_MIN,
    REQUIRED_CATEGORIES,
    SCHEMA_VERSION,
    ToolSelectionCorpusError,
    load_corpus,
    score_first_tool,
    validate_corpus,
)

pytestmark = pytest.mark.issue(124)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _corpus() -> dict:
    return load_corpus()


@pytest.mark.issue(124)
def test_corpus_loads_and_matches_contract_pins():
    corpus = _corpus()

    assert corpus["contract"] == CONTRACT
    assert corpus["schema_version"] == SCHEMA_VERSION
    assert corpus["corpus_revision"]
    assert corpus["repository"]["revision"]
    assert corpus["tool_catalog"]["path"] == "tests/fixtures/agent-inventory/v1/inventory.json"
    assert corpus["tool_catalog"]["revision"]
    assert corpus["model"]["id"] and corpus["model"]["revision"]
    assert corpus["agent_environment"]["id"] and corpus["agent_environment"]["revision"]
    assert (REPO_ROOT / corpus["rubric"]).is_file()
    assert FIXTURE_PATH.is_file()


@pytest.mark.issue(124)
def test_corpus_has_twenty_to_thirty_prompts_covering_required_categories():
    corpus = _corpus()
    prompts = corpus["prompts"]

    assert PROMPT_MIN <= len(prompts) <= PROMPT_MAX
    assert set(corpus["categories"]) == REQUIRED_CATEGORIES
    assert {item["category"] for item in prompts} == REQUIRED_CATEGORIES

    for item in prompts:
        assert item["expected_first_tool"]
        assert item["expectation_kind"] in {"fixed", "none", "ambiguous"}
        assert isinstance(item["acceptable_alternatives"], list)
        assert item["prompt"].strip()


@pytest.mark.issue(124)
def test_expected_answers_are_present_before_any_candidate_scoring():
    """Ground truth is frozen on every prompt; scoring only consumes those fields."""
    corpus = _corpus()
    for item in corpus["prompts"]:
        assert "expected_first_tool" in item
        assert "acceptable_alternatives" in item
        assert "expectation_kind" in item
        # Candidate description text is intentionally absent from the corpus.
        assert "candidate_description" not in item
        assert "tool_description" not in item


@pytest.mark.issue(124)
def test_no_murlocs_call_prompts_expect_none():
    corpus = _corpus()
    none_prompts = [item for item in corpus["prompts"] if item["category"] == "no_murlocs_call"]
    assert none_prompts
    for item in none_prompts:
        assert item["expectation_kind"] == "none"
        assert item["expected_first_tool"] == NONE_TOOL
        assert item["acceptable_alternatives"] == []


@pytest.mark.issue(124)
def test_rubric_labels_distinguish_required_outcomes():
    assert (
        score_first_tool(
            expectation_kind="fixed",
            expected_first_tool="orient",
            acceptable_alternatives=["explain"],
            selected_first_tool="orient",
        )
        == "correct_first_tool"
    )
    assert (
        score_first_tool(
            expectation_kind="fixed",
            expected_first_tool="orient",
            acceptable_alternatives=["explain"],
            selected_first_tool="explain",
        )
        == "acceptable_alternative"
    )
    assert (
        score_first_tool(
            expectation_kind="none",
            expected_first_tool="none",
            acceptable_alternatives=[],
            selected_first_tool="check",
        )
        == "unnecessary_call"
    )
    assert (
        score_first_tool(
            expectation_kind="fixed",
            expected_first_tool="finish",
            acceptable_alternatives=["check"],
            selected_first_tool="none",
        )
        == "missed_call"
    )
    assert (
        score_first_tool(
            expectation_kind="ambiguous",
            expected_first_tool="orient",
            acceptable_alternatives=["explain", "none"],
            selected_first_tool="explain",
        )
        == "ambiguous_prompt"
    )


@pytest.mark.issue(124)
def test_unknown_corpus_fields_fail_visibly():
    corpus = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    corpus["unexpected"] = True

    with pytest.raises(ToolSelectionCorpusError, match="unknown or missing fields"):
        validate_corpus(corpus, repo_root=REPO_ROOT)


@pytest.mark.issue(124)
def test_unknown_prompt_fields_and_tools_fail_visibly():
    corpus = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    broken = copy.deepcopy(corpus)
    broken["prompts"][0]["candidate_description"] = "should not appear"

    with pytest.raises(ToolSelectionCorpusError, match="unknown or missing fields"):
        validate_corpus(broken, repo_root=REPO_ROOT)

    broken = copy.deepcopy(corpus)
    broken["prompts"][0]["expected_first_tool"] = "not-a-real-tool"
    with pytest.raises(ToolSelectionCorpusError, match="not in the pinned tool catalog"):
        validate_corpus(broken, repo_root=REPO_ROOT)


@pytest.mark.issue(124)
def test_expected_tools_are_drawn_from_agent_inventory():
    inventory = json.loads(
        (REPO_ROOT / "tests/fixtures/agent-inventory/v1/inventory.json").read_text(encoding="utf-8")
    )
    allowed = {
        entry["name"] for entry in inventory["registry"]["commands"] if entry["audience"] == "agent"
    } | {NONE_TOOL}
    corpus = _corpus()
    for item in corpus["prompts"]:
        assert item["expected_first_tool"] in allowed
        for alt in item["acceptable_alternatives"]:
            assert alt in allowed
