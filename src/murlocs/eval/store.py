"""Persist task definitions, run metadata, and raw evidence for auditing."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import asdict
from pathlib import Path
from typing import Any

from murlocs.eval._atomic import atomic_write_text
from murlocs.eval.model import (
    ARMS,
    ComparisonSummary,
    ExpectedFact,
    RunEvidence,
    RunRecord,
    TaskDefinition,
)

SCHEMA_VERSION = 1
MAX_TASK_ID_LENGTH = 128
TASK_ID_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")


def load_task(path: Path) -> TaskDefinition:
    """Load and validate a versioned TOML task definition."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"cannot read task file {path}: {exc}") from exc
    _schema_version(data, path)
    _reject_unknown(
        data,
        {
            "schema_version",
            "id",
            "prompt",
            "target_path",
            "repository_revision",
            "correctness_threshold",
            "expected_facts",
        },
        str(path),
    )
    raw_facts = _list(data, "expected_facts", path)
    facts: list[ExpectedFact] = []
    fact_ids: set[str] = set()
    for index, raw_fact in enumerate(raw_facts):
        context = f"{path}: expected_facts[{index}]"
        fact = _mapping(raw_fact, context)
        _reject_unknown(fact, {"id", "description", "any_of"}, context)
        fact_id = _nonempty_string(fact, "id", context)
        if fact_id in fact_ids:
            raise ValueError(f"{context}: duplicate expected fact id {fact_id!r}")
        fact_ids.add(fact_id)
        candidates = tuple(
            _string(value, f"{context}.any_of[{candidate_index}]")
            for candidate_index, value in enumerate(_list(fact, "any_of", context))
        )
        if not candidates or any(not candidate for candidate in candidates):
            raise ValueError(f"{context}.any_of must contain non-empty strings")
        facts.append(
            ExpectedFact(
                id=fact_id,
                description=_optional_string(fact, "description", context),
                any_of=candidates,
            )
        )
    if not facts:
        raise ValueError(
            f"{path}: expected_facts must contain at least one objectively checkable fact"
        )
    threshold = _number(data.get("correctness_threshold", 1.0), f"{path}: correctness_threshold")
    if not 0 < threshold <= 1:
        raise ValueError(f"{path}: correctness_threshold must be greater than 0 and at most 1")
    return TaskDefinition(
        id=_task_id(_nonempty_string(data, "id", str(path)), f"{path}: id"),
        prompt=_nonempty_string(data, "prompt", str(path)),
        target_path=_nonempty_string(data, "target_path", str(path)),
        repository_revision=_nonempty_string(data, "repository_revision", str(path)),
        expected_facts=tuple(facts),
        correctness_threshold=threshold,
    )


