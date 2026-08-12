"""Re-derive backlog truth and surface drift between tracker state and reality.

This is Murlocs' drift model applied to work items: declared issue state vs
actual signals from merged PRs, native sub-issue hierarchy, and acceptance
anchors. Pure derivation lives here so fixtures and tests never need the
network; GitHub I/O stays in ``scripts/reconcile_backlog.py``.

Surfaced divergence classes:

- **closeable** — an open issue a merged PR closes, and whose acceptance
  anchor passes (existence alone is not enough for auto-close).
- **closure-candidate** — a parent whose native children are all completed.
- **decided-but-unbuilt** — a decision/RFC issue closed while an
  implementation sibling under the same parent remains open.

Read-only by default. Auto-close is an opt-in caller action gated on
``closeable`` findings only.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

AnchorStatus = Literal["pass", "fail", "missing"]

_CLOSING = re.compile(
    r"^\s*(?:[-*]\s*)?(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)\b",
    re.IGNORECASE | re.MULTILINE,
)
_MENTION = re.compile(r"(?<![/\w-])#(\d+)")
_BRANCH_ISSUE = re.compile(r"\bissue[-_/](\d+)\b", re.IGNORECASE)
_ADVANCES = re.compile(r"\badvances-epic\s*:\s*#(\d+)", re.IGNORECASE)

_PARENT_LABELS = frozenset({"saga", "epic", "implementation-epic"})
_DECISION_LABELS = frozenset({"decision", "rfc", "research"})
_IMPL_LABELS = frozenset({"implementation", "implementation-epic"})
_BLOCKED_LABELS = frozenset({"blocked", "upstream-blocked"})

DERIVED_LABELS: dict[str, tuple[str, str]] = {
    "closeable": ("0e8a16", "Merged PR closes this; acceptance anchor passes — verify & close"),
    "merged-pending-close": (
        "c2e0c6",
        "Merged PR closes this; acceptance not yet passing — do not auto-close",
    ),
    "stale-epic-review": ("fbca04", "Epic referenced by merged work; re-check children / close"),
    "acceptance-tracked": ("1d76db", "Has a discoverable acceptance anchor"),
    "closure-candidate": ("0e8a16", "All native child issues completed; verify parent gates"),
    "decided-but-unbuilt": (
        "d93f0b",
        "Decision/RFC closed but implementation sibling remains open",
    ),
    "needs-grooming": ("d93f0b", "Backlog workability or hierarchy needs maintainer review"),
    "needs-decomposition": ("fbca04", "Unblocked parent has no native child issues"),
}

_RECONCILIATION_LABELS = frozenset(
    {
        "closeable",
        "merged-pending-close",
        "stale-epic-review",
        "acceptance-tracked",
        "decided-but-unbuilt",
    }
)
_WORKABILITY_LABELS = frozenset({"closure-candidate", "needs-decomposition", "needs-grooming"})


@dataclass(frozen=True)
class Finding:
    """One derived drift signal for a single issue."""

    number: int
    title: str
    kind: str
    reasons: tuple[str, ...]
    add_labels: tuple[str, ...] = ()
    remove_labels: tuple[str, ...] = ()
    closed_by: tuple[int, ...] = ()
    mentioned_by: tuple[int, ...] = ()
    related: tuple[int, ...] = ()
    codes: tuple[str, ...] = ()
    is_epic: bool = False
    has_tests: bool = False
    anchor_status: AnchorStatus | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "kind": self.kind,
            "reasons": list(self.reasons),
            "add_labels": list(self.add_labels),
            "remove_labels": list(self.remove_labels),
            "closed_by": list(self.closed_by),
            "mentioned_by": list(self.mentioned_by),
            "related": list(self.related),
            "codes": list(self.codes),
            "is_epic": self.is_epic,
            "has_tests": self.has_tests,
            "anchor_status": self.anchor_status,
        }


@dataclass(frozen=True)
class ReconcileReport:
    """Full offline reconciliation result."""

    closeable: tuple[Finding, ...] = ()
    pending_close: tuple[Finding, ...] = ()
    closure_candidates: tuple[Finding, ...] = ()
    decided_but_unbuilt: tuple[Finding, ...] = ()
    reconciliation: tuple[Finding, ...] = ()
    workability: tuple[Finding, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "closeable": [item.as_dict() for item in self.closeable],
            "pending_close": [item.as_dict() for item in self.pending_close],
            "closure_candidates": [item.as_dict() for item in self.closure_candidates],
            "decided_but_unbuilt": [item.as_dict() for item in self.decided_but_unbuilt],
            "reconciliation": [item.as_dict() for item in self.reconciliation],
            "workability": [item.as_dict() for item in self.workability],
        }


@dataclass(frozen=True)
class BacklogFixture:
    """Offline backlog graph used by tests and ``--fixture`` runs."""

    issues: tuple[dict[str, Any], ...] = ()
    merged_prs: tuple[dict[str, Any], ...] = ()
    anchor_results: Mapping[int, AnchorStatus] = field(default_factory=dict)
    coverage: Mapping[int, Sequence[str]] = field(default_factory=dict)


def label_name(label: object) -> str:
    """Normalize a gh label (dict ``{name}`` or plain string) to its name."""
    if isinstance(label, dict):
        return str(label.get("name", ""))
    return str(label)


def issue_labels(issue: Mapping[str, Any]) -> set[str]:
    return {label_name(label) for label in issue.get("labels", [])}


def pr_issue_links(pr: Mapping[str, Any]) -> tuple[set[int], set[int]]:
    """Return ``(closing, mentioned)`` issue numbers a merged PR points at.

    ``closing`` contains only explicit GitHub closing-keyword references.
    ``mentioned`` contains bare references, ``Advances-Epic`` trailers, and the
    ``issue-<N>-`` branch convention. Associations are weak and never trigger a
    close recommendation on their own.
    """
    text = f"{pr.get('title', '')}\n{pr.get('body', '') or ''}"
    closing = {int(number) for number in _CLOSING.findall(text)}
    branch = pr.get("headRefName", "") or ""
    mentioned = {int(number) for number in _MENTION.findall(text)} - closing
    mentioned |= {int(number) for number in _ADVANCES.findall(text)}
    mentioned |= {int(number) for number in _BRANCH_ISSUE.findall(branch)}
    mentioned -= closing
    return closing, mentioned


def extract_closing_issues(body: str) -> set[int]:
    """Issue numbers a PR body declares it will close via a closing keyword."""
    return {int(number) for number in _CLOSING.findall(body or "")}


def normalize_anchor_results(
    raw: Mapping[str | int, str] | None,
    *,
    coverage: Mapping[int, Sequence[str]] | None = None,
) -> dict[int, AnchorStatus]:
    """Normalize fixture/CLI anchor statuses; fill missing coverage as ``missing``."""
    results: dict[int, AnchorStatus] = {}
    if raw:
        for key, value in raw.items():
            status = str(value).strip().lower()
            if status not in {"pass", "fail", "missing"}:
                raise ValueError(f"anchor status must be pass|fail|missing, got {value!r}")
            results[int(key)] = status  # type: ignore[assignment]
    if coverage:
        for number in coverage:
            results.setdefault(int(number), "missing")
    return results


def _is_open(issue: Mapping[str, Any]) -> bool:
    return str(issue.get("state", "OPEN")).upper() == "OPEN"


def _is_parent(issue: Mapping[str, Any]) -> bool:
    return bool(issue_labels(issue) & _PARENT_LABELS) or bool(issue.get("subIssues"))


def _open_children(issue: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [child for child in issue.get("subIssues", []) if _is_open(child)]


def _leaf_state(issue: Mapping[str, Any]) -> str:
    labels = issue_labels(issue)
    ready = "ready" in labels
    blocked = bool(labels & _BLOCKED_LABELS)
    open_blockers = [
        blocker
        for blocker in issue.get("blockedBy", [])
        if str(blocker.get("state", "OPEN")).upper() == "OPEN"
    ]
    if ready and (blocked or open_blockers):
        return "conflict"
    if ready:
        return "ready"
    if blocked:
        return "blocked"
    return "missing"


def _skip_contributor(issue: Mapping[str, Any]) -> bool:
    labels = issue_labels(issue)
    return "good first issue" in labels or str(issue.get("title", "")).startswith("[GF]")


def derive_reconciliation_findings(
    open_issues: Sequence[Mapping[str, Any]],
    merged_prs: Sequence[Mapping[str, Any]],
    *,
    coverage: Mapping[int, Sequence[str]] | None = None,
    anchor_results: Mapping[int, AnchorStatus] | None = None,
) -> list[Finding]:
    """Map open issues + merged PRs + acceptance status to reconciliation findings."""
    coverage = coverage or {}
    anchors = dict(anchor_results or {})
    closed_by: dict[int, list[int]] = {}
    mentioned_by: dict[int, list[int]] = {}
    for pr in merged_prs:
        closing, mentioned = pr_issue_links(pr)
        for number in closing:
            closed_by.setdefault(number, []).append(int(pr["number"]))
        for number in mentioned:
            mentioned_by.setdefault(number, []).append(int(pr["number"]))

    findings: list[Finding] = []
    for issue in open_issues:
        if _skip_contributor(issue):
            continue
        number = int(issue["number"])
        labels = issue_labels(issue)
        is_epic = bool(labels & _PARENT_LABELS)
        closes = tuple(sorted(set(closed_by.get(number, []))))
        mentions = tuple(sorted(set(mentioned_by.get(number, []))))
        has_tests = number in coverage
        status = anchors.get(number)
        if status is None and has_tests:
            status = "missing"

        desired: set[str] = set()
        reasons: list[str] = []
        kind = "observation"
        if closes:
            joined = ", ".join(f"#{pr}" for pr in closes)
            if is_epic:
                desired.add("stale-epic-review")
                kind = "stale-epic-review"
                reasons.append(
                    f"epic referenced by merged PR(s) {joined} — re-check children / close"
                )
            elif status == "pass":
                desired.add("closeable")
                kind = "closeable"
                reasons.append(
                    f"merged PR(s) {joined} close this and acceptance anchor passes — closeable"
                )
            else:
                desired.add("merged-pending-close")
                kind = "merged-pending-close"
                if status == "fail":
                    reasons.append(f"merged PR(s) {joined} close this but acceptance anchor failed")
                elif has_tests:
                    reasons.append(
                        f"merged PR(s) {joined} close this; acceptance test exists but is not "
                        "marked passing"
                    )
                else:
                    reasons.append(
                        f"merged PR(s) {joined} close this but no passing acceptance anchor"
                    )
        elif mentions and not is_epic:
            joined = ", ".join(f"#{pr}" for pr in mentions)
            reasons.append(f"mentioned by merged PR(s) {joined} (weak signal — review)")
        if has_tests:
            desired.add("acceptance-tracked")
            reasons.append(f"has {len(coverage[number])} acceptance test(s)")

        current_owned = labels & _RECONCILIATION_LABELS
        if "closeable" in desired:
            desired.discard("merged-pending-close")
        add = tuple(sorted(desired - current_owned))
        remove = tuple(sorted(current_owned - desired))
        if add or remove or reasons:
            findings.append(
                Finding(
                    number=number,
                    title=str(issue.get("title", "")),
                    kind=kind,
                    reasons=tuple(reasons),
                    add_labels=add,
                    remove_labels=remove,
                    closed_by=closes,
                    mentioned_by=mentions,
                    is_epic=is_epic,
                    has_tests=has_tests,
                    anchor_status=status,
                )
            )
    return findings


def derive_workability_findings(open_issues: Sequence[Mapping[str, Any]]) -> list[Finding]:
    """Return graph/work-state findings for the open non-contributor backlog."""
    by_number = {int(issue["number"]): issue for issue in open_issues}
    findings: list[Finding] = []

    def descendants(number: int, visiting: frozenset[int] = frozenset()) -> set[str]:
        if number in visiting:
            return {"conflict"}
        issue = by_number[number]
        children = _open_children(issue)
        if not children:
            return {_leaf_state(issue)}
        states: set[str] = set()
        for child in children:
            child_number = int(child["number"])
            if child_number in by_number:
                states |= descendants(child_number, visiting | {number})
            else:
                states.add(_leaf_state(child))
        return states

    for issue in open_issues:
        if _skip_contributor(issue):
            continue
        number = int(issue["number"])
        labels = issue_labels(issue)
        children = list(issue.get("subIssues", []))
        open_children = _open_children(issue)
        is_parent = _is_parent(issue)
        codes: list[str] = []
        reasons: list[str] = []

        if is_parent:
            if "ready" in labels:
                codes.append("ready-parent")
                reasons.append("ready is reserved for executable leaves")
            if not children and not (labels & _BLOCKED_LABELS):
                codes.append("parent-no-children")
                reasons.append("unblocked parent has no native sub-issues")
            elif children and not open_children:
                state_reasons = {str(child.get("stateReason") or "").upper() for child in children}
                if state_reasons <= {"COMPLETED", ""}:
                    codes.append("closure-candidate")
                    reasons.append("all native child issues are closed as completed")
                else:
                    codes.append("closed-not-planned-child")
                    reasons.append("all children are closed but at least one was not planned")
            elif open_children:
                states = descendants(number)
                if "ready" not in states and states != {"blocked"}:
                    codes.append("parent-no-workable-leaf")
                    reasons.append("no open descendant is ready and not every path is blocked")
        else:
            state = _leaf_state(issue)
            if state == "missing":
                codes.append("work-state-missing")
                reasons.append("leaf has neither ready nor blocked state")
            elif state == "conflict":
                codes.append("work-state-conflict")
                reasons.append("leaf is ready while a blocked state or open blocker remains")
            if "blocked" in labels and not issue.get("blockedBy"):
                codes.append("blocked-without-dependency")
                reasons.append("internally blocked leaf has no native blocked-by relationship")

        desired: set[str] = set()
        kind = "workability"
        if "closure-candidate" in codes:
            desired.add("closure-candidate")
            kind = "closure-candidate"
        if "parent-no-children" in codes:
            desired.add("needs-decomposition")
        if any(
            code
            in {
                "ready-parent",
                "parent-no-workable-leaf",
                "work-state-missing",
                "work-state-conflict",
                "blocked-without-dependency",
                "closed-not-planned-child",
            }
            for code in codes
        ):
            desired.add("needs-grooming")
        current = labels & _WORKABILITY_LABELS
        if codes or current:
            findings.append(
                Finding(
                    number=number,
                    title=str(issue.get("title", "")),
                    kind=kind if codes else "workability-resolved",
                    reasons=tuple(reasons)
                    if codes
                    else ("previous workability finding is now resolved",),
                    add_labels=tuple(sorted(desired - current)),
                    remove_labels=tuple(sorted(current - desired)),
                    codes=tuple(codes),
                )
            )
    return findings


def _is_decision_issue(issue: Mapping[str, Any]) -> bool:
    labels = issue_labels(issue)
    if labels & _DECISION_LABELS:
        return True
    title = str(issue.get("title", "")).lower()
    return title.startswith("rfc") or "decision" in title


def _is_implementation_issue(issue: Mapping[str, Any]) -> bool:
    labels = issue_labels(issue)
    if labels & _IMPL_LABELS:
        return True
    if labels & _DECISION_LABELS:
        return False
    title = str(issue.get("title", "")).lower()
    return title.startswith("impl") or "implementation" in title


def derive_decided_but_unbuilt(
    issues: Sequence[Mapping[str, Any]],
) -> list[Finding]:
    """Flag open implementation siblings of a closed decision/RFC issue.

    Hierarchy source of truth is native ``subIssues`` on parents when present.
    Issues that declare ``parent`` are grouped as siblings as a fixture-friendly
    offline fallback when the parent node is absent from the snapshot.
    """
    by_number = {int(issue["number"]): dict(issue) for issue in issues}
    groups: dict[int, list[dict[str, Any]]] = {}
    parents_from_subissues: set[int] = set()

    for issue in by_number.values():
        children = list(issue.get("subIssues", []))
        if children or (issue_labels(issue) & _PARENT_LABELS):
            parent_number = int(issue["number"])
            parents_from_subissues.add(parent_number)
            groups[parent_number] = [
                by_number.get(int(child["number"]), dict(child)) for child in children
            ]

    for issue in by_number.values():
        parent = issue.get("parent")
        if parent is None:
            continue
        parent_number = (
            int(parent["number"])
            if isinstance(parent, dict) and parent.get("number") is not None
            else int(parent)
        )
        if parent_number in parents_from_subissues:
            continue
        groups.setdefault(parent_number, []).append(issue)

    findings: list[Finding] = []
    seen: set[int] = set()
    for siblings in groups.values():
        decisions = [
            sibling for sibling in siblings if not _is_open(sibling) and _is_decision_issue(sibling)
        ]
        implementations = [
            sibling
            for sibling in siblings
            if _is_open(sibling) and _is_implementation_issue(sibling)
        ]
        if not decisions or not implementations:
            continue
        decision_numbers = tuple(sorted({int(item["number"]) for item in decisions}))
        for impl in implementations:
            number = int(impl["number"])
            if number in seen:
                continue
            seen.add(number)
            labels = issue_labels(impl)
            desired = {"decided-but-unbuilt"}
            current = labels & {"decided-but-unbuilt"}
            joined = ", ".join(f"#{item}" for item in decision_numbers)
            findings.append(
                Finding(
                    number=number,
                    title=str(impl.get("title", "")),
                    kind="decided-but-unbuilt",
                    reasons=(
                        f"decision issue(s) {joined} closed but implementation sibling "
                        f"#{number} remains open",
                    ),
                    add_labels=tuple(sorted(desired - current)),
                    remove_labels=(),
                    related=decision_numbers,
                )
            )
    return findings


def reconcile_backlog(
    *,
    issues: Sequence[Mapping[str, Any]],
    merged_prs: Sequence[Mapping[str, Any]],
    coverage: Mapping[int, Sequence[str]] | None = None,
    anchor_results: Mapping[int, AnchorStatus] | None = None,
) -> ReconcileReport:
    """Derive the full offline backlog drift report from a fixture-shaped graph."""
    coverage = coverage or {}
    anchors = normalize_anchor_results(
        {str(key): value for key, value in (anchor_results or {}).items()},
        coverage=coverage,
    )
    open_issues = [issue for issue in issues if _is_open(issue)]
    reconciliation = derive_reconciliation_findings(
        open_issues,
        merged_prs,
        coverage=coverage,
        anchor_results=anchors,
    )
    workability = derive_workability_findings(open_issues)
    decided = derive_decided_but_unbuilt(issues)
    closeable = tuple(item for item in reconciliation if item.kind == "closeable")
    pending = tuple(item for item in reconciliation if item.kind == "merged-pending-close")
    closure = tuple(item for item in workability if item.kind == "closure-candidate")
    return ReconcileReport(
        closeable=closeable,
        pending_close=pending,
        closure_candidates=closure,
        decided_but_unbuilt=tuple(decided),
        reconciliation=tuple(reconciliation),
        workability=tuple(workability),
    )


def closeable_issue_numbers(report: ReconcileReport) -> tuple[int, ...]:
    """Issues that may be auto-closed when the operator opts in."""
    return tuple(item.number for item in report.closeable)


def render_report(report: ReconcileReport) -> str:
    """Render a Markdown reconciliation report suitable for CI summaries."""
    lines = ["# Backlog reconciliation", ""]

    def section(title: str, rows: Sequence[Finding]) -> None:
        lines.append(f"## {title} ({len(rows)})")
        if not rows:
            lines.append("_none_")
            lines.append("")
            return
        for finding in rows:
            reason = "; ".join(finding.reasons)
            label_note = ""
            if finding.add_labels:
                label_note += f" → add `{'`, `'.join(finding.add_labels)}`"
            if finding.remove_labels:
                label_note += f" → remove `{'`, `'.join(finding.remove_labels)}`"
            lines.append(f"- #{finding.number} {finding.title}{label_note} — {reason}")
        lines.append("")

    section("Closeable (acceptance passing)", report.closeable)
    section("Merged — pending close (acceptance not passing)", report.pending_close)
    section("Parents — closure candidates", report.closure_candidates)
    section("Decided but unbuilt", report.decided_but_unbuilt)

    epic_review = [item for item in report.reconciliation if item.kind == "stale-epic-review"]
    weak = [
        item
        for item in report.reconciliation
        if not item.closed_by and item.mentioned_by and not item.is_epic
    ]
    tracked = [item for item in report.reconciliation if item.has_tests]
    section("Epics to re-review", epic_review)
    section("Mentioned by merged PRs (weak)", weak)
    lines.append(f"## Acceptance-tracked issues ({len(tracked)})")
    if tracked:
        lines.extend(f"- #{item.number} ({item.anchor_status or 'tracked'})" for item in tracked)
    else:
        lines.append(
            "_No open issue yet carries a discoverable acceptance anchor. "
            "See docs/backlog-truth.md._"
        )
    lines.append("")

    workability_rows = [item for item in report.workability if item.kind != "closure-candidate"]
    if workability_rows:
        lines.append("# Backlog workability")
        lines.append("")
        for finding in workability_rows:
            changes = [
                *(f"+{label}" for label in finding.add_labels),
                *(f"-{label}" for label in finding.remove_labels),
            ]
            suffix = f" ({', '.join(changes)})" if changes else ""
            lines.append(
                f"- #{finding.number} {finding.title}{suffix}: {'; '.join(finding.reasons)}"
            )
        lines.append("")
    return "\n".join(lines)


def load_fixture(data: Mapping[str, Any]) -> BacklogFixture:
    """Parse a JSON-shaped offline backlog fixture."""
    issues = tuple(data.get("issues") or ())
    merged_prs = tuple(data.get("merged_prs") or data.get("mergedPrs") or ())
    raw_anchors = data.get("anchor_results") or data.get("anchorResults") or {}
    raw_coverage = data.get("coverage") or {}
    coverage = {int(key): list(value) for key, value in raw_coverage.items()}
    anchors = normalize_anchor_results(raw_anchors, coverage=coverage)
    return BacklogFixture(
        issues=issues,
        merged_prs=merged_prs,
        anchor_results=anchors,
        coverage=coverage,
    )
