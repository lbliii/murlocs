from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from murlocs.cli import build_cli
from murlocs.errors import MurlocsError
from murlocs.split import apply_split_layers, parse_split_targets, plan_split_layers

MURLOCS = Path(shutil.which("murlocs") or Path(sys.executable).with_name("murlocs"))
PROJECT_SRC = Path(__file__).parents[1] / "src"


def invoke(*argv: str):
    return build_cli().invoke(list(argv))


def invoke_packaged(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(MURLOCS), *argv],
        env={
            **os.environ,
            "NO_COLOR": "1",
            "PYTHONPATH": os.pathsep.join(
                value
                for value in (str(PROJECT_SRC), os.environ.get("PYTHONPATH"))
                if value
            ),
        },
        text=True,
        capture_output=True,
        check=False,
    )


MANIFEST = """schema_version = 1
network = "Split fixture"
protocol = ".murlocs/PROTOCOL.md"
max_active_bytes = 24576

pillars = ["Keep guidance reviewable."]
search_policy = ["Read local maps."]
operating_rules = ["Run focused checks."]
stop_and_ask = ["Ownership is unclear."]
done_criteria = ["Checks pass."]

[coverage]
roots = ["src/core", "docs"]
source_suffixes = [".py"]

[coverage.exemptions]
"src/core/generated" = "generated code"

[policies]
require_scope_invariants = true

[checks.shared]
invoke = "pytest"
location = "pyproject.toml"
proof_contains = "pytest"

[checks.core-only]
invoke = "ruff check src/core"
location = "pyproject.toml"
proof_contains = "ruff"

[[scopes]]
id = "root"
path = "."
map = "AGENTS.md"
point_of_view = "Repository guidance."
owns = ["pyproject.toml"]

[[scopes]]
id = "core"
path = "src/core"
map = "src/core/AGENTS.md"
point_of_view = "Core guidance."
owns = ["src/core"]

[[scopes]]
id = "docs"
path = "docs"
map = "docs/AGENTS.md"
point_of_view = "Docs guidance."
owns = ["docs"]

[judgments.core]
advocate = ["Small deterministic changes."]

[[invariants]]
id = "root-proof"
scope = "root"
statement = "Root stays reviewed."
severity = "important"
verification = "manual"
evidence_file = ".murlocs/PROTOCOL.md"
anchor = "review protocol"

[[invariants]]
id = "core-shared"
scope = "core"
statement = "Core uses the shared check."
severity = "important"
verification = "command"
enforced_by = "shared"

[[invariants]]
id = "docs-shared"
scope = "docs"
statement = "Docs uses the shared check."
severity = "important"
verification = "command"
enforced_by = "shared"

[[invariants]]
id = "core-focused"
scope = "core"
statement = "Core also has a focused check."
severity = "important"
verification = "command"
enforced_by = "core-only"
"""


def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".murlocs").mkdir(parents=True)
    (root / "src" / "core" / "generated").mkdir(parents=True)
    (root / "src" / "core" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "example.py").write_text("VALUE = 2\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n[tool.ruff]\n", encoding="utf-8"
    )
    (root / ".murlocs" / "PROTOCOL.md").write_text("# review protocol\n", encoding="utf-8")
    (root / ".murlocs" / "manifest.toml").write_text(MANIFEST, encoding="utf-8")
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    return root


def specs() -> tuple:
    return parse_split_targets(["core=core,domain,@core", "docs=docs,domain,@docs"])


