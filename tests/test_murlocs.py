from __future__ import annotations

import json
from pathlib import Path

import pytest
from milo import generate_llms_txt
from milo.testing import MCPClient

from murlocs import __version__
from murlocs.cli import _repeatable_option_flags, build_cli
from murlocs.errors import MurlocsError
from murlocs.lockfile import read_lock, render_lock, sha256_bytes
from murlocs.manifest import load_manifest
from murlocs.render import compile_manifest
from murlocs.verify import _tool_version_findings, validate
from tests.support import initialize_repo, invoke


def make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
    return root


def test_all_array_options_are_discovered_from_command_schemas():
    observed = {
        path: tuple(sorted(_repeatable_option_flags(command.schema)))
        for path, command in build_cli().walk_commands()
        if _repeatable_option_flags(command.schema)
    }

    assert observed == {
        "add-scope": ("--defer", "--owners"),
        "bootstrap": ("--coverage-root",),
        "hook.install": ("--event",),
        "hook.run": ("--path",),
        "hook.uninstall": ("--event",),
        "impact": ("--path",),
        "finish": ("--path",),
        "init": ("--coverage-root",),
        "review-changes": ("--path",),
        "scaffold.backlog-truth": ("--only",),
        "split-layers": (
            "--check",
            "--coverage-exemption",
            "--coverage-root",
            "--root-owner",
            "--scope",
        ),
    }


def test_hook_command_registry_contract_is_stable():
    hook = build_cli().groups["hook"]
    commands = hook.commands

    assert hook.description == "Run and conservatively manage passive Git hooks"
    assert tuple(commands) == ("run", "install", "uninstall", "status")
    assert {name: command.description for name, command in commands.items()} == {
        "run": "Assess an exact Git hook view",
        "install": "Install only into safe default Git hook slots",
        "uninstall": "Remove only exact Murlocs-owned Git hooks",
        "status": "Inspect default Git hook ownership",
    }
    assert {name: command.surfaces for name, command in commands.items()} == {
        name: ("cli",) for name in commands
    }
    inspection = {
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    }
    mutation = {
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    }
    assert {name: command.annotations for name, command in commands.items()} == {
        "run": inspection,
        "install": mutation,
        "uninstall": mutation,
        "status": inspection,
    }
    assert commands["run"].schema == {
        "type": "object",
        "properties": {
            "event": {
                "enum": ["pre-commit", "pre-push"],
                "type": "string",
                "x-milo-cli": {"kind": "positional", "metavar": "EVENT"},
                "description": "Git hook event to assess.",
            },
            "repo": {
                "type": "string",
                "x-milo-cli": {"kind": "option", "metavar": "PATH"},
                "description": "Exact Git worktree root.",
                "default": ".",
            },
            "correlation_id": {
                "type": "string",
                "x-milo-cli": {"kind": "option", "metavar": "ID"},
                "description": "Optional caller task/run id carried unchanged.",
                "default": None,
            },
            "deadline_ms": {
                "type": "integer",
                "x-milo-cli": {"kind": "option", "metavar": "MILLISECONDS"},
                "description": "Total local fail-closed deadline.",
                "default": 10_000,
            },
            "path": {
                "type": "array",
                "items": {"type": "string"},
                "x-milo-cli": {"kind": "option", "metavar": "PATH"},
                "description": (
                    "Optional explicit staged path; repeat without changing exact-index coverage."
                ),
                "default": None,
            },
            "remote_name": {
                "type": "string",
                "x-milo-cli": {"kind": "option", "metavar": "NAME"},
                "description": "Pre-push remote name, treated only as inert metadata.",
                "default": None,
            },
            "remote_url": {
                "type": "string",
                "x-milo-cli": {"kind": "option", "metavar": "URL"},
                "description": "Pre-push remote URL, treated only as inert metadata.",
                "default": None,
            },
            "expected_build_id": {
                "type": "string",
                "x-milo-cli": {"kind": "option", "metavar": "SHA256"},
                "description": "Exact runtime build identity pinned by a managed dispatcher.",
                "default": None,
            },
        },
        "required": ["event"],
    }
    event_option = {
        "type": "array",
        "items": {"enum": ["pre-commit", "pre-push"], "type": "string"},
        "x-milo-cli": {"kind": "option", "metavar": "EVENT"},
        "default": None,
    }
    repo_option = {
        "type": "string",
        "x-milo-cli": {"kind": "option", "metavar": "PATH"},
        "description": "Exact Git worktree root.",
        "default": ".",
    }
    runner_option = {
        "type": "string",
        "x-milo-cli": {"kind": "option", "metavar": "PATH"},
        "description": "Explicit durable Murlocs executable to pin in the generated dispatcher.",
        "default": None,
    }
    assert commands["install"].schema == {
        "type": "object",
        "properties": {
            "event": {
                **event_option,
                "description": ("Hook event to install; omission selects both supported events."),
            },
            "repo": repo_option,
            "runner": runner_option,
        },
    }
    assert commands["uninstall"].schema == {
        "type": "object",
        "properties": {
            "event": {
                **event_option,
                "description": ("Hook event to remove; omission selects both supported events."),
            },
            "repo": repo_option,
        },
    }
    assert commands["status"].schema == {
        "type": "object",
        "properties": {"repo": repo_option},
    }


