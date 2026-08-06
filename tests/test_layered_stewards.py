from __future__ import annotations

import json
import tomllib
from pathlib import Path

from murlocs.cli import build_cli
from murlocs.migration import (
    adopt_manifest,
    candidate_from_stewards,
    diff_stewards_candidate,
    inventory_repository,
    rollback_migration,
)
from murlocs.stewards import render_legacy_layered_maps, translate_layered_stewards

FIXTURE = Path(__file__).parent / "fixtures" / "stewards" / "layered"


def invoke(*argv: str):
    return build_cli().invoke(list(argv))


def _load_layered() -> tuple[dict, list[dict]]:
    data = tomllib.loads((FIXTURE / "manifest.toml").read_text(encoding="utf-8"))
    layer_datas = [
        tomllib.loads((FIXTURE / decl["path"].replace(".stewards/", "")).read_text("utf-8"))
        for decl in data["layer"]
    ]
    return data, layer_datas


def make_layered_repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "repo"
    stewards = root / ".stewards" / "layers"
    stewards.mkdir(parents=True)
    (root / ".stewards" / "manifest.toml").write_text(
        (FIXTURE / "manifest.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (root / ".stewards" / "PROTOCOL.md").write_text("# Legacy review protocol\n", encoding="utf-8")
    for name in ("base", "widget", "overlay"):
        (stewards / f"{name}.toml").write_text(
            (FIXTURE / "layers" / f"{name}.toml").read_text(encoding="utf-8"), encoding="utf-8"
        )
    (root / "src" / "widget").mkdir(parents=True)
    (root / "src" / "widget" / "widget.py").write_text(
        "class WidgetError(Exception):\n    pass\n", encoding="utf-8"
    )
    (root / "tests").mkdir()
    (root / "tests" / "test_widget.py").write_text(
        "def test_widget_contract():\n    pass\n", encoding="utf-8"
    )
    data, layer_datas = _load_layered()
    maps = render_legacy_layered_maps(data, layer_datas)
    for relative, content in maps.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root, maps


def test_translation_preserves_layer_order_kinds_owners_and_overrides():
    data, layer_datas = _load_layered()
    translated = translate_layered_stewards(data, layer_datas)
    assert [(layer.id, layer.kind) for layer in translated.layers] == [
        ("base", "base"),
        ("widget", "domain"),
        ("overlay", "overlay"),
    ]
    assert translated.layers[0].owners == ("@platform",)
    assert translated.layers[1].owners == ("@widget-team",)
    # The overlay steward carries explicit override intent.
    overlay_scope = translated.layers[2].fragment["scopes"][0]
    assert overlay_scope["override"] is True
    assert "path" not in overlay_scope
    assert [f.code for f in translated.findings if f.level == "info"][0] == "layered-import"


def test_import_writes_layer_files_and_registers_them(tmp_path):
    root, _ = make_layered_repo(tmp_path)
    result = invoke(
        "import", "--repo", str(root), "--from", "stewards", "--output", ".murlocs/manifest.toml"
    )
    assert result.exit_code == 0
    manifest = (root / ".murlocs" / "manifest.toml").read_text(encoding="utf-8")
    assert "[[layers]]" in manifest
    assert 'id = "widget"' in manifest
    assert 'owners = ["@widget-team"]' in manifest
    for name in ("base", "widget", "overlay"):
        assert (root / ".murlocs" / "layers" / f"{name}.toml").is_file()


def test_import_and_dry_run_perform_no_unintended_writes(tmp_path):
    root, _ = make_layered_repo(tmp_path)
    invoke("import", "--repo", str(root), "--from", "stewards")  # stdout only
    assert not (root / ".murlocs").exists()
    # Dry-run adoption writes nothing either.
    invoke(
        "import", "--repo", str(root), "--from", "stewards", "--output", ".murlocs/manifest.toml"
    )
    invoke("--dry-run", "adopt", "--repo", str(root))
    assert not (root / ".murlocs" / "migration.json").exists()


def test_semantic_diff_distinguishes_authoring_semantics(tmp_path):
    root, _ = make_layered_repo(tmp_path)
    result = diff_stewards_candidate(root)
    semantic = result["semantic"]
    assert semantic["layered"] is True
    assert semantic["layers"] == 3
    codes = {finding["code"] for finding in semantic["findings"]}
    # Rendered maps can look equivalent, but the loss report still records that the
    # network is authored as ordered, owned layers rather than one flat manifest.
    assert "layered-import" in codes


def test_inventory_reports_layered_network(tmp_path):
    root, _ = make_layered_repo(tmp_path)
    inventory = inventory_repository(root)
    legacy = inventory["legacy_stewards"]
    assert legacy["layered"] is True
    assert [layer["id"] for layer in legacy["layers"]] == ["base", "widget", "overlay"]
    assert legacy["scopes"] == 2


def test_adoption_and_rollback_are_byte_safe(tmp_path):
    root, maps = make_layered_repo(tmp_path)
    invoke(
        "import", "--repo", str(root), "--from", "stewards", "--output", ".murlocs/manifest.toml"
    )
    adopt_manifest(root)
    assert invoke("check", "--repo", str(root)).exit_code == 0
    # The adopted map carries layer provenance.
    assert "## Provenance" in (root / "src" / "widget" / "AGENTS.md").read_text("utf-8")
    rollback_migration(root)
    for relative, content in maps.items():
        assert (root / relative).read_text(encoding="utf-8") == content


def test_unsupported_composition_is_blocking(tmp_path):
    root, _ = make_layered_repo(tmp_path)
    # An override on a non-overlay (base/domain) layer is unsupported composition.
    widget = root / ".stewards" / "layers" / "widget.toml"
    widget.write_text(
        widget.read_text(encoding="utf-8").replace(
            'id = "widget"\npath',
            'id = "widget"\noverride = true\npath',
        ),
        encoding="utf-8",
    )
    candidate = candidate_from_stewards(root)
    assert any(f.level == "blocking" for f in candidate.findings)
    result = invoke(
        "import", "--repo", str(root), "--from", "stewards", "--output", ".murlocs/manifest.toml"
    )
    assert result.exit_code == 1
    assert "blocking loss" in result.stderr
    assert not (root / ".murlocs").exists()


def test_unknown_layer_field_is_reported_as_cumulative_blocking_loss(tmp_path):
    root, _ = make_layered_repo(tmp_path)
    base = root / ".stewards" / "layers" / "base.toml"
    base.write_text('mystery = ["nope"]\n' + base.read_text(encoding="utf-8"), encoding="utf-8")
    # Read-only stdout import surfaces the unknown field as a blocking loss finding
    # (naming it) and exits non-zero, rather than fail-fasting on the first mismatch.
    result = invoke("import", "--repo", str(root), "--from", "stewards", "--format", "json")
    assert result.exit_code == 1
    payload = json.loads(result.output)
    blocking = [f for f in payload["findings"] if f["level"] == "blocking"]
    assert any(f["code"] == "unsupported-field" for f in blocking)
    assert any("mystery" in subject for f in blocking for subject in f["subjects"])