def test_preview_is_read_only_and_separates_semantics_order_and_provenance(tmp_path):
    root = repository(tmp_path)
    before = {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }

    result = invoke(
        "--dry-run",
        "split-layers",
        "--scope",
        "core=core,domain,@core",
        "docs=docs,domain,@docs",
        "--repo",
        str(root),
        "--format",
        "json",
    )

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.output)
    assert payload["semantic_changes"] == []
    assert payload["order_only_changes"] == ["invariants"]
    assert "check:shared kept in root" in "\n".join(payload["decisions"])
    assert payload["moved"]["core"] == [
        "scope:core",
        "judgment:core",
        "invariant:core-shared",
        "invariant:core-focused",
        "check:core-only",
        "coverage-root:src/core",
        "coverage-exemption:src/core/generated",
    ]
    assert all(item["provenance_only"] for item in payload["rendered_changes"])
    assert all(item["after_bytes"] > item["before_bytes"] for item in payload["budgets"])
    after = {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    assert after == before


def test_apply_recompiles_deterministically_and_supports_read_flows(tmp_path):
    root = repository(tmp_path)

    result = invoke(
        "split-layers",
        "--scope",
        "core=core,domain,@core",
        "docs=docs,domain,@docs",
        "--repo",
        str(root),
        "--apply",
    )

    assert result.exit_code == 0, result.stderr
    assert (root / ".murlocs" / "layers" / "core.toml").is_file()
    assert (root / ".murlocs" / "layers" / "docs.toml").is_file()
    assert invoke("check", "--repo", str(root)).exit_code == 0
    snapshot = {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    assert {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
    } == snapshot
    explained = invoke("explain", "src/core/app.py", "--repo", str(root))
    assert explained.exit_code == 0
    assert "[core] src/core/AGENTS.md" in explained.output
    impacted = invoke(
        "impact", "--path", "src/core/app.py", "--repo", str(root), "--format", "json"
    )
    assert impacted.exit_code == 0
    core_impact = next(
        item for item in json.loads(impacted.output)["scopes"] if item["id"] == "core"
    )
    assert core_impact["status"] == "required"


def test_preview_reports_root_and_layer_codeowners_requirements(tmp_path):
    root = repository(tmp_path)
    manifest = root / ".murlocs" / "manifest.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "require_scope_invariants = true",
            "require_scope_invariants = true\n"
            "require_layer_owners = true\n"
            "validate_codeowners = true",
        ),
        encoding="utf-8",
    )
    (root / ".github").mkdir()
    (root / ".github" / "CODEOWNERS").write_text("", encoding="utf-8")

    result = invoke(
        "--dry-run",
        "split-layers",
        "--scope",
        "core=core,domain,@core",
        "docs=docs,domain,@docs",
        "--root-owner",
        "@platform",
        "--repo",
        str(root),
        "--format",
        "json",
    )

    assert result.exit_code == 0
    requirements = json.loads(result.output)["codeowners_requirements"]
    assert [item["entry"] for item in requirements] == [
        "/.murlocs/manifest.toml @platform",
        "/.murlocs/layers/core.toml @core",
        "/.murlocs/layers/docs.toml @docs",
    ]
    assert all(item["blocking"] for item in requirements)
    assert not (root / ".murlocs" / "layers").exists()


def test_shared_controls_move_only_with_explicit_assignments(tmp_path):
    root = repository(tmp_path)

    plan = plan_split_layers(
        root,
        specs(),
        check_assignments={"shared": "core"},
        coverage_root_assignments={"src/core": "root"},
        exemption_assignments={"src/core/generated": "root"},
    )

    assert "check:shared" in plan.moved["core"]
    assert "coverage-root:src/core" not in plan.moved["core"]
    assert "coverage-exemption:src/core/generated" not in plan.moved["core"]
    assert plan.semantic_changes == ()
    assert "check:shared explicitly assigned to core" in plan.decisions
    assert "coverage-root:src/core explicitly assigned to root" in plan.decisions


def test_real_parser_preserves_every_repeated_split_option(tmp_path):
    root = repository(tmp_path)
    manifest = root / ".murlocs" / "manifest.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            '"src/core/generated" = "generated code"',
            '"src/core/generated" = "generated code"\n'
            '"docs/generated" = "generated documentation"',
        ),
        encoding="utf-8",
    )

    result = invoke_packaged(
        "--dry-run",
        "split-layers",
        "--scope",
        "core=core,domain,@core",
        "--scope",
        "docs=docs,domain,@docs",
        "--root-owner",
        "@platform",
        "--root-owner",
        "@security",
        "--check",
        "shared=root",
        "--check",
        "core-only=core",
        "--coverage-root",
        "src/core=core",
        "--coverage-root",
        "docs=docs",
        "--coverage-exemption",
        "src/core/generated=core",
        "--coverage-exemption",
        "docs/generated=docs",
        "--repo",
        str(root),
    )

    assert result.returncode == 0, result.stderr
    assert 'owners = ["@platform", "@security"]' in result.stdout
    assert "core → core" in result.stdout
    assert "docs → docs" in result.stdout
    assert "check:shared explicitly assigned to root" in result.stdout
    assert "check:core-only explicitly assigned to core" in result.stdout
    assert "coverage-root:src/core explicitly assigned to core" in result.stdout
    assert "coverage-root:docs explicitly assigned to docs" in result.stdout
    assert "coverage-exemption:src/core/generated explicitly assigned to core" in result.stdout
    assert "coverage-exemption:docs/generated explicitly assigned to docs" in result.stdout


