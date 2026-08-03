"""Deterministic planning and transactional application of manifest layer splits.

This module only moves existing TOML values.  It does not infer or rewrite guidance.
The planner operates on a single-file manifest, produces an in-memory layered model,
and reports the mechanically observable differences before any repository write.
"""

from __future__ import annotations

import contextlib
import copy
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from murlocs.errors import MurlocsError
from murlocs.layers import (
    CONTROL_FIELDS,
    FRAGMENT_FIELDS,
    LAYER_KINDS,
    ROOT_SOURCE_ID,
    compose,
    read_disk_sources,
)
from murlocs.lockfile import LOCK_PATH, render_lock, sha256_bytes, sha256_text
from murlocs.manifest import load_manifest, parse_manifest_data
from murlocs.model import LayerSource, Manifest
from murlocs.paths import repo_path
from murlocs.render import prepare_manifest, render_outputs
from murlocs.rollout import CodeownersRequirement, codeowners_requirements_for
from murlocs.serialization import render_fragment_data, render_manifest_data
from murlocs.verify import Finding, validate


@dataclass(frozen=True)
class SplitTarget:
    scope_id: str
    layer_id: str
    kind: str
    owners: tuple[str, ...]


@dataclass(frozen=True)
class RenderedChange:
    path: str
    status: str
    provenance_only: bool
    before_bytes: int
    after_bytes: int


@dataclass(frozen=True)
class BudgetChange:
    scope: str
    before_bytes: int
    after_bytes: int
    max_active_bytes: int


@dataclass(frozen=True)
class SplitPlan:
    root_toml: str
    original_root_sha256: str
    layer_toml: dict[str, str]
    manifest: Manifest
    targets: tuple[SplitTarget, ...]
    moved: dict[str, tuple[str, ...]]
    root_edits: tuple[str, ...]
    semantic_changes: tuple[str, ...]
    order_only_changes: tuple[str, ...]
    decisions: tuple[str, ...]
    rendered_changes: tuple[RenderedChange, ...]
    budgets: tuple[BudgetChange, ...]
    codeowners_requirements: tuple[CodeownersRequirement, ...]
    blocking_findings: tuple[Finding, ...] = field(default_factory=tuple)


def parse_split_targets(entries: list[str]) -> tuple[SplitTarget, ...]:
    """Parse ``SCOPE=LAYER,KIND,OWNER...`` entries without guessing ownership."""
    targets: list[SplitTarget] = []
    for entry in entries:
        scope, separator, raw = entry.partition("=")
        parts = [part.strip() for part in raw.split(",")]
        if not separator or not scope.strip() or len(parts) < 2 or not all(parts[:2]):
            raise MurlocsError("scope split must be SCOPE=LAYER,KIND[,OWNER...]: " + entry)
        layer_id, kind, *owners = parts
        if kind not in LAYER_KINDS:
            raise MurlocsError(f"layer {layer_id} kind must be one of {', '.join(LAYER_KINDS)}")
        if any(not owner for owner in owners):
            raise MurlocsError(f"scope split contains an empty owner: {entry}")
        targets.append(SplitTarget(scope.strip(), layer_id, kind, tuple(owners)))
    if not targets:
        raise MurlocsError("at least one --scope SCOPE=LAYER,KIND[,OWNER...] is required")
    return tuple(targets)


def parse_assignments(entries: list[str], *, option: str) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for entry in entries:
        subject, separator, destination = entry.partition("=")
        if not separator or not subject.strip() or not destination.strip():
            raise MurlocsError(f"{option} assignment must be NAME=LAYER|root: {entry}")
        key = subject.strip()
        if key in assignments:
            raise MurlocsError(f"duplicate {option} assignment: {key}")
        assignments[key] = destination.strip()
    return assignments


