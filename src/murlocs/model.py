from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Edge:
    type: str
    to: str
    what: str


@dataclass(frozen=True)
class OwnershipGroup:
    kind: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class Ownership:
    paths: tuple[str, ...] = ()
    groups: tuple[OwnershipGroup, ...] = ()

    @property
    def all_paths(self) -> tuple[str, ...]:
        return self.paths + tuple(path for group in self.groups for path in group.paths)


@dataclass(frozen=True)
class Judgment:
    advocate: tuple[str, ...] = ()
    do_not: tuple[str, ...] = ()
    serves: tuple[str, ...] = ()


@dataclass(frozen=True)
class Scope:
    id: str
    path: str
    map: str
    point_of_view: str
    owns: Ownership
    guardrails: tuple[str, ...] = ()
    edges: tuple[Edge, ...] = ()
    judgment: Judgment = Judgment()


@dataclass(frozen=True)
class Invariant:
    id: str
    scope: str
    statement: str
    severity: str
    verification: str
    enforced_by: str | None = None
    evidence_file: str | None = None
    anchor: str | None = None
    annotation: SourceAnnotation | None = None


@dataclass(frozen=True)
class SourceAnnotation:
    """A reviewed declaration for one inert source-annotation attachment."""

    identifier: str
    kind: str
    file: str
    version: str


@dataclass(frozen=True)
class Check:
    name: str
    invoke: str
    location: str
    proof_contains: str | None = None
    description: str = ""


@dataclass(frozen=True)
class LayerSource:
    """One ordered source file that contributes to the resolved manifest."""

    id: str
    kind: str
    path: str
    sha256: str
    owners: tuple[str, ...] = ()


@dataclass(frozen=True)
class Override:
    """A later layer replacing a value an earlier layer set."""

    subject: str
    field: str
    winner_layer: str
    shadowed_layer: str
    winner_value: str
    shadowed_value: str


@dataclass(frozen=True)
class Manifest:
    root: Path
    schema_version: int
    network: str
    protocol: str
    max_active_bytes: int
    pillars: tuple[str, ...]
    search_policy: tuple[str, ...]
    operating_rules: tuple[str, ...]
    stop_and_ask: tuple[str, ...]
    done_criteria: tuple[str, ...]
    coverage_roots: tuple[str, ...]
    source_suffixes: tuple[str, ...]
    coverage_exemptions: dict[str, str]
    require_scope_invariants: bool
    scopes: tuple[Scope, ...]
    invariants: tuple[Invariant, ...]
    checks: dict[str, Check] = field(default_factory=dict)
    require_layer_owners: bool = False
    validate_codeowners: bool = False
    layered: bool = False
    sources: tuple[LayerSource, ...] = ()
    scope_layers: dict[str, tuple[str, ...]] = field(default_factory=dict)
    invariant_layers: dict[str, str] = field(default_factory=dict)
    overrides: tuple[Override, ...] = ()

    def __hash__(self) -> int:
        # `frozen=True` asks the dataclass to synthesize `__hash__`, but four
        # fields (`coverage_exemptions`, `checks`, `scope_layers`,
        # `invariant_layers`) are plain dicts, so the synthesized version raises
        # `TypeError: unhashable type: 'dict'` the moment a manifest is used as a
        # set member or dict key. Fold those dicts into sorted item tuples so the
        # hash is total and stays consistent with the field-wise `__eq__`
        # (equal manifests hash equal); every other field is already an
        # immutable scalar, tuple, or frozen dataclass.
        return hash(
            (
                self.root,
                self.schema_version,
                self.network,
                self.protocol,
                self.max_active_bytes,
                self.pillars,
                self.search_policy,
                self.operating_rules,
                self.stop_and_ask,
                self.done_criteria,
                self.coverage_roots,
                self.source_suffixes,
                tuple(sorted(self.coverage_exemptions.items())),
                self.require_scope_invariants,
                self.scopes,
                self.invariants,
                tuple(sorted(self.checks.items())),
                self.require_layer_owners,
                self.validate_codeowners,
                self.layered,
                self.sources,
                tuple(sorted(self.scope_layers.items())),
                tuple(sorted(self.invariant_layers.items())),
                self.overrides,
            )
        )

    @property
    def manifest_path(self) -> Path:
        return self.root / ".murlocs" / "manifest.toml"

    def source(self, layer_id: str) -> LayerSource | None:
        for source in self.sources:
            if source.id == layer_id:
                return source
        return None

    def source_ids_for_scope(self, scope_id: str) -> tuple[str, ...]:
        """Return the ordered authored sources represented by a scope's map."""
        if not self.layered:
            return ()
        if scope_id == "root":
            # The root map summarizes the complete guidance network.
            return tuple(source.id for source in self.sources)
        return self.scope_layers.get(scope_id, ())

    def source_for_invariant(self, invariant_id: str) -> LayerSource | None:
        """Return the reviewed source that declares an invariant when known."""
        layer_id = self.invariant_layers.get(invariant_id)
        return self.source(layer_id) if layer_id is not None else None
