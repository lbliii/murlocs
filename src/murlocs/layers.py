"""Resolve an ordered set of manifest layer files into one canonical manifest.

The root ``.murlocs/manifest.toml`` is always the base source. It may additionally
declare an ordered ``[[layers]]`` set of repository-relative fragment files. Each
fragment contributes list guidance, coverage, checks, scopes, invariants, and
judgments. Composition is deterministic: lists append in layer order with stable
exact deduplication, scope identity and output paths stay immutable, and duplicate
scopes, invariants, or checks are rejected unless the later declaration explicitly
opts into ``override``.

Single-file manifests (no declared layers) are a supported degenerate case and are
returned unchanged so their compiled output is byte-identical to prior releases.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from murlocs.errors import MurlocsError
from murlocs.lockfile import sha256_bytes
from murlocs.model import LayerSource, Override
from murlocs.paths import repo_path

ROOT_SOURCE_ID = "manifest"
ROOT_SOURCE_PATH = ".murlocs/manifest.toml"
LAYER_KINDS = ("base", "domain", "overlay")
LAYER_DECL_FIELDS = {"id", "kind", "path", "owners"}

LIST_FIELDS = (
    "pillars",
    "search_policy",
    "operating_rules",
    "stop_and_ask",
    "done_criteria",
)
# Fields a layer fragment may contribute. Global scalars and the layer list itself
# stay on the root control plane so output paths and identity remain immutable.
FRAGMENT_FIELDS = set(LIST_FIELDS) | {"coverage", "checks", "scopes", "invariants", "judgments"}
CONTROL_FIELDS = {
    "schema_version",
    "network",
    "protocol",
    "max_active_bytes",
    "policies",
    "layers",
    "owners",
}


@dataclass(frozen=True)
class ResolvedManifest:
    data: dict[str, Any]
    layered: bool
    sources: tuple[LayerSource, ...]
    scope_layers: dict[str, tuple[str, ...]]
    overrides: tuple[Override, ...]


def resolve_manifest(root: Path) -> ResolvedManifest:
    """Read the root manifest and compose any declared layers into one model."""
    manifest_path = root / ".murlocs" / "manifest.toml"
    try:
        raw = manifest_path.read_bytes()
    except FileNotFoundError as exc:
        raise MurlocsError(f"manifest not found: {manifest_path}") from exc
    try:
        root_data = tomllib.loads(raw.decode("utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise MurlocsError(f"invalid TOML in {manifest_path}: {exc}") from exc

    root_owners = _owners(root_data.get("owners", []), "manifest owners")
    root_source = LayerSource(
        id=ROOT_SOURCE_ID,
        kind="base",
        path=ROOT_SOURCE_PATH,
        sha256=sha256_bytes(raw),
        owners=root_owners,
    )

    decls = root_data.get("layers")
    if not decls:
        if decls is not None and not isinstance(decls, list):
            raise MurlocsError("manifest layers must be an array of tables")
        scope_layers = {
            str(scope.get("id", "")): (ROOT_SOURCE_ID,)
            for scope in root_data.get("scopes", [])
            if isinstance(scope, dict)
        }
        return ResolvedManifest(
            data=_strip_control(root_data),
            layered=False,
            sources=(root_source,),
            scope_layers=scope_layers,
            overrides=(),
        )

    return _resolve_layers(root, root_data, root_source, decls)


def _resolve_layers(
    root: Path,
    root_data: dict[str, Any],
    root_source: LayerSource,
    decls: Any,
) -> ResolvedManifest:
    if not isinstance(decls, list):
        raise MurlocsError("manifest layers must be an array of tables")

    sources = [root_source]
    seen_ids = {root_source.id}
    fragments: list[tuple[LayerSource, dict[str, Any]]] = [(root_source, root_data)]
    for index, decl in enumerate(decls):
        source, data = _load_layer(root, index, decl, seen_ids)
        sources.append(source)
        fragments.append((source, data))

    merged: dict[str, Any] = {
        "schema_version": root_data.get("schema_version"),
        "network": root_data.get("network"),
        "protocol": root_data.get("protocol"),
    }
    if "max_active_bytes" in root_data:
        merged["max_active_bytes"] = root_data["max_active_bytes"]
    if "policies" in root_data:
        merged["policies"] = root_data["policies"]

    state = _MergeState()
    for source, data in fragments:
        state.absorb(source, data)
    state.finish(merged)

    return ResolvedManifest(
        data=merged,
        layered=True,
        sources=tuple(sources),
        scope_layers={key: tuple(value) for key, value in state.scope_layers.items()},
        overrides=tuple(state.overrides),
    )


def _load_layer(
    root: Path,
    index: int,
    decl: Any,
    seen_ids: set[str],
) -> tuple[LayerSource, dict[str, Any]]:
    if not isinstance(decl, dict):
        raise MurlocsError(f"layers[{index}] must be a table")
    unknown = sorted(set(decl) - LAYER_DECL_FIELDS)
    if unknown:
        raise MurlocsError(f"layers[{index}] has unsupported fields: {', '.join(unknown)}")
    layer_id = str(decl.get("id") or "")
    if not layer_id:
        raise MurlocsError(f"layers[{index}] requires an id")
    if layer_id in seen_ids:
        raise MurlocsError(f"duplicate layer id: {layer_id}")
    seen_ids.add(layer_id)
    kind = str(decl.get("kind") or "")
    if kind not in LAYER_KINDS:
        raise MurlocsError(
            f"layer {layer_id} kind must be one of {', '.join(LAYER_KINDS)}"
        )
    relative = str(decl.get("path") or "")
    if not relative:
        raise MurlocsError(f"layer {layer_id} requires a path")
    target = repo_path(root, relative, field=f"layer {layer_id} path")
    if not target.is_file():
        raise MurlocsError(f"layer {layer_id} file not found: {relative}")
    raw = target.read_bytes()
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise MurlocsError(f"invalid TOML in {relative}: {exc}") from exc
    control = sorted(set(data) & CONTROL_FIELDS)
    if control:
        raise MurlocsError(
            f"layer {layer_id} may not set control-plane fields: {', '.join(control)}"
        )
    unknown_fragment = sorted(set(data) - FRAGMENT_FIELDS)
    if unknown_fragment:
        raise MurlocsError(
            f"layer {layer_id} has unsupported fields: {', '.join(unknown_fragment)}"
        )
    owners = _owners(decl.get("owners", []), f"layer {layer_id} owners")
    source = LayerSource(
        id=layer_id, kind=kind, path=relative, sha256=sha256_bytes(raw), owners=owners
    )
    return source, data


class _MergeState:
    def __init__(self) -> None:
        self.layer_paths: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {key: [] for key in LIST_FIELDS}
        self.coverage_roots: list[str] = []
        self.source_suffixes: list[str] = []
        self.exemptions: dict[str, tuple[str, str]] = {}
        self.checks: dict[str, tuple[str, dict[str, Any]]] = {}
        self.check_order: list[str] = []
        self.scopes: dict[str, dict[str, Any]] = {}
        self.scope_order: list[str] = []
        self.scope_layers: dict[str, list[str]] = {}
        self.invariants: dict[str, tuple[str, dict[str, Any]]] = {}
        self.invariant_order: list[str] = []
        self.judgments: dict[str, dict[str, list[str]]] = {}
        self.judgment_order: list[str] = []
        self.overrides: list[Override] = []

    def absorb(self, source: LayerSource, data: dict[str, Any]) -> None:
        self.layer_paths[source.id] = source.path
        for key in LIST_FIELDS:
            self.lists[key].extend(_strings(data.get(key, []), key))
        coverage = data.get("coverage", {})
        if not isinstance(coverage, dict):
            raise MurlocsError("coverage must be a table")
        self.coverage_roots.extend(_strings(coverage.get("roots", []), "coverage.roots"))
        self.source_suffixes.extend(
            _strings(coverage.get("source_suffixes", []), "coverage.source_suffixes")
        )
        for path, reason in coverage.get("exemptions", {}).items():
            path = str(path)
            reason = str(reason)
            existing = self.exemptions.get(path)
            if existing is not None and existing[1] != reason:
                raise MurlocsError(
                    f"conflicting coverage exemption for {path}: "
                    f"{existing[0]} vs {source.id}"
                )
            self.exemptions[path] = (source.id, reason)
        self._absorb_checks(source, data.get("checks", {}))
        self._absorb_scopes(source, data.get("scopes", []))
        self._absorb_invariants(source, data.get("invariants", []))
        self._absorb_judgments(source, data.get("judgment", data.get("judgments", {})))

    def _absorb_checks(self, source: LayerSource, raw: Any) -> None:
        if not isinstance(raw, dict):
            raise MurlocsError("checks must be a table")
        for name, value in raw.items():
            name = str(name)
            if not isinstance(value, dict):
                raise MurlocsError(f"check {name} must be a table")
            override = bool(value.get("override", False))
            clean = {k: v for k, v in value.items() if k != "override"}
            if name in self.checks:
                if not override:
                    raise MurlocsError(
                        f"duplicate check {name}; set override = true to replace it"
                    )
                prior_layer = self.checks[name][0]
                self.overrides.append(
                    Override(
                        subject=f"check:{name}",
                        field="definition",
                        winner_layer=source.id,
                        shadowed_layer=prior_layer,
                        winner_value=name,
                        shadowed_value=name,
                    )
                )
            else:
                self.check_order.append(name)
            self.checks[name] = (source.id, clean)

    def _absorb_scopes(self, source: LayerSource, raw: Any) -> None:
        if not isinstance(raw, list):
            raise MurlocsError("scopes must be an array of tables")
        for item in raw:
            if not isinstance(item, dict):
                raise MurlocsError("scope entries must be tables")
            scope_id = str(item.get("id") or "")
            if not scope_id:
                raise MurlocsError("scope entries require an id")
            override = bool(item.get("override", False))
            clean = {k: v for k, v in item.items() if k != "override"}
            if scope_id not in self.scopes:
                if override:
                    raise MurlocsError(
                        f"scope {scope_id} overrides nothing; no earlier layer defines it"
                    )
                self.scopes[scope_id] = clean
                self.scope_order.append(scope_id)
                self.scope_layers[scope_id] = [source.id]
            else:
                if not override:
                    raise MurlocsError(
                        f"duplicate scope {scope_id}; set override = true to layer it"
                    )
                self._override_scope(source, scope_id, clean)
                self.scope_layers[scope_id].append(source.id)

    def _override_scope(
        self, source: LayerSource, scope_id: str, incoming: dict[str, Any]
    ) -> None:
        base = self.scopes[scope_id]
        definer = self.scope_layers[scope_id][0]
        prior_layer = self.scope_layers[scope_id][-1]
        for immutable in ("path", "map"):
            if immutable in incoming and str(incoming[immutable]) != str(base.get(immutable)):
                raise MurlocsError(
                    f"scope {scope_id} may not change {immutable} "
                    f"({self._at(definer)} -> {self._at(source.id)}): "
                    f"{base.get(immutable)} -> {incoming[immutable]}"
                )
        if "point_of_view" in incoming and incoming["point_of_view"] != base.get(
            "point_of_view"
        ):
            self.overrides.append(
                Override(
                    subject=f"scope:{scope_id}",
                    field="point_of_view",
                    winner_layer=source.id,
                    shadowed_layer=prior_layer,
                    winner_value=str(incoming["point_of_view"]),
                    shadowed_value=str(base.get("point_of_view", "")),
                )
            )
            base["point_of_view"] = incoming["point_of_view"]
        base["guardrails"] = _dedupe(
            list(base.get("guardrails", []))
            + _strings(incoming.get("guardrails", []), "guardrails")
        )
        base_edges = list(base.get("edges", []))
        for edge in incoming.get("edges", []):
            if edge not in base_edges:
                base_edges.append(edge)
        if base_edges:
            base["edges"] = base_edges
        if "owns" in incoming:
            base["owns"] = _merge_owns(base.get("owns"), incoming["owns"], scope_id)

    def _absorb_invariants(self, source: LayerSource, raw: Any) -> None:
        if not isinstance(raw, list):
            raise MurlocsError("invariants must be an array of tables")
        for item in raw:
            if not isinstance(item, dict):
                raise MurlocsError("invariant entries must be tables")
            invariant_id = str(item.get("id") or "")
            if not invariant_id:
                raise MurlocsError("invariant entries require an id")
            override = bool(item.get("override", False))
            clean = {k: v for k, v in item.items() if k != "override"}
            if invariant_id in self.invariants:
                if not override:
                    raise MurlocsError(
                        f"duplicate invariant {invariant_id}; "
                        "set override = true to replace it"
                    )
                prior_layer = self.invariants[invariant_id][0]
                self.overrides.append(
                    Override(
                        subject=f"invariant:{invariant_id}",
                        field="definition",
                        winner_layer=source.id,
                        shadowed_layer=prior_layer,
                        winner_value=str(clean.get("statement", "")),
                        shadowed_value=str(self.invariants[invariant_id][1].get("statement", "")),
                    )
                )
            else:
                self.invariant_order.append(invariant_id)
            self.invariants[invariant_id] = (source.id, clean)

    def _absorb_judgments(self, source: LayerSource, raw: Any) -> None:
        if not isinstance(raw, dict):
            raise MurlocsError("judgments must be a table")
        for scope_id, value in raw.items():
            scope_id = str(scope_id)
            if not isinstance(value, dict):
                raise MurlocsError(f"judgment {scope_id} must be a table")
            merged = self.judgments.setdefault(scope_id, {})
            if scope_id not in self.judgment_order:
                self.judgment_order.append(scope_id)
            for field_name in ("advocate", "do_not", "serves"):
                if field_name in value:
                    merged[field_name] = _dedupe(
                        list(merged.get(field_name, []))
                        + _strings(value[field_name], f"judgment {scope_id}.{field_name}")
                    )

    def _at(self, layer_id: str) -> str:
        path = self.layer_paths.get(layer_id)
        return f"{layer_id}@{path}" if path else layer_id

    def finish(self, merged: dict[str, Any]) -> None:
        for key in LIST_FIELDS:
            merged[key] = _dedupe(self.lists[key])
        coverage: dict[str, Any] = {
            "roots": _dedupe(self.coverage_roots),
            "source_suffixes": _dedupe(self.source_suffixes),
            "exemptions": {path: reason for path, (_, reason) in self.exemptions.items()},
        }
        merged["coverage"] = coverage
        merged["checks"] = {name: self.checks[name][1] for name in self.check_order}
        merged["scopes"] = [self.scopes[scope_id] for scope_id in self.scope_order]
        merged["invariants"] = [
            self.invariants[invariant_id][1] for invariant_id in self.invariant_order
        ]
        merged["judgments"] = {
            scope_id: self.judgments[scope_id] for scope_id in self.judgment_order
        }


def _merge_owns(base: Any, incoming: Any, scope_id: str) -> Any:
    if isinstance(base, list) and isinstance(incoming, list):
        return _dedupe([*base, *incoming])
    if isinstance(base, dict) and isinstance(incoming, dict):
        result = {key: list(value) for key, value in base.items()}
        for key, value in incoming.items():
            result[key] = _dedupe(list(result.get(key, [])) + list(value))
        return result
    if base is None:
        return incoming
    raise MurlocsError(f"scope {scope_id} owns cannot mix array and table forms across layers")


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value)
        if text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _owners(value: Any, context: str) -> tuple[str, ...]:
    return tuple(_strings(value, context))


def _strings(value: Any, context: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise MurlocsError(f"{context} must be an array of strings")
    return list(value)


def _strip_control(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if key not in {"layers", "owners"}}
