#!/usr/bin/env python3
"""Fail a PR that claims to close an issue without acceptance proof.

Stdlib-only stub shipped by ``murlocs scaffold backlog-truth``. A PR body that
says ``Closes #N`` / ``Fixes #N`` / ``Resolves #N`` must either:

1. Include an ``@pytest.mark.issue(N)`` test in the tree, or
2. Declare ``Acceptance #N: n/a (reason)`` in the PR body.

Full engine parity with Murlocs check surfaces lands with backlog-truth closure
work (#207). This stub keeps day-one workflow wiring valid and enforceable.

Usage::

    PR_BODY="$PR_BODY" python scripts/check_closure_acceptance.py
    python scripts/check_closure_acceptance.py --body-file pr_body.txt
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import sys
from pathlib import Path

_CLOSING = re.compile(
    r"^\s*(?:[-*]\s*)?(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)\b",
    re.IGNORECASE | re.MULTILINE,
)
_EXEMPT = re.compile(
    r"^\s*acceptance\s+#(\d+)\s*:\s*(?:n/?a|none|not applicable)\s*\(([^)]+)\)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def extract_closing_issues(body: str) -> set[int]:
    return {int(match) for match in _CLOSING.findall(body or "")}


def extract_exemptions(body: str) -> dict[int, str]:
    return {
        int(issue): reason.strip()
        for issue, reason in _EXEMPT.findall(body or "")
        if reason.strip()
    }


def collect_issue_markers(root: Path) -> set[int]:
    """Discover ``@pytest.mark.issue(N)`` markers under common test roots."""
    found: set[int] = set()
    roots = [root / name for name in ("tests", "test", "src") if (root / name).is_dir()]
    if not roots:
        roots = [root]
    for base in roots:
        for path in base.rglob("test_*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not (
                    isinstance(func, ast.Attribute)
                    and func.attr == "issue"
                    and isinstance(func.value, ast.Attribute)
                    and func.value.attr == "mark"
                ):
                    continue
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, int):
                        found.add(arg.value)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--body", default=None, help="PR body text")
    parser.add_argument("--body-file", type=Path, default=None, help="File containing PR body")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Repository root to scan for acceptance markers",
    )
    args = parser.parse_args(argv)

    if args.body is not None:
        body = args.body
    elif args.body_file is not None:
        body = args.body_file.read_text(encoding="utf-8")
    else:
        body = os.environ.get("PR_BODY", "")
        if not body and not sys.stdin.isatty():
            body = sys.stdin.read()

    claimed = extract_closing_issues(body)
    if not claimed:
        print("closure-gate: no Closes/Fixes/Resolves claims; ok")
        return 0

    exemptions = extract_exemptions(body)
    markers = collect_issue_markers(args.root)
    missing = sorted(
        issue for issue in claimed if issue not in markers and issue not in exemptions
    )
    if missing:
        print(
            "closure-gate: PR claims to close issues without acceptance proof: "
            + ", ".join(f"#{issue}" for issue in missing)
        )
        print(
            "Add @pytest.mark.issue(N) tests, or declare "
            "'Acceptance #N: n/a (reason)' per issue in the PR body."
        )
        return 1

    covered = sorted(claimed & markers)
    exempted = sorted(claimed & set(exemptions))
    parts: list[str] = []
    if covered:
        parts.append("anchored " + ", ".join(f"#{issue}" for issue in covered))
    if exempted:
        parts.append("exempted " + ", ".join(f"#{issue}" for issue in exempted))
    print("closure-gate: ok (" + "; ".join(parts) + ")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
