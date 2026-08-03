from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from milo.testing import MCPClient

from murlocs.cli import build_cli
from murlocs.errors import MurlocsError
from murlocs.outcome import (
    bind_integration_tokens,
    merge_outcomes,
    outcome_json_bytes,
    parse_outcome,
    parse_outcome_json,
)

MANIFEST = """schema_version = 1
network = "Outcome"
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

[[layers]]
id = "api"
kind = "domain"
path = ".murlocs/layers/api.toml"
owners = ["@api"]

[[scopes]]
id = "root"
path = "."
map = "AGENTS.md"
point_of_view = "Repository."
owns = ["README.md"]
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
edges = []

[[invariants]]
id = "api-contract"
scope = "api"
statement = "The API contract is reviewed."
severity = "critical"
verification = "manual"
evidence_file = "docs/api.md"
anchor = "API contract"
"""

FIXTURE_ROOT = Path(__file__).parent / "fixtures/outcome-envelope/v1"
TOKEN_SCOPE = {
    "adapter_id": "fixture-adapter",
    "adapter_version": "1",
    "session_id": "session-a",
}


def invoke(*argv: str):
    return build_cli().invoke(list(argv))


def build(root: Path) -> None:
    for directory in ("src/api/app", "src/api/scratch", "docs", ".murlocs/layers"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("# Repo\n", encoding="utf-8")
    (root / "src/api/app/service.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "src/api/scratch/note.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "docs/api.md").write_text("# API contract\n", encoding="utf-8")
    (root / ".murlocs/manifest.toml").write_text(MANIFEST, encoding="utf-8")
    (root / ".murlocs/layers/api.toml").write_text(API_LAYER, encoding="utf-8")
    (root / ".murlocs/PROTOCOL.md").write_text("# Review\n", encoding="utf-8")
    compiled = invoke("compile", "--repo", str(root))
    assert compiled.exit_code == 0, compiled.stderr


def structured(command: str, root: Path, **kwargs):
    return MCPClient(build_cli()).call(command, repo=str(root), **kwargs).structured


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_v1_conformance_goldens_are_canonical_and_malformed_fixtures_fail():
    golden = json.loads((FIXTURE_ROOT / "conformance.json").read_text(encoding="utf-8"))

    assert golden["contract"] == "io.murlocs.outcome"
    assert golden["schema_version"] == 1
    assert [case["id"] for case in golden["cases"]] == [
        "pass",
        "deterministic-repair",
        "agent-action",
        "authority-required",
    ]
    for case in golden["cases"]:
        parsed = parse_outcome(case["outcome"])
        assert parse_outcome_json(outcome_json_bytes(parsed)) == parsed
    malformed = sorted((FIXTURE_ROOT / "malformed").glob("*.json"))
    assert len(malformed) == 5
    for path in malformed:
        with pytest.raises(MurlocsError):
            parse_outcome_json(path.read_bytes())


def test_check_pass_and_safe_drift_are_versioned_read_only_outcomes(tmp_path: Path):
    root = tmp_path / "repo"
    build(root)
    before = snapshot(root)

    healthy = structured("check", root, correlation_id="task-42")["outcome"]

    assert healthy["contract"] == "io.murlocs.outcome"
    assert healthy["schema_version"] == 1
    assert healthy["code"] == "MURLOCS_OUTCOME_PASS"
    assert healthy["resolution_class"] == "pass"
    assert healthy["silent"] is True
    assert healthy["blocking"] is False
    assert healthy["next_actions"] == []
    assert healthy["correlation"] == {
        "correlation_id": "task-42",
        "state_id": None,
        "dependency_id": None,
        "token_source": "none",
        "token_scope": None,
    }
    assert snapshot(root) == before

    manifest = root / ".murlocs/manifest.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("Repository.", "Changed repository."),
        encoding="utf-8",
    )
    before_drift = snapshot(root)

    drift = structured("check", root, correlation_id="task-42")["outcome"]

    assert drift["code"] == "MURLOCS_OUTCOME_DETERMINISTIC_REPAIR"
    assert drift["resolution_class"] == "deterministic_repair"
    assert drift["blocking"] is True
    assert drift["source"]["exit_code"] == 1
    assert [item["operation"] for item in drift["next_actions"]] == [
        "compile_managed_guidance"
    ]
    assert "command" not in json.dumps(drift)
    assert snapshot(root) == before_drift


