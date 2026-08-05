"""Repository-local Claude Code lifecycle adapter.

Claude Code supplies JSON hook payloads on stdin and accepts structured hook
responses on stdout.  This bridge accepts no root, freshness token, command,
or authority decision from the active agent.  It invokes only Murlocs' typed
read-only APIs; no command registered by the repository manifest is executed.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from murlocs.adapter_conformance import ADAPTER_CONTRACT, REQUIRED_CAPABILITIES
from murlocs.cli import check_command, impact_command
from murlocs.copilot_adapter import LifecycleAdapterDriver
from murlocs.hooks import run_hook

ADAPTER_ID = "claude-code-hooks"
ADAPTER_VERSION = "1"
MAX_CONTEXT_BYTES = 9_000


def descriptor() -> dict[str, Any]:
    """Return the closed v1 capability declaration for this adapter."""
    return {
        "contract": ADAPTER_CONTRACT,
        "schema_version": 1,
        "adapter_id": ADAPTER_ID,
        "adapter_version": ADAPTER_VERSION,
        "lifecycle_versions": [1],
        "outcome_versions": [1],
        "required_capabilities": list(REQUIRED_CAPABILITIES),
        "optional_capabilities": ["native-task-start", "native-post-edit", "native-pre-completion"],
        "events": {
            "task-start": "host-enforced",
            # The v1 suite specifies a prompt-mediated prospective finding.
            # Claude Code could block here, but doing so would make one host's
            # policy an accidental change to the portable contract.
            "prospective-impact": "prompt-mediated",
            "post-edit": "host-enforced",
            "pre-commit": "host-enforced",
            "pre-completion": "host-enforced",
        },
        "root_formats": ["posix", "windows-drive", "windows-unc"],
        "fallbacks": ["generated-guidance", "git-hook", "ci"],
        "deprecated_versions": [],
    }


class ClaudeAdapterDriver(LifecycleAdapterDriver):
    """Portable production driver with Claude's own trusted adapter identity."""

    adapter_id = ADAPTER_ID
    adapter_version = ADAPTER_VERSION
    conformance_name = "claude"
    host_name = "Claude Code"

    def descriptor(self) -> Mapping[str, Any]:
        return descriptor()


def _payload() -> dict[str, Any]:
    try:
        value = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError("Claude Code hook input must be one JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError("Claude Code hook input must be an object")
    return value


def _root(payload: Mapping[str, Any]) -> Path:
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        raise ValueError("Claude Code hook payload has no cwd")
    root = Path(cwd).resolve()
    if not (root / ".murlocs" / "manifest.toml").is_file():
        raise FileNotFoundError(".murlocs/manifest.toml is absent")
    return root


def _session(payload: Mapping[str, Any]) -> str:
    value = payload.get("session_id", "unknown")
    if not isinstance(value, str) or not value:
        return "unknown"
    return value[:128]


def _confined_path(root: Path, raw: str) -> str | None:
    """Normalize one host path while rejecting escapes through parents or symlinks."""
    if not raw or "\0" in raw:
        return None
    candidate = Path(raw)
    target = candidate if candidate.is_absolute() else root / candidate
    try:
        resolved_root = root.resolve(strict=True)
        resolved = target.resolve(strict=False)
        relative = resolved.relative_to(resolved_root)
    except OSError, ValueError:
        return None
    return None if relative == Path() else relative.as_posix()


def _paths(root: Path, value: object) -> list[str]:
    """Extract explicit edit targets from documented tool arguments, conservatively."""
    if not isinstance(value, Mapping):
        return []
    candidates = [value.get(name) for name in ("file_path", "path", "file", "filePath")]
    paths: list[str] = []
    for item in candidates:
        if not isinstance(item, str):
            continue
        normalized = _confined_path(root, item)
        if normalized is not None:
            paths.append(normalized)
    return sorted(set(paths))


def _has_path_candidate(value: object) -> bool:
    return isinstance(value, Mapping) and any(
        isinstance(value.get(name), str) for name in ("path", "file", "filePath", "file_path")
    )


def _git_paths(root: Path, arguments: Sequence[str]) -> list[str]:
    """Run one read-only Git path query and parse its NUL-delimited output."""
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        capture_output=True,
        timeout=5,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise OSError(message or f"Git path query exited {completed.returncode}")
    try:
        values = [item.decode("utf-8") for item in completed.stdout.split(b"\0") if item]
    except UnicodeDecodeError as exc:
        raise OSError("Git returned a non-UTF-8 repository path") from exc
    return values


def _changed_paths(root: Path) -> list[str]:
    """Capture staged, unstaged, deleted, and untracked paths without hooks or drivers."""
    staged = _git_paths(
        root,
        ("diff", "--cached", "--name-only", "-z", "--no-ext-diff", "--no-textconv", "--"),
    )
    unstaged = _git_paths(
        root,
        ("diff", "--name-only", "-z", "--no-ext-diff", "--no-textconv", "--"),
    )
    untracked = _git_paths(root, ("ls-files", "--others", "--exclude-standard", "-z", "--"))
    return sorted(set(staged) | set(unstaged) | set(untracked))


def _is_git_commit(value: object) -> bool:
    """Recognize direct Git commit commands without matching inert argument text."""
    if isinstance(value, Mapping):
        value = value.get("command")
    if not isinstance(value, str):
        return False
    lexer = shlex.shlex(value, posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        tokens = list(lexer)
    except ValueError:
        return False
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token and set(token) <= {";", "&", "|"}:
            segments.append([])
        else:
            segments[-1].append(token)
    options_with_values = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
    for segment in segments:
        executable = segment[0].replace("\\", "/").rsplit("/", 1)[-1].lower() if segment else ""
        if executable not in {"git", "git.exe"}:
            continue
        index = 1
        while index < len(segment):
            token = segment[index]
            if token in options_with_values:
                index += 2
                continue
            if token.startswith(("--git-dir=", "--work-tree=", "--namespace=", "--exec-path=")):
                index += 1
                continue
            if token.startswith("-"):
                index += 1
                continue
            if token == "commit":
                return True
            break
    return False


def _outcome(result: Mapping[str, Any]) -> Mapping[str, Any]:
    outcome = result.get("outcome")
    return outcome if isinstance(outcome, Mapping) else {}


def _packet(outcomes: Sequence[Mapping[str, Any]]) -> str:
    """Forward compact structured remediation without impersonating approval."""
    selected = [item for item in outcomes if item and not item.get("silent")]
    if not selected:
        return ""
    packet = {
        "adapter": {"id": ADAPTER_ID, "version": ADAPTER_VERSION},
        "outcomes": [
            {
                "code": item.get("code"),
                "resolution_class": item.get("resolution_class"),
                "summary": item.get("summary"),
                "next_actions": item.get("next_actions", []),
            }
            for item in selected
        ],
    }
    return json.dumps(packet, separators=(",", ":"), sort_keys=True)[:MAX_CONTEXT_BYTES]


def _run(root: Path, event: str, paths: Sequence[str], correlation: str) -> list[Mapping[str, Any]]:
    """Run the typed, read-only operations required by a lifecycle event."""
    checks: list[Mapping[str, Any]] = []
    if event != "prospective-impact":
        checks.append(_outcome(check_command(repo=str(root), correlation_id=correlation)))
    if event != "task-start":
        if not paths:
            return [
                {
                    "code": "MURLOCS_ACTIVATION_UNAVAILABLE",
                    "resolution_class": "agent_action",
                    "silent": False,
                    "summary": "No changed paths were available for Murlocs impact.",
                    "next_actions": [
                        {
                            "operation": "use_fallback",
                            "arguments": {"fallback": "git-hook"},
                            "effect": "read_repository",
                            "authority": "integration",
                        }
                    ],
                }
            ]
        checks.append(
            _outcome(impact_command(path=list(paths), repo=str(root), correlation_id=correlation))
        )
    return checks


def _context(event_name: str, packet: str) -> dict[str, Any]:
    if not packet:
        return {}
    return {"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": packet}}


def _deny_pre_tool(packet: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": packet,
        }
    }


