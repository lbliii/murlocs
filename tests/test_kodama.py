from __future__ import annotations

import json
from pathlib import Path

from kodama.cli import main
from kodama.lockfile import sha256_bytes


def make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
    return root


def test_init_compile_check_and_explain(tmp_path, capsys):
    root = make_repo(tmp_path)
    assert main(["init", "--repo", str(root), "--name", "Example Forest"]) == 0
    assert (root / "AGENTS.md").is_file()
    assert (root / ".kodama" / "lock.json").is_file()
    assert main(["compile", "--repo", str(root)]) == 0
    assert main(["check", "--repo", str(root)]) == 0
    assert main(["explain", "src/pkg/core.py", "--repo", str(root)]) == 0
    output = capsys.readouterr().out
    assert "Example Forest" in (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "[root] AGENTS.md" in output


def test_compile_is_deterministic(tmp_path):
    root = make_repo(tmp_path)
    assert main(["init", "--repo", str(root)]) == 0
    before = (root / "AGENTS.md").read_bytes()
    assert main(["compile", "--repo", str(root)]) == 0
    assert (root / "AGENTS.md").read_bytes() == before


def test_init_refuses_unmanaged_agents_file(tmp_path, capsys):
    root = make_repo(tmp_path)
    (root / "AGENTS.md").write_text("# Mine\n", encoding="utf-8")
    assert main(["init", "--repo", str(root)]) == 2
    assert "unmanaged" in capsys.readouterr().err
    assert (root / "AGENTS.md").read_text(encoding="utf-8") == "# Mine\n"


def test_compile_refuses_modified_generated_file(tmp_path, capsys):
    root = make_repo(tmp_path)
    assert main(["init", "--repo", str(root)]) == 0
    (root / "AGENTS.md").write_text("manual edit\n", encoding="utf-8")
    assert main(["compile", "--repo", str(root)]) == 2
    assert "modified generated file" in capsys.readouterr().err
    assert (root / "AGENTS.md").read_text(encoding="utf-8") == "manual edit\n"


def test_check_detects_manifest_drift(tmp_path, capsys):
    root = make_repo(tmp_path)
    assert main(["init", "--repo", str(root)]) == 0
    manifest = root / ".kodama" / "manifest.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("Repository guidance", "Agent guidance"),
        encoding="utf-8",
    )
    assert main(["check", "--repo", str(root)]) == 1
    assert "manifest changed" in capsys.readouterr().err


def test_check_detects_missing_manual_proof(tmp_path, capsys):
    root = make_repo(tmp_path)
    assert main(["init", "--repo", str(root)]) == 0
    manifest = root / ".kodama" / "manifest.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            'anchor = "Use this protocol"', 'anchor = "nope"'
        ),
        encoding="utf-8",
    )
    assert main(["check", "--repo", str(root)]) == 1
    assert "manual evidence was not found" in capsys.readouterr().err


def test_check_detects_uncovered_source_unit(tmp_path, capsys):
    root = make_repo(tmp_path)
    assert main(["init", "--repo", str(root)]) == 0
    manifest = root / ".kodama" / "manifest.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("roots = []", 'roots = ["src"]'),
        encoding="utf-8",
    )
    assert main(["check", "--repo", str(root)]) == 1
    assert "src/pkg" in capsys.readouterr().err


def test_reasoned_coverage_exemption(tmp_path):
    root = make_repo(tmp_path)
    assert main(["init", "--repo", str(root)]) == 0
    manifest = root / ".kodama" / "manifest.toml"
    text = manifest.read_text(encoding="utf-8")
    text = text.replace("roots = []", 'roots = ["src"]')
    text = text.replace("[coverage.exemptions]", '[coverage.exemptions]\n"src/pkg" = "small leaf"')
    manifest.write_text(text, encoding="utf-8")
    assert main(["compile", "--repo", str(root)]) == 0
    assert main(["check", "--repo", str(root)]) == 0


def test_lock_hash_matches_generated_map(tmp_path):
    root = make_repo(tmp_path)
    assert main(["init", "--repo", str(root)]) == 0
    lock = json.loads((root / ".kodama" / "lock.json").read_text(encoding="utf-8"))
    assert lock["generated"]["AGENTS.md"]["sha256"] == sha256_bytes(
        (root / "AGENTS.md").read_bytes()
    )


def test_explain_rejects_path_outside_repo(tmp_path, capsys):
    root = make_repo(tmp_path)
    assert main(["init", "--repo", str(root)]) == 0
    assert main(["explain", "../outside", "--repo", str(root)]) == 2
    assert "outside repository" in capsys.readouterr().err


def test_check_reports_escaping_map_path(tmp_path, capsys):
    root = make_repo(tmp_path)
    assert main(["init", "--repo", str(root)]) == 0
    manifest = root / ".kodama" / "manifest.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace('map = "AGENTS.md"', 'map = "../AGENTS.md"'),
        encoding="utf-8",
    )
    assert main(["check", "--repo", str(root)]) == 1
    assert "escapes the repository" in capsys.readouterr().err


def test_context_budget_uses_largest_applicable_chain(tmp_path, capsys):
    root = make_repo(tmp_path)
    assert main(["init", "--repo", str(root)]) == 0
    manifest = root / ".kodama" / "manifest.toml"
    text = manifest.read_text(encoding="utf-8")
    text = text.replace("max_active_bytes = 24576", "max_active_bytes = 1")
    manifest.write_text(text, encoding="utf-8")
    assert main(["check", "--repo", str(root)]) == 1
    assert "generated guidance is" in capsys.readouterr().err
