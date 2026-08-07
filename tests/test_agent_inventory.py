from __future__ import annotations

import json

from murlocs.agent_inventory import (
    FIXTURE_PATH,
    INVENTORY_CONTRACT,
    INVENTORY_SCHEMA_VERSION,
    build_agent_inventory,
    render_agent_inventory,
)
from murlocs.task_commands import TASK_CONTRACT, TASK_SCHEMA_VERSION


def test_agent_inventory_matches_checked_in_fixture():
    expected = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    actual = build_agent_inventory()
    assert actual == expected


def test_agent_inventory_fixture_is_canonical_json():
    text = FIXTURE_PATH.read_text(encoding="utf-8")
    assert text == render_agent_inventory(json.loads(text))


def test_agent_inventory_covers_required_surfaces():
    inventory = build_agent_inventory()
    assert inventory["contract"] == INVENTORY_CONTRACT
    assert inventory["schema_version"] == INVENTORY_SCHEMA_VERSION

    commands = inventory["registry"]["commands"]
    assert commands, "registry must list CLI commands"

    agent_commands = [entry["name"] for entry in commands if entry["audience"] == "agent"]
    assert "check" in agent_commands
    assert "orient" in agent_commands
    assert "finish" in agent_commands

    composite = [entry for entry in commands if entry["kind"] == "composite"]
    assert {entry["name"] for entry in composite} == {
        "orient",
        "review-changes",
        "finish",
    }
    for entry in composite:
        assert entry["stable"]["contract"] == {
            "name": TASK_CONTRACT,
            "schema_version": TASK_SCHEMA_VERSION,
        }

    assert inventory["skills"], "skills surface must be inventoried"
    assert inventory["generated_guidance"], "generated guidance must be inventoried"
    assert inventory["documentation"], "task-command documentation must be inventoried"
    assert inventory["registry"]["llms_txt"]["present"] is True

    analysis = inventory["analysis"]
    assert "duplicates" in analysis
    assert "conflicts" in analysis
    assert "product_internal" in analysis
    assert "missing_trigger_language" in analysis
    assert analysis["missing_trigger_language"], (
        "agent-facing commands currently lack explicit trigger language (#121 follow-up)"
    )
