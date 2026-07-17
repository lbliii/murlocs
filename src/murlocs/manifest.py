from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from murlocs.errors import MurlocsError
from murlocs.model import Check, Edge, Invariant, Manifest, Scope

MANIFEST_TEMPLATE = """schema_version = 1
network = "{network}"
protocol = ".murlocs/PROTOCOL.md"
max_active_bytes = 24576

pillars = [
  "Repository guidance is local, layered, and reviewable.",
  "Every strong claim names how it is verified.",
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
    path = root / ".murlocs" / "manifest.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MurlocsError(f"manifest not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise MurlocsError(f"invalid TOML in {path}: {exc}") from exc

    try:
        coverage = data.get("coverage", {})
        exemptions = coverage.get("exemptions", {})
        scopes = tuple(
            Scope(
                id=str(_required(item, "id", "scopes[]")),
                path=str(_required(item, "path", "scopes[]")),
                map=str(_required(item, "map", "scopes[]")),
                point_of_view=str(_required(item, "point_of_view", "scopes[]")),
                owns=tuple(str(value) for value in item.get("owns", [])),
                guardrails=tuple(str(value) for value in item.get("guardrails", [])),
                edges=tuple(
                    Edge(
                        type=str(_required(edge, "type", "scopes[].edges[]")),
                        to=str(_required(edge, "to", "scopes[].edges[]")),
                        what=str(_required(edge, "what", "scopes[].edges[]")),
                    )
                    for edge in item.get("edges", [])
                ),
            )
            for item in data.get("scopes", [])
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
                proof_contains=str(_required(item, "proof_contains", f"checks.{name}")),
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
            operating_rules=tuple(str(value) for value in data.get("operating_rules", [])),
            stop_and_ask=tuple(str(value) for value in data.get("stop_and_ask", [])),
            done_criteria=tuple(str(value) for value in data.get("done_criteria", [])),
            coverage_roots=tuple(str(value) for value in coverage.get("roots", [])),
            source_suffixes=tuple(str(value) for value in coverage.get("source_suffixes", [])),
            coverage_exemptions={str(key): str(value) for key, value in exemptions.items()},
            scopes=scopes,
            invariants=invariants,
            checks=checks,
        )
    except (TypeError, ValueError, AttributeError) as exc:
        raise MurlocsError(f"invalid manifest shape: {exc}") from exc


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value)


def render_manifest(network: str) -> str:
    escaped = network.replace("\\", "\\\\").replace('"', '\\"')
    return MANIFEST_TEMPLATE.format(network=escaped)
