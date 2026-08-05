#!/usr/bin/env python
"""Fail when `ty` reports more diagnostics than the recorded baseline.

Murlocs was not written against a type checker, so `ty check src/murlocs`
currently reports a backlog. Gating on zero would either block every change or
force a large speculative refactor, and turning the check off entirely would let
the backlog grow silently.

The ratchet takes the middle path: the current count is checked in, new
diagnostics fail, and removing diagnostics without lowering the baseline also
fails so the recorded number cannot drift away from reality. When the baseline
reaches zero, delete this script and gate on `ty check` directly.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

BASELINE_PATH = Path(__file__).parent / "ty_baseline.json"
TARGET = "src/murlocs"
SUMMARY = re.compile(r"^Found (\d+) diagnostic", re.MULTILINE)


def current_diagnostics() -> int:
    """Return the diagnostic count `ty` reports for the checked target."""
    result = subprocess.run(
        [sys.executable, "-m", "ty", "check", TARGET],
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    if "All checks passed" in output:
        return 0
    match = SUMMARY.search(output)
    if match is None:
        sys.stderr.write(
            "ty produced no diagnostic summary; the ratchet cannot compare a count.\n"
            f"exit status {result.returncode}\n{output}"
        )
        raise SystemExit(2)
    return int(match.group(1))


def main() -> int:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    allowed = int(baseline["diagnostics"])
    actual = current_diagnostics()

    if actual > allowed:
        print(
            f"ty reports {actual} diagnostics but the baseline allows {allowed}.\n"
            f"Fix the new diagnostics, or explain the increase and raise the baseline in "
            f"{BASELINE_PATH.name}.",
            file=sys.stderr,
        )
        return 1

    if actual < allowed:
        print(
            f"ty reports {actual} diagnostics, below the baseline of {allowed}. "
            f"Lower `diagnostics` to {actual} in {BASELINE_PATH.name} to lock in the improvement.",
            file=sys.stderr,
        )
        return 1

    print(f"ty diagnostics holding at the baseline of {allowed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