def plan_split_layers(
    root: Path,
    targets: tuple[SplitTarget, ...],
    *,
    root_owners: tuple[str, ...] = (),
    check_assignments: dict[str, str] | None = None,
    coverage_root_assignments: dict[str, str] | None = None,
    exemption_assignments: dict[str, str] | None = None,
) -> SplitPlan:
    """Plan a semantic-preserving split of an existing single-file manifest."""
    disk = read_disk_sources(root)
    if disk.root_data.get("layers"):
        raise MurlocsError("split-layers only accepts a single-file manifest")
    original_manifest = load_manifest(root)
    original_raw = (root / ".murlocs" / "manifest.toml").read_bytes()
    root_data = copy.deepcopy(disk.root_data)
    _validate_lossless_shape(root_data)
    if "judgment" in root_data:
        if "judgments" in root_data:
            raise MurlocsError("manifest cannot define both judgment and judgments")
        root_data["judgments"] = root_data.pop("judgment")

    scopes = {
        str(item.get("id")): item for item in root_data.get("scopes", []) if isinstance(item, dict)
    }
    selected: dict[str, SplitTarget] = {}
    layers: dict[str, SplitTarget] = {}
    layer_order: list[str] = []
    portable_layer_ids: set[str] = set()
    for target in targets:
        if target.scope_id == "root":
            raise MurlocsError("the root scope must remain in the root manifest")
        if target.scope_id not in scopes:
            raise MurlocsError(f"unknown scope: {target.scope_id}")
        if target.scope_id in selected:
            raise MurlocsError(f"scope selected more than once: {target.scope_id}")
        portable_id = target.layer_id.casefold()
        if portable_id in portable_layer_ids:
            raise MurlocsError(f"duplicate or portable-path-colliding layer id: {target.layer_id}")
        if portable_id == ROOT_SOURCE_ID.casefold():
            raise MurlocsError(f"reserved layer id: {ROOT_SOURCE_ID}")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", target.layer_id) is None:
            raise MurlocsError(
                f"unsafe layer id {target.layer_id!r}; use letters, digits, dot, dash, "
                "or underscore"
            )
        layer_path = f".murlocs/layers/{target.layer_id}.toml"
        target_path = repo_path(root, layer_path, field=f"layer {target.layer_id} path")
        if target_path.exists():
            raise MurlocsError(f"refusing to overwrite existing layer file: {layer_path}")
        selected[target.scope_id] = target
        layers[target.layer_id] = target
        layer_order.append(target.layer_id)
        portable_layer_ids.add(portable_id)

    destinations = {"root", *layer_order}
    check_assignments = dict(check_assignments or {})
    coverage_root_assignments = dict(coverage_root_assignments or {})
    exemption_assignments = dict(exemption_assignments or {})
    _validate_destinations(destinations, check_assignments, "check")
    _validate_destinations(destinations, coverage_root_assignments, "coverage-root")
    _validate_destinations(destinations, exemption_assignments, "coverage-exemption")

    fragments: dict[str, dict[str, Any]] = {layer_id: {} for layer_id in layer_order}
    moved: dict[str, list[str]] = {layer_id: [] for layer_id in layer_order}
    decisions: list[str] = []

    kept_scopes: list[dict[str, Any]] = []
    for scope in root_data.get("scopes", []):
        scope_id = str(scope.get("id"))
        target = selected.get(scope_id)
        if target is None:
            kept_scopes.append(scope)
            continue
        fragments[target.layer_id].setdefault("scopes", []).append(scope)
        moved[target.layer_id].append(f"scope:{scope_id}")
    root_data["scopes"] = kept_scopes

    judgments = root_data.get("judgments", {})
    for scope_id in list(judgments):
        target = selected.get(str(scope_id))
        if target is not None:
            fragments[target.layer_id].setdefault("judgments", {})[scope_id] = judgments.pop(
                scope_id
            )
            moved[target.layer_id].append(f"judgment:{scope_id}")
    if not judgments:
        root_data.pop("judgments", None)

    kept_invariants: list[dict[str, Any]] = []
    invariant_destinations: dict[str, str] = {}
    for invariant in root_data.get("invariants", []):
        scope_id = str(invariant.get("scope"))
        target = selected.get(scope_id)
        destination = target.layer_id if target is not None else "root"
        invariant_destinations[str(invariant.get("id"))] = destination
        if target is None:
            kept_invariants.append(invariant)
        else:
            fragments[target.layer_id].setdefault("invariants", []).append(invariant)
            moved[target.layer_id].append(f"invariant:{invariant.get('id')}")
    root_data["invariants"] = kept_invariants

    checks = root_data.get("checks", {})
    unknown_checks = sorted(set(check_assignments) - set(checks))
    if unknown_checks:
        raise MurlocsError("unknown checks in assignments: " + ", ".join(unknown_checks))
    check_refs: dict[str, set[str]] = {str(name): set() for name in checks}
    for invariant in disk.root_data.get("invariants", []):
        if invariant.get("verification") == "command" and invariant.get("enforced_by"):
            destination = invariant_destinations.get(str(invariant.get("id")), "root")
            check_refs.setdefault(str(invariant["enforced_by"]), set()).add(destination)
    for name in list(checks):
        refs = check_refs.get(str(name), set())
        automatic = next(iter(refs)) if len(refs) == 1 else "root"
        destination = check_assignments.get(str(name), automatic)
        if destination != "root":
            fragments[destination].setdefault("checks", {})[name] = checks.pop(name)
            moved[destination].append(f"check:{name}")
        if str(name) in check_assignments:
            decisions.append(f"check:{name} explicitly assigned to {destination}")
        elif len(refs) != 1:
            reason = "unreferenced" if not refs else "shared across " + ", ".join(sorted(refs))
            decisions.append(f"check:{name} kept in root ({reason})")
    if not checks:
        root_data.pop("checks", None)

    coverage = root_data.setdefault("coverage", {})
    scope_paths = {scope_id: str(scopes[scope_id].get("path", ".")) for scope_id in selected}
    roots = list(coverage.get("roots", []))
    unknown_roots = sorted(set(coverage_root_assignments) - set(map(str, roots)))
    if unknown_roots:
        raise MurlocsError("unknown coverage roots in assignments: " + ", ".join(unknown_roots))
    kept_roots: list[str] = []
    for value in roots:
        raw = str(value)
        automatic = _scope_destination(raw, selected, scope_paths)
        destination = coverage_root_assignments.get(raw, automatic)
        if raw in coverage_root_assignments:
            decisions.append(f"coverage-root:{raw} explicitly assigned to {destination}")
        if destination == "root":
            kept_roots.append(value)
            if automatic == "root" and raw not in coverage_root_assignments:
                decisions.append(f"coverage-root:{raw} kept in root (shared or broad)")
        else:
            fragments[destination].setdefault("coverage", {}).setdefault("roots", []).append(value)
            moved[destination].append(f"coverage-root:{raw}")
    coverage["roots"] = kept_roots

    exemptions = coverage.get("exemptions", {})
    unknown_exemptions = sorted(set(exemption_assignments) - set(exemptions))
    if unknown_exemptions:
        raise MurlocsError(
            "unknown coverage exemptions in assignments: " + ", ".join(unknown_exemptions)
        )
    for path in list(exemptions):
        automatic = _scope_destination(str(path), selected, scope_paths)
        destination = exemption_assignments.get(str(path), automatic)
        if str(path) in exemption_assignments:
            decisions.append(
                f"coverage-exemption:{path} explicitly assigned to {destination}"
            )
        if destination != "root":
            fragments[destination].setdefault("coverage", {}).setdefault("exemptions", {})[path] = (
                exemptions.pop(path)
            )
            moved[destination].append(f"coverage-exemption:{path}")
        elif automatic == "root" and str(path) not in exemption_assignments:
            decisions.append(f"coverage-exemption:{path} kept in root (shared or broad)")

    if root_owners:
        root_data["owners"] = list(root_owners)
    root_data["layers"] = [
        {
            "id": layer_id,
            "kind": layers[layer_id].kind,
            "path": f".murlocs/layers/{layer_id}.toml",
            "owners": list(layers[layer_id].owners),
        }
        for layer_id in layer_order
    ]
    root_toml = render_manifest_data(root_data)
    layer_toml = {
        f".murlocs/layers/{layer_id}.toml": render_fragment_data(fragments[layer_id])
        for layer_id in layer_order
    }
    sources = [
        LayerSource(
            id=ROOT_SOURCE_ID,
            kind="base",
            path=".murlocs/manifest.toml",
            sha256=sha256_text(root_toml),
            owners=tuple(root_data.get("owners", [])),
        )
    ]
    fragment_list: list[dict[str, Any]] = [root_data]
    for layer_id in layer_order:
        path = f".murlocs/layers/{layer_id}.toml"
        sources.append(
            LayerSource(
                id=layer_id,
                kind=layers[layer_id].kind,
                path=path,
                sha256=sha256_text(layer_toml[path]),
                owners=layers[layer_id].owners,
            )
        )
        fragment_list.append(fragments[layer_id])
    resolved = compose(root_data, sources, fragment_list)
    candidate = parse_manifest_data(
        root,
        resolved.data,
        layered=True,
        sources=resolved.sources,
        scope_layers=resolved.scope_layers,
        overrides=resolved.overrides,
    )
    semantic_changes, order_only = _semantic_comparison(original_manifest, candidate)
    old_outputs = render_outputs(original_manifest)
    new_outputs = render_outputs(candidate)
    rendered_changes = _rendered_changes(old_outputs, new_outputs)
    budgets = _budget_changes(original_manifest, candidate, old_outputs, new_outputs)
    codeowners = codeowners_requirements_for(candidate)
    ignored = {"drift", "lock", "ownership"}
    blocking = tuple(item for item in validate(candidate) if item.code not in ignored)
    root_edits = (
        "remove selected scopes and their scope-local invariants/judgments",
        "register ordered layer sources",
        "retain shared controls unless explicitly assigned",
    )
    return SplitPlan(
        root_toml=root_toml,
        original_root_sha256=sha256_bytes(original_raw),
        layer_toml=layer_toml,
        manifest=candidate,
        targets=targets,
        moved={key: tuple(value) for key, value in moved.items()},
        root_edits=root_edits,
        semantic_changes=semantic_changes,
        order_only_changes=order_only,
        decisions=tuple(decisions),
        rendered_changes=rendered_changes,
        budgets=budgets,
        codeowners_requirements=codeowners,
        blocking_findings=blocking,
    )


