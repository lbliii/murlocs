from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import murlocs.claude_adapter as adapter
from murlocs.adapter_conformance import (
    ADAPTER_CONTRACT,
    REQUIRED_CAPABILITIES,
    run_adapter_conformance,
)


def _repo(tmp_path: Path) -> Path:
    (tmp_path / ".murlocs").mkdir(parents=True)
    (tmp_path / ".murlocs" / "manifest.toml").write_text("fixture\n", encoding="utf-8")
    return tmp_path


def _git(root: Path, *arguments: str) -> None:
    config = ["-c", "commit.gpgsign=false"] if arguments[0] == "commit" else []
    subprocess.run(
        ["git", "-C", str(root), *config, *arguments],
        check=True,
        capture_output=True,
    )


def _outcome(
    operation: str,
    *,
    code: str = "MURLOCS_OUTCOME_PASS",
    silent: bool = True,
    blocking: bool = False,
) -> dict[str, object]:
    return {
        "outcome": {
            "code": code,
            "resolution_class": "pass" if silent else "agent_action",
            "summary": code,
            "next_actions": [],
            "source": {"operation": operation},
            "silent": silent,
            "blocking": blocking,
        }
    }


def test_descriptor_honestly_declares_claude_lifecycle_boundaries():
    value = adapter.descriptor()
    assert value["contract"] == ADAPTER_CONTRACT
    assert value["adapter_id"] == adapter.ADAPTER_ID
    assert value["required_capabilities"] == list(REQUIRED_CAPABILITIES)
    assert value["events"]["task-start"] == "host-enforced"
    assert value["events"]["prospective-impact"] == "prompt-mediated"
    assert value["events"]["pre-completion"] == "host-enforced"


def test_production_driver_passes_portable_conformance_suite(tmp_path: Path):
    report = run_adapter_conformance(adapter.ClaudeAdapterDriver(), temporary_parent=tmp_path)
    assert report["passed"] is True, report
    assert report["adapter_id"] == adapter.ADAPTER_ID


def test_production_bridge_exercises_every_configured_lifecycle_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = _repo(tmp_path)
    target = root / "src" / "app.py"
    calls: list[tuple[str, tuple[str, ...]]] = []

    def check_command(**_kwargs):
        calls.append(("check", ()))
        return _outcome("check")

    def impact_command(*, path, **_kwargs):
        calls.append(("impact", tuple(path)))
        return _outcome("impact")

    monkeypatch.setattr(adapter, "check_command", check_command)
    monkeypatch.setattr(adapter, "impact_command", impact_command)
    monkeypatch.setattr(
        adapter,
        "run_hook",
        lambda *_args, **_kwargs: SimpleNamespace(
            payload={"outcome": _outcome("check")["outcome"]}, exit_code=0
        ),
    )
    monkeypatch.setattr(adapter, "_changed_paths", lambda _root: ["src/app.py"])

    base = {"cwd": str(root), "session_id": "production-session"}
    assert adapter.handle("task-start", base) == {}
    assert adapter.handle(
        "prospective-impact", {**base, "tool_input": {"file_path": str(target)}}
    ) == {}
    assert adapter.handle(
        "post-edit", {**base, "tool_input": {"file_path": str(target)}}
    ) == {}
    assert adapter.handle(
        "pre-commit", {**base, "tool_input": {"command": "git  commit -m safe"}}
    ) == {}
    assert adapter.handle("pre-completion", base) == {}

    assert calls == [
        ("check", ()),
        ("impact", ("src/app.py",)),
        ("check", ()),
        ("impact", ("src/app.py",)),
        ("check", ()),
        ("impact", ("src/app.py",)),
    ]


def test_absent_manifest_is_a_silent_no_op(tmp_path: Path):
    assert adapter.handle("task-start", {"cwd": str(tmp_path), "session_id": "one"}) == {}


@pytest.mark.parametrize(
    ("field", "spelling"),
    [
        ("path", "src/../src/app.py"),
        ("file", "src/app.py"),
        ("filePath", "ABSOLUTE"),
        ("file_path", "src/app.py"),
    ],
)
def test_paths_normalize_confined_relative_and_absolute_targets(
    tmp_path: Path, field: str, spelling: str
):
    root = _repo(tmp_path)
    raw = str(root / "src/app.py") if spelling == "ABSOLUTE" else spelling
    assert adapter._paths(root, {field: raw}) == ["src/app.py"]


def test_paths_reject_parent_absolute_nul_and_symlink_escapes(tmp_path: Path):
    root = _repo(tmp_path / "repository")
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)

    assert adapter._paths(root, {"file_path": "../outside/file.py"}) == []
    assert adapter._paths(root, {"file_path": str(outside / "file.py")}) == []
    assert adapter._paths(root, {"file_path": "bad\0name.py"}) == []
    assert adapter._paths(root, {"file_path": "link/file.py"}) == []


