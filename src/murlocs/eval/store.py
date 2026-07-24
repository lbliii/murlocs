"""Persist task definitions, run metadata, and raw evidence for auditing."""

from __future__ import annotations

import json
import tomllib
from dataclasses import asdict
from pathlib import Path

from murlocs.eval.model import (
    ComparisonSummary,
    ExpectedFact,
    RunRecord,
    TaskDefinition,
)


def load_task(path: Path) -> TaskDefinition:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    facts = tuple(
        ExpectedFact(
            id=str(fact["id"]),
            description=str(fact.get("description", "")),
            any_of=tuple(str(value) for value in fact["any_of"]),
        )
        for fact in data.get("expected_facts", [])
    )
    return TaskDefinition(
        id=str(data["id"]),
        prompt=str(data["prompt"]),
        target_path=str(data["target_path"]),
        repository_revision=str(data.get("repository_revision", "unknown")),
        expected_facts=facts,
        correctness_threshold=float(data.get("correctness_threshold", 1.0)),
    )


def save_results(
    directory: Path,
    task: TaskDefinition,
    summary: ComparisonSummary,
    records: list[RunRecord],
) -> Path:
    """Write the comparison summary and raw evidence deterministically as JSON."""
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "task": asdict(task),
        "summary": asdict(summary),
        "evidence": {record.arm: list(record.evidence.transcript) for record in records},
    }
    target = directory / f"{task.id}.json"
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return target


def render_summary(summary: ComparisonSummary) -> str:
    """A compact, human-readable comparison table."""
    lines = [
        f"task: {summary.task_id} @ {summary.repository_revision}",
        f"prompt: {summary.prompt}",
        "",
        f"{'arm':<14} {'correct':<9} {'steps':>6} {'lines':>7} {'bytes':>7} {'~tokens':>8}",
    ]
    for score in summary.scores:
        correct = f"{score.correctness.fraction:.0%}"
        if score.efficiency is None:
            lines.append(
                f"{score.arm:<14} {correct:<9} {'—':>6} {'—':>7} {'—':>7} {'—':>8} "
                f"(below correctness threshold)"
            )
            continue
        efficiency = score.efficiency
        lines.append(
            f"{score.arm:<14} {correct:<9} {efficiency.executable_steps:>6} "
            f"{efficiency.lines_inspected:>7} {efficiency.active_guidance_bytes:>7} "
            f"{efficiency.estimated_prompt_tokens:>8}"
        )
    lines.append("")
    if summary.most_efficient_arm is None:
        lines.append("most efficient correct arm: none met the correctness threshold")
    else:
        lines.append(f"most efficient correct arm: {summary.most_efficient_arm}")
    if summary.scores:
        model = summary.scores[0].model
        ade = summary.scores[0].ade
        lines.append(f"model/ADE: {model} / {ade}")
    return "\n".join(lines)
