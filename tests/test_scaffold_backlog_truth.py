from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from murlocs.scaffolds.backlog_truth import KIT_FILES, apply_kit, kit_findings, kit_status
from tests.support import initialize_repo, invoke


def _empty_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return root


@pytest.mark.issue(210)
def test_scaffold_dry_run_writes_nothing(tmp_path: Path):
    root = _empty_repo(tmp_path)

    result = invoke(
        "--dry-run",
        "scaffold",
        "backlog-truth",
        "--repo",
        str(root),
        "--format",
        "json",
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["written"]
    assert not (root / ".github").exists()
    assert not (root / ".murlocs" / "kits").exists()


@pytest.mark.issue(210)
def test_scaffold_apply_on_empty_repo_stamps_kit_and_reports_current(tmp_path: Path):
    root = _empty_repo(tmp_path)

    applied = invoke("scaffold", "backlog-truth", "--repo", str(root), "--format", "json")
    assert applied.exit_code == 0, applied.stderr
    payload = json.loads(applied.output)
    assert payload["ok"] is True
    assert payload["kit"] == "backlog_truth"
    assert set(payload["pieces"]) == {
        "templates",
        "labels",
        "workflows",
        "conventions",
        "process",
    }
    assert payload["status"]["state"] == "current"

    for item in KIT_FILES:
        path = root / item.destination
        assert path.is_file(), item.destination
        assert path.stat().st_size > 0

    assert (root / ".murlocs" / "kits" / "backlog_truth.toml").is_file()
    assert "Investigation" in (root / ".github" / "ISSUE_TEMPLATE" / "investigation.yml").read_text(
        encoding="utf-8"
    )
    assert "outside" in (root / "docs" / "plan" / "issue-lifecycle.md").read_text(encoding="utf-8")
    assert "non-compile" in (root / "docs" / "plan" / "BACKLOG.md").read_text(encoding="utf-8")

    status = invoke("scaffold", "status", "--repo", str(root), "--format", "json")
    assert status.exit_code == 0
    status_payload = json.loads(status.output)
    assert status_payload["present"] is True
    assert status_payload["current"] is True
    assert status_payload["state"] == "current"


@pytest.mark.issue(210)
def test_scaffold_pieces_are_individually_adoptable(tmp_path: Path):
    root = _empty_repo(tmp_path)

    result = invoke(
        "scaffold",
        "backlog-truth",
        "--repo",
        str(root),
        "--only",
        "templates",
        "--format",
        "json",
    )
    assert result.exit_code == 0
    assert json.loads(result.output)["pieces"] == ["templates"]
    assert (root / ".github" / "ISSUE_TEMPLATE" / "saga.yml").is_file()
    assert not (root / ".github" / "labels.yml").exists()
    assert not (root / ".github" / "workflows").exists()

    second = invoke(
        "scaffold",
        "backlog-truth",
        "--repo",
        str(root),
        "--only",
        "workflows",
        "--format",
        "json",
    )
    assert second.exit_code == 0
    assert (root / "scripts" / "check_closure_acceptance.py").is_file()
    assert (root / ".github" / "workflows" / "issue-closure-gate.yml").is_file()


@pytest.mark.issue(210)
def test_scaffold_refuses_overwrite_without_force(tmp_path: Path):
    root = _empty_repo(tmp_path)
    assert invoke("scaffold", "backlog-truth", "--repo", str(root)).exit_code == 0
    target = root / ".github" / "ISSUE_TEMPLATE" / "saga.yml"
    target.write_text("# local fork\n", encoding="utf-8")

    blocked = invoke("scaffold", "backlog-truth", "--repo", str(root))
    assert blocked.exit_code == 1
    assert "refusing to overwrite" in blocked.stderr

    forced = invoke("scaffold", "backlog-truth", "--repo", str(root), "--force", "--format", "json")
    assert forced.exit_code == 0
    assert "local fork" not in target.read_text(encoding="utf-8")
    assert kit_status(root).current is True


@pytest.mark.issue(210)
def test_scaffold_records_manifest_section_and_check_reports_kit(tmp_path: Path):
    root = _empty_repo(tmp_path)
    (root / "src").mkdir()
    (root / "src" / "pkg.py").write_text("VALUE = 1\n", encoding="utf-8")
    initialize_repo(root, "--name", "Kit Repo", "--coverage-root", "src")

    assert invoke("scaffold", "backlog-truth", "--repo", str(root)).exit_code == 0
    # Scaffolding updates the manifest; recompile so check is not blocked on drift.
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    manifest = (root / ".murlocs" / "manifest.toml").read_text(encoding="utf-8")
    assert "[kits.backlog_truth]" in manifest
    assert "process_docs" in manifest
    assert "docs/plan/issue-lifecycle.md" in manifest

    check = invoke("check", "--repo", str(root), "--format", "json")
    assert check.exit_code == 0, check.output
    assert not any(item["code"].startswith("kit") for item in json.loads(check.output)["findings"])

    # Drift a stamped file and ensure check surfaces kit-drift.
    (root / "docs" / "backlog-automation.md").write_text("changed\n", encoding="utf-8")
    drifted = invoke("check", "--repo", str(root), "--format", "json")
    assert drifted.exit_code == 1
    codes = {item["code"] for item in json.loads(drifted.output)["findings"]}
    assert "kit-drift" in codes


@pytest.mark.issue(210)
def test_closure_gate_stub_passes_without_claims_and_fails_without_anchor(tmp_path: Path):
    root = _empty_repo(tmp_path)
    assert apply_kit(root, pieces=["workflows"])["ok"] is True
    script = root / "scripts" / "check_closure_acceptance.py"

    clean = subprocess.run(
        [sys.executable, str(script)],
        cwd=root,
        env={**os.environ, "PR_BODY": "No closes"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert clean.returncode == 0, clean.stdout + clean.stderr

    missing = subprocess.run(
        [sys.executable, str(script), "--body", "Closes #210"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode == 1
    assert "#210" in missing.stdout

    exempt = subprocess.run(
        [
            sys.executable,
            str(script),
            "--body",
            "Closes #210\n\nAcceptance #210: n/a (docs-only scaffold wiring)\n",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert exempt.returncode == 0, exempt.stdout + exempt.stderr


@pytest.mark.issue(210)
def test_kit_findings_empty_without_install(tmp_path: Path):
    root = _empty_repo(tmp_path)
    assert kit_findings(root) == []
