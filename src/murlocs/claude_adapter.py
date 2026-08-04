"""Repository-local Claude Code lifecycle adapter.

Claude Code supplies JSON hook payloads on stdin and accepts structured hook
responses on stdout.  This bridge accepts no root, freshness token, command,
or authority decision from the active agent.  It invokes only Murlocs' typed
read-only APIs; no command registered by the repository manifest is executed.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from murlocs.adapter_conformance import ADAPTER_CONTRACT, REQUIRED_CAPABILITIES
from murlocs.cli import check_command, impact_command
from murlocs.hooks import run_hook

ADAPTER_ID = "claude-code-hooks"
ADAPTER_VERSION = "1"
MAX_CONTEXT_BYTES = 9_000
_GIT_COMMIT = re.compile(r"(?:^|[;&|]\s*)git(?:\s+-[^\s]+)*\s+commit(?:\s|$)")


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


def _paths(root: Path, value: object) -> list[str]:
    """Extract explicit edit targets, rejecting paths outside the project root."""
    if not isinstance(value, Mapping):
        return []
    candidates = [value.get(name) for name in ("file_path", "path", "file", "filePath")]
    paths: set[str] = set()
    for item in candidates:
        if not isinstance(item, str) or not item:
            continue
        candidate = Path(item)
        resolved = (
            candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
        )
        try:
            paths.add(resolved.relative_to(root).as_posix())
        except ValueError:
            continue
    return sorted(paths)


def _changed_paths(root: Path) -> list[str]:
    """Read tracked and untracked paths, without hooks or diff conversion."""
    commands = (
        ["git", "-C", str(root), "diff", "--name-only", "--no-ext-diff", "HEAD"],
        ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard"],
    )
    paths: set[str] = set()
    for command in commands:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if completed.returncode not in {0, 1}:
            return []
        paths.update(line for line in completed.stdout.splitlines() if line)
    return sorted(paths)


def _is_git_commit(value: object) -> bool:
    if isinstance(value, Mapping):
        value = value.get("command")
    return isinstance(value, str) and bool(_GIT_COMMIT.search(value))


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
    packet = _packet(_run(root, event, paths, correlation))
    if event == "pre-completion":
        if packet:
            return {"decision": "block", "reason": packet}
        return {}
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
        if args[0] == "pre-completion":
            response = {"decision": "block", "reason": f"Murlocs adapter unavailable: {exc}"}
        elif args[0] == "prospective-impact":
            response = _context("PreToolUse", f"Murlocs adapter unavailable: {exc}")
        else:
            response = _context(
                "SessionStart" if args[0] == "task-start" else "PostToolUse",
                f"Murlocs adapter unavailable: {exc}",
            )
    print(json.dumps(response, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
