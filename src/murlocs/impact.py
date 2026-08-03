"""Deterministic, read-only guidance review impact reporting."""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any

from murlocs.errors import MurlocsError
from murlocs.model import Manifest, Scope

POLICY_VERSION = 1
REQUIRED_POLICY = (
    "A changed path is owned by a scope or names its generated map, guidance source, "
    "review protocol, manual evidence, or registered-check configuration."
)
RECOMMENDED_POLICY = (
    "A changed path falls inside the nearest non-root scope without declared ownership, "
    "or a required scope is connected by one declared edge."
)
UNAFFECTED_POLICY = (
    "No declared ownership, guidance source, proof, check configuration, scoped path, "
    "or one-hop edge relationship associates the change with the scope."
)


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
                "diff",
                "--no-ext-diff",
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
        )
    except OSError as exc:
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
) -> dict[str, Any]:
    """Classify guidance review impact without claiming semantic truth."""
    scopes_by_id = {scope.id: scope for scope in manifest.scopes}
    required: dict[str, set[str]] = {scope.id: set() for scope in manifest.scopes}
    recommended: dict[str, set[str]] = {scope.id: set() for scope in manifest.scopes}

    for changed in changed_paths:
        _classify_direct_path(manifest, changed, required, recommended)

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
    }


def _classify_direct_path(
    manifest: Manifest,
    changed: str,
    required: dict[str, set[str]],
    recommended: dict[str, set[str]],
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
        for scope in contributing or list(manifest.scopes):
            required[scope.id].add(
                f"{changed} changes contributing guidance layer {source.id}"
            )

    for scope in manifest.scopes:
        if changed == _clean(scope.map):
            required[scope.id].add(f"{changed} is the generated map for scope {scope.id}")
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
    layer_ids = manifest.scope_layers.get(scope.id, ())
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