def test_repeatable_option_help_matches_supported_terminal_syntax():
    add_scope = invoke("add-scope", "--help")
    split = invoke("split-layers", "--help")

    assert "repeat for multiple owners" in add_scope.output
    assert "repeat as needed" in add_scope.output
    assert "repeat as needed" in split.output


def initialize(root: Path, name: str | None = None) -> None:
    argv: list[str] = []
    if name is not None:
        argv.extend(["--name", name])
    initialize_repo(root, *argv)


def test_init_compile_check_and_explain(tmp_path):
    root = make_repo(tmp_path)
    argv = ["init", "--repo", str(root), "--name", "Example Shoal"]
    result = invoke(*argv)
    assert result.exit_code == 0, result.stderr

    assert (root / "AGENTS.md").is_file()
    assert (root / ".murlocs" / "lock.json").is_file()
    compile_result = invoke("compile", "--repo", str(root))
    assert compile_result.exit_code == 1
    assert "src/pkg" in compile_result.stderr
    checked = invoke("check", "--repo", str(root))
    explained = invoke("explain", "src/pkg/core.py", "--repo", str(root))

    assert checked.exit_code == 1
    assert "src/pkg" in checked.stderr
    assert "coverage incomplete" in checked.stderr
    assert explained.exit_code == 0
    assert "Example Shoal" in (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "[root] AGENTS.md" in explained.output


def test_compile_is_deterministic(tmp_path):
    root = make_repo(tmp_path)
    initialize(root)
    before = (root / "AGENTS.md").read_bytes()
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    assert (root / "AGENTS.md").read_bytes() == before


def test_init_dry_run_writes_nothing(tmp_path):
    root = make_repo(tmp_path)
    result = invoke("--dry-run", "init", "--repo", str(root), "--format", "json")
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["coverage"]["roots"] == ["src"]
    assert payload["dry_run"] is True
    assert not (root / ".murlocs").exists()
    assert not (root / "AGENTS.md").exists()


def test_init_infers_coverage_roots_from_repository_layout(tmp_path):
    root = make_repo(tmp_path)
    (root / "tests").mkdir()
    (root / "tests" / "test_core.py").write_text("def test_core(): pass\n", encoding="utf-8")

    result = invoke("init", "--repo", str(root), "--format", "json")

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.output)
    assert payload["coverage"]["roots"] == ["src", "tests"]
    manifest = (root / ".murlocs" / "manifest.toml").read_text(encoding="utf-8")
    assert 'roots = ["src", "tests"]' in manifest