def test_repeated_split_assignment_key_is_rejected_actionably(tmp_path):
    root = repository(tmp_path)

    result = invoke(
        "--dry-run",
        "split-layers",
        "--scope",
        "core=core,domain,@core",
        "--check",
        "shared=root",
        "--check",
        "shared=core",
        "--repo",
        str(root),
    )

    assert result.exit_code == 1
    assert "duplicate check assignment: shared" in result.stderr
    assert not (root / ".murlocs" / "layers").exists()


def test_apply_recomputes_codeowners_after_planning(tmp_path):
    root = repository(tmp_path)
    manifest = root / ".murlocs" / "manifest.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "require_scope_invariants = true",
            "require_scope_invariants = true\nvalidate_codeowners = true",
        ),
        encoding="utf-8",
    )
    codeowners = root / ".github" / "CODEOWNERS"
    codeowners.parent.mkdir()
    codeowners.write_text(
        "/.murlocs/manifest.toml @platform\n"
        "/.murlocs/layers/core.toml @core\n"
        "/.murlocs/layers/docs.toml @docs\n",
        encoding="utf-8",
    )
    plan = plan_split_layers(root, specs(), root_owners=("@platform",))
    assert all(item.satisfied for item in plan.codeowners_requirements)
    codeowners.write_text(
        codeowners.read_text(encoding="utf-8").replace(
            "/.murlocs/layers/docs.toml @docs",
            "/.murlocs/layers/docs.toml @wrong-owner",
        ),
        encoding="utf-8",
    )
    mutated = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }

    with pytest.raises(MurlocsError, match="CODEOWNERS requirements are not satisfied"):
        apply_split_layers(root, plan)

    assert {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    } == mutated
    assert not (root / ".murlocs" / "layers").exists()


def test_conflicts_and_modified_outputs_cause_no_source_writes(tmp_path):
    root = repository(tmp_path)
    (root / "src" / "core" / "AGENTS.md").write_text("modified\n", encoding="utf-8")
    original_manifest = (root / ".murlocs" / "manifest.toml").read_bytes()
    plan = plan_split_layers(root, specs())

    with pytest.raises(MurlocsError, match="modified generated"):
        apply_split_layers(root, plan)

    assert (root / ".murlocs" / "manifest.toml").read_bytes() == original_manifest
    assert not (root / ".murlocs" / "layers").exists()
    with pytest.raises(MurlocsError, match="duplicate or portable-path-colliding"):
        plan_split_layers(
            root,
            parse_split_targets(["core=team,domain,@core", "docs=team,base,@docs"]),
        )
    with pytest.raises(MurlocsError, match="portable-path-colliding"):
        plan_split_layers(
            root,
            parse_split_targets(["core=Team,domain,@core", "docs=team,domain,@docs"]),
        )
    with pytest.raises(MurlocsError, match="unsafe layer id"):
        plan_split_layers(
            root,
            parse_split_targets(["core=../escape,domain,@core"]),
        )

    manifest = root / ".murlocs" / "manifest.toml"
    manifest.write_text("mystery = true\n" + manifest.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(MurlocsError, match="cannot preserve: mystery"):
        plan_split_layers(root, specs())


def test_transaction_restores_every_file_when_replace_fails(tmp_path, monkeypatch):
    root = repository(tmp_path)
    plan = plan_split_layers(root, specs())
    before = {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    import murlocs.split as split_module

    real_replace = split_module.os.replace
    failed = False

    def fail_once(source, destination):
        nonlocal failed
        if not failed and Path(destination) == root / "docs" / "AGENTS.md":
            failed = True
            raise OSError("simulated transaction failure")
        return real_replace(source, destination)

    monkeypatch.setattr(split_module.os, "replace", fail_once)
    with pytest.raises(OSError, match="simulated transaction failure"):
        apply_split_layers(root, plan)

    after = {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    assert after == before
    assert not (root / ".murlocs" / "layers").exists()


@pytest.mark.parametrize("mutation", ["remove-proof", "add-uncovered-source"])
def test_apply_revalidates_current_filesystem_and_writes_nothing(tmp_path, mutation):
    root = repository(tmp_path)
    plan = plan_split_layers(root, specs())
    if mutation == "remove-proof":
        (root / ".murlocs" / "PROTOCOL.md").unlink()
        expected = "manual evidence was not found"
    else:
        new_unit = root / "src" / "core" / "new-unit"
        new_unit.mkdir()
        (new_unit / "feature.py").write_text("VALUE = 3\n", encoding="utf-8")
        expected = "source-bearing unit has no map: src/core/new-unit"
    mutated = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }

    with pytest.raises(MurlocsError, match=expected):
        apply_split_layers(root, plan)

    after = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    assert after == mutated
    assert not (root / ".murlocs" / "layers").exists()
