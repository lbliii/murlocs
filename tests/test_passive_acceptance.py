from __future__ import annotations

import json
from pathlib import Path

import pytest

from murlocs.passive_acceptance import (
    CONTRACT,
    PassiveAcceptanceError,
    private_expectation_commitment,
    validate_observations,
)

FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "passive-agent-acceptance"
    / "v1"
    / "example-observations.json"
)


def observations() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_versioned_example_covers_every_required_journey_and_replays_deterministically():
    report = validate_observations(observations())

    assert report == {
        "contract": CONTRACT,
        "schema_version": 1,
        "passed": True,
        "scenarios": [
            "authority-required-exception",
            "cross-scope-global-guidance",
            "generated-drift",
            "ordinary-code",
            "semantic-local-guidance",
        ],
        "failures": [],
    }


def test_expectation_commitment_is_stable_and_retains_no_task_text():
    assert private_expectation_commitment("private expectation") == (
        "61307ffd341cb17c1bec0e17a9c9184fffbed6edb818ba1208d6b29ac54e721b"
    )


def test_ordinary_journey_requires_silent_outcomes():
    evidence = observations()
    evidence["observations"][0]["outcomes"][0]["silent"] = False

    with pytest.raises(PassiveAcceptanceError, match="must have silent outcomes"):
        validate_observations(evidence)


@pytest.mark.parametrize("forbidden", ["prompt", "task", "transcript", "command"])
def test_observation_rejects_prompt_and_tool_argument_content(forbidden: str):
    evidence = observations()
    first = evidence["observations"][0]
    first[forbidden] = "sensitive text"

    with pytest.raises(PassiveAcceptanceError, match="unknown or missing fields|must not retain"):
        validate_observations(evidence)


def test_failure_records_are_attributed_to_one_boundary_without_falsely_passing():
    evidence = observations()
    first = evidence["observations"][0]
    first["status"] = "fail"
    first["failure"] = {"cause": "host_capability", "observation": "missing_lifecycle_event"}

    report = validate_observations(evidence)

    assert report["passed"] is False
    assert report["failures"] == [{"scenario": "ordinary-code", "cause": "host_capability"}]
