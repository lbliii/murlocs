from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from milo.testing import MCPClient

import murlocs.impact as impact_module
from murlocs.cli import _normalize_repeatable_options, build_cli
from murlocs.impact import build_impact_report
from murlocs.manifest import load_manifest

MANIFEST = """schema_version = 1
network = "Impact"
protocol = ".murlocs/PROTOCOL.md"
max_active_bytes = 24576
owners = ["@platform"]
pillars = []
search_policy = []
operating_rules = []
stop_and_ask = []
done_criteria = []

[coverage]
roots = []
source_suffixes = [".py"]

[coverage.exemptions]

[policies]

[checks.api-test]
invoke = "touch MUST_NOT_EXIST"
location = "pyproject.toml"
proof_contains = "[tool.pytest.ini_options]"
description = "Check API behavior."

[[layers]]
id = "api"
kind = "domain"
path = ".murlocs/layers/api.toml"
owners = ["@api"]

[[layers]]
id = "worker"
kind = "domain"
path = ".murlocs/layers/worker.toml"
owners = ["@worker"]

[[scopes]]
id = "root"
path = "."
map = "AGENTS.md"
point_of_view = "Repository."
owns = ["README.md", "docs"]
guardrails = []
edges = []
"""

API_LAYER = """[[scopes]]
id = "api"
path = "src/api"
map = "src/api/AGENTS.md"
point_of_view = "API."
owns = ["src/api/app"]
guardrails = []
edges = [{ type = "verified-by", to = "worker", what = "Worker tests API contracts." }]

[[invariants]]
id = "api-contract"
scope = "api"
statement = "The API contract is checked."
severity = "critical"
verification = "command"
enforced_by = "api-test"

[[invariants]]
id = "api-design"
scope = "api"
statement = "The API design is documented."
severity = "important"
verification = "manual"
evidence_file = "docs/api.md"
anchor = "API design"
"""

WORKER_LAYER = """[[scopes]]
id = "worker"
path = "src/worker"
map = "src/worker/AGENTS.md"
point_of_view = "Worker."
owns = ["src/worker"]
guardrails = []
edges = []
"""


def add_annotation(root: Path) -> None:
    source = root / "src/api/app/service.py"
    source.write_text(
        '# murlocs:annotation/v1 evidence "api.marker"\nVALUE = 1\n',
        encoding="utf-8",
    )
    layer = root / ".murlocs/layers/api.toml"
    layer.write_text(
        layer.read_text(encoding="utf-8").replace(
            'anchor = "API design"',
            'anchor = "API design"\nannotation = { id = "api.marker", '
            'kind = "evidence", file = "src/api/app/service.py", version = "v1" }',
        ),
        encoding="utf-8",
    )


def invoke(*argv: str):
    return build_cli().invoke(list(argv))


