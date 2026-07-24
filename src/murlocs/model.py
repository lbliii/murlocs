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
    overrides: tuple[Override, ...] = ()

    @property
    def manifest_path(self) -> Path:
        return self.root / ".murlocs" / "manifest.toml"

    def source(self, layer_id: str) -> LayerSource | None:
        for source in self.sources:
            if source.id == layer_id:
                return source
        return None
