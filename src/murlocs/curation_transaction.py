"""Recoverable transactions whose crash journals are treated as untrusted input."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from murlocs.errors import MurlocsError
from murlocs.layers import read_disk_sources
from murlocs.lockfile import sha256_bytes
from murlocs.paths import relative_posix, repo_path

TRANSACTION_DIRECTORY = ".murlocs/curation/.transaction"
CURATION_DIRECTORY = ".murlocs/curation"


@dataclass(frozen=True)
class FileUpdate:
    path: Path
    before: bytes
    after: bytes
    role: str
    proposal_id: str | None = None


@dataclass(frozen=True)
class FileGuard:
    path: Path
    before: bytes | None


@dataclass(frozen=True)
class TreeGuard:
    path: Path
    suffixes: tuple[str, ...]
    before_sha256: str


@dataclass(frozen=True)
class RecoveryPlan:
    expected_source: str
    proposal_ids: tuple[str, ...]
    operation: str
    journal_sha256: str | None
    updates: tuple[FileUpdate, ...]
    guards: tuple[FileGuard, ...]
    status: str


FailureHook = Callable[[str], None]


def transaction_pending(root: Path) -> bool:
    unresolved = root / TRANSACTION_DIRECTORY
    return unresolved.exists() or unresolved.is_symlink()


def plan_recovery(
    root: Path,
    *,
    expected_source: str,
    proposal_ids: tuple[str, ...],
) -> RecoveryPlan:
    """Validate an untrusted crash journal and preview an explicit rollback."""
    directory = _transaction_directory(root)
    if not directory.exists():
        raise MurlocsError("no interrupted curation transaction exists")
    metadata_path = directory / "transaction.json"
    if metadata_path.is_symlink():
        raise MurlocsError("curation transaction metadata may not be a symlink")
    if not metadata_path.exists():
        return RecoveryPlan(
            expected_source,
            proposal_ids,
            "incomplete",
            None,
            (),
            (),
            "remove incomplete staging without writing repository files",
        )
    try:
        metadata_bytes = metadata_path.read_bytes()
        data = json.loads(metadata_bytes.decode("utf-8"))
        _validate_metadata_shape(data)
        if data["source_path"] != expected_source:
            raise ValueError("journal source does not match the explicitly selected source")
        if tuple(data["proposal_ids"]) != proposal_ids:
            raise ValueError("journal proposal ids do not match the explicit recovery target")
        expected_operation = "supersede" if len(proposal_ids) == 2 else None
        if expected_operation is not None and data["operation"] != expected_operation:
            raise ValueError("two-record recovery requires a supersede journal")
        if expected_operation is None and data["operation"] not in {"promote", "prune"}:
            raise ValueError("one-record recovery requires a promote or prune journal")

        expected = [
            (expected_source, "source", None),
            *[
                (f"{CURATION_DIRECTORY}/{proposal_id}.toml", "record", proposal_id)
                for proposal_id in proposal_ids
            ],
        ]
        raw_updates = data["updates"]
        if len(raw_updates) != len(expected):
            raise ValueError("journal target count does not match the recovery operation")
        seen: set[Path] = set()
        for index, (raw, target_shape) in enumerate(zip(raw_updates, expected, strict=True)):
            expected_path, expected_role, expected_id = target_shape
            if (
                raw["path"],
                raw["role"],
                raw["proposal_id"],
            ) != target_shape:
                raise ValueError(f"journal update {index} does not match its exact target role")
            _reject_symlinks(root, expected_path, label="transaction target")
            target = repo_path(root, expected_path, field="transaction target")
            if target in seen:
                raise ValueError(f"journal repeats target: {expected_path}")
            seen.add(target)
            before_path = directory / f"{index}.before"
            after_path = directory / f"{index}.after"
            if before_path.is_symlink() or after_path.is_symlink():
                raise ValueError(f"journal image {index} may not be a symlink")
            if not before_path.is_file() or not after_path.is_file():
                raise ValueError(f"journal image {index} is missing or not a regular file")
            before = before_path.read_bytes()
            after = after_path.read_bytes()
            if sha256_bytes(before) != raw["before_sha256"]:
                raise ValueError(f"corrupt before image for {expected_path}")
            if sha256_bytes(after) != raw["after_sha256"]:
                raise ValueError(f"corrupt after image for {expected_path}")
    except (
        KeyError,
        TypeError,
        ValueError,
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise MurlocsError(f"invalid curation transaction journal: {exc}") from exc

    return RecoveryPlan(
        expected_source,
        proposal_ids,
        data["operation"],
        sha256_bytes(metadata_bytes),
        (),
        (),
        "journal validated; lifecycle semantics must authorize recovery",
    )


def apply_recovery(root: Path, plan: RecoveryPlan) -> None:
    """Apply only a semantically derived recovery plan after an exact journal recheck."""
    current = plan_recovery(
        root,
        expected_source=plan.expected_source,
        proposal_ids=plan.proposal_ids,
    )
    if (
        current.expected_source != plan.expected_source
        or current.proposal_ids != plan.proposal_ids
        or current.operation != plan.operation
        or current.journal_sha256 != plan.journal_sha256
    ):
        raise MurlocsError("curation recovery plan changed before apply; preview it again")
    if current.updates:
        raise AssertionError("journal inspection may never supply writable updates")
    expected_source_path = repo_path(root, plan.expected_source, field="recovery source")
    _require_guards(root, list(plan.guards))
    for update in plan.updates:
        if update.role != "source" or update.path != expected_source_path:
            raise MurlocsError("recovery may write only the explicitly active source")
        if update.path.read_bytes() != update.before:
            raise MurlocsError(
                "curation recovery target changed before apply: "
                + relative_posix(root, update.path)
            )
        _atomic_replace(update.path, update.after)
    directory = _transaction_directory(root)
    shutil.rmtree(directory)


def apply_transaction(
    root: Path,
    updates: tuple[FileUpdate, ...],
    *,
    operation: str,
    proposal_ids: tuple[str, ...],
    expected_source: str | None = None,
    guards: tuple[FileGuard, ...] = (),
    tree_guards: tuple[TreeGuard, ...] = (),
    failure_hook: FailureHook | None = None,
) -> None:
    if not updates:
        raise MurlocsError("curation transaction has no updates")
    if transaction_pending(root):
        raise MurlocsError(
            "an untrusted or interrupted curation transaction requires explicit recovery"
        )
    normalized = _normalize_updates(root, updates)
    normalized_guards = _normalize_guards(root, guards, normalized)
    normalized_tree_guards = _normalize_tree_guards(root, tree_guards)
    if len(normalized) == 1:
        if len(proposal_ids) != 1:
            raise MurlocsError("single-file curation write requires one proposal id")
        update = normalized[0]
        expected_record = _expected_record_path(root, proposal_ids[0])
        if (
            update.role != "record"
            or update.proposal_id != proposal_ids[0]
            or update.path != expected_record
        ):
            raise MurlocsError("single-file curation writes may update only their named record")
        if failure_hook is not None:
            failure_hook("before_commit")
        _require_guards(root, normalized_guards)
        _require_tree_guards(root, normalized_tree_guards)
        _require_before(root, update)
        _atomic_replace(update.path, update.after)
        if failure_hook is not None:
            failure_hook("after_write:0")
        return

    _validate_apply_shape(
        root, normalized, operation, proposal_ids, expected_source=expected_source
    )
    directory = _transaction_directory(root, require_exists=False)
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise MurlocsError("another curation transaction is active") from exc

    wrote_target = False
    try:
        metadata: dict[str, Any] = {
            "version": 1,
            "operation": operation,
            "proposal_ids": list(proposal_ids),
            "source_path": expected_source,
            "updates": [],
        }
        for index, update in enumerate(normalized):
            (directory / f"{index}.before").write_bytes(update.before)
            (directory / f"{index}.after").write_bytes(update.after)
            metadata["updates"].append(
                {
                    "path": relative_posix(root, update.path),
                    "role": update.role,
                    "proposal_id": update.proposal_id,
                    "before_sha256": sha256_bytes(update.before),
                    "after_sha256": sha256_bytes(update.after),
                }
            )
        _atomic_replace(
            directory / "transaction.json",
            (json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        )
        if failure_hook is not None:
            failure_hook("before_commit")
        _require_guards(root, normalized_guards)
        _require_tree_guards(root, normalized_tree_guards)
        for update in normalized:
            _require_before(root, update)
        for index, update in enumerate(normalized):
            _atomic_replace(update.path, update.after)
            wrote_target = True
            if failure_hook is not None:
                failure_hook(f"after_write:{index}")
        shutil.rmtree(directory)
    except Exception:
        if wrote_target:
            _rollback_trusted_plan(root, normalized)
        elif directory.exists():
            shutil.rmtree(directory)
        raise


def _normalize_updates(root: Path, updates: tuple[FileUpdate, ...]) -> list[FileUpdate]:
    seen: set[Path] = set()
    normalized: list[FileUpdate] = []
    for update in updates:
        relative = relative_posix(root, update.path)
        _reject_symlinks(root, relative, label="transaction target")
        target = repo_path(root, relative, field="transaction target")
        if target in seen:
            raise MurlocsError(f"curation transaction repeats path: {relative}")
        seen.add(target)
        normalized.append(
            FileUpdate(
                target,
                update.before,
                update.after,
                update.role,
                update.proposal_id,
            )
        )
    return normalized


def _normalize_guards(
    root: Path, guards: tuple[FileGuard, ...], updates: list[FileUpdate]
) -> list[FileGuard]:
    update_paths = {update.path for update in updates}
    seen: set[Path] = set()
    normalized: list[FileGuard] = []
    for guard in guards:
        relative = relative_posix(root, guard.path)
        _reject_symlinks(root, relative, label="preflight dependency")
        target = repo_path(root, relative, field="preflight dependency")
        if target in update_paths or target in seen:
            continue
        seen.add(target)
        normalized.append(FileGuard(target, guard.before))
    return normalized


def _normalize_tree_guards(root: Path, guards: tuple[TreeGuard, ...]) -> list[TreeGuard]:
    seen: set[Path] = set()
    normalized: list[TreeGuard] = []
    for guard in guards:
        relative = relative_posix(root, guard.path)
        _reject_symlinks(root, relative, label="coverage root")
        target = repo_path(root, relative, field="coverage root")
        if target in seen:
            continue
        seen.add(target)
        normalized.append(TreeGuard(target, tuple(sorted(guard.suffixes)), guard.before_sha256))
    return normalized


def _validate_apply_shape(
    root: Path,
    updates: list[FileUpdate],
    operation: str,
    proposal_ids: tuple[str, ...],
    *,
    expected_source: str | None,
) -> None:
    expected_records = 2 if operation == "supersede" else 1
    if operation not in {"promote", "prune", "supersede"}:
        raise MurlocsError(f"unsupported journaled curation operation: {operation}")
    if len(proposal_ids) != expected_records or len(updates) != expected_records + 1:
        raise MurlocsError("curation transaction target count does not match its operation")
    if updates[0].role != "source" or updates[0].proposal_id is not None:
        raise MurlocsError("curation transaction must begin with exactly one active source")
    if expected_source is None:
        raise MurlocsError("journaled curation transaction requires an expected active source")
    active_matches = [
        source for source in read_disk_sources(root).sources if source.path == expected_source
    ]
    if len(active_matches) != 1:
        raise MurlocsError("expected curation source is not exactly one active source")
    source_path = repo_path(root, expected_source, field="expected active source")
    if updates[0].path != source_path:
        raise MurlocsError("curation transaction source does not match the expected active source")
    record_ids = tuple(update.proposal_id for update in updates[1:])
    record_paths = tuple(update.path for update in updates[1:])
    expected_paths = tuple(_expected_record_path(root, item) for item in proposal_ids)
    if (
        any(update.role != "record" for update in updates[1:])
        or record_ids != proposal_ids
        or record_paths != expected_paths
    ):
        raise MurlocsError("curation transaction records do not match the named proposals")


def _expected_record_path(root: Path, proposal_id: str) -> Path:
    return repo_path(
        root,
        f"{CURATION_DIRECTORY}/{proposal_id}.toml",
        field="curation transaction record",
    )


def _validate_metadata_shape(data: Any) -> None:
    top_fields = {"version", "operation", "proposal_ids", "source_path", "updates"}
    update_fields = {
        "path",
        "role",
        "proposal_id",
        "before_sha256",
        "after_sha256",
    }
    if not isinstance(data, dict) or set(data) != top_fields:
        raise ValueError("top level has unsupported or missing fields")
    if data["version"] != 1:
        raise ValueError(f"unsupported transaction journal version: {data['version']!r}")
    if not isinstance(data["operation"], str):
        raise ValueError("operation must be a string")
    if not isinstance(data["source_path"], str):
        raise ValueError("source_path must be a string")
    if not isinstance(data["proposal_ids"], list) or any(
        not isinstance(item, str) for item in data["proposal_ids"]
    ):
        raise ValueError("proposal_ids must be a string array")
    if not isinstance(data["updates"], list) or not data["updates"]:
        raise ValueError("updates must be a non-empty array")
    for index, update in enumerate(data["updates"]):
        if not isinstance(update, dict) or set(update) != update_fields:
            raise ValueError(f"invalid update {index}")


def _rollback_trusted_plan(root: Path, updates: list[FileUpdate]) -> None:
    for update in reversed(updates):
        current = update.path.read_bytes()
        if current == update.before:
            continue
        if current != update.after:
            raise MurlocsError(
                "transaction rollback found unexpected external changes in "
                + relative_posix(root, update.path)
            )
        _atomic_replace(update.path, update.before)
    directory = _transaction_directory(root)
    shutil.rmtree(directory)


def _require_before(root: Path, update: FileUpdate) -> None:
    _reject_symlinks(root, relative_posix(root, update.path), label="transaction target")
    if not update.path.is_file() or update.path.read_bytes() != update.before:
        raise MurlocsError(
            "curation plan is stale; repository bytes changed before commit: "
            + relative_posix(root, update.path)
        )


def _require_guards(root: Path, guards: list[FileGuard]) -> None:
    for guard in guards:
        _reject_symlinks(root, relative_posix(root, guard.path), label="preflight dependency")
        current = guard.path.read_bytes() if guard.path.is_file() else None
        if current != guard.before:
            raise MurlocsError(
                "curation preflight dependency changed before commit: "
                + relative_posix(root, guard.path)
            )


def _require_tree_guards(root: Path, guards: list[TreeGuard]) -> None:
    for guard in guards:
        _reject_symlinks(root, relative_posix(root, guard.path), label="coverage root")
        if source_tree_sha256(root, guard.path, guard.suffixes) != guard.before_sha256:
            raise MurlocsError(
                "curation coverage topology changed before commit: "
                + relative_posix(root, guard.path)
            )


def source_tree_sha256(root: Path, path: Path, suffixes: tuple[str, ...]) -> str:
    """Digest source-bearing path topology without trusting file contents."""
    if not path.exists():
        return sha256_bytes(b"missing\n")
    entries = []
    candidates = [path] if path.is_file() else path.rglob("*")
    for candidate in candidates:
        if candidate.is_file() and candidate.suffix in suffixes:
            entries.append(relative_posix(root, candidate))
    return sha256_bytes(("\n".join(sorted(entries)) + "\n").encode("utf-8"))


def _transaction_directory(root: Path, *, require_exists: bool = True) -> Path:
    unresolved = root / TRANSACTION_DIRECTORY
    if unresolved.is_symlink():
        raise MurlocsError("curation transaction directory may not be a symlink")
    directory = repo_path(root, TRANSACTION_DIRECTORY, field="transaction directory")
    if require_exists and not directory.exists():
        raise MurlocsError("no interrupted curation transaction exists")
    if directory.exists() and not directory.is_dir():
        raise MurlocsError("curation transaction path must be a directory")
    return directory


def _reject_symlinks(root: Path, raw: str, *, label: str) -> None:
    candidate = Path(raw)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise MurlocsError(f"{label} must be a safe repository-relative path: {raw}")
    current = root
    for part in candidate.parts:
        current = current / part
        if current.is_symlink():
            raise MurlocsError(f"{label} may not traverse a symlink: {raw}")


def _atomic_replace(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
