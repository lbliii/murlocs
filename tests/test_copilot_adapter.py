from __future__ import annotations

import json
from pathlib import Path

from test_adapter_conformance import FixtureAdapter

from murlocs.adapter_conformance import (
    ADAPTER_CONTRACT,
    REQUIRED_CAPABILITIES,
    run_adapter_conformance,
)
from murlocs.copilot_adapter import ADAPTER_ID, descriptor, handle


class CopilotConformanceDriver(FixtureAdapter):
    """Drive the portable suite through Copilot's declared adapter identity."""

    def descriptor(self):
        return descriptor()

    def invoke(self, request, context):
        observed = super().invoke(request, context)
        return _replace_adapter_id(observed)


def _replace_adapter_id(value):
    if isinstance(value, dict):
        return {key: _replace_adapter_id(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_adapter_id(item) for item in value]
    return ADAPTER_ID if value == "fixture-adapter" else value


def _repo(tmp_path: Path) -> Path:
    (tmp_path / ".murlocs").mkdir()
    (tmp_path / ".murlocs" / "manifest.toml").write_text(
        "[network]\nname = 'fixture'\nmax_active_bytes = 12000\n", encoding="utf-8"
    )
    return tmp_path


def test_descriptor_honestly_declares_copilot_lifecycle_boundaries():
    value = descriptor()
    assert value["contract"] == ADAPTER_CONTRACT
    assert value["adapter_id"] == ADAPTER_ID
    assert value["required_capabilities"] == list(REQUIRED_CAPABILITIES)
    assert value["events"]["task-start"] == "host-enforced"
    assert value["events"]["pre-commit"] == "host-enforced"


def test_copilot_contract_identity_passes_the_portable_conformance_suite(tmp_path: Path):
    report = run_adapter_conformance(CopilotConformanceDriver(), temporary_parent=tmp_path)
    assert report["passed"] is True, report
    assert report["adapter_id"] == ADAPTER_ID


def test_absent_manifest_is_a_silent_no_op(tmp_path: Path):
    assert handle("task-start", {"cwd": str(tmp_path), "sessionId": "one"}) == {}


def test_task_start_does_not_need_an_agent_prompt(tmp_path: Path):
    root = _repo(tmp_path)
    response = handle("task-start", {"cwd": str(root), "sessionId": "one"})
    assert set(response) <= {"additionalContext"}


def test_missing_edit_path_becomes_a_structured_remediation_packet(tmp_path: Path):
    root = _repo(tmp_path)
    response = handle("post-edit", {"cwd": str(root), "sessionId": "one", "toolArgs": {}})
    packet = json.loads(response["additionalContext"])
    assert packet["outcomes"][0]["code"] == "MURLOCS_ACTIVATION_UNAVAILABLE"
    assert packet["outcomes"][0]["next_actions"][0]["authority"] == "integration"


def test_pre_completion_blocks_when_fresh_impact_paths_are_unavailable(tmp_path: Path):
    root = _repo(tmp_path)
    response = handle("pre-completion", {"cwd": str(root), "sessionId": "one"})
    assert response["decision"] == "block"
    assert "MURLOCS_ACTIVATION_UNAVAILABLE" in response["reason"]
