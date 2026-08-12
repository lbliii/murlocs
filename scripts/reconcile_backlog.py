"""Re-derive backlog truth from git/GitHub (or fixtures) and surface drift.

Ports Chirp's reconciliation sweep into Murlocs' backlog-truth model. See
``docs/backlog-truth.md``. Pure derivation lives in ``murlocs.reconcile``;
this script is the CLI + optional GitHub I/O.

Usage::

    # Offline fixture (tests / local dry-run — no network)
    python scripts/reconcile_backlog.py --fixture tests/fixtures/reconcile/sample.json

    # Live report (requires ``gh`` + token)
    python scripts/reconcile_backlog.py --report-workability

    # Opt-in label apply / auto-close (auto-close only for closeable findings)
    python scripts/reconcile_backlog.py --apply
    python scripts/reconcile_backlog.py --auto-close
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from murlocs.acceptance import collect_pytest_issue_tests  # noqa: E402
from murlocs.reconcile import (  # noqa: E402
    DERIVED_LABELS,
    Finding,
    ReconcileReport,
    closeable_issue_numbers,
    label_name,
    load_fixture,
    reconcile_backlog,
    render_report,
)

# --------------------------------------------------------------------------- #
# I/O helpers (the only part that touches the network / gh).
# --------------------------------------------------------------------------- #


def _gh_json(args: list[str]) -> Any:
    out = subprocess.run(
        ["gh", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if out.returncode:
        detail = out.stderr.strip() or out.stdout.strip() or "no diagnostic output"
        raise RuntimeError(f"gh {' '.join(args[:2])} failed: {detail}")
    return json.loads(out.stdout or "[]")


_ISSUES_QUERY = """
query BacklogIssues($owner:String!, $name:String!, $cursor:String) {
  repository(owner:$owner, name:$name) {
    issues(
      first:100, after:$cursor, states:[OPEN, CLOSED],
      orderBy:{field:UPDATED_AT,direction:DESC}
    ) {
      nodes {
        id databaseId number title body url state stateReason createdAt updatedAt
        labels(first:50) { nodes { name } }
        parent { number }
        subIssues(first:100) {
          nodes { number title state stateReason labels(first:20) { nodes { name } } }
        }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""


def _repo_name() -> str:
    value = _gh_json(["repo", "view", "--json", "nameWithOwner"])
    if not isinstance(value, dict) or not isinstance(value.get("nameWithOwner"), str):
        raise TypeError("gh repo view returned no nameWithOwner")
    return cast(str, value["nameWithOwner"])


def _normalize_issue(node: dict) -> dict:
    return {
        **{
            key: node.get(key)
            for key in (
                "id",
                "databaseId",
                "number",
                "title",
                "body",
                "url",
                "state",
                "stateReason",
                "createdAt",
                "updatedAt",
            )
        },
        "labels": [item["name"] for item in node.get("labels", {}).get("nodes", [])],
        "parent": node.get("parent"),
        "subIssues": [
            {
                **{key: child.get(key) for key in ("number", "title", "state", "stateReason")},
                "labels": [item["name"] for item in child.get("labels", {}).get("nodes", [])],
            }
            for child in node.get("subIssues", {}).get("nodes", [])
        ],
    }


def _fetch_blockers(repository: str, number: int) -> list[dict]:
    value = _gh_json(
        [
            "api",
            f"repos/{repository}/issues/{number}/dependencies/blocked_by?per_page=100",
            "--paginate",
            "--slurp",
        ]
    )
    pages = value if isinstance(value, list) else []
    rows = (
        [item for page in pages for item in page]
        if pages and all(isinstance(page, list) for page in pages)
        else pages
    )
    return [{"number": row["number"], "title": row["title"], "state": row["state"]} for row in rows]


def fetch_issues(
    limit: int = 1000,
    repository: str | None = None,
    *,
    include_dependencies: bool = False,
) -> list[dict]:
    """Fetch recent open+closed issues with native sub-issue edges via GraphQL."""
    repository = repository or _repo_name()
    owner, name = repository.split("/", 1)
    cursor: str | None = None
    issues: list[dict] = []
    while len(issues) < limit:
        args = [
            "api",
            "graphql",
            "-f",
            f"query={_ISSUES_QUERY}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
        ]
        if cursor is not None:
            args += ["-F", f"cursor={cursor}"]
        payload = _gh_json(args)
        if not isinstance(payload, dict):
            raise TypeError("GitHub GraphQL returned a non-object response")
        if payload.get("errors"):
            raise RuntimeError(f"GitHub GraphQL errors: {payload['errors']}")
        connection = payload["data"]["repository"]["issues"]
        issues.extend(_normalize_issue(node) for node in connection["nodes"])
        page = connection["pageInfo"]
        if not page["hasNextPage"]:
            break
        cursor = page["endCursor"]
        if not cursor:
            raise RuntimeError("GitHub reported another issue page without an end cursor")
    issues = issues[:limit]
    if include_dependencies:
        blocked = frozenset({"blocked", "upstream-blocked", "ready"})
        for issue in issues:
            labels = {label_name(label) for label in issue.get("labels", [])}
            issue["blockedBy"] = (
                _fetch_blockers(repository, issue["number"]) if labels & blocked else []
            )
    return issues


def fetch_merged_prs(limit: int) -> list[dict]:
    value = _gh_json(
        [
            "pr",
            "list",
            "--state",
            "merged",
            "--limit",
            str(limit),
            "--json",
            "number,title,body,headRefName",
        ]
    )
    if not isinstance(value, list):
        raise TypeError("gh pr list returned a non-list response")
    return cast(list[dict], value)


def ensure_labels() -> None:
    for name, (color, desc) in DERIVED_LABELS.items():
        subprocess.run(
            ["gh", "label", "create", name, "--color", color, "--description", desc, "--force"],
            check=True,
            capture_output=True,
            text=True,
        )


def apply_labels(findings: list[Finding]) -> int:
    applied = 0
    for finding in findings:
        if not finding.add_labels and not finding.remove_labels:
            continue
        cmd = ["gh", "issue", "edit", str(finding.number)]
        for label in finding.add_labels:
            cmd += ["--add-label", label]
        for label in finding.remove_labels:
            cmd += ["--remove-label", label]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        applied += 1
    return applied


def auto_close_issues(numbers: list[int], *, comment: str) -> int:
    """Close issues that are already gated as closeable by the caller."""
    closed = 0
    for number in numbers:
        subprocess.run(
            [
                "gh",
                "issue",
                "close",
                str(number),
                "--reason",
                "completed",
                "--comment",
                comment,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        closed += 1
    return closed


def _coverage_map(root: Path) -> dict[int, list[str]]:
    discovered = collect_pytest_issue_tests(root, ("tests", "examples"))
    return {
        int(reference.removeprefix("issue(").removesuffix(")")): [
            location.location for location in locations
        ]
        for reference, locations in discovered.items()
    }


def _load_anchor_results(path: str | None) -> dict[int, str]:
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("anchor results must be a JSON object of issue -> status")
    return {int(key): str(value) for key, value in payload.items()}


def build_report_from_fixture(
    fixture_path: Path,
    *,
    repo_root: Path,
    anchor_results_path: str | None = None,
) -> tuple[ReconcileReport, list[dict]]:
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    fixture = load_fixture(data)
    coverage = dict(fixture.coverage) or _coverage_map(repo_root)
    anchors = dict(fixture.anchor_results)
    anchors.update(_load_anchor_results(anchor_results_path))
    report = reconcile_backlog(
        issues=fixture.issues,
        merged_prs=fixture.merged_prs,
        coverage=coverage,
        anchor_results=anchors,  # type: ignore[arg-type]
    )
    return report, list(fixture.issues)


def build_report_from_github(
    *,
    repo_root: Path,
    limit: int,
    include_dependencies: bool,
    anchor_results_path: str | None = None,
) -> tuple[ReconcileReport, list[dict]]:
    issues = fetch_issues(limit, include_dependencies=include_dependencies)
    merged_prs = fetch_merged_prs(limit)
    coverage = _coverage_map(repo_root)
    anchors = _load_anchor_results(anchor_results_path)
    report = reconcile_backlog(
        issues=issues,
        merged_prs=merged_prs,
        coverage=coverage,
        anchor_results=anchors,  # type: ignore[arg-type]
    )
    return report, issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        help="offline JSON backlog graph (skips GitHub; required for hermetic runs)",
    )
    parser.add_argument(
        "--anchor-results",
        help="JSON object mapping issue number -> pass|fail|missing",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="ensure + attach derived labels (mutates GitHub; ignored with --fixture)",
    )
    parser.add_argument(
        "--auto-close",
        action="store_true",
        help="close issues classified as closeable (requires passing anchors; no fixture)",
    )
    parser.add_argument("--limit", type=int, default=1000, help="max issues/PRs to scan")
    parser.add_argument("--json", action="store_true", help="emit findings as JSON")
    parser.add_argument("--snapshot", help="write the normalized issue graph as JSON")
    parser.add_argument(
        "--report-workability",
        action="store_true",
        help="include hierarchy/work-state findings in the Markdown report",
    )
    parser.add_argument(
        "--with-dependencies",
        action="store_true",
        help="include native blocked-by relationships (live mode only)",
    )
    parser.add_argument(
        "--repo-root",
        default=str(_REPO_ROOT),
        help="repository root for acceptance-test discovery",
    )
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root)

    if args.fixture:
        report, issues = build_report_from_fixture(
            Path(args.fixture),
            repo_root=repo_root,
            anchor_results_path=args.anchor_results,
        )
    else:
        report, issues = build_report_from_github(
            repo_root=repo_root,
            limit=args.limit,
            include_dependencies=args.with_dependencies,
            anchor_results_path=args.anchor_results,
        )

    if args.snapshot:
        Path(args.snapshot).write_text(json.dumps(issues, indent=2) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        text = render_report(report)
        if not args.report_workability:
            marker = "\n# Backlog workability\n"
            if marker in text:
                text = text.split(marker, 1)[0].rstrip() + "\n"
        print(text)

    if args.fixture and (args.apply or args.auto_close):
        print(
            "\n_Fixture mode is read-only: --apply / --auto-close require live GitHub access._",
            file=sys.stderr,
        )
        return 2

    if args.apply:
        ensure_labels()
        findings = [
            *report.reconciliation,
            *report.workability,
            *report.decided_but_unbuilt,
        ]
        applied = apply_labels(findings)
        print(f"\n_Applied derived labels to {applied} issue(s)._")

    if args.auto_close:
        numbers = list(closeable_issue_numbers(report))
        if not numbers:
            print("\n_No closeable issues (passing acceptance required)._")
        else:
            closed = auto_close_issues(
                numbers,
                comment=(
                    "Closed by backlog reconciliation: merged PR closing intent plus "
                    "passing acceptance anchor. See docs/backlog-truth.md."
                ),
            )
            print(f"\n_Auto-closed {closed} closeable issue(s)._")
    return 0


if __name__ == "__main__":
    sys.exit(main())
