"""Map ``@pytest.mark.issue(N)`` markers to the tests that carry them.

This is the offline half of backlog-truth acceptance discovery (see
``docs/backlog-truth.md``). It answers: *which work items have an executable
acceptance test, and which do not?*

Design constraints:

- **Stdlib only through Murlocs, no network, returns 0/1** so it runs anywhere.
- **AST-based, not regex** — understands function, class, and module-level
  (``pytestmark``) markers and ignores markers inside strings/comments.

Usage::

    python scripts/issue_coverage.py                 # table of issue -> tests
    python scripts/issue_coverage.py --issue 206      # tests proving #206
    python scripts/issue_coverage.py --json           # machine-readable map
    python scripts/issue_coverage.py --untested 206   # exit 1 if any lack tests
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from murlocs.acceptance import collect_pytest_issue_tests  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue", type=int, help="show tests proving a single issue")
    parser.add_argument("--json", action="store_true", help="emit the full map as JSON")
    parser.add_argument(
        "--untested",
        type=int,
        nargs="+",
        metavar="N",
        help="exit 1 if any listed issue has no acceptance test",
    )
    args = parser.parse_args(argv)

    discovered = collect_pytest_issue_tests(_REPO_ROOT, ("tests", "examples"))
    coverage = {
        int(reference.removeprefix("issue(").removesuffix(")")): [
            location.location for location in locations
        ]
        for reference, locations in discovered.items()
    }

    if args.untested is not None:
        missing = [number for number in args.untested if number not in coverage]
        if missing:
            print(
                "Issues without a @pytest.mark.issue acceptance test: "
                + ", ".join(f"#{number}" for number in missing)
            )
            return 1
        print("All listed issues have at least one acceptance test.")
        return 0

    if args.json:
        print(json.dumps(coverage, indent=2))
        return 0

    if args.issue is not None:
        locations = coverage.get(args.issue, [])
        if not locations:
            print(f"#{args.issue}: no acceptance test (@pytest.mark.issue({args.issue})) found.")
            return 0
        print(f"#{args.issue}: {len(locations)} acceptance test(s)")
        for location in locations:
            print(f"  {location}")
        return 0

    if not coverage:
        print("No @pytest.mark.issue(...) markers found yet. See docs/backlog-truth.md.")
        return 0
    print(f"{len(coverage)} issue(s) with acceptance tests:")
    for issue, locations in sorted(coverage.items()):
        print(f"  #{issue}: {len(locations)} test(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
