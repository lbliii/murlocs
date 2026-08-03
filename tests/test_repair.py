from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import murlocs.repair as repair_module
from murlocs.cli import build_cli
from murlocs.repair import apply_repair, plan_repair_from_root, recover_repair


def invoke(*argv: str):
    return build_cli().invoke(list(argv))


def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    assert invoke("init", "--repo", str(root)).exit_code == 0
    return root


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def introduce_source_drift(root: Path) -> None:
    manifest = root / ".murlocs" / "manifest.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "Repository guidance", "Reviewed repository guidance"
        ),
        encoding="utf-8",
    )


def test_repair_preview_and_apply_have_exact_path_and_byte_parity(tmp_path: Path):
    root = repository(tmp_path)
    introduce_source_drift(root)
    before = snapshot(root)

    preview = invoke("--dry-run", "repair", "--repo", str(root), "--format", "json")
    preview_payload = json.loads(preview.output)

    assert preview.exit_code == 0
    assert preview_payload["changed"] == [".murlocs/lock.json", "AGENTS.md"]
    assert preview_payload["restage_required"] is True
    assert preview_payload["rerun_required"] is True
    assert preview_payload["outcome"]["resolution_class"] == "deterministic_repair"
    assert snapshot(root) == before

    applied = invoke("repair", "--repo", str(root), "--format", "json")
    applied_payload = json.loads(applied.output)

    assert applied.exit_code == 0
    assert applied_payload["changed"] == preview_payload["changed"]
    assert applied_payload["updates"] == preview_payload["updates"]
    assert (root / "AGENTS.md").read_bytes() != before["AGENTS.md"]
    assert invoke("check", "--repo", str(root)).exit_code == 0


def test_repair_refuses_semantic_and_modified_output_findings_without_writes(tmp_path: Path):
    root = repository(tmp_path)
    protocol = root / ".murlocs" / "PROTOCOL.md"
    protocol_bytes = protocol.read_bytes()
    protocol.unlink()
    before = snapshot(root)

    semantic = invoke("repair", "--repo", str(root), "--format", "json")
    semantic_payload = json.loads(semantic.output)

    assert semantic.exit_code == 1
    assert semantic_payload["ok"] is False
    assert semantic_payload["changed"] == []
    assert semantic_payload["outcome"]["resolution_class"] in {
        "agent_action",
        "authority_required",
    }
    assert snapshot(root) == before

    protocol.write_bytes(protocol_bytes)
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    (root / "AGENTS.md").write_text("manual edit\n", encoding="utf-8")
    before = snapshot(root)

    modified = invoke("repair", "--repo", str(root), "--format", "json")
    modified_payload = json.loads(modified.output)

    assert modified.exit_code == 1
    assert modified_payload["ok"] is False
    assert modified_payload["changed"] == []
    assert modified_payload["outcome"]["resolution_class"] == "authority_required"
    assert snapshot(root) == before


def test_interrupted_repair_leaves_exact_recovery_state_then_rolls_back(
    tmp_path: Path, monkeypatch
):
    root = repository(tmp_path)
    introduce_source_drift(root)
    before = snapshot(root)
    plan = plan_repair_from_root(root)
    original = repair_module._atomic_write
    agents_writes = 0

    def interrupted(path: Path, content: bytes) -> None:
        nonlocal agents_writes
        if path.name == "AGENTS.md":
            agents_writes += 1
            raise OSError("simulated repair interruption")
        if path.name == "lock.json" and agents_writes:
            raise OSError("simulated repair interruption")
        original(path, content)

    monkeypatch.setattr(repair_module, "_atomic_write", interrupted)
    with pytest.raises(OSError, match="simulated repair interruption"):
        apply_repair(plan)

    journal = root / ".murlocs" / "repair" / ".transaction"
    assert journal.is_dir()
    monkeypatch.setattr(repair_module, "_atomic_write", original)

    status, changed = recover_repair(root, dry_run=True)
    assert status == "roll back interrupted repair transaction"
    assert changed == [".murlocs/lock.json"]
    assert journal.is_dir()

    status, changed = recover_repair(root, dry_run=False)
    assert status == "roll back interrupted repair transaction"
    assert changed == [".murlocs/lock.json"]
    assert not journal.exists()
    assert snapshot(root) == before


def test_repair_result_tells_git_callers_to_restage_and_rerun(tmp_path: Path):
    root = repository(tmp_path)
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    introduce_source_drift(root)
    preview = invoke("--dry-run", "repair", "--repo", str(root))
    result = invoke("repair", "--repo", str(root), "--format", "json")
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["restage_required"] is True
    assert payload["rerun_required"] is True
    assert "re-stage changed paths and re-run the gate" in preview.output
