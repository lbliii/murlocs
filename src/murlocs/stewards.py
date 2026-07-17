from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal

from murlocs.errors import MurlocsError

LEGACY_TOP_LEVEL = {
    "network",
    "protocol",
    "max_active_bytes",
    "coverage_roots",
    "pillars",
    "search_policy",
    "operating_rules",
    "stop_and_ask",
    "done_criteria",
    "check",
    "steward",
    "invariant",
    "judgment",
}
LEGACY_CHECK_FIELDS = {"invoke", "location", "proof_contains"}
LEGACY_STEWARD_FIELDS = {"id", "path", "point_of_view", "owns", "guardrails", "edges"}
LEGACY_EDGE_FIELDS = {"type", "to", "what"}
LEGACY_INVARIANT_FIELDS = {
    "id",
    "steward",
    "statement",
    "severity",
    "verification",
    "enforced_by",
    "evidence_file",
    "anchor",
}
LEGACY_JUDGMENT_FIELDS = {"advocate", "do_not", "serves"}
VERIFICATION_MAPPING = {"machine": "command", "manual": "manual", "none": "unknown"}


@dataclass(frozen=True)
class TranslationFinding:
    level: Literal["info", "debt"]
    code: str
    message: str
    subjects: tuple[str, ...] = ()


@dataclass(frozen=True)
class StewardTranslation:
    manifest: dict[str, Any]
    findings: tuple[TranslationFinding, ...]


def translate_stewards_manifest(
    data: dict[str, Any],
    *,
    require_scope_invariants: bool = False,
) -> StewardTranslation:
    """Translate the known Chirp/Kida steward dialect without reading or writing a repository."""
    _reject_unknown("legacy manifest", data, LEGACY_TOP_LEVEL)
    checks = _translate_checks(_table(data, "check"))
    scopes = _translate_scopes(_array(data, "steward"))
    invariants = _translate_invariants(_array(data, "invariant"))
    judgments = _translate_judgments(data.get("judgment", {}))

    missing_anchors = tuple(
        name for name, check in checks.items() if not check.get("proof_contains")
    )
    aliased_severities = tuple(
        item["id"]
        for item in invariants
        if item["severity"] in {"P0", "P1", "P2", "P3"}
    )
    findings: list[TranslationFinding] = []
    if missing_anchors:
        findings.append(
            TranslationFinding(
                level="debt",
                code="missing-proof-anchor",
                message=(
                    "registered checks without proof_contains remain unanchored proof debt"
                ),
                subjects=missing_anchors,
            )
        )
    if aliased_severities:
        findings.append(
            TranslationFinding(
                level="info",
                code="legacy-severity",
                message=(
                    "legacy severity spelling is preserved; P0/P1/P2/P3 mean "
                    "critical/important/advisory/advisory"
                ),
                subjects=aliased_severities,
            )
        )

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "network": _string(data, "network"),
        "protocol": _string(data, "protocol"),
        "max_active_bytes": int(data.get("max_active_bytes", 24576)),
        "pillars": _strings(data.get("pillars", []), "pillars"),
        "search_policy": _strings(data.get("search_policy", []), "search_policy"),
        "operating_rules": _strings(data.get("operating_rules", []), "operating_rules"),
        "stop_and_ask": _strings(data.get("stop_and_ask", []), "stop_and_ask"),
        "done_criteria": _strings(data.get("done_criteria", []), "done_criteria"),
        "coverage": {
            "roots": _strings(data.get("coverage_roots", []), "coverage_roots"),
            "source_suffixes": [".py"],
            "exemptions": {},
        },
        "policies": {"require_scope_invariants": require_scope_invariants},
        "checks": checks,
        "scopes": scopes,
        "invariants": invariants,
        "judgments": judgments,
    }
    return StewardTranslation(manifest=manifest, findings=tuple(findings))


