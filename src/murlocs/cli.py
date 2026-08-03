from __future__ import annotations

import json
import sys
import tomllib
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

from milo import CLI, Context, Option, Positional

from murlocs import __version__
from murlocs.adoption import adoption_status
from murlocs.curation import check_records, propose_record, review_proposal
from murlocs.errors import MurlocsError
from murlocs.impact import (
    build_impact_report,
    changed_paths_from_revision,
    normalize_changed_paths,
)
from murlocs.manifest import (
    PROTOCOL_TEMPLATE,
    load_manifest,
    parse_manifest_data,
    render_manifest,
)
from murlocs.migration import (
    adopt_manifest,
    candidate_from_stewards,
    diff_stewards_candidate,
    inventory_repository,
    prune_legacy,
    rollback_migration,
    write_candidate,
)
from murlocs.model import Manifest
from murlocs.paths import repo_path
from murlocs.render import compile_manifest, prepare_manifest, render_outputs
from murlocs.rollout import ScopePlan, apply_add_scope, plan_add_scope
from murlocs.verify import Finding, validate


def _normalize_impact_path_options(
    argv: list[str],
    command_names: frozenset[str],
    root_value_options: frozenset[str] = frozenset(),
) -> list[str]:
    """Coalesce repeated impact paths into Milo's array-option syntax."""
    resolved = list(argv)
    command_index: int | None = None
    index = 0
    while index < len(resolved):
        token = resolved[index]
        if token in command_names:
            command_index = index
            break
        if token in root_value_options:
            index += 2
            continue
        index += 1
    if command_index is None or resolved[command_index] != "impact":
        return resolved

    before = resolved[: command_index + 1]
    after = resolved[command_index + 1 :]
    normalized: list[str] = []
    paths: list[str] = []
    insertion_index: int | None = None
    index = 0
    while index < len(after):
        token = after[index]
        if token == "--path":
            if insertion_index is None:
                insertion_index = len(normalized)
            index += 1
            while index < len(after) and not after[index].startswith("-"):
                paths.append(after[index])
                index += 1
            continue
        if token.startswith("--path="):
            if insertion_index is None:
                insertion_index = len(normalized)
            paths.append(token.partition("=")[2])
            index += 1
            continue
        normalized.append(token)
        index += 1

    if insertion_index is None:
        return resolved
    normalized[insertion_index:insertion_index] = ["--path", *paths]
    return [*before, *normalized]


class MurlocsCLI(CLI):
    """Milo registry with a 0.4.3 repeatable-array compatibility shim."""

    def run(self, argv: list[str] | None = None) -> Any:
        resolved = list(sys.argv[1:] if argv is None else argv)
        command_names = frozenset(path.split(".", 1)[0] for path, _ in self.walk_commands())
        root_value_options = frozenset(
            flag
            for option in self.root_option_specs()
            if option.action == "store"
            for flag in option.flags
        )
        normalized = _normalize_impact_path_options(
            resolved,
            command_names,
            root_value_options,
        )
        return super().run(normalized)


class CommandResult(dict[str, Any]):
    """Structured result with terminal-only presentation and exit metadata."""

    __slots__ = ("exit_code", "terminal_stream", "terminal_text")

    def __init__(
        self,
        payload: dict[str, Any],
        *,
        terminal_text: str,
        exit_code: int = 0,
        terminal_stream: Literal["stdout", "stderr"] = "stdout",
    ) -> None:
        super().__init__(payload)
        self.terminal_text = terminal_text
        self.exit_code = exit_code
        self.terminal_stream = terminal_stream


class ErrorPayload(TypedDict):
    code: str
    message: str


class FailurePayload(TypedDict):
    ok: bool
    error: ErrorPayload


class CompilePayload(TypedDict):
    ok: bool
    network: str
    generated: list[str]
    dry_run: bool


class CoveragePayload(TypedDict):
    state: Literal["unconfigured", "structurally_incomplete", "structurally_complete"]
    roots: list[str]
    evaluated: bool


class InitPayload(CompilePayload):
    coverage: CoveragePayload


class FindingPayload(TypedDict):
    code: str
    message: str


class SummaryPayload(TypedDict):
    scopes: int
    invariants: int
    checks: int
    issues: int


class CheckPayload(TypedDict):
    ok: bool
    findings: list[FindingPayload]
    summary: SummaryPayload
    coverage: CoveragePayload


class InvariantPayload(TypedDict):
    id: str
    severity: str
    statement: str


class LayerPayload(TypedDict):
    id: str
    kind: str
    path: str
    owners: list[str]


class ScopePayload(TypedDict):
    id: str
    map: str
    point_of_view: str
    invariants: list[InvariantPayload]
    layers: list[LayerPayload]


class OverridePayload(TypedDict):
    subject: str
    field: str
    winner_layer: str
    winner_path: str
    shadowed_layer: str
    shadowed_path: str
    winner_value: str
    shadowed_value: str


class FocusedCheckPayload(TypedDict):
    name: str
    invoke: str
    location: str


class BudgetPayload(TypedDict):
    active_bytes: int
    max_active_bytes: int


class ExplainPayload(TypedDict):
    ok: bool
    path: str
    scopes: list[ScopePayload]
    overrides: list[OverridePayload]
    checks: list[FocusedCheckPayload]
    budget: BudgetPayload


class ImpactPayload(TypedDict):
    ok: bool
    schema_version: int
    input: ImpactInputPayload
    policy: ImpactPolicyPayload
    summary: ImpactSummaryPayload
    scopes: list[ImpactScopePayload]


class ImpactInputPayload(TypedDict):
    paths: list[str]
    revision_range: str | None


class ImpactPolicyPayload(TypedDict):
    version: int
    required: str
    recommended: str
    unaffected: str


class ImpactSummaryPayload(TypedDict):
    required: int
    recommended: int
    unaffected: int


class ImpactGuidancePayload(TypedDict):
    id: str
    map: str


class ImpactInvariantPayload(TypedDict):
    id: str
    severity: str
    statement: str
    verification: str
    enforced_by: str | None
    evidence_file: str | None
    anchor: str | None


class ImpactCheckPayload(TypedDict):
    name: str
    invoke: str
    location: str
    description: str


class ImpactEdgePayload(TypedDict):
    direction: str
    type: str
    scope: str
    what: str


class ImpactScopePayload(TypedDict):
    id: str
    path: str
    map: str
    status: str
    reasons: list[str]
    guidance_chain: list[ImpactGuidancePayload]
    layers: list[LayerPayload]
    owners: list[str]
    invariants: list[ImpactInvariantPayload]
    checks: list[ImpactCheckPayload]
    edges: list[ImpactEdgePayload]
    review_protocol: str


