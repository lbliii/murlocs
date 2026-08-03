from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from murlocs.curation import (
    CurationEvent,
    CurationEvidence,
    CurationRecord,
    load_record,
    render_record,
    stable_list_key,
)
from murlocs.eval.__main__ import main as eval_main
from murlocs.eval.longitudinal import (
    analyze_longitudinal,
    load_longitudinal,
    save_longitudinal_results,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def event(
    state: str,
    hour: int,
    *,
    rationale: str | None = None,
    source_before: str | None = None,
    source_after: str | None = None,
    related: str | None = None,
) -> CurationEvent:
    applied = state in {"promoted", "superseded", "pruned"}
    return CurationEvent(
        state=state,
        actor="@owner",
        at=f"2026-08-0{1 + hour // 24}T{hour % 24:02d}:00:00Z",
        rationale=rationale or f"{state} for longitudinal fixture",
        before_sha256=HASH_A if applied else None,
        after_sha256=HASH_B if applied else None,
        source_before_sha256=source_before,
        source_after_sha256=source_after,
        related_proposal_id=related,
    )


def record(
    proposal_id: str,
    intent: str,
    base: str,
    events: tuple[CurationEvent, ...],
) -> CurationRecord:
    return CurationRecord(
        schema_version=1,
        id=proposal_id,
        intent=intent,
        subject_kind="operating_rule",
        target_source=".murlocs/manifest.toml",
        target_scope="root",
        target_key=None if intent == "add" else stable_list_key("Added guidance."),
        base_source_sha256=base,
        origin="issue-27",
        rationale="Measure recorded outcomes.",
        proposer="@author",
        required_owners=("@owner",),
        evidence=(CurationEvidence("evaluation", "series-27", "Recorded-run evidence."),),
        payload=None if intent == "remove" else {"value": "Added guidance."},
        events=events,
    )


def write_task(path: Path, repository_revision: str) -> None:
    path.write_text(
        "\n".join(
            [
                "schema_version = 1",
                'id = "task-one"',
                'prompt = "Find the correct fact."',
                'target_path = "src/app.py"',
                f"repository_revision = {json.dumps(repository_revision)}",
                "correctness_threshold = 1.0",
                "",
                "[[expected_facts]]",
                'id = "fact"',
                'description = "The answer is objectively recorded."',
                'any_of = ["correct"]',
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_runs(
    path: Path,
    repository_revision: str,
    guidance_revision: str,
    *,
    correct: bool = True,
    steps: int = 10,
    active_bytes: int = 0,
    run_id: str = "fixture",
) -> None:
    runs = []
    for arm in ("no-guidance", "inline-dump", "murlocs"):
        runs.append(
            {
                "arm": arm,
                "model": "model-v1",
                "ade": "ade-v1",
                "guidance_revision": (
                    guidance_revision
                    if arm == "murlocs"
                    else ("none" if arm == "no-guidance" else "inline")
                ),
                "answer": "correct" if correct or arm != "murlocs" else "wrong",
                "guidance_text": "x" * active_bytes if arm == "murlocs" else "",
                "evidence": {
                    "files_inspected": steps // 2,
                    "lines_inspected": steps * 3,
                    "tool_calls": steps,
                    "executable_steps": steps,
                    "transcript": [f"recorded {arm} evidence for {run_id}"],
                },
            }
        )
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "task_id": "task-one",
                "repository_revision": repository_revision,
                "runs": runs,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def build_series(tmp_path: Path) -> Path:
    records = tmp_path / "records"
    tasks = tmp_path / "tasks"
    runs = tmp_path / "runs"
    records.mkdir(parents=True)
    tasks.mkdir()
    runs.mkdir()
    proposals = [
        record(
            "addition",
            "add",
            HASH_A,
            (
                event("proposed", 0),
                event("accepted", 1),
                event("promoted", 2, source_before=HASH_A, source_after=HASH_B),
                event(
                    "superseded",
                    26,
                    rationale="replacement transaction",
                    source_before=HASH_B,
                    source_after=HASH_C,
                    related="replacement",
                ),
            ),
        ),
        record(
            "replacement",
            "replace",
            HASH_B,
            (
                event("proposed", 24),
                event("accepted", 25),
                event(
                    "promoted",
                    26,
                    rationale="replacement transaction",
                    source_before=HASH_B,
                    source_after=HASH_C,
                    related="addition",
                ),
            ),
        ),
        record(
            "rejected",
            "add",
            HASH_C,
            (event("proposed", 27), event("rejected", 28)),
        ),
        record(
            "pruning",
            "remove",
            HASH_C,
            (
                event("proposed", 29),
                event("accepted", 30),
                event("pruned", 31, source_before=HASH_C, source_after=HASH_D),
            ),
        ),
    ]
    for item in proposals:
        (records / f"{item.id}.toml").write_text(render_record(item), encoding="utf-8")

    revisions = {
        "addition": ("repo-0", "repo-1", HASH_A, HASH_B, "guide-0", "guide-1", 100, 110),
        "replacement": (
            "repo-1",
            "repo-2",
            HASH_B,
            HASH_C,
            "guide-1",
            "guide-2",
            110,
            106,
        ),
        "rejected": ("repo-2", None, HASH_C, None, "guide-2", None, 106, None),
        "pruning": ("repo-2", "repo-3", HASH_C, HASH_D, "guide-2", "guide-3", 106, 90),
    }
    links = []
    observations = []
    for proposal_id, values in revisions.items():
        (
            repo_before,
            repo_after,
            source_before,
            source_after,
            guide_before,
            guide_after,
            size_before,
            size_after,
        ) = values
        links.append(
            {
                "record": f"records/{proposal_id}.toml",
                "revisions": {
                    "repository_before": repo_before,
                    "repository_after": repo_after,
                    "source_before": source_before,
                    "source_after": source_after,
                    "guidance_before": guide_before,
                    "guidance_after": guide_after,
                },
                "affected_chains": [
                    {
                        "scope": "root",
                        "chain": ["root"],
                        "active_bytes_before": size_before,
                        "active_bytes_after": size_after,
                    }
                ],
            }
        )
        phases = [("before", repo_before, source_before, guide_before, 12, size_before)]
        if repo_after is not None:
            phases.append(("after", repo_after, source_after, guide_after, 8, size_after))
        for phase, repo_revision, source_revision, guide_revision, steps, active_bytes in phases:
            stem = f"{proposal_id}-{phase}"
            task_path = tasks / f"{stem}.toml"
            runs_path = runs / f"{stem}.json"
            write_task(task_path, repo_revision)
            write_runs(
                runs_path,
                repo_revision,
                guide_revision,
                correct=not (proposal_id == "replacement" and phase == "after"),
                steps=steps,
                active_bytes=active_bytes,
                run_id=stem,
            )
            observations.append(
                {
                    "proposal_id": proposal_id,
                    "phase": phase,
                    "scope": "root",
                    "chain": ["root"],
                    "source_revision": source_revision,
                    "task": f"tasks/{stem}.toml",
                    "runs": f"runs/{stem}.json",
                }
            )
    manifest = tmp_path / "longitudinal.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "series_id": "series-27",
                "proposals": links,
                "observations": observations,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest


def test_longitudinal_summary_distinguishes_lifecycle_and_preserves_evidence(tmp_path):
    path = build_series(tmp_path)
    result = analyze_longitudinal(load_longitudinal(path))

    assert result["summary"] == {
        "proposal_count": 4,
        "states": {"pruned": 1, "promoted": 1, "rejected": 1, "superseded": 1},
        "intents": {"add": 2, "remove": 1, "replace": 1},
        "accepted": 3,
        "acceptance_rate": 0.75,
        "applied_additions": 1,
        "applied_replacements": 1,
        "supersessions": 1,
        "rejections": 1,
        "pruning": 1,
        "replacement_to_addition_ratio": 1.0,
        "recorded_active_bytes_delta": -10,
    }
    assert [item["proposal_id"] for item in result["active_bytes_timeline"]] == [
        "addition",
        "replacement",
        "rejected",
        "pruning",
    ]
    assert all(
        item["time_to_decision_seconds"] == 3600
        for item in result["active_bytes_timeline"]
    )
    assert result["active_bytes_timeline"][-1][
        "cumulative_recorded_active_bytes_delta"
    ] == -10
    evidence = result["raw_evidence"][0]
    assert evidence["proposal_id"] == "addition"
    assert evidence["lifecycle_state"] == "superseded"
    assert evidence["affected_chains"] == [
        {
            "scope": "root",
            "chain": ["root"],
            "active_bytes_before": 100,
            "active_bytes_after": 110,
        }
    ]
    assert evidence["revisions"]["source_after"] == HASH_B
    assert evidence["observations"][0]["records"][2]["evidence"]["transcript"]
    assert len(evidence["observations"][0]["runs_sha256"]) == 64
    assert len(evidence["observations"][0]["snapshot_sha256"]) == 64
    assert "efficiency_delta" in result["metric_definitions"]


def test_correctness_gate_withholds_every_efficiency_delta(tmp_path):
    result = analyze_longitudinal(load_longitudinal(build_series(tmp_path)))
    replacement = next(
        item for item in result["comparisons"] if item["proposal_id"] == "replacement"
    )
    assert replacement["before"]["correctness"]["passed"] is True
    assert replacement["after"]["correctness"]["passed"] is False
    assert replacement["correctness_gate_passed"] is False
    assert replacement["before"]["efficiency"] is None
    assert replacement["after"]["efficiency"] is None
    assert replacement["efficiency_delta"] is None
    assert "withheld" in replacement["gate_reason"]
    assert not any(key.startswith("efficiency") for key in result["summary"])


def test_revision_mismatch_and_incompatible_task_are_rejected(tmp_path):
    path = build_series(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["observations"][1]["source_revision"] = HASH_D
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="source_revision.*does not match"):
        load_longitudinal(path)

    path = build_series(tmp_path / "incompatible")
    task = tmp_path / "incompatible/tasks/addition-after.toml"
    task.write_text(
        task.read_text(encoding="utf-8").replace(
            'prompt = "Find the correct fact."', 'prompt = "A relabeled different task."'
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="revision-incompatible task definitions"):
        load_longitudinal(path)


@pytest.mark.parametrize("field", ["repository", "guidance", "bytes"])
def test_run_metadata_cannot_relabel_incompatible_revision(tmp_path, field):
    path = build_series(tmp_path)
    task_path = tmp_path / "tasks/addition-before.toml"
    runs_path = tmp_path / "runs/addition-before.json"
    runs = json.loads(runs_path.read_text(encoding="utf-8"))
    murlocs = next(item for item in runs["runs"] if item["arm"] == "murlocs")
    if field == "repository":
        task_path.write_text(
            task_path.read_text(encoding="utf-8").replace("repo-0", "repo-relabeled"),
            encoding="utf-8",
        )
        runs["repository_revision"] = "repo-relabeled"
        expected = "task repository_revision.*does not match proposal"
    elif field == "guidance":
        murlocs["guidance_revision"] = "guide-relabeled"
        expected = "murlocs guidance_revision.*does not match proposal"
    else:
        murlocs["guidance_text"] += "extra"
        expected = "guidance bytes do not match"
    runs_path.write_text(json.dumps(runs), encoding="utf-8")
    with pytest.raises(ValueError, match=expected):
        load_longitudinal(path)


def test_longitudinal_join_rejects_task_without_objective_facts(tmp_path):
    path = build_series(tmp_path)
    task = tmp_path / "tasks/addition-before.toml"
    task.write_text(
        task.read_text(encoding="utf-8").split("[[expected_facts]]", 1)[0]
        + "expected_facts = []\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="expected_facts must contain at least one"):
        load_longitudinal(path)


def test_longitudinal_join_rejects_zero_threshold_with_wrong_answers(tmp_path):
    path = build_series(tmp_path)
    task = tmp_path / "tasks/addition-before.toml"
    task.write_text(
        task.read_text(encoding="utf-8").replace(
            "correctness_threshold = 1.0", "correctness_threshold = 0.0"
        ),
        encoding="utf-8",
    )
    runs_path = tmp_path / "runs/addition-before.json"
    runs = json.loads(runs_path.read_text(encoding="utf-8"))
    for item in runs["runs"]:
        item["answer"] = "wholly wrong"
    runs_path.write_text(json.dumps(runs), encoding="utf-8")

    with pytest.raises(ValueError, match="must be greater than 0 and at most 1"):
        load_longitudinal(path)


@pytest.mark.parametrize("failure", ["missing", "ambiguous"])
def test_missing_and_ambiguous_run_links_are_rejected(tmp_path, failure):
    path = build_series(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if failure == "missing":
        payload["observations"] = [
            item
            for item in payload["observations"]
            if not (item["proposal_id"] == "pruning" and item["phase"] == "after")
        ]
        expected = "requires matching before/after"
    else:
        payload["observations"].append(dict(payload["observations"][0]))
        expected = "ambiguous duplicate observation"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=expected):
        load_longitudinal(path)


def test_longitudinal_output_is_deterministic_and_inputs_are_read_only(tmp_path):
    path = build_series(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    reversed_path = tmp_path / "reversed.json"
    reversed_path.write_text(
        json.dumps(
            {
                **payload,
                "proposals": list(reversed(payload["proposals"])),
                "observations": list(reversed(payload["observations"])),
            }
        ),
        encoding="utf-8",
    )
    before = {
        item.relative_to(tmp_path).as_posix(): item.read_bytes()
        for item in tmp_path.rglob("*")
        if item.is_file()
    }
    first = analyze_longitudinal(load_longitudinal(path))
    assert {
        item.relative_to(tmp_path).as_posix(): item.read_bytes()
        for item in tmp_path.rglob("*")
        if item.is_file()
    } == before
    for runs_path in (tmp_path / "runs").glob("*.json"):
        runs = json.loads(runs_path.read_text(encoding="utf-8"))
        runs["runs"].reverse()
        runs_path.write_text(json.dumps(runs), encoding="utf-8")
    reordered = {
        item.relative_to(tmp_path).as_posix(): item.read_bytes()
        for item in tmp_path.rglob("*")
        if item.is_file()
    }
    second = analyze_longitudinal(load_longitudinal(reversed_path))
    after = {
        item.relative_to(tmp_path).as_posix(): item.read_bytes()
        for item in tmp_path.rglob("*")
        if item.is_file()
    }
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert after == reordered


def test_longitudinal_cli_writes_only_explicit_result(tmp_path, capsys):
    path = build_series(tmp_path)
    output = tmp_path / "results"
    assert eval_main(["--longitudinal", str(path), "--output", str(output)]) == 0
    assert "acceptance rate: 75.0%" in capsys.readouterr().out
    result = json.loads((output / "series-27.json").read_text(encoding="utf-8"))
    assert result["summary"]["rejections"] == 1
    assert result["raw_evidence"][0]["record"]["id"] == "addition"


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_longitudinal_save_replaces_links_without_mutating_external_target(
    tmp_path, link_kind
):
    result = analyze_longitudinal(load_longitudinal(build_series(tmp_path / "series")))
    output = tmp_path / "results"
    output.mkdir()
    victim = tmp_path / "external.json"
    victim.write_text("external content\n", encoding="utf-8")
    target = output / "series-27.json"
    if link_kind == "symlink":
        target.symlink_to(victim)
    else:
        os.link(victim, target)

    assert save_longitudinal_results(output, result) == target

    assert victim.read_text(encoding="utf-8") == "external content\n"
    assert not target.is_symlink()
    assert target.is_file()
    assert target.stat().st_ino != victim.stat().st_ino
    assert json.loads(target.read_text(encoding="utf-8"))["series_id"] == "series-27"


def test_supersession_must_have_reciprocal_proposal_link(tmp_path):
    path = build_series(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["proposals"] = [
        item for item in payload["proposals"] if not item["record"].endswith("replacement.toml")
    ]
    payload["observations"] = [
        item for item in payload["observations"] if item["proposal_id"] != "replacement"
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="missing related proposal link"):
        load_longitudinal(path)


def rewrite_superseded_event(tmp_path: Path, **changes: object) -> None:
    record_path = tmp_path / "records/addition.toml"
    current = load_record(record_path, expected_id="addition")
    terminal = replace(current.events[-1], **changes)
    updated = replace(current, events=(*current.events[:-1], terminal))
    record_path.write_text(render_record(updated), encoding="utf-8")


def test_superseded_source_after_must_match_replacement_transaction(tmp_path):
    path = build_series(tmp_path)
    rewrite_superseded_event(tmp_path, source_after_sha256=HASH_D)

    with pytest.raises(ValueError, match="supersession transaction.*source_after_sha256"):
        load_longitudinal(path)


@pytest.mark.parametrize(
    ("field", "mismatch"),
    [
        ("before_sha256", HASH_D),
        ("after_sha256", HASH_D),
        ("source_before_sha256", HASH_D),
        ("at", "2026-08-02T03:00:00Z"),
        ("actor", "@different-owner"),
        ("rationale", "different transaction rationale"),
        ("review_ref", "review-elsewhere"),
    ],
)
def test_supersession_events_must_share_every_audit_field(tmp_path, field, mismatch):
    path = build_series(tmp_path)
    rewrite_superseded_event(tmp_path, **{field: mismatch})

    with pytest.raises(ValueError, match=rf"supersession transaction.*{field}"):
        load_longitudinal(path)


def test_duplicate_affected_chain_is_ambiguous(tmp_path):
    path = build_series(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["proposals"][0]["affected_chains"].append(
        dict(payload["proposals"][0]["affected_chains"][0])
    )
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="ambiguous duplicate affected guidance chain"):
        load_longitudinal(path)


def test_physical_snapshot_cannot_be_attributed_to_unrelated_proposals(tmp_path):
    path = build_series(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    pruning = next(
        item
        for item in payload["observations"]
        if item["proposal_id"] == "pruning" and item["phase"] == "before"
    )
    rejected = next(
        item for item in payload["observations"] if item["proposal_id"] == "rejected"
    )
    copied_task = tmp_path / "tasks/copied-unrelated.toml"
    copied_runs = tmp_path / "runs/copied-unrelated.json"
    copied_task.write_bytes((tmp_path / pruning["task"]).read_bytes())
    reordered_runs = json.loads((tmp_path / pruning["runs"]).read_text(encoding="utf-8"))
    reordered_runs["runs"].reverse()
    copied_runs.write_text(json.dumps(reordered_runs), encoding="utf-8")
    rejected["task"] = "tasks/copied-unrelated.toml"
    rejected["runs"] = "runs/copied-unrelated.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="may be reused only.*adjacent applied proposals"):
        load_longitudinal(path)


def test_adjacent_after_to_before_snapshot_reuse_is_explicitly_safe(tmp_path):
    path = build_series(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    addition_after = next(
        item
        for item in payload["observations"]
        if item["proposal_id"] == "addition" and item["phase"] == "after"
    )
    replacement_before = next(
        item
        for item in payload["observations"]
        if item["proposal_id"] == "replacement" and item["phase"] == "before"
    )
    copied_task = tmp_path / "tasks/copied-adjacent.toml"
    copied_runs = tmp_path / "runs/copied-adjacent.json"
    copied_task.write_bytes((tmp_path / addition_after["task"]).read_bytes())
    copied_runs.write_bytes((tmp_path / addition_after["runs"]).read_bytes())
    replacement_before["task"] = "tasks/copied-adjacent.toml"
    replacement_before["runs"] = "runs/copied-adjacent.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = analyze_longitudinal(load_longitudinal(path))
    assert len(result["comparisons"]) == 3


@pytest.mark.parametrize(
    "disconnect", ["revision", "chain_bytes", "unapplied", "cross_source"]
)
def test_linear_series_rejects_disconnected_or_branching_history(tmp_path, disconnect):
    path = build_series(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    replacement = next(
        item
        for item in payload["proposals"]
        if item["record"].endswith("replacement.toml")
    )
    if disconnect == "revision":
        replacement["revisions"]["repository_before"] = "branch-x"
        expected = "disconnected revision history"
    elif disconnect == "chain_bytes":
        replacement["affected_chains"][0]["active_bytes_before"] = 999
        expected = "affected chain bytes conflict"
    elif disconnect == "unapplied":
        rejected = next(
            item
            for item in payload["proposals"]
            if item["record"].endswith("rejected.toml")
        )
        rejected["revisions"]["guidance_before"] = "disconnected-guidance"
        expected = "unapplied proposal.*disconnected"
    else:
        record_path = tmp_path / "records/rejected.toml"
        record_path.write_text(
            record_path.read_text(encoding="utf-8").replace(
                'target_source = ".murlocs/manifest.toml"',
                'target_source = ".murlocs/layers/other.toml"',
            ),
            encoding="utf-8",
        )
        expected = "must target exactly one active source"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=expected):
        load_longitudinal(path)


def test_record_target_scope_must_be_in_supplied_affected_scopes(tmp_path):
    path = build_series(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["proposals"][0]["affected_chains"][0]["scope"] = "unrelated"
    payload["proposals"][0]["affected_chains"][0]["chain"] = ["unrelated"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="record target_scope.*must be among"):
        load_longitudinal(path)


def test_longitudinal_references_reject_traversal_and_symlinks(tmp_path):
    path = build_series(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["proposals"][0]["record"] = "../outside.toml"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="safe path relative"):
        load_longitudinal(path)

    path = build_series(tmp_path / "symlink")
    payload = json.loads(path.read_text(encoding="utf-8"))
    original = tmp_path / "symlink/tasks/addition-before.toml"
    linked = tmp_path / "symlink/tasks/linked.toml"
    linked.symlink_to(original)
    payload["observations"][0]["task"] = "tasks/linked.toml"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="may not traverse a symlink"):
        load_longitudinal(path)
