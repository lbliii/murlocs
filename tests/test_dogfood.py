from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from murlocs.dogfood import (
    CONTRACT,
    DogfoodError,
    private_expectation_commitment,
    validate_dogfood_sheet,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "self-hosting-dogfood" / "v1"
SHEET = FIXTURE_DIR / "tranche-2026-08-12.json"
PROTOCOL = Path(__file__).parents[1] / "docs" / "self-hosting-dogfood.md"
REPORT = Path(__file__).parents[1] / "docs" / "pilots" / "self-hosting-dogfood-2026-08-12.md"


def tranche_sheet() -> dict[str, object]:
    return json.loads(SHEET.read_text(encoding="utf-8"))


@pytest.mark.issue(97)
def test_tranche_sheet_validates_as_complete():
    report = validate_dogfood_sheet(tranche_sheet())

    assert report["contract"] == CONTRACT
    assert report["tranche_id"] == "murlocs-self-host-2026-08-12"
    assert report["tranche_status"] == "complete"
    assert report["task_count"] >= 5
    assert report["fresh_sessions"] >= 5
    assert report["hooks_retained"] is True
    assert report["proceed_to_multi_repo_pilot"] is True
    assert report["telemetry_required"] is False
    assert report["interviews_complete"] is True
    assert "useful-silence" in report["classifications"]
    assert "useful-intervention" in report["classifications"]


@pytest.mark.issue(97)
def test_protocol_and_report_cover_acceptance_boundary():
    protocol = PROTOCOL.read_text(encoding="utf-8")
    report = REPORT.read_text(encoding="utf-8")

    assert "fresh session" in protocol.lower() or "fresh-session" in protocol.lower()
    assert "expectation commitment" in protocol.lower() or "expectation_commitment" in protocol
    assert "useful silence" in protocol.lower() or "useful-silence" in protocol
    assert "proceed" in report.lower()
    assert "multi-repository" in report.lower() or "multi-repo" in report.lower()
    assert "no prompt" in report.lower() or "privacy" in report.lower()


@pytest.mark.issue(97)
def test_sheet_rejects_prompt_retention_and_short_tranches():
    sheet = tranche_sheet()
    sheet["tasks"][0]["prompt"] = "do-not-store"
    with pytest.raises(DogfoodError, match="prompt or transcript"):
        validate_dogfood_sheet(sheet)

    sheet = tranche_sheet()
    sheet["tasks"] = sheet["tasks"][:3]
    with pytest.raises(DogfoodError, match="five"):
        validate_dogfood_sheet(sheet)


@pytest.mark.issue(97)
def test_complete_tranche_requires_hooks_and_journeys():
    sheet = tranche_sheet()
    sheet["tranche"]["hooks_retained"] = False
    with pytest.raises(DogfoodError, match="hooks_retained"):
        validate_dogfood_sheet(sheet)

    sheet = tranche_sheet()
    sheet["acceptance"]["authority_required_journey_observed"] = False
    with pytest.raises(DogfoodError, match="authority-required"):
        validate_dogfood_sheet(sheet)


@pytest.mark.issue(97)
def test_private_expectation_commitment_is_stable():
    digest = private_expectation_commitment("bounded private expectation text")
    assert digest == private_expectation_commitment("bounded private expectation text")
    assert len(digest) == 64

    mutated = copy.deepcopy(tranche_sheet())
    mutated["tasks"][0]["murlocs_mentioned_in_prompt"] = True
    with pytest.raises(DogfoodError, match="must be false"):
        validate_dogfood_sheet(mutated)
