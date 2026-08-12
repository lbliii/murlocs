#!/usr/bin/env python3
"""Read-only backlog reconcile stub stamped by ``murlocs scaffold backlog-truth``.

Reports that the kit wiring is present. Full derivation of closeable issues,
parent completion, and decided-but-unbuilt residue lands with #208. Keep this
script stdlib-only so the workflow stub stays valid without a hosted service.

Usage::

    python scripts/reconcile_backlog.py --report
    python scripts/reconcile_backlog.py --report --apply   # refused by stub
"""

from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true", help="Print a Markdown report")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply derived labels (not supported by this stub)",
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)

    root = args.root.resolve()
    lines = [
        "# Backlog reconciliation (stub)",
        "",
        f"Repository: `{root}`",
        "",
        "This is the day-one reconcile stub from `murlocs scaffold backlog-truth`.",
        "It confirms workflow wiring only. Full derive/apply behaviour lands with",
        "backlog-truth reconcile work (#208).",
        "",
        "## Status",
        "",
        "- Mode: read-only stub",
        "- Closeable-from-anchor detection: pending #208",
        "- Parent-completion detection: pending #208",
        "- Decided-but-unbuilt detection: pending #208",
        "",
    ]
    report = "\n".join(lines)
    if args.report or not args.apply:
        print(report)
    if args.apply:
        print(
            "reconcile-stub: --apply refused; label updates require the full "
            "reconcile engine (#208).",
            flush=True,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
