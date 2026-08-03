from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Any, Literal

from milo import CLI, Option, Positional

from murlocs.cli_result import CommandResult
from murlocs.errors import MurlocsError
from murlocs.gitview import MAX_PRE_PUSH_BYTES
from murlocs.hooks import hook_status, install_hooks, run_hook, uninstall_hooks


def hook_run_command(
    event: Annotated[Literal["pre-commit", "pre-push"], Positional("EVENT")],
    repo: Annotated[str, Option(metavar="PATH")] = ".",
    correlation_id: Annotated[str | None, Option(metavar="ID")] = None,
    deadline_ms: Annotated[int, Option(metavar="MILLISECONDS")] = 10_000,
    path: Annotated[list[str] | None, Option(metavar="PATH")] = None,
    remote_name: Annotated[str | None, Option(metavar="NAME")] = None,
    remote_url: Annotated[str | None, Option(metavar="URL")] = None,
) -> dict[str, Any]:
    """Run one passive Git hook against the exact selected Git view.

    Args:
        event: Git hook event to assess.
        repo: Exact Git worktree root.
        correlation_id: Optional caller task/run id carried unchanged.
        deadline_ms: Total local fail-closed deadline.
        path: Optional explicit staged path; repeat without changing exact-index coverage.
        remote_name: Pre-push remote name, treated only as inert metadata.
        remote_url: Pre-push remote URL, treated only as inert metadata.
    """
    del remote_name, remote_url

    pre_push_input = b""
    if event == "pre-push":
        source = getattr(sys.stdin, "buffer", sys.stdin)
        raw = source.read(MAX_PRE_PUSH_BYTES + 1)
        pre_push_input = raw.encode() if isinstance(raw, str) else raw
    result = run_hook(
        event,
        Path(repo),
        correlation_id=correlation_id,
        deadline_ms=deadline_ms,
        pre_push_input=pre_push_input,
        explicit_paths=tuple(path or ()),
    )
    return CommandResult(
        result.payload,
        terminal_text=result.terminal_text,
        exit_code=result.exit_code,
        terminal_stream="stderr" if result.exit_code else "stdout",
    )


def hook_install_command(
    event: Annotated[
        list[Literal["pre-commit", "pre-push"]] | None,
        Option(metavar="EVENT"),
    ] = None,
    repo: Annotated[str, Option(metavar="PATH")] = ".",
) -> dict[str, Any]:
    """Conservatively install Murlocs-owned default Git hooks.

    Args:
        event: Hook event to install; omission selects both supported events.
        repo: Exact Git worktree root.
    """
    try:
        result = install_hooks(Path(repo), tuple(event or ()))
    except MurlocsError as exc:
        return CommandResult(
            {"ok": False, "error": {"code": "MURLOCS_HOOK_INSTALL", "message": str(exc)}},
            terminal_text=f"error: {exc}",
            exit_code=1,
            terminal_stream="stderr",
        )
    return CommandResult(
        result,
        terminal_text=(
            "installed " + ", ".join(result["changed"])
            if result["changed"]
            else "Murlocs hooks already installed"
        ),
    )


def hook_uninstall_command(
    event: Annotated[
        list[Literal["pre-commit", "pre-push"]] | None,
        Option(metavar="EVENT"),
    ] = None,
    repo: Annotated[str, Option(metavar="PATH")] = ".",
) -> dict[str, Any]:
    """Remove only byte-exact Murlocs-owned default Git hooks.

    Args:
        event: Hook event to remove; omission selects both supported events.
        repo: Exact Git worktree root.
    """
    try:
        result = uninstall_hooks(Path(repo), tuple(event or ()))
    except MurlocsError as exc:
        return CommandResult(
            {"ok": False, "error": {"code": "MURLOCS_HOOK_UNINSTALL", "message": str(exc)}},
            terminal_text=f"error: {exc}",
            exit_code=1,
            terminal_stream="stderr",
        )
    return CommandResult(
        result,
        terminal_text=(
            "removed " + ", ".join(result["changed"])
            if result["changed"]
            else "no Murlocs-owned hooks were installed"
        ),
    )


def hook_status_command(
    repo: Annotated[str, Option(metavar="PATH")] = ".",
) -> dict[str, Any]:
    """Inspect default Git hook slots without changing them.

    Args:
        repo: Exact Git worktree root.
    """
    try:
        result = hook_status(Path(repo))
    except MurlocsError as exc:
        return CommandResult(
            {"ok": False, "error": {"code": "MURLOCS_HOOK_STATUS", "message": str(exc)}},
            terminal_text=f"error: {exc}",
            exit_code=1,
            terminal_stream="stderr",
        )
    return CommandResult(
        result,
        terminal_text="\n".join(
            f"{event}: {state}" for event, state in result["events"].items()
        ),
    )


def register_hook_commands(
    app: CLI,
    *,
    terminal_renderer: Callable[[Any, Any], str],
) -> None:
    """Register the passive Git-hook command group on a Milo CLI."""
    hook = app.group(
        "hook",
        description="Run and conservatively manage passive Git hooks",
    )
    hook.command(
        "run",
        description="Assess an exact Git hook view",
        surfaces=("cli",),
        terminal_renderer=terminal_renderer,
    )(hook_run_command)
    hook.command(
        "install",
        description="Install only into safe default Git hook slots",
        surfaces=("cli",),
        terminal_renderer=terminal_renderer,
    )(hook_install_command)
    hook.command(
        "uninstall",
        description="Remove only exact Murlocs-owned Git hooks",
        surfaces=("cli",),
        terminal_renderer=terminal_renderer,
    )(hook_uninstall_command)
    hook.command(
        "status",
        description="Inspect default Git hook ownership",
        surfaces=("cli",),
        terminal_renderer=terminal_renderer,
    )(hook_status_command)
    for command_name in ("install", "uninstall"):
        hook._commands[command_name] = replace(
            hook.get_command(command_name),
            annotations={
                "destructiveHint": True,
                "idempotentHint": True,
                "openWorldHint": True,
            },
        )
    inspection = {
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    }
    for command_name in ("run", "status"):
        hook._commands[command_name] = replace(
            hook.get_command(command_name), annotations=inspection
        )
