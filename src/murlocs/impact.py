"""Deterministic, read-only guidance review impact reporting."""

from __future__ import annotations

import re
import subprocess
import tomllib
from collections import Counter
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any

from murlocs.errors import MurlocsError
from murlocs.layers import LIST_FIELDS, read_disk_sources
from murlocs.lockfile import read_lock, sha256_bytes
from murlocs.model import Manifest, Scope

POLICY_VERSION = 2
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
    }


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
    """Compare source semantics with the Git blob recorded by the compile lock."""
    try:
        lock = read_lock(manifest.root)
    except (MurlocsError, OSError):
        return None
    if lock is None:
        return None
    locked = next((source for source in lock.sources if source.path == source_path), None)
    if locked is None:
        return None
    try:
        history = subprocess.run(
            ["git", "log", "--format=%H", "--all", "--", source_path],
            cwd=manifest.root,
            check=False,
            capture_output=True,
        )
        current_bytes = (manifest.root / source_path).read_bytes()
    except OSError:
        return None
    if history.returncode:
        return None
    baseline_bytes = None
    for raw_commit in history.stdout.splitlines():
        commit = raw_commit.decode("ascii", errors="ignore")
        if not commit:
            continue
        completed = subprocess.run(
            ["git", "show", f"{commit}:{source_path}"],
            cwd=manifest.root,
            check=False,
            capture_output=True,
        )
        if completed.returncode == 0 and sha256_bytes(completed.stdout) == locked.sha256:
            baseline_bytes = completed.stdout
            break
    if baseline_bytes is None:
        return None
    try:
        before = tomllib.loads(baseline_bytes.decode("utf-8"))
        after = tomllib.loads(current_bytes.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    return _fragment_changes_root_render(before, after)


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
        )
    except OSError:
        return False
    if completed.returncode:
        return False
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
