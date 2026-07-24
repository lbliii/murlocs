"""Safe, deterministic add-scope rollout for selected directories.

A repository can start with a root-only map and introduce high-value directories one at
a time. Agent-assisted discovery may propose the point of view and owners, but this module
owns path validation, conflict handling, and writes: it previews a proposed domain layer,
registers it in the root layer order, declares the scoped ``AGENTS.md`` output, records
reasoned deferrals for source-bearing areas left out of the rollout, and reports exactly
which files would change. Existing unmanaged or modified generated files are never
overwritten.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from murlocs.errors import MurlocsError
from murlocs.layers import compose, read_disk_sources
from murlocs.lockfile import sha256_text
from murlocs.manifest import parse_manifest_data
from murlocs.model import LayerSource, Manifest
from murlocs.paths import relative_posix, repo_path
from murlocs.render import compile_manifest, render_outputs
from murlocs.verify import validate


@dataclass(frozen=True)
class ScopePlan:
    scope_id: str
    scope_path: str
    map_path: str
    layer_id: str
    layer_path: str
    layer_toml: str
    decl_toml: str
    owners: tuple[str, ...]
    deferrals: dict[str, str]
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    uncovered: list[str] = field(default_factory=list)


def plan_add_scope(
    root: Path,
    path: str,
    *,
    scope_id: str | None = None,
    point_of_view: str | None = None,
    owners: tuple[str, ...] = (),
    deferrals: dict[str, str] | None = None,
) -> tuple[ScopePlan, Manifest]:
    """Build and validate a proposed rollout without writing anything."""
    deferrals = dict(deferrals or {})
    scope_dir = repo_path(root, path, field="scope path")
    if not scope_dir.is_dir():
        raise MurlocsError(f"scope path is not a directory: {path}")
    scope_path = relative_posix(root, scope_dir)
    if scope_path == ".":
        raise MurlocsError("add-scope targets a subdirectory, not the repository root")

    resolved_id = scope_id or _slug(scope_path)
    disk = read_disk_sources(root)

    existing_scope_ids = {
        str(scope.get("id"))
        for fragment in disk.fragments
        for scope in fragment.get("scopes", [])
        if isinstance(scope, dict)
    }
    if resolved_id in existing_scope_ids:
        raise MurlocsError(f"scope already exists: {resolved_id}")
    layer_id = resolved_id
    if any(source.id == layer_id for source in disk.sources):
        raise MurlocsError(f"layer id already exists: {layer_id}")

    for defer_path, reason in deferrals.items():
        repo_path(root, defer_path, field="deferred path")
        if not str(reason).strip():
            raise MurlocsError(f"deferred path needs a reason: {defer_path}")

    map_path = f"{scope_path}/AGENTS.md"
    pov = point_of_view or f"Guidance for {scope_path}."
    layer_relative = f".murlocs/layers/{layer_id}.toml"

    fragment = {
        "scopes": [
            {
                "id": resolved_id,
                "path": scope_path,
                "map": map_path,
                "point_of_view": pov,
                "owns": [scope_path],
            }
        ],
    }
    if deferrals:
        fragment["coverage"] = {"exemptions": dict(deferrals)}

    new_source = LayerSource(
        id=layer_id,
        kind="domain",
        path=layer_relative,
        sha256="",  # not meaningful for the in-memory preview
        owners=tuple(owners),
    )
    resolved = compose(
        disk.root_data,
        [*disk.sources, new_source],
        [*disk.fragments, fragment],
    )
    manifest = parse_manifest_data(
        root,
        resolved.data,
        layered=True,
        sources=resolved.sources,
        scope_layers=resolved.scope_layers,
        overrides=resolved.overrides,
    )

    blocking = [item for item in validate(manifest) if item.code not in {"drift", "lock"}]
    coverage_blocking = [item for item in blocking if item.code != "coverage"]
    if coverage_blocking:
        messages = "; ".join(str(item) for item in coverage_blocking)
        raise MurlocsError(f"proposed rollout is not valid: {messages}")
    uncovered = [item.message for item in blocking if item.code == "coverage"]

    added, changed = _output_changes(root, manifest)

    layer_toml = _render_layer(resolved_id, scope_path, map_path, pov, deferrals)
    decl_toml = _render_decl(layer_id, layer_relative, owners)
    plan = ScopePlan(
        scope_id=resolved_id,
        scope_path=scope_path,
        map_path=map_path,
        layer_id=layer_id,
        layer_path=layer_relative,
        layer_toml=layer_toml,
        decl_toml=decl_toml,
        owners=tuple(owners),
        deferrals=deferrals,
        added=added,
        changed=changed,
        uncovered=uncovered,
    )
    return plan, manifest


def apply_add_scope(root: Path, plan: ScopePlan, manifest: Manifest) -> list[str]:
    """Write the layer, register it, and compile — after an ownership preflight."""
    layer_file = repo_path(root, plan.layer_path, field="layer path")
    if layer_file.exists():
        raise MurlocsError(f"refusing to overwrite existing layer file: {plan.layer_path}")
    # Preflight ownership on the proposed model so a half-applied rollout is impossible.
    _preflight_outputs(root, manifest)

    layer_file.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(layer_file, plan.layer_toml)
    try:
        manifest_path = root / ".murlocs" / "manifest.toml"
        text = manifest_path.read_text(encoding="utf-8").rstrip("\n")
        _write_atomic(manifest_path, text + "\n\n" + plan.decl_toml)
    except BaseException:
        layer_file.unlink(missing_ok=True)
        raise

    from murlocs.manifest import load_manifest

    return compile_manifest(load_manifest(root))


def _preflight_outputs(root: Path, manifest: Manifest) -> None:
    from murlocs.render import prepare_manifest

    prepare_manifest(manifest)


def _output_changes(root: Path, manifest: Manifest) -> tuple[list[str], list[str]]:
    outputs = render_outputs(manifest)
    added: list[str] = []
    changed: list[str] = []
    for relative, content in sorted(outputs.items()):
        target = repo_path(root, relative, field="map")
        if not target.exists():
            added.append(relative)
        elif sha256_text(target.read_text(encoding="utf-8")) != sha256_text(content):
            changed.append(relative)
    return added, changed


def _slug(scope_path: str) -> str:
    slug = scope_path.strip("/").replace("/", "-")
    return slug or "scope"


def _render_layer(
    scope_id: str,
    scope_path: str,
    map_path: str,
    pov: str,
    deferrals: dict[str, str],
) -> str:
    lines = [
        "[[scopes]]",
        f"id = {_quote(scope_id)}",
        f"path = {_quote(scope_path)}",
        f"map = {_quote(map_path)}",
        f"point_of_view = {_quote(pov)}",
        f"owns = [{_quote(scope_path)}]",
        "",
    ]
    if deferrals:
        lines.append("[coverage.exemptions]")
        for defer_path, reason in sorted(deferrals.items()):
            lines.append(f"{_quote(defer_path)} = {_quote(reason)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_decl(layer_id: str, layer_path: str, owners: tuple[str, ...]) -> str:
    lines = [
        "[[layers]]",
        f"id = {_quote(layer_id)}",
        'kind = "domain"',
        f"path = {_quote(layer_path)}",
    ]
    if owners:
        joined = ", ".join(_quote(owner) for owner in owners)
        lines.append(f"owners = [{joined}]")
    return "\n".join(lines) + "\n"


def _quote(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