def test_init_leaves_coverage_unconfigured_without_source_files(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("# Docs only\n", encoding="utf-8")

    result = invoke("init", "--repo", str(root))

    assert result.exit_code == 0, result.stderr
    manifest = (root / ".murlocs" / "manifest.toml").read_text(encoding="utf-8")
    assert "roots = []" in manifest
    checked = invoke("check", "--repo", str(root))
    assert checked.exit_code == 0
    assert "coverage unconfigured" in checked.output


def test_init_accepts_explicit_coverage_roots_and_reports_structural_gaps(tmp_path):
    root = make_repo(tmp_path)
    (root / "tests").mkdir()
    (root / "tests" / "test_core.py").write_text("def test_core(): pass\n", encoding="utf-8")

    result = invoke(
        "init",
        "--repo",
        str(root),
        "--coverage-root",
        "src",
        "--coverage-root",
        "tests",
        "--format",
        "json",
    )

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.output)
    assert payload["coverage"] == {
        "state": "structurally_incomplete",
        "roots": ["src", "tests"],
        "evaluated": True,
    }
    assert 'roots = ["src", "tests"]' in (root / ".murlocs" / "manifest.toml").read_text(
        encoding="utf-8"
    )
    checked = invoke("check", "--repo", str(root), "--format", "json")
    checked_payload = json.loads(checked.output)
    assert checked_payload["ok"] is False
    assert checked_payload["coverage"]["state"] == "structurally_incomplete"


def test_init_rejects_invalid_coverage_root_before_writing(tmp_path):
    root = make_repo(tmp_path)

    result = invoke("init", "--repo", str(root), "--coverage-root", "missing")

    assert result.exit_code == 1
    assert "coverage root is not a directory" in result.stderr
    assert not (root / ".murlocs").exists()
    assert not (root / "AGENTS.md").exists()


def test_repeatable_option_occurrence_requires_a_value(tmp_path):
    root = make_repo(tmp_path)

    empty = invoke(
        "init",
        "--repo",
        str(root),
        "--coverage-root",
        "--format",
        "json",
    )
    repeated_empty = invoke(
        "init",
        "--repo",
        str(root),
        "--coverage-root",
        "src",
        "--coverage-root",
        "--format",
        "json",
    )
    inline_empty = invoke(
        "init",
        "--repo",
        str(root),
        "--coverage-root=",
        "--format",
        "json",
    )

    for result in (empty, repeated_empty, inline_empty):
        assert result.exit_code == 2
        assert "argument --coverage-root: expected at least one argument" in result.stderr
    assert not (root / ".murlocs").exists()
    assert not (root / "AGENTS.md").exists()


def test_compile_dry_run_preserves_files(tmp_path):
    root = make_repo(tmp_path)
    initialize(root)
    manifest = root / ".murlocs" / "manifest.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("Repository guidance", "Agent guidance"),
        encoding="utf-8",
    )
    before_map = (root / "AGENTS.md").read_bytes()
    before_lock = (root / ".murlocs" / "lock.json").read_bytes()

    result = invoke("--dry-run", "compile", "--repo", str(root))

    assert result.exit_code == 0
    assert "would write AGENTS.md" in result.output
    assert "would write .murlocs/lock.json" in result.output
    payload = json.loads(
        invoke("--dry-run", "compile", "--repo", str(root), "--format", "json").output
    )
    assert payload["changed"] == [".murlocs/lock.json", "AGENTS.md"]
    assert payload["unchanged"] == []
    assert (root / "AGENTS.md").read_bytes() == before_map
    assert (root / ".murlocs" / "lock.json").read_bytes() == before_lock


def test_compile_dry_run_reports_synchronized_outputs_as_unchanged(tmp_path):
    root = make_repo(tmp_path)
    initialize(root)
    before_map = (root / "AGENTS.md").read_bytes()
    before_lock = (root / ".murlocs" / "lock.json").read_bytes()

    result = invoke("--dry-run", "compile", "--repo", str(root))
    payload = json.loads(
        invoke("--dry-run", "compile", "--repo", str(root), "--format", "json").output
    )

    assert result.exit_code == 0
    assert "would write" not in result.output
    assert result.output.splitlines() == [
        "unchanged .murlocs/lock.json",
        "unchanged AGENTS.md",
    ]
    assert payload["changed"] == []
    assert payload["unchanged"] == [".murlocs/lock.json", "AGENTS.md"]
    assert (root / "AGENTS.md").read_bytes() == before_map
    assert (root / ".murlocs" / "lock.json").read_bytes() == before_lock


def test_init_refuses_unmanaged_agents_file(tmp_path):
    root = make_repo(tmp_path)
    (root / "AGENTS.md").write_text("# Mine\n", encoding="utf-8")
    result = invoke("init", "--repo", str(root))
    assert result.exit_code == 1
    assert "unmanaged" in result.stderr
    assert (root / "AGENTS.md").read_text(encoding="utf-8") == "# Mine\n"