class InventoryInstructionPayload(TypedDict):
    path: str
    kind: str
    generator: str


class LegacySummaryPayload(TypedDict):
    network: str
    scopes: int
    invariants: int
    checks: int
    proof_debt: int


class MurlocsStatusPayload(TypedDict):
    manifest: bool
    lock: bool
    migration: bool


class InventoryPayload(TypedDict):
    ok: bool
    root: str
    instructions: list[InventoryInstructionPayload]
    legacy_stewards: LegacySummaryPayload | None
    murlocs: MurlocsStatusPayload
    ownership_conflicts: list[str]


class StatusEvidencePayload(TypedDict):
    id: str
    path: str
    detail: str


class StatusBlockerPayload(TypedDict):
    id: str
    message: str
    evidence: list[str]


class StatusActionPayload(TypedDict):
    id: str
    command: str
    writes: bool
    review_required: bool
    reason: str


class AdoptionCoveragePayload(TypedDict):
    state: Literal["configured", "unconfigured"]
    roots: list[str]


class StatusPayload(TypedDict):
    ok: bool
    root: str
    state: Literal[
        "uninitialized",
        "user_owned",
        "legacy_detected",
        "candidate_manifest",
        "migration_adopted",
        "migration_pruned",
        "managed_synchronized",
        "managed_invalid",
        "ambiguous",
    ]
    evidence: list[StatusEvidencePayload]
    blockers: list[StatusBlockerPayload]
    next_actions: list[StatusActionPayload]
    coverage: AdoptionCoveragePayload
    semantic_correctness: Literal["not_evaluated"]


class TranslationFindingPayload(TypedDict):
    level: str
    code: str
    message: str
    subjects: list[str]


class SemanticDiffPayload(TypedDict):
    network: str
    scopes: int
    invariants: int
    checks: int
    findings: list[TranslationFindingPayload]


class RenderedDiffPayload(TypedDict):
    path: str
    status: str
    diff: str


class DiffPayload(TypedDict, total=False):
    ok: bool
    semantic: SemanticDiffPayload
    rendered: list[RenderedDiffPayload]


class ImportPayload(TypedDict):
    ok: bool
    source: str
    manifest: str
    findings: list[TranslationFindingPayload]
    written: list[str]
    dry_run: bool


class MigrationActionPayload(TypedDict, total=False):
    ok: bool
    id: str
    status: str
    backup: str
    adopted: list[str]
    originals: list[str]
    created: list[str]
    pruned: list[str]
    restore: list[str]
    remove: list[str]
    restore_legacy: bool
    lock_existed: bool
    adopted_sha256: dict[str, str]


class CurationFindingPayload(TypedDict):
    code: str
    message: str
    blocking: bool


class CurationRecordSummaryPayload(TypedDict):
    id: str
    path: str
    state: str


class CurationCheckPayload(TypedDict):
    ok: bool
    records: list[CurationRecordSummaryPayload]
    findings: list[CurationFindingPayload]


class CurationProposalPayload(TypedDict, total=False):
    id: str
    state: str
    intent: str
    subject_kind: str
    target_source: str
    target_scope: str | None
    target_key: str | None
    proposer: str
    origin: str
    rationale: str


class CurationOwnersPayload(TypedDict):
    recorded: list[str]
    current: list[str]


class CurationDecisionPayload(TypedDict):
    state: str
    actor: str
    at: str
    rationale: str
    review_ref: str | None


class CurationEvidencePayload(TypedDict):
    kind: str
    reference: str
    summary: str


class CurationChangePayload(TypedDict):
    operation: str
    before: (
        str
        | list[str]
        | dict[
            str,
            str | bool | list[str] | list[dict[str, str]] | dict[str, list[str]],
        ]
        | None
    )
    after: (
        str
        | list[str]
        | dict[
            str,
            str | bool | list[str] | list[dict[str, str]] | dict[str, list[str]],
        ]
        | None
    )


class CurationSourcePayload(TypedDict):
    recorded_sha256: str
    current_sha256: str
    proposed_sha256: str
    stale_base: bool


class CurationChainPayload(TypedDict):
    scope: str
    path: str
    maps: list[str]
    current_bytes: int
    proposed_bytes: int
    delta_bytes: int
    max_active_bytes: int
    over_budget: bool


class CurationValidationPayload(TypedDict):
    code: str
    message: str


class CurationReviewPayload(TypedDict):
    ok: bool
    proposal: CurationProposalPayload
    owners: CurationOwnersPayload
    decisions: list[CurationDecisionPayload]
    evidence: list[CurationEvidencePayload]
    change: CurationChangePayload
    source: CurationSourcePayload
    exact_duplicates: list[CurationFindingPayload]
    key_collisions: list[CurationFindingPayload]
    shadowing: list[CurationFindingPayload]
    affected_chains: list[CurationChainPayload]
    validation_findings: list[CurationValidationPayload]
    findings: list[CurationFindingPayload]


class CurationProposePayload(TypedDict):
    ok: bool
    id: str
    path: str
    dry_run: bool
    record: str
    review: CurationReviewPayload


def _render_result(result: Any, _ctx: Any) -> str:
    if not isinstance(result, CommandResult):
        return str(result)
    if result.exit_code:
        stream = sys.stderr if result.terminal_stream == "stderr" else sys.stdout
        stream.write(result.terminal_text)
        if result.terminal_text and not result.terminal_text.endswith("\n"):
            stream.write("\n")
        stream.flush()
        raise SystemExit(result.exit_code)
    return result.terminal_text


def _failure(code: str, error: Exception) -> FailurePayload:
    message = str(error)
    return CommandResult(
        {
            "ok": False,
            "error": {
                "code": code,
                "message": message,
            },
        },
        terminal_text=f"error: {message}",
        exit_code=1,
        terminal_stream="stderr",
    )


def _root(path: str) -> Path:
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise MurlocsError(f"repository root is not a directory: {root}")
    return root


