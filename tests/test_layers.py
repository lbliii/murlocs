from __future__ import annotations

import json
from pathlib import Path

from murlocs.cli import build_cli
from murlocs.errors import MurlocsError
from murlocs.layers import resolve_manifest
from murlocs.manifest import load_manifest

ROOT_TEMPLATE = """schema_version = 1
network = "Layered"
protocol = ".murlocs/PROTOCOL.md"
max_active_bytes = 24576

pillars = ["Base pillar."]
search_policy = ["Read root first."]
operating_rules = ["Edit manifest."]
stop_and_ask = ["A boundary is crossed."]
done_criteria = ["Checks pass."]

[coverage]
roots = []
source_suffixes = [".py"]

[coverage.exemptions]

[policies]
require_scope_invariants = false

{layers}
"""

BASE_LAYER = """pillars = ["Base pillar.", "Shared pillar."]

[[scopes]]
id = "root"
path = "."
map = "AGENTS.md"
point_of_view = "Repo-wide."
owns = ["README.md"]

[[invariants]]
id = "root-inv"
scope = "root"
statement = "Root truth."
severity = "critical"
verification = "manual"
evidence_file = ".murlocs/PROTOCOL.md"
anchor = "Use this protocol"
"""

DOCS_LAYER = """[[scopes]]
id = "docs"
path = "docs"
map = "docs/AGENTS.md"
point_of_view = "Docs domain."
owns = ["docs"]
"""


def invoke(*argv: str):
    return build_cli().invoke(list(argv))


def scaffold(root: Path, *, layers: str, files: dict[str, str]) -> None:
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "guide.md").write_text("guide\n", encoding="utf-8")
    murlocs = root / ".murlocs"
    (murlocs / "layers").mkdir(parents=True)
    (murlocs / "manifest.toml").write_text(ROOT_TEMPLATE.format(layers=layers), encoding="utf-8")
    (murlocs / "PROTOCOL.md").write_text("Use this protocol\n", encoding="utf-8")
    for relative, content in files.items():
        (root / relative).write_text(content, encoding="utf-8")


ONE_LAYER_DECL = '[[layers]]\nid = "base"\nkind = "base"\npath = ".murlocs/layers/base.toml"\n'


def two_layer_decl() -> str:
    return (
        '[[layers]]\nid = "base"\nkind = "base"\npath = ".murlocs/layers/base.toml"\n\n'
        '[[layers]]\nid = "docs"\nkind = "domain"\npath = ".murlocs/layers/docs.toml"\n'
    )


def test_layered_manifest_compiles_and_is_deterministic(tmp_path):
    root = tmp_path / "repo"
    scaffold(
        root,
        layers=two_layer_decl(),
        files={
            ".murlocs/layers/base.toml": BASE_LAYER,
            ".murlocs/layers/docs.toml": DOCS_LAYER,
        },
    )
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    before_root = (root / "AGENTS.md").read_bytes()
    before_docs = (root / "docs" / "AGENTS.md").read_bytes()

    assert invoke("compile", "--repo", str(root)).exit_code == 0
    assert invoke("check", "--repo", str(root)).exit_code == 0
    assert (root / "AGENTS.md").read_bytes() == before_root
    assert (root / "docs" / "AGENTS.md").read_bytes() == before_docs


def test_list_fields_append_in_order_with_dedup(tmp_path):
    root = tmp_path / "repo"
    scaffold(
        root,
        layers=two_layer_decl(),
        files={
            ".murlocs/layers/base.toml": BASE_LAYER,
            ".murlocs/layers/docs.toml": DOCS_LAYER,
        },
    )
    resolved = resolve_manifest(root)
    # "Base pillar." is declared in both root and base but appears exactly once, first.
    assert resolved.data["pillars"] == ["Base pillar.", "Shared pillar."]


def test_lockfile_records_ordered_layer_hashes(tmp_path):
    root = tmp_path / "repo"
    scaffold(
        root,
        layers=two_layer_decl(),
        files={
            ".murlocs/layers/base.toml": BASE_LAYER,
            ".murlocs/layers/docs.toml": DOCS_LAYER,
        },
    )
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    lock = json.loads((root / ".murlocs" / "lock.json").read_text(encoding="utf-8"))
    paths = [entry["path"] for entry in lock["sources"]]
    assert paths == [
        ".murlocs/manifest.toml",
        ".murlocs/layers/base.toml",
        ".murlocs/layers/docs.toml",
    ]