def test_compile_refuses_modified_generated_file(tmp_path):
    root = make_repo(tmp_path)
    initialize(root)
    (root / "AGENTS.md").write_text("manual edit\n", encoding="utf-8")
    result = invoke("compile", "--repo", str(root))
    assert result.exit_code == 1
    assert "modified generated file" in result.stderr
    assert (root / "AGENTS.md").read_text(encoding="utf-8") == "manual edit\n"


def test_check_detects_manifest_drift(tmp_path):
    root = make_repo(tmp_path)
    initialize(root)
    manifest = root / ".murlocs" / "manifest.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("Repository guidance", "Agent guidance"),
        encoding="utf-8",
    )
    result = invoke("check", "--repo", str(root))
    assert result.exit_code == 1
    assert "manifest changed" in result.stderr


def test_structured_check_preserves_declared_exit_code_and_payload(tmp_path):
    root = make_repo(tmp_path)
    initialize(root)
    manifest = root / ".murlocs" / "manifest.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("Repository guidance", "Agent guidance"),
        encoding="utf-8",
    )

    result = invoke("check", "--repo", str(root), "--format", "json")

    assert result.exit_code == 1
    assert result.stderr == ""
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert any(item["code"] == "drift" for item in payload["findings"])

    mcp_result = MCPClient(build_cli()).call("check", repo=str(root))
    assert mcp_result.is_error is False
    assert mcp_result.structured == payload


def test_check_detects_missing_manual_proof(tmp_path):
    root = make_repo(tmp_path)
    initialize(root)
    manifest = root / ".murlocs" / "manifest.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            'anchor = "Use this protocol"', 'anchor = "nope"'
        ),
        encoding="utf-8",
    )
    result = invoke("check", "--repo", str(root))
    assert result.exit_code == 1
    assert "manual evidence was not found" in result.stderr


def test_check_detects_uncovered_source_unit(tmp_path):
    root = make_repo(tmp_path)
    invoke("init", "--repo", str(root))
    result = invoke("check", "--repo", str(root))
    assert result.exit_code == 1
    assert "src/pkg" in result.stderr


def test_check_detects_source_unit_with_only_nested_files(tmp_path):
    root = tmp_path / "repo"
    nested = root / "src" / "pkg" / "nested"
    nested.mkdir(parents=True)
    (nested / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    invoke("init", "--repo", str(root))

    result = invoke("check", "--repo", str(root))

    assert result.exit_code == 1
    assert "src/pkg" in result.stderr


def test_reasoned_coverage_exemption(tmp_path):
    root = make_repo(tmp_path)
    initialize(root)
    checked = invoke("check", "--repo", str(root), "--format", "json")
    assert json.loads(checked.output)["coverage"] == {
        "state": "structurally_complete",
        "roots": ["src"],
        "evaluated": True,
    }


def test_lock_hash_matches_generated_map(tmp_path):
    root = make_repo(tmp_path)
    initialize(root)
    lock = json.loads((root / ".murlocs" / "lock.json").read_text(encoding="utf-8"))
    assert lock["generated"]["AGENTS.md"]["sha256"] == sha256_bytes(
        (root / "AGENTS.md").read_bytes()
    )


def test_explain_rejects_path_outside_repo(tmp_path):
    root = make_repo(tmp_path)
    initialize(root)
    result = invoke("explain", "../outside", "--repo", str(root))
    assert result.exit_code == 1
    assert "outside repository" in result.stderr


def test_check_reports_escaping_map_path(tmp_path):
    root = make_repo(tmp_path)
    initialize(root)
    manifest = root / ".murlocs" / "manifest.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace('map = "AGENTS.md"', 'map = "../AGENTS.md"'),
        encoding="utf-8",
    )
    result = invoke("check", "--repo", str(root))
    assert result.exit_code == 1
    assert "escapes the repository" in result.stderr


def test_context_budget_uses_largest_applicable_chain(tmp_path):
    root = make_repo(tmp_path)
    initialize(root)
    manifest = root / ".murlocs" / "manifest.toml"
    text = manifest.read_text(encoding="utf-8")
    text = text.replace("max_active_bytes = 24576", "max_active_bytes = 1")
    manifest.write_text(text, encoding="utf-8")
    result = invoke("check", "--repo", str(root))
    assert result.exit_code == 1
    assert "generated guidance is" in result.stderr


