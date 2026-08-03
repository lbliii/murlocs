"""Offline evidence that an installed wheel owns a working Git dispatcher."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parents[1]


def run(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, (
        f"command failed ({completed.returncode}): {' '.join(argv)}\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    return completed


@pytest.mark.skipif(os.name == "nt", reason="the managed dispatcher is a POSIX shell hook")
def test_clean_installed_wheel_commits_through_generated_dispatcher(tmp_path: Path) -> None:
    """Build and install offline, then prove the pinned user tool runs on commit."""
    uv = shutil.which("uv")
    assert uv is not None, "installed-wheel smoke requires uv in CI and local test environments"
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    environment.update({"PIP_NO_INDEX": "1", "UV_OFFLINE": "1"})

    dist = tmp_path / "dist"
    run(
        [uv, "build", "--wheel", "--offline", "--out-dir", str(dist)],
        cwd=PROJECT_ROOT,
        env=environment,
    )
    wheel = next(dist.glob("murlocs-*.whl"))

    tool_environment = tmp_path / "tool-environment"
    run(
        [sys.executable, "-m", "venv", str(tool_environment)],
        cwd=tmp_path,
        env=environment,
    )
    scripts = tool_environment / "bin"
    tool_python = scripts / "python"
    run(
        [uv, "pip", "install", "--python", str(tool_python), "--offline", str(wheel)],
        cwd=tmp_path,
        env=environment,
    )
    installed_module = run(
        [str(tool_python), "-c", "import murlocs; print(murlocs.__file__)"],
        cwd=tmp_path,
        env=environment,
    ).stdout.strip()
    assert str(tool_environment) in installed_module

    # A durable user-level launcher is deliberately outside the managed tool environment.
    tool_bin = tmp_path / "tool-bin"
    tool_bin.mkdir()
    runner = tool_bin / "murlocs"
    runner.write_text(f'#!/bin/sh\nexec "{scripts / "murlocs"}" "$@"\n')
    runner.chmod(0o755)
    environment["PATH"] = str(tool_bin) + os.pathsep + environment["PATH"]

    repository = tmp_path / "repository"
    repository.mkdir()
    for args in (
        ["git", "init", "--quiet"],
        ["git", "config", "user.name", "Installed Wheel"],
        ["git", "config", "user.email", "wheel@example.invalid"],
        ["git", "config", "commit.gpgsign", "false"],
    ):
        run(args, cwd=repository, env=environment)
    run(
        [str(runner), "init", "--name", "Installed Wheel", "--repo", str(repository)],
        cwd=repository,
        env=environment,
    )
    run(["git", "add", "."], cwd=repository, env=environment)
    run(["git", "commit", "--quiet", "-m", "initial"], cwd=repository, env=environment)

    run(
        [str(runner), "hook", "install", "--repo", str(repository)],
        cwd=repository,
        env=environment,
    )
    hook = repository / ".git" / "hooks" / "pre-commit"
    assert str(runner) in hook.read_text()
    (repository / "README.md").write_text("# installed wheel dispatcher smoke\n")
    run(["git", "add", "README.md"], cwd=repository, env=environment)
    run(["git", "commit", "--quiet", "-m", "dispatcher smoke"], cwd=repository, env=environment)
