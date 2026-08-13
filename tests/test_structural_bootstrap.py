from __future__ import annotations

import json
from pathlib import Path

from murlocs.cli import build_cli
from murlocs.manifest import load_manifest
from murlocs.structural_bootstrap import run_structural_bootstrap, uncovered_scope_paths


def invoke(*argv: str):
    return build_cli().invoke(list(argv))


def write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_uncovered_scope_paths_reports_coverage_gaps(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    write(root, "src/app/core.py", "VALUE = 1\n")
    write(root, "tests/test_app.py", "def test_app(): pass\n")
    result = invoke(
        "init",
        "--repo",
        str(root),
        "--name",
        "Example",
        "--coverage-root",
        "src",
        "--coverage-root",
        "tests",
    )
    assert result.exit_code == 0, result.output + result.stderr
    manifest = load_manifest(root)
    assert uncovered_scope_paths(manifest) == ["src/app", "tests"]


def test_run_structural_bootstrap_completes_greenfield_coverage(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    write(root, "httpx/__init__.py", "VERSION = '1'\n")
    write(root, "httpx/_client.py", "class Client: ...\n")
    write(root, "tests/test_client.py", "def test_client(): pass\n")

    result = run_structural_bootstrap(root, name="Httpx")
    assert result.initialized is True
    assert result.structurally_complete is True
    assert "httpx" in result.scopes_added
    assert "tests" in result.scopes_added

    checked = invoke("check", "--repo", str(root))
    assert "coverage structurally complete" in checked.output


def test_bootstrap_command_is_one_shot(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    write(root, "src/flask/app.py", "APP = 1\n")
    write(root, "tests/test_app.py", "def test_app(): pass\n")

    payload = json.loads(
        invoke("bootstrap", "--repo", str(root), "--name", "Flask", "--format", "json").output
    )
    assert payload["ok"] is True
    assert payload["initialized"] is True
    assert payload["coverage"]["state"] == "structurally_complete"
    assert payload["scopes_added"]

    explained = invoke("explain", "--repo", str(root), "src/flask/app.py")
    assert "src/flask" in explained.output


def test_bootstrap_dry_run_does_not_write(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    write(root, "src/app.py", "APP = 1\n")

    preview = invoke("-n", "bootstrap", "--repo", str(root), "--name", "Example")
    assert "would initialize Example" in preview.output
    assert not (root / ".murlocs/manifest.toml").exists()


def test_bootstrap_refuses_unmanaged_agents_md(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    write(root, "AGENTS.md", "# Existing\n")
    write(root, "src/app.py", "APP = 1\n")

    result = invoke("bootstrap", "--repo", str(root))
    assert result.exit_code != 0
    combined = (result.output + result.stderr).lower()
    assert "unmanaged" in combined