def init_command(
    repo: Annotated[str, Option(metavar="PATH")] = ".",
    name: str | None = None,
    coverage_root: Annotated[list[str] | None, Option(metavar="PATH")] = None,
    ctx: Context | None = None,
) -> InitPayload | FailurePayload:
    """Create a starter manifest and compile its root guidance map.

    Args:
        repo: Repository root to initialize.
        name: Guidance network name; defaults to the repository directory name.
        coverage_root: Repository-relative source root to evaluate; repeat for multiple roots.
        ctx: Milo host context used to honor dry-run policy.
    """
    try:
        root = _root(repo)
        manifest_path = root / ".murlocs" / "manifest.toml"
        protocol_path = root / ".murlocs" / "PROTOCOL.md"
        if manifest_path.exists():
            raise MurlocsError(f"manifest already exists: {manifest_path}")
        if (root / "AGENTS.md").exists():
            raise MurlocsError(
                "AGENTS.md already exists and is unmanaged; "
                "migrate it into the manifest before compiling"
            )
        network = name or root.name
        coverage_roots = _normalize_coverage_roots(root, coverage_root or [])
        preview = parse_manifest_data(
            root,
            tomllib.loads(render_manifest(network, tuple(coverage_roots))),
        )
        preview_findings = validate(preview)
        coverage = _coverage_payload(
            coverage_roots,
            [item for item in preview_findings if item.code == "coverage"],
        )
        if ctx is not None and ctx.dry_run:
            planned = [".murlocs/manifest.toml", ".murlocs/PROTOCOL.md", "AGENTS.md"]
            return CommandResult(
                {
                    "ok": True,
                    "network": network,
                    "generated": planned,
                    "dry_run": True,
                    "coverage": coverage,
                },
                terminal_text="\n".join(
                    [
                        *(f"would write {relative}" for relative in planned),
                        _coverage_terminal(coverage),
                    ]
                ),
            )
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            render_manifest(network, tuple(coverage_roots)), encoding="utf-8"
        )
        protocol_path.write_text(PROTOCOL_TEMPLATE, encoding="utf-8")
        manifest = load_manifest(root)
        initial_findings = validate(manifest)
        coverage = _coverage_payload(
            list(manifest.coverage_roots),
            [item for item in initial_findings if item.code == "coverage"],
        )
        blocking = [
            item
            for item in initial_findings
            if item.code not in {"coverage", "drift", "lock"}
        ]
        if blocking:
            messages = "; ".join(str(item) for item in blocking)
            raise MurlocsError(f"starter manifest is not valid: {messages}")
        written = compile_manifest(manifest)
    except MurlocsError as exc:
        return _failure("MURLOCS_INIT", exc)

    return CommandResult(
        {
            "ok": True,
            "network": manifest.network,
            "generated": written,
            "dry_run": False,
            "coverage": coverage,
        },
        terminal_text="\n".join(
            [
                f"initialized {manifest.network} with {len(written)} managed map(s)",
                _coverage_terminal(coverage),
            ]
        ),
    )


def _normalize_coverage_roots(root: Path, entries: list[str]) -> list[str]:
    normalized: list[str] = []
    for entry in entries:
        target = repo_path(root, entry, field="coverage root")
        if not target.is_dir():
            raise MurlocsError(f"coverage root is not a directory: {entry}")
        relative = target.relative_to(root).as_posix()
        if relative not in normalized:
            normalized.append(relative)
    return normalized


def _coverage_payload(roots: list[str], findings: list[Finding]) -> CoveragePayload:
    if not roots:
        state: Literal[
            "unconfigured", "structurally_incomplete", "structurally_complete"
        ] = "unconfigured"
    elif findings:
        state = "structurally_incomplete"
    else:
        state = "structurally_complete"
    return {"state": state, "roots": roots, "evaluated": bool(roots)}


def _coverage_terminal(coverage: CoveragePayload) -> str:
    count = len(coverage["roots"])
    if coverage["state"] == "unconfigured":
        return "coverage unconfigured: no source roots were evaluated"
    if coverage["state"] == "structurally_incomplete":
        return f"coverage incomplete: {count} declared root(s) have structural findings"
    return f"coverage structurally complete: {count} declared root(s) have no findings"


def compile_command(
    repo: Annotated[str, Option(metavar="PATH")] = ".",
    ctx: Context | None = None,
) -> CompilePayload | FailurePayload:
    """Compile managed AGENTS.md maps from the repository manifest.

    Args:
        repo: Repository root containing `.murlocs/manifest.toml`.
        ctx: Milo host context used to honor dry-run policy.
    """
    try:
        manifest = load_manifest(_root(repo))
        blocking = _precompile_findings(manifest)
        if blocking:
            messages = "; ".join(str(item) for item in blocking)
            raise MurlocsError(f"manifest validation failed: {messages}")
        if ctx is not None and ctx.dry_run:
            written = sorted(prepare_manifest(manifest))
        else:
            written = compile_manifest(manifest)
    except MurlocsError as exc:
        return _failure("MURLOCS_COMPILE", exc)

    return CommandResult(
        {
            "ok": True,
            "network": manifest.network,
            "generated": written,
            "dry_run": bool(ctx is not None and ctx.dry_run),
        },
        terminal_text="\n".join(
            f"{'would write' if ctx is not None and ctx.dry_run else 'wrote'} {relative}"
            for relative in written
        ),
    )


class CodeownersRequirementPayload(TypedDict):
    file: str
    path: str
    owners: list[str]
    entry: str
    status: str
    actual_owners: list[str]
    blocking: bool


class AddScopePayload(TypedDict):
    ok: bool
    scope: str
    layer: str
    map: str
    owners: list[str]
    added: list[str]
    changed: list[str]
    deferred: dict[str, str]
    uncovered: list[str]
    codeowners_requirements: list[CodeownersRequirementPayload]
    written: list[str]
    dry_run: bool