def load_runs(path: Path, task: TaskDefinition) -> list[RunRecord]:
    """Load recorded agent runs, checking task identity, revisions, and experiment arms."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read recorded-run file {path}: {exc}") from exc
    payload = _mapping(data, str(path))
    _schema_version(payload, path)
    _reject_unknown(
        payload,
        {"schema_version", "task_id", "repository_revision", "runs"},
        str(path),
    )
    task_id = _nonempty_string(payload, "task_id", str(path))
    if task_id != task.id:
        raise ValueError(f"{path}: task_id {task_id!r} does not match task {task.id!r}")
    revision = _nonempty_string(payload, "repository_revision", str(path))
    if revision != task.repository_revision:
        raise ValueError(
            f"{path}: repository_revision {revision!r} does not match task "
            f"{task.repository_revision!r}"
        )

    raw_runs = _list(payload, "runs", path)
    records: list[RunRecord] = []
    seen_arms: set[str] = set()
    for index, raw_run in enumerate(raw_runs):
        context = f"{path}: runs[{index}]"
        run = _mapping(raw_run, context)
        _reject_unknown(
            run,
            {
                "arm",
                "model",
                "ade",
                "guidance_revision",
                "answer",
                "guidance_text",
                "evidence",
            },
            context,
        )
        arm = _nonempty_string(run, "arm", context)
        if arm not in ARMS:
            raise ValueError(f"{context}.arm must be one of {', '.join(ARMS)}; got {arm!r}")
        if arm in seen_arms:
            raise ValueError(f"{context}: duplicate recorded run for arm {arm!r}")
        seen_arms.add(arm)
        evidence = _mapping(run.get("evidence"), f"{context}.evidence")
        _reject_unknown(
            evidence,
            {
                "files_inspected",
                "lines_inspected",
                "tool_calls",
                "executable_steps",
                "transcript",
            },
            f"{context}.evidence",
        )
        transcript = tuple(
            _string(value, f"{context}.evidence.transcript[{step}]")
            for step, value in enumerate(_list(evidence, "transcript", context))
        )
        records.append(
            RunRecord(
                arm=arm,
                model=_nonempty_string(run, "model", context),
                ade=_nonempty_string(run, "ade", context),
                guidance_revision=_nonempty_string(run, "guidance_revision", context),
                answer=_string(run.get("answer"), f"{context}.answer"),
                guidance_text=_string(run.get("guidance_text"), f"{context}.guidance_text"),
                evidence=RunEvidence(
                    files_inspected=_nonnegative_integer(evidence, "files_inspected", context),
                    lines_inspected=_nonnegative_integer(evidence, "lines_inspected", context),
                    tool_calls=_nonnegative_integer(evidence, "tool_calls", context),
                    executable_steps=_nonnegative_integer(evidence, "executable_steps", context),
                    transcript=transcript,
                ),
            )
        )
    missing = [arm for arm in ARMS if arm not in seen_arms]
    if missing:
        raise ValueError(f"{path}: missing recorded runs for arms: {', '.join(missing)}")
    return sorted(records, key=lambda record: ARMS.index(record.arm))


def save_results(
    directory: Path,
    task: TaskDefinition,
    summary: ComparisonSummary,
    records: list[RunRecord],
    *,
    illustrative: bool = False,
) -> Path:
    """Write the comparison summary and raw evidence deterministically as JSON."""
    _task_id(task.id, "task id")
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "task": asdict(task),
        "summary": asdict(summary),
        "records": [
            asdict(record) for record in sorted(records, key=lambda item: ARMS.index(item.arm))
        ],
    }
    if illustrative:
        payload["illustrative"] = True
        payload["illustrative_note"] = (
            "Synthetic FORMAT EXAMPLE produced by --demo. Not a measured result; "
            "the figures are hand-authored and imply no verdict."
        )
        target = directory / f"{task.id}.illustrative-example.json"
    else:
        target = directory / f"{task.id}.json"
    return atomic_write_text(target, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _schema_version(data: dict[str, Any], path: Path) -> None:
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"{path}: unsupported schema_version {version!r}; expected {SCHEMA_VERSION}"
        )


def _task_id(value: str, context: str) -> str:
    if len(value) > MAX_TASK_ID_LENGTH or ".." in value or TASK_ID_PATTERN.fullmatch(value) is None:
        raise ValueError(
            f"{context} must be 1-{MAX_TASK_ID_LENGTH} ASCII letters, digits, dots, "
            "underscores, or hyphens; it must start and end with a letter or digit and "
            "must not contain '..'"
        )
    return value


def _reject_unknown(data: dict[str, Any], allowed: set[str], context: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{context}: unknown fields: {', '.join(unknown)}")


def _mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object/table")
    return value


def _list(data: dict[str, Any], key: str, context: str | Path) -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{context}: {key} must be a list")
    return value


def _string(value: Any, context: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{context} must be a string")
    return value


def _nonempty_string(data: dict[str, Any], key: str, context: str) -> str:
    value = _string(data.get(key), f"{context}: {key}")
    if not value.strip():
        raise ValueError(f"{context}: {key} must not be empty")
    return value


def _optional_string(data: dict[str, Any], key: str, context: str) -> str:
    value = data.get(key, "")
    return _string(value, f"{context}: {key}")


def _number(value: Any, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{context} must be a number")
    return float(value)


def _nonnegative_integer(data: dict[str, Any], key: str, context: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{context}.evidence.{key} must be a non-negative integer")
    return value


ILLUSTRATIVE_BANNER: tuple[str, ...] = (
    "=" * 72,
    "FORMAT EXAMPLE - illustrative synthetic data, NOT a measured result.",
    "The model, ADE, and efficiency figures below are hand-authored to show",
    "the shape of a real comparison. They are not evidence, they were not",
    "measured, and they imply no verdict about any guidance arm.",
    "=" * 72,
)


def render_summary(summary: ComparisonSummary, *, illustrative: bool = False) -> str:
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
    if illustrative:
        lines.append(
            "verdict: withheld - illustrative synthetic data implies no efficiency "
            "comparison and names no most-efficient arm"
        )
    elif summary.most_efficient_arm is None:
        lines.append("most efficient correct arm: none met the correctness threshold")
    else:
        lines.append(f"most efficient correct arm: {summary.most_efficient_arm}")
    if summary.scores:
        model = summary.scores[0].model
        ade = summary.scores[0].ade
        lines.append(f"model/ADE: {model} / {ade}")
    if illustrative:
        return "\n".join([*ILLUSTRATIVE_BANNER, "", *lines])
    return "\n".join(lines)