def apply_split_layers(root: Path, plan: SplitPlan) -> list[str]:
    """Apply a preflighted plan as one recoverable multi-file transaction."""
    manifest_path = root / ".murlocs" / "manifest.toml"
    if sha256_bytes(manifest_path.read_bytes()) != plan.original_root_sha256:
        raise MurlocsError("manifest changed since the split plan was created")
    if plan.semantic_changes:
        raise MurlocsError("refusing to apply a split with semantic changes")
    if plan.blocking_findings:
        raise MurlocsError(
            "proposed split is not valid: "
            + "; ".join(str(item) for item in plan.blocking_findings)
        )
    unsatisfied = [item for item in plan.codeowners_requirements if not item.satisfied]
    if unsatisfied:
        raise MurlocsError(
            "CODEOWNERS requirements are not satisfied: "
            + "; ".join(item.entry for item in unsatisfied)
        )
    ownership = [item for item in validate(plan.manifest) if item.code == "ownership"]
    if ownership:
        raise MurlocsError(
            "source ownership requirements are not satisfied: "
            + "; ".join(str(item) for item in ownership)
        )
    for relative in plan.layer_toml:
        if repo_path(root, relative, field="layer path").exists():
            raise MurlocsError(f"refusing to overwrite existing layer file: {relative}")
    outputs = prepare_manifest(plan.manifest)
    writes: dict[str, str] = {
        ".murlocs/manifest.toml": plan.root_toml,
        **plan.layer_toml,
        **outputs,
    }
    writes[LOCK_PATH.as_posix()] = render_lock(
        plan.root_toml.encode("utf-8"), outputs, plan.manifest.sources
    )
    _commit_atomically(root, writes)
    return sorted(writes)


