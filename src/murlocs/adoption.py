from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from murlocs.errors import MurlocsError
from murlocs.lockfile import LOCK_PATH
from murlocs.manifest import load_manifest
from murlocs.migration import MIGRATION_STATE, inventory_repository
from murlocs.verify import Finding, validate


def adoption_status(root: Path) -> dict[str, Any]:
    """Classify repository adoption using only checked-in filesystem evidence."""
    manifest_path = root / ".murlocs" / "manifest.toml"
    lock_path = root / LOCK_PATH
    migration_path = root / MIGRATION_STATE
    legacy_path = root / ".stewards" / "manifest.toml"

    evidence: list[dict[str, str]] = []
    blockers: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []

    manifest_exists = manifest_path.is_file()
    lock_exists = lock_path.is_file()
    legacy_exists = legacy_path.is_file()
    migration_exists = migration_path.is_file()

    inventory_error: str | None = None
    try:
        inventory = inventory_repository(root)
    except (MurlocsError, OSError, ValueError, TypeError, KeyError) as exc:
        inventory_error = str(exc)
        inventory = {
            "instructions": [],
            "legacy_stewards": None,
            "ownership_conflicts": [],
        }

    if manifest_exists:
        _evidence(evidence, "murlocs_manifest", ".murlocs/manifest.toml", "manifest present")
    if lock_exists:
        _evidence(evidence, "ownership_lock", LOCK_PATH.as_posix(), "ownership lock present")
    if legacy_exists:
        _evidence(
            evidence,
            "legacy_manifest",
            ".stewards/manifest.toml",
            "legacy steward manifest present",
        )
    if migration_exists:
        _evidence(
            evidence,
            "migration_record",
            MIGRATION_STATE.as_posix(),
            "migration record present",
        )
    for item in inventory["instructions"]:
        _evidence(
            evidence,
            f"instruction_{item['generator']}",
            item["path"],
            f"{item['generator']}-identified {item['kind']}",
        )

    migration_status: str | None = None
    migration_error: str | None = None
    if migration_exists:
        try:
            migration_data = json.loads(migration_path.read_text(encoding="utf-8"))
            raw_status = migration_data.get("status")
            if raw_status not in {"adopted", "pruned", "rolled_back"}:
                raise ValueError(f"unsupported status {raw_status!r}")
            migration_status = str(raw_status)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            migration_error = str(exc)

    findings: list[Finding] = []
    manifest_error: str | None = None
    coverage_roots: list[str] = []
    if manifest_exists:
        try:
            manifest = load_manifest(root)
            coverage_roots = list(manifest.coverage_roots)
            findings = validate(manifest)
        except (MurlocsError, OSError, ValueError) as exc:
            manifest_error = str(exc)

    conflicts = list(inventory["ownership_conflicts"])
    for path in conflicts:
        _blocker(
            blockers,
            "unmanaged_agents_file",
            f"AGENTS.md is not identified as Murlocs-managed: {path}",
            [path],
        )

    ambiguity: list[tuple[str, str, list[str]]] = []
    if inventory_error:
        ambiguity.append(
            (
                "inventory_evidence_invalid",
                f"repository guidance inventory cannot be classified: {inventory_error}",
                [".stewards/manifest.toml" if legacy_exists else "."],
            )
        )
    if lock_exists and not manifest_exists:
        ambiguity.append(
            (
                "lock_without_manifest",
                "ownership lock exists without a manifest",
                [LOCK_PATH.as_posix()],
            )
        )
    if migration_exists and not manifest_exists:
        ambiguity.append(
            (
                "migration_without_manifest",
                "migration record exists without a manifest",
                [MIGRATION_STATE.as_posix()],
            )
        )
    if migration_error:
        ambiguity.append(
            (
                "invalid_migration_record",
                f"migration status requires manual review: {migration_error}",
                [MIGRATION_STATE.as_posix()],
            )
        )
    if manifest_exists and lock_exists and legacy_exists and migration_status not in {
        "adopted",
        "pruned",
    }:
        ambiguity.append(
            (
                "parallel_managed_and_legacy_networks",
                "managed and legacy networks are both present without an active migration",
                [".murlocs/manifest.toml", LOCK_PATH.as_posix(), ".stewards/manifest.toml"],
            )
        )

    if ambiguity:
        for blocker_id, message, paths in ambiguity:
            _blocker(blockers, blocker_id, message, paths)
        state = "ambiguous"
        _action(
            actions,
            "inspect_inventory",
            "murlocs inventory --repo .",
            False,
            True,
            "Review conflicting repository evidence before any ownership change.",
        )
    elif migration_status in {"adopted", "pruned"}:
        state = f"migration_{migration_status}"
        if migration_status == "adopted":
            if not legacy_exists:
                _blocker(
                    blockers,
                    "legacy_source_missing",
                    "adopted migration no longer has its live legacy source directory",
                    [MIGRATION_STATE.as_posix(), ".stewards"],
                )
            _action(
                actions,
                "preview_prune",
                "murlocs --dry-run prune --repo .",
                False,
                True,
                "Preview the legacy files that would move into the recoverable backup.",
            )
        else:
            _action(
                actions,
                "validate_managed_network",
                "murlocs check --repo .",
                False,
                False,
                "Validate the adopted network before deciding whether to retain or roll it back.",
            )
        _action(
            actions,
            "preview_rollback",
            "murlocs --dry-run rollback --repo .",
            False,
            True,
            "Preview exact restoration from the migration backup.",
        )
        if manifest_error:
            _blocker(
                blockers,
                "invalid_manifest_or_lock",
                manifest_error,
                [".murlocs/manifest.toml", LOCK_PATH.as_posix()],
            )
        else:
            _validation_blockers(blockers, findings)
    elif not manifest_exists and legacy_exists:
        state = "legacy_detected"
        _action(
            actions,
            "compare_legacy_projection",
            "murlocs diff --repo .",
            False,
            True,
            "Review semantic translation findings and rendered changes before importing.",
        )
    elif not manifest_exists and inventory["instructions"]:
        state = "user_owned"
        _action(
            actions,
            "inspect_existing_guidance",
            "murlocs inventory --repo .",
            False,
            True,
            "Review existing guidance ownership before creating or importing a manifest.",
        )
    elif not manifest_exists:
        state = "uninitialized"
        _evidence(
            evidence,
            "repository_root",
            ".",
            "no instruction network or Murlocs manifest detected",
        )
        _action(
            actions,
            "preview_initialization",
            "murlocs --dry-run init --repo .",
            False,
            True,
            "Preview the starter manifest, protocol, and root map.",
        )
    elif not lock_exists:
        state = "candidate_manifest"
        if manifest_error:
            _blocker(
                blockers,
                "invalid_manifest",
                manifest_error,
                [".murlocs/manifest.toml"],
            )
        else:
            _validation_blockers(blockers, [item for item in findings if item.code != "lock"])
        if legacy_exists:
            _action(
                actions,
                "compare_candidate",
                "murlocs diff --repo .",
                False,
                True,
                "Compare the candidate with the legacy network before adoption.",
            )
        else:
            _action(
                actions,
                "validate_candidate",
                "murlocs check --repo .",
                False,
                True,
                "Inspect candidate validation findings before the first compile.",
            )
    elif manifest_error or findings:
        state = "managed_invalid"
        if manifest_error:
            _blocker(
                blockers,
                "invalid_manifest_or_lock",
                manifest_error,
                [".murlocs/manifest.toml", LOCK_PATH.as_posix()],
            )
        else:
            _validation_blockers(blockers, findings)
        _action(
            actions,
            "inspect_validation",
            "murlocs check --repo .",
            False,
            True,
            "Inspect structural, proof, coverage, ownership, and drift findings.",
        )
    else:
        state = "managed_synchronized"
        _action(
            actions,
            "explain_target_guidance",
            "murlocs explain PATH --repo .",
            False,
            False,
            "Inspect the guidance chain for the next target path.",
        )

    return {
        "root": str(root),
        "state": state,
        "evidence": evidence,
        "blockers": blockers,
        "next_actions": actions,
        "coverage": {
            "state": "configured" if coverage_roots else "unconfigured",
            "roots": coverage_roots,
        },
        "semantic_correctness": "not_evaluated",
    }


def _evidence(items: list[dict[str, str]], evidence_id: str, path: str, detail: str) -> None:
    items.append({"id": evidence_id, "path": path, "detail": detail})


def _blocker(
    items: list[dict[str, Any]], blocker_id: str, message: str, evidence: list[str]
) -> None:
    items.append({"id": blocker_id, "message": message, "evidence": evidence})


def _action(
    items: list[dict[str, Any]],
    action_id: str,
    command: str,
    writes: bool,
    review_required: bool,
    reason: str,
) -> None:
    items.append(
        {
            "id": action_id,
            "command": command,
            "writes": writes,
            "review_required": review_required,
            "reason": reason,
        }
    )


def _validation_blockers(items: list[dict[str, Any]], findings: list[Finding]) -> None:
    for finding in findings:
        _blocker(
            items,
            f"validation_{finding.code.replace('-', '_')}",
            finding.message,
            [".murlocs/manifest.toml"],
        )
