from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from murlocs.cli import build_cli
from murlocs.errors import MurlocsError
from murlocs.migration import (
    _migration_lock,
    adopt_manifest,
    candidate_from_stewards,
    diff_stewards_candidate,
    inventory_repository,
    prune_legacy,
    rollback_migration,
    write_candidate,
)
from murlocs.stewards import render_legacy_steward_maps

FIXTURE = Path(__file__).parent / "fixtures" / "stewards" / "kida.toml"


def make_legacy_repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "repo"
    stewards = root / ".stewards"
    stewards.mkdir(parents=True)
    manifest_text = FIXTURE.read_text(encoding="utf-8")
    (stewards / "manifest.toml").write_text(manifest_text, encoding="utf-8")
    (stewards / "PROTOCOL.md").write_text("# Legacy review protocol\n", encoding="utf-8")
    (stewards / "project.py").write_text("# legacy projector\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text("# User-owned guide\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_compiler.py").write_text(
        "def test_compile_time_contract(): pass\n", encoding="utf-8"
    )
    source = root / "src" / "kida" / "compiler"
    source.mkdir(parents=True)
    (source / "compile.py").write_text("VALUE = 1\n", encoding="utf-8")
    data = tomllib.loads(manifest_text)
    maps = render_legacy_steward_maps(data)
    for relative, content in maps.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root, maps


def invoke(*argv: str):
    return build_cli().invoke(list(argv))


def test_inventory_and_import_are_read_only_by_default(tmp_path):
    root, _ = make_legacy_repo(tmp_path)
    nested = root / "nested-worktree"
    nested.mkdir()
    (nested / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    (nested / "AGENTS.md").write_text("# Nested\n", encoding="utf-8")

    inventory = inventory_repository(root)
    imported = invoke("import", "--repo", str(root), "--from", "stewards")

    assert inventory["legacy_stewards"] == {
        "network": "Kida",
        "scopes": 2,
        "invariants": 2,
        "checks": 1,
        "layered": False,
        "layers": [],
        "proof_debt": 0,
    }
    assert {item["generator"] for item in inventory["instructions"]} == {
        "stewards",
        "user",
    }
    assert all(
        not item["path"].startswith("nested-worktree/") for item in inventory["instructions"]
    )
    assert imported.exit_code == 0
    assert "schema_version = 1" in imported.output
    assert "# migration info: legacy-severity (2)" in imported.output
    assert not (root / ".murlocs").exists()


def test_import_writes_candidate_but_does_not_adopt_maps(tmp_path):
    root, legacy_maps = make_legacy_repo(tmp_path)
    before = {path: (root / path).read_bytes() for path in legacy_maps}

    result = invoke(
        "import",
        "--repo",
        str(root),
        "--output",
        ".murlocs/manifest.toml",
    )

    assert result.exit_code == 0, result.stderr
    assert (root / ".murlocs" / "manifest.toml").is_file()
    assert (root / ".murlocs" / "PROTOCOL.md").is_file()
    assert {path: (root / path).read_bytes() for path in legacy_maps} == before
    assert not (root / ".murlocs" / "lock.json").exists()


def test_semantic_and_rendered_diff_are_machine_readable(tmp_path):
    root, legacy_maps = make_legacy_repo(tmp_path)

    result = diff_stewards_candidate(root)

    assert result["semantic"]["scopes"] == 2
    assert result["semantic"]["invariants"] == 2
    assert result["semantic"]["findings"][0]["code"] == "legacy-severity"
    assert {item["path"] for item in result["rendered"]} == set(legacy_maps)
    assert all(item["status"] == "changed" for item in result["rendered"])
    assert all(item["diff"].startswith("--- a/") for item in result["rendered"])


def test_adopt_prune_and_rollback_restore_exact_legacy_network(tmp_path):
    root, legacy_maps = make_legacy_repo(tmp_path)
    managed_internal_guide = root / ".murlocs" / "active" / "AGENTS.md"
    managed_internal_guide.parent.mkdir(parents=True)
    managed_internal_guide.write_text("# Active internal guide\n", encoding="utf-8")
    legacy_bytes = {path: (root / path).read_bytes() for path in legacy_maps}
    candidate = candidate_from_stewards(root)
    write_candidate(root, candidate, ".murlocs/manifest.toml")
    active_instruction_paths = {item["path"] for item in inventory_repository(root)["instructions"]}
    assert active_instruction_paths == {
        *legacy_maps,
        ".murlocs/active/AGENTS.md",
        "CLAUDE.md",
    }

    preview = adopt_manifest(root, dry_run=True)
    assert set(preview["adopted"]) == set(legacy_maps)
    assert not (root / ".murlocs" / "lock.json").exists()

    adopted = adopt_manifest(root)
    assert adopted["status"] == "adopted"
    assert (root / ".murlocs" / "lock.json").is_file()
    assert (root / ".murlocs" / "migration.json").is_file()
    assert all(
        (root / path).read_text().startswith("<!-- Generated by Murlocs.") for path in legacy_maps
    )
    assert (root / "CLAUDE.md").read_text() == "# User-owned guide\n"
    assert {
        item["path"] for item in inventory_repository(root)["instructions"]
    } == active_instruction_paths

    prune_preview = prune_legacy(root, dry_run=True)
    rollback_preview = rollback_migration(root, dry_run=True)
    assert prune_preview["pruned"]
    assert rollback_preview["restore"]
    assert (root / ".stewards").is_dir()

    pruned = prune_legacy(root)
    assert pruned["status"] == "pruned"
    assert not (root / ".stewards").exists()
    assert {
        item["path"] for item in inventory_repository(root)["instructions"]
    } == active_instruction_paths

    rolled_back = rollback_migration(root)
    assert rolled_back["status"] == "rolled_back"
    assert (root / ".stewards" / "manifest.toml").is_file()
    assert not (root / ".murlocs" / "lock.json").exists()
    assert {path: (root / path).read_bytes() for path in legacy_maps} == legacy_bytes
    state = json.loads((root / ".murlocs" / "migration.json").read_text())
    assert state["status"] == "rolled_back"
    assert {
        item["path"] for item in inventory_repository(root)["instructions"]
    } == active_instruction_paths


def test_adoption_refuses_modified_legacy_map(tmp_path):
    root, _ = make_legacy_repo(tmp_path)
    candidate = candidate_from_stewards(root)
    write_candidate(root, candidate, ".murlocs/manifest.toml")
    (root / "AGENTS.md").write_text("modified\n", encoding="utf-8")

    result = invoke("adopt", "--repo", str(root))

    assert result.exit_code == 1
    assert "modified or stale" in result.stderr
    assert not (root / ".murlocs" / "lock.json").exists()


def test_rollback_refuses_modified_adopted_map(tmp_path):
    root, _ = make_legacy_repo(tmp_path)
    candidate = candidate_from_stewards(root)
    write_candidate(root, candidate, ".murlocs/manifest.toml")
    adopt_manifest(root)
    (root / "AGENTS.md").write_text("post-adoption edit\n", encoding="utf-8")

    result = invoke("rollback", "--repo", str(root))

    assert result.exit_code == 1
    assert "modified adopted map" in result.stderr
    assert (root / "AGENTS.md").read_text() == "post-adoption edit\n"


def test_concurrent_migration_operations_are_refused(tmp_path):
    root, _ = make_legacy_repo(tmp_path)
    write_candidate(root, candidate_from_stewards(root), ".murlocs/manifest.toml")

    with (
        _migration_lock(root),
        pytest.raises(MurlocsError, match="another migration operation is in progress"),
    ):
        adopt_manifest(root)
