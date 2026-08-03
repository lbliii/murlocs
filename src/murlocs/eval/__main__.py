"""Score versioned recorded agent runs without invoking a live model."""

from __future__ import annotations

import argparse
from pathlib import Path

from murlocs.eval.harness import compare_runs
from murlocs.eval.store import load_runs, load_task, render_summary, save_results

FIXTURES = Path(__file__).parent / "fixtures"
DEMO_TASK = FIXTURES / "tasks" / "import-graph.toml"
DEMO_RUNS = FIXTURES / "runs" / "import-graph.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m murlocs.eval",
        description="Score recorded agent runs; this command never invokes a model.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--demo", action="store_true", help="score the bundled example records")
    source.add_argument("--task", type=Path, help="versioned TOML task definition")
    parser.add_argument("--runs", type=Path, help="versioned JSON recorded-run dataset")
    parser.add_argument("--output", type=Path, help="directory for deterministic JSON results")
    args = parser.parse_args(argv)
    if args.demo and args.runs is not None:
        parser.error("--runs cannot be combined with --demo")
    if args.task is not None and args.runs is None:
        parser.error("--runs is required with --task")

    task_path = DEMO_TASK if args.demo else args.task
    runs_path = DEMO_RUNS if args.demo else args.runs
    assert task_path is not None and runs_path is not None
    try:
        task = load_task(task_path)
        records = load_runs(runs_path, task)
    except ValueError as exc:
        parser.error(str(exc))
    summary = compare_runs(task, records)
    print(render_summary(summary))
    if args.output is not None:
        target = save_results(args.output, task, summary, records)
        print(f"\nwrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