def add_scope_command(
    path: Annotated[str, Positional("PATH")],
    repo: Annotated[str, Option(metavar="PATH")] = ".",
    id: Annotated[str | None, Option(metavar="ID")] = None,
    pov: Annotated[str | None, Option(metavar="TEXT")] = None,
    owners: Annotated[list[str] | None, Option(metavar="OWNER")] = None,
    defer: Annotated[list[str] | None, Option(metavar="PATH=REASON")] = None,
    ctx: Context | None = None,
) -> AddScopePayload | FailurePayload:
    """Introduce a scoped guidance layer for a selected directory.

    Args:
        path: Repository directory to give its own scoped map.
        repo: Repository root containing `.murlocs/manifest.toml`.
        id: Scope and layer id; defaults to a slug of the path.
        pov: Point of view for the new scope.
        owners: Guidance owners recorded on the new layer.
        defer: Source-bearing paths intentionally left out, as `PATH=REASON`.
        ctx: Milo host context used to honor dry-run policy.
    """
    try:
        root = _root(repo)
        deferrals = _parse_deferrals(defer or [])
        plan, manifest = plan_add_scope(
            root,
            path,
            scope_id=id,
            point_of_view=pov,
            owners=tuple(owners or ()),
            deferrals=deferrals,
        )
        dry_run = bool(ctx is not None and ctx.dry_run)
        written: list[str] = []
        if not dry_run:
            written = apply_add_scope(root, plan, manifest)
    except MurlocsError as exc:
        return _failure("MURLOCS_ADD_SCOPE", exc)

    return CommandResult(
        {
            "ok": True,
            "scope": plan.scope_id,
            "layer": plan.layer_path,
            "map": plan.map_path,
            "owners": list(plan.owners),
            "added": plan.added,
            "changed": plan.changed,
            "deferred": plan.deferrals,
            "uncovered": plan.uncovered,
            "codeowners_requirements": [
                {
                    "file": requirement.file,
                    "path": requirement.path,
                    "owners": list(requirement.owners),
                    "entry": requirement.entry,
                    "status": requirement.status,
                    "actual_owners": list(requirement.actual_owners),
                    "blocking": not requirement.satisfied,
                }
                for requirement in plan.codeowners_requirements
            ],
            "written": written,
            "dry_run": dry_run,
        },
        terminal_text=_render_add_scope(plan, dry_run),
    )


def _parse_deferrals(entries: list[str]) -> dict[str, str]:
    deferrals: dict[str, str] = {}
    for entry in entries:
        target, sep, reason = entry.partition("=")
        if not sep or not target.strip() or not reason.strip():
            raise MurlocsError(f"deferral must be PATH=REASON: {entry}")
        deferrals[target.strip()] = reason.strip()
    return deferrals


def _render_add_scope(plan: ScopePlan, dry_run: bool) -> str:
    verb = "would add" if dry_run else "added"
    lines = [
        f"{verb} scope {plan.scope_id} → {plan.map_path}",
        f"layer: {plan.layer_path}"
        + (f" (owners: {', '.join(plan.owners)})" if plan.owners else ""),
    ]
    for relative in plan.added:
        lines.append(f"  + {relative}")
    for relative in plan.changed:
        lines.append(f"  ~ {relative}")
    for defer_path, reason in sorted(plan.deferrals.items()):
        lines.append(f"  deferred {defer_path}: {reason}")
    for message in plan.uncovered:
        lines.append(f"  uncovered: {message}")
    for requirement in plan.codeowners_requirements:
        state = "ready" if requirement.satisfied else f"blocking: {requirement.status}"
        lines.extend(
            [
                f"  CODEOWNERS ({state}) {requirement.file}",
                f"    required exact entry: {requirement.entry}",
            ]
        )
        if requirement.actual_owners and not requirement.satisfied:
            lines.append(f"    current owners: {' '.join(requirement.actual_owners)}")
        if not requirement.satisfied:
            lines.append("    add or correct this entry before applying; Murlocs will not edit it")
    if dry_run:
        lines.extend(["", "manifest registration:", plan.decl_toml.rstrip()])
        lines.extend(["", f"layer {plan.layer_path}:", plan.layer_toml.rstrip()])
    return "\n".join(lines)


def _precompile_findings(manifest: Manifest) -> list[Finding]:
    return [item for item in validate(manifest) if item.code not in {"drift", "lock"}]


def check_command(
    repo: Annotated[str, Option(metavar="PATH")] = ".",
) -> CheckPayload | FailurePayload:
    """Validate manifest structure, proofs, coverage, ownership, and drift.

    Args:
        repo: Repository root to inspect. Registered checks are never executed.
    """
    try:
        manifest = load_manifest(_root(repo))
        findings = validate(manifest)
    except MurlocsError as exc:
        return _failure("MURLOCS_CHECK", exc)

    summary = {
        "scopes": len(manifest.scopes),
        "invariants": len(manifest.invariants),
        "checks": len(manifest.checks),
        "issues": len(findings),
    }
    coverage = _coverage_payload(
        list(manifest.coverage_roots),
        [item for item in findings if item.code == "coverage"],
    )
    if findings:
        terminal = "\n".join(
            [
                *(str(item) for item in findings),
                f"murlocs found {len(findings)} issue(s)",
                _coverage_terminal(coverage),
            ]
        )
        return CommandResult(
            {
                "ok": False,
                "findings": [
                    {
                        "code": item.code,
                        "message": item.message,
                    }
                    for item in findings
                ],
                "summary": summary,
                "coverage": coverage,
            },
            terminal_text=terminal,
            exit_code=1,
            terminal_stream="stderr",
        )

    terminal = (
        f"murlocs check passed: {summary['scopes']} scope(s), "
        f"{summary['invariants']} invariant(s), {summary['checks']} check(s)"
        f"\n{_coverage_terminal(coverage)}"
    )
    return CommandResult(
        {
            "ok": True,
            "findings": [],
            "summary": summary,
            "coverage": coverage,
        },
        terminal_text=terminal,
    )