def test_modified_generated_output_never_claims_deterministic_repair(tmp_path: Path):
    root = tmp_path / "repo"
    build(root)
    (root / "AGENTS.md").write_text("user edit\n", encoding="utf-8")

    outcome = structured("check", root)["outcome"]

    assert outcome["resolution_class"] == "authority_required"
    assert all(
        action["operation"] != "compile_managed_guidance"
        for action in outcome["next_actions"]
    )
    assert outcome["next_actions"][0]["operation"] == "request_authority"
    assert "@platform" in outcome["next_actions"][0]["arguments"]["owners"]


def test_mixed_check_findings_bind_each_finding_to_its_own_resolution(tmp_path: Path):
    root = tmp_path / "repo"
    build(root)
    manifest = root / ".murlocs/manifest.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("Repository.", "Changed repository."),
        encoding="utf-8",
    )
    (root / "docs/api.md").unlink()

    outcome = structured("check", root)["outcome"]

    by_code = {finding["code"]: finding for finding in outcome["findings"]}
    assert by_code["MURLOCS_CHECK_DRIFT"]["resolution_class"] == "authority_required"
    assert by_code["MURLOCS_CHECK_DRIFT"]["action_ids"] == [
        "outcome.request-authority"
    ]
    assert by_code["MURLOCS_CHECK_PROOF"]["resolution_class"] == "agent_action"
    assert by_code["MURLOCS_CHECK_PROOF"]["action_ids"] == [
        "outcome.inspect-findings"
    ]
    assert {
        action["operation"] for action in outcome["next_actions"]
    } == {"inspect_findings", "request_authority"}


def test_impact_agent_and_authority_outcomes_name_exact_routing(tmp_path: Path):
    root = tmp_path / "repo"
    build(root)

    recommended = structured(
        "impact", root, path=["src/api/scratch/note.py"], correlation_id="run:7"
    )["outcome"]
    required = structured(
        "impact", root, path=["src/api/app/service.py"], correlation_id="run:7"
    )["outcome"]

    assert recommended["resolution_class"] == "agent_action"
    assert recommended["status"] == "advisory"
    assert recommended["blocking"] is False
    assert recommended["source"]["exit_code"] == 0
    assert recommended["next_actions"][0]["operation"] == "inspect_findings"
    assert required["resolution_class"] == "authority_required"
    assert required["status"] == "advisory"
    assert required["blocking"] is False
    assert required["source"]["exit_code"] == 0
    assert required["next_actions"][0]["operation"] == "request_authority"
    affected = required["findings"][0]["affected"]
    assert affected["scopes"] == ["api"]
    assert "src/api/AGENTS.md" in affected["maps"]
    assert affected["owners"] == ["@api"]


@pytest.mark.parametrize(
    ("command", "kwargs"),
    [
        ("check", {}),
        ("impact", {"path": ["src/api/app/service.py"]}),
    ],
)
def test_terminal_programmatic_mcp_and_discovery_have_outcome_parity(
    tmp_path: Path, command: str, kwargs: dict[str, object]
):
    root = tmp_path / "repo"
    build(root)
    argv = [command, "--repo", str(root), "--correlation-id", "parity-1", "--format", "json"]
    if command == "impact":
        argv.extend(["--path", "src/api/app/service.py"])

    terminal_result = invoke(*argv)
    terminal = json.loads(terminal_result.output)
    programmatic = build_cli().call(
        command, repo=str(root), correlation_id="parity-1", **kwargs
    )
    mcp = MCPClient(build_cli()).call(
        command, repo=str(root), correlation_id="parity-1", **kwargs
    ).structured
    tool = next(item for item in MCPClient(build_cli()).list_tools() if item.name == command)

    assert terminal == programmatic == mcp
    assert terminal["outcome"]["correlation"]["correlation_id"] == "parity-1"
    assert "outcome" in json.dumps(tool.output_schema)
    assert "correlation_id" in tool.input_schema["properties"]


