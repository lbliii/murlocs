from __future__ import annotations

from pathlib import Path
from typing import Any

from murlocs.errors import MurlocsError
from murlocs.layers import resolve_manifest
from murlocs.model import (
    Check,
    Edge,
    Invariant,
    Judgment,
    LayerSource,
    Manifest,
    Override,
    Ownership,
    OwnershipGroup,
    Scope,
)

MANIFEST_TEMPLATE = """schema_version = 1
network = "{network}"
protocol = ".murlocs/PROTOCOL.md"
max_active_bytes = 24576

pillars = [
  "Repository guidance is local, layered, and reviewable.",
  "Every strong claim names how it is verified.",
]
search_policy = [
  "Read the root map before repository discovery.",
  "Open only the nearest scoped map for the path being investigated.",
]
operating_rules = [
  "Read the applicable AGENTS.md chain before editing.",
  "Keep generated maps concise; put durable detail in source documentation.",
]
stop_and_ask = [
  "The requested change conflicts with a declared invariant.",
  "A required verification command or its proof cannot be located.",
]
done_criteria = [
  "Relevant tests and checks pass.",
  "Murlocs reports no manifest, coverage, or drift errors.",
]

[coverage]
roots = []
source_suffixes = [".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java"]

[coverage.exemptions]

[policies]
require_scope_invariants = false

[[scopes]]
id = "root"
path = "."
map = "AGENTS.md"
point_of_view = "Repository-wide architecture, workflow, and integration boundaries."
owns = ["README.md", ".murlocs/manifest.toml", ".murlocs/PROTOCOL.md"]
guardrails = ["Prefer the smallest change that preserves declared invariants."]

[[invariants]]
id = "guidance-stays-verified"
scope = "root"
statement = "Generated guidance must match the checked-in Murlocs manifest."
severity = "critical"
verification = "manual"
evidence_file = ".murlocs/PROTOCOL.md"
anchor = "Use this protocol"
"""

PROTOCOL_TEMPLATE = """# Murlocs review protocol

Use this protocol when a change crosses a scope boundary or touches a critical invariant.

1. Read the applicable `AGENTS.md` chain from the repository root to the target.
2. Identify the scopes, invariants, and registered checks affected by the change.
3. Inspect the named proof before trusting a command or policy claim.
4. Run only checks authorized by the task and repository policy.
5. Report unresolved conflicts, missing proof, and accepted risk explicitly.
"""


def _required(data: dict[str, Any], key: str, context: str) -> Any:
    if key not in data:
        raise MurlocsError(f"missing {context}.{key}")
    return data[key]


def load_manifest(root: Path) -> Manifest:
    resolved = resolve_manifest(root)
    return parse_manifest_data(
        root,
        resolved.data,
        layered=resolved.layered,
        sources=resolved.sources,
        scope_layers=resolved.scope_layers,
        overrides=resolved.overrides,
    )