def build(root: Path) -> None:
    for directory in ("src/api/app", "src/api/scratch", "src/worker", "docs"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / "src/api/app/service.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "src/api/scratch/note.txt").write_text("note\n", encoding="utf-8")
    (root / "src/worker/job.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "docs/api.md").write_text("# API design\n", encoding="utf-8")
    (root / "README.md").write_text("# Repo\n", encoding="utf-8")
    (root / "CHANGELOG.md").write_text("# Changes\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    (root / ".murlocs/layers").mkdir(parents=True)
    (root / ".murlocs/manifest.toml").write_text(MANIFEST, encoding="utf-8")
    (root / ".murlocs/PROTOCOL.md").write_text("# Protocol\n", encoding="utf-8")
    (root / ".murlocs/layers/api.toml").write_text(API_LAYER, encoding="utf-8")
    (root / ".murlocs/layers/worker.toml").write_text(WORKER_LAYER, encoding="utf-8")


def structured(root: Path, **kwargs):
    return MCPClient(build_cli()).call("impact", repo=str(root), **kwargs).structured


def by_id(report: dict, scope_id: str) -> dict:
    return next(scope for scope in report["scopes"] if scope["id"] == scope_id)


def commit_all(root: Path, message: str) -> None:
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            message,
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )


def test_root_owned_change_requires_root_review(tmp_path):
    root = tmp_path / "repo"
    build(root)

    report = structured(root, path=["README.md"])

    root_scope = by_id(report, "root")
    assert root_scope["status"] == "required"
    assert root_scope["layers"][0] == {
        "id": "manifest",
        "kind": "base",
        "path": ".murlocs/manifest.toml",
        "owners": ["@platform"],
    }
    assert "@platform" in root_scope["owners"]
    assert by_id(report, "api")["status"] == "unaffected"
    assert report["policy"]["version"] == 3


def test_nested_owned_change_reports_chain_layers_owners_and_proof(tmp_path):
    root = tmp_path / "repo"
    build(root)

    report = structured(root, path=["src/api/app/service.py"])
    api = by_id(report, "api")

    assert api["status"] == "required"
    assert api["guidance_chain"] == [
        {"id": "root", "map": "AGENTS.md"},
        {"id": "api", "map": "src/api/AGENTS.md"},
    ]
    assert api["owners"] == ["@api"]
    assert [layer["id"] for layer in api["layers"]] == ["api"]
    assert [item["id"] for item in api["invariants"]] == ["api-contract", "api-design"]
    assert api["checks"] == [
        {
            "name": "api-test",
            "invoke": "touch MUST_NOT_EXIST",
            "location": "pyproject.toml",
            "description": "Check API behavior.",
        }
    ]
    assert not (root / "MUST_NOT_EXIST").exists()


def test_edge_propagates_one_hop_recommendation(tmp_path):
    root = tmp_path / "repo"
    build(root)

    report = structured(root, path=["src/api/app/service.py"])

    worker = by_id(report, "worker")
    assert worker["status"] == "recommended"
    assert worker["reasons"] == ["edge api -[verified-by]-> worker: Worker tests API contracts."]


def test_evidence_and_check_configuration_require_review(tmp_path):
    root = tmp_path / "repo"
    build(root)

    report = structured(root, path=["docs/api.md", "pyproject.toml"])
    api = by_id(report, "api")
    root_scope = by_id(report, "root")

    assert api["status"] == "required"
    assert any("evidence for invariant api-design" in reason for reason in api["reasons"])
    assert any("configures check api-test" in reason for reason in api["reasons"])
    assert root_scope["status"] == "required"


def test_review_protocol_change_requires_every_scope(tmp_path):
    root = tmp_path / "repo"
    build(root)

    report = structured(root, path=[".murlocs/PROTOCOL.md"])

    assert {scope["status"] for scope in report["scopes"]} == {"required"}
    assert all(
        any("network review protocol" in reason for reason in scope["reasons"])
        for scope in report["scopes"]
    )


def test_unowned_nested_path_is_recommended_and_unrelated_path_is_unaffected(tmp_path):
    root = tmp_path / "repo"
    build(root)

    nested = structured(root, path=["src/api/scratch/note.txt"])
    unrelated = structured(root, path=["CHANGELOG.md"])

    assert by_id(nested, "api")["status"] == "recommended"
    assert unrelated["summary"] == {"required": 0, "recommended": 0, "unaffected": 3}


def test_output_is_deterministic_for_explicit_path_order(tmp_path):
    root = tmp_path / "repo"
    build(root)

    first = structured(root, path=["src/worker/job.py", "README.md"])
    second = structured(root, path=["README.md", "src/worker/job.py"])

    assert first == second
    assert first["input"]["paths"] == ["README.md", "src/worker/job.py"]


def test_revision_range_supplies_changed_paths(tmp_path):
    root = tmp_path / "repo"
    build(root)
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    commit_all(root, "base")
    (root / "src/worker/job.py").write_text("VALUE = 2\n", encoding="utf-8")
    commit_all(root, "change")

    report = structured(root, revision_range="HEAD~1..HEAD")

    assert report["input"] == {
        "paths": ["src/worker/job.py"],
        "revision_range": "HEAD~1..HEAD",
    }
    assert by_id(report, "worker")["status"] == "required"


def test_human_output_preserves_review_truth_boundary(tmp_path):
    root = tmp_path / "repo"
    build(root)

    result = invoke(
        "impact",
        "--path",
        "README.md",
        "--path",
        "src/api/app/service.py",
        "--repo",
        str(root),
    )

    assert result.exit_code == 0
    assert "  README.md" in result.output
    assert "  src/api/app/service.py" in result.output
    assert "[required] api" in result.output
    assert "does not claim that guidance is false" in result.output


def test_repeated_terminal_paths_match_programmatic_and_mcp_surfaces(tmp_path):
    root = tmp_path / "repo"
    build(root)
    paths = ["README.md", "src/api/app/service.py"]

    result = invoke(
        "impact",
        "--path",
        paths[0],
        "--path",
        paths[1],
        "--repo",
        str(root),
        "--format",
        "json",
    )

    assert result.exit_code == 0
    terminal = json.loads(result.output)
    programmatic = build_cli().call("impact", repo=str(root), path=paths)
    mcp = structured(root, path=paths)
    assert terminal["input"]["paths"] == paths
    assert terminal == programmatic == mcp


def test_repeated_terminal_paths_accept_equals_syntax_in_order(tmp_path):
    root = tmp_path / "repo"
    build(root)

    result = invoke(
        "impact",
        "--path=src/worker/job.py",
        "--path",
        "README.md",
        "--path=src/api/app/service.py",
        "--repo",
        str(root),
        "--format",
        "json",
    )

    assert result.exit_code == 0
    report = json.loads(result.output)
    assert report["input"]["paths"] == [
        "README.md",
        "src/api/app/service.py",
        "src/worker/job.py",
    ]


def test_inline_dash_path_matches_programmatic_and_mcp_surfaces(tmp_path):
    root = tmp_path / "repo"
    build(root)
    (root / "-dash.py").write_text("VALUE = 1\n", encoding="utf-8")

    result = invoke(
        "impact",
        "--path=-dash.py",
        "--path",
        "README.md",
        "--repo",
        str(root),
        "--format",
        "json",
    )

    assert result.exit_code == 0, result.stderr
    terminal = json.loads(result.output)
    paths = ["-dash.py", "README.md"]
    programmatic = build_cli().call("impact", repo=str(root), path=paths)
    mcp = structured(root, path=paths)
    assert terminal["input"]["paths"] == paths
    assert terminal == programmatic == mcp


def test_generated_root_map_change_routes_every_active_guidance_chain(tmp_path):
    root = tmp_path / "repo"
    build(root)

    report = structured(root, path=["AGENTS.md"])

    assert {scope["status"] for scope in report["scopes"]} == {"required"}
    assert all(
        any("active guidance chain" in reason for reason in scope["reasons"])
        for scope in report["scopes"]
    )


def test_uncompiled_global_layer_change_routes_every_scope(tmp_path):
    root = tmp_path / "repo"
    build(root)
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    layer = root / ".murlocs/layers/api.toml"
    layer.write_text(
        'operating_rules = ["Review every affected guidance chain."]\n\n'
        + layer.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    report = structured(root, path=[".murlocs/layers/api.toml"])

    assert {scope["status"] for scope in report["scopes"]} == {"required"}
    assert all(
        any("affecting AGENTS.md" in reason for reason in scope["reasons"])
        for scope in report["scopes"]
    )


def test_synchronized_global_layer_still_routes_every_scope_from_source_only(tmp_path):
    root = tmp_path / "repo"
    build(root)
    layer = root / ".murlocs/layers/api.toml"
    layer.write_text(
        'operating_rules = ["Review every affected guidance chain."]\n\n'
        + layer.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    commit_all(root, "synchronized global guidance")

    report = structured(root, path=[".murlocs/layers/api.toml"])

    assert report["summary"] == {"required": 3, "recommended": 0, "unaffected": 0}


def test_synchronized_global_source_survives_local_generated_map_drift(tmp_path):
    root = tmp_path / "repo"
    build(root)
    layer = root / ".murlocs/layers/api.toml"
    layer.write_text(
        'operating_rules = ["Review every affected guidance chain."]\n\n'
        + layer.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    generated = root / "src/api/AGENTS.md"
    generated.write_text(
        generated.read_text(encoding="utf-8") + "\nmanual output drift\n",
        encoding="utf-8",
    )

    report = structured(root, path=[".murlocs/layers/api.toml"])

    assert report["summary"] == {"required": 3, "recommended": 0, "unaffected": 0}


def test_missing_root_map_is_drift_for_uncompiled_root_summary_change(tmp_path):
    root = tmp_path / "repo"
    build(root)
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    commit_all(root, "compiled baseline")
    (root / "AGENTS.md").unlink()
    layer = root / ".murlocs/layers/api.toml"
    layer.write_text(
        layer.read_text(encoding="utf-8")
        + '\n[[invariants]]\nid = "api-added"\nscope = "api"\n'
        + 'statement = "An added API invariant."\nseverity = "important"\n'
        + 'verification = "manual"\nevidence_file = "docs/api.md"\n'
        + 'anchor = "API design"\n',
        encoding="utf-8",
    )

    report = structured(root, path=[".murlocs/layers/api.toml"])

    assert report["summary"] == {"required": 3, "recommended": 0, "unaffected": 0}


def test_scope_local_layer_change_remains_focused_before_and_after_compile(tmp_path):
    root = tmp_path / "repo"
    build(root)
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    layer = root / ".murlocs/layers/api.toml"
    layer.write_text(
        layer.read_text(encoding="utf-8")
        + '\n[judgments.api]\nadvocate = ["Prefer explicit API boundaries."]\n',
        encoding="utf-8",
    )

    before_compile = structured(root, path=[".murlocs/layers/api.toml"])
    assert by_id(before_compile, "api")["status"] == "required"
    assert by_id(before_compile, "worker")["status"] == "recommended"
    assert by_id(before_compile, "root")["status"] == "unaffected"

    assert invoke("compile", "--repo", str(root)).exit_code == 0
    after_compile = structured(root, path=[".murlocs/layers/api.toml"])
    assert by_id(after_compile, "api")["status"] == "required"
    assert by_id(after_compile, "worker")["status"] == "recommended"
    assert by_id(after_compile, "root")["status"] == "unaffected"


def test_existing_global_content_does_not_widen_exact_local_rendered_drift(tmp_path):
    root = tmp_path / "repo"
    build(root)
    layer = root / ".murlocs/layers/api.toml"
    layer.write_text(
        'operating_rules = ["Existing global rule."]\n\n' + layer.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    layer.write_text(
        layer.read_text(encoding="utf-8")
        + '\n[judgments.api]\nadvocate = ["Prefer explicit API boundaries."]\n',
        encoding="utf-8",
    )

    report = structured(root, path=[".murlocs/layers/api.toml"])

    assert by_id(report, "api")["status"] == "required"
    assert by_id(report, "worker")["status"] == "recommended"
    assert by_id(report, "root")["status"] == "unaffected"


def test_unrelated_worker_drift_is_not_attributed_to_explicit_api_source(tmp_path):
    root = tmp_path / "repo"
    build(root)
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    worker = root / ".murlocs/layers/worker.toml"
    worker.write_text(
        worker.read_text(encoding="utf-8")
        + '\n[[invariants]]\nid = "worker-queue"\nscope = "worker"\n'
        + 'statement = "Worker queues stay bounded."\nseverity = "important"\n'
        + 'verification = "manual"\nevidence_file = "docs/api.md"\n'
        + 'anchor = "API design"\n',
        encoding="utf-8",
    )

    report = structured(root, path=[".murlocs/layers/api.toml"])

    assert by_id(report, "api")["status"] == "required"
    assert by_id(report, "worker")["status"] == "recommended"
    assert by_id(report, "root")["status"] == "unaffected"
    assert not any(
        "affecting src/worker/AGENTS.md" in reason
        for scope in report["scopes"]
        for reason in scope["reasons"]
    )


def test_two_stale_sources_use_git_semantics_to_keep_explicit_local_edit_focused(
    tmp_path,
):
    root = tmp_path / "repo"
    build(root)
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    commit_all(root, "rendered baseline")
    api = root / ".murlocs/layers/api.toml"
    api.write_text(
        api.read_text(encoding="utf-8")
        + '\n[judgments.api]\nadvocate = ["Prefer explicit API boundaries."]\n',
        encoding="utf-8",
    )
    worker = root / ".murlocs/layers/worker.toml"
    worker.write_text(
        'operating_rules = ["Review worker-wide changes."]\n\n'
        + worker.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    report = structured(root, path=[".murlocs/layers/api.toml"])

    assert by_id(report, "api")["status"] == "required"
    assert by_id(report, "worker")["status"] == "recommended"
    assert by_id(report, "root")["status"] == "unaffected"


def test_non_git_sources_report_conservative_root_ambiguity_with_one_git_probe(
    tmp_path, monkeypatch
):
    root = tmp_path / "repo"
    build(root)
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    api = root / ".murlocs/layers/api.toml"
    api.write_text(
        api.read_text(encoding="utf-8")
        + '\n[judgments.api]\nadvocate = ["Prefer explicit API boundaries."]\n',
        encoding="utf-8",
    )
    worker = root / ".murlocs/layers/worker.toml"
    worker.write_text(
        'operating_rules = ["Review worker-wide changes."]\n\n'
        + worker.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    calls = []
    real_run = subprocess.run

    def tracked_run(*args, **kwargs):
        calls.append(args[0])
        return real_run(*args, **kwargs)

    monkeypatch.setattr(impact_module.subprocess, "run", tracked_run)

    report = structured(root, path=[".murlocs/layers/api.toml"])

    assert not (root / ".git").exists()
    assert len(calls) == 1
    assert "rev-list" in calls[0]
    assert {scope["status"] for scope in report["scopes"]} == {"required"}
    assert all(
        any("cannot be attributed more narrowly" in reason for reason in scope["reasons"])
        for scope in report["scopes"]
    )


def test_concurrent_source_swap_after_history_probe_fails_closed(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    build(root)
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    commit_all(root, "compiled baseline")
    api = root / ".murlocs/layers/api.toml"
    api.write_text(
        api.read_text(encoding="utf-8") + '\n[judgments.api]\nadvocate = ["Initial local edit."]\n',
        encoding="utf-8",
    )
    worker = root / ".murlocs/layers/worker.toml"
    worker.write_text(
        'operating_rules = ["Review worker-wide changes."]\n\n'
        + worker.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    calls = []
    real_run = subprocess.run

    def swap_after_history(*args, **kwargs):
        result = real_run(*args, **kwargs)
        calls.append(args[0])
        if "rev-list" in args[0]:
            api.write_text(
                api.read_text(encoding="utf-8").replace(
                    "Initial local edit.", "Concurrent local edit."
                ),
                encoding="utf-8",
            )
        return result

    monkeypatch.setattr(impact_module.subprocess, "run", swap_after_history)

    report = structured(root, path=[".murlocs/layers/api.toml"])

    assert {scope["status"] for scope in report["scopes"]} == {"required"}
    assert len(calls) == 1
    assert "rev-list" in calls[0]
    assert "Concurrent local edit." in api.read_text(encoding="utf-8")


def test_unsupported_no_lazy_fetch_option_fails_closed_without_retry(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    build(root)
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    commit_all(root, "compiled baseline")
    api = root / ".murlocs/layers/api.toml"
    api.write_text(
        api.read_text(encoding="utf-8")
        + '\n[judgments.api]\nadvocate = ["Prefer explicit API boundaries."]\n',
        encoding="utf-8",
    )
    worker = root / ".murlocs/layers/worker.toml"
    worker.write_text(
        'operating_rules = ["Review worker-wide changes."]\n\n'
        + worker.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    calls = []

    def unsupported_git(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(
            args, 129, stdout=b"", stderr=b"unknown option: --no-lazy-fetch"
        )

    monkeypatch.setattr(impact_module.subprocess, "run", unsupported_git)

    report = structured(root, path=[".murlocs/layers/api.toml"])

    assert {scope["status"] for scope in report["scopes"]} == {"required"}
    assert len(calls) == 1
    assert "--no-lazy-fetch" in calls[0]


def test_locked_baseline_lookup_is_batched_bounded_and_fails_closed_on_exhaustion(
    tmp_path, monkeypatch
):
    root = tmp_path / "repo"
    build(root)
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    commit_all(root, "compiled baseline")
    api = root / ".murlocs/layers/api.toml"
    for index in range(impact_module.GIT_SOURCE_HISTORY_LIMIT + 1):
        api.write_text(
            api.read_text(encoding="utf-8") + f"\n# local history {index}\n",
            encoding="utf-8",
        )
        commit_all(root, f"local source history {index}")
    worker = root / ".murlocs/layers/worker.toml"
    worker.write_text(
        'operating_rules = ["Review worker-wide changes."]\n\n'
        + worker.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    calls = []
    real_run = subprocess.run

    def tracked_run(*args, **kwargs):
        calls.append((args[0], kwargs))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(impact_module.subprocess, "run", tracked_run)

    report = structured(root, path=[".murlocs/layers/api.toml"])

    assert {scope["status"] for scope in report["scopes"]} == {"required"}
    assert len(calls) == 3
    assert "rev-list" in calls[0][0]
    assert f"--max-count={impact_module.GIT_SOURCE_HISTORY_LIMIT}" in calls[0][0]
    assert calls[1][0][-1] == "--batch-check"
    assert calls[2][0][-1] == "--batch"
    assert all("show" not in call[0] for call in calls)
    assert all("--no-lazy-fetch" in call[0] for call in calls)
    assert all(call[1]["env"]["GIT_NO_LAZY_FETCH"] == "1" for call in calls)
    assert all(call[1]["env"]["GIT_OPTIONAL_LOCKS"] == "0" for call in calls)
    assert all(
        any("cannot be attributed more narrowly" in reason for reason in scope["reasons"])
        for scope in report["scopes"]
    )


def test_raw_batched_lookup_never_executes_diff_filters_or_hooks(tmp_path):
    root = tmp_path / "repo"
    build(root)
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    sentinel = root / "GIT_DRIVER_EXECUTED"
    driver = root / "git-driver.sh"
    driver.write_text(
        f'#!/bin/sh\ntouch "{sentinel}"\ncat "$1" 2>/dev/null || cat\n',
        encoding="utf-8",
    )
    driver.chmod(0o755)
    hooks = root / "hooks"
    hooks.mkdir()
    for name in ("post-checkout", "post-merge", "pre-commit", "reference-transaction"):
        hook = hooks / name
        hook.write_text(f'#!/bin/sh\ntouch "{sentinel}"\n', encoding="utf-8")
        hook.chmod(0o755)
    (root / ".gitattributes").write_text("*.toml diff=sentinel filter=sentinel\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    commit_all(root, "compiled baseline with inert drivers")
    for key, value in (
        ("diff.sentinel.textconv", str(driver)),
        ("filter.sentinel.clean", str(driver)),
        ("filter.sentinel.smudge", str(driver)),
        ("filter.sentinel.process", str(driver)),
        ("core.hooksPath", str(hooks)),
    ):
        subprocess.run(["git", "config", key, value], cwd=root, check=True, capture_output=True)
    api = root / ".murlocs/layers/api.toml"
    api.write_text(
        api.read_text(encoding="utf-8")
        + '\n[judgments.api]\nadvocate = ["Prefer explicit API boundaries."]\n',
        encoding="utf-8",
    )
    worker = root / ".murlocs/layers/worker.toml"
    worker.write_text(
        'operating_rules = ["Review worker-wide changes."]\n\n'
        + worker.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    report = structured(root, path=[".murlocs/layers/api.toml"])

    assert by_id(report, "api")["status"] == "required"
    assert by_id(report, "worker")["status"] == "recommended"
    assert by_id(report, "root")["status"] == "unaffected"
    assert not sentinel.exists()
    assert not (root / "MUST_NOT_EXIST").exists()


def test_real_git_batch_preserves_special_path_spaces_and_duplicate_blob_order(
    tmp_path, monkeypatch
):
    root = tmp_path / "repo"
    build(root)
    special_path = ".murlocs/layers/- api:glob[*?].toml"
    manifest = root / ".murlocs/manifest.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(".murlocs/layers/api.toml", special_path),
        encoding="utf-8",
    )
    api = root / ".murlocs/layers/api.toml"
    special = root / special_path
    api.rename(special)
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    commit_all(root, "compiled special-path baseline")
    baseline = special.read_bytes()
    special.write_text(
        special.read_text(encoding="utf-8") + "\n# intermediate blob\n",
        encoding="utf-8",
    )
    commit_all(root, "intermediate special-path blob")
    special.write_bytes(baseline)
    commit_all(root, "restore duplicate baseline blob")
    history = subprocess.run(
        ["git", "rev-list", "--all", "--", f":(literal){special_path}"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    blob_ids = [
        subprocess.run(
            ["git", "rev-parse", f"{commit}:{special_path}"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        for commit in history
    ]
    assert len(blob_ids) > len(set(blob_ids))
    special.write_text(
        special.read_text(encoding="utf-8")
        + '\n[judgments.api]\nadvocate = ["Current local edit."]\n',
        encoding="utf-8",
    )
    worker = root / ".murlocs/layers/worker.toml"
    worker.write_text(
        'operating_rules = ["Review worker-wide changes."]\n\n'
        + worker.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    calls = []
    real_run = subprocess.run

    def tracked_run(*args, **kwargs):
        calls.append((args[0], kwargs))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(impact_module.subprocess, "run", tracked_run)

    report = structured(root, path=[special_path])

    assert by_id(report, "api")["status"] == "required"
    assert by_id(report, "worker")["status"] == "recommended"
    assert by_id(report, "root")["status"] == "unaffected"
    assert len(calls) == 3
    assert calls[1][1]["input"] == calls[2][1]["input"]
    assert special_path.encode("utf-8") in calls[1][1]["input"]
    assert all("%(rest)" not in argument for argument in calls[1][0])


def test_newline_layer_path_is_never_split_into_git_batch_input(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    build(root)
    newline_path = ".murlocs/layers/api\n.toml"
    manifest = root / ".murlocs/manifest.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            ".murlocs/layers/api.toml", ".murlocs/layers/api\\n.toml"
        ),
        encoding="utf-8",
    )
    api = root / ".murlocs/layers/api.toml"
    newline_source = root / newline_path
    api.rename(newline_source)
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    newline_source.write_text(
        newline_source.read_text(encoding="utf-8")
        + '\n[judgments.api]\nadvocate = ["Current local edit."]\n',
        encoding="utf-8",
    )
    worker = root / ".murlocs/layers/worker.toml"
    worker.write_text(
        'operating_rules = ["Review worker-wide changes."]\n\n'
        + worker.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    def unexpected_git(*args, **kwargs):
        pytest.fail("newline source paths must fail closed before Git batch input")

    monkeypatch.setattr(impact_module.subprocess, "run", unexpected_git)

    report = structured(root, path=[newline_path])

    assert {scope["status"] for scope in report["scopes"]} == {"required"}
    assert all(
        any("cannot be attributed more narrowly" in reason for reason in scope["reasons"])
        for scope in report["scopes"]
    )


def test_missing_locked_git_blob_falls_back_conservatively(tmp_path):
    root = tmp_path / "repo"
    build(root)
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "gc.auto", "0"], cwd=root, check=True, capture_output=True)
    commit_all(root, "compiled baseline")
    blob = subprocess.run(
        ["git", "rev-parse", "HEAD:.murlocs/layers/api.toml"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    object_path = root / ".git/objects" / blob[:2] / blob[2:]
    assert object_path.is_file()
    object_path.unlink()
    api = root / ".murlocs/layers/api.toml"
    api.write_text(
        api.read_text(encoding="utf-8")
        + '\n[[invariants]]\nid = "api-added"\nscope = "api"\n'
        + 'statement = "An added API invariant."\nseverity = "important"\n'
        + 'verification = "manual"\nevidence_file = "docs/api.md"\n'
        + 'anchor = "API design"\n',
        encoding="utf-8",
    )

    report = structured(root, path=[".murlocs/layers/api.toml"])

    assert report["summary"] == {"required": 3, "recommended": 0, "unaffected": 0}


def test_git_batch_parser_accepts_missing_entries_and_rejects_partial_output():
    oid = b"a" * 40
    object_names = ("first:path", "missing:path")
    sizes = oid + b" blob 3\nmissing:path missing\n"
    valid = oid + b" blob 3\nabc\nmissing:path missing\n"
    metadata = ((oid, 3), None)

    assert impact_module._parse_git_batch_sizes(sizes, object_names) == metadata
    assert impact_module._parse_git_batch_sizes(sizes, object_names[:1]) is None
    assert impact_module._parse_git_batch_blobs(valid, object_names, metadata) == (b"abc", None)
    assert impact_module._parse_git_batch_blobs(valid[:-1], object_names, metadata) is None
    assert impact_module._parse_git_batch_blobs(valid + b"extra", object_names, metadata) is None


def test_git_batch_parsers_reject_invalid_ids_sequence_sizes_and_caps():
    oid = b"a" * 40
    other_oid = b"b" * 40
    object_names = ("candidate:path",)
    metadata = ((oid, 3),)
    locked_bytes = b"abc"

    assert impact_module._parse_git_commit_ids(oid + b"\ninvalid\n") is None
    assert impact_module._parse_git_commit_ids(b"A" * 40 + b"\n") is None
    assert impact_module._parse_git_batch_sizes(b"z" * 40 + b" blob 3\n", object_names) is None
    assert (
        impact_module._parse_git_batch_sizes(oid + b" blob " + b"9" * 5000 + b"\n", object_names)
        is None
    )
    assert impact_module._parse_git_batch_sizes(oid + b" blob 3 extra\n", object_names) is None
    assert impact_module._parse_git_batch_sizes(b"other:path missing\n", object_names) is None
    corrupt_headers = (
        other_oid + b" blob 3\n" + locked_bytes + b"\n",
        oid + b" blob 4\n" + locked_bytes + b"x\n",
        oid + b" blob 3 extra\n" + locked_bytes + b"\n",
        b"candidate:path missing\n",
        oid + b" blob " + b"9" * 5000 + b"\n",
    )
    assert all(
        impact_module._parse_git_batch_blobs(output, object_names, metadata) is None
        for output in corrupt_headers
    )
    oversized = impact_module.GIT_SOURCE_BLOB_LIMIT + 1
    assert (
        impact_module._parse_git_batch_blobs(
            oid + f" blob {oversized}\n".encode("ascii"),
            object_names,
            ((oid, oversized),),
        )
        is None
    )
    assert (
        impact_module._parse_git_batch_blobs(
            oid + b" blob 3\n" + locked_bytes + b"\n",
            object_names,
            (None,),
        )
        is None
    )


def test_fake_batch_oid_with_locked_bytes_cannot_narrow_routing(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    build(root)
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    commit_all(root, "compiled baseline")
    api = root / ".murlocs/layers/api.toml"
    locked_bytes = api.read_bytes()
    api.write_text(
        api.read_text(encoding="utf-8")
        + '\n[judgments.api]\nadvocate = ["Prefer explicit API boundaries."]\n',
        encoding="utf-8",
    )
    worker = root / ".murlocs/layers/worker.toml"
    worker.write_text(
        'operating_rules = ["Review worker-wide changes."]\n\n'
        + worker.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    calls = []

    def fake_git(args, **kwargs):
        calls.append(args)
        if "rev-list" in args:
            stdout = b"a" * 40 + b"\n"
        elif "--batch-check" in args:
            stdout = b"z" * 40 + f" blob {len(locked_bytes)}\n".encode("ascii")
        else:
            stdout = (
                b"z" * 40 + f" blob {len(locked_bytes)}\n".encode("ascii") + locked_bytes + b"\n"
            )
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr=b"")

    monkeypatch.setattr(impact_module.subprocess, "run", fake_git)

    report = structured(root, path=[".murlocs/layers/api.toml"])

    assert {scope["status"] for scope in report["scopes"]} == {"required"}
    assert len(calls) == 2
    assert not any(call[-1] == "--batch" for call in calls)


def test_oversized_historical_blob_fails_closed_before_content_batch(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    build(root)
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    commit_all(root, "compiled baseline")
    api = root / ".murlocs/layers/api.toml"
    baseline = api.read_bytes()
    api.write_text(
        api.read_text(encoding="utf-8")
        + "\n# "
        + "x" * (impact_module.GIT_SOURCE_BLOB_LIMIT + 1)
        + "\n",
        encoding="utf-8",
    )
    commit_all(root, "oversized historical source")
    api.write_bytes(baseline)
    commit_all(root, "restore compiled source")
    api.write_text(
        api.read_text(encoding="utf-8")
        + '\n[judgments.api]\nadvocate = ["Prefer explicit API boundaries."]\n',
        encoding="utf-8",
    )
    worker = root / ".murlocs/layers/worker.toml"
    worker.write_text(
        'operating_rules = ["Review worker-wide changes."]\n\n'
        + worker.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    calls = []
    real_run = subprocess.run

    def tracked_run(*args, **kwargs):
        calls.append(args[0])
        return real_run(*args, **kwargs)

    monkeypatch.setattr(impact_module.subprocess, "run", tracked_run)

    report = structured(root, path=[".murlocs/layers/api.toml"])

    assert {scope["status"] for scope in report["scopes"]} == {"required"}
    assert len(calls) == 2
    assert "rev-list" in calls[0]
    assert calls[1][-1] == "--batch-check"
    assert not any(call[-1] == "--batch" for call in calls)


def test_cumulative_historical_blob_cap_blocks_content_batch(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    build(root)
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    commits = [f"{index:040x}" for index in range(9)]
    calls = []

    def bounded_git(args, **kwargs):
        calls.append(args)
        if "rev-list" in args:
            stdout = ("\n".join(commits) + "\n").encode("ascii")
        elif "--batch-check" in args:
            stdout = b"".join(
                f"{commit} blob {impact_module.GIT_SOURCE_BLOB_LIMIT}\n".encode("ascii")
                for commit in commits
            )
        else:
            pytest.fail("content batch must not run after cumulative size overflow")
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr=b"")

    monkeypatch.setattr(impact_module.subprocess, "run", bounded_git)

    result = impact_module._workspace_source_changes_root_render(
        load_manifest(root), ".murlocs/layers/api.toml"
    )

    assert result is None
    assert len(calls) == 2


def test_committed_source_with_uncompiled_root_semantics_uses_locked_git_blob(tmp_path):
    root = tmp_path / "repo"
    build(root)
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    commit_all(root, "compiled baseline")
    api = root / ".murlocs/layers/api.toml"
    api.write_text(
        api.read_text(encoding="utf-8")
        + '\n[[invariants]]\nid = "api-added"\nscope = "api"\n'
        + 'statement = "An added API invariant."\nseverity = "important"\n'
        + 'verification = "manual"\nevidence_file = "docs/api.md"\n'
        + 'anchor = "API design"\n',
        encoding="utf-8",
    )
    commit_all(root, "commit source without compiling")

    report = structured(root, path=[".murlocs/layers/api.toml"])

    assert report["summary"] == {"required": 3, "recommended": 0, "unaffected": 0}


def test_workspace_invariant_addition_routes_root_but_statement_edit_stays_focused(
    tmp_path,
):
    root = tmp_path / "repo"
    build(root)
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    layer = root / ".murlocs/layers/api.toml"
    layer.write_text(
        layer.read_text(encoding="utf-8").replace(
            "The API design is documented.", "The API design stays documented."
        ),
        encoding="utf-8",
    )

    focused = structured(root, path=[".murlocs/layers/api.toml"])
    assert by_id(focused, "api")["status"] == "required"
    assert by_id(focused, "root")["status"] == "unaffected"

    assert invoke("compile", "--repo", str(root)).exit_code == 0
    layer.write_text(
        layer.read_text(encoding="utf-8")
        + '\n[[invariants]]\nid = "api-added"\nscope = "api"\n'
        + 'statement = "An added API invariant."\nseverity = "important"\n'
        + 'verification = "manual"\nevidence_file = "docs/api.md"\n'
        + 'anchor = "API design"\n',
        encoding="utf-8",
    )

    widened = structured(root, path=[".murlocs/layers/api.toml"])
    assert widened["summary"] == {"required": 3, "recommended": 0, "unaffected": 0}


def test_revision_source_only_routes_removal_of_last_global_field(tmp_path):
    root = tmp_path / "repo"
    build(root)
    layer = root / ".murlocs/layers/api.toml"
    global_header = 'operating_rules = ["Review every affected guidance chain."]\n\n'
    layer.write_text(global_header + layer.read_text(encoding="utf-8"), encoding="utf-8")
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    commit_all(root, "global baseline")

    layer.write_text(
        layer.read_text(encoding="utf-8").removeprefix(global_header), encoding="utf-8"
    )
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    report = build_impact_report(
        load_manifest(root),
        (".murlocs/layers/api.toml",),
        revision_range="HEAD",
    )

    assert report["summary"] == {"required": 3, "recommended": 0, "unaffected": 0}


def test_revision_local_edit_ignores_unchanged_global_content(tmp_path):
    root = tmp_path / "repo"
    build(root)
    layer = root / ".murlocs/layers/api.toml"
    layer.write_text(
        'operating_rules = ["Existing global rule."]\n\n' + layer.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    commit_all(root, "global baseline")

    layer.write_text(
        layer.read_text(encoding="utf-8")
        + '\n[judgments.api]\nadvocate = ["Prefer explicit API boundaries."]\n',
        encoding="utf-8",
    )
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    report = build_impact_report(
        load_manifest(root),
        (".murlocs/layers/api.toml",),
        revision_range="HEAD",
    )

    assert by_id(report, "api")["status"] == "required"
    assert by_id(report, "worker")["status"] == "recommended"
    assert by_id(report, "root")["status"] == "unaffected"


@pytest.mark.parametrize("change", ["scope-add", "invariant-add", "command-backed"])
def test_revision_root_summary_semantics_route_every_scope(tmp_path, change):
    root = tmp_path / "repo"
    build(root)
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    commit_all(root, "summary baseline")
    layer = root / ".murlocs/layers/api.toml"
    text = layer.read_text(encoding="utf-8")
    if change == "scope-add":
        text += (
            '\n[[scopes]]\nid = "api-child"\npath = "src/api/app"\n'
            + 'map = "src/api/app/AGENTS.md"\npoint_of_view = "API child."\n'
            + "owns = []\nguardrails = []\nedges = []\n"
        )
    elif change == "invariant-add":
        text += (
            '\n[[invariants]]\nid = "api-added"\nscope = "api"\n'
            + 'statement = "An added API invariant."\nseverity = "important"\n'
            + 'verification = "manual"\nevidence_file = "docs/api.md"\n'
            + 'anchor = "API design"\n'
        )
    else:
        text = text.replace(
            'verification = "manual"\nevidence_file = "docs/api.md"\nanchor = "API design"',
            'verification = "command"\nenforced_by = "api-test"',
        )
    layer.write_text(text, encoding="utf-8")

    report = structured(root, revision_range="HEAD")

    assert {scope["status"] for scope in report["scopes"]} == {"required"}


def test_revision_invariant_statement_only_edit_remains_focused(tmp_path):
    root = tmp_path / "repo"
    build(root)
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    commit_all(root, "invariant baseline")
    layer = root / ".murlocs/layers/api.toml"
    layer.write_text(
        layer.read_text(encoding="utf-8").replace(
            "The API design is documented.", "The API design stays documented."
        ),
        encoding="utf-8",
    )

    report = structured(root, revision_range="HEAD")

    assert by_id(report, "api")["status"] == "required"
    assert by_id(report, "worker")["status"] == "recommended"
    assert by_id(report, "root")["status"] == "unaffected"


def test_explicit_global_source_semantics_survive_union_with_worker_revision(tmp_path):
    root = tmp_path / "repo"
    build(root)
    api = root / ".murlocs/layers/api.toml"
    api.write_text(
        'operating_rules = ["Existing global rule."]\n\n' + api.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    commit_all(root, "global baseline")
    worker = root / ".murlocs/layers/worker.toml"
    worker.write_text(
        worker.read_text(encoding="utf-8")
        + '\n[judgments.worker]\nadvocate = ["Keep worker queues bounded."]\n',
        encoding="utf-8",
    )

    result = invoke(
        "impact",
        "--path",
        ".murlocs/layers/api.toml",
        "--revision-range",
        "HEAD",
        "--repo",
        str(root),
        "--format",
        "json",
    )
    assert result.exit_code == 0, result.stderr
    report = json.loads(result.output)
    assert report["summary"] == {"required": 3, "recommended": 0, "unaffected": 0}


def test_revision_content_inspection_never_executes_textconv(tmp_path):
    root = tmp_path / "repo"
    build(root)
    sentinel = root / "TEXTCONV_EXECUTED"
    driver = root / "textconv-driver.sh"
    driver.write_text(f'#!/bin/sh\ntouch "{sentinel}"\ncat "$1"\n', encoding="utf-8")
    driver.chmod(0o755)
    (root / ".gitattributes").write_text("*.toml diff=sentinel\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "diff.sentinel.textconv", str(driver)],
        cwd=root,
        check=True,
        capture_output=True,
    )
    commit_all(root, "textconv baseline")
    api = root / ".murlocs/layers/api.toml"
    api.write_text(
        'operating_rules = ["Revision global rule."]\n\n' + api.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    report = structured(root, revision_range="HEAD")

    assert report["summary"] == {"required": 3, "recommended": 0, "unaffected": 0}
    assert not sentinel.exists()


@pytest.mark.issue(88)
def test_annotation_explicit_path_is_conservative_path_only_routing(tmp_path):
    root = tmp_path / "repo"
    build(root)
    add_annotation(root)

    report = structured(root, path=["src/api/app/service.py"])

    assert report["annotations"] == {
        "comparison": "path-only",
        "changes": [],
        "uncertainty": [],
    }
    assert by_id(report, "api")["status"] == "required"
    assert any("path-only evidence" in item for item in by_id(report, "api")["reasons"])
    assert (
        "does not claim that guidance is false"
        in invoke("impact", "--path", "src/api/app/service.py", "--repo", str(root)).output
    )


@pytest.mark.issue(88)
def test_annotation_revision_reports_move_without_claiming_semantic_staleness(tmp_path):
    root = tmp_path / "repo"
    build(root)
    add_annotation(root)
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    commit_all(root, "annotation baseline")
    source = root / "src/api/app/service.py"
    source.write_text(
        'VALUE = 1\n# murlocs:annotation/v1 evidence "api.marker"\n', encoding="utf-8"
    )

    report = structured(root, revision_range="HEAD")

    assert report["annotations"]["comparison"] == "compared"
    assert report["annotations"]["changes"] == [
        {
            "id": "api.marker",
            "kind": "moved",
            "invariant": "api-design",
            "scope": "api",
            "owners": ["@api"],
            "before": [{"file": "src/api/app/service.py", "line": 1}],
            "after": [{"file": "src/api/app/service.py", "line": 2}],
        }
    ]
    reason = next(item for item in by_id(report, "api")["reasons"] if "attachment moved" in item)
    assert "does not assert that the invariant is semantically false" in reason
    terminal = invoke("impact", "--revision-range", "HEAD", "--repo", str(root)).output
    assert "Annotation comparison: compared" in terminal
    assert "api.marker: moved" in terminal


@pytest.mark.parametrize(
    ("content", "kind"),
    [
        ("VALUE = 1\n", "removed"),
        (
            '# murlocs:annotation/v1 evidence "api.marker"\n'
            '# murlocs:annotation/v1 evidence "api.marker"\n',
            "duplicated",
        ),
    ],
)
def test_annotation_revision_reports_removal_and_duplication(tmp_path, content, kind):
    root = tmp_path / "repo"
    build(root)
    add_annotation(root)
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    commit_all(root, "annotation baseline")
    (root / "src/api/app/service.py").write_text(content, encoding="utf-8")

    report = structured(root, revision_range="HEAD")

    assert kind in {item["kind"] for item in report["annotations"]["changes"]}
    assert by_id(report, "api")["status"] == "required"


@pytest.mark.issue(88)
def test_annotation_revision_reports_declaration_change_and_surface_parity(tmp_path):
    root = tmp_path / "repo"
    build(root)
    add_annotation(root)
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    commit_all(root, "annotation baseline")
    layer = root / ".murlocs/layers/api.toml"
    layer.write_text(
        layer.read_text(encoding="utf-8").replace("api.marker", "api.changed"),
        encoding="utf-8",
    )
    source = root / "src/api/app/service.py"
    source.write_text(
        '# murlocs:annotation/v1 evidence "api.changed"\nVALUE = 1\n', encoding="utf-8"
    )

    terminal = invoke("impact", "--revision-range", "HEAD", "--repo", str(root), "--format", "json")
    assert terminal.exit_code == 0
    report = json.loads(terminal.output)
    assert report == build_cli().call("impact", repo=str(root), revision_range="HEAD")
    assert report == structured(root, revision_range="HEAD")
    assert {item["kind"] for item in report["annotations"]["changes"]} == {
        "added",
        "removed",
    }


@pytest.mark.issue(88)
def test_annotation_unavailable_baseline_never_reports_declared_attachment_unaffected(
    tmp_path,
):
    root = tmp_path / "repo"
    build(root)
    add_annotation(root)

    report = build_impact_report(
        load_manifest(root),
        ("src/api/app/service.py",),
        revision_range="not-a-revision",
        revision_paths=("src/api/app/service.py",),
    )

    assert report["annotations"]["comparison"] == "uncertain"
    assert report["annotations"]["uncertainty"]
    assert by_id(report, "api")["status"] == "required"
    assert any("not treated as unaffected" in item for item in by_id(report, "api")["reasons"])


def test_annotation_explicit_path_reports_unavailable_declared_source_as_uncertain(tmp_path):
    root = tmp_path / "repo"
    build(root)
    add_annotation(root)
    source = root / "src/api/app/service.py"
    outside = tmp_path / "outside.py"
    outside.write_text('# murlocs:annotation/v1 evidence "api.marker"\n', encoding="utf-8")
    source.unlink()
    source.symlink_to(outside)

    report = build_impact_report(
        load_manifest(root),
        ("src/api/app/service.py",),
        revision_range=None,
        explicit_paths=("src/api/app/service.py",),
    )

    assert report["annotations"]["comparison"] == "uncertain"
    assert report["annotations"]["uncertainty"]
    reasons = by_id(report, "api")["reasons"]
    assert any("path-only evidence" in item for item in reasons)
    assert any("not treated as unaffected" in item for item in reasons)


def test_annotation_oversize_current_source_is_uncertain_not_removed(tmp_path):
    root = tmp_path / "repo"
    build(root)
    add_annotation(root)
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    commit_all(root, "annotation baseline")
    (root / "src/api/app/service.py").write_bytes(b"x" * (64 * 1024 + 1))

    report = structured(root, revision_range="HEAD")

    assert report["annotations"]["comparison"] == "uncertain"
    assert report["annotations"]["changes"] == []
    assert report["annotations"]["uncertainty"]


def test_annotation_confirmed_source_deletion_is_a_removal_not_uncertainty(tmp_path):
    root = tmp_path / "repo"
    build(root)
    add_annotation(root)
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    commit_all(root, "annotation baseline")
    (root / "src/api/app/service.py").unlink()

    report = structured(root, revision_range="HEAD")

    assert report["annotations"]["comparison"] == "compared"
    assert [item["kind"] for item in report["annotations"]["changes"]] == ["removed"]


def test_annotation_rename_reports_declaration_change_and_attachment_move(tmp_path):
    root = tmp_path / "repo"
    build(root)
    add_annotation(root)
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    commit_all(root, "annotation baseline")
    layer = root / ".murlocs/layers/api.toml"
    layer.write_text(
        layer.read_text(encoding="utf-8").replace(
            "src/api/app/service.py", "src/api/app/renamed.py"
        ),
        encoding="utf-8",
    )
    (root / "src/api/app/service.py").rename(root / "src/api/app/renamed.py")

    report = structured(root, revision_range="HEAD")

    assert [item["kind"] for item in report["annotations"]["changes"]] == [
        "declaration-changed",
        "moved",
    ]


def test_annotation_revision_git_reads_are_no_lazy_fetch_bounded_and_no_replace(
    tmp_path, monkeypatch
):
    root = tmp_path / "repo"
    build(root)
    add_annotation(root)
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    commit_all(root, "annotation baseline")
    source = root / "src/api/app/service.py"
    source.write_text(
        'VALUE = 1\n# murlocs:annotation/v1 evidence "api.marker"\n', encoding="utf-8"
    )
    calls: list[tuple[list[str], dict[str, object]]] = []
    original = impact_module.subprocess.run

    def observe(command, *args, **kwargs):
        if command and command[0] == "git":
            calls.append((command, kwargs))
        return original(command, *args, **kwargs)

    monkeypatch.setattr(impact_module.subprocess, "run", observe)
    structured(root, revision_range="HEAD")
    impact_module._revision_mentions_global_guidance(root, "HEAD", ".murlocs/layers/api.toml")

    assert calls
    assert any("--no-lazy-fetch" in command for command, _ in calls)
    for command, kwargs in calls:
        assert "--no-replace-objects" in command
        assert kwargs["timeout"] == impact_module.GIT_READ_TIMEOUT_SECONDS
        assert kwargs["env"]["GIT_NO_LAZY_FETCH"] == "1"


def test_repeatable_normalizer_preserves_root_option_values_and_coalesces_selected_flags():
    argv = [
        "--output-file",
        "impact",
        "init",
        "--coverage-root",
        "src",
        "--coverage-root",
        "tests",
        "--name",
        "impact",
    ]

    normalized, protected = _normalize_repeatable_options(
        argv,
        command_index=2,
        option_flags={"--coverage-root": "--coverage-root"},
    )
    assert protected == {}
    assert normalized == [
        "--output-file",
        "impact",
        "init",
        "--coverage-root",
        "src",
        "tests",
        "--name",
        "impact",
    ]


def test_repeatable_normalizer_protects_inline_dash_values_and_rejects_empty_occurrences():
    normalized, protected = _normalize_repeatable_options(
        ["impact", "--path=-dash.py", "--path", "README.md"],
        command_index=0,
        option_flags={"--path": "--path"},
    )

    assert normalized[:2] == ["impact", "--path"]
    assert normalized[3:] == ["README.md"]
    assert protected == {normalized[2]: "-dash.py"}

    with pytest.raises(ValueError, match="--path"):
        _normalize_repeatable_options(
            ["impact", "--path", "README.md", "--path", "--format", "json"],
            command_index=0,
            option_flags={"--path": "--path"},
        )
    with pytest.raises(ValueError, match="--path"):
        _normalize_repeatable_options(
            ["impact", "--path="],
            command_index=0,
            option_flags={"--path": "--path"},
        )


@pytest.mark.parametrize(
    "flag",
    [
        "--coverage-root",
        "--owners",
        "--defer",
        "--scope",
        "--root-owner",
        "--check",
        "--coverage-exemption",
        "--path",
    ],
)
def test_every_repeatable_option_can_protect_an_inline_dash_value(flag):
    normalized, protected = _normalize_repeatable_options(
        ["command", f"{flag}=-dash-value", flag, "ordinary-value"],
        command_index=0,
        option_flags={flag: flag},
    )

    assert normalized[:2] == ["command", flag]
    assert normalized[3:] == ["ordinary-value"]
    assert protected == {normalized[2]: "-dash-value"}


def test_duplicate_repeated_terminal_paths_are_deduplicated(tmp_path):
    root = tmp_path / "repo"
    build(root)

    result = invoke(
        "impact",
        "--path",
        "src/api/app/service.py",
        "--path",
        "src/api/app/service.py",
        "--repo",
        str(root),
        "--format",
        "json",
    )

    assert result.exit_code == 0
    report = json.loads(result.output)
    assert report["input"]["paths"] == ["src/api/app/service.py"]
    assert by_id(report, "api")["reasons"] == [
        "src/api/app/service.py is within owned path src/api/app"
    ]
    assert report["summary"] == {"required": 1, "recommended": 1, "unaffected": 1}


def test_repeated_terminal_paths_union_with_revision_range(tmp_path):
    root = tmp_path / "repo"
    build(root)
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    commit_all(root, "base")
    (root / "src/worker/job.py").write_text("VALUE = 2\n", encoding="utf-8")
    commit_all(root, "change")

    result = invoke(
        "impact",
        "--path",
        "README.md",
        "--path",
        "src/api/app/service.py",
        "--revision-range",
        "HEAD~1..HEAD",
        "--repo",
        str(root),
        "--format",
        "json",
    )

    assert result.exit_code == 0
    report = json.loads(result.output)
    assert report["input"] == {
        "paths": ["README.md", "src/api/app/service.py", "src/worker/job.py"],
        "revision_range": "HEAD~1..HEAD",
    }
    assert report["summary"] == {"required": 3, "recommended": 0, "unaffected": 0}
