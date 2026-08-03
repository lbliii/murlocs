from __future__ import annotations

import json
from pathlib import Path

import pytest
from milo.testing import MCPClient

from murlocs.adoption import adoption_status
from murlocs.cli import build_cli

LEGACY_FIXTURE = Path(__file__).parent / "fixtures" / "stewards" / "kida.toml"


def invoke(*argv: str):
    return build_cli().invoke(list(argv))


def initialize(root: Path) -> None:
    result = invoke("init", "--repo", str(root))
    assert result.exit_code == 0, result.stderr


def add_legacy_manifest(root: Path) -> None:
    stewards = root / ".stewards"
    stewards.mkdir(parents=True)
    (stewards / "manifest.toml").write_text(
        LEGACY_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8"
    )


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        ("empty", "uninitialized"),
        ("user", "user_owned"),
        ("legacy", "legacy_detected"),
        ("candidate", "candidate_manifest"),
        ("adopted", "migration_adopted"),
        ("pruned", "migration_pruned"),
        ("managed", "managed_synchronized"),
        ("invalid", "managed_invalid"),
        ("ambiguous", "ambiguous"),
    ],
)
def test_status_classifies_supported_lifecycle_states_without_writes(
    tmp_path: Path, setup: str, expected: str
) -> None:
    root = tmp_path / setup
    root.mkdir()
    if setup == "user":
        (root / "AGENTS.md").write_text("# User guidance\n", encoding="utf-8")
    elif setup == "legacy":
        add_legacy_manifest(root)
    elif setup in {"candidate", "adopted", "pruned", "managed", "invalid", "ambiguous"}:
        initialize(root)
        if setup == "candidate":
            (root / ".murlocs" / "lock.json").unlink()
        elif setup == "adopted":
            add_legacy_manifest(root)
            (root / ".murlocs" / "migration.json").write_text(
                json.dumps({"status": "adopted"}), encoding="utf-8"
            )
        elif setup == "pruned":
            (root / ".murlocs" / "migration.json").write_text(
                json.dumps({"status": "pruned"}), encoding="utf-8"
            )
        elif setup == "invalid":
            manifest = root / ".murlocs" / "manifest.toml"
            manifest.write_text(
                manifest.read_text(encoding="utf-8").replace(
                    "Repository guidance", "Changed guidance"
                ),
                encoding="utf-8",
            )
        elif setup == "ambiguous":
            add_legacy_manifest(root)

    before = snapshot(root)
    result = adoption_status(root)

    assert result["state"] == expected
    assert result["evidence"]
    assert result["next_actions"]
    assert result["semantic_correctness"] == "not_evaluated"
    assert all(action["writes"] is False for action in result["next_actions"])
    action_ids = {action["id"] for action in result["next_actions"]}
    if setup == "adopted":
        assert action_ids == {"preview_prune", "preview_rollback"}
    elif setup == "pruned":
        assert action_ids == {"validate_managed_network", "preview_rollback"}
    assert snapshot(root) == before


def test_status_reports_stable_blockers_and_ambiguous_precedence(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".murlocs").mkdir()
    (root / ".murlocs" / "lock.json").write_text("{}\n", encoding="utf-8")

    result = adoption_status(root)

    assert result["state"] == "ambiguous"
    assert "lock_without_manifest" in {item["id"] for item in result["blockers"]}
    assert result["next_actions"][0]["id"] == "inspect_inventory"


def test_status_does_not_guess_when_migration_record_is_invalid(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    initialize(root)
    (root / ".murlocs" / "migration.json").write_text("{}\n", encoding="utf-8")

    result = adoption_status(root)

    assert result["state"] == "ambiguous"
    assert "invalid_migration_record" in {item["id"] for item in result["blockers"]}


def test_status_human_and_structured_surfaces_name_evidence_and_next_action(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    before = snapshot(root)

    human = invoke("status", "--repo", str(root))
    structured = MCPClient(build_cli()).call("status", repo=str(root)).structured

    assert human.exit_code == 0
    assert "state: uninitialized" in human.output
    assert "evidence repository_root: ." in human.output
    assert "next preview_initialization: murlocs --dry-run init --repo ." in human.output
    assert structured["state"] == "uninitialized"
    assert structured["evidence"][0]["path"] == "."
    assert structured["next_actions"][0]["id"] == "preview_initialization"
    assert snapshot(root) == before


def test_structured_status_with_blockers_retains_success_exit_contract(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".murlocs").mkdir()
    (root / ".murlocs" / "lock.json").write_text("{}\n", encoding="utf-8")

    result = invoke("status", "--repo", str(root), "--format", "json")

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["state"] == "ambiguous"
    assert payload["blockers"]