def handle(event: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Translate one documented Claude Code hook event into a safe response."""
    try:
        root = _root(payload)
    except FileNotFoundError:
        return {}
    correlation = f"claude:{_session(payload)}:{event}"[:128]
    tool_input = payload.get("tool_input")
    paths = _paths(root, tool_input)
    if event == "prospective-impact":
        if not paths:
            if _has_path_candidate(tool_input):
                return _context(
                    "PreToolUse",
                    "Murlocs rejected an edit path outside the repository boundary.",
                )
            return {}
        packet = _packet(_run(root, event, paths, correlation))
        return _context("PreToolUse", packet) if packet else {}
    if event == "pre-commit":
        if not _is_git_commit(tool_input):
            return {}
        result = run_hook("pre-commit", root, correlation_id=correlation, deadline_ms=10_000)
        outcome = result.payload.get("outcome")
        packet = _packet([outcome] if isinstance(outcome, Mapping) else [])
        if result.exit_code:
            return _deny_pre_tool(packet or "Murlocs pre-commit gate failed.")
        return {}
    if event == "pre-completion":
        paths = _changed_paths(root)
    outcomes = _run(root, event, paths, correlation)
    packet = _packet(outcomes)
    if event == "pre-completion":
        blocking = any(item.get("blocking") is True for item in outcomes)
        unavailable = not outcomes or any(
            item.get("code") == "MURLOCS_ACTIVATION_UNAVAILABLE" for item in outcomes
        )
        if blocking or unavailable:
            reason = packet or "Murlocs completion evidence is unavailable."
            if payload.get("stop_hook_active") is True:
                reason += (
                    " Claude Code reports an active Stop-hook continuation; its eight-block "
                    "runaway guard may ultimately override this gate."
                )
            return {"decision": "block", "reason": reason}
        return {"systemMessage": packet} if packet else {}
    return _context("SessionStart" if event == "task-start" else "PostToolUse", packet)


def main(argv: list[str] | None = None) -> None:
    """Entrypoint used by repository-local Claude Code command hooks."""
    args = sys.argv[1:] if argv is None else argv
    events = {"task-start", "prospective-impact", "post-edit", "pre-commit", "pre-completion"}
    if len(args) != 1 or args[0] not in events:
        raise SystemExit("usage: murlocs-claude-adapter EVENT")
    try:
        response = handle(args[0], _payload())
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        message = f"Murlocs adapter unavailable: {exc}"
        if args[0] == "pre-completion":
            response = {"decision": "block", "reason": message}
        elif args[0] == "prospective-impact":
            response = _context("PreToolUse", message)
        elif args[0] == "pre-commit":
            response = _deny_pre_tool(message)
        else:
            response = _context(
                "SessionStart" if args[0] == "task-start" else "PostToolUse",
                message,
            )
    print(json.dumps(response, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
