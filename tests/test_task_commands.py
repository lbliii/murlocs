from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from milo.testing import MCPClient

from murlocs.cli import build_cli
from murlocs.task_commands import STALE_RECEIPT_CODE, TASK_CONTRACT, TASK_SCHEMA_VERSION

MANIFEST = """schema_version = 1
network = "Tasks"
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
guardrails = ["Keep the repository root map compact."]
edges = []
"""

API_LAYER = """[[scopes]]
id = "api"
path = "src/api"
map = "src/api/AGENTS.md"
point_of_view = "API."
owns = ["src/api/app"]
guardrails = ["Version the API contract."]
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
    result = invoke("compile", "--repo", str(root))
    assert result.exit_code == 0, result.stderr


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def call(name: str, root: Path, **kwargs):
    return build_cli().call(name, repo=str(root), **kwargs)


def mcp(name: str, root: Path, **kwargs):
    return MCPClient(build_cli()).call(name, repo=str(root), **kwargs).structured


def git_init(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)


def commit_all(root: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
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


def receipt_codes(envelope: dict) -> set[str]:
    codes: set[str] = {STALE_RECEIPT_CODE}
    for name in ("check", "impact"):
        outcome = envelope["receipts"][name]
        if outcome is not None:
            for finding in outcome["findings"]:
                codes.add(finding["code"])
    curation = envelope["receipts"]["curation"]
    if curation is not None:
        for finding in curation["findings"]:
            codes.add(f"MURLOCS_CURATION_{str(finding['code']).upper()}")
    return codes


# ---------------------------------------------------------------------------
# Envelope contract
# ---------------------------------------------------------------------------


def test_envelope_shared_contract_fields(tmp_path):
    root = tmp_path / "repo"
    build(root)

    orient = call("orient", root, path="src/api/app/service.py")
    review = call("review-changes", root, path=["README.md"])
    finish = call("finish", root, path=["README.md"])

    for envelope, command in (
        (orient, "orient"),
        (review, "review-changes"),
        (finish, "finish"),
    ):
        assert envelope["contract"] == TASK_CONTRACT
        assert envelope["schema_version"] == TASK_SCHEMA_VERSION
        assert envelope["command"] == command
        assert set(envelope["classification"]) == {
            "blocking",
            "authority_required",
            "agent_action",
            "recommended",
        }
        assert set(envelope["correlation"]) == {
            "correlation_id",
            "state_id",
            "dependency_id",
            "token_source",
            "token_scope",
        }
        assert envelope["repository"]["manifest_present"] is True
        assert envelope["status"] in {"pass", "advisory", "blocking"}
        assert envelope["silent"] == (envelope["status"] == "pass")
        for action in envelope["actions"]:
            assert action["classification"] in envelope["classification"]


def test_classification_counts_match_actions(tmp_path):
    root = tmp_path / "repo"
    build(root)
    envelope = call("finish", root, path=["src/api/app/service.py"])
    counts = envelope["classification"]
    for key in counts:
        assert counts[key] == sum(
            1 for action in envelope["actions"] if action["classification"] == key
        )


def test_actions_are_traceable_to_granular_receipts(tmp_path):
    root = tmp_path / "repo"
    build(root)
    for envelope in (
        call("orient", root, path="src/api/app/service.py"),
        call("review-changes", root, path=["docs/api.md", "pyproject.toml"]),
        call("finish", root, path=["src/api/app/service.py"]),
    ):
        available = receipt_codes(envelope)
        for action in envelope["actions"]:
            assert set(action["codes"]).issubset(available), action


# ---------------------------------------------------------------------------
# orient
# ---------------------------------------------------------------------------


def test_orient_reports_orientation_for_path(tmp_path):
    root = tmp_path / "repo"
    build(root)
    envelope = call("orient", root, path="src/api/app/service.py")

    orientation = envelope["orientation"]
    assert orientation["path"] == "src/api/app/service.py"
    scope_ids = [scope["id"] for scope in orientation["scopes"]]
    assert scope_ids == ["root", "api"]
    assert "src/api/AGENTS.md" in orientation["maps"]
    assert "@api" in orientation["owners"]
    assert any(check["name"] == "api-test" for check in orientation["focused_checks"])
    assert any(
        "Version the API contract." in scope["guardrails"] for scope in orientation["scopes"]
    )
    assert any(related["scope"] == "worker" for related in orientation["related_scopes"])
    assert orientation["budget"]["max_active_bytes"] == 24576
    assert envelope["git_view"]["kind"] == "path"


def test_orient_path_outside_repository_fails_safely(tmp_path):
    root = tmp_path / "repo"
    build(root)
    result = invoke("orient", "../escape.py", "--repo", str(root))
    assert result.exit_code == 1
    assert "outside repository" in result.stderr


def test_orient_ambiguous_adoption_fails_visibly(tmp_path):
    root = tmp_path / "repo"
    (root / ".murlocs").mkdir(parents=True)
    (root / ".murlocs" / "lock.json").write_text("{}\n", encoding="utf-8")
    result = invoke("orient", "src", "--repo", str(root))
    assert result.exit_code == 1
    assert "ambiguous" in result.stderr


# ---------------------------------------------------------------------------
# review-changes
# ---------------------------------------------------------------------------


def test_review_changes_requires_explicit_view(tmp_path):
    root = tmp_path / "repo"
    build(root)
    result = invoke("review-changes", "--repo", str(root))
    assert result.exit_code == 1
    assert "select exactly one change view" in result.stderr


def test_review_changes_ambiguous_view_fails(tmp_path):
    root = tmp_path / "repo"
    build(root)
    result = invoke("review-changes", "--path", "README.md", "--staged", "--repo", str(root))
    assert result.exit_code == 1
    assert "ambiguous change view" in result.stderr


def test_review_changes_echoes_repo_and_view(tmp_path):
    root = tmp_path / "repo"
    build(root)
    result = invoke("review-changes", "--path", "src/api/app/service.py", "--repo", str(root))
    assert result.exit_code == 0
    assert str(root) in result.output
    assert "view: paths" in result.output


def test_review_changes_routes_required_and_recommended(tmp_path):
    root = tmp_path / "repo"
    build(root)
    envelope = call("review-changes", root, path=["src/api/app/service.py"])
    review = envelope["review"]
    by_id = {scope["id"]: scope for scope in review["scopes"]}
    assert by_id["api"]["status"] == "required"
    assert by_id["worker"]["status"] == "recommended"
    assert any(action["classification"] == "authority_required" for action in envelope["actions"])
    assert any(action["classification"] == "recommended" for action in envelope["actions"])


def test_review_changes_never_claims_semantic_truth(tmp_path):
    root = tmp_path / "repo"
    build(root)
    envelope = call("review-changes", root, path=["src/api/app/service.py"])
    # Routing is exposed only as review impact, never a merge/semantic decision.
    assert "decision" not in envelope
    assert envelope["review"]["policy"]["version"] >= 1


@pytest.mark.parametrize(
    "paths",
    [
        ["src/api/app/service.py", "src/api/app/service.py"],  # repeated
        ["docs/api.md", "README.md"],  # unsorted -> deterministic order
    ],
)
def test_review_changes_paths_normalized_and_deterministic(tmp_path, paths):
    root = tmp_path / "repo"
    build(root)
    first = call("review-changes", root, path=paths)
    second = call("review-changes", root, path=list(reversed(paths)))
    assert first["git_view"]["paths"] == second["git_view"]["paths"]
    assert first["git_view"]["paths"] == sorted(set(paths))


def test_review_changes_handles_space_and_unicode_paths(tmp_path):
    root = tmp_path / "repo"
    build(root)
    (root / "src/api/app").mkdir(parents=True, exist_ok=True)
    weird = ["src/api/app/a b.py", "src/api/app/café.py"]
    for relative in weird:
        (root / relative).write_text("VALUE = 1\n", encoding="utf-8")
    envelope = call("review-changes", root, path=weird)
    assert envelope["git_view"]["paths"] == sorted(weird)


def test_review_changes_dash_leading_path_matches_surfaces(tmp_path):
    root = tmp_path / "repo"
    build(root)
    (root / "-dash.py").write_text("VALUE = 1\n", encoding="utf-8")
    result = invoke(
        "review-changes",
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
    programmatic = call("review-changes", root, path=paths)
    surface = mcp("review-changes", root, path=paths)
    assert terminal["git_view"]["paths"] == paths
    assert terminal == programmatic == surface


# ---------------------------------------------------------------------------
# review-changes over Git views (staged / working-tree / revision / deletions)
# ---------------------------------------------------------------------------


def test_review_changes_staged_view_is_freshness_bound(tmp_path):
    root = tmp_path / "repo"
    build(root)
    git_init(root)
    commit_all(root, "base")
    (root / "src/worker/job.py").write_text("VALUE = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/worker/job.py"], cwd=root, check=True, capture_output=True)

    envelope = call("review-changes", root, staged=True)
    assert envelope["git_view"]["kind"] == "staged"
    assert envelope["git_view"]["paths"] == ["src/worker/job.py"]
    assert envelope["freshness"]["view_state_id"] is not None
    assert any(
        dependency.startswith("git-view:staged:")
        for dependency in envelope["freshness"]["dependencies"]
    )


def test_review_changes_deletion_and_rename_over_revision(tmp_path):
    root = tmp_path / "repo"
    build(root)
    git_init(root)
    commit_all(root, "base")
    (root / "src/worker/job.py").unlink()
    (root / "src/worker/renamed.py").write_text("VALUE = 1\n", encoding="utf-8")
    commit_all(root, "delete and rename")

    envelope = call("review-changes", root, revision_range="HEAD~1..HEAD")
    assert envelope["git_view"]["kind"] == "revision"
    assert "src/worker/job.py" in envelope["git_view"]["paths"]
    assert "src/worker/renamed.py" in envelope["git_view"]["paths"]


def test_review_changes_unavailable_git_fails(tmp_path):
    root = tmp_path / "repo"
    build(root)
    result = invoke("review-changes", "--staged", "--repo", str(root))
    assert result.exit_code == 1
    assert result.stderr


# ---------------------------------------------------------------------------
# finish
# ---------------------------------------------------------------------------


def test_finish_is_read_only_and_never_executes_checks(tmp_path):
    root = tmp_path / "repo"
    build(root)
    before = snapshot(root)

    call("orient", root, path="src/api/app/service.py")
    call("review-changes", root, path=["src/api/app/service.py"])
    call("finish", root, path=["src/api/app/service.py"])

    assert snapshot(root) == before
    assert not (root / "MUST_NOT_EXIST").exists()


def test_finish_names_registered_checks_without_running_them(tmp_path):
    root = tmp_path / "repo"
    build(root)
    envelope = call("finish", root, path=["src/api/app/service.py"])
    completion = envelope["completion"]
    assert completion["executed_checks"] is False
    assert completion["curation_validation_ran"] is True
    assert any(check["name"] == "api-test" for check in completion["registered_checks"])
    assert not (root / "MUST_NOT_EXIST").exists()


def test_finish_healthy_is_silent_capable(tmp_path):
    root = tmp_path / "repo"
    build(root)
    envelope = call("finish", root, path=["CHANGELOG.md"])
    assert envelope["status"] == "pass"
    assert envelope["silent"] is True
    assert envelope["blocking"] is False
    assert envelope["receipts"]["check"]["status"] == "pass"


def test_finish_stale_receipt_cannot_complete(tmp_path):
    root = tmp_path / "repo"
    build(root)
    git_init(root)
    commit_all(root, "base")
    (root / "src/worker/job.py").write_text("VALUE = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/worker/job.py"], cwd=root, check=True, capture_output=True)

    fresh = call("finish", root, staged=True)
    stale = call(
        "finish",
        root,
        staged=True,
        receipt_state_id="sha256:" + "0" * 64,
    )
    assert fresh["freshness"]["stale"] is False
    assert stale["freshness"]["stale"] is True
    assert stale["blocking"] is True
    assert stale["status"] == "blocking"
    assert any(action["codes"] == [STALE_RECEIPT_CODE] for action in stale["actions"])


def test_finish_working_tree_stale_receipt_detects_unstaged_edit(tmp_path):
    root = tmp_path / "repo"
    build(root)
    git_init(root)
    commit_all(root, "base")

    fresh = call("finish", root, working_tree=True)
    baseline_state = fresh["freshness"]["view_state_id"]
    assert baseline_state is not None

    # An UNSTAGED edit to a tracked file must invalidate the pre-edit receipt,
    # even though the Git index is unchanged.
    (root / "src/worker/job.py").write_text("VALUE = 99\n", encoding="utf-8")
    stale = call("finish", root, working_tree=True, receipt_state_id=baseline_state)

    assert stale["freshness"]["view_state_id"] != baseline_state
    assert stale["freshness"]["stale"] is True
    assert stale["blocking"] is True
    assert stale["status"] == "blocking"
    result = invoke(
        "finish",
        "--working-tree",
        "--receipt-state-id",
        baseline_state,
        "--repo",
        str(root),
    )
    assert result.exit_code == 1


def test_working_tree_view_surfaces_untracked_files(tmp_path):
    root = tmp_path / "repo"
    build(root)
    git_init(root)
    commit_all(root, "base")
    (root / "src/worker/new_source.py").write_text("VALUE = 1\n", encoding="utf-8")

    envelope = call("review-changes", root, working_tree=True)
    assert "src/worker/new_source.py" in envelope["git_view"]["paths"]
    # A subsequent edit to the untracked file also changes the freshness anchor.
    first_state = call("finish", root, working_tree=True)["freshness"]["view_state_id"]
    (root / "src/worker/new_source.py").write_text("VALUE = 2\n", encoding="utf-8")
    second_state = call("finish", root, working_tree=True)["freshness"]["view_state_id"]
    assert first_state != second_state


def test_finish_receipt_state_id_requires_git_view(tmp_path):
    root = tmp_path / "repo"
    build(root)
    result = invoke(
        "finish",
        "--path",
        "README.md",
        "--receipt-state-id",
        "sha256:abc",
        "--repo",
        str(root),
    )
    assert result.exit_code == 1
    assert "receipt-state-id" in result.stderr


def test_finish_interrupted_curation_transaction_blocks(tmp_path):
    root = tmp_path / "repo"
    build(root)
    (root / ".murlocs/curation/.transaction").mkdir(parents=True)
    envelope = call("finish", root, path=["README.md"])
    assert envelope["blocking"] is True
    check_codes = {finding["code"] for finding in envelope["receipts"]["check"]["findings"]}
    assert "MURLOCS_CHECK_CURATION_TRANSACTION" in check_codes


# ---------------------------------------------------------------------------
# Surface parity and granular-command stability
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "argv", "kwargs"),
    [
        ("orient", ["orient", "src/api/app/service.py"], {"path": "src/api/app/service.py"}),
        (
            "review-changes",
            ["review-changes", "--path", "src/api/app/service.py"],
            {"path": ["src/api/app/service.py"]},
        ),
        (
            "finish",
            ["finish", "--path", "src/api/app/service.py"],
            {"path": ["src/api/app/service.py"]},
        ),
    ],
)
def test_cli_programmatic_and_mcp_surfaces_are_equivalent(tmp_path, name, argv, kwargs):
    root = tmp_path / "repo"
    build(root)
    result = invoke(*argv, "--repo", str(root), "--format", "json")
    assert result.exit_code == 0, result.stderr
    terminal = json.loads(result.output)
    programmatic = call(name, root, **kwargs)
    surface = mcp(name, root, **kwargs)
    assert terminal == programmatic == surface


def test_receipts_equal_granular_command_outcomes(tmp_path):
    root = tmp_path / "repo"
    build(root)
    paths = ["src/api/app/service.py"]

    finish = call("finish", root, path=paths)
    check = call("check", root)
    impact = call("impact", root, path=paths)

    assert finish["receipts"]["check"] == check["outcome"]
    assert finish["receipts"]["impact"] == impact["outcome"]


def test_correlation_id_flows_into_receipts(tmp_path):
    root = tmp_path / "repo"
    build(root)
    envelope = call("finish", root, path=["README.md"], correlation_id="task-42")
    assert envelope["correlation"]["correlation_id"] == "task-42"
    assert envelope["receipts"]["check"]["correlation"]["correlation_id"] == "task-42"
    assert envelope["receipts"]["impact"]["correlation"]["correlation_id"] == "task-42"


def test_existing_granular_commands_still_pass(tmp_path):
    root = tmp_path / "repo"
    build(root)
    assert invoke("check", "--repo", str(root)).exit_code == 0
    assert invoke("explain", "src/api/app/service.py", "--repo", str(root)).exit_code == 0
    assert invoke("impact", "--path", "src/api/app/service.py", "--repo", str(root)).exit_code == 0
