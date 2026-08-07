from __future__ import annotations

import json
from typing import Any


def render_manifest_data(data: dict[str, Any]) -> str:
    """Render canonical manifest data as deterministic TOML."""
    lines = [
        f"schema_version = {int(data['schema_version'])}",
        f"network = {_quote(data['network'])}",
        f"protocol = {_quote(data['protocol'])}",
        f"max_active_bytes = {int(data.get('max_active_bytes', 24576))}",
        "",
    ]
    if data.get("owners"):
        lines.extend([f"owners = {_array(data['owners'])}", ""])
    for key in (
        "pillars",
        "search_policy",
        "operating_rules",
        "stop_and_ask",
        "done_criteria",
    ):
        lines.extend(_multiline_array(key, data.get(key, [])))

    coverage = data.get("coverage", {})
    lines.extend(
        [
            "[coverage]",
            f"roots = {_array(coverage.get('roots', []))}",
            f"source_suffixes = {_array(coverage.get('source_suffixes', []))}",
            "",
            "[coverage.exemptions]",
        ]
    )
    for path, reason in sorted(coverage.get("exemptions", {}).items()):
        lines.append(f"{_quote(path)} = {_quote(reason)}")
    lines.append("")

    policies = data.get("policies", {})
    lines.extend(
        [
            "[policies]",
            "require_scope_invariants = "
            + ("true" if policies.get("require_scope_invariants", False) else "false"),
            "",
        ]
    )
    for key in ("require_layer_owners", "validate_codeowners"):
        if policies.get(key):
            lines.insert(len(lines) - 1, f"{key} = true")

    for layer in data.get("layers", []):
        lines.extend(
            [
                "[[layers]]",
                f"id = {_quote(layer['id'])}",
                f"kind = {_quote(layer['kind'])}",
                f"path = {_quote(layer['path'])}",
            ]
        )
        if layer.get("owners"):
            lines.append(f"owners = {_array(layer['owners'])}")
        lines.append("")

    for name, check in data.get("checks", {}).items():
        lines.extend(
            [
                f"[checks.{_bare_key(name)}]",
                f"invoke = {_quote(check['invoke'])}",
                f"location = {_quote(check['location'])}",
            ]
        )
        if check.get("proof_contains") is not None:
            lines.append(f"proof_contains = {_quote(check['proof_contains'])}")
        if check.get("description"):
            lines.append(f"description = {_quote(check['description'])}")
        lines.append("")

    for scope in data.get("scopes", []):
        lines.extend(
            [
                "[[scopes]]",
                f"id = {_quote(scope['id'])}",
                f"path = {_quote(scope['path'])}",
                f"map = {_quote(scope['map'])}",
                f"point_of_view = {_quote(scope['point_of_view'])}",
                f"owns = {_ownership(scope.get('owns', []))}",
                f"guardrails = {_array(scope.get('guardrails', []))}",
                f"edges = {_edges(scope.get('edges', []))}",
                "",
            ]
        )

    for scope_id, judgment in data.get("judgments", {}).items():
        lines.append(f"[judgments.{_bare_key(scope_id)}]")
        for key in ("advocate", "do_not", "serves"):
            if key in judgment:
                lines.append(f"{key} = {_array(judgment[key])}")
        lines.append("")

    for invariant in data.get("invariants", []):
        lines.extend(
            [
                "[[invariants]]",
                f"id = {_quote(invariant['id'])}",
                f"scope = {_quote(invariant['scope'])}",
                f"statement = {_quote(invariant['statement'])}",
                f"severity = {_quote(invariant['severity'])}",
                f"verification = {_quote(invariant['verification'])}",
            ]
        )
        for optional in ("enforced_by", "evidence_file", "anchor", "proof_contains"):
            if invariant.get(optional) is not None:
                lines.append(f"{optional} = {_quote(invariant[optional])}")
        _append_annotation(lines, invariant.get("annotation"))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_fragment_data(fragment: dict[str, Any]) -> str:
    """Render a Murlocs layer fragment (a manifest subset) as deterministic TOML."""
    lines: list[str] = []
    for key in (
        "pillars",
        "search_policy",
        "operating_rules",
        "stop_and_ask",
        "done_criteria",
    ):
        if key in fragment:
            lines.extend(_multiline_array(key, fragment[key]))

    coverage = fragment.get("coverage")
    if coverage:
        if coverage.get("roots") or coverage.get("source_suffixes"):
            lines.append("[coverage]")
            if coverage.get("roots"):
                lines.append(f"roots = {_array(coverage['roots'])}")
            if coverage.get("source_suffixes"):
                lines.append(f"source_suffixes = {_array(coverage['source_suffixes'])}")
            lines.append("")
        if coverage.get("exemptions"):
            lines.append("[coverage.exemptions]")
            for path, reason in sorted(coverage["exemptions"].items()):
                lines.append(f"{_quote(path)} = {_quote(reason)}")
            lines.append("")

    for name, check in fragment.get("checks", {}).items():
        lines.extend(
            [
                f"[checks.{_bare_key(name)}]",
                f"invoke = {_quote(check['invoke'])}",
                f"location = {_quote(check['location'])}",
            ]
        )
        if check.get("proof_contains") is not None:
            lines.append(f"proof_contains = {_quote(check['proof_contains'])}")
        if check.get("description"):
            lines.append(f"description = {_quote(check['description'])}")
        lines.append("")

    for scope in fragment.get("scopes", []):
        lines.append("[[scopes]]")
        lines.append(f"id = {_quote(scope['id'])}")
        if scope.get("override"):
            lines.append("override = true")
        for key in ("path", "map", "point_of_view"):
            if key in scope:
                lines.append(f"{key} = {_quote(scope[key])}")
        if "owns" in scope:
            lines.append(f"owns = {_ownership(scope['owns'])}")
        if scope.get("guardrails"):
            lines.append(f"guardrails = {_array(scope['guardrails'])}")
        if scope.get("edges"):
            lines.append(f"edges = {_edges(scope['edges'])}")
        lines.append("")

    for scope_id, judgment in fragment.get("judgments", {}).items():
        lines.append(f"[judgments.{_bare_key(scope_id)}]")
        for key in ("advocate", "do_not", "serves"):
            if key in judgment:
                lines.append(f"{key} = {_array(judgment[key])}")
        lines.append("")

    for invariant in fragment.get("invariants", []):
        lines.append("[[invariants]]")
        lines.append(f"id = {_quote(invariant['id'])}")
        if invariant.get("override"):
            lines.append("override = true")
        for key in ("scope", "statement", "severity", "verification"):
            lines.append(f"{key} = {_quote(invariant[key])}")
        for optional in ("enforced_by", "evidence_file", "anchor", "proof_contains"):
            if invariant.get(optional) is not None:
                lines.append(f"{optional} = {_quote(invariant[optional])}")
        _append_annotation(lines, invariant.get("annotation"))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _multiline_array(key: str, values: list[str]) -> list[str]:
    lines = [f"{key} = ["]
    lines.extend(f"  {_quote(value)}," for value in values)
    lines.extend(["]", ""])
    return lines


def _quote(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _array(values: list[str]) -> str:
    return "[" + ", ".join(_quote(value) for value in values) + "]"


def _append_annotation(lines: list[str], annotation: Any) -> None:
    if annotation is None:
        return
    lines.append(
        "annotation = { "
        + ", ".join(
            f"{key} = {_quote(annotation[key])}" for key in ("id", "kind", "file", "version")
        )
        + " }"
    )


def _bare_key(value: str) -> str:
    return value if value.replace("-", "").replace("_", "").isalnum() else _quote(value)


def _ownership(value: Any) -> str:
    if isinstance(value, list):
        return _array(value)
    entries = ", ".join(f"{_bare_key(kind)} = {_array(paths)}" for kind, paths in value.items())
    return "{ " + entries + " }"


def _edges(edges: list[dict[str, str]]) -> str:
    if not edges:
        return "[]"
    values = []
    for edge in edges:
        values.append(
            "{ type = "
            + _quote(edge["type"])
            + ", to = "
            + _quote(edge["to"])
            + ", what = "
            + _quote(edge["what"])
            + " }"
        )
    return "[" + ", ".join(values) + "]"
