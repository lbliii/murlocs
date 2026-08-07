"""Deterministic, read-only guidance review impact reporting."""

from __future__ import annotations

import os
import re
import subprocess
import time
import tomllib
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from murlocs.errors import MurlocsError
from murlocs.layers import LIST_FIELDS, compose, read_disk_sources
from murlocs.lockfile import read_lock, sha256_bytes
from murlocs.manifest import parse_manifest_data
from murlocs.model import LayerSource, Manifest, Scope
from murlocs.source_annotations import (
    _EXCLUDED_PARTS,
    MAX_CANDIDATE_COMMENTS,
    MAX_DECLARED_FILES,
    MAX_FILE_BYTES,
    MAX_PATH_COMPONENTS,
    MAX_RESOLUTION_SECONDS,
    MAX_TOTAL_BYTES,
    _declared_file,
    _is_ignored,
    _read_declared_file,
    _scan_comments,
)

POLICY_VERSION = 3
GIT_SOURCE_HISTORY_LIMIT = 64
GIT_SOURCE_BLOB_LIMIT = 1024 * 1024
GIT_SOURCE_BATCH_LIMIT = 8 * 1024 * 1024
GIT_READ_TIMEOUT_SECONDS = 10
ANNOTATION_REVISION_SOURCE_LIMIT = 256
REQUIRED_POLICY = (
    "A changed path is owned by a scope or names its generated map, guidance source, "
    "review protocol, manual evidence, or registered-check configuration; guidance-map "
    "changes require every scope whose active chain contains that map."
)
RECOMMENDED_POLICY = (
    "A changed path falls inside the nearest non-root scope without declared ownership, "
    "or a required scope is connected by one declared edge."
)
UNAFFECTED_POLICY = (
    "No declared ownership, guidance source, proof, check configuration, scoped path, "
    "or one-hop edge relationship associates the change with the scope."
)


@dataclass(frozen=True)
class _AnnotationDeclaration:
    identifier: str
    invariant: str
    scope: str
    file: str
    kind: str
    version: str
    owners: tuple[str, ...]


@dataclass(frozen=True)
class _AnnotationAttachment:
    declaration: _AnnotationDeclaration
    line: int


@dataclass(frozen=True)
class _AnnotationSnapshot:
    declarations: dict[str, _AnnotationDeclaration]
    locations: dict[str, tuple[_AnnotationAttachment, ...]]


class _AnnotationSnapshotUnavailable(Exception):
    """A bounded current-source read could not establish attachment state."""