def _validate_destinations(
    destinations: set[str], assignments: dict[str, str], option: str
) -> None:
    unknown = sorted(set(assignments.values()) - destinations)
    if unknown:
        raise MurlocsError(f"{option} assignments name unknown destinations: {', '.join(unknown)}")


def _validate_lossless_shape(data: dict[str, Any]) -> None:
    """Reject fields the deterministic serializers cannot preserve verbatim."""
    _reject_unknown(data, CONTROL_FIELDS | FRAGMENT_FIELDS | {"judgment"}, "manifest")
    coverage = data.get("coverage", {})
    _reject_unknown(coverage, {"roots", "source_suffixes", "exemptions"}, "coverage")
    _reject_unknown(
        data.get("policies", {}),
        {"require_scope_invariants", "require_layer_owners", "validate_codeowners"},
        "policies",
    )
    for index, scope in enumerate(data.get("scopes", [])):
        _reject_unknown(
            scope,
            {"id", "path", "map", "point_of_view", "owns", "guardrails", "edges"},
            f"scopes[{index}]",
        )
        for edge_index, edge in enumerate(scope.get("edges", [])):
            _reject_unknown(edge, {"type", "to", "what"}, f"scopes[{index}].edges[{edge_index}]")
    for index, invariant in enumerate(data.get("invariants", [])):
        _reject_unknown(
            invariant,
            {
                "id",
                "scope",
                "statement",
                "severity",
                "verification",
                "enforced_by",
                "evidence_file",
                "anchor",
            },
            f"invariants[{index}]",
        )
    for name, check in data.get("checks", {}).items():
        _reject_unknown(
            check,
            {"invoke", "location", "proof_contains", "description"},
            f"checks.{name}",
        )
    judgments = data.get("judgments", data.get("judgment", {}))
    for scope_id, judgment in judgments.items():
        _reject_unknown(judgment, {"advocate", "do_not", "serves"}, f"judgments.{scope_id}")