def test_changing_a_layer_is_detected_as_drift(tmp_path):
    root = tmp_path / "repo"
    scaffold(
        root,
        layers=two_layer_decl(),
        files={
            ".murlocs/layers/base.toml": BASE_LAYER,
            ".murlocs/layers/docs.toml": DOCS_LAYER,
        },
    )
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    layer = root / ".murlocs" / "layers" / "docs.toml"
    layer.write_text(
        layer.read_text(encoding="utf-8").replace("Docs domain.", "Docs revised."),
        encoding="utf-8",
    )
    result = invoke("check", "--repo", str(root))
    assert result.exit_code == 1
    assert "layer changed since the last compile" in result.stderr


def test_overlay_override_merges_without_changing_paths(tmp_path):
    root = tmp_path / "repo"
    overlay = (
        '[[scopes]]\nid = "root"\noverride = true\n'
        'point_of_view = "Repo-wide, refined."\nguardrails = ["Stay small."]\n'
    )
    layers = two_layer_decl() + (
        '\n[[layers]]\nid = "overlay"\nkind = "overlay"\npath = ".murlocs/layers/overlay.toml"\n'
    )
    scaffold(
        root,
        layers=layers,
        files={
            ".murlocs/layers/base.toml": BASE_LAYER,
            ".murlocs/layers/docs.toml": DOCS_LAYER,
            ".murlocs/layers/overlay.toml": overlay,
        },
    )
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    text = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "Repo-wide, refined." in text
    assert "Stay small." in text
    resolved = resolve_manifest(root)
    assert resolved.scope_layers["root"] == ("base", "overlay")
    assert any(o.subject == "scope:root" and o.field == "point_of_view" for o in resolved.overrides)


def test_override_cannot_change_output_path(tmp_path):
    root = tmp_path / "repo"
    overlay = '[[scopes]]\nid = "root"\noverride = true\nmap = "OTHER.md"\n'
    layers = two_layer_decl() + (
        '\n[[layers]]\nid = "overlay"\nkind = "overlay"\npath = ".murlocs/layers/overlay.toml"\n'
    )
    scaffold(
        root,
        layers=layers,
        files={
            ".murlocs/layers/base.toml": BASE_LAYER,
            ".murlocs/layers/docs.toml": DOCS_LAYER,
            ".murlocs/layers/overlay.toml": overlay,
        },
    )
    result = invoke("compile", "--repo", str(root))
    assert result.exit_code == 1
    assert "may not change map" in result.stderr


def test_duplicate_scope_without_override_is_rejected(tmp_path):
    root = tmp_path / "repo"
    dup = '[[scopes]]\nid = "root"\npath = "."\nmap = "AGENTS.md"\npoint_of_view = "Second root."\n'
    layers = two_layer_decl() + (
        '\n[[layers]]\nid = "dup"\nkind = "domain"\npath = ".murlocs/layers/dup.toml"\n'
    )
    scaffold(
        root,
        layers=layers,
        files={
            ".murlocs/layers/base.toml": BASE_LAYER,
            ".murlocs/layers/docs.toml": DOCS_LAYER,
            ".murlocs/layers/dup.toml": dup,
        },
    )
    result = invoke("check", "--repo", str(root))
    assert result.exit_code == 1
    assert "duplicate scope root" in result.stderr


def test_duplicate_invariant_without_override_is_rejected(tmp_path):
    root = tmp_path / "repo"
    dup = (
        '[[invariants]]\nid = "root-inv"\nscope = "root"\n'
        'statement = "Conflicting."\nseverity = "critical"\nverification = "unknown"\n'
    )
    layers = two_layer_decl() + (
        '\n[[layers]]\nid = "dup"\nkind = "domain"\npath = ".murlocs/layers/dup.toml"\n'
    )
    scaffold(
        root,
        layers=layers,
        files={
            ".murlocs/layers/base.toml": BASE_LAYER,
            ".murlocs/layers/docs.toml": DOCS_LAYER,
            ".murlocs/layers/dup.toml": dup,
        },
    )
    result = invoke("check", "--repo", str(root))
    assert result.exit_code == 1
    assert "duplicate invariant root-inv" in result.stderr


def test_layer_with_unknown_field_is_rejected(tmp_path):
    root = tmp_path / "repo"
    bad = 'mystery = ["nope"]\n' + BASE_LAYER
    scaffold(
        root,
        layers=two_layer_decl(),
        files={
            ".murlocs/layers/base.toml": bad,
            ".murlocs/layers/docs.toml": DOCS_LAYER,
        },
    )
    result = invoke("check", "--repo", str(root))
    assert result.exit_code == 1
    assert "unsupported fields" in result.stderr


