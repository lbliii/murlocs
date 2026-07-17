from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

from milo import CLI, Context, Option, Positional

from murlocs import __version__
from murlocs.errors import MurlocsError
from murlocs.manifest import PROTOCOL_TEMPLATE, load_manifest, render_manifest
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
    inspection = {
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    }
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