def explain_command(
    path: Annotated[str, Positional("PATH")],
    repo: Annotated[str, Option(metavar="PATH")] = ".",
) -> ExplainPayload | FailurePayload:
    """Explain the ordered guidance chain applicable to a repository path.

    Args:
        path: File or directory inside the repository.
        repo: Repository root containing the guidance network.
    """
    try:
        root = _root(repo)
        manifest = load_manifest(root)
        target = Path(path)
        absolute = target.resolve() if target.is_absolute() else (root / target).resolve()
        try:
            relative = absolute.relative_to(root)
        except ValueError as exc:
            raise MurlocsError(f"path is outside repository: {target}") from exc

        applicable = []
        for scope in manifest.scopes:
            scope_root = repo_path(root, scope.path, field="scope path")
            try:
                absolute.relative_to(scope_root)
                applicable.append((len(scope_root.parts), scope))
            except ValueError:
                continue
        applicable.sort(key=lambda item: item[0])
        outputs = render_outputs(manifest)
    except MurlocsError as exc:
        return _failure("MURLOCS_EXPLAIN", exc)

    applicable_scopes = [scope for _, scope in applicable]
    applicable_ids = {scope.id for scope in applicable_scopes}
    scopes: list[ScopePayload] = []
    lines = [f"Guidance for {relative.as_posix() or '.'}"]
    for scope in applicable_scopes:
        invariants = [item for item in manifest.invariants if item.scope == scope.id]
        layer_ids = _contributing_layers(manifest, scope)
        layer_payloads = _layer_payloads(manifest, layer_ids)
        scopes.append(
            {
                "id": scope.id,
                "map": scope.map,
                "point_of_view": scope.point_of_view,
                "invariants": [
                    {
                        "id": item.id,
                        "severity": item.severity,
                        "statement": item.statement,
                    }
                    for item in invariants
                ],
                "layers": layer_payloads,
            }
        )
        lines.extend(["", f"[{scope.id}] {scope.map}", f"  {scope.point_of_view}"])
        if layer_payloads:
            trace = ", ".join(
                f"{layer['id']} ({layer['kind']})" for layer in layer_payloads
            )
            lines.append(f"  from: {trace}")
            owners = sorted({owner for layer in layer_payloads for owner in layer["owners"]})
            if owners:
                lines.append(f"  owners: {', '.join(owners)}")
        for invariant in invariants:
            lines.append(f"  - {invariant.id} ({invariant.severity}): {invariant.statement}")

    overrides = _applicable_overrides(manifest, applicable_ids)
    if overrides:
        lines.extend(["", "Overrides:"])
        for override in overrides:
            lines.append(
                f"  {override['subject']}.{override['field']}: "
                f"{override['winner_layer']} wins over {override['shadowed_layer']}"
            )

    focused_checks = _focused_checks(manifest, applicable_scopes)
    if focused_checks:
        lines.extend(["", "Focused checks:"])
        for check in focused_checks:
            lines.append(f"  {check['name']}: `{check['invoke']}`")

    active_bytes = sum(
        len(outputs.get(scope.map, "").encode("utf-8")) for scope in applicable_scopes
    )
    lines.extend(["", f"Active guidance: {active_bytes}/{manifest.max_active_bytes} bytes"])

    return CommandResult(
        {
            "ok": True,
            "path": relative.as_posix() or ".",
            "scopes": scopes,
            "overrides": overrides,
            "checks": focused_checks,
            "budget": {
                "active_bytes": active_bytes,
                "max_active_bytes": manifest.max_active_bytes,
            },
        },
        terminal_text="\n".join(lines),
    )


def _contributing_layers(manifest: Manifest, scope: Any) -> tuple[str, ...]:
    return manifest.source_ids_for_scope(scope.id)


def _layer_payloads(manifest: Manifest, layer_ids: tuple[str, ...]) -> list[LayerPayload]:
    payloads: list[LayerPayload] = []
    for layer_id in layer_ids:
        source = manifest.source(layer_id)
        if source is None:
            continue
        payloads.append(
            {
                "id": source.id,
                "kind": source.kind,
                "path": source.path,
                "owners": list(source.owners),
            }
        )
    return payloads


def _applicable_overrides(manifest: Manifest, scope_ids: set[str]) -> list[OverridePayload]:
    invariant_scope = {item.id: item.scope for item in manifest.invariants}
    payloads: list[OverridePayload] = []
    for override in manifest.overrides:
        kind, _, name = override.subject.partition(":")
        if kind == "scope" and name not in scope_ids:
            continue
        if kind == "invariant" and invariant_scope.get(name) not in scope_ids:
            continue
        if kind == "check":
            continue
        winner = manifest.source(override.winner_layer)
        shadowed = manifest.source(override.shadowed_layer)
        payloads.append(
            {
                "subject": override.subject,
                "field": override.field,
                "winner_layer": override.winner_layer,
                "winner_path": winner.path if winner else "",
                "shadowed_layer": override.shadowed_layer,
                "shadowed_path": shadowed.path if shadowed else "",
                "winner_value": override.winner_value,
                "shadowed_value": override.shadowed_value,
            }
        )
    return payloads


def _focused_checks(manifest: Manifest, scopes: list[Any]) -> list[FocusedCheckPayload]:
    ordered: list[FocusedCheckPayload] = []
    seen: set[str] = set()
    scope_ids = {scope.id for scope in scopes}
    for invariant in manifest.invariants:
        if invariant.scope not in scope_ids or invariant.verification != "command":
            continue
        check = manifest.checks.get(invariant.enforced_by or "")
        if check is None or check.name in seen:
            continue
        seen.add(check.name)
        ordered.append(
            {"name": check.name, "invoke": check.invoke, "location": check.location}
        )
    return ordered


def impact_command(
    path: Annotated[list[str] | None, Option(metavar="PATH")] = None,
    revision_range: Annotated[str | None, Option(metavar="REVISION_RANGE")] = None,
    repo: Annotated[str, Option(metavar="PATH")] = ".",
) -> ImpactPayload | FailurePayload:
    """Report which guidance scopes need review for a changed-path set.

    Args:
        path: Changed repository path. Repeat the option to report a set.
        revision_range: Git revision range whose changed paths should be included.
        repo: Repository root containing the guidance network.
    """
    try:
        root = _root(repo)
        manifest = load_manifest(root)
        if not path and revision_range is None:
            raise MurlocsError("provide at least one --path or --revision-range")
        explicit = normalize_changed_paths(root, path or ())
        from_git = (
            changed_paths_from_revision(root, revision_range) if revision_range else ()
        )
        changed = tuple(sorted(set(explicit) | set(from_git)))
        report = build_impact_report(
            manifest,
            changed,
            revision_range=revision_range,
        )
    except MurlocsError as exc:
        return _failure("MURLOCS_IMPACT", exc)

    affected = [scope for scope in report["scopes"] if scope["status"] != "unaffected"]
    lines = [
        f"Guidance review impact for {len(changed)} changed path(s)",
        *[f"  {changed_path}" for changed_path in changed],
    ]
    if affected:
        for scope in affected:
            lines.extend(["", f"[{scope['status']}] {scope['id']} → {scope['map']}"])
            if scope["owners"]:
                lines.append(f"  owners: {', '.join(scope['owners'])}")
            for reason in scope["reasons"]:
                lines.append(f"  - {reason}")
    else:
        lines.extend(["", "No declared guidance scope is affected."])
    lines.extend(
        [
            "",
            "Review impact is a routing signal; it does not claim that guidance is false.",
            (
                f"Summary: {report['summary']['required']} required, "
                f"{report['summary']['recommended']} recommended, "
                f"{report['summary']['unaffected']} unaffected"
            ),
        ]
    )
    return CommandResult(report, terminal_text="\n".join(lines))


