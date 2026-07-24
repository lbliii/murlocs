"""Run the bundled evaluation task over illustrative recorded runs.

Usage: ``python -m murlocs.eval [RESULTS_DIR]``

The records here are illustrative recorded runs, not a live agent invocation: the harness
scores recorded evidence so results stay auditable and reproducible. Point it at your own
recorded transcripts to evaluate real runs.
"""

from __future__ import annotations

import sys
from pathlib import Path

from murlocs.eval.harness import compare_runs
from murlocs.eval.model import RunEvidence, RunRecord
from murlocs.eval.store import load_task, render_summary, save_results

FIXTURES = Path(__file__).parent / "fixtures"

# Illustrative recorded runs. Correctness is objective; the efficiency numbers mirror the
# qualitative pattern reported in the steward experiment (scoped guidance reduces search).
SAMPLE_RECORDS = [
    RunRecord(
        arm="no-guidance",
        model="illustrative-model",
        ade="illustrative-ade",
        guidance_revision="none",
        answer="service.py imports app.render and app.store.",
        guidance_text="",
        evidence=RunEvidence(
            files_inspected=9,
            lines_inspected=643,
            tool_calls=18,
            executable_steps=42,
            transcript=("grep -r import src/", "read src/app/service.py"),
        ),
    ),
    RunRecord(
        arm="inline-dump",
        model="illustrative-model",
        ade="illustrative-ade",
        guidance_revision="dump-1",
        answer="It depends on app.render and app.store.",
        guidance_text="# Whole-repo guidance dump\n" + ("context line\n" * 400),
        evidence=RunEvidence(
            files_inspected=4,
            lines_inspected=120,
            tool_calls=9,
            executable_steps=30,
            transcript=("read AGENTS.md", "read src/app/service.py"),
        ),
    ),
    RunRecord(
        arm="murlocs",
        model="illustrative-model",
        ade="illustrative-ade",
        guidance_revision="lock-abc123",
        answer="The app.render and app.store modules.",
        guidance_text="# app scope\nservice.py depends on render and store.\n",
        evidence=RunEvidence(
            files_inspected=2,
            lines_inspected=12,
            tool_calls=3,
            executable_steps=23,
            transcript=("read src/app/AGENTS.md", "read src/app/service.py"),
        ),
    ),
]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    task = load_task(FIXTURES / "tasks" / "import-graph.toml")
    summary = compare_runs(task, SAMPLE_RECORDS)
    print(render_summary(summary))
    if argv:
        target = save_results(Path(argv[0]), task, summary, SAMPLE_RECORDS)
        print(f"\nwrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
