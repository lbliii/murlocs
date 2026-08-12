"""Fail when a PR claims Closes/Fixes/Resolves #N without an acceptance anchor.

This is the merge-time half of backlog-truth (see ``docs/backlog-truth.md``).
Presence of ``Closes #N`` (or Fixes/Resolves) in a pull-request body is only
allowed when:

1. an offline acceptance anchor exists (today: ``@pytest.mark.issue(N)``), or
2. the body declares ``Acceptance #N: n/a (reason)``.

Design constraints:

- **Stdlib only through Murlocs, no network, returns 0/1** so CI and laptops
  can share one gate.
- **PR body is supplied out-of-band** (env, file, or stdin) — this script never
  calls the GitHub API.
- **Does not verify anchor strength** (see issue #209) and **does not
  auto-close** issues (see issue #208).

Usage::

    PR_BODY='Closes #207' python scripts/check_closure_acceptance.py
    python scripts/check_closure_acceptance.py --body-file /tmp/pr-body.md
    echo 'Closes #207' | python scripts/check_closure_acceptance.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from murlocs.acceptance import (  # noqa: E402
    evaluate_closure_acceptance,
    format_closure_report,
)


def _read_body(body_file: Path | None) -> str:
    if body_file is not None:
        return body_file.read_text(encoding="utf-8")
    for key in ("MURLOCS_PR_BODY", "PR_BODY"):
        if key in os.environ:
            return os.environ[key]
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit(
        "error: provide --body-file, MURLOCS_PR_BODY/PR_BODY, or pipe the PR body on stdin"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--body-file",
        type=Path,
        help="read the pull-request body from this path",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=_REPO_ROOT,
        help="repository root used for offline acceptance discovery",
    )
    parser.add_argument(
        "--test-root",
        action="append",
        dest="test_roots",
        metavar="DIR",
        help="test root relative to --repo (repeatable; default: tests, examples)",
    )
    args = parser.parse_args(argv)

    try:
        body = _read_body(args.body_file)
    except OSError as exc:
        print(f"error: could not read PR body: {exc}", file=sys.stderr)
        return 2

    test_roots = tuple(args.test_roots) if args.test_roots else None
    verdict = evaluate_closure_acceptance(body, args.repo.resolve(), test_roots)
    report = format_closure_report(verdict)
    stream = sys.stderr if verdict.missing else sys.stdout
    print(report, file=stream)
    return 1 if verdict.missing else 0


if __name__ == "__main__":
    sys.exit(main())