def inventory_command(
    repo: Annotated[str, Option(metavar="PATH")] = ".",
) -> InventoryPayload | FailurePayload:
    """Inventory repository guidance and migration ownership conflicts.

    Args:
        repo: Repository root to inspect without writing files.
    """
    try:
        inventory = inventory_repository(_root(repo))
    except (MurlocsError, OSError, ValueError) as exc:
        return _failure("MURLOCS_INVENTORY", exc)
    legacy = inventory["legacy_stewards"]
    lines = [f"found {len(inventory['instructions'])} instruction file(s)"]
    if legacy:
        lines.append(
            f"legacy network: {legacy['scopes']} scope(s), {legacy['invariants']} invariant(s), "
            f"{legacy['checks']} check(s), {legacy['proof_debt']} proof-debt item(s)"
        )
    lines.extend(
        f"{item['generator']:>8}  {item['path']}" for item in inventory["instructions"]
    )
    return CommandResult(
        {"ok": True, **inventory},
        terminal_text="\n".join(lines),
    )


def status_command(
    repo: Annotated[str, Option(metavar="PATH")] = ".",
) -> StatusPayload | FailurePayload:
    """Report repository adoption state and evidence without making changes.

    Args:
        repo: Repository root to classify from checked-in evidence.
    """
    try:
        status = adoption_status(_root(repo))
    except (MurlocsError, OSError, ValueError) as exc:
        return _failure("MURLOCS_STATUS", exc)
    lines = [f"state: {status['state']}"]
    lines.extend(
        f"evidence {item['id']}: {item['path']} — {item['detail']}"
        for item in status["evidence"]
    )
    lines.extend(
        f"blocker {item['id']}: {item['message']}" for item in status["blockers"]
    )
    lines.extend(
        f"next {item['id']}: {item['command']} — {item['reason']}"
        for item in status["next_actions"]
    )
    lines.append("semantic correctness: not evaluated")
    return CommandResult({"ok": True, **status}, terminal_text="\n".join(lines))


def import_command(
    repo: Annotated[str, Option(metavar="PATH")] = ".",
    source: Annotated[str, Option(aliases=("--from",), metavar="FORMAT")] = "stewards",
    output: Annotated[str | None, Option(metavar="PATH")] = None,
    ctx: Context | None = None,
) -> ImportPayload | FailurePayload:
    """Translate legacy guidance into a candidate manifest without adopting maps.

    Args:
        repo: Repository root containing the legacy guidance network.
        source: Legacy format. Only `stewards` is supported in v0.2.
        output: Optional repository-relative candidate path; stdout when omitted.
        ctx: Milo host context used to honor dry-run policy.
    """
    try:
        if source != "stewards":
            raise MurlocsError(f"unsupported import source: {source}")
        root = _root(repo)
        candidate = candidate_from_stewards(root)
        written: list[str] = []
        dry_run = bool(ctx is not None and ctx.dry_run)
        if output and not dry_run:
            written = write_candidate(root, candidate, output)
        elif output:
            written = [output]
            if output == ".murlocs/manifest.toml":
                written.append(".murlocs/PROTOCOL.md")
    except (MurlocsError, OSError, ValueError) as exc:
        return _failure("MURLOCS_IMPORT", exc)
    findings = [
        {
            "level": item.level,
            "code": item.code,
            "message": item.message,
            "subjects": list(item.subjects),
        }
        for item in candidate.findings
    ]
    finding_lines = [
        f"{item.level}: {item.code} ({len(item.subjects)})" for item in candidate.findings
    ]
    if not output:
        report = "\n".join(f"# migration {line}" for line in finding_lines)
        terminal = candidate.manifest_toml + ("\n" + report if report else "")
    else:
        terminal = "\n".join(
            [
                *(
                    f"{'would write' if dry_run else 'wrote'} {path}"
                    for path in written
                ),
                *finding_lines,
            ]
        )
    return CommandResult(
        {
            "ok": True,
            "source": source,
            "manifest": candidate.manifest_toml,
            "findings": findings,
            "written": written,
            "dry_run": dry_run,
        },
        terminal_text=terminal,
    )


def diff_command(
    repo: Annotated[str, Option(metavar="PATH")] = ".",
    mode: Literal["semantic", "rendered", "both"] = "both",
) -> DiffPayload | FailurePayload:
    """Compare the legacy network with its candidate Murlocs projection.

    Args:
        repo: Repository root containing `.stewards/manifest.toml`.
        mode: Include semantic summary, rendered patches, or both.
    """
    try:
        result = diff_stewards_candidate(_root(repo))
    except (MurlocsError, OSError, ValueError) as exc:
        return _failure("MURLOCS_DIFF", exc)
    payload: dict[str, Any] = {"ok": True}
    lines: list[str] = []
    if mode in {"semantic", "both"}:
        payload["semantic"] = result["semantic"]
        semantic = result["semantic"]
        lines.append(
            f"{semantic['network']}: {semantic['scopes']} scope(s), "
            f"{semantic['invariants']} invariant(s), {semantic['checks']} check(s)"
        )
        lines.extend(
            f"{item['level']}: {item['code']} ({len(item['subjects'])})"
            for item in semantic["findings"]
        )
    if mode in {"rendered", "both"}:
        payload["rendered"] = result["rendered"]
        for item in result["rendered"]:
            lines.append(f"{item['status']:>7}  {item['path']}")
            if item["status"] != "same":
                lines.append(item["diff"].rstrip())
    return CommandResult(payload, terminal_text="\n".join(lines))


def adopt_command(
    repo: Annotated[str, Option(metavar="PATH")] = ".",
    ctx: Context | None = None,
) -> MigrationActionPayload | FailurePayload:
    """Explicitly adopt reviewed candidate maps with recoverable backups.

    Args:
        repo: Repository root with a reviewed `.murlocs/manifest.toml`.
        ctx: Milo host context used to honor dry-run policy.
    """
    try:
        result = adopt_manifest(_root(repo), dry_run=bool(ctx is not None and ctx.dry_run))
    except (MurlocsError, OSError, ValueError) as exc:
        return _failure("MURLOCS_ADOPT", exc)
    paths = result.get("adopted", list(result.get("adopted_sha256", {})))
    verb = "would adopt" if ctx is not None and ctx.dry_run else "adopted"
    return CommandResult({"ok": True, **result}, terminal_text=f"{verb} {len(paths)} map(s)")


def prune_command(
    repo: Annotated[str, Option(metavar="PATH")] = ".",
    ctx: Context | None = None,
) -> MigrationActionPayload | FailurePayload:
    """Move legacy steward tooling into the active recoverable backup.

    Args:
        repo: Adopted repository root.
        ctx: Milo host context used to honor dry-run policy.
    """
    try:
        result = prune_legacy(_root(repo), dry_run=bool(ctx is not None and ctx.dry_run))
    except (MurlocsError, OSError, ValueError) as exc:
        return _failure("MURLOCS_PRUNE", exc)
    count = len(result.get("pruned", []))
    verb = "would prune" if ctx is not None and ctx.dry_run else "pruned"
    return CommandResult({"ok": True, **result}, terminal_text=f"{verb} {count} legacy file(s)")


