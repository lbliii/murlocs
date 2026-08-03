from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

import murlocs.hooks as hooks_module
from murlocs.cli import build_cli, compile_command, init_command
from murlocs.errors import MurlocsError
from murlocs.gitview import HookTimeout
from murlocs.hooks import (
    hook_status,
    install_hooks,
    parse_pre_push,
    run_hook,
    uninstall_hooks,
)

HOOK_RUNNER = Path(sys.executable).with_name("murlocs")


def git(root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        input=input_bytes,
        check=True,
        capture_output=True,
    )
    return result.stdout.strip()


def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "--quiet")
    git(root, "config", "user.name", "Hook Test")
    git(root, "config", "user.email", "hook@example.invalid")
    git(root, "config", "commit.gpgsign", "false")
    git(root, "config", "user.name", "Hook Test")
    git(root, "config", "user.email", "hook@example.invalid")
    (root / "README.md").write_text("# Hook test\n")
    initialized = init_command(repo=str(root), name="Hook Test")
    assert initialized["ok"] is True
    git(root, "add", ".")
    git(root, "commit", "--quiet", "-m", "initial")
    return root


def invoke(*argv: str):
    return build_cli().invoke(list(argv))


def durable_runner(tmp_path: Path, version: str = "0.1.0") -> Path:
    """Create a user-level launcher that remains outside a project virtualenv."""
    runner = tmp_path / "user-bin" / "murlocs"
    runner.parent.mkdir(exist_ok=True)
    runner.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then\n"
        f"  echo 'murlocs {version}'\n"
        "  exit 0\n"
        "fi\n"
        f"exec {shlex.quote(str(sys.executable))} -m murlocs \"$@\"\n"
    )
    runner.chmod(0o755)
    return runner


def test_hook_cli_management_preserves_output_and_exit_behavior(tmp_path: Path) -> None:
    root = repository(tmp_path)

    installed = invoke(
        "hook",
        "install",
        "--event",
        "pre-commit",
        "--repo",
        str(root),
        "--runner",
        str(HOOK_RUNNER),
    )
    status = invoke("hook", "status", "--repo", str(root))
    removed = invoke(
        "hook", "uninstall", "--event", "pre-commit", "--repo", str(root)
    )
    missing = invoke("hook", "status", "--repo", str(tmp_path / "missing"))

    assert (installed.exit_code, installed.output, installed.stderr) == (
        0,
        "installed pre-commit\n",
        "",
    )
    assert (status.exit_code, status.output, status.stderr) == (
        0,
        "pre-commit: installed\npre-push: absent\n",
        "",
    )
    assert (removed.exit_code, removed.output, removed.stderr) == (
        0,
        "removed pre-commit\n",
        "",
    )
    assert missing.exit_code == 1
    assert missing.output == ""
    assert missing.stderr.startswith("error: ")


def test_hook_cli_run_preserves_silent_success_and_blocking_exit(tmp_path: Path) -> None:
    root = repository(tmp_path)
    plain = tmp_path / "plain"
    plain.mkdir()
    (root / "README.md").write_text("# staged\n")
    git(root, "add", "README.md")

    healthy = invoke(
        "hook",
        "run",
        "pre-commit",
        "--repo",
        str(root),
        "--correlation-id",
        "test:cli",
    )
    invalid = invoke(
        "hook",
        "run",
        "pre-commit",
        "--repo",
        str(plain),
        "--correlation-id",
        "test:invalid",
    )

    assert (healthy.exit_code, healthy.output, healthy.stderr) == (0, "\n", "")
    assert invalid.exit_code == 1
    assert invalid.output == ""
    assert invalid.stderr == (
        "fatal: not a git repository (or any of the parent directories): .git\n"
    )


def test_pre_commit_uses_exact_index_and_is_silent_when_healthy(tmp_path: Path) -> None:
    root = repository(tmp_path)
    (root / "README.md").write_text("# staged\n")
    git(root, "add", "README.md")
    # An invalid unstaged manifest must not leak into the selected index view.
    (root / ".murlocs/manifest.toml").write_text("not toml = [")

    result = run_hook("pre-commit", root, correlation_id="test:index")

    assert result.exit_code == 0
    assert result.terminal_text == ""
    assert result.payload["repository"]["view"] == "index"
    assert result.payload["repository"]["blocking"] is False
    assert [item["operation"] for item in result.payload["operations"]] == [
        "check",
        "impact",
    ]
    assert "dependency_before_id" not in result.payload["operations"][0]
    assert result.payload["operations"][1]["dependency_before_id"]


