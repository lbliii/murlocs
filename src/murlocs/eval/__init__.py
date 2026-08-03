"""Optional agent-guidance efficiency evaluation harness.

Separate from the deterministic Murlocs core: `murlocs compile` and `murlocs check` never
import this package. See `docs/evaluation.md` for what the measurements can and cannot show.
"""

from __future__ import annotations

from murlocs.eval.harness import (
    check_correctness,
    compare_runs,
    estimate_tokens,
    guidance_bytes,
    score_run,
)
from murlocs.eval.model import (
    ARMS,
    METRIC_DEFINITIONS,
    ComparisonSummary,
    CorrectnessResult,
    Efficiency,
    ExpectedFact,
    RunEvidence,
    RunRecord,
    RunScore,
    TaskDefinition,
)
from murlocs.eval.store import SCHEMA_VERSION, load_runs, load_task, render_summary, save_results

__all__ = [
    "ARMS",
    "METRIC_DEFINITIONS",
    "ComparisonSummary",
    "CorrectnessResult",
    "Efficiency",
    "ExpectedFact",
    "RunEvidence",
    "RunRecord",
    "RunScore",
    "SCHEMA_VERSION",
    "TaskDefinition",
    "check_correctness",
    "compare_runs",
    "estimate_tokens",
    "guidance_bytes",
    "load_task",
    "load_runs",
    "render_summary",
    "save_results",
    "score_run",
]