def rollback_command(
    repo: Annotated[str, Option(metavar="PATH")] = ".",
    ctx: Context | None = None,
) -> MigrationActionPayload | FailurePayload:
    """Restore the exact pre-adoption guidance network from its backup.

    Args:
        repo: Repository root with active Murlocs migration state.
        ctx: Milo host context used to honor dry-run policy.
    """
    try:
        result = rollback_migration(
            _root(repo), dry_run=bool(ctx is not None and ctx.dry_run)
        )
    except (MurlocsError, OSError, ValueError) as exc:
        return _failure("MURLOCS_ROLLBACK", exc)
    verb = "would roll back" if ctx is not None and ctx.dry_run else "rolled back"
    return CommandResult({"ok": True, **result}, terminal_text=f"{verb} migration")


def curate_propose_command(
    id: Annotated[str, Positional("ID")],
    intent: Annotated[str, Option(metavar="INTENT")],
    subject_kind: Annotated[str, Option(metavar="KIND")],
    target_source: Annotated[str, Option(metavar="PATH")],
    origin: Annotated[str, Option(metavar="REF")],
    rationale: Annotated[str, Option(metavar="TEXT")],
    proposer: Annotated[str, Option(metavar="ACTOR")],
    evidence_kind: Annotated[str, Option(metavar="KIND")],
    evidence_reference: Annotated[str, Option(metavar="REF")],
    evidence_summary: Annotated[str, Option(metavar="TEXT")],
    at: Annotated[str, Option(metavar="TIMESTAMP")],
    repo: Annotated[str, Option(metavar="PATH")] = ".",
    target_scope: Annotated[str | None, Option(metavar="SCOPE")] = None,
    target_key: Annotated[str | None, Option(metavar="KEY")] = None,
    value: Annotated[str | None, Option(metavar="TEXT")] = None,
    payload_json: Annotated[str | None, Option(metavar="JSON")] = None,
    ctx: Context | None = None,
) -> CurationProposePayload | FailurePayload:
    """Create one inert curation proposal without editing active guidance.

    Args:
        id: Repository-unique path-safe proposal id.
        intent: Proposed operation: add, replace, or remove.
        subject_kind: Canonical guidance subject kind.
        target_source: Explicitly active manifest or layer source.
        origin: Issue, task, or observation that originated the proposal.
        rationale: Why the guidance change is proposed.
        proposer: Attributed proposal actor.
        evidence_kind: Evidence type such as file_anchor, issue, or note.
        evidence_reference: Governed reference for the evidence.
        evidence_summary: Concise explanation of the evidence.
        at: Caller-supplied event timestamp.
        repo: Repository root containing the guidance network.
        target_scope: Optional affected scope id.
        target_key: Stable key for replacement, removal, or structured addition.
        value: String payload for list guidance.
        payload_json: Object payload for structured guidance.
        ctx: Milo host context used to honor dry-run policy.
    """
    try:
        result = propose_record(
            _root(repo),
            proposal_id=id,
            intent=intent,
            subject_kind=subject_kind,
            target_source=target_source,
            target_scope=target_scope,
            target_key=target_key,
            origin=origin,
            rationale=rationale,
            proposer=proposer,
            evidence_kind=evidence_kind,
            evidence_reference=evidence_reference,
            evidence_summary=evidence_summary,
            at=at,
            value=value,
            payload_json=payload_json,
            dry_run=bool(ctx is not None and ctx.dry_run),
        )
    except (MurlocsError, OSError, ValueError) as exc:
        return _failure("MURLOCS_CURATE_PROPOSE", exc)
    verb = "would write" if result["dry_run"] else "wrote"
    review = result["review"]
    return CommandResult(
        result,
        terminal_text="\n".join(
            [
                f"{verb} {result['path']}",
                f"proposal {id}: {intent} {subject_kind}",
                f"review findings: {len(review['findings'])}",
            ]
        ),
    )


def curate_review_command(
    id: Annotated[str, Positional("ID")],
    repo: Annotated[str, Option(metavar="PATH")] = ".",
) -> CurationReviewPayload | FailurePayload:
    """Review an inert proposal and its prospective guidance model without writes.

    Args:
        id: Path-safe proposal id under `.murlocs/curation/`.
        repo: Repository root containing the guidance network.
    """
    try:
        report = review_proposal(_root(repo), id)
    except (MurlocsError, OSError, ValueError) as exc:
        return _failure("MURLOCS_CURATE_REVIEW", exc)
    proposal = report["proposal"]
    source = report["source"]
    change = report["change"]
    lines = [
        f"Curation proposal {proposal['id']} [{proposal['state']}]",
        f"  intent: {proposal['intent']} {proposal['subject_kind']}",
        f"  target: {proposal['target_source']} key={proposal['target_key'] or '-'}",
        f"  target scope: {proposal['target_scope'] or '-'}",
        f"  current owners: {', '.join(report['owners']['current']) or 'unowned'}",
        f"  recorded owners: {', '.join(report['owners']['recorded']) or 'unowned'}",
        f"  proposer: {proposal['proposer']} · origin: {proposal['origin']}",
        f"  rationale: {proposal['rationale']}",
        f"  stale base: {'yes' if source['stale_base'] else 'no'}",
        f"  recorded source: {source['recorded_sha256']}",
        f"  current source: {source['current_sha256']}",
        f"  proposed source: {source['proposed_sha256']}",
        "",
        "Before:",
        _curation_value(change["before"]),
        "After:",
        _curation_value(change["after"]),
        "",
        "Evidence:",
        *(
            f"  - [{item['kind']}] {item['reference']}: {item['summary']}"
            for item in report["evidence"]
        ),
        "",
        "Decisions:",
        *(
            f"  - {item['state']} by {item['actor']} at {item['at']}: "
            f"{item['rationale']}"
            + (f" ({item['review_ref']})" if item["review_ref"] else "")
            for item in report["decisions"]
        ),
        "",
        "Affected guidance chains:",
    ]
    if report["affected_chains"]:
        for chain in report["affected_chains"]:
            delta = f"{chain['delta_bytes']:+d}"
            lines.append(
                f"  - {chain['scope']} [{chain['path']}] "
                f"({' -> '.join(chain['maps'])}): "
                f"{chain['current_bytes']} -> {chain['proposed_bytes']} bytes "
                f"({delta}; max {chain['max_active_bytes']})"
            )
    else:
        lines.append("  - none")
    for title, key in (
        ("Exact duplicates", "exact_duplicates"),
        ("Key collisions", "key_collisions"),
        ("Deterministic shadowing", "shadowing"),
        ("Validation findings", "validation_findings"),
        ("All findings", "findings"),
    ):
        lines.extend(["", f"{title}:"])
        if report[key]:
            lines.extend(f"  - [{item['code']}] {item['message']}" for item in report[key])
        else:
            lines.append("  - none")
    return CommandResult(
        report,
        terminal_text="\n".join(lines),
        exit_code=0 if report["ok"] else 1,
        terminal_stream="stdout",
    )


