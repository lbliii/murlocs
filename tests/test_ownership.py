from __future__ import annotations

import json
from pathlib import Path

from murlocs.cli import build_cli

BASE_LAYER = """[[scopes]]
id = "root"
path = "."
map = "AGENTS.md"
point_of_view = "Repo-wide."
owns = ["README.md"]
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


def build(
    root: Path,
    *,
    policies: str = "",
    root_owners: str = 'owners = ["@control"]',
    base_owners: str = 'owners = ["@platform"]',
    docs_owners: str = 'owners = ["@docs"]',
    codeowners: str | None = None,
    reversed_order: bool = False,
) -> None:
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "guide.md").write_text("guide\n", encoding="utf-8")
    murlocs = root / ".murlocs"
    (murlocs / "layers").mkdir(parents=True)
    decls = [
        '[[layers]]\nid = "base"\nkind = "base"\n'
        f'path = ".murlocs/layers/base.toml"\n{base_owners}',
        '[[layers]]\nid = "docs"\nkind = "domain"\n'
        f'path = ".murlocs/layers/docs.toml"\n{docs_owners}',
    ]
    if reversed_order:
        decls = list(reversed(decls))
    (murlocs / "manifest.toml").write_text(
        "schema_version = 1\n"
        'network = "Owned"\n'
        'protocol = ".murlocs/PROTOCOL.md"\n'
        "max_active_bytes = 24576\n"
        f"{root_owners}\n"
        'pillars = ["P."]\n'
        'search_policy = ["S."]\n'
        'operating_rules = ["O."]\n'
        'stop_and_ask = ["A."]\n'
        'done_criteria = ["D."]\n'
        '[coverage]\nroots = []\nsource_suffixes = [".py"]\n[coverage.exemptions]\n'
        f"[policies]\n{policies}\n\n" + "\n\n".join(decls) + "\n",
        encoding="utf-8",
    )
    (murlocs / "PROTOCOL.md").write_text("Use this protocol\n", encoding="utf-8")
    (murlocs / "layers" / "base.toml").write_text(BASE_LAYER, encoding="utf-8")
    (murlocs / "layers" / "docs.toml").write_text(DOCS_LAYER, encoding="utf-8")
    if codeowners is not None:
        (root / ".github").mkdir(parents=True, exist_ok=True)
        (root / ".github" / "CODEOWNERS").write_text(codeowners, encoding="utf-8")


def test_generated_map_names_contributing_layers_and_owners(tmp_path):
    root = tmp_path / "repo"
    build(root)
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    docs_map = (root / "docs" / "AGENTS.md").read_text(encoding="utf-8")
    assert "## Provenance" in docs_map
    assert "`.murlocs/layers/docs.toml`" in docs_map
    assert "owners: @docs" in docs_map
    root_map = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "`.murlocs/manifest.toml`" in root_map
    assert "owners: @control" in root_map
    assert "`.murlocs/layers/base.toml`" in root_map
    assert "owners: @platform" in root_map


def test_require_layer_owners_flags_missing_owner(tmp_path):
    root = tmp_path / "repo"
    build(root, policies="require_layer_owners = true", docs_owners="")
    result = invoke("check", "--repo", str(root))
    assert result.exit_code == 1
    assert "layer docs declares no owner" in result.stderr


def test_require_layer_owners_flags_missing_root_manifest_owner(tmp_path):
    root = tmp_path / "repo"
    build(root, policies="require_layer_owners = true", root_owners="")
    result = invoke("check", "--repo", str(root))
    assert result.exit_code == 1
    assert "root manifest declares no owner" in result.stderr


def test_require_layer_owners_passes_when_declared(tmp_path):
    root = tmp_path / "repo"
    build(root, policies="require_layer_owners = true")
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    assert invoke("check", "--repo", str(root)).exit_code == 0


def test_codeowners_validation_detects_missing_entry(tmp_path):
    root = tmp_path / "repo"
    build(
        root,
        policies="validate_codeowners = true",
        codeowners=("/.murlocs/manifest.toml @control\n/.murlocs/layers/base.toml @platform\n"),
    )
    result = invoke("check", "--repo", str(root))
    assert result.exit_code == 1
    assert "no exact CODEOWNERS entry" in result.stderr
    assert "docs.toml" in result.stderr


def test_codeowners_validation_detects_missing_root_manifest_entry(tmp_path):
    root = tmp_path / "repo"
    build(
        root,
        policies="validate_codeowners = true",
        codeowners=("/.murlocs/layers/base.toml @platform\n/.murlocs/layers/docs.toml @docs\n"),
    )
    result = invoke("check", "--repo", str(root))
    assert result.exit_code == 1
    assert "root manifest has no exact CODEOWNERS entry" in result.stderr
    assert "expected owners: ['@control']" in result.stderr


def test_codeowners_validation_detects_owner_mismatch(tmp_path):
    root = tmp_path / "repo"
    build(
        root,
        policies="validate_codeowners = true",
        codeowners=(
            "/.murlocs/manifest.toml @control\n"
            "/.murlocs/layers/base.toml @platform\n"
            "/.murlocs/layers/docs.toml @someone-else\n"
        ),
    )
    result = invoke("check", "--repo", str(root))
    assert result.exit_code == 1
    assert "owners do not match CODEOWNERS" in result.stderr


def test_codeowners_validation_detects_root_manifest_owner_mismatch(tmp_path):
    root = tmp_path / "repo"
    build(
        root,
        policies="validate_codeowners = true",
        codeowners=(
            "/.murlocs/manifest.toml @someone-else\n"
            "/.murlocs/layers/base.toml @platform\n"
            "/.murlocs/layers/docs.toml @docs\n"
        ),
    )
    result = invoke("check", "--repo", str(root))
    assert result.exit_code == 1
    assert "root manifest owners do not match CODEOWNERS" in result.stderr
    assert "expected=['@control'] actual=['@someone-else']" in result.stderr


def test_codeowners_validation_passes_on_exact_match(tmp_path):
    root = tmp_path / "repo"
    build(
        root,
        policies="validate_codeowners = true",
        codeowners=(
            "/.murlocs/manifest.toml @control\n"
            "/.murlocs/layers/base.toml @platform\n"
            "/.murlocs/layers/docs.toml @docs\n"
        ),
    )
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    assert invoke("check", "--repo", str(root)).exit_code == 0


def test_codeowners_opt_in_without_file_is_reported(tmp_path):
    root = tmp_path / "repo"
    build(root, policies="validate_codeowners = true")
    result = invoke("check", "--repo", str(root))
    assert result.exit_code == 1
    assert "no CODEOWNERS file was found" in result.stderr


def test_repo_without_codeowners_is_normal_when_not_opted_in(tmp_path):
    root = tmp_path / "repo"
    build(root)
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    assert invoke("check", "--repo", str(root)).exit_code == 0


def test_policy_disabled_layered_repo_does_not_require_root_ownership(tmp_path):
    root = tmp_path / "repo"
    build(root, root_owners="", base_owners="", docs_owners="")
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    assert invoke("check", "--repo", str(root)).exit_code == 0


def test_single_file_manifest_ignores_layer_ownership_policies(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    assert invoke("init", "--repo", str(root), "--name", "Single").exit_code == 0
    manifest = root / ".murlocs" / "manifest.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "require_scope_invariants = false",
            "require_scope_invariants = false\n"
            "require_layer_owners = true\n"
            "validate_codeowners = true",
        ),
        encoding="utf-8",
    )
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    assert invoke("check", "--repo", str(root)).exit_code == 0


def test_reordering_layers_changes_provenance_and_lock(tmp_path):
    forward = tmp_path / "forward"
    build(forward)
    assert invoke("compile", "--repo", str(forward)).exit_code == 0
    forward_root = (forward / "AGENTS.md").read_text(encoding="utf-8")
    forward_lock = json.loads((forward / ".murlocs" / "lock.json").read_text(encoding="utf-8"))

    reverse = tmp_path / "reverse"
    build(reverse, reversed_order=True)
    assert invoke("compile", "--repo", str(reverse)).exit_code == 0
    reverse_root = (reverse / "AGENTS.md").read_text(encoding="utf-8")
    reverse_lock = json.loads((reverse / ".murlocs" / "lock.json").read_text(encoding="utf-8"))

    forward_prov = forward_root[forward_root.index("## Provenance") :]
    reverse_prov = reverse_root[reverse_root.index("## Provenance") :]
    assert forward_prov != reverse_prov
    assert [s["path"] for s in forward_lock["sources"]] != [
        s["path"] for s in reverse_lock["sources"]
    ]