def parse_manifest_data(
    root: Path,
    data: dict[str, Any],
    *,
    layered: bool = False,
    sources: tuple[LayerSource, ...] = (),
    scope_layers: dict[str, tuple[str, ...]] | None = None,
    overrides: tuple[Override, ...] = (),
) -> Manifest:
    """Parse an already-loaded canonical manifest without reading or writing repository files."""
    try:
        coverage = data.get("coverage", {})
        policies = data.get("policies", {})
        exemptions = coverage.get("exemptions", {})
        if "judgment" in data and "judgments" in data:
            raise ValueError("manifest cannot define both judgment and judgments")
        judgments = data.get("judgments", data.get("judgment", {}))
        scopes = tuple(
            Scope(
                id=str(_required(item, "id", "scopes[]")),
                path=str(_required(item, "path", "scopes[]")),
                map=str(_required(item, "map", "scopes[]")),
                point_of_view=str(_required(item, "point_of_view", "scopes[]")),
                owns=_parse_ownership(item.get("owns", [])),
                guardrails=tuple(str(value) for value in item.get("guardrails", [])),
                edges=tuple(
                    Edge(
                        type=str(_required(edge, "type", "scopes[].edges[]")),
                        to=str(_required(edge, "to", "scopes[].edges[]")),
                        what=str(_required(edge, "what", "scopes[].edges[]")),
                    )
                    for edge in item.get("edges", [])
                ),
                judgment=_parse_judgment(judgments.get(str(item.get("id", "")), {})),
            )
            for item in data.get("scopes", [])
        )
        unknown_judgments = sorted(set(judgments) - {scope.id for scope in scopes})
        if unknown_judgments:
            raise ValueError(
                "judgments reference unknown scopes: " + ", ".join(unknown_judgments)
            )
        invariants = tuple(
            Invariant(
                id=str(_required(item, "id", "invariants[]")),
                scope=str(_required(item, "scope", "invariants[]")),
                statement=str(_required(item, "statement", "invariants[]")),
                severity=str(_required(item, "severity", "invariants[]")),
                verification=str(_required(item, "verification", "invariants[]")),
                enforced_by=_optional_string(item.get("enforced_by")),
                evidence_file=_optional_string(item.get("evidence_file")),
                anchor=_optional_string(item.get("anchor")),
            )
            for item in data.get("invariants", [])
        )
        checks = {
            str(name): Check(
                name=str(name),
                invoke=str(_required(item, "invoke", f"checks.{name}")),
                location=str(_required(item, "location", f"checks.{name}")),
                proof_contains=_optional_string(item.get("proof_contains")),
                description=str(item.get("description", "")),
            )
            for name, item in data.get("checks", {}).items()
        }
        return Manifest(
            root=root.resolve(),
            schema_version=int(_required(data, "schema_version", "manifest")),
            network=str(_required(data, "network", "manifest")),
            protocol=str(_required(data, "protocol", "manifest")),
            max_active_bytes=int(data.get("max_active_bytes", 24576)),
            pillars=tuple(str(value) for value in data.get("pillars", [])),
            search_policy=tuple(str(value) for value in data.get("search_policy", [])),
            operating_rules=tuple(str(value) for value in data.get("operating_rules", [])),
            stop_and_ask=tuple(str(value) for value in data.get("stop_and_ask", [])),
            done_criteria=tuple(str(value) for value in data.get("done_criteria", [])),
            coverage_roots=tuple(str(value) for value in coverage.get("roots", [])),
            source_suffixes=tuple(str(value) for value in coverage.get("source_suffixes", [])),
            coverage_exemptions={str(key): str(value) for key, value in exemptions.items()},
            require_scope_invariants=_boolean(
                policies.get("require_scope_invariants", False),
                "policies.require_scope_invariants",
            ),
            scopes=scopes,
            invariants=invariants,
            checks=checks,
            layered=layered,
            sources=sources,
            scope_layers=dict(scope_layers or {}),
            overrides=overrides,
        )
    except (TypeError, ValueError, AttributeError) as exc:
        raise MurlocsError(f"invalid manifest shape: {exc}") from exc


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value)


def _boolean(value: Any, context: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{context} must be a boolean")
    return value


def _parse_ownership(raw: Any) -> Ownership:
    if isinstance(raw, list):
        return Ownership(paths=tuple(str(value) for value in raw))
    if isinstance(raw, dict):
        if any(not isinstance(paths, list) for paths in raw.values()):
            raise TypeError("scopes[].owns categories must contain path arrays")
        return Ownership(
            groups=tuple(
                OwnershipGroup(
                    kind=str(kind),
                    paths=tuple(str(value) for value in paths),
                )
                for kind, paths in raw.items()
            )
        )
    raise TypeError("scopes[].owns must be an array or a table of path arrays")


def _parse_judgment(raw: Any) -> Judgment:
    if not isinstance(raw, dict):
        raise TypeError("judgments entries must be tables")
    unknown = sorted(set(raw) - {"advocate", "do_not", "serves"})
    if unknown:
        raise ValueError("judgment contains unsupported fields: " + ", ".join(unknown))
    return Judgment(
        advocate=tuple(str(value) for value in raw.get("advocate", [])),
        do_not=tuple(str(value) for value in raw.get("do_not", [])),
        serves=tuple(str(value) for value in raw.get("serves", [])),
    )


def render_manifest(network: str) -> str:
    escaped = network.replace("\\", "\\\\").replace('"', '\\"')
    return MANIFEST_TEMPLATE.format(network=escaped)