def changed_paths_from_revision(root: Path, revision_range: str) -> tuple[str, ...]:
    """Return repository-relative paths changed by a Git revision range."""
    if not revision_range.strip():
        raise MurlocsError("revision range must not be empty")
    if revision_range.lstrip().startswith("-"):
        raise MurlocsError("revision range must not be a Git option")
    try:
        completed = subprocess.run(
            [
                "git",
                "--no-lazy-fetch",
                "--no-pager",
                "--no-replace-objects",
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--name-only",
                "-z",
                "--diff-filter=ACDMRTUXB",
                "--no-renames",
                revision_range,
                "--",
            ],
            cwd=root,
            check=False,
            capture_output=True,
            env=_safe_git_env(),
            timeout=GIT_READ_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MurlocsError(f"could not inspect Git revision range: {exc}") from exc
    if completed.returncode:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise MurlocsError(
            f"could not inspect Git revision range {revision_range}: "
            f"{message or 'git diff failed'}"
        )
    return tuple(
        sorted(
            {
                _normalize_relative_path(root, raw.decode("utf-8", errors="surrogateescape"))
                for raw in completed.stdout.split(b"\0")
                if raw
            }
        )
    )


def normalize_changed_paths(root: Path, paths: Iterable[str]) -> tuple[str, ...]:
    """Normalize, validate, deduplicate, and sort an explicit changed-path set."""
    return tuple(sorted({_normalize_relative_path(root, path) for path in paths}))


def build_impact_report(
    manifest: Manifest,
    changed_paths: tuple[str, ...],
    *,
    revision_range: str | None,
    explicit_paths: tuple[str, ...] | None = None,
    revision_paths: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Classify guidance review impact without claiming semantic truth."""
    scopes_by_id = {scope.id: scope for scope in manifest.scopes}
    required: dict[str, set[str]] = {scope.id: set() for scope in manifest.scopes}
    recommended: dict[str, set[str]] = {scope.id: set() for scope in manifest.scopes}
    source_paths = {source.path for source in manifest.sources}
    source_change = any(path in source_paths for path in changed_paths)
    drifted_maps = _drifted_generated_maps(manifest) if source_change else ()
    stale_sources = _stale_source_paths_against_lock(manifest) if source_change else ()
    if explicit_paths is None and revision_paths is None:
        explicit_set = set(changed_paths) if revision_range is None else set()
        revision_set = set(changed_paths) if revision_range is not None else set()
    else:
        explicit_set = set(explicit_paths or ())
        revision_set = set(revision_paths or ())

    annotation_impact = _annotation_impact(
        manifest,
        revision_range=revision_range,
        explicit_paths=explicit_set,
        revision_paths=revision_set,
    )
    for route in annotation_impact["routes"]:
        _require_annotation_scope(
            manifest,
            route["scope"],
            required,
            route["reason"],
        )

    for changed in changed_paths:
        _classify_direct_path(
            manifest,
            changed,
            required,
            recommended,
            drifted_maps=drifted_maps,
            stale_sources=stale_sources,
            revision_range=revision_range,
            explicit=changed in explicit_set,
            from_revision=changed in revision_set,
        )

    directly_required = {scope_id for scope_id, reasons in required.items() if reasons}
    for scope_id in sorted(directly_required):
        scope = scopes_by_id[scope_id]
        for edge in scope.edges:
            if edge.to not in directly_required:
                recommended[edge.to].add(
                    f"edge {scope.id} -[{edge.type}]-> {edge.to}: {edge.what}"
                )
        for candidate in manifest.scopes:
            for edge in candidate.edges:
                if edge.to == scope_id and candidate.id not in directly_required:
                    recommended[candidate.id].add(
                        f"edge {candidate.id} -[{edge.type}]-> {scope.id}: {edge.what}"
                    )

    scope_payloads = []
    for scope in sorted(manifest.scopes, key=lambda item: item.id):
        if required[scope.id]:
            status = "required"
            reasons = sorted(required[scope.id])
        elif recommended[scope.id]:
            status = "recommended"
            reasons = sorted(recommended[scope.id])
        else:
            status = "unaffected"
            reasons = []
        scope_payloads.append(_scope_payload(manifest, scope, status, reasons))

    counts = {
        status: sum(1 for scope in scope_payloads if scope["status"] == status)
        for status in ("required", "recommended", "unaffected")
    }
    return {
        "ok": True,
        "schema_version": 1,
        "input": {
            "paths": list(changed_paths),
            "revision_range": revision_range,
        },
        "policy": {
            "version": POLICY_VERSION,
            "required": REQUIRED_POLICY,
            "recommended": RECOMMENDED_POLICY,
            "unaffected": UNAFFECTED_POLICY,
        },
        "summary": counts,
        "scopes": scope_payloads,
        "annotations": {
            "comparison": annotation_impact["comparison"],
            "changes": annotation_impact["changes"],
            "uncertainty": annotation_impact["uncertainty"],
        },
    }


def _require_annotation_scope(
    manifest: Manifest,
    scope_id: str | None,
    required: dict[str, set[str]],
    reason: str,
) -> None:
    """Route an attachment to its declared scope, or conservatively to all scopes."""
    if scope_id in required:
        assert scope_id is not None
        required[scope_id].add(reason)
        return
    for scope in manifest.scopes:
        required[scope.id].add(reason)


def _annotation_impact(
    manifest: Manifest,
    *,
    revision_range: str | None,
    explicit_paths: set[str],
    revision_paths: set[str],
) -> dict[str, Any]:
    """Classify finite declared attachments without inferring semantic truth.

    Explicit paths deliberately remain path-only: a hook's staged view has no
    portable historical baseline.  Revision comparison reads just the old
    manifest, its declared layers, and declared annotation files through Git's
    object database; it never asks Git to diff source text or run a driver.
    """
    declarations, declaration_error = _annotation_declarations(manifest)
    current, current_error = _annotation_snapshot_from_disk(manifest)
    declared_paths = {declaration.file for declaration in declarations.values()}
    routes: list[dict[str, str | None]] = []
    changes: list[dict[str, Any]] = []
    uncertainty: list[str] = []

    path_only = sorted(explicit_paths.intersection(declared_paths))
    for path in path_only:
        for declaration in sorted(
            (
                item
                for item in declarations.values()
                if item.file == path
            ),
            key=lambda item: (item.scope, item.invariant, item.identifier),
        ):
            routes.append(
                {
                    "scope": declaration.scope,
                    "reason": (
                        f"{path} has path-only evidence for source annotation "
                        f"{declaration.identifier} attached to invariant "
                        f"{declaration.invariant}; compare a safe revision to classify "
                        "the attachment"
                    ),
                }
            )

    if revision_range is None:
        if declaration_error is not None or current_error is not None:
            detail = declaration_error or current_error
            assert detail is not None
            uncertainty.append(detail)
            _route_annotation_uncertainty(manifest, declarations, routes, detail)
            return {
                "comparison": "uncertain",
                "changes": changes,
                "uncertainty": uncertainty,
                "routes": routes,
            }
        return {
            "comparison": "path-only" if path_only else "not-requested",
            "changes": changes,
            "uncertainty": uncertainty,
            "routes": routes,
        }

    if declaration_error is not None or current_error is not None:
        detail = declaration_error or current_error
        assert detail is not None
        uncertainty.append(detail)
        _route_annotation_uncertainty(manifest, declarations, routes, detail)
        return {
            "comparison": "uncertain",
            "changes": changes,
            "uncertainty": uncertainty,
            "routes": routes,
        }
    assert current is not None
    baseline, baseline_error = _annotation_snapshot_from_revision(manifest.root, revision_range)
    if baseline_error is not None:
        uncertainty.append(baseline_error)
        _route_annotation_uncertainty(manifest, declarations, routes, uncertainty[-1])
        return {
            "comparison": "uncertain",
            "changes": changes,
            "uncertainty": uncertainty,
            "routes": routes,
        }
    assert baseline is not None
    changes = _compare_annotation_snapshots(baseline, current)
    for change in changes:
        routes.append(
            {
                "scope": change["scope"],
                "reason": (
                    f"revision {revision_range} reports source annotation "
                    f"{change['id']} attachment {change['kind']} for invariant "
                    f"{change['invariant']}; attachment state changed, but this does "
                    "not assert that the invariant is semantically false"
                ),
            }
        )
    # A revision that mentions an active declared source but yields no attachment
    # delta still needs no special route: normal ownership and evidence rules
    # remain authoritative.  Keep the provenance marker to make that distinction
    # explicit to JSON consumers.
    comparison = "compared" if changes else "compared-no-attachment-change"
    if not changes and revision_paths.intersection(declared_paths):
        comparison = "compared-no-attachment-change"
    return {
        "comparison": comparison,
        "changes": changes,
        "uncertainty": uncertainty,
        "routes": routes,
    }


def _route_annotation_uncertainty(
    manifest: Manifest,
    declarations: dict[str, _AnnotationDeclaration],
    routes: list[dict[str, str | None]],
    detail: str,
) -> None:
    scoped = sorted({item.scope for item in declarations.values()})
    if scoped:
        routes.extend(
            {
                "scope": scope,
                "reason": (
                    "source annotation revision comparison is uncertain; "
                    f"{detail}; attachment state is not treated as unaffected"
                ),
            }
            for scope in scoped
        )
        return
    routes.extend(
        {
            "scope": scope.id,
            "reason": (
                "source annotation revision comparison is uncertain; "
                f"{detail}; attachment state is not treated as unaffected"
            ),
        }
        for scope in manifest.scopes
    )


def _compare_annotation_snapshots(
    before: _AnnotationSnapshot, after: _AnnotationSnapshot
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for identifier in sorted(set(before.declarations) | set(after.declarations)):
        old_declaration = before.declarations.get(identifier)
        new_declaration = after.declarations.get(identifier)
        old_locations = before.locations.get(identifier, ())
        new_locations = after.locations.get(identifier, ())
        chosen = new_declaration or old_declaration
        assert chosen is not None
        base = {
            "id": identifier,
            "invariant": chosen.invariant,
            "scope": chosen.scope,
            "owners": list(chosen.owners),
            "before": _attachment_locations(old_locations),
            "after": _attachment_locations(new_locations),
        }
        if old_declaration is None:
            changes.append({**base, "kind": "added"})
        elif new_declaration is None:
            changes.append({**base, "kind": "removed"})
        elif _declaration_key(old_declaration) != _declaration_key(new_declaration):
            changes.append({**base, "kind": "declaration-changed"})
        if len(new_locations) > 1 and len(new_locations) > len(old_locations):
            changes.append({**base, "kind": "duplicated"})
        if len(old_locations) == len(new_locations) == 1:
            if _attachment_locations(old_locations) != _attachment_locations(new_locations):
                changes.append({**base, "kind": "moved"})
        elif old_locations and not new_locations and new_declaration is not None:
            changes.append({**base, "kind": "removed"})
        elif not old_locations and new_locations and old_declaration is not None:
            changes.append({**base, "kind": "added"})
    return changes


def _attachment_locations(
    attachments: tuple[_AnnotationAttachment, ...]
) -> list[dict[str, int | str]]:
    return [
        {"file": item.declaration.file, "line": item.line}
        for item in sorted(attachments, key=lambda item: (item.declaration.file, item.line))
    ]


def _declaration_key(item: _AnnotationDeclaration) -> tuple[str, str, str, str, str, str]:
    return item.invariant, item.scope, item.file, item.kind, item.version, ",".join(item.owners)


def _annotation_snapshot_from_disk(
    manifest: Manifest,
) -> tuple[_AnnotationSnapshot | None, str | None]:
    started = time.monotonic()
    total_bytes = 0

    def read(path: str) -> bytes | None:
        nonlocal total_bytes
        if time.monotonic() - started > MAX_RESOLUTION_SECONDS:
            raise _AnnotationSnapshotUnavailable(
                "declared annotation source inspection exceeded its time budget"
            )
        candidate, boundary = _declared_file(manifest.root, path)
        if boundary is not None:
            if _confirmed_annotation_deletion(manifest.root, path):
                return None
            raise _AnnotationSnapshotUnavailable(
                f"declared annotation source {path} is unavailable or excluded"
            )
        assert candidate is not None
        raw, boundary = _read_declared_file(candidate)
        if boundary is not None or raw is None:
            raise _AnnotationSnapshotUnavailable(
                f"declared annotation source {path} is unavailable or excluded"
            )
        if len(raw) > MAX_FILE_BYTES or total_bytes + len(raw) > MAX_TOTAL_BYTES:
            raise _AnnotationSnapshotUnavailable(
                "declared annotation source inspection exceeded its byte budget"
            )
        total_bytes += len(raw)
        return raw

    try:
        return _annotation_snapshot(manifest, read, started=started)
    except _AnnotationSnapshotUnavailable as exc:
        return None, str(exc)


def _annotation_snapshot_from_revision(
    root: Path, revision_range: str
) -> tuple[_AnnotationSnapshot | None, str | None]:
    revision = _revision_baseline(root, revision_range)
    if revision is None:
        return None, "the requested Git baseline is unavailable or unsupported"
    historical, error = _historical_manifest(root, revision)
    if error is not None:
        return None, error
    assert historical is not None
    paths = sorted(
        {item.annotation.file for item in historical.invariants if item.annotation is not None}
    )
    blobs = _git_revision_blobs(root, revision, paths)
    if blobs is None:
        return None, "declared annotation source blobs are unavailable, malformed, or over budget"
    return _annotation_snapshot(historical, lambda path: blobs.get(path))


def _annotation_snapshot(
    manifest: Manifest, read: Any, *, started: float | None = None
) -> tuple[_AnnotationSnapshot | None, str | None]:
    declarations, declaration_error = _annotation_declarations(manifest)
    if declaration_error is not None:
        return None, declaration_error
    locations: dict[str, list[_AnnotationAttachment]] = {
        identifier: [] for identifier in declarations
    }
    by_file: dict[str, list[_AnnotationDeclaration]] = {}
    for declaration in declarations.values():
        by_file.setdefault(declaration.file, []).append(declaration)
    candidates = 0
    for path, expected in sorted(by_file.items()):
        if started is not None and time.monotonic() - started > MAX_RESOLUTION_SECONDS:
            return None, "declared annotation source inspection exceeded its time budget"
        raw = read(path)
        if raw is None:
            # A positively established current deletion is an attachment removal.
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None, f"declared annotation source {path} is not decodable"
        if text.startswith("\ufeff"):
            return None, f"declared annotation source {path} is not decodable"
        parsed, findings, count = _scan_comments(path, text)
        candidates += count
        if candidates > MAX_CANDIDATE_COMMENTS:
            return None, "declared annotation source inspection exceeded its marker budget"
        if findings:
            return None, f"declared annotation source {path} has unsupported or malformed forms"
        expected_ids = {item.identifier for item in expected}
        for annotation, location in parsed:
            if annotation.identifier not in expected_ids:
                return None, f"declared annotation source {path} contains an undeclared marker"
            declaration = declarations[annotation.identifier]
            locations[annotation.identifier].append(
                _AnnotationAttachment(declaration, location.line)
            )
    return (
        _AnnotationSnapshot(
            declarations=declarations,
            locations={
                identifier: tuple(sorted(items, key=lambda item: item.line))
                for identifier, items in locations.items()
            },
        ),
        None,
    )


def _annotation_declarations(
    manifest: Manifest,
) -> tuple[dict[str, _AnnotationDeclaration], str | None]:
    declarations: dict[str, _AnnotationDeclaration] = {}
    for invariant in manifest.invariants:
        annotation = invariant.annotation
        if annotation is None:
            continue
        source = manifest.source_for_invariant(invariant.id)
        declaration = _AnnotationDeclaration(
            identifier=annotation.identifier,
            invariant=invariant.id,
            scope=invariant.scope,
            file=annotation.file,
            kind=annotation.kind,
            version=annotation.version,
            owners=() if source is None else source.owners,
        )
        if declaration.identifier in declarations or not _safe_annotation_path(declaration.file):
            return {}, "annotation declarations are malformed or have an unsafe source path"
        declarations[declaration.identifier] = declaration
    if len({item.file for item in declarations.values()}) > MAX_DECLARED_FILES:
        return {}, "annotation declarations exceed the declared-file budget"
    return declarations, None


def _confirmed_annotation_deletion(root: Path, path: str) -> bool:
    """Return true only when a confined path is provably absent, never excluded."""
    parts = PurePosixPath(path).parts
    if (
        not parts
        or len(parts) > MAX_PATH_COMPONENTS
        or any(part.casefold() in _EXCLUDED_PARTS for part in parts)
        or _is_ignored(root, Path(path))
    ):
        return False
    current = root
    for index, part in enumerate(parts):
        current /= part
        try:
            current.stat(follow_symlinks=False)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        if current.is_symlink() or not current.is_relative_to(root):
            return False
        if index < len(parts) - 1 and (current / ".git").exists():
            return False
        if index < len(parts) - 1 and not current.is_dir():
            return False
    return False


def _safe_annotation_path(path: str) -> bool:
    candidate = PurePosixPath(path)
    return (
        bool(path)
        and "\\" not in path
        and ":" not in path
        and "\0" not in path
        and "\n" not in path
        and "\r" not in path
        and not candidate.is_absolute()
        and ".." not in candidate.parts
    )


def _revision_baseline(root: Path, revision_range: str) -> str | None:
    """Resolve only the old side of an explicitly supplied Git comparison."""
    if not revision_range.strip() or revision_range.lstrip().startswith("-"):
        return None
    if "..." in revision_range:
        left, separator, right = revision_range.partition("...")
        if not separator or not left or not right or ".." in right:
            return None
        command = [
            "git",
            "--no-lazy-fetch",
            "--no-pager",
            "--no-replace-objects",
            "merge-base",
            "--",
            left,
            right,
        ]
    elif ".." in revision_range:
        left, separator, right = revision_range.partition("..")
        if not separator or not left or not right or ".." in right:
            return None
        command = [
            "git",
            "--no-lazy-fetch",
            "--no-pager",
            "--no-replace-objects",
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{left}^{{commit}}",
        ]
    else:
        command = [
            "git",
            "--no-lazy-fetch",
            "--no-pager",
            "--no-replace-objects",
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{revision_range}^{{commit}}",
        ]
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            check=False,
            capture_output=True,
            timeout=GIT_READ_TIMEOUT_SECONDS,
            env=_safe_git_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = completed.stdout.strip()
    if completed.returncode or re.fullmatch(rb"[0-9a-f]{40}|[0-9a-f]{64}", value) is None:
        return None
    return value.decode("ascii")


def _historical_manifest(root: Path, revision: str) -> tuple[Manifest | None, str | None]:
    root_blob = _git_revision_blobs(root, revision, [".murlocs/manifest.toml"])
    raw_root = None if root_blob is None else root_blob.get(".murlocs/manifest.toml")
    if raw_root is None:
        return None, "the baseline manifest is unavailable"
    try:
        root_data = tomllib.loads(raw_root.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None, "the baseline manifest is malformed"
    declarations = root_data.get("layers", [])
    if not isinstance(declarations, list) or len(declarations) > ANNOTATION_REVISION_SOURCE_LIMIT:
        return None, "the baseline layer declaration set is unsupported or over budget"
    paths: list[str] = []
    source_specs: list[tuple[str, str, str, tuple[str, ...]]] = []
    for item in declarations:
        if not isinstance(item, dict):
            return None, "the baseline layer declaration is malformed"
        source_id = item.get("id")
        kind = item.get("kind")
        path = item.get("path")
        owners = item.get("owners", [])
        if (
            not isinstance(source_id, str)
            or not isinstance(kind, str)
            or not isinstance(path, str)
            or not isinstance(owners, list)
            or not all(isinstance(owner, str) for owner in owners)
            or not _safe_annotation_path(path)
        ):
            return None, "the baseline layer declaration is malformed or unsafe"
        paths.append(path)
        source_specs.append((source_id, kind, path, tuple(owners)))
    blobs = _git_revision_blobs(root, revision, paths)
    if blobs is None or any(path not in blobs for path in paths):
        return None, "a baseline layer blob is unavailable, malformed, or over budget"
    try:
        sources = [
            LayerSource(
                id="manifest",
                kind="base",
                path=".murlocs/manifest.toml",
                sha256=sha256_bytes(raw_root),
                owners=tuple(str(item) for item in root_data.get("owners", [])),
            )
        ]
        fragments = [root_data]
        for source_id, kind, path, owners in source_specs:
            raw = blobs[path]
            fragments.append(tomllib.loads(raw.decode("utf-8")))
            sources.append(
                LayerSource(
                    id=source_id,
                    kind=kind,
                    path=path,
                    sha256=sha256_bytes(raw),
                    owners=owners,
                )
            )
        resolved = compose(root_data, sources, fragments)
        return (
            parse_manifest_data(
                root,
                resolved.data,
                layered=resolved.layered,
                sources=resolved.sources,
                scope_layers=resolved.scope_layers,
                invariant_layers=resolved.invariant_layers,
                overrides=resolved.overrides,
            ),
            None,
        )
    except (MurlocsError, UnicodeDecodeError, tomllib.TOMLDecodeError, TypeError, ValueError):
        return None, "the baseline manifest composition is malformed"


def _safe_git_env() -> dict[str, str]:
    git_env = os.environ.copy()
    git_env.update({"GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0"})
    return git_env


def _git_revision_blobs(
    root: Path, revision: str, paths: list[str]
) -> dict[str, bytes] | None:
    """Read finite Git blobs exactly, without diff, filters, drivers, or hooks."""
    if len(paths) > ANNOTATION_REVISION_SOURCE_LIMIT or any(
        not _safe_annotation_path(path) for path in paths
    ):
        return None
    if not paths:
        return {}
    object_names = tuple(f"{revision}:{path}" for path in paths)
    batch_input = ("\n".join(object_names) + "\n").encode("utf-8")
    try:
        checked = subprocess.run(
            [
                "git",
                "--no-lazy-fetch",
                "--no-pager",
                "--no-replace-objects",
                "cat-file",
                "--batch-check",
            ],
            cwd=root,
            check=False,
            capture_output=True,
            input=batch_input,
            env=_safe_git_env(),
            timeout=GIT_READ_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    metadata = _parse_git_batch_sizes(checked.stdout, object_names)
    sizes = tuple(item[1] for item in metadata or () if item is not None)
    if (
        checked.returncode
        or metadata is None
        or any(item is None for item in metadata)
        or any(size > GIT_SOURCE_BLOB_LIMIT for size in sizes)
        or sum(sizes) > GIT_SOURCE_BATCH_LIMIT
    ):
        return None
    try:
        completed = subprocess.run(
            [
                "git",
                "--no-lazy-fetch",
                "--no-pager",
                "--no-replace-objects",
                "cat-file",
                "--batch",
            ],
            cwd=root,
            check=False,
            capture_output=True,
            input=batch_input,
            env=_safe_git_env(),
            timeout=GIT_READ_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    contents = _parse_git_batch_blobs(completed.stdout, object_names, metadata)
    if completed.returncode or contents is None or any(item is None for item in contents):
        return None
    return {path: blob for path, blob in zip(paths, contents, strict=True) if blob is not None}


def _classify_direct_path(
    manifest: Manifest,
    changed: str,
    required: dict[str, set[str]],
    recommended: dict[str, set[str]],
    *,
    drifted_maps: tuple[str, ...],
    stale_sources: tuple[str, ...] | None,
    revision_range: str | None,
    explicit: bool,
    from_revision: bool,
) -> None:
    before = {scope_id: len(reasons) for scope_id, reasons in required.items()}
    sources = {source.path: source for source in manifest.sources}
    if changed == ".murlocs/manifest.toml":
        for scope in manifest.scopes:
            required[scope.id].add(f"{changed} changes the guidance control plane")
    elif changed == _clean(manifest.protocol):
        for scope in manifest.scopes:
            required[scope.id].add(f"{changed} changes the network review protocol")
    elif changed in sources:
        source = sources[changed]
        contributing = [
            scope
            for scope in manifest.scopes
            if source.id in manifest.scope_layers.get(scope.id, ())
        ]
        local_maps = {_clean(scope.map) for scope in contributing}
        affected_maps = set(drifted_maps).intersection(local_maps)
        root_scope = next((scope for scope in manifest.scopes if scope.id == "root"), None)
        root_map = None if root_scope is None else _clean(root_scope.map)
        revision_global = bool(
            from_revision
            and revision_range is not None
            and _revision_mentions_global_guidance(manifest.root, revision_range, changed)
        )
        explicit_global = explicit and _source_has_global_guidance(manifest, changed)
        source_stale = None if stale_sources is None else changed in stale_sources
        workspace_global = (
            _workspace_source_changes_root_render(manifest, changed)
            if explicit and root_map in drifted_maps and source_stale is not False
            else None
        )
        uncertain_root = bool(
            explicit
            and root_map in drifted_maps
            and source_stale is not False
            and workspace_global is None
            and (stale_sources is None or len(stale_sources) != 1)
        )
        if root_map is not None and (
            revision_global
            or (
                explicit
                and (
                    (
                        explicit_global
                        and (not affected_maps or source_stale is False)
                    )
                    or (
                        root_map in drifted_maps
                        and source_stale is not False
                        and workspace_global is not False
                    )
                )
            )
        ):
            affected_maps.add(root_map)
        if affected_maps:
            for changed_map in sorted(affected_maps):
                reason = f"{changed} changes guidance layer {source.id}, affecting {changed_map}"
                if changed_map == root_map and uncertain_root:
                    reason = (
                        f"{changed} is one of multiple or untracked stale guidance sources; "
                        "root-map drift cannot be attributed more narrowly"
                    )
                _require_guidance_chain_scopes(
                    manifest,
                    changed_map,
                    required,
                    reason,
                )
        else:
            for scope in contributing or list(manifest.scopes):
                required[scope.id].add(
                    f"{changed} changes contributing guidance layer {source.id}"
                )

    for scope in manifest.scopes:
        if changed == _clean(scope.map):
            _require_guidance_chain_scopes(
                manifest,
                changed,
                required,
                f"{changed} is a generated map in the active guidance chain",
            )
        for owned in scope.owns.all_paths:
            if _contains(_clean(owned), changed):
                required[scope.id].add(f"{changed} is within owned path {_clean(owned)}")

    for invariant in manifest.invariants:
        if invariant.evidence_file and changed == _clean(invariant.evidence_file):
            required[invariant.scope].add(
                f"{changed} is evidence for invariant {invariant.id}"
            )
        if invariant.enforced_by:
            check = manifest.checks.get(invariant.enforced_by)
            if check is not None and changed == _clean(check.location):
                required[invariant.scope].add(
                    f"{changed} configures check {check.name} for invariant {invariant.id}"
                )

    if any(len(reasons) > before[scope_id] for scope_id, reasons in required.items()):
        return
    candidates = [
        scope
        for scope in manifest.scopes
        if _clean(scope.path) != "." and _contains(_clean(scope.path), changed)
    ]
    if candidates:
        deepest = max(len(PurePosixPath(_clean(scope.path)).parts) for scope in candidates)
        for scope in candidates:
            if len(PurePosixPath(_clean(scope.path)).parts) == deepest:
                recommended[scope.id].add(
                    f"{changed} is inside scope path {_clean(scope.path)} but not declared owned"
                )


def _require_guidance_chain_scopes(
    manifest: Manifest,
    changed_map: str,
    required: dict[str, set[str]],
    reason: str,
) -> None:
    for target in manifest.scopes:
        if changed_map in {_clean(scope.map) for scope in _guidance_chain(manifest, target.path)}:
            required[target.id].add(reason)


def _guidance_chain(manifest: Manifest, target_path: str) -> list[Scope]:
    cleaned_target = _clean(target_path)
    return sorted(
        [scope for scope in manifest.scopes if _contains(_clean(scope.path), cleaned_target)],
        key=lambda scope: (len(PurePosixPath(_clean(scope.path)).parts), scope.id),
    )


def _drifted_generated_maps(manifest: Manifest) -> tuple[str, ...]:
    """Return maps whose checked-in bytes differ from the current deterministic render."""
    from murlocs.render import render_outputs

    changed = []
    for raw, rendered in render_outputs(manifest).items():
        path = manifest.root / raw
        try:
            current = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            current = None
        except UnicodeDecodeError:
            current = None
        if current != rendered:
            changed.append(_clean(raw))
    return tuple(sorted(changed))


def _source_has_global_guidance(manifest: Manifest, source_path: str) -> bool:
    """Identify active source content that contributes to root guidance collections."""
    try:
        disk = read_disk_sources(manifest.root)
    except (MurlocsError, OSError):
        return False
    for source, fragment in zip(disk.sources, disk.fragments, strict=True):
        if source.path != source_path:
            continue
        return any(bool(fragment.get(field)) for field in LIST_FIELDS) or bool(
            fragment.get("checks")
        )
    return False


def _stale_source_paths_against_lock(manifest: Manifest) -> tuple[str, ...] | None:
    """Return sources changed since compilation, or None without complete evidence."""
    try:
        lock = read_lock(manifest.root)
    except (MurlocsError, OSError):
        return None
    if lock is None:
        return None
    locked = {source.path: source.sha256 for source in lock.sources}
    current = {source.path: source.sha256 for source in manifest.sources}
    if locked.keys() != current.keys():
        return None
    return tuple(sorted(path for path, digest in current.items() if locked[path] != digest))


def _workspace_source_changes_root_render(
    manifest: Manifest, source_path: str
) -> bool | None:
    """Compare source semantics with a bounded, batched locked Git baseline."""
    try:
        lock = read_lock(manifest.root)
    except (MurlocsError, OSError):
        return None
    if lock is None:
        return None
    locked = next((source for source in lock.sources if source.path == source_path), None)
    loaded = next((source for source in manifest.sources if source.path == source_path), None)
    if (
        locked is None
        or loaded is None
        or any(marker in source_path for marker in ("\0", "\n", "\r"))
    ):
        return None
    git_env = os.environ.copy()
    git_env.update({"GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0"})
    try:
        history = subprocess.run(
            [
                "git",
                "--no-lazy-fetch",
                "--no-pager",
                "--no-replace-objects",
                "rev-list",
                f"--max-count={GIT_SOURCE_HISTORY_LIMIT}",
                "--all",
                "--",
                f":(literal){source_path}",
            ],
            cwd=manifest.root,
            check=False,
            capture_output=True,
            env=git_env,
            timeout=GIT_READ_TIMEOUT_SECONDS,
        )
        current_bytes = (manifest.root / source_path).read_bytes()
    except (OSError, subprocess.TimeoutExpired):
        return None
    if history.returncode or sha256_bytes(current_bytes) != loaded.sha256:
        return None
    commits = _parse_git_commit_ids(history.stdout)
    if commits is None:
        return None
    object_names = tuple(f"{commit}:{source_path}" for commit in commits)
    batch_input = ("\n".join(object_names) + "\n").encode("utf-8")
    try:
        checked = subprocess.run(
            [
                "git",
                "--no-lazy-fetch",
                "--no-pager",
                "--no-replace-objects",
                "cat-file",
                "--batch-check",
            ],
            cwd=manifest.root,
            check=False,
            capture_output=True,
            input=batch_input,
            env=git_env,
            timeout=GIT_READ_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    metadata = _parse_git_batch_sizes(checked.stdout, object_names)
    present_sizes = tuple(
        entry[1] for entry in metadata or () if entry is not None
    )
    if (
        checked.returncode
        or metadata is None
        or not present_sizes
        or any(size > GIT_SOURCE_BLOB_LIMIT for size in present_sizes)
        or sum(present_sizes) > GIT_SOURCE_BATCH_LIMIT
    ):
        return None
    try:
        completed = subprocess.run(
            [
                "git",
                "--no-lazy-fetch",
                "--no-pager",
                "--no-replace-objects",
                "cat-file",
                "--batch",
            ],
            cwd=manifest.root,
            check=False,
            capture_output=True,
            input=batch_input,
            env=git_env,
            timeout=GIT_READ_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    blobs = _parse_git_batch_blobs(completed.stdout, object_names, metadata)
    if completed.returncode or blobs is None:
        return None
    baseline_bytes = next(
        (blob for blob in blobs if blob is not None and sha256_bytes(blob) == locked.sha256),
        None,
    )
    if baseline_bytes is None:
        return None
    try:
        before = tomllib.loads(baseline_bytes.decode("utf-8"))
        after = tomllib.loads(current_bytes.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    return _fragment_changes_root_render(before, after)


def _parse_git_commit_ids(output: bytes) -> tuple[str, ...] | None:
    """Require a nonempty sequence of exact lowercase Git object ids."""
    lines = output.splitlines()
    if not lines or any(
        re.fullmatch(rb"[0-9a-f]{40}|[0-9a-f]{64}", line) is None for line in lines
    ):
        return None
    return tuple(line.decode("ascii") for line in lines)


def _parse_git_size(raw: bytes) -> int | None:
    """Bound numeric parsing before integer conversion of untrusted Git output."""
    if not raw or len(raw) > 20 or not raw.isdigit():
        return None
    return int(raw)


def _parse_git_batch_sizes(
    output: bytes, object_names: tuple[str, ...]
) -> tuple[tuple[bytes, int] | None, ...] | None:
    """Parse exact `git cat-file --batch-check` output before reading content."""
    lines = output.splitlines()
    if len(lines) != len(object_names):
        return None
    metadata: list[tuple[bytes, int] | None] = []
    for line, object_name in zip(lines, object_names, strict=True):
        if line == object_name.encode("utf-8") + b" missing":
            metadata.append(None)
            continue
        fields = line.split(b" ")
        size = _parse_git_size(fields[2]) if len(fields) == 3 else None
        if (
            len(fields) != 3
            or re.fullmatch(rb"[0-9a-f]{40}|[0-9a-f]{64}", fields[0]) is None
            or fields[1] != b"blob"
            or size is None
        ):
            return None
        metadata.append((fields[0], size))
    return tuple(metadata)


def _parse_git_batch_blobs(
    output: bytes,
    object_names: tuple[str, ...],
    metadata: tuple[tuple[bytes, int] | None, ...],
) -> tuple[bytes | None, ...] | None:
    """Parse exact `git cat-file --batch` output without accepting partial results."""
    if len(metadata) != len(object_names):
        return None
    position = 0
    total_size = 0
    blobs: list[bytes | None] = []
    for object_name, expected in zip(object_names, metadata, strict=True):
        header_end = output.find(b"\n", position)
        if header_end < 0:
            return None
        header = output[position:header_end]
        position = header_end + 1
        if expected is None:
            if header != object_name.encode("utf-8") + b" missing":
                return None
            blobs.append(None)
            continue
        fields = header.split(b" ")
        size = _parse_git_size(fields[2]) if len(fields) == 3 else None
        if (
            len(fields) != 3
            or re.fullmatch(rb"[0-9a-f]{40}|[0-9a-f]{64}", fields[0]) is None
            or fields[0] != expected[0]
            or fields[1] != b"blob"
            or size is None
            or size != expected[1]
        ):
            return None
        total_size += size
        if size > GIT_SOURCE_BLOB_LIMIT or total_size > GIT_SOURCE_BATCH_LIMIT:
            return None
        content_end = position + size
        if content_end >= len(output) or output[content_end : content_end + 1] != b"\n":
            return None
        blobs.append(output[position:content_end])
        position = content_end + 1
    if position != len(output):
        return None
    return tuple(blobs)


def _revision_mentions_global_guidance(
    root: Path, revision_range: str, source_path: str
) -> bool:
    """Catch removal of the last global field by inspecting the already-authorized Git diff."""
    if not revision_range.strip() or revision_range.lstrip().startswith("-"):
        return False
    try:
        completed = subprocess.run(
            [
                "git",
                "--no-lazy-fetch",
                "--no-pager",
                "--no-replace-objects",
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "--unified=1000000",
                revision_range,
                "--",
                source_path,
            ],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            env=_safe_git_env(),
            timeout=GIT_READ_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    if completed.returncode:
        return True
    before_lines: list[str] = []
    after_lines: list[str] = []
    in_hunk = False
    for line in completed.stdout.splitlines():
        if line.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk or line.startswith("\\ No newline") or not line:
            continue
        marker, content = line[0], line[1:]
        if marker in {" ", "-"}:
            before_lines.append(content)
        if marker in {" ", "+"}:
            after_lines.append(content)
    try:
        before = tomllib.loads("\n".join(before_lines)) if before_lines else {}
        after = tomllib.loads("\n".join(after_lines)) if after_lines else {}
    except tomllib.TOMLDecodeError:
        before = after = None
    if before is not None and after is not None:
        return _fragment_changes_root_render(before, after)
    fields = "|".join(re.escape(field) for field in LIST_FIELDS)
    pattern = re.compile(
        rf"^[+-](?![+-])\s*(?:{fields})\s*=|"
        r"^[+-](?![+-])\s*\[checks(?:\.|\])|"
        r"^[+-](?![+-])\s*\[\[(?:scopes|invariants)\]\]"
    )
    return any(pattern.search(line) for line in completed.stdout.splitlines())


def _fragment_changes_root_render(before: dict[str, Any], after: dict[str, Any]) -> bool:
    """Compare the subset of one source fragment rendered into the root map."""
    if any(before.get(field) != after.get(field) for field in LIST_FIELDS):
        return True
    if before.get("checks") != after.get("checks"):
        return True

    def scopes(data: dict[str, Any]) -> tuple[tuple[str, str], ...]:
        return tuple(
            sorted(
                (str(item.get("id", "")), str(item.get("map", "")))
                for item in data.get("scopes", [])
                if isinstance(item, dict)
            )
        )

    def invariant_summary(data: dict[str, Any]) -> Counter[tuple[str, bool]]:
        return Counter(
            (
                str(item.get("scope", "")),
                item.get("verification") == "command",
            )
            for item in data.get("invariants", [])
            if isinstance(item, dict)
        )

    return scopes(before) != scopes(after) or invariant_summary(before) != invariant_summary(
        after
    )


def _scope_payload(
    manifest: Manifest,
    scope: Scope,
    status: str,
    reasons: list[str],
) -> dict[str, Any]:
    chain = sorted(
        (
            candidate
            for candidate in manifest.scopes
            if _contains(_clean(candidate.path), _clean(scope.path))
        ),
        key=lambda candidate: (len(PurePosixPath(_clean(candidate.path)).parts), candidate.id),
    )
    layer_ids = manifest.source_ids_for_scope(scope.id)
    layers = []
    for layer_id in layer_ids:
        source = manifest.source(layer_id)
        if source is not None:
            layers.append(
                {
                    "id": source.id,
                    "kind": source.kind,
                    "path": source.path,
                    "owners": list(source.owners),
                }
            )
    owners = sorted({owner for layer in layers for owner in layer["owners"]})
    invariants = []
    check_names: set[str] = set()
    for invariant in manifest.invariants:
        if invariant.scope != scope.id:
            continue
        invariants.append(
            {
                "id": invariant.id,
                "severity": invariant.severity,
                "statement": invariant.statement,
                "verification": invariant.verification,
                "enforced_by": invariant.enforced_by,
                "evidence_file": invariant.evidence_file,
                "anchor": invariant.anchor,
            }
        )
        if invariant.enforced_by:
            check_names.add(invariant.enforced_by)
    checks = [
        {
            "name": check.name,
            "invoke": check.invoke,
            "location": check.location,
            "description": check.description,
        }
        for name in sorted(check_names)
        if (check := manifest.checks.get(name)) is not None
    ]
    edges = [
        {"direction": "outgoing", "type": edge.type, "scope": edge.to, "what": edge.what}
        for edge in scope.edges
    ]
    edges.extend(
        {
            "direction": "incoming",
            "type": edge.type,
            "scope": candidate.id,
            "what": edge.what,
        }
        for candidate in manifest.scopes
        for edge in candidate.edges
        if edge.to == scope.id
    )
    return {
        "id": scope.id,
        "path": scope.path,
        "map": scope.map,
        "status": status,
        "reasons": reasons,
        "guidance_chain": [
            {"id": candidate.id, "map": candidate.map} for candidate in chain
        ],
        "layers": layers,
        "owners": owners,
        "invariants": invariants,
        "checks": checks,
        "edges": sorted(
            edges,
            key=lambda edge: (edge["direction"], edge["scope"], edge["type"], edge["what"]),
        ),
        "review_protocol": manifest.protocol,
    }


def _normalize_relative_path(root: Path, raw: str) -> str:
    value = raw.strip()
    if not value:
        raise MurlocsError("changed path must not be empty")
    candidate = Path(value)
    absolute = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        relative = absolute.relative_to(root.resolve())
    except ValueError as exc:
        raise MurlocsError(f"changed path is outside repository: {raw}") from exc
    normalized = relative.as_posix()
    return normalized or "."


def _clean(raw: str) -> str:
    value = PurePosixPath(raw).as_posix().rstrip("/")
    return value or "."


def _contains(parent: str, child: str) -> bool:
    if parent == ".":
        return True
    return child == parent or child.startswith(parent + "/")
