from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from murlocs.hooks import HookResult

BENCHMARK_PATH = Path(__file__).parents[1] / "benchmarks" / "passive_hot_path.py"
SPEC = importlib.util.spec_from_file_location("murlocs_passive_hot_path", BENCHMARK_PATH)
assert SPEC is not None and SPEC.loader is not None
BENCHMARK = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BENCHMARK
SPEC.loader.exec_module(BENCHMARK)


def test_passive_hot_path_budgets_cover_required_operations_and_shapes(tmp_path: Path) -> None:
    result = BENCHMARK.run_suite(tmp_path)

    assert set(result["shapes"]) == {
        "small",
        "layered",
        "multi-domain",
        "long-history",
        "scale-91",
    }
    assert result["shapes"]["scale-91"]["maps"] >= 80
    assert result["shapes"]["long-history"]["long_history"] == {
        "git_subprocesses": 3,
        "history_limit": 64,
        "conservative": True,
    }
    assert result["shapes"]["scale-91"]["measurements"]["task_start_discovery"][
        "files_read"
    ] > result["shapes"]["small"]["measurements"]["task_start_discovery"][
        "files_read"
    ]
    operation_names = set()
    for shape in result["shapes"].values():
        measurements = shape["measurements"]
        operation_names.update(measurements)
        if "healthy_pre_commit" in measurements:
            assert measurements["healthy_pre_commit"]["payload"]["silent"] is True
        if "drifted_checks" in measurements:
            assert measurements["drifted_checks"]["payload"]["repository"]["blocking"] is True
        if "staged_impact" in measurements:
            assert (
                measurements["staged_impact"]["payload"]["metrics"]["operation_subprocesses"]
                == 2
            )
        if "completion_gating" in measurements:
            completion = measurements["completion_gating"]
            assert completion["git_subprocesses"] > 0
            assert completion["files_read"] > 0
            assert len(completion["payload"]["results"]) == 1
        for measurement in measurements.values():
            assert measurement["cold_ms"] <= result["budget"]["cold_ms"]
            assert measurement["warm_ms"] <= result["budget"]["warm_ms"]
            assert measurement["git_subprocesses"] <= result["budget"]["git_subprocesses"]
            assert measurement["files_read"] <= result["budget"]["files_read"]
            assert (
                measurement["peak_memory_bound_bytes"]
                <= result["budget"]["peak_memory_bytes"]
            )
    assert operation_names == {
        "task_start_discovery",
        "explicit_impact",
        "staged_impact",
        "healthy_pre_commit",
        "drifted_checks",
        "completion_gating",
    }
    external_driver = subprocess.run(
        ["git", "config", "--get", "diff.external"],
        cwd=tmp_path / "small",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert external_driver.endswith("driver.sh")


@pytest.mark.parametrize(
    ("event", "payload"),
    [
        (
            "pre-commit",
            {"execution": {"status": "invalid"}, "operations": [], "metrics": {}},
        ),
        ("pre-push", {"contract": "io.murlocs.hook-batch", "results": []}),
    ],
)
def test_benchmark_rejects_incomplete_hook_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, event: str, payload: dict[str, object]
) -> None:
    monkeypatch.setattr(
        BENCHMARK,
        "run_hook",
        lambda *args, **kwargs: HookResult(payload, 1, "invalid"),
    )

    with pytest.raises(RuntimeError, match="did not complete|no activation results"):
        BENCHMARK._hook_operation(event, tmp_path)
