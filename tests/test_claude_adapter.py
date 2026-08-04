from __future__ import annotations

import json
import subprocess
from pathlib import Path

from test_adapter_conformance import FixtureAdapter

from murlocs.adapter_conformance import (
    ADAPTER_CONTRACT,
    REQUIRED_CAPABILITIES,
    run_adapter_conformance,
)
from murlocs.claude_adapter import ADAPTER_ID, _changed_paths, descriptor, handle


class ClaudeContractDriver(FixtureAdapter):
    """Run the portable suite under Claude Code's independent identity.

    The suite models the host-owned trusted context.  Transport-level behavior
    is separately tested below through the production ``handle`` bridge.
    """

    def descriptor(self):
        return descriptor()

    def invoke(self, request, context):
        return _replace_adapter_id(super().invoke(request, context))


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


def _silent_result(**_kwargs):
    return {"outcome": {"silent": True}}


def test_descriptor_honestly_declares_claude_lifecycle_boundaries():
    value = descriptor()
    assert value["contract"] == ADAPTER_CONTRACT
    assert value["adapter_id"] == ADAPTER_ID
    assert value["required_capabilities"] == list(REQUIRED_CAPABILITIES)
    assert value["events"]["task-start"] == "host-enforced"
    assert value["events"]["prospective-impact"] == "prompt-mediated"
    assert value["events"]["pre-completion"] == "host-enforced"


def test_claude_contract_identity_passes_the_portable_conformance_suite(tmp_path: Path):
    report = run_adapter_conformance(ClaudeContractDriver(), temporary_parent=tmp_path)
    assert report["passed"] is True, report
    assert report["adapter_id"] == ADAPTER_ID


def test_absent_manifest_is_a_silent_no_op(tmp_path: Path):
    assert handle("task-start", {"cwd": str(tmp_path), "session_id": "one"}) == {}


def test_post_edit_production_bridge_normalizes_an_absolute_path(tmp_path: Path, monkeypatch):
    root = _repo(tmp_path)
    target = root / "src" / "widget.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    observed: list[tuple[str, object]] = []

    def check(**kwargs):
        observed.append(("check", kwargs["repo"]))
        return _silent_result()

    def impact(**kwargs):
        observed.append(("impact", kwargs["path"]))
        return _silent_result()

    monkeypatch.setattr("murlocs.claude_adapter.check_command", check)
    monkeypatch.setattr("murlocs.claude_adapter.impact_command", impact)

    response = handle(
        "post-edit",
        {"cwd": str(root), "session_id": "one", "tool_input": {"file_path": str(target)}},
    )
    assert response == {}
    assert observed == [("check", str(root)), ("impact", ["src/widget.py"])]


def test_external_absolute_path_never_reaches_impact(tmp_path: Path):
    root = _repo(tmp_path)
    response = handle(
        "post-edit",
        {"cwd": str(root), "session_id": "one", "tool_input": {"file_path": "/tmp/outside.py"}},
    )
    packet = json.loads(response["hookSpecificOutput"]["additionalContext"])
    assert packet["outcomes"][0]["code"] == "MURLOCS_ACTIVATION_UNAVAILABLE"


def test_pre_completion_includes_untracked_paths(tmp_path: Path, monkeypatch):
    root = _repo(tmp_path)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True
    )
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "commit.gpgSign=false", "commit", "-qm", "fixture"],
        check=True,
    )
    (root / "untracked.py").write_text("value = 1\n", encoding="utf-8")
    assert _changed_paths(root) == ["untracked.py"]
    observed: list[list[str]] = []

    monkeypatch.setattr("murlocs.claude_adapter.check_command", _silent_result)

    def impact(**kwargs):
        observed.append(kwargs["path"])
        return _silent_result()

    monkeypatch.setattr("murlocs.claude_adapter.impact_command", impact)
    assert handle("pre-completion", {"cwd": str(root), "session_id": "one"}) == {}
    assert observed == [["untracked.py"]]


def test_pre_commit_only_gates_a_real_git_commit(tmp_path: Path, monkeypatch):
    root = _repo(tmp_path)
    calls: list[object] = []

    def hook(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("a non-commit command must not run the gate")

    monkeypatch.setattr("murlocs.claude_adapter.run_hook", hook)
    assert handle(
        "pre-commit",
        {"cwd": str(root), "session_id": "one", "tool_input": {"command": "echo git commit"}},
    ) == {}
    assert calls == []


def test_prospective_impact_is_context_not_a_host_policy_decision(tmp_path: Path, monkeypatch):
    root = _repo(tmp_path)
    target = root / "widget.py"
    target.write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setattr("murlocs.claude_adapter.impact_command", _silent_result)
    monkeypatch.setattr(
        "murlocs.claude_adapter._packet",
        lambda _outcomes: '{"outcomes":[{"code":"MURLOCS_ACTION_REQUIRED"}]}',
    )
    response = handle(
        "prospective-impact",
        {"cwd": str(root), "session_id": "one", "tool_input": {"file_path": str(target)}},
    )
    nested = response["hookSpecificOutput"]
    assert nested["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in nested
