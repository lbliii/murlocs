"""Verify that an acceptance anchor fails when changed code is reverted.

Coarse strength check for backlog-truth issue #209: presence of
``@pytest.mark.issue(N)`` is necessary but not sufficient. On a ``Closes #N``
change, the linked tests must pass on the clean tree and fail after the
implementation paths are restored to their baseline (pre-change) contents.

Design constraints:

- **Deterministic revert** via explicit path snapshots (or git ``BASE:path``).
- **Scoped to acceptance anchors** — only ``issue(N)`` tests run, never the
  whole suite.
- **Offline-friendly library core** in ``murlocs.acceptance``; this script is
  the PR-facing CLI wrapper.

Usage::

    python scripts/check_acceptance_strength.py --issue 209 \\
        --baseline path/to/impl.py:path/to/old_impl.py
    python scripts/check_acceptance_strength.py --issue 209 \\
        --git-base origin/main --path src/murlocs/acceptance.py
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from murlocs.acceptance import (  # noqa: E402
    format_strength_report,
    snapshot_paths,
    verify_acceptance_strength,
)


def _parse_baseline_pair(value: str) -> tuple[str, Path]:
    """Parse ``repo/rel/path:filesystem/baseline`` into a snapshot pair."""
    if ":" not in value:
        raise argparse.ArgumentTypeError(
            "baseline must be repo-relative:baseline-file, for example "
            "src/pkg.py:/tmp/pkg.py.base"
        )
    relative, baseline = value.split(":", 1)
    relative = relative.strip()
    baseline_path = Path(baseline.strip())
    if not relative:
        raise argparse.ArgumentTypeError("baseline repo-relative path must not be empty")
    if not baseline_path.is_file():
        raise argparse.ArgumentTypeError(f"baseline file not found: {baseline_path}")
    return relative, baseline_path


def _git_show(root: Path, revision: str, relative: str) -> str | None:
    """Return file contents at ``revision:relative``, or ``None`` if absent."""
    completed = subprocess.run(
        ["git", "show", f"{revision}:{relative}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue", type=int, required=True, help="issue number to strength-check")
    parser.add_argument(
        "--repo",
        type=Path,
        default=_REPO_ROOT,
        help="repository root (default: this checkout)",
    )
    parser.add_argument(
        "--baseline",
        action="append",
        default=[],
        metavar="REL:FILE",
        type=_parse_baseline_pair,
        help="repo-relative path paired with a baseline content file (repeatable)",
    )
    parser.add_argument(
        "--git-base",
        help="git revision whose file contents are the revert baseline",
    )
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        dest="paths",
        metavar="REL",
        help="repo-relative path to revert against --git-base (repeatable)",
    )
    parser.add_argument(
        "--absent",
        action="append",
        default=[],
        metavar="REL",
        help="path that must be deleted during the mutation (did not exist at baseline)",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    root = args.repo.resolve()
    snapshots: dict[str, str | None] = {}

    for relative, baseline_path in args.baseline:
        snapshots[relative] = baseline_path.read_text(encoding="utf-8")

    for relative in args.absent:
        snapshots[relative] = None

    if args.git_base:
        if not args.paths and not args.absent:
            parser.error("--git-base requires at least one --path or --absent")
        for relative in args.paths:
            snapshots[relative] = _git_show(root, args.git_base, relative)
    elif args.paths:
        parser.error("--path requires --git-base (or pass contents via --baseline)")

    if not snapshots:
        parser.error("provide --baseline, --git-base/--path, and/or --absent")

    # Refuse to mutate when the clean tree already matches the baseline for every
    # path: that cannot distinguish a faithful test from a tautology.
    current = snapshot_paths(root, tuple(snapshots))
    if current == snapshots:
        print(
            "Baseline snapshots match the working tree; nothing to revert. "
            "Pass pre-change contents (for example via --git-base).",
            file=sys.stderr,
        )
        return 2

    result = verify_acceptance_strength(root, args.issue, baseline_snapshots=snapshots)
    if args.json:
        print(
            json.dumps(
                {
                    "issue": result.issue,
                    "strong": result.strong,
                    "clean_passed": result.clean_passed,
                    "mutated_failed": result.mutated_failed,
                    "locations": list(result.locations),
                    "mutated_paths": list(result.mutated_paths),
                    "message": result.message,
                },
                indent=2,
            )
        )
    else:
        print(format_strength_report(result))
    return 0 if result.strong else 1


if __name__ == "__main__":
    sys.exit(main())