def _translate_checks(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    checks: dict[str, dict[str, Any]] = {}
    for name, value in raw.items():
        if not isinstance(value, dict):
            raise MurlocsError(f"legacy check {name} must be a table")
        _reject_unknown(f"legacy check {name}", value, LEGACY_CHECK_FIELDS)
        check = {
            "invoke": _string(value, "invoke", context=f"legacy check {name}"),
            "location": _string(value, "location", context=f"legacy check {name}"),
        }
        if value.get("proof_contains") is not None:
            check["proof_contains"] = str(value["proof_contains"])
        checks[str(name)] = check
    return checks


def _translate_scopes(raw: list[Any]) -> list[dict[str, Any]]:
    scopes: list[dict[str, Any]] = []
    for index, value in enumerate(raw):
        if not isinstance(value, dict):
            raise MurlocsError(f"legacy steward[{index}] must be a table")
        context = f"legacy steward[{index}]"
        _reject_unknown(context, value, LEGACY_STEWARD_FIELDS)
        scope_id = _string(value, "id", context=context)
        map_path = _string(value, "path", context=context)
        parent = PurePosixPath(map_path).parent.as_posix()
        scope_path = "." if parent == "." else parent
        edges = deepcopy(value.get("edges", []))
        if not isinstance(edges, list):
            raise MurlocsError(f"{context}.edges must be an array")
        for edge_index, edge in enumerate(edges):
            if not isinstance(edge, dict):
                raise MurlocsError(f"{context}.edges[{edge_index}] must be a table")
            _reject_unknown(
                f"{context}.edges[{edge_index}]",
                edge,
                LEGACY_EDGE_FIELDS,
            )
        raw_owns = value.get("owns", {})
        if not isinstance(raw_owns, dict):
            raise MurlocsError(f"{context}.owns must be a table")
        owns = {
            str(kind): _strings(paths, f"{context}.owns.{kind}")
            for kind, paths in raw_owns.items()
        }
        scopes.append(
            {
                "id": scope_id,
                "path": scope_path,
                "map": map_path,
                "point_of_view": _string(value, "point_of_view", context=context),
                "owns": owns,
                "guardrails": _strings(value.get("guardrails", []), f"{context}.guardrails"),
                "edges": edges,
            }
        )
    return scopes


def _translate_invariants(raw: list[Any]) -> list[dict[str, Any]]:
    invariants: list[dict[str, Any]] = []
    for index, value in enumerate(raw):
        if not isinstance(value, dict):
            raise MurlocsError(f"legacy invariant[{index}] must be a table")
        context = f"legacy invariant[{index}]"
        _reject_unknown(context, value, LEGACY_INVARIANT_FIELDS)
        verification = _string(value, "verification", context=context)
        try:
            canonical_verification = VERIFICATION_MAPPING[verification]
        except KeyError as exc:
            raise MurlocsError(
                f"{context}.verification has unsupported value: {verification}"
            ) from exc
        translated = {
            "id": _string(value, "id", context=context),
            "scope": _string(value, "steward", context=context),
            "statement": _string(value, "statement", context=context),
            "severity": _string(value, "severity", context=context),
            "verification": canonical_verification,
        }
        for optional in ("enforced_by", "evidence_file", "anchor"):
            if value.get(optional) is not None:
                translated[optional] = str(value[optional])
        invariants.append(translated)
    return invariants


def _translate_judgments(raw: Any) -> dict[str, dict[str, list[str]]]:
    if not isinstance(raw, dict):
        raise MurlocsError("legacy judgment must be a table")
    judgments: dict[str, dict[str, list[str]]] = {}
    for scope_id, value in raw.items():
        if not isinstance(value, dict):
            raise MurlocsError(f"legacy judgment {scope_id} must be a table")
        _reject_unknown(f"legacy judgment {scope_id}", value, LEGACY_JUDGMENT_FIELDS)
        judgments[str(scope_id)] = {
            key: _strings(value.get(key, []), f"legacy judgment {scope_id}.{key}")
            for key in ("advocate", "do_not", "serves")
            if key in value
        }
    return judgments


def _reject_unknown(context: str, value: dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise MurlocsError(f"{context} contains unsupported fields: {', '.join(unknown)}")


def _table(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise MurlocsError(f"legacy manifest.{key} must be a table")
    return value


def _array(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key, [])
    if not isinstance(value, list):
        raise MurlocsError(f"legacy manifest.{key} must be an array")
    return value


def _string(data: dict[str, Any], key: str, *, context: str = "legacy manifest") -> str:
    if key not in data:
        raise MurlocsError(f"missing {context}.{key}")
    value = data[key]
    if not isinstance(value, str):
        raise MurlocsError(f"{context}.{key} must be a string")
    return value


def _strings(value: Any, context: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise MurlocsError(f"{context} must be an array of strings")
    return list(value)
