from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

import murlocs.eval._atomic as eval_atomic
from murlocs.cli import build_cli
from murlocs.eval import (
    METRIC_DEFINITIONS,
    RunEvidence,
    RunRecord,
    check_correctness,
    compare_runs,
    load_runs,
    load_task,
    save_results,
    score_run,
)
from murlocs.eval.__main__ import main as eval_main
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
RUNS_FIXTURE = TASK_FIXTURE.parents[1] / "runs" / "import-graph.json"


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


def test_versioned_task_requires_objective_correctness_facts(tmp_path):
    text = (
        TASK_FIXTURE.read_text(encoding="utf-8").split("[[expected_facts]]", 1)[0]
        + "expected_facts = []\n"
    )
    candidate = tmp_path / "empty-facts.toml"
    candidate.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match="expected_facts must contain at least one"):
        load_task(candidate)


def test_versioned_task_rejects_zero_correctness_threshold(tmp_path):
    candidate = tmp_path / "zero-threshold.toml"
    candidate.write_text(
        TASK_FIXTURE.read_text(encoding="utf-8").replace(
            "correctness_threshold = 1.0", "correctness_threshold = 0.0"
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must be greater than 0 and at most 1"):
        load_task(candidate)


@pytest.mark.parametrize(
    "task_id",
    ["../escape", "nested/task", r"nested\task", "..", ".hidden", "trailing.", "a..b"],
)
def test_task_ingestion_rejects_nonportable_and_traversal_ids(tmp_path, task_id):
    toml_id = task_id.replace("\\", "\\\\")
    text = TASK_FIXTURE.read_text(encoding="utf-8").replace(
        'id = "import-graph"', f'id = "{toml_id}"', 1
    )
    candidate = tmp_path / "task.toml"
    candidate.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match="must be 1-128 ASCII"):
        load_task(candidate)


def test_save_results_revalidates_task_id_before_creating_output(tmp_path):
    task = replace(load_task(TASK_FIXTURE), id="../escaped")
    records = [make_record("murlocs", "app.render and app.store", steps=12)]
    summary = compare_runs(task, records)
    output = tmp_path / "results"

    with pytest.raises(ValueError, match="task id must be 1-128 ASCII"):
        save_results(output, task, summary, records)

    assert not output.exists()
    assert not (tmp_path / "escaped.json").exists()


def test_versioned_recorded_runs_load_all_experiment_arms():
    task = load_task(TASK_FIXTURE)
    records = load_runs(RUNS_FIXTURE, task)
    assert [record.arm for record in records] == [
        "no-guidance",
        "inline-dump",
        "murlocs",
    ]
    assert records[-1].guidance_revision == "lock-abc123"
    assert records[-1].evidence.transcript[-1] == "read src/app/service.py"


def test_run_ingestion_rejects_task_and_revision_mismatches(tmp_path):
    task = load_task(TASK_FIXTURE)
    payload = json.loads(RUNS_FIXTURE.read_text(encoding="utf-8"))
    payload["task_id"] = "another-task"
    mismatched_task = tmp_path / "task-mismatch.json"
    mismatched_task.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="task_id.*does not match"):
        load_runs(mismatched_task, task)

    payload["task_id"] = task.id
    payload["repository_revision"] = "another-revision"
    mismatched_revision = tmp_path / "revision-mismatch.json"
    mismatched_revision.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="repository_revision.*does not match"):
        load_runs(mismatched_revision, task)


def test_run_ingestion_rejects_unknown_missing_and_duplicate_arms(tmp_path):
    task = load_task(TASK_FIXTURE)
    payload = json.loads(RUNS_FIXTURE.read_text(encoding="utf-8"))

    payload["runs"][0]["arm"] = "mystery"
    unknown = tmp_path / "unknown.json"
    unknown.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="arm must be one of"):
        load_runs(unknown, task)

    payload = json.loads(RUNS_FIXTURE.read_text(encoding="utf-8"))
    payload["runs"] = payload["runs"][:-1]
    missing = tmp_path / "missing.json"
    missing.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="missing recorded runs for arms: murlocs"):
        load_runs(missing, task)

    payload = json.loads(RUNS_FIXTURE.read_text(encoding="utf-8"))
    payload["runs"][1]["arm"] = "no-guidance"
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate recorded run"):
        load_runs(duplicate, task)


def test_ingestion_requires_supported_schema_versions(tmp_path):
    task = load_task(TASK_FIXTURE)
    payload = json.loads(RUNS_FIXTURE.read_text(encoding="utf-8"))
    payload["schema_version"] = 2
    unsupported = tmp_path / "unsupported.json"
    unsupported.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported schema_version 2"):
        load_runs(unsupported, task)