def _curation_value(value: Any) -> str:
    if value is None:
        return "  (none)"
    return "  " + json.dumps(value, ensure_ascii=False, sort_keys=True)


def curate_check_command(
    repo: Annotated[str, Option(metavar="PATH")] = ".",
) -> CurationCheckPayload | FailurePayload:
    """Validate all inert curation records without changing repository files.

    Args:
        repo: Repository root containing optional `.murlocs/curation/` records.
    """
    try:
        result = check_records(_root(repo))
    except (MurlocsError, OSError, ValueError) as exc:
        return _failure("MURLOCS_CURATE_CHECK", exc)
    lines = [f"checked {len(result['records'])} curation record(s)"]
    lines.extend(f"[{item['code']}] {item['message']}" for item in result["findings"])
    if result["ok"]:
        lines.append("curation check passed")
    else:
        lines.append(f"curation check found {len(result['findings'])} issue(s)")
    return CommandResult(
        result,
        terminal_text="\n".join(lines),
        exit_code=0 if result["ok"] else 1,
        terminal_stream="stdout" if result["ok"] else "stderr",
    )


def build_cli(*, name: str = "murlocs") -> CLI:
    """Build an invocation-local Milo command registry."""
    app = MurlocsCLI(
        name=name,
        description="Raise and verify repository-local guidance networks.",
        version=__version__,
    )
    app.command(
        "init",
        description="Create a starter guidance network",
        surfaces=("cli",),
        annotations={"destructiveHint": True, "openWorldHint": True},
        terminal_renderer=_render_result,
    )(init_command)
    app.command(
        "compile",
        description="Compile managed AGENTS.md maps",
        surfaces=("cli",),
        annotations={
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        terminal_renderer=_render_result,
    )(compile_command)
    app.command(
        "add-scope",
        description="Introduce a scoped guidance layer for a selected directory",
        surfaces=("cli",),
        annotations={"destructiveHint": True, "openWorldHint": True},
        terminal_renderer=_render_result,
    )(add_scope_command)
    app.command(
        "import",
        description="Translate legacy guidance into a candidate manifest",
        surfaces=("cli",),
        annotations={"destructiveHint": True, "openWorldHint": True},
        terminal_renderer=_render_result,
    )(import_command)
    app.command(
        "adopt",
        description="Adopt reviewed candidate maps with recoverable backups",
        surfaces=("cli",),
        annotations={"destructiveHint": True, "openWorldHint": True},
        terminal_renderer=_render_result,
    )(adopt_command)
    app.command(
        "prune",
        description="Move legacy tooling into the migration backup",
        surfaces=("cli",),
        annotations={"destructiveHint": True, "openWorldHint": True},
        terminal_renderer=_render_result,
    )(prune_command)
    app.command(
        "rollback",
        description="Restore the pre-adoption guidance network",
        surfaces=("cli",),
        annotations={"destructiveHint": True, "openWorldHint": True},
        terminal_renderer=_render_result,
    )(rollback_command)
    inspection = {
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    }
    app.command(
        "inventory",
        description="Inventory repository guidance and ownership conflicts",
        surfaces=("cli", "mcp", "llms"),
        annotations=inspection,
        terminal_renderer=_render_result,
    )(inventory_command)
    app.command(
        "status",
        description="Report repository adoption state and next safe actions",
        surfaces=("cli", "mcp", "llms"),
        annotations=inspection,
        terminal_renderer=_render_result,
    )(status_command)
    app.command(
        "diff",
        description="Compare legacy guidance with its candidate projection",
        surfaces=("cli", "mcp", "llms"),
        annotations=inspection,
        terminal_renderer=_render_result,
    )(diff_command)
    app.command(
        "check",
        description="Validate guidance, proofs, coverage, ownership, and drift",
        surfaces=("cli", "mcp", "llms"),
        annotations=inspection,
        terminal_renderer=_render_result,
    )(check_command)
    app.command(
        "explain",
        description="Explain the guidance chain for a repository path",
        surfaces=("cli", "mcp", "llms"),
        annotations=inspection,
        terminal_renderer=_render_result,
    )(explain_command)
    app.command(
        "impact",
        description="Report guidance review impact for changed repository paths",
        surfaces=("cli", "mcp", "llms"),
        annotations=inspection,
        terminal_renderer=_render_result,
    )(impact_command)
    curate = app.group(
        "curate",
        description="Create and inspect inert guidance curation proposals",
    )
    curate.command(
        "propose",
        description="Create an inert guidance proposal",
        surfaces=("cli",),
        terminal_renderer=_render_result,
    )(curate_propose_command)
    curate.command(
        "review",
        description="Review a proposal and its prospective guidance model",
        surfaces=("cli", "mcp", "llms"),
        terminal_renderer=_render_result,
    )(curate_review_command)
    curate.command(
        "check",
        description="Validate inert curation records",
        surfaces=("cli", "mcp", "llms"),
        terminal_renderer=_render_result,
    )(curate_check_command)
    # Milo 0.4 exposes annotations on grouped CommandDef objects but not on
    # Group.command(). Preserve the same trust hints as root inspection commands.
    curate._commands["propose"] = replace(
        curate.get_command("propose"),
        annotations={"destructiveHint": True, "openWorldHint": True},
    )
    for command_name in ("review", "check"):
        curate._commands[command_name] = replace(
            curate.get_command(command_name), annotations=inspection
        )
    return app


cli = build_cli()


def main(argv: list[str] | None = None) -> None:
    """Run the canonical packaged command."""
    build_cli(name="murlocs").run(argv)


def mrr(argv: list[str] | None = None) -> None:
    """Run the short packaged alias with alias-aware help and errors."""
    build_cli(name="mrr").run(argv)


if __name__ == "__main__":
    main()
