from __future__ import annotations

from pathlib import Path

from murlocs.cli import build_cli


def invoke(*argv: str):
    return build_cli().invoke(list(argv))


def root_only(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "docs" / "api").mkdir(parents=True)
    (root / "docs" / "api" / "x.md").write_text("x\n", encoding="utf-8")
    (root / "legacy").mkdir()
    (root / "legacy" / "old.py").write_text("OLD = 1\n", encoding="utf-8")
    assert invoke("init", "--repo", str(root), "--name", "Roll").exit_code == 0
    return root


def test_dry_run_previews_and_writes_nothing(tmp_path):
    root = root_only(tmp_path)
    result = invoke(
        "--dry-run", "add-scope", "docs", "--repo", str(root), "--owners", "@docs"
    )
    assert result.exit_code == 0
    assert "would add scope docs" in result.output
    assert "manifest registration:" in result.output
    assert "[[layers]]" in result.output
    assert "docs/AGENTS.md" in result.output
    # Nothing was written.
    assert not (root / ".murlocs" / "layers").exists()
    assert not (root / "docs" / "AGENTS.md").exists()
    assert "[[layers]]" not in (root / ".murlocs" / "manifest.toml").read_text(encoding="utf-8")


def test_apply_adds_scope_and_check_passes(tmp_path):
    root = root_only(tmp_path)
    result = invoke("add-scope", "docs", "--repo", str(root), "--owners", "@docs")
    assert result.exit_code == 0
    assert (root / ".murlocs" / "layers" / "docs.toml").is_file()
    assert (root / "docs" / "AGENTS.md").is_file()
    assert invoke("check", "--repo", str(root)).exit_code == 0
    # The root map gains provenance once the network becomes layered.
    assert "## Provenance" in (root / "AGENTS.md").read_text(encoding="utf-8")


def test_scopes_can_be_added_independently(tmp_path):
    root = root_only(tmp_path)
    assert invoke("add-scope", "docs", "--repo", str(root)).exit_code == 0
    assert invoke("add-scope", "src/pkg", "--repo", str(root), "--id", "pkg").exit_code == 0
    assert invoke("check", "--repo", str(root)).exit_code == 0
    assert (root / "docs" / "AGENTS.md").is_file()
    assert (root / "src" / "pkg" / "AGENTS.md").is_file()


def test_nested_path_produces_root_to_scope_chain(tmp_path):
    root = root_only(tmp_path)
    assert invoke("add-scope", "docs/api", "--repo", str(root)).exit_code == 0
    explained = invoke("explain", "docs/api/x.md", "--repo", str(root))
    assert explained.exit_code == 0
    scope_lines = [line for line in explained.output.splitlines() if line.startswith("[")]
    assert scope_lines == ["[root] AGENTS.md", "[docs-api] docs/api/AGENTS.md"]


def test_deferred_area_is_recorded_as_reasoned_exemption(tmp_path):
    root = root_only(tmp_path)
    result = invoke(
        "add-scope",
        "docs",
        "--repo",
        str(root),
        "--defer",
        "legacy=migrating in a later phase",
    )
    assert result.exit_code == 0
    layer = (root / ".murlocs" / "layers" / "docs.toml").read_text(encoding="utf-8")
    assert "[coverage.exemptions]" in layer
    assert "migrating in a later phase" in layer


def test_apply_never_overwrites_unmanaged_map(tmp_path):
    root = root_only(tmp_path)
    (root / "docs" / "AGENTS.md").write_text("# hand written\n", encoding="utf-8")
    result = invoke("add-scope", "docs", "--repo", str(root))
    assert result.exit_code == 1
    assert "unmanaged" in result.stderr
    assert (root / "docs" / "AGENTS.md").read_text(encoding="utf-8") == "# hand written\n"
    # The manifest is left untouched by the failed rollout.
    assert "[[layers]]" not in (root / ".murlocs" / "manifest.toml").read_text(encoding="utf-8")
    assert not (root / ".murlocs" / "layers" / "docs.toml").exists()


def test_duplicate_scope_id_is_rejected(tmp_path):
    root = root_only(tmp_path)
    assert invoke("add-scope", "docs", "--repo", str(root)).exit_code == 0
    result = invoke("add-scope", "docs", "--repo", str(root), "--id", "docs")
    assert result.exit_code == 1
    assert "already exists" in result.stderr


def test_add_scope_rejects_repository_root(tmp_path):
    root = root_only(tmp_path)
    result = invoke("add-scope", ".", "--repo", str(root))
    assert result.exit_code == 1
    assert "not the repository root" in result.stderr


def test_add_scope_is_not_agent_visible():
    app = build_cli()
    assert "add-scope" in app.commands
    assert app.commands["add-scope"].surfaces == ("cli",)
