from __future__ import annotations

import json
from pathlib import Path

from murlocs.cli import build_cli
from murlocs.eval import (
    METRIC_DEFINITIONS,
    RunEvidence,
    RunRecord,
    check_correctness,
    compare_runs,
    load_task,
    save_results,
    score_run,
)
from murlocs.eval.guidance import inline_dump_guidance, murlocs_guidance

TASK_FIXTURE = (
    Path(__file__).parents[1]
    / "src"
    / "murlocs"
    / "eval"
    / "fixtures"
    / "tasks"
    / "import-graph.toml"
)


def invoke(*argv: str):
    return build_cli().invoke(list(argv))


def make_record(arm: str, answer: str, *, steps: int, guidance: str = "") -> RunRecord:
    return RunRecord(
        arm=arm,
        model="test-model",
        ade="test-ade",
        guidance_revision="rev-1",
        answer=answer,
        guidance_text=guidance,
        evidence=RunEvidence(
            files_inspected=1,
            lines_inspected=steps * 2,
            tool_calls=steps,
            executable_steps=steps,
            transcript=(f"step for {arm}",),
        ),
    )


def test_fixture_task_is_objectively_checkable():
    task = load_task(TASK_FIXTURE)
    assert task.id == "import-graph"
    assert task.target_path == "src/app/service.py"
    assert {fact.id for fact in task.expected_facts} == {
        "depends-on-render",
        "depends-on-store",
    }


def test_correctness_requires_all_expected_facts():
    task = load_task(TASK_FIXTURE)
    right = check_correctness(
        task, make_record("murlocs", "uses app.render and app.store", steps=5)
    )
    wrong = check_correctness(task, make_record("murlocs", "uses app.render only", steps=5))
    assert right.passed is True
    assert wrong.passed is False
    assert "depends-on-store" in wrong.missing


def test_efficiency_is_withheld_until_correctness_passes():
    task = load_task(TASK_FIXTURE)
    incorrect = score_run(task, make_record("no-guidance", "not sure", steps=40))
    correct = score_run(task, make_record("murlocs", "app.render and app.store", steps=5))
    assert incorrect.efficiency is None
    assert correct.efficiency is not None
    assert correct.efficiency.executable_steps == 5


def test_comparison_picks_most_efficient_correct_arm():
    task = load_task(TASK_FIXTURE)
    records = [
        make_record("no-guidance", "app.render and app.store", steps=42),
        make_record("inline-dump", "app.render and app.store", steps=30, guidance="x" * 4000),
        make_record("murlocs", "app.render and app.store", steps=12, guidance="scoped"),
    ]
    summary = compare_runs(task, records)
    assert summary.most_efficient_arm == "murlocs"
    assert summary.repository_revision == "fixture-1"
    # Metadata for auditing is present.
    assert all(score.model == "test-model" and score.ade == "test-ade" for score in summary.scores)
    assert "estimated_prompt_tokens" in summary.metric_definitions
    assert "caveats" in METRIC_DEFINITIONS


def test_incorrect_arm_never_wins_even_if_cheaper():
    task = load_task(TASK_FIXTURE)
    records = [
        make_record("no-guidance", "app.render only", steps=1),  # cheapest but wrong
        make_record("murlocs", "app.render and app.store", steps=12),
    ]
    summary = compare_runs(task, records)
    assert summary.most_efficient_arm == "murlocs"


def test_save_results_preserves_evidence_and_summary(tmp_path):
    task = load_task(TASK_FIXTURE)
    records = [make_record("murlocs", "app.render and app.store", steps=12)]
    summary = compare_runs(task, records)
    target = save_results(tmp_path / "results", task, summary, records)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["task"]["id"] == "import-graph"
    assert payload["summary"]["most_efficient_arm"] == "murlocs"
    assert payload["evidence"]["murlocs"] == ["step for murlocs"]


def test_scoped_guidance_is_smaller_than_inline_dump(tmp_path):
    root = tmp_path / "repo"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "guide.md").write_text("guide\n", encoding="utf-8")
    assert invoke("init", "--repo", str(root), "--name", "Eval").exit_code == 0
    assert invoke("add-scope", "src/pkg", "--repo", str(root), "--id", "pkg").exit_code == 0
    assert invoke("add-scope", "docs", "--repo", str(root)).exit_code == 0

    scoped = murlocs_guidance(root, "src/pkg/core.py")
    dump = inline_dump_guidance(root)
    assert len(scoped.encode("utf-8")) < len(dump.encode("utf-8"))
    # The scoped chain excludes the unrelated docs scope's own body.
    assert "Guidance for docs." not in scoped
    assert "Guidance for docs." in dump


def test_deterministic_core_does_not_import_the_harness():
    core = Path(__file__).parents[1] / "src" / "murlocs"
    offenders = []
    for path in core.glob("*.py"):
        if "murlocs.eval" in path.read_text(encoding="utf-8"):
            offenders.append(path.name)
    assert offenders == [], f"core modules must not import the eval harness: {offenders}"