def test_legacy_exit_contract_is_independent_of_outcome_blocking(tmp_path: Path):
    root = tmp_path / "repo"
    build(root)
    manifest = root / ".murlocs/manifest.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("Repository.", "Changed repository."),
        encoding="utf-8",
    )

    check = invoke("check", "--repo", str(root), "--format", "json")
    impact = invoke(
        "impact",
        "--repo",
        str(root),
        "--path",
        "src/api/app/service.py",
        "--format",
        "json",
    )

    assert check.exit_code == 1
    assert json.loads(check.output)["outcome"]["blocking"] is True
    assert impact.exit_code == 0
    assert json.loads(impact.output)["outcome"]["blocking"] is False


def test_integration_binding_only_echoes_opaque_tokens_and_merge_requires_identity(
    tmp_path: Path,
):
    root = tmp_path / "repo"
    build(root)
    check = structured("check", root, correlation_id="task-1")["outcome"]
    impact = structured(
        "impact", root, path=["src/api/scratch/note.py"], correlation_id="task-1"
    )["outcome"]
    bound_check = bind_integration_tokens(
        check,
        correlation_id="task-1",
        state_id="adapter-state-17",
        token_scope=TOKEN_SCOPE,
    )
    bound_impact = bind_integration_tokens(
        impact,
        correlation_id="task-1",
        state_id="adapter-state-17",
        token_scope=TOKEN_SCOPE,
        dependency_id="impact-dependency-3",
    )

    merged = merge_outcomes([bound_check, bound_impact])
    assert merged["correlation"] == bound_impact["correlation"]
    assert merged["correlation"]["token_scope"] == TOKEN_SCOPE
    assert merged["source"]["murlocs_version"] == bound_check["source"]["murlocs_version"]
    assert merged["resolution_class"] == "agent_action"
    assert merged["change"] == {"repository_state_changed": False, "paths": []}
    with pytest.raises(MurlocsError, match="only be bound once"):
        bind_integration_tokens(
            bound_check,
            correlation_id="task-1",
            state_id="adapter-state-17",
            token_scope=TOKEN_SCOPE,
        )
    with pytest.raises(MurlocsError, match="must equal"):
        bind_integration_tokens(
            check,
            correlation_id="different",
            state_id="adapter-state-17",
            token_scope=TOKEN_SCOPE,
        )
    with pytest.raises(MurlocsError, match="state id"):
        bind_integration_tokens(
            check,
            correlation_id="task-1",
            state_id=None,  # type: ignore[arg-type]
            token_scope=TOKEN_SCOPE,
        )
    with pytest.raises(MurlocsError, match="only valid for impact"):
        bind_integration_tokens(
            check,
            correlation_id="task-1",
            state_id="adapter-state-17",
            token_scope=TOKEN_SCOPE,
            dependency_id="not-check-local",
        )
    wrong_scope = copy.deepcopy(bound_impact)
    wrong_scope["correlation"]["token_scope"]["session_id"] = "session-b"
    with pytest.raises(MurlocsError, match="do not match"):
        merge_outcomes([bound_check, wrong_scope])
    wrong_version = copy.deepcopy(bound_impact)
    wrong_version["source"]["murlocs_version"] = "99.0"
    with pytest.raises(MurlocsError, match="versions do not match"):
        merge_outcomes([bound_check, wrong_version])


def test_merge_unions_same_action_arguments_and_rejects_conflicting_findings(
    tmp_path: Path,
):
    root = tmp_path / "repo"
    build(root)
    first = structured(
        "impact", root, path=["src/api/scratch/note.py"], correlation_id="merge-1"
    )["outcome"]
    second = copy.deepcopy(first)
    finding = second["findings"][0]
    finding["message"] = "Scope worker is recommended for guidance review."
    finding["evidence"][0]["reference"] = "worker"
    finding["affected"] = {
        "scopes": ["worker"],
        "maps": ["src/worker/AGENTS.md"],
        "owners": ["@worker"],
    }
    second["next_actions"][0]["arguments"].update(
        scopes=["worker"], maps=["src/worker/AGENTS.md"], owners=["@worker"]
    )

    merged = merge_outcomes([first, second])

    assert merged["next_actions"][0]["arguments"]["scopes"] == ["api", "worker"]
    assert merged["next_actions"][0]["arguments"]["owners"] == ["@api", "@worker"]
    conflicting = copy.deepcopy(first)
    conflicting["findings"][0]["message"] = "Conflicting interpretation."
    with pytest.raises(MurlocsError, match="conflicting outcome finding"):
        merge_outcomes([first, conflicting])