def test_pre_commit_blocks_staged_guidance_drift_without_running_checks(
    tmp_path: Path,
) -> None:
    root = repository(tmp_path)
    sentinel = root / "MUST_NOT_EXIST"
    manifest = root / ".murlocs/manifest.toml"
    manifest.write_text(
        manifest.read_text().replace(
            'verification = "manual"\nevidence_file = ".murlocs/PROTOCOL.md"\n'
            'anchor = "Use this protocol"',
            'verification = "command"\nenforced_by = "never-run"',
        )
        + '\n[checks.never-run]\ninvoke = "touch MUST_NOT_EXIST"\n'
        + 'location = "README.md"\nproof_contains = "# Hook test"\n'
    )
    assert compile_command(repo=str(root))["ok"] is True
    git(root, "add", ".")
    git(root, "commit", "--quiet", "-m", "register inert check")
    agents = root / "AGENTS.md"
    agents.write_text(agents.read_text() + "\nmanual drift\n")
    git(root, "add", "AGENTS.md")

    result = run_hook("pre-commit", root, correlation_id="test:drift")

    assert result.exit_code == 1
    assert result.payload["execution"]["status"] == "completed", result.payload["summary"]
    assert result.payload["repository"]["blocking"] is True
    assert not sentinel.exists()


def test_pre_commit_accepts_adversarial_paths_as_data(tmp_path: Path) -> None:
    root = repository(tmp_path)
    names = ("-dash.txt", "space name.txt", "unicodé.txt", "line\nbreak.txt")
    for name in names:
        (root / name).write_text(name)
    git(root, "add", "--", *names)

    result = run_hook(
        "pre-commit",
        root,
        correlation_id="test:paths",
        explicit_paths=(names[2], names[0], names[0]),
    )

    assert result.exit_code == 0
    assert result.payload["metrics"]["changed_paths"] == len(names)


def test_manifest_deletion_fails_closed_as_authority_removal(tmp_path: Path) -> None:
    root = repository(tmp_path)
    git(root, "rm", "--quiet", ".murlocs/manifest.toml")

    result = run_hook("pre-commit", root, correlation_id="test:deleted")

    assert result.exit_code == 1
    assert result.payload["execution"]["status"] == "invalid"
    assert "authority" in result.payload["summary"]
    assert result.payload["operations"] == []


def test_staged_malformed_manifest_fails_with_source_diagnostic(tmp_path: Path) -> None:
    root = repository(tmp_path)
    (root / ".murlocs/manifest.toml").write_text("not toml = [")
    git(root, "add", ".murlocs/manifest.toml")

    result = run_hook("pre-commit", root, correlation_id="test:malformed")

    assert result.exit_code == 1
    assert result.payload["execution"]["status"] == "invalid"
    assert "manifest" in result.payload["summary"].lower()


def test_pre_push_assesses_outgoing_commit_and_rejects_malformed_input(
    tmp_path: Path,
) -> None:
    root = repository(tmp_path)
    old = git(root, "rev-parse", "HEAD").decode()
    (root / "README.md").write_text("# pushed\n")
    git(root, "add", "README.md")
    git(root, "commit", "--quiet", "-m", "outgoing")
    new = git(root, "rev-parse", "HEAD").decode()
    update = f"refs/heads/main {new} refs/heads/main {old}\n".encode()

    result = run_hook(
        "pre-push", root, correlation_id="test:push", pre_push_input=update
    )

    assert result.exit_code == 0
    assert result.payload["results"][0]["event"] == "pre-completion"
    assert result.payload["results"][0]["cache"] == {"decision": "forbidden"}
    with pytest.raises(MurlocsError, match="malformed"):
        parse_pre_push(b"not a pre push line\n", "sha1")


def test_installer_is_idempotent_reversible_and_preserves_occupied_slots(
    tmp_path: Path,
) -> None:
    root = repository(tmp_path)
    first = install_hooks(root, (), runner=str(HOOK_RUNNER))
    second = install_hooks(root, ())

    assert first["changed"] == ["pre-commit", "pre-push"]
    assert second["changed"] == []
    assert hook_status(root)["events"] == {
        "pre-commit": "installed",
        "pre-push": "installed",
    }
    removed = uninstall_hooks(root, ("pre-commit", "pre-push"))
    assert removed["changed"] == ["pre-commit", "pre-push"]

    occupied = Path(git(root, "rev-parse", "--git-dir").decode())
    if not occupied.is_absolute():
        occupied = root / occupied
    occupied = occupied / "hooks" / "pre-commit"
    occupied.write_text("#!/bin/sh\nexit 0\n")
    with pytest.raises(MurlocsError, match="refusing to replace"):
        install_hooks(root, ("pre-commit",), runner=str(HOOK_RUNNER))
    assert occupied.read_text() == "#!/bin/sh\nexit 0\n"

    occupied.unlink()
    occupied.symlink_to("missing-manager-hook")
    with pytest.raises(MurlocsError, match="refusing to replace"):
        install_hooks(root, ("pre-commit",), runner=str(HOOK_RUNNER))
    assert occupied.is_symlink()


