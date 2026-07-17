from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

from milo import CLI, Context, Option, Positional

from murlocs import __version__
from murlocs.errors import MurlocsError
from murlocs.manifest import PROTOCOL_TEMPLATE, load_manifest, render_manifest
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
from murlocs.render import compile_manifest, prepare_manifest
from murlocs.verify import Finding, validate


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


class InvariantPayload(TypedDict):
    id: str
    severity: str
    statement: str


class ScopePayload(TypedDict):
    id: str
    map: str
    point_of_view: str
    invariants: list[InvariantPayload]


class ExplainPayload(TypedDict):
    ok: bool
    path: str
    scopes: list[ScopePayload]


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
    ctx: Context | None = None,
) -> CompilePayload | FailurePayload:
    """Create a starter manifest and compile its root guidance map.

    Args:
        repo: Repository root to initialize.
        name: Guidance network name; defaults to the repository directory name.
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
        if ctx is not None and ctx.dry_run:
            planned = [".murlocs/manifest.toml", ".murlocs/PROTOCOL.md", "AGENTS.md"]
            return CommandResult(
                {
                    "ok": True,
                    "network": network,
                    "generated": planned,
                    "dry_run": True,
                },
                terminal_text="\n".join(f"would write {relative}" for relative in planned),
            )
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(render_manifest(network), encoding="utf-8")
        protocol_path.write_text(PROTOCOL_TEMPLATE, encoding="utf-8")
        manifest = load_manifest(root)
        blocking = _precompile_findings(manifest)
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
        },
        terminal_text=f"initialized {manifest.network} with {len(written)} managed map(s)",
    )


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
    if findings:
        terminal = "\n".join(
            [*(str(item) for item in findings), f"murlocs found {len(findings)} issue(s)"]
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
            },
            terminal_text=terminal,
            exit_code=1,
            terminal_stream="stderr",
        )

    terminal = (
        f"murlocs check passed: {summary['scopes']} scope(s), "
        f"{summary['invariants']} invariant(s), {summary['checks']} check(s)"
    )
    return CommandResult(
        {
            "ok": True,
            "findings": [],
            "summary": summary,
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
    except MurlocsError as exc:
        return _failure("MURLOCS_EXPLAIN", exc)

    scopes: list[ScopePayload] = []
    lines = [f"Guidance for {relative.as_posix() or '.'}"]
    for _, scope in applicable:
        invariants = [item for item in manifest.invariants if item.scope == scope.id]
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
            }
        )
        lines.extend(["", f"[{scope.id}] {scope.map}", f"  {scope.point_of_view}"])
        for invariant in invariants:
            lines.append(f"  - {invariant.id} ({invariant.severity}): {invariant.statement}")

    return CommandResult(
        {
            "ok": True,
            "path": relative.as_posix() or ".",
            "scopes": scopes,
        },
        terminal_text="\n".join(lines),
    )


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


def build_cli(*, name: str = "murlocs") -> CLI:
    """Build an invocation-local Milo command registry."""
    app = CLI(
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
