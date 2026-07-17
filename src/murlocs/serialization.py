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
        for optional in ("enforced_by", "evidence_file", "anchor"):
            if invariant.get(optional) is not None:
                lines.append(f"{optional} = {_quote(invariant[optional])}")
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


def _bare_key(value: str) -> str:
    return value if value.replace("-", "").replace("_", "").isalnum() else _quote(value)


def _ownership(value: Any) -> str:
    if isinstance(value, list):
        return _array(value)
    entries = ", ".join(
        f"{_bare_key(kind)} = {_array(paths)}" for kind, paths in value.items()
    )
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
