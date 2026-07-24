"""Data model for the agent-guidance efficiency evaluation harness.

This package is intentionally separate from the deterministic Murlocs core: `compile`
and `check` never import it. The harness scores *recorded* runs of a coding agent rather
than invoking a model itself, so results are auditable and reproducible. It compares three
guidance arms — no repository guidance, a large inline guidance dump, and compiled scoped
Murlocs guidance — measuring correctness first and efficiency only once correctness holds.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ARMS = ("no-guidance", "inline-dump", "murlocs")

# What each efficiency metric means, and — just as important — what it cannot establish.
METRIC_DEFINITIONS: dict[str, str] = {
    "files_inspected": "Distinct files the agent opened while answering.",
    "lines_inspected": "Total lines the agent read, summed across inspected files.",
    "tool_calls": "Number of tool invocations (search, read, run) the agent issued.",
    "executable_steps": "Discrete executable actions in the transcript (commands or edits).",
    "active_guidance_bytes": "UTF-8 byte size of the guidance placed in front of the agent.",
    "estimated_prompt_tokens": (
        "Heuristic token estimate (bytes / 4) for the guidance text. An estimate, not a "
        "billed count; real tokenization is model-specific."
    ),
    "caveats": (
        "Efficiency numbers describe search and action cost on one task and repository "
        "revision with one model. They do not establish that guidance improves correctness "
        "in general, nor that lower cost is always better if it sacrifices coverage."
    ),
}


@dataclass(frozen=True)
class ExpectedFact:
    """An objectively checkable claim a correct answer must contain."""

    id: str
    description: str
    any_of: tuple[str, ...]


@dataclass(frozen=True)
class TaskDefinition:
    id: str
    prompt: str
    target_path: str
    repository_revision: str
    expected_facts: tuple[ExpectedFact, ...]
    correctness_threshold: float = 1.0


@dataclass(frozen=True)
class RunEvidence:
    files_inspected: int
    lines_inspected: int
    tool_calls: int
    executable_steps: int
    transcript: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunRecord:
    arm: str
    model: str
    ade: str
    guidance_revision: str
    answer: str
    guidance_text: str
    evidence: RunEvidence


@dataclass(frozen=True)
class CorrectnessResult:
    passed: bool
    fraction: float
    matched: tuple[str, ...]
    missing: tuple[str, ...]


@dataclass(frozen=True)
class Efficiency:
    files_inspected: int
    lines_inspected: int
    tool_calls: int
    executable_steps: int
    active_guidance_bytes: int
    estimated_prompt_tokens: int


@dataclass(frozen=True)
class RunScore:
    arm: str
    model: str
    ade: str
    guidance_revision: str
    correctness: CorrectnessResult
    efficiency: Efficiency | None


@dataclass(frozen=True)
class ComparisonSummary:
    task_id: str
    prompt: str
    repository_revision: str
    scores: tuple[RunScore, ...]
    most_efficient_arm: str | None
    metric_definitions: dict[str, str] = field(default_factory=lambda: dict(METRIC_DEFINITIONS))