def test_versioned_inputs_reject_unknown_fields(tmp_path):
    task_text = TASK_FIXTURE.read_text(encoding="utf-8").replace(
        "\n[[expected_facts]]", '\nfuture_field = "value"\n\n[[expected_facts]]', 1
    )
    task_path = tmp_path / "unknown-task.toml"
    task_path.write_text(task_text, encoding="utf-8")
    with pytest.raises(ValueError, match="unknown fields: future_field"):
        load_task(task_path)

    task = load_task(TASK_FIXTURE)
    payload = json.loads(RUNS_FIXTURE.read_text(encoding="utf-8"))
    payload["runs"][0]["future_field"] = "value"
    runs_path = tmp_path / "unknown-runs.json"
    runs_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown fields: future_field"):
        load_runs(runs_path, task)


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
    assert payload["schema_version"] == 1
    assert payload["task"]["id"] == "import-graph"
    assert payload["summary"]["most_efficient_arm"] == "murlocs"
    assert payload["records"][0]["evidence"]["transcript"] == ["step for murlocs"]
    assert payload["records"][0]["answer"] == "app.render and app.store"


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_save_results_replaces_links_without_mutating_external_target(tmp_path, link_kind):
    task = load_task(TASK_FIXTURE)
    records = [make_record("murlocs", "app.render and app.store", steps=12)]
    summary = compare_runs(task, records)
    output = tmp_path / "results"
    output.mkdir()
    victim = tmp_path / "external.json"
    victim.write_text("external content\n", encoding="utf-8")
    target = output / "import-graph.json"
    if link_kind == "symlink":
        target.symlink_to(victim)
    else:
        os.link(victim, target)

    assert save_results(output, task, summary, records) == target

    assert victim.read_text(encoding="utf-8") == "external content\n"
    assert not target.is_symlink()
    assert target.is_file()
    assert target.stat().st_ino != victim.stat().st_ino
    assert json.loads(target.read_text(encoding="utf-8"))["task"]["id"] == "import-graph"


def test_save_results_cleans_temporary_file_when_replace_fails(tmp_path, monkeypatch):
    task = load_task(TASK_FIXTURE)
    records = [make_record("murlocs", "app.render and app.store", steps=12)]
    summary = compare_runs(task, records)
    output = tmp_path / "results"

    def fail_replace(_source, _target):
        raise OSError("forced replace failure")

    monkeypatch.setattr(eval_atomic.os, "replace", fail_replace)
    with pytest.raises(OSError, match="forced replace failure"):
        save_results(output, task, summary, records)

    assert output.is_dir()
    assert list(output.iterdir()) == []


def test_eval_cli_ingests_files_and_writes_reproducible_results(tmp_path, capsys):
    first = tmp_path / "first"
    second = tmp_path / "second"
    reordered_runs = tmp_path / "reordered-runs.json"
    reordered_payload = json.loads(RUNS_FIXTURE.read_text(encoding="utf-8"))
    reordered_payload["runs"].reverse()
    reordered_runs.write_text(json.dumps(reordered_payload), encoding="utf-8")
    assert (
        eval_main(
            ["--task", str(TASK_FIXTURE), "--runs", str(RUNS_FIXTURE), "--output", str(first)]
        )
        == 0
    )
    assert "most efficient correct arm: murlocs" in capsys.readouterr().out
    assert (
        eval_main(
            ["--task", str(TASK_FIXTURE), "--runs", str(reordered_runs), "--output", str(second)]
        )
        == 0
    )
    capsys.readouterr()
    assert (first / "import-graph.json").read_bytes() == (second / "import-graph.json").read_bytes()


def test_eval_cli_requires_explicit_demo_or_inputs(capsys):
    with pytest.raises(SystemExit) as missing:
        eval_main([])
    assert missing.value.code == 2
    assert (
        "one of the arguments --demo --task --longitudinal is required" in capsys.readouterr().err
    )

    assert eval_main(["--demo"]) == 0
    assert "illustrative-model / illustrative-ade" in capsys.readouterr().out


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


def test_demo_output_is_labeled_illustrative_and_withholds_verdict(capsys):
    assert eval_main(["--demo"]) == 0
    out = capsys.readouterr().out
    # The banner marks the demo as a non-measured format example.
    assert "FORMAT EXAMPLE" in out
    assert "illustrative synthetic data, NOT a measured result" in out
    # The bare efficiency verdict is suppressed so synthetic figures crown no winner.
    assert "most efficient correct arm:" not in out
    assert "no most-efficient arm" in out


def test_non_demo_paths_keep_the_efficiency_verdict_without_the_banner(capsys):
    assert eval_main(["--task", str(TASK_FIXTURE), "--runs", str(RUNS_FIXTURE)]) == 0
    out = capsys.readouterr().out
    assert "most efficient correct arm: murlocs" in out
    assert "FORMAT EXAMPLE" not in out
    assert "illustrative synthetic data" not in out


def test_demo_output_artifact_is_flagged_illustrative(tmp_path):
    output = tmp_path / "demo-results"
    assert eval_main(["--demo", "--output", str(output)]) == 0
    written = list(output.iterdir())
    assert written == [output / "import-graph.illustrative-example.json"]
    payload = json.loads(written[0].read_text(encoding="utf-8"))
    assert payload["illustrative"] is True
    assert "not a measured result" in payload["illustrative_note"].lower()
    # A real recorded-run artifact keeps the plain filename and no illustrative flag.
    real = tmp_path / "real-results"
    real_argv = ["--task", str(TASK_FIXTURE), "--runs", str(RUNS_FIXTURE), "--output", str(real)]
    assert eval_main(real_argv) == 0
    assert list(real.iterdir()) == [real / "import-graph.json"]
    real_payload = json.loads((real / "import-graph.json").read_text(encoding="utf-8"))
    assert "illustrative" not in real_payload
