from __future__ import annotations

import json
import subprocess
from pathlib import Path

from milo.testing import MCPClient

from murlocs.cli import _normalize_impact_path_options, build_cli

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
    assert report["policy"]["version"] == 1


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
    assert worker["reasons"] == [
        "edge api -[verified-by]-> worker: Worker tests API contracts."
    ]


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


def test_impact_path_normalizer_does_not_change_unrelated_commands():
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

    assert _normalize_impact_path_options(
        argv,
        frozenset({"init", "impact"}),
        frozenset({"--output-file"}),
    ) == argv


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
