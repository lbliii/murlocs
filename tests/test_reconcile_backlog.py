"""Offline backlog reconciliation / drift checks (issue #208).

Derivation is pure and fixture-driven — these tests never call GitHub.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from murlocs.acceptance import collect_pytest_issue_tests
from murlocs.manifest import load_manifest
from murlocs.reconcile import (
    closeable_issue_numbers,
    derive_decided_but_unbuilt,
    derive_reconciliation_findings,
    derive_workability_findings,
    extract_closing_issues,
    load_fixture,
    pr_issue_links,
    reconcile_backlog,
    render_report,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "reconcile" / "sample.json"
_SCRIPT = _REPO_ROOT / "scripts" / "reconcile_backlog.py"


def _issue(
    number: int,
    *,
    labels: tuple[str, ...] = (),
    parent: int | None = None,
    children: list[dict] | None = None,
    title: str | None = None,
    state: str = "OPEN",
    state_reason: str | None = None,
):
    return {
        "number": number,
        "title": title or f"Issue {number}",
        "state": state,
        "stateReason": state_reason,
        "labels": list(labels),
        "parent": {"number": parent} if parent is not None else None,
        "subIssues": children or [],
    }


def _child(number: int, *, state: str = "OPEN", labels: tuple[str, ...] = (), reason=None):
    return {
        "number": number,
        "title": f"Issue {number}",
        "state": state,
        "stateReason": reason,
        "labels": list(labels),
    }


def test_pr_issue_links_and_closing_keyword_stub():
    pr = {
        "number": 99,
        "title": "Land it (#50)",
        "body": "Closes #50\nFixes #51\nAlso touches #999.",
        "headRefName": "issue-50-reconcile",
    }
    closing, mentioned = pr_issue_links(pr)
    assert closing == {50, 51}
    assert mentioned == {999}
    assert extract_closing_issues(pr["body"]) == {50, 51}
    assert extract_closing_issues("This does not close #300.") == set()


def test_merged_pr_with_passing_anchor_is_closeable():
    open_issues = [_issue(50, labels=("P2", "ready"), title="Ship reconcile")]
    merged_prs = [
        {
            "number": 99,
            "title": "Land reconcile",
            "body": "Closes #50",
            "headRefName": "cursor/reconcile-50",
        }
    ]
    findings = derive_reconciliation_findings(
        open_issues,
        merged_prs,
        coverage={50: ["tests/test_x.py::test_y"]},
        anchor_results={50: "pass"},
    )
    assert len(findings) == 1
    assert findings[0].kind == "closeable"
    assert "closeable" in findings[0].add_labels
    assert findings[0].anchor_status == "pass"

    report = reconcile_backlog(
        issues=open_issues,
        merged_prs=merged_prs,
        coverage={50: ["tests/test_x.py::test_y"]},
        anchor_results={50: "pass"},
    )
    assert closeable_issue_numbers(report) == (50,)
    assert "Closeable" in render_report(report)


def test_merged_pr_without_passing_anchor_is_pending_not_closeable():
    issues = [_issue(50, labels=("P2",))]
    merged_prs = [{"number": 1, "title": "x", "body": "Closes #50", "headRefName": "b"}]
    report = reconcile_backlog(
        issues=issues,
        merged_prs=merged_prs,
        coverage={50: ["tests/test_x.py::test_y"]},
        anchor_results={50: "fail"},
    )
    assert report.closeable == ()
    assert len(report.pending_close) == 1
    assert report.pending_close[0].kind == "merged-pending-close"


def test_parent_with_all_completed_children_is_closure_candidate():
    issues = [
        _issue(
            1,
            labels=("epic",),
            children=[_child(2, state="CLOSED", reason="COMPLETED")],
        )
    ]
    findings = derive_workability_findings(issues)
    assert findings[0].codes == ("closure-candidate",)
    assert findings[0].add_labels == ("closure-candidate",)

    report = reconcile_backlog(issues=issues, merged_prs=[])
    assert len(report.closure_candidates) == 1
    assert report.closure_candidates[0].number == 1


def test_decided_but_unbuilt_via_native_subissues():
    issues = [
        _issue(
            10,
            labels=("epic",),
            children=[
                _child(20, state="CLOSED", labels=("rfc", "decision"), reason="COMPLETED"),
                _child(21, state="OPEN", labels=("implementation", "ready")),
            ],
        ),
        _issue(
            20,
            labels=("rfc", "decision"),
            parent=10,
            title="RFC: decide",
            state="CLOSED",
            state_reason="COMPLETED",
        ),
        _issue(
            21,
            labels=("implementation", "ready"),
            parent=10,
            title="Implement decision",
        ),
    ]
    findings = derive_decided_but_unbuilt(issues)
    assert len(findings) == 1
    assert findings[0].number == 21
    assert findings[0].kind == "decided-but-unbuilt"
    assert findings[0].related == (20,)
    assert "decided-but-unbuilt" in findings[0].add_labels


def test_decided_but_unbuilt_parent_fallback_without_subissues_api():
    """Fixture-friendly offline model: siblings linked only via parent number."""
    issues = [
        _issue(
            20,
            labels=("decision",),
            parent=7,
            title="Decision record",
            state="CLOSED",
            state_reason="COMPLETED",
        ),
        _issue(
            21,
            labels=("implementation",),
            parent=7,
            title="Impl the decision",
        ),
    ]
    findings = derive_decided_but_unbuilt(issues)
    assert [item.number for item in findings] == [21]
    assert findings[0].related == (20,)


def test_sample_fixture_covers_closeable_and_decided_but_unbuilt():
    fixture = load_fixture(json.loads(_FIXTURE.read_text(encoding="utf-8")))
    report = reconcile_backlog(
        issues=fixture.issues,
        merged_prs=fixture.merged_prs,
        coverage=fixture.coverage,
        anchor_results=fixture.anchor_results,
    )
    assert closeable_issue_numbers(report) == (50,)
    assert {item.number for item in report.decided_but_unbuilt} == {21}
    assert {item.number for item in report.closure_candidates} == {40}


def test_script_fixture_mode_is_offline_and_reports_json():
    result = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--fixture",
            str(_FIXTURE),
            "--json",
            "--repo-root",
            str(_REPO_ROOT),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert [item["number"] for item in payload["closeable"]] == [50]
    assert [item["number"] for item in payload["decided_but_unbuilt"]] == [21]


def test_script_fixture_rejects_mutating_flags():
    result = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--fixture",
            str(_FIXTURE),
            "--auto-close",
            "--repo-root",
            str(_REPO_ROOT),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )
    assert result.returncode == 2
    assert "read-only" in result.stderr.lower()


@pytest.mark.issue(208)
def test_dogfood_reconcile_for_issue_208():
    """Executable proof that issue #208 reconciliation drift checks are wired."""
    fixture = load_fixture(json.loads(_FIXTURE.read_text(encoding="utf-8")))
    report = reconcile_backlog(
        issues=fixture.issues,
        merged_prs=fixture.merged_prs,
        coverage=fixture.coverage,
        anchor_results=fixture.anchor_results,
    )
    assert report.closeable and report.decided_but_unbuilt

    discovered = collect_pytest_issue_tests(_REPO_ROOT, ("tests",))
    assert "issue(208)" in discovered
    assert any("test_reconcile_backlog.py" in item.location for item in discovered["issue(208)"])

    # Manifest work item (when present) must resolve offline.
    manifest = load_manifest(_REPO_ROOT)
    work_ids = {item.id for item in manifest.work_items}
    assert "208" in work_ids