def test_milo_agent_surface_is_read_only_by_default():
    app = build_cli()
    tools = {tool.name for tool in MCPClient(app).list_tools()}
    discovery = generate_llms_txt(app)

    assert tools == {
        "check",
        "curate.check",
        "curate.review",
        "diff",
        "explain",
        "finish",
        "impact",
        "inventory",
        "orient",
        "review-changes",
        "scaffold.status",
        "status",
        "version",
    }
    assert "**check**" in discovery
    assert "**explain**" in discovery
    assert "**inventory**" in discovery
    assert "**status**" in discovery
    assert "**diff**" in discovery
    assert "**impact**" in discovery
    assert "**orient**" in discovery
    assert "**review-changes**" in discovery
    assert "**finish**" in discovery
    assert "**version**" in discovery
    assert "## Stamp opt-in repository kits (templates, workflows, docs)" in discovery
    assert "- **status**: Report whether a stamped kit is present and current" in discovery
    assert "## Create and inspect inert guidance curation proposals" in discovery
    assert "- **check**: Validate inert curation records" in discovery
    assert "- **review**: Review a proposal" in discovery
    assert "- **propose**:" not in discovery
    assert "**init**" not in discovery
    assert "**compile**" not in discovery
    assert "**import**" not in discovery
    assert "**adopt**" not in discovery
    assert "**prune**" not in discovery
    assert "**rollback**" not in discovery
    assert app.commands["check"].annotations == {
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    }
    assert app.commands["status"].annotations == app.commands["check"].annotations
    assert app.commands["impact"].annotations == app.commands["check"].annotations
    assert app.groups["curate"].commands["review"].annotations == app.commands["check"].annotations
    assert app.groups["curate"].commands["check"].annotations == app.commands["check"].annotations


def test_mcp_check_and_explain_return_structured_results(tmp_path):
    root = make_repo(tmp_path)
    initialize(root)
    client = MCPClient(build_cli())

    checked = client.call("check", repo=str(root))
    explained = client.call("explain", path="src/pkg/core.py", repo=str(root))

    assert checked.is_error is False
    assert checked.structured["ok"] is True
    assert checked.structured["summary"]["issues"] == 0
    assert checked.structured["coverage"] == {
        "state": "structurally_complete",
        "roots": ["src"],
        "evaluated": True,
    }
    assert explained.is_error is False
    assert explained.structured["path"] == "src/pkg/core.py"
    assert [scope["id"] for scope in explained.structured["scopes"]] == ["root"]


def test_usage_errors_retain_argparse_exit_code_two():
    result = invoke("check", "--unknown-option")
    assert result.exit_code == 2
    assert "unrecognized arguments" in result.stderr


def test_short_alias_uses_its_own_program_name():
    result = build_cli(name="mrr").invoke(["--help"])

    assert result.exit_code == 0
    assert result.output.startswith("mrr ")
    assert not result.output.startswith("murlocs ")


def test_coverage_findings_are_emitted_in_stable_order(tmp_path):
    root = tmp_path / "repo"
    (root / "src" / "alpha").mkdir(parents=True)
    (root / "src" / "beta").mkdir(parents=True)
    (root / "src" / "alpha" / "a.py").write_text("A = 1\n", encoding="utf-8")
    (root / "src" / "beta" / "b.py").write_text("B = 1\n", encoding="utf-8")
    assert invoke("init", "--repo", str(root)).exit_code == 0

    findings = [str(item) for item in validate(load_manifest(root)) if item.code == "coverage"]

    assert findings == sorted(findings)
    assert any("src/alpha" in item for item in findings)
    assert any("src/beta" in item for item in findings)


