from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Edge:
    type: str
    to: str
    what: str


@dataclass(frozen=True)
class Scope:
    id: str
    path: str
    map: str
    point_of_view: str
    owns: tuple[str, ...]
    guardrails: tuple[str, ...] = ()
    edges: tuple[Edge, ...] = ()


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
    proof_contains: str
    description: str = ""


@dataclass(frozen=True)
class Manifest:
    root: Path
    schema_version: int
    network: str
    protocol: str
    max_active_bytes: int
    pillars: tuple[str, ...]
    operating_rules: tuple[str, ...]
    stop_and_ask: tuple[str, ...]
    done_criteria: tuple[str, ...]
    coverage_roots: tuple[str, ...]
    source_suffixes: tuple[str, ...]
    coverage_exemptions: dict[str, str]
    scopes: tuple[Scope, ...]
    invariants: tuple[Invariant, ...]
    checks: dict[str, Check] = field(default_factory=dict)

    @property
    def manifest_path(self) -> Path:
        return self.root / ".kodama" / "manifest.toml"