def test_parser_ignores_future_metadata_but_actions_are_closed(tmp_path: Path):
    root = tmp_path / "repo"
    build(root)
    outcome = structured("impact", root, path=["src/api/app/service.py"])["outcome"]
    future = copy.deepcopy(outcome)
    future["future"] = {"shell": "ignored as unrecognized metadata"}
    future["findings"][0]["future"] = {"value": 1}

    parsed = parse_outcome(future)

    assert "future" not in parsed
    assert "future" not in parsed["findings"][0]
    for mutation in (
        lambda value: value["next_actions"][0].update(operation="run_shell"),
        lambda value: value["next_actions"][0].update(shell="echo pwned"),
        lambda value: value["next_actions"][0]["arguments"].update(argv=["sh"]),
    ):
        malformed = copy.deepcopy(outcome)
        mutation(malformed)
        with pytest.raises(MurlocsError):
            parse_outcome(malformed)
    for field, value in (
        ("owners", ["@attacker"]),
        ("maps", ["outside/AGENTS.md"]),
    ):
        tampered = copy.deepcopy(outcome)
        tampered["next_actions"][0]["arguments"][field] = value
        with pytest.raises(MurlocsError, match="arguments do not match findings"):
            parse_outcome(tampered)


def test_strict_json_and_semantic_validation_reject_malformed_envelopes(tmp_path: Path):
    root = tmp_path / "repo"
    build(root)
    outcome = structured("check", root)["outcome"]
    raw = outcome_json_bytes(outcome).decode("utf-8")

    duplicate = raw.replace('"blocking":false', '"blocking":false,"blocking":false', 1)
    with pytest.raises(MurlocsError, match="duplicate JSON member"):
        parse_outcome_json(duplicate)
    with pytest.raises(MurlocsError, match="non-finite"):
        parse_outcome_json(raw.replace('"schema_version":1', '"future":NaN,"schema_version":1'))
    for version in (True, 0, 2):
        malformed = copy.deepcopy(outcome)
        malformed["schema_version"] = version
        with pytest.raises(MurlocsError, match="unsupported outcome schema_version"):
            parse_outcome(malformed)
    inconsistent = copy.deepcopy(outcome)
    inconsistent["blocking"] = True
    with pytest.raises(MurlocsError, match="blocking"):
        parse_outcome(inconsistent)
    inconsistent_exit = copy.deepcopy(outcome)
    inconsistent_exit["source"]["exit_code"] = 1
    with pytest.raises(MurlocsError, match="exit_code"):
        parse_outcome(inconsistent_exit)
    with pytest.raises(MurlocsError, match="exceeds"):
        parse_outcome_json(b" " * (1024 * 1024 + 1))
    malformed_correlation = copy.deepcopy(outcome)
    malformed_correlation["correlation"]["correlation_id"] = 7
    for malformed in (malformed_correlation, [outcome], 7, None):
        with pytest.raises(MurlocsError) as error:
            parse_outcome(malformed)
        assert isinstance(error.value, MurlocsError)
    with pytest.raises(MurlocsError):
        parse_outcome_json(7)  # type: ignore[arg-type]


def test_failure_sidecars_preserve_structured_exit_and_reject_bad_correlation(
    tmp_path: Path,
):
    missing = tmp_path / "missing"
    result = invoke("check", "--repo", str(missing), "--format", "json")
    invalid_correlation = invoke(
        "impact",
        "--repo",
        str(tmp_path),
        "--path",
        "README.md",
        "--correlation-id",
        "not valid",
        "--format",
        "json",
    )

    assert result.exit_code == 1
    assert json.loads(result.output)["outcome"]["source"]["exit_code"] == 1
    assert invalid_correlation.exit_code == 1
    payload = json.loads(invalid_correlation.output)
    assert payload["error"]["code"] == "MURLOCS_IMPACT"
    assert payload["outcome"]["correlation"]["correlation_id"] is None