def test_compile_releases_unmodified_orphaned_maps(tmp_path):
    root = make_repo(tmp_path)
    initialize(root)
    manifest_path = root / ".murlocs" / "manifest.toml"
    base_manifest = manifest_path.read_text(encoding="utf-8")
    extra_scope = """

[[scopes]]
id = "pkg"
path = "src/pkg"
map = "src/pkg/AGENTS.md"
point_of_view = "Package guidance."
"""
    manifest_path.write_text(base_manifest + extra_scope, encoding="utf-8")
    compile_manifest(load_manifest(root))
    orphan = root / "src" / "pkg" / "AGENTS.md"
    assert orphan.is_file()

    manifest_path.write_text(base_manifest, encoding="utf-8")
    compile_manifest(load_manifest(root))

    assert not orphan.exists()
    lock = json.loads((root / ".murlocs" / "lock.json").read_text(encoding="utf-8"))
    assert "src/pkg/AGENTS.md" not in lock["generated"]


def test_compile_refuses_modified_orphaned_maps(tmp_path):
    root = make_repo(tmp_path)
    initialize(root)
    manifest_path = root / ".murlocs" / "manifest.toml"
    base_manifest = manifest_path.read_text(encoding="utf-8")
    extra_scope = """

[[scopes]]
id = "pkg"
path = "src/pkg"
map = "src/pkg/AGENTS.md"
point_of_view = "Package guidance."
"""
    manifest_path.write_text(base_manifest + extra_scope, encoding="utf-8")
    compile_manifest(load_manifest(root))
    orphan = root / "src" / "pkg" / "AGENTS.md"
    orphan.write_text(orphan.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    manifest_path.write_text(base_manifest, encoding="utf-8")

    with pytest.raises(MurlocsError) as exc:
        compile_manifest(load_manifest(root))

    assert "lockfile owns maps no longer declared" in str(exc.value)


def test_render_lock_records_tool_version_and_read_lock_round_trips():
    rendered = render_lock(b"manifest bytes", {"AGENTS.md": "content"})
    assert json.loads(rendered)["tool_version"] == __version__


def test_matching_tool_version_yields_no_finding(tmp_path):
    root = make_repo(tmp_path)
    initialize(root)
    lock = read_lock(root)
    assert lock is not None
    assert lock.tool_version == __version__
    assert _tool_version_findings(lock) == []
    assert not any(item.code == "tool-version" for item in validate(load_manifest(root)))


def test_mismatched_tool_version_emits_one_advisory_finding(tmp_path):
    root = make_repo(tmp_path)
    initialize(root)
    lock_path = root / ".murlocs" / "lock.json"
    data = json.loads(lock_path.read_text(encoding="utf-8"))
    data["tool_version"] = "0.0.0-incompatible"
    lock_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lock = read_lock(root)
    assert lock is not None
    assert lock.tool_version == "0.0.0-incompatible"

    version_findings = [
        item for item in validate(load_manifest(root)) if item.code == "tool-version"
    ]
    assert len(version_findings) == 1
    assert "0.0.0-incompatible" in version_findings[0].message


def test_missing_tool_version_does_not_crash_or_warn(tmp_path):
    root = make_repo(tmp_path)
    initialize(root)
    lock_path = root / ".murlocs" / "lock.json"
    data = json.loads(lock_path.read_text(encoding="utf-8"))
    del data["tool_version"]
    lock_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lock = read_lock(root)
    assert lock is not None
    assert lock.tool_version == ""
    assert _tool_version_findings(lock) == []
    assert not any(item.code == "tool-version" for item in validate(load_manifest(root)))


def test_validate_reads_repository_root_once_regardless_of_check_count(tmp_path, monkeypatch):
    root = make_repo(tmp_path)
    initialize(root)
    manifest_path = root / ".murlocs" / "manifest.toml"
    # Four command checks, one of which names a missing local path. Before the
    # fix each check triggered its own root readdir; behavior is unchanged.
    extra = "\n".join(
        f"[checks.check{index}]\n"
        f'invoke = "pytest src/pkg/missing{index}.py"\n'
        f'location = "README.md"\n'
        f'proof_contains = "protocol"\n'
        for index in range(4)
    )
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8") + "\n" + extra,
        encoding="utf-8",
    )

    resolved_root = root.resolve()
    original_iterdir = Path.iterdir
    root_reads = 0

    def counting_iterdir(self):
        nonlocal root_reads
        if self.resolve() == resolved_root:
            root_reads += 1
        return original_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", counting_iterdir)

    findings = validate(load_manifest(root))

    assert root_reads == 1
    # The shared listing still drives per-check command-path resolution.
    assert any(item.code == "check" and "missing0.py" in item.message for item in findings)
