"""Intent-shaped read-only task commands over existing Murlocs primitives.

This module owns the *composition* contract for the task-language front door
(`orient`, `review-changes`, and `finish`). It never reimplements finding,
routing, proof, authority, or outcome semantics: it composes the granular
`check`, `impact`, adoption, explanation, and curation-validation results into
one shared, versioned envelope so the three intent-shaped commands cannot drift
from the primitives or weaken lifecycle freshness.

The envelope classifies every composite action as ``blocking``,
``authority_required``, ``agent_action``, or ``recommended``; makes the
repository state, exact Git view, correlation id, and freshness dependencies
explicit; keeps healthy output compact and silent-capable; and fails visibly on
an unsupported or ambiguous change view.

See `docs/task-commands.md` for the normative specification, including the
versioning and backward-compatibility rules.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

from murlocs.adoption import adoption_status
from murlocs.curation import check_records
from murlocs.curation_transaction import transaction_pending
from murlocs.errors import MurlocsError
from murlocs.gitview import (
    Deadline,
    GitContext,
    capture_head,
    capture_index,
    changed_paths,
    discover_git,
    run_git,
)
from murlocs.impact import (
    build_impact_report,
    changed_paths_from_revision,
    normalize_changed_paths,
)
from murlocs.manifest import load_manifest
from murlocs.model import Manifest, Scope
from murlocs.outcome import (
    OutcomeCorrelationPayload,
    OutcomeFindingPayload,
    OutcomePayload,
    build_check_outcome,
    build_impact_outcome,
    validate_correlation_id,
)
from murlocs.paths import repo_path
from murlocs.render import render_outputs
from murlocs.source_annotations import annotation_provenance_payload
from murlocs.verify import Finding, annotation_findings, validate

TASK_CONTRACT = "io.murlocs.task"
TASK_SCHEMA_VERSION = 1
TASK_DEADLINE_MS = 10_000
STALE_RECEIPT_CODE = "MURLOCS_TASK_STALE_RECEIPT"

TaskCommandName = Literal["orient", "review-changes", "finish"]
TaskClassification = Literal["blocking", "authority_required", "agent_action", "recommended"]
GitViewKind = Literal["path", "paths", "staged", "working-tree", "revision"]
TaskLifecycle = Literal["inspection", "completion"]
TaskSource = Literal["check", "impact", "curation", "freshness"]
TaskStatus = Literal["pass", "advisory", "blocking"]

_CLASSIFICATION_ORDER: dict[TaskClassification, int] = {
    "blocking": 0,
    "authority_required": 1,
    "agent_action": 2,
    "recommended": 3,
}
_SOURCE_ORDER: dict[TaskSource, int] = {
    "freshness": 0,
    "check": 1,
    "curation": 2,
    "impact": 3,
}


class TaskRepositoryPayload(TypedDict):
    root: str
    adoption_state: str
    manifest_present: bool


class TaskGitViewPayload(TypedDict):
    kind: GitViewKind
    revision_range: str | None
    paths: list[str]
    available: bool
    detail: str


class TaskFreshnessPayload(TypedDict):
    lifecycle: TaskLifecycle
    view_state_id: str | None
    receipt_state_id: str | None
    stale: bool
    dependencies: list[str]


class TaskActionPayload(TypedDict):
    id: str
    classification: TaskClassification
    source: TaskSource
    summary: str
    codes: list[str]
    scopes: list[str]
    maps: list[str]
    owners: list[str]


class TaskClassificationCountsPayload(TypedDict):
    blocking: int
    authority_required: int
    agent_action: int
    recommended: int


class TaskCurationReceiptPayload(TypedDict):
    ok: bool
    records: list[dict[str, str]]
    findings: list[dict[str, Any]]


class TaskReceiptsPayload(TypedDict):
    check: OutcomePayload | None
    impact: OutcomePayload | None
    curation: TaskCurationReceiptPayload | None


class TaskEnvelopePayload(TypedDict):
    ok: bool
    contract: str
    schema_version: int
    command: TaskCommandName
    repository: TaskRepositoryPayload
    git_view: TaskGitViewPayload
    freshness: TaskFreshnessPayload
    correlation: OutcomeCorrelationPayload
    classification: TaskClassificationCountsPayload
    actions: list[TaskActionPayload]
    receipts: TaskReceiptsPayload
    status: TaskStatus
    blocking: bool
    silent: bool
    summary: str


class TaskScopeOrientationPayload(TypedDict):
    id: str
    map: str
    point_of_view: str
    owners: list[str]
    invariants: list[dict[str, str]]
    guardrails: list[str]
    focused_checks: list[dict[str, str]]
    provenance: list[dict[str, Any]]
    related_scopes: list[dict[str, str]]


class OrientationPayload(TypedDict):
    path: str
    scopes: list[TaskScopeOrientationPayload]
    maps: list[str]
    owners: list[str]
    focused_checks: list[dict[str, str]]
    related_scopes: list[dict[str, str]]
    budget: dict[str, int]


class OrientPayload(TaskEnvelopePayload):
    path: str
    orientation: OrientationPayload


class ReviewChangesPayload(TaskEnvelopePayload):
    review: dict[str, Any]


class CompletionPayload(TypedDict):
    registered_checks: list[dict[str, str]]
    executed_checks: bool
    curation_validation_ran: bool


class FinishPayload(TaskEnvelopePayload):
    completion: CompletionPayload


# ---------------------------------------------------------------------------
# Shared composition helpers
# ---------------------------------------------------------------------------


def _correlation(correlation_id: str | None) -> OutcomeCorrelationPayload:
    """Echo an unauthenticated caller correlation id without minting tokens."""
    return {
        "correlation_id": correlation_id,
        "state_id": None,
        "dependency_id": None,
        "token_source": "none",
        "token_scope": None,
    }


def _repository(root: Path, state: str, *, manifest_present: bool) -> TaskRepositoryPayload:
    return {
        "root": str(root),
        "adoption_state": state,
        "manifest_present": manifest_present,
    }


def _load_context(root: Path) -> tuple[Manifest, str]:
    """Resolve adoption state and the manifest, failing visibly on ambiguity."""
    status = adoption_status(root)
    state = str(status["state"])
    if state == "ambiguous":
        blockers = "; ".join(str(item["message"]) for item in status["blockers"])
        raise MurlocsError(
            "repository adoption state is ambiguous; resolve it before task commands"
            + (f": {blockers}" if blockers else "")
        )
    manifest = load_manifest(root)
    return manifest, state


def _classify_finding(finding: OutcomeFindingPayload) -> TaskClassification:
    """Map one granular outcome finding onto exactly one composite class."""
    resolution = finding["resolution_class"]
    if resolution == "authority_required":
        return "authority_required"
    if resolution == "deterministic_repair":
        return "blocking"
    # agent_action
    if finding["status"] == "advisory":
        return "recommended"
    return "agent_action"


class _Bucket:
    __slots__ = ("codes", "scopes", "maps", "owners", "count")

    def __init__(self) -> None:
        self.codes: set[str] = set()
        self.scopes: set[str] = set()
        self.maps: set[str] = set()
        self.owners: set[str] = set()
        self.count = 0


def _action_summary(classification: TaskClassification, source: TaskSource, bucket: _Bucket) -> str:
    scopes = ", ".join(sorted(bucket.scopes)) or "none"
    owners = ", ".join(sorted(bucket.owners)) or "none"
    if source == "freshness":
        return (
            "The supplied pre-edit receipt is stale; recompute completion evidence "
            "against the current repository state."
        )
    if classification == "authority_required":
        return (
            f"Obtain owner review before the gated boundary for scope(s): {scopes}; "
            f"owners: {owners}."
        )
    if classification == "blocking":
        return (
            f"Resolve {bucket.count} blocking {source} finding(s) before completion; "
            f"scope(s): {scopes}."
        )
    if classification == "recommended":
        return f"Consider reviewing recommended scope(s): {scopes}."
    return (
        f"Inspect {bucket.count} {source} finding(s) and the affected guidance; scope(s): {scopes}."
    )


def _build_actions(
    finding_sources: list[tuple[TaskSource, list[OutcomeFindingPayload]]],
    *,
    curation: TaskCurationReceiptPayload | None,
    stale: bool,
) -> list[TaskActionPayload]:
    """Derive one ordered action list traceable to its granular source findings."""
    buckets: dict[tuple[TaskClassification, TaskSource], _Bucket] = {}

    def bucket(classification: TaskClassification, source: TaskSource) -> _Bucket:
        return buckets.setdefault((classification, source), _Bucket())

    for source, findings in finding_sources:
        for finding in findings:
            classification = _classify_finding(finding)
            item = bucket(classification, source)
            item.codes.add(finding["code"])
            item.scopes.update(finding["affected"]["scopes"])
            item.maps.update(finding["affected"]["maps"])
            item.owners.update(finding["affected"]["owners"])
            item.count += 1

    if curation is not None:
        for finding in curation["findings"]:
            classification = "blocking" if finding["blocking"] else "agent_action"
            item = bucket(classification, "curation")
            item.codes.add(f"MURLOCS_CURATION_{str(finding['code']).upper()}")
            item.count += 1

    if stale:
        item = bucket("blocking", "freshness")
        item.codes.add(STALE_RECEIPT_CODE)
        item.count += 1

    actions: list[TaskActionPayload] = []
    for (classification, source), item in buckets.items():
        actions.append(
            {
                "id": f"task.{source}.{classification}",
                "classification": classification,
                "source": source,
                "summary": _action_summary(classification, source, item),
                "codes": sorted(item.codes),
                "scopes": sorted(item.scopes),
                "maps": sorted(item.maps),
                "owners": sorted(item.owners),
            }
        )
    return sorted(
        actions,
        key=lambda action: (
            _CLASSIFICATION_ORDER[action["classification"]],
            _SOURCE_ORDER[action["source"]],
            action["id"],
        ),
    )


def _counts(actions: list[TaskActionPayload]) -> TaskClassificationCountsPayload:
    counts: TaskClassificationCountsPayload = {
        "blocking": 0,
        "authority_required": 0,
        "agent_action": 0,
        "recommended": 0,
    }
    for action in actions:
        counts[action["classification"]] += 1
    return counts


def _envelope_status(
    *,
    check_outcome: OutcomePayload | None,
    curation: TaskCurationReceiptPayload | None,
    stale: bool,
    actions: list[TaskActionPayload],
) -> TaskStatus:
    hard_blocking = stale
    if check_outcome is not None and check_outcome["status"] == "blocking":
        hard_blocking = True
    if curation is not None and any(item["blocking"] for item in curation["findings"]):
        hard_blocking = True
    if hard_blocking:
        return "blocking"
    return "advisory" if actions else "pass"


def _summary(
    command: TaskCommandName, status: TaskStatus, counts: TaskClassificationCountsPayload
) -> str:
    if status == "pass":
        return f"murlocs {command}: healthy; no guidance action is required."
    parts = [
        f"{counts['blocking']} blocking",
        f"{counts['authority_required']} authority",
        f"{counts['agent_action']} agent",
        f"{counts['recommended']} recommended",
    ]
    return f"murlocs {command}: {status}; " + ", ".join(parts) + " action(s)."


def _assemble(
    *,
    command: TaskCommandName,
    repository: TaskRepositoryPayload,
    git_view: TaskGitViewPayload,
    freshness: TaskFreshnessPayload,
    correlation: OutcomeCorrelationPayload,
    receipts: TaskReceiptsPayload,
    finding_sources: list[tuple[TaskSource, list[OutcomeFindingPayload]]],
) -> TaskEnvelopePayload:
    actions = _build_actions(
        finding_sources,
        curation=receipts["curation"],
        stale=freshness["stale"],
    )
    status = _envelope_status(
        check_outcome=receipts["check"],
        curation=receipts["curation"],
        stale=freshness["stale"],
        actions=actions,
    )
    counts = _counts(actions)
    return {
        "ok": status != "blocking",
        "contract": TASK_CONTRACT,
        "schema_version": TASK_SCHEMA_VERSION,
        "command": command,
        "repository": repository,
        "git_view": git_view,
        "freshness": freshness,
        "correlation": correlation,
        "classification": counts,
        "actions": actions,
        "receipts": receipts,
        "status": status,
        "blocking": status == "blocking",
        "silent": status == "pass",
        "summary": _summary(command, status, counts),
    }


# ---------------------------------------------------------------------------
# Git view resolution (explicit, never guessed)
# ---------------------------------------------------------------------------


def _selected_view(
    *, paths: bool, staged: bool, working_tree: bool, revision_range: str | None
) -> GitViewKind:
    selected = [
        name
        for name, chosen in (
            ("paths", paths),
            ("staged", staged),
            ("working-tree", working_tree),
            ("revision", revision_range is not None),
        )
        if chosen
    ]
    if not selected:
        raise MurlocsError(
            "select exactly one change view: --path, --staged, --working-tree, or --revision"
        )
    if len(selected) > 1:
        raise MurlocsError("ambiguous change view; select only one of: " + ", ".join(selected))
    return cast(GitViewKind, selected[0])


def _untracked_paths(root: Path, deadline: Deadline) -> tuple[str, ...]:
    """List untracked, non-ignored working-tree files that `git diff HEAD` omits."""
    completed = run_git(deadline, root, ["ls-files", "--others", "--exclude-standard", "-z"])
    decoded = [os.fsdecode(part) for part in completed.stdout.split(b"\0") if part]
    return normalize_changed_paths(root, decoded)


def _working_tree_state_id(root: Path, changed: tuple[str, ...]) -> str:
    """Fingerprint the working-tree content of the changed paths for freshness.

    The state id changes whenever the reviewed working-tree content changes, so a
    stale pre-edit receipt is detected even for an unstaged edit that never
    touches the Git index.
    """
    digest = hashlib.sha256(b"murlocs-working-tree-view-v1\0")
    for path in sorted(changed):
        encoded = os.fsencode(path)
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        try:
            content = (root / path).read_bytes()
        except (OSError, ValueError):
            digest.update(b"\0absent\0")
        else:
            digest.update(b"\0blob\0")
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(hashlib.sha256(content).digest())
    return "sha256:" + digest.hexdigest()


def _resolve_change_view(
    root: Path,
    *,
    paths: tuple[str, ...],
    staged: bool,
    working_tree: bool,
    revision_range: str | None,
) -> tuple[
    GitViewKind,
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    str | None,
    TaskGitViewPayload,
    GitContext | None,
]:
    """Resolve one explicit change view into deterministic changed paths.

    Returns the view kind, the sorted changed paths, the explicit and
    revision-derived path partitions for `build_impact_report`, the observed Git
    view state id (freshness anchor), the structured Git-view payload, and the
    resolved Git context when a Git view was materialized.
    """
    kind = _selected_view(
        paths=bool(paths),
        staged=staged,
        working_tree=working_tree,
        revision_range=revision_range,
    )
    if kind == "paths":
        changed = normalize_changed_paths(root, paths)
        view: TaskGitViewPayload = {
            "kind": "paths",
            "revision_range": None,
            "paths": list(changed),
            "available": True,
            "detail": f"{len(changed)} explicit changed path(s)",
        }
        return "paths", changed, changed, (), None, view, None

    deadline = Deadline.start(TASK_DEADLINE_MS)
    context = discover_git(root, deadline)
    state_id: str | None
    if kind == "staged":
        after = capture_index(context, deadline)
        before = capture_head(context, deadline)
        changed = changed_paths(before, after)
        state_id = after.state_id
        detail = "staged index versus HEAD"
        used_range = None
    elif kind == "working-tree":
        tracked = changed_paths_from_revision(root, "HEAD")
        untracked = _untracked_paths(root, deadline)
        changed = tuple(sorted(set(tracked) | set(untracked)))
        # Anchor freshness to actual working-tree content, not the index: an
        # unstaged edit must invalidate a prior receipt. See docs/task-commands.md.
        state_id = _working_tree_state_id(root, changed)
        detail = "working tree versus HEAD (tracked changes and untracked files)"
        used_range = None
    else:  # revision
        assert revision_range is not None
        changed = changed_paths_from_revision(root, revision_range)
        state_id = None
        detail = f"revision range {revision_range}"
        used_range = revision_range
    view = {
        "kind": kind,
        "revision_range": used_range,
        "paths": list(changed),
        "available": True,
        "detail": detail,
    }
    return kind, changed, (), changed, state_id, view, context


def _freshness(
    *,
    lifecycle: TaskLifecycle,
    kind: GitViewKind,
    view_state_id: str | None,
    receipt_state_id: str | None,
) -> TaskFreshnessPayload:
    dependencies: list[str] = []
    if view_state_id is not None:
        dependencies.append(f"git-view:{kind}:{view_state_id}")
    if kind == "revision":
        dependencies.append("git-revision:immutable-commit-range")
    stale = receipt_state_id is not None and receipt_state_id != view_state_id
    return {
        "lifecycle": lifecycle,
        "view_state_id": view_state_id,
        "receipt_state_id": receipt_state_id,
        "stale": stale,
        "dependencies": sorted(dependencies),
    }


# ---------------------------------------------------------------------------
# orient
# ---------------------------------------------------------------------------


def _resolve_repo_path(root: Path, path: str) -> str:
    target = Path(path)
    absolute = target.resolve() if target.is_absolute() else (root / target).resolve()
    try:
        relative = absolute.relative_to(root)
    except ValueError as exc:
        raise MurlocsError(f"path is outside repository: {path}") from exc
    return relative.as_posix() or "."


def _applicable_scopes(manifest: Manifest, root: Path, relative: str) -> list[Scope]:
    absolute = (root / relative).resolve()
    applicable: list[tuple[int, Scope]] = []
    for scope in manifest.scopes:
        scope_root = repo_path(root, scope.path, field="scope path")
        try:
            absolute.relative_to(scope_root)
        except ValueError:
            continue
        applicable.append((len(scope_root.parts), scope))
    applicable.sort(key=lambda item: (item[0], item[1].id))
    return [scope for _, scope in applicable]


def _scope_owners(manifest: Manifest, scope: Scope) -> list[str]:
    owners: set[str] = set()
    for layer_id in manifest.source_ids_for_scope(scope.id):
        source = manifest.source(layer_id)
        if source is not None:
            owners.update(source.owners)
    return sorted(owners)


def _scope_focused_checks(manifest: Manifest, scope: Scope) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    seen: set[str] = set()
    for invariant in manifest.invariants:
        if invariant.scope != scope.id or invariant.verification != "command":
            continue
        check = manifest.checks.get(invariant.enforced_by or "")
        if check is None or check.name in seen:
            continue
        seen.add(check.name)
        checks.append({"name": check.name, "invoke": check.invoke, "location": check.location})
    return sorted(checks, key=lambda item: item["name"])


def _related_scopes(manifest: Manifest, scope: Scope) -> list[dict[str, str]]:
    related: list[dict[str, str]] = [
        {"direction": "outgoing", "type": edge.type, "scope": edge.to, "what": edge.what}
        for edge in scope.edges
    ]
    related.extend(
        {"direction": "incoming", "type": edge.type, "scope": candidate.id, "what": edge.what}
        for candidate in manifest.scopes
        for edge in candidate.edges
        if edge.to == scope.id
    )
    return sorted(
        related,
        key=lambda item: (item["direction"], item["scope"], item["type"], item["what"]),
    )


def _orientation(manifest: Manifest, root: Path, relative: str) -> OrientationPayload:
    applicable = _applicable_scopes(manifest, root, relative)
    outputs = render_outputs(manifest)
    provenance_by_invariant: dict[str, list[dict[str, Any]]] = {}
    for annotation in annotation_provenance_payload(manifest):
        provenance_by_invariant.setdefault(str(annotation["invariant"]), []).append(annotation)

    scope_payloads: list[TaskScopeOrientationPayload] = []
    all_maps: set[str] = set()
    all_owners: set[str] = set()
    all_checks: dict[str, dict[str, str]] = {}
    all_related: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for scope in applicable:
        invariants = [
            {
                "id": invariant.id,
                "severity": invariant.severity,
                "statement": invariant.statement,
                "verification": invariant.verification,
            }
            for invariant in manifest.invariants
            if invariant.scope == scope.id
        ]
        owners = _scope_owners(manifest, scope)
        checks = _scope_focused_checks(manifest, scope)
        related = _related_scopes(manifest, scope)
        provenance = sorted(
            (
                item
                for invariant in manifest.invariants
                if invariant.scope == scope.id
                for item in provenance_by_invariant.get(invariant.id, [])
            ),
            key=lambda item: (str(item["id"]), str(item["file"]), int(item["line"])),
        )
        scope_payloads.append(
            {
                "id": scope.id,
                "map": scope.map,
                "point_of_view": scope.point_of_view,
                "owners": owners,
                "invariants": invariants,
                "guardrails": list(scope.guardrails),
                "focused_checks": checks,
                "provenance": provenance,
                "related_scopes": related,
            }
        )
        all_maps.add(scope.map)
        all_owners.update(owners)
        for check in checks:
            all_checks[check["name"]] = check
        for item in related:
            all_related[(item["direction"], item["scope"], item["type"], item["what"])] = item

    active_bytes = sum(
        len(outputs.get(map_name, "").encode("utf-8"))
        for map_name in {scope.map for scope in applicable}
    )
    return {
        "path": relative,
        "scopes": scope_payloads,
        "maps": sorted(all_maps),
        "owners": sorted(all_owners),
        "focused_checks": [all_checks[name] for name in sorted(all_checks)],
        "related_scopes": [all_related[key] for key in sorted(all_related)],
        "budget": {
            "active_bytes": active_bytes,
            "max_active_bytes": manifest.max_active_bytes,
        },
    }


def build_orient(root: Path, path: str, *, correlation_id: str | None = None) -> OrientPayload:
    """Compose adoption, explanation, health, and routing for one repository path."""
    correlation_id = validate_correlation_id(correlation_id)
    manifest, state = _load_context(root)
    relative = _resolve_repo_path(root, path)
    orientation = _orientation(manifest, root, relative)

    check_findings = _check_findings(manifest)
    check_outcome = build_check_outcome(manifest, check_findings, correlation_id=correlation_id)
    changed = normalize_changed_paths(root, (relative,))
    report = build_impact_report(
        manifest, changed, revision_range=None, explicit_paths=changed, revision_paths=()
    )
    impact_outcome = build_impact_outcome(report, correlation_id=correlation_id)

    git_view: TaskGitViewPayload = {
        "kind": "path",
        "revision_range": None,
        "paths": [relative],
        "available": True,
        "detail": "single-path orientation; no Git diff view",
    }
    freshness = _freshness(
        lifecycle="inspection", kind="path", view_state_id=None, receipt_state_id=None
    )
    receipts: TaskReceiptsPayload = {
        "check": check_outcome,
        "impact": impact_outcome,
        "curation": None,
    }
    envelope = _assemble(
        command="orient",
        repository=_repository(root, state, manifest_present=True),
        git_view=git_view,
        freshness=freshness,
        correlation=_correlation(correlation_id),
        receipts=receipts,
        finding_sources=[
            ("check", check_outcome["findings"]),
            ("impact", impact_outcome["findings"]),
        ],
    )
    result = cast(OrientPayload, dict(envelope))
    result["path"] = relative
    result["orientation"] = orientation
    return result


# ---------------------------------------------------------------------------
# review-changes
# ---------------------------------------------------------------------------


def build_review_changes(
    root: Path,
    *,
    paths: tuple[str, ...] = (),
    staged: bool = False,
    working_tree: bool = False,
    revision_range: str | None = None,
    correlation_id: str | None = None,
) -> ReviewChangesPayload:
    """Route an explicit change view to required and recommended guidance review."""
    correlation_id = validate_correlation_id(correlation_id)
    manifest, state = _load_context(root)
    (
        kind,
        changed,
        explicit_paths,
        revision_paths,
        view_state_id,
        git_view,
        _context,
    ) = _resolve_change_view(
        root,
        paths=paths,
        staged=staged,
        working_tree=working_tree,
        revision_range=revision_range,
    )
    report = build_impact_report(
        manifest,
        changed,
        revision_range=revision_range,
        explicit_paths=explicit_paths,
        revision_paths=revision_paths,
    )
    impact_outcome = build_impact_outcome(report, correlation_id=correlation_id)
    report["outcome"] = impact_outcome

    freshness = _freshness(
        lifecycle="inspection",
        kind=kind,
        view_state_id=view_state_id,
        receipt_state_id=None,
    )
    receipts: TaskReceiptsPayload = {
        "check": None,
        "impact": impact_outcome,
        "curation": None,
    }
    envelope = _assemble(
        command="review-changes",
        repository=_repository(root, state, manifest_present=True),
        git_view=git_view,
        freshness=freshness,
        correlation=_correlation(correlation_id),
        receipts=receipts,
        finding_sources=[("impact", impact_outcome["findings"])],
    )
    result = cast(ReviewChangesPayload, dict(envelope))
    result["review"] = report
    return result


# ---------------------------------------------------------------------------
# finish
# ---------------------------------------------------------------------------


def _check_findings(manifest: Manifest) -> list[Finding]:
    findings = [*validate(manifest), *annotation_findings(manifest)]
    if transaction_pending(manifest.root):
        findings.append(
            Finding(
                "curation_transaction",
                "an interrupted curation transaction requires recovery before compile",
            )
        )
    return findings


def _registered_checks(manifest: Manifest) -> list[dict[str, str]]:
    return [
        {
            "name": check.name,
            "invoke": check.invoke,
            "location": check.location,
            "description": check.description,
        }
        for name, check in sorted(manifest.checks.items())
    ]


def build_finish(
    root: Path,
    *,
    paths: tuple[str, ...] = (),
    staged: bool = False,
    working_tree: bool = False,
    revision_range: str | None = None,
    receipt_state_id: str | None = None,
    correlation_id: str | None = None,
) -> FinishPayload:
    """Aggregate check, impact, and curation validation into a fresh receipt."""
    correlation_id = validate_correlation_id(correlation_id)
    manifest, state = _load_context(root)
    (
        kind,
        changed,
        explicit_paths,
        revision_paths,
        view_state_id,
        git_view,
        _context,
    ) = _resolve_change_view(
        root,
        paths=paths,
        staged=staged,
        working_tree=working_tree,
        revision_range=revision_range,
    )
    if receipt_state_id is not None and view_state_id is None:
        raise MurlocsError(
            "--receipt-state-id requires an index-bound Git view (--staged or --working-tree)"
        )

    check_findings = _check_findings(manifest)
    check_outcome = build_check_outcome(manifest, check_findings, correlation_id=correlation_id)
    report = build_impact_report(
        manifest,
        changed,
        revision_range=revision_range,
        explicit_paths=explicit_paths,
        revision_paths=revision_paths,
    )
    impact_outcome = build_impact_outcome(report, correlation_id=correlation_id)
    curation_result = check_records(root)
    curation: TaskCurationReceiptPayload = {
        "ok": bool(curation_result["ok"]),
        "records": list(curation_result["records"]),
        "findings": list(curation_result["findings"]),
    }

    freshness = _freshness(
        lifecycle="completion",
        kind=kind,
        view_state_id=view_state_id,
        receipt_state_id=receipt_state_id,
    )
    receipts: TaskReceiptsPayload = {
        "check": check_outcome,
        "impact": impact_outcome,
        "curation": curation,
    }
    envelope = _assemble(
        command="finish",
        repository=_repository(root, state, manifest_present=True),
        git_view=git_view,
        freshness=freshness,
        correlation=_correlation(correlation_id),
        receipts=receipts,
        finding_sources=[
            ("check", check_outcome["findings"]),
            ("impact", impact_outcome["findings"]),
        ],
    )
    result = cast(FinishPayload, dict(envelope))
    result["completion"] = {
        "registered_checks": _registered_checks(manifest),
        "executed_checks": False,
        "curation_validation_ran": True,
    }
    return result


# ---------------------------------------------------------------------------
# Compact, silent-capable terminal rendering
# ---------------------------------------------------------------------------


def render_task_lines(envelope: TaskEnvelopePayload) -> list[str]:
    """Render bounded, deterministic human text; the repo and Git view are explicit."""
    view = envelope["git_view"]
    view_detail = view["detail"]
    lines = [
        f"repo: {envelope['repository']['root']} ({envelope['repository']['adoption_state']})",
        f"view: {view['kind']} — {view_detail}",
        f"status: {envelope['status']}",
    ]
    if envelope["freshness"]["stale"]:
        lines.append("freshness: supplied receipt is stale for the current repository state")
    for action in envelope["actions"]:
        lines.append(f"- [{action['classification']}] {action['source']}: {action['summary']}")
    lines.append(envelope["summary"])
    return lines