def _reject_unknown(data: Any, allowed: set[str], context: str) -> None:
    if not isinstance(data, dict):
        return
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise MurlocsError(
            f"{context} has fields the split serializer cannot preserve: {', '.join(unknown)}"
        )


def _scope_destination(
    raw: str,
    selected: dict[str, SplitTarget],
    scope_paths: dict[str, str],
) -> str:
    matches = [
        (len(path.split("/")), selected[scope_id].layer_id)
        for scope_id, path in scope_paths.items()
        if path != "." and (raw == path or raw.startswith(path.rstrip("/") + "/"))
    ]
    if not matches:
        return "root"
    depth = max(item[0] for item in matches)
    layers = {layer for item_depth, layer in matches if item_depth == depth}
    return next(iter(layers)) if len(layers) == 1 else "root"


def _semantic_comparison(
    before: Manifest, after: Manifest
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    changes: list[str] = []
    order_only: list[str] = []
    scalar_fields = (
        "schema_version",
        "network",
        "protocol",
        "max_active_bytes",
        "pillars",
        "search_policy",
        "operating_rules",
        "stop_and_ask",
        "done_criteria",
        "coverage_exemptions",
        "require_scope_invariants",
        "require_layer_owners",
        "validate_codeowners",
    )
    for field_name in scalar_fields:
        if getattr(before, field_name) != getattr(after, field_name):
            changes.append(field_name)
    for field_name in ("coverage_roots", "source_suffixes"):
        old_values = getattr(before, field_name)
        new_values = getattr(after, field_name)
        if set(old_values) != set(new_values):
            changes.append(field_name)
        elif old_values != new_values:
            order_only.append(field_name)
    for label, old, new, key in (
        ("scopes", before.scopes, after.scopes, lambda value: value.id),
        ("invariants", before.invariants, after.invariants, lambda value: value.id),
        (
            "checks",
            tuple(before.checks.values()),
            tuple(after.checks.values()),
            lambda value: value.name,
        ),
    ):
        old_keyed = {key(value): value for value in old}
        new_keyed = {key(value): value for value in new}
        if old_keyed != new_keyed:
            changes.append(label)
        elif tuple(key(value) for value in old) != tuple(key(value) for value in new):
            order_only.append(label)
    return tuple(changes), tuple(order_only)


def _strip_provenance(text: str) -> str:
    marker = "\n## Provenance\n"
    return text.split(marker, 1)[0].rstrip() + "\n"


def _rendered_changes(before: dict[str, str], after: dict[str, str]) -> tuple[RenderedChange, ...]:
    changes: list[RenderedChange] = []
    for path in sorted(set(before) | set(after)):
        old = before.get(path, "")
        new = after.get(path, "")
        if path not in before:
            status = "added"
        elif path not in after:
            status = "removed"
        elif old == new:
            status = "unchanged"
        else:
            status = "changed"
        provenance_only = status == "changed" and _strip_provenance(old) == _strip_provenance(new)
        changes.append(
            RenderedChange(
                path,
                status,
                provenance_only,
                len(old.encode("utf-8")),
                len(new.encode("utf-8")),
            )
        )
    return tuple(changes)


def _active_bytes(manifest: Manifest, outputs: dict[str, str], scope_id: str) -> int:
    target = next(scope for scope in manifest.scopes if scope.id == scope_id)
    target_path = repo_path(manifest.root, target.path, field="scope path")
    total = 0
    for candidate in manifest.scopes:
        candidate_path = repo_path(manifest.root, candidate.path, field="scope path")
        try:
            target_path.relative_to(candidate_path)
        except ValueError:
            continue
        total += len(outputs[candidate.map].encode("utf-8"))
    return total


def _budget_changes(
    before: Manifest,
    after: Manifest,
    old_outputs: dict[str, str],
    new_outputs: dict[str, str],
) -> tuple[BudgetChange, ...]:
    return tuple(
        BudgetChange(
            scope.id,
            _active_bytes(before, old_outputs, scope.id),
            _active_bytes(after, new_outputs, scope.id),
            after.max_active_bytes,
        )
        for scope in after.scopes
    )


def _commit_atomically(root: Path, writes: dict[str, str]) -> None:
    originals: dict[Path, bytes | None] = {}
    staged: dict[Path, Path] = {}
    created_dirs: list[Path] = []
    replaced: list[Path] = []
    try:
        for relative, content in writes.items():
            target = repo_path(root, relative, field="transaction target")
            originals[target] = target.read_bytes() if target.exists() else None
            if not target.parent.exists():
                missing: list[Path] = []
                cursor = target.parent
                while not cursor.exists() and cursor != root:
                    missing.append(cursor)
                    cursor = cursor.parent
                target.parent.mkdir(parents=True)
                created_dirs.extend(reversed(missing))
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{target.name}.split.", dir=target.parent
            )
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
            staged[target] = Path(temporary)
        for target, temporary in staged.items():
            os.replace(temporary, target)
            replaced.append(target)
    except BaseException:
        for target in reversed(replaced):
            original = originals[target]
            if original is None:
                target.unlink(missing_ok=True)
            else:
                _restore_bytes(target, original)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)
        for directory in reversed(created_dirs):
            with contextlib.suppress(OSError):
                directory.rmdir()


def _restore_bytes(path: Path, content: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.restore.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
