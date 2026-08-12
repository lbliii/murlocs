from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from murlocs.passive_loop_pilot import (
    CONTRACT,
    PassiveLoopPilotError,
    validate_pilot_sheet,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "passive-loop-pilot" / "v1"
EXAMPLE = FIXTURE_DIR / "example-sheet.json"
PROTOCOL = Path(__file__).parents[1] / "docs" / "passive-loop-pilot.md"
REPORT = Path(__file__).parents[1] / "docs" / "pilots" / "passive-loop-multi-repo.md"


def example_sheet() -> dict[str, object]:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


@pytest.mark.issue(68)
def test_example_sheet_validates_and_stays_harness_only():
    report = validate_pilot_sheet(example_sheet())

    assert report == {
        "contract": CONTRACT,
        "schema_version": 1,
        "pilot_id": "passive-loop-harness-rehearsal",
        "pilot_status": "harness-only",
        "repository_count": 2,
        "repository_ids": ["fixture-compact-mature", "fixture-broad-bootstrap"],
        "diversity_axes": [
            "guidance_maturity",
            "primary_agent_workflow",
            "scope_topology",
            "size_class",
        ],
        "finding_kinds": [
            "missed-opportunity",
            "noise",
            "useful-intervention",
            "useful-silence",
        ],
        "executed_repositories": 0,
        "simulated_repositories": 2,
        "live_execution_complete": False,
        "telemetry_required": False,
        "recommendation_buckets": {
            "graduate": ["no-prompt-acceptance-harness"],
            "change": ["false-positive-routing-thresholds"],
            "remain_experimental": ["longitudinal-operator-feedback"],
            "remove": [],
        },
    }


@pytest.mark.issue(68)
def test_protocol_and_report_separate_executed_from_planned_live_work():
    protocol = PROTOCOL.read_text(encoding="utf-8")
    report = REPORT.read_text(encoding="utf-8")

    assert "Baseline before activation" in protocol
    assert "Review cadence and rollback" in protocol
    assert "repository-governed" in protocol
    assert "Live multi-repository longitudinal execution is not complete" in report
    assert "Planned follow-up" in report
    assert "does not claim" in report.lower() or "not claim" in report.lower()


@pytest.mark.issue(68)
def test_sheet_requires_two_materially_diverse_repositories():
    sheet = example_sheet()
    twin = copy.deepcopy(sheet["repositories"][0])
    twin["id"] = "fixture-clone"
    twin["label"] = "clone"
    sheet["repositories"] = [sheet["repositories"][0], twin]

    with pytest.raises(PassiveLoopPilotError, match="differ across size, topology, maturity"):
        validate_pilot_sheet(sheet)


@pytest.mark.issue(68)
def test_sheet_requires_each_finding_kind_and_rejects_sensitive_keys():
    sheet = example_sheet()
    sheet["repositories"][1]["findings"] = [
        {
            "kind": "useful-silence",
            "example_id": "duplicate-kind",
            "note": "Missing-other-kinds",
        }
    ]
    with pytest.raises(PassiveLoopPilotError, match="each kind"):
        validate_pilot_sheet(sheet)

    sheet = example_sheet()
    sheet["repositories"][0]["transcript"] = "sensitive"
    with pytest.raises(PassiveLoopPilotError, match="prompt or transcript"):
        validate_pilot_sheet(sheet)


@pytest.mark.issue(68)
def test_live_completion_cannot_be_claimed_for_simulated_rows():
    sheet = example_sheet()
    sheet["acceptance"]["live_execution_complete"] = True

    with pytest.raises(PassiveLoopPilotError, match="live_execution_complete cannot be true"):
        validate_pilot_sheet(sheet)


@pytest.mark.issue(68)
def test_harness_only_pilot_forbids_telemetry_and_complete_status():
    sheet = example_sheet()
    sheet["pilot"]["telemetry_required"] = True
    with pytest.raises(PassiveLoopPilotError, match="must not require telemetry"):
        validate_pilot_sheet(sheet)

    sheet = example_sheet()
    sheet["pilot"]["status"] = "complete"
    with pytest.raises(PassiveLoopPilotError, match="complete pilot status requires"):
        validate_pilot_sheet(sheet)


@pytest.mark.issue(68)
def test_baselines_metrics_rollback_and_recommendation_buckets_are_required():
    sheet = example_sheet()
    del sheet["repositories"][0]["baseline"]["human_interventions"]
    with pytest.raises(PassiveLoopPilotError, match="baseline has unknown or missing fields"):
        validate_pilot_sheet(sheet)

    sheet = example_sheet()
    del sheet["repositories"][0]["metrics"]["authority_escalation_rate"]
    with pytest.raises(PassiveLoopPilotError, match="metrics has unknown or missing fields"):
        validate_pilot_sheet(sheet)

    sheet = example_sheet()
    sheet["repositories"][0]["rollback"]["exercised"] = True
    sheet["repositories"][0]["rollback"]["safe"] = False
    with pytest.raises(PassiveLoopPilotError, match="exercised rollback must be recorded as safe"):
        validate_pilot_sheet(sheet)

    sheet = example_sheet()
    del sheet["recommendations"]["remove"]
    with pytest.raises(
        PassiveLoopPilotError, match="recommendations has unknown or missing fields"
    ):
        validate_pilot_sheet(sheet)