def test_installer_refuses_custom_hook_path(tmp_path: Path) -> None:
    root = repository(tmp_path)
    git(root, "config", "core.hooksPath", ".githooks")

    with pytest.raises(MurlocsError, match="core.hooksPath"):
        install_hooks(root, (), runner=str(HOOK_RUNNER))


def test_installer_rejects_uv_run_project_virtual_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repository(tmp_path)
    virtualenv = tmp_path / "project" / ".venv"
    runner = virtualenv / "bin" / "murlocs"
    runner.parent.mkdir(parents=True)
    (virtualenv / "pyvenv.cfg").write_text("home = /python\n")
    runner.write_text("#!/bin/sh\necho 'murlocs 0.1.0'\n")
    runner.chmod(0o755)
    monkeypatch.setenv("PATH", str(runner.parent) + os.pathsep + os.environ["PATH"])

    with pytest.raises(MurlocsError, match="project virtual environment"):
        install_hooks(root, ("pre-commit",))

    assert hook_status(root)["events"]["pre-commit"] == "absent"


def test_user_level_runner_dispatcher_smoke_and_status_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repository(tmp_path)
    runner = durable_runner(tmp_path)
    monkeypatch.setenv("PATH", str(runner.parent) + os.pathsep + os.environ["PATH"])

    installed = install_hooks(root, ("pre-commit",))
    hook = Path(git(root, "rev-parse", "--git-path", "hooks/pre-commit").decode())
    if not hook.is_absolute():
        hook = root / hook
    assert installed["changed"] == ["pre-commit"]
    assert str(runner) in hook.read_text()
    assert hook_status(root)["events"]["pre-commit"] == "installed"

    (root / "README.md").write_text("# dispatcher smoke\n")
    git(root, "add", "README.md")
    git(root, "commit", "--quiet", "-m", "dispatch through generated hook")

    runner.unlink()
    assert hook_status(root)["events"]["pre-commit"] == "missing runner"
    missing = subprocess.run([str(hook)], cwd=root, capture_output=True, text=True)
    assert missing.returncode == 1
    assert missing.stderr == (
        "Murlocs hook runner is missing; run 'murlocs hook install' to repair it.\n"
    )
    assert uninstall_hooks(root, ("pre-commit",))["changed"] == ["pre-commit"]


def test_status_reports_detectable_runner_version_mismatch(tmp_path: Path) -> None:
    root = repository(tmp_path)
    runner = durable_runner(tmp_path)
    install_hooks(root, ("pre-commit",), runner=str(runner))
    durable_runner(tmp_path, version="0.2.0")

    assert hook_status(root)["events"]["pre-commit"] == "version mismatch"


def test_non_git_and_partial_adoption_fail_with_actionable_errors(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    non_git = run_hook("pre-commit", plain, correlation_id="test:non-git")
    assert non_git.exit_code == 1
    assert non_git.payload["execution"]["status"] == "invalid"

    root = tmp_path / "partial"
    root.mkdir()
    git(root, "init", "--quiet")
    (root / ".murlocs").mkdir()
    (root / ".murlocs/PROTOCOL.md").write_text("# Partial\n")
    git(root, "add", ".murlocs/PROTOCOL.md")
    partial = run_hook("pre-commit", root, correlation_id="test:partial")
    assert partial.exit_code == 1
    assert "incomplete" in partial.payload["summary"]


def test_state_race_and_timeout_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repository(tmp_path)
    (root / "README.md").write_text("# staged\n")
    git(root, "add", "README.md")
    original = hooks_module._recapture

    def stale(*args, **kwargs):
        snapshot = original(*args, **kwargs)
        return type(snapshot)(
            snapshot.view, snapshot.object_id, snapshot.entries, "sha256:" + "0" * 64
        )

    monkeypatch.setattr(hooks_module, "_recapture", stale)
    raced = run_hook("pre-commit", root, correlation_id="test:race")
    assert raced.exit_code == 1
    assert raced.payload["execution"]["status"] == "stale"

    def timeout(*args, **kwargs):
        raise HookTimeout("bounded timeout")

    monkeypatch.setattr(hooks_module, "discover_git", timeout)
    timed_out = run_hook("pre-commit", root, correlation_id="test:timeout")
    assert timed_out.exit_code == 1
    assert timed_out.payload["execution"]["status"] == "timeout"
