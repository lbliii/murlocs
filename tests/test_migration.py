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


def test_adoption_write_failure_restores_operator_maps_byte_for_byte(tmp_path, monkeypatch):
    """A fault partway through adoption must restore the operator's maps exactly."""
    from murlocs import migration as mmig

    root, legacy_maps = make_legacy_repo(tmp_path)
    assert len(legacy_maps) >= 2  # a partial write is only meaningful with >1 map
    legacy_bytes = {path: (root / path).read_bytes() for path in legacy_maps}
    write_candidate(root, candidate_from_stewards(root), ".murlocs/manifest.toml")

    real_write = mmig._write_bytes_atomic
    calls = {"n": 0}

    def flaky(path, content):
        calls["n"] += 1
        # Fail on the second adopted-map write: the first map has already been
        # overwritten, so this exercises the
        # `except BaseException: _restore_adoption(...)` recovery, not a no-op.
        if calls["n"] == 2:
            raise OSError("injected write failure")
        return real_write(path, content)

    monkeypatch.setattr(mmig, "_write_bytes_atomic", flaky)

    with pytest.raises(OSError, match="injected write failure"):
        adopt_manifest(root)

    # Every operator map is restored byte-for-byte, and no migration artifact
    # survives the aborted adoption.
    for path in legacy_maps:
        assert (root / path).read_bytes() == legacy_bytes[path]
    assert not (root / ".murlocs" / "migration.json").exists()
    assert not (root / ".murlocs" / "lock.json").exists()


def test_prune_failure_moves_stewards_back(tmp_path, monkeypatch):
    """A state-write fault after the legacy move must roll the move back."""
    from murlocs import migration as mmig

    root, _ = make_legacy_repo(tmp_path)
    write_candidate(root, candidate_from_stewards(root), ".murlocs/manifest.toml")
    adopt_manifest(root)
    before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in (root / ".stewards").rglob("*")
        if path.is_file()
    }

    def boom(path, data):
        raise OSError("injected state write failure")

    monkeypatch.setattr(mmig, "_write_json_atomic", boom)

    with pytest.raises(OSError, match="injected state write failure"):
        prune_legacy(root)

    # The compensating shutil.move restored .stewards intact.
    assert (root / ".stewards").is_dir()
    after = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in (root / ".stewards").rglob("*")
        if path.is_file()
    }
    assert after == before
    assert json.loads((root / ".murlocs" / "migration.json").read_text())["status"] == "adopted"


def test_rollback_missing_backup_fails_before_mutation(tmp_path):
    """Removing the backup directory yields a MurlocsError, not a raw traceback,
    and leaves the adopted maps untouched rather than half-restored."""
    import shutil

    root, _ = make_legacy_repo(tmp_path)
    write_candidate(root, candidate_from_stewards(root), ".murlocs/manifest.toml")
    adopted = adopt_manifest(root)
    adopted_bytes = {path: (root / path).read_bytes() for path in adopted["adopted_sha256"]}

    shutil.rmtree(root / ".murlocs" / "backups")

    with pytest.raises(MurlocsError, match="backup is missing"):
        rollback_migration(root)

    for path, data in adopted_bytes.items():
        assert (root / path).read_bytes() == data
    assert json.loads((root / ".murlocs" / "migration.json").read_text())["status"] == "adopted"


def test_rollback_incomplete_backup_fails_before_mutation(tmp_path):
    """A single missing backup file is caught pre-flight, before any restore."""
    root, _ = make_legacy_repo(tmp_path)
    write_candidate(root, candidate_from_stewards(root), ".murlocs/manifest.toml")
    adopted = adopt_manifest(root)
    files = root / ".murlocs" / "backups" / adopted["id"] / "files"
    victim = next(path for path in sorted(files.rglob("*")) if path.is_file())
    victim.unlink()
    adopted_bytes = {path: (root / path).read_bytes() for path in adopted["adopted_sha256"]}

    with pytest.raises(MurlocsError, match="backup is incomplete"):
        rollback_migration(root)

    for path, data in adopted_bytes.items():
        assert (root / path).read_bytes() == data


def test_corrupt_migration_state_raises_murlocs_error(tmp_path):
    """A truncated migration.json surfaces as a clean MurlocsError."""
    root, _ = make_legacy_repo(tmp_path)
    write_candidate(root, candidate_from_stewards(root), ".murlocs/manifest.toml")
    adopt_manifest(root)
    (root / ".murlocs" / "migration.json").write_text('{"status": "ado', encoding="utf-8")

    with pytest.raises(MurlocsError, match="corrupt"):
        rollback_migration(root)
    with pytest.raises(MurlocsError, match="corrupt"):
        prune_legacy(root)


def test_double_adopt_is_refused(tmp_path):
    """Adopting twice is a deterministic MurlocsError, not silent double state."""
    root, _ = make_legacy_repo(tmp_path)
    write_candidate(root, candidate_from_stewards(root), ".murlocs/manifest.toml")
    adopt_manifest(root)

    with pytest.raises(MurlocsError, match="active migration already exists"):
        adopt_manifest(root)


def test_double_rollback_is_refused(tmp_path):
    """Rolling back an already-rolled-back migration fails cleanly."""
    root, _ = make_legacy_repo(tmp_path)
    write_candidate(root, candidate_from_stewards(root), ".murlocs/manifest.toml")
    adopt_manifest(root)
    rollback_migration(root)

    with pytest.raises(MurlocsError, match="expected"):
        rollback_migration(root)
