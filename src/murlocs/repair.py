"""Allowlisted, recoverable repair of Murlocs-managed generated guidance."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from murlocs.errors import MurlocsError
from murlocs.lockfile import LOCK_PATH, render_lock, sha256_bytes
from murlocs.model import Manifest
from murlocs.paths import repo_path
from murlocs.render import prepare_manifest
from murlocs.verify import Finding, validate

REPAIR_TRANSACTION_DIRECTORY = ".murlocs/repair/.transaction"


@dataclass(frozen=True)
class RepairUpdate:
    """One exact managed output replacement in a deterministic repair plan."""

    path: str
    before: bytes | None
    after: bytes


@dataclass(frozen=True)
class RepairPlan:
    """A preflighted, byte-exact repair plan with no repository writes."""

    root: Path
    updates: tuple[RepairUpdate, ...]

    @property
    def paths(self) -> list[str]:
        return [item.path for item in self.updates]


class RepairUnavailable(MurlocsError):
    """Repair was refused because the findings are not mechanically safe."""

    def __init__(self, manifest: Manifest, findings: list[Finding]) -> None:
        super().__init__("repair is available only for preflight-safe generated guidance drift")
        self.manifest = manifest
        self.findings = findings


class RepairRecoveryRequired(MurlocsError):
    """A pending repair journal must be resolved before normal repair."""


def plan_repair(manifest: Manifest) -> RepairPlan:
    """Plan only drift/lock repairs whose managed outputs pass ownership preflight."""
    if repair_transaction_pending(manifest.root):
        raise RepairRecoveryRequired(
            "an interrupted repair requires `murlocs repair --recover` before another repair"
        )
    findings = validate(manifest)
    if any(item.code not in {"drift", "lock"} for item in findings):
        raise RepairUnavailable(manifest, findings)
    outputs = prepare_manifest(manifest)
    expected: dict[str, bytes] = {
        relative: content.encode("utf-8") for relative, content in outputs.items()
    }
    expected[LOCK_PATH.as_posix()] = render_lock(
        manifest.manifest_path.read_bytes(), outputs, manifest.sources
    ).encode("utf-8")
    updates: list[RepairUpdate] = []
    for relative in sorted(expected):
        target = _target(manifest.root, relative)
        before = _read_target(target)
        if before != expected[relative]:
            updates.append(RepairUpdate(relative, before, expected[relative]))
    return RepairPlan(manifest.root, tuple(updates))


def apply_repair(plan: RepairPlan) -> list[str]:
    """Apply a current plan as a recoverable all-or-revert transaction."""
    current = plan_repair_from_root(plan.root)
    if current != plan:
        raise MurlocsError("repair plan changed before apply; preview the current repository again")
    if not plan.updates:
        return []

    directory = _repair_directory(plan.root)
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise MurlocsError("another repair transaction is active; recover it first") from exc

    written: list[RepairUpdate] = []
    try:
        _write_journal(directory, plan)
        for update in plan.updates:
            target = _target(plan.root, update.path)
            if _read_target(target) != update.before:
                raise MurlocsError(f"repair target changed before apply: {update.path}")
            _atomic_write(target, update.after)
            written.append(update)
        shutil.rmtree(directory)
    except BaseException:
        try:
            _restore_written(plan.root, written)
        except BaseException:
            # The journal remains an actionable, exact recovery state.
            raise
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return plan.paths


def plan_repair_from_root(root: Path) -> RepairPlan:
    """Load and preflight the current repository without executing registered checks."""
    from murlocs.manifest import load_manifest

    return plan_repair(load_manifest(root))


def repair_transaction_pending(root: Path) -> bool:
    """Return whether an untrusted interrupted repair journal exists."""
    directory = root / REPAIR_TRANSACTION_DIRECTORY
    return directory.exists() or directory.is_symlink()


def recover_repair(root: Path, *, dry_run: bool) -> tuple[str, list[str]]:
    """Finalize a complete repair journal or roll back an interrupted one exactly."""
    updates = _read_journal(root)
    states = [_read_target(_target(root, item.path)) for item in updates]
    if all(current == item.after for current, item in zip(states, updates, strict=True)):
        if not dry_run:
            shutil.rmtree(_repair_directory(root))
        return "finalize completed repair transaction", [item.path for item in updates]
    if any(
        current != item.before and current != item.after
        for current, item in zip(states, updates, strict=True)
    ):
        raise MurlocsError(
            "repair recovery target changed outside the transaction; "
            "leave the journal for manual remediation"
        )
    changed_updates = [
        item
        for item, current in zip(updates, states, strict=True)
        if current == item.after
    ]
    paths = [item.path for item in changed_updates]
    if not dry_run:
        _restore_written(root, changed_updates)
        shutil.rmtree(_repair_directory(root))
    return "roll back interrupted repair transaction", paths


def _repair_directory(root: Path) -> Path:
    directory = root / REPAIR_TRANSACTION_DIRECTORY
    if directory.is_symlink():
        raise MurlocsError("repair transaction directory may not be a symlink")
    return directory


def _target(root: Path, relative: str) -> Path:
    raw = root / relative
    if raw.is_symlink():
        raise MurlocsError(f"repair target may not be a symlink: {relative}")
    return repo_path(root, relative, field="repair target")


def _read_target(path: Path) -> bytes | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise MurlocsError(f"repair target is not a regular file: {path}")
    return path.read_bytes()


def _write_journal(directory: Path, plan: RepairPlan) -> None:
    metadata: dict[str, object] = {"version": 1, "updates": []}
    entries: list[dict[str, object]] = []
    for index, update in enumerate(plan.updates):
        before = update.before or b""
        (directory / f"{index}.before").write_bytes(before)
        (directory / f"{index}.after").write_bytes(update.after)
        entries.append(
            {
                "path": update.path,
                "before_exists": update.before is not None,
                "before_sha256": sha256_bytes(before),
                "after_sha256": sha256_bytes(update.after),
            }
        )
    metadata["updates"] = entries
    _atomic_write(
        directory / "transaction.json",
        (json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n").encode(),
    )


def _read_journal(root: Path) -> tuple[RepairUpdate, ...]:
    directory = _repair_directory(root)
    metadata_path = directory / "transaction.json"
    if not directory.is_dir() or metadata_path.is_symlink():
        raise MurlocsError("invalid repair transaction journal")
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
        if set(data) != {"version", "updates"} or data["version"] != 1:
            raise ValueError("unsupported metadata")
        entries = data["updates"]
        if not isinstance(entries, list) or not entries:
            raise ValueError("missing updates")
        allowed = _managed_paths(root)
        updates: list[RepairUpdate] = []
        for index, entry in enumerate(entries):
            if set(entry) != {"path", "before_exists", "before_sha256", "after_sha256"}:
                raise ValueError("invalid update metadata")
            path = entry["path"]
            if not isinstance(path, str) or path not in allowed:
                raise ValueError("journal target is not a managed output")
            if not isinstance(entry["before_exists"], bool):
                raise ValueError("invalid before existence")
            before_path = directory / f"{index}.before"
            after_path = directory / f"{index}.after"
            if any(path.is_symlink() or not path.is_file() for path in (before_path, after_path)):
                raise ValueError("journal image is missing or unsafe")
            before = before_path.read_bytes()
            after = after_path.read_bytes()
            if (
                sha256_bytes(before) != entry["before_sha256"]
                or sha256_bytes(after) != entry["after_sha256"]
            ):
                raise ValueError("journal image hash mismatch")
            updates.append(RepairUpdate(path, before if entry["before_exists"] else None, after))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MurlocsError(f"invalid repair transaction journal: {exc}") from exc
    if [item.path for item in updates] != sorted({item.path for item in updates}):
        raise MurlocsError("invalid repair transaction journal: paths are not unique and ordered")
    return tuple(updates)


def _managed_paths(root: Path) -> set[str]:
    from murlocs.manifest import load_manifest

    manifest = load_manifest(root)
    return {scope.map for scope in manifest.scopes} | {LOCK_PATH.as_posix()}


def _restore_written(root: Path, updates: list[RepairUpdate]) -> None:
    for update in reversed(updates):
        target = _target(root, update.path)
        if _read_target(target) != update.after:
            raise MurlocsError(f"repair target changed during recovery: {update.path}")
        if update.before is None:
            target.unlink(missing_ok=True)
        else:
            _atomic_write(target, update.before)


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
