from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from murlocs.cli import explain_command, impact_command
from murlocs.manifest import load_manifest
from murlocs.render import compile_manifest
from murlocs.verify import validate

PILOT_PATH = Path(__file__).parents[1] / "benchmarks" / "scale_network.py"
SPEC = importlib.util.spec_from_file_location("murlocs_scale_network_benchmark", PILOT_PATH)
assert SPEC is not None and SPEC.loader is not None
PILOT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PILOT
SPEC.loader.exec_module(PILOT)

EXPECTED_MAPS = PILOT.EXPECTED_MAPS
TARGET_FILE = PILOT.TARGET_FILE
TARGET_SCOPE = PILOT.TARGET_SCOPE
build_fixture = PILOT.build_fixture
run_pilot = PILOT.run_pilot


def test_scale_fixture_refuses_nonempty_targets_before_writing(tmp_path: Path):
    root = tmp_path / "repository"
    sentinel = root / "README.md"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("user content\n", encoding="utf-8")

    with pytest.raises(ValueError, match="fixture repository target"):
        build_fixture(root)
    assert sentinel.read_text(encoding="utf-8") == "user content\n"

    empty_root = tmp_path / "empty-repository"
    artifacts = tmp_path / "evaluation"
    artifact_sentinel = artifacts / "existing.json"
    artifact_sentinel.parent.mkdir(parents=True)
    artifact_sentinel.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="evaluation artifact target"):
        run_pilot(empty_root, artifacts, samples=1)
    assert not empty_root.exists()
    assert artifact_sentinel.read_text(encoding="utf-8") == "{}\n"


def test_scale_fixture_is_owned_layered_and_byte_deterministic(tmp_path: Path):
    root = tmp_path / "repository"
    build_fixture(root)
    manifest = load_manifest(root)

    assert len(manifest.scopes) == EXPECTED_MAPS == 91
    assert len(manifest.sources) == 11  # root manifest, nine domains, one overlay
    assert manifest.require_layer_owners is True
    assert manifest.validate_codeowners is True
    assert len({owner for source in manifest.sources for owner in source.owners}) == 6
    assert not [finding for finding in validate(manifest) if finding.code not in {"lock", "drift"}]

    compile_manifest(manifest)
    first = {scope.map: (root / scope.map).read_bytes() for scope in manifest.scopes}
    compile_manifest(load_manifest(root))
    second = {scope.map: (root / scope.map).read_bytes() for scope in manifest.scopes}
    assert first == second
    assert validate(load_manifest(root)) == []


def test_scale_fixture_explain_and_review_fan_out_stay_focused(tmp_path: Path):
    root = tmp_path / "repository"
    build_fixture(root)
    compile_manifest(load_manifest(root))

    trace = explain_command(TARGET_FILE, repo=str(root))
    assert [scope["id"] for scope in trace["scopes"]] == [
        "root",
        "domain-08",
        "domain-08-platform",
        "domain-08-runtime",
        "domain-08-adapters",
        TARGET_SCOPE,
    ]
    assert [layer["id"] for layer in trace["scopes"][-1]["layers"]] == [
        "domain-08",
        "architecture-overlay",
    ]
    assert len(trace["overrides"]) == 1

    root_trace = explain_command("README.md", repo=str(root))
    nested_trace = explain_command("src/domain-03/service-b/unit.py", repo=str(root))
    other_domain_trace = explain_command(
        "src/domain-02/platform/runtime/adapters/queue/unit.py", repo=str(root)
    )
    assert [scope["id"] for scope in root_trace["scopes"]] == ["root"]
    assert [scope["id"] for scope in nested_trace["scopes"]] == [
        "root",
        "domain-03",
        "domain-03-service-b",
    ]
    assert [scope["id"] for scope in other_domain_trace["scopes"]][-1] == "domain-02-queue"

    focused = impact_command(path=[TARGET_FILE], repo=str(root))
    global_change = impact_command(path=[".murlocs/manifest.toml"], repo=str(root))
    assert focused["summary"]["required"] < EXPECTED_MAPS
    assert focused["summary"]["recommended"] == 1
    assert global_change["summary"]["required"] == EXPECTED_MAPS


def test_scale_pilot_reports_invariants_without_speed_thresholds(tmp_path: Path):
    result = run_pilot(
        tmp_path / "repository",
        tmp_path / "evaluation",
        samples=1,
    )

    assert result["fixture"]["map_count"] == EXPECTED_MAPS
    assert result["fixture"]["byte_deterministic_recompile"] is True
    assert (
        result["fixture"]["maximum_active_chain_bytes"] < result["fixture"]["total_generated_bytes"]
    )
    assert set(result["measurements"]) == {"compile", "check", "explain", "impact"}
    assert all(
        measurement["median_ms"] >= 0 and measurement["peak_memory_bytes"] > 0
        for measurement in result["measurements"].values()
    )
    assert all(probe["detected"] for probe in result["failure_probes"].values())
    assert result["evaluation"]["all_arms_correct"] is True
    assert result["evaluation"]["most_efficient_correct_arm"] == "murlocs"

    recorded = json.loads(
        (Path(__file__).parents[1] / "docs/pilots/scale-network-results.json").read_text(
            encoding="utf-8"
        )
    )
    assert recorded.keys() == result.keys()
    assert recorded["fixture"].keys() == result["fixture"].keys()
    assert recorded["governance"].keys() == result["governance"].keys()
    assert recorded["failure_probes"].keys() == result["failure_probes"].keys()
    assert recorded["evaluation"].keys() == result["evaluation"].keys()