def test_prospective_path_escape_is_visible_without_host_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = _repo(tmp_path / "repository")
    outside = tmp_path / "outside.py"
    monkeypatch.setattr(adapter, "_run", lambda *_args: pytest.fail("operation ran"))
    response = adapter.handle(
        "prospective-impact",
        {"cwd": str(root), "session_id": "one", "tool_input": {"file_path": str(outside)}},
    )
    nested = response["hookSpecificOutput"]
    assert nested["hookEventName"] == "PreToolUse"
    assert "outside the repository" in nested["additionalContext"]
    assert "permissionDecision" not in nested


def test_changed_paths_include_staged_unstaged_deleted_and_untracked_nul_names(tmp_path: Path):
    root = tmp_path
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "adapter@example.test")
    _git(root, "config", "user.name", "Adapter Test")
    (root / "modified.py").write_text("before\n", encoding="utf-8")
    (root / "deleted.py").write_text("delete\n", encoding="utf-8")
    _git(root, "add", "modified.py", "deleted.py")
    _git(root, "commit", "-qm", "base")

    (root / "modified.py").write_text("after\n", encoding="utf-8")
    (root / "deleted.py").unlink()
    staged = "staged\nname.py"
    untracked = "untracked\nname.py"
    (root / staged).write_text("staged\n", encoding="utf-8")
    (root / untracked).write_text("untracked\n", encoding="utf-8")
    _git(root, "add", staged)

    assert adapter._changed_paths(root) == ["deleted.py", "modified.py", staged, untracked]


def test_pre_completion_routes_newline_path_and_reports_active_stop_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = _repo(tmp_path)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "adapter@example.test")
    _git(root, "config", "user.name", "Adapter Test")
    _git(root, "add", ".murlocs/manifest.toml")
    _git(root, "commit", "-qm", "base")
    untracked = "new\nfile.py"
    (root / untracked).write_text("new\n", encoding="utf-8")
    observed: list[str] = []

    def run(_root, _event, paths, _correlation):
        observed.extend(paths)
        return [
            _outcome(
                "impact",
                code="MURLOCS_OUTCOME_DETERMINISTIC_REPAIR",
                silent=False,
                blocking=True,
            )["outcome"]
        ]

    monkeypatch.setattr(adapter, "_run", run)
    response = adapter.handle(
        "pre-completion",
        {"cwd": str(root), "session_id": "one", "stop_hook_active": True},
    )
    assert observed == [untracked]
    assert response["decision"] == "block"
    assert "eight-block" in response["reason"]


def test_pre_completion_preserves_advisory_context_without_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = _repo(tmp_path)
    monkeypatch.setattr(adapter, "_changed_paths", lambda _root: ["src/app.py"])
    monkeypatch.setattr(
        adapter,
        "_run",
        lambda *_args: [
            _outcome(
                "impact",
                code="MURLOCS_OUTCOME_AUTHORITY_REQUIRED",
                silent=False,
                blocking=False,
            )["outcome"]
        ],
    )
    response = adapter.handle("pre-completion", {"cwd": str(root), "session_id": "one"})
    assert "decision" not in response
    assert "MURLOCS_OUTCOME_AUTHORITY_REQUIRED" in response["systemMessage"]


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m safe",
        "git  commit -m safe",
        "git -C . commit -m safe",
        "/usr/bin/git --no-pager -c commit.gpgsign=false commit -m safe",
        "cd src && git commit -m safe",
    ],
)
def test_git_commit_recognition_covers_supported_spellings(command: str):
    assert adapter._is_git_commit({"command": command}) is True


@pytest.mark.parametrize(
    "command",
    [
        "echo git commit",
        "printf 'git commit'",
        "git status",
        "python build.py",
        "git checkout commit",
    ],
)
def test_git_commit_recognition_ignores_inert_text_and_ordinary_shell(command: str):
    assert adapter._is_git_commit({"command": command}) is False


def test_non_commit_shell_does_not_run_index_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = _repo(tmp_path)
    monkeypatch.setattr(adapter, "run_hook", lambda *_args, **_kwargs: pytest.fail("gate ran"))
    payload = {"cwd": str(root), "session_id": "one", "tool_input": {"command": "echo git commit"}}
    assert adapter.handle("pre-commit", payload) == {}


@pytest.mark.parametrize(
    ("event", "field", "expected"),
    [
        ("prospective-impact", "hookSpecificOutput", "PreToolUse"),
        ("pre-commit", "hookSpecificOutput", "PreToolUse"),
        ("post-edit", "hookSpecificOutput", "PostToolUse"),
        ("pre-completion", "decision", "block"),
    ],
)
def test_entrypoint_reports_host_owned_failure_in_the_correct_event_shape(
    event: str,
    field: str,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    monkeypatch.setattr(adapter, "_payload", lambda: {})
    monkeypatch.setattr(adapter, "handle", lambda *_args: (_ for _ in ()).throw(OSError("boom")))
    adapter.main([event])
    response = json.loads(capsys.readouterr().out)
    if field == "hookSpecificOutput":
        assert response[field]["hookEventName"] == expected
    else:
        assert response[field] == expected
    assert "Murlocs adapter unavailable: boom" in json.dumps(response)