def test_layer_may_not_set_control_plane_fields(tmp_path):
    root = tmp_path / "repo"
    bad = 'network = "Nope"\n' + BASE_LAYER
    scaffold(
        root,
        layers=two_layer_decl(),
        files={
            ".murlocs/layers/base.toml": bad,
            ".murlocs/layers/docs.toml": DOCS_LAYER,
        },
    )
    result = invoke("check", "--repo", str(root))
    assert result.exit_code == 1
    assert "control-plane fields" in result.stderr


def test_unsafe_layer_path_is_rejected(tmp_path):
    root = tmp_path / "repo"
    layers = '[[layers]]\nid = "escape"\nkind = "base"\npath = "../escape.toml"\n'
    scaffold(root, layers=layers, files={})
    result = invoke("check", "--repo", str(root))
    assert result.exit_code == 1
    assert "escapes the repository" in result.stderr


def test_duplicate_layer_id_is_rejected(tmp_path):
    root = tmp_path / "repo"
    layers = (
        '[[layers]]\nid = "base"\nkind = "base"\npath = ".murlocs/layers/base.toml"\n\n'
        '[[layers]]\nid = "base"\nkind = "domain"\npath = ".murlocs/layers/docs.toml"\n'
    )
    scaffold(
        root,
        layers=layers,
        files={
            ".murlocs/layers/base.toml": BASE_LAYER,
            ".murlocs/layers/docs.toml": DOCS_LAYER,
        },
    )
    result = invoke("check", "--repo", str(root))
    assert result.exit_code == 1
    assert "duplicate layer id" in result.stderr


def test_conflicting_coverage_exemption_is_rejected(tmp_path):
    root = tmp_path / "repo"
    base = BASE_LAYER + '\n[coverage.exemptions]\n"src/pkg" = "reason one"\n'
    docs = DOCS_LAYER + '\n[coverage.exemptions]\n"src/pkg" = "reason two"\n'
    scaffold(
        root,
        layers=two_layer_decl(),
        files={
            ".murlocs/layers/base.toml": base,
            ".murlocs/layers/docs.toml": docs,
        },
    )
    result = invoke("check", "--repo", str(root))
    assert result.exit_code == 1
    assert "conflicting coverage exemption" in result.stderr


def test_single_file_manifest_is_not_layered(tmp_path):
    root = tmp_path / "repo"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
    result = invoke("init", "--repo", str(root), "--name", "Plain")
    assert result.exit_code == 0
    resolved = resolve_manifest(root)
    assert resolved.layered is False
    assert [source.id for source in resolved.sources] == ["manifest"]
    lock = json.loads((root / ".murlocs" / "lock.json").read_text(encoding="utf-8"))
    # Single-file manifests omit the layer set; the manifest hash still guards drift.
    assert "sources" not in lock or len(lock["sources"]) == 1


def test_resolve_reports_missing_layer_file(tmp_path):
    root = tmp_path / "repo"
    scaffold(root, layers=two_layer_decl(), files={".murlocs/layers/base.toml": BASE_LAYER})
    try:
        resolve_manifest(root)
    except MurlocsError as exc:
        assert "docs" in str(exc) and "not found" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected a missing-layer error")


def test_layered_manifest_missing_required_field_is_rejected(tmp_path):
    """A layered root that omits `network` must fail like a single-file one.

    Regression: composition copied required keys with `.get()`, so an absent
    value became `None`, survived validation, and rendered `# None: root`.
    """
    root = tmp_path / "repo"
    scaffold(root, layers=ONE_LAYER_DECL, files={".murlocs/layers/base.toml": BASE_LAYER})
    manifest = root / ".murlocs" / "manifest.toml"
    manifest.write_text(
        "\n".join(
            line
            for line in manifest.read_text(encoding="utf-8").splitlines()
            if not line.startswith("network =")
        )
        + "\n",
        encoding="utf-8",
    )

    # `resolve_manifest` composes; `load_manifest` is where required fields
    # are enforced, so assert at the layer that actually validates.
    try:
        load_manifest(root)
    except MurlocsError as exc:
        assert "missing manifest.network" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected a missing-network error")


def test_layered_compile_refuses_a_manifest_without_a_network(tmp_path):
    """The failure must reach the command, not just the resolver."""
    root = tmp_path / "repo"
    scaffold(root, layers=ONE_LAYER_DECL, files={".murlocs/layers/base.toml": BASE_LAYER})
    manifest = root / ".murlocs" / "manifest.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace('network = "Layered"\n', ""),
        encoding="utf-8",
    )

    result = invoke("compile", "--repo", str(root))

    assert result.exit_code == 1
    assert "missing manifest.network" in result.stderr
    assert not (root / "AGENTS.md").exists()
