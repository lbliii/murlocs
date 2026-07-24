from __future__ import annotations

from pathlib import Path

from milo.testing import MCPClient

from murlocs.cli import build_cli

MANIFEST = """schema_version = 1
network = "Exp"
protocol = ".murlocs/PROTOCOL.md"
max_active_bytes = 24576
pillars = ["P."]
search_policy = ["S."]
operating_rules = ["O."]
stop_and_ask = ["A."]
done_criteria = ["D."]

[coverage]
roots = []
source_suffixes = [".py"]

[coverage.exemptions]

[policies]

[checks.docs-test]
invoke = "pytest tests/test_docs.py"
location = ".murlocs/PROTOCOL.md"
proof_contains = "Use this protocol"

[[layers]]
id = "base"
kind = "base"
path = ".murlocs/layers/base.toml"
owners = ["@platform"]

[[layers]]
id = "docs"
kind = "domain"
path = ".murlocs/layers/docs.toml"
owners = ["@docs"]

[[layers]]
id = "overlay"
kind = "overlay"
path = ".murlocs/layers/overlay.toml"
owners = ["@lead"]
"""

BASE_LAYER = """[[scopes]]
id = "root"
path = "."
map = "AGENTS.md"
point_of_view = "Repo."
owns = ["README.md"]
"""

DOCS_LAYER = """[[scopes]]
id = "docs"
path = "docs"
map = "docs/AGENTS.md"
point_of_view = "Docs domain."
owns = ["docs"]

[[invariants]]
id = "docs-checked"
scope = "docs"
statement = "Docs pass tests."
severity = "important"
verification = "command"
enforced_by = "docs-test"
"""

OVERLAY_LAYER = """[[scopes]]
id = "docs"
override = true
point_of_view = "Docs domain, refined."
guardrails = ["Keep examples runnable."]
"""


def invoke(*argv: str):
    return build_cli().invoke(list(argv))


def build(root: Path, *, overlay: str = OVERLAY_LAYER) -> None:
    (root / "docs" / "api").mkdir(parents=True)
    (root / "docs" / "api" / "x.md").write_text("x\n", encoding="utf-8")
    murlocs = root / ".murlocs"
    (murlocs / "layers").mkdir(parents=True)
    (murlocs / "manifest.toml").write_text(MANIFEST, encoding="utf-8")
    (murlocs / "PROTOCOL.md").write_text("Use this protocol\n", encoding="utf-8")
    (murlocs / "layers" / "base.toml").write_text(BASE_LAYER, encoding="utf-8")
    (murlocs / "layers" / "docs.toml").write_text(DOCS_LAYER, encoding="utf-8")
    (murlocs / "layers" / "overlay.toml").write_text(overlay, encoding="utf-8")


def test_human_output_traces_layers_without_dumping_manifest(tmp_path):
    root = tmp_path / "repo"
    build(root)
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    result = invoke("explain", "docs/api/x.md", "--repo", str(root))
    assert result.exit_code == 0
    output = result.output
    assert "from: docs (domain), overlay (overlay)" in output
    assert "owners: @docs, @lead" in output
    assert "Active guidance:" in output
    # The full resolved manifest is not dumped.
    assert "[[scopes]]" not in output
    assert "schema_version" not in output


def test_structured_output_reports_override_source_locations(tmp_path):
    root = tmp_path / "repo"
    build(root)
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    structured = MCPClient(build_cli()).call(
        "explain", path="docs/api/x.md", repo=str(root)
    ).structured
    overrides = structured["overrides"]
    assert len(overrides) == 1
    override = overrides[0]
    assert override["subject"] == "scope:docs"
    assert override["field"] == "point_of_view"
    assert override["winner_path"] == ".murlocs/layers/overlay.toml"
    assert override["shadowed_path"] == ".murlocs/layers/docs.toml"
    assert override["winner_value"] == "Docs domain, refined."
    assert override["shadowed_value"] == "Docs domain."


def test_structured_output_reports_focused_checks_and_budget(tmp_path):
    root = tmp_path / "repo"
    build(root)
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    structured = MCPClient(build_cli()).call(
        "explain", path="docs/api/x.md", repo=str(root)
    ).structured
    assert [check["name"] for check in structured["checks"]] == ["docs-test"]
    assert structured["budget"]["max_active_bytes"] == 24576
    assert structured["budget"]["active_bytes"] > 0
    docs_scope = next(s for s in structured["scopes"] if s["id"] == "docs")
    assert [layer["id"] for layer in docs_scope["layers"]] == ["docs", "overlay"]


def test_rejected_override_reports_both_source_locations(tmp_path):
    root = tmp_path / "repo"
    bad_overlay = '[[scopes]]\nid = "docs"\noverride = true\nmap = "OTHER.md"\n'
    build(root, overlay=bad_overlay)
    result = invoke("explain", "docs/api/x.md", "--repo", str(root))
    assert result.exit_code == 1
    assert ".murlocs/layers/docs.toml" in result.stderr
    assert ".murlocs/layers/overlay.toml" in result.stderr


def test_single_file_explain_has_no_layer_trace(tmp_path):
    root = tmp_path / "repo"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
    assert invoke("init", "--repo", str(root), "--name", "Plain").exit_code == 0
    result = invoke("explain", "src/pkg/core.py", "--repo", str(root))
    assert result.exit_code == 0
    assert "from:" not in result.output
    assert "Overrides:" not in result.output
    assert "Active guidance:" in result.output
