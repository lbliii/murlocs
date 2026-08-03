"""Correctness gating and efficiency scoring for recorded guidance-arm runs."""

from __future__ import annotations

from math import ceil

from murlocs.eval.model import (
    ARMS,
    ComparisonSummary,
    CorrectnessResult,
    Efficiency,
    RunRecord,
    RunScore,
    TaskDefinition,
)


def estimate_tokens(text: str) -> int:
    """Heuristic token estimate. Real tokenization is model-specific; this is an estimate."""
    return ceil(len(text.encode("utf-8")) / 4)


def guidance_bytes(text: str) -> int:
    return len(text.encode("utf-8"))


def check_correctness(task: TaskDefinition, record: RunRecord) -> CorrectnessResult:
    """Every expected fact must appear (any_of, case-insensitively) in the answer."""
    answer = record.answer.lower()
    matched: list[str] = []
    missing: list[str] = []
    for fact in task.expected_facts:
        if any(candidate.lower() in answer for candidate in fact.any_of):
            matched.append(fact.id)
        else:
            missing.append(fact.id)
    total = len(task.expected_facts)
    fraction = 1.0 if total == 0 else len(matched) / total
    return CorrectnessResult(
        passed=fraction >= task.correctness_threshold,
        fraction=fraction,
        matched=tuple(matched),
        missing=tuple(missing),
    )


def score_run(task: TaskDefinition, record: RunRecord) -> RunScore:
    """Score one run. Efficiency is withheld unless correctness meets the threshold."""
    correctness = check_correctness(task, record)
    efficiency: Efficiency | None = None
    if correctness.passed:
        efficiency = Efficiency(
            files_inspected=record.evidence.files_inspected,
            lines_inspected=record.evidence.lines_inspected,
            tool_calls=record.evidence.tool_calls,
            executable_steps=record.evidence.executable_steps,
            active_guidance_bytes=guidance_bytes(record.guidance_text),
            estimated_prompt_tokens=estimate_tokens(record.guidance_text),
        )
    return RunScore(
        arm=record.arm,
        model=record.model,
        ade=record.ade,
        guidance_revision=record.guidance_revision,
        correctness=correctness,
        efficiency=efficiency,
    )


def _efficiency_key(score: RunScore) -> tuple[int, int, int, int]:
    efficiency = score.efficiency
    assert efficiency is not None
    return (
        efficiency.executable_steps,
        efficiency.lines_inspected,
        efficiency.tool_calls,
        efficiency.estimated_prompt_tokens,
    )


def compare_runs(task: TaskDefinition, records: list[RunRecord]) -> ComparisonSummary:
    """Score every arm and pick the most efficient among those that met correctness."""
    scores = [
        score_run(task, record)
        for record in sorted(records, key=lambda item: ARMS.index(item.arm))
    ]
    correct = [score for score in scores if score.efficiency is not None]
    most_efficient = min(correct, key=_efficiency_key).arm if correct else None
    return ComparisonSummary(
        task_id=task.id,
        prompt=task.prompt,
        repository_revision=task.repository_revision,
        scores=tuple(scores),
        most_efficient_arm=most_efficient,
    )
