"""Repository-local GitHub Copilot lifecycle adapter.

The bridge deliberately accepts only Copilot's hook payload on stdin.  It never
accepts a repository root, freshness token, command, or authority decision from
the active agent.  Hooks invoke the installed Murlocs API directly, so no
repository-registered command is ever executed.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from murlocs.adapter_conformance import ADAPTER_CONTRACT, REQUIRED_CAPABILITIES
from murlocs.cli import check_command, impact_command
from murlocs.hooks import run_hook

ADAPTER_ID = "github-copilot-hooks"
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
        raise ValueError("Copilot hook input must be one JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError("Copilot hook input must be an object")
    return value


def _root(payload: Mapping[str, Any]) -> Path:
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        raise ValueError("Copilot hook payload has no cwd")
    root = Path(cwd).resolve()
    if not (root / ".murlocs" / "manifest.toml").is_file():
        raise FileNotFoundError(".murlocs/manifest.toml is absent")
    return root


def _session(payload: Mapping[str, Any]) -> str:
    value = payload.get("sessionId", payload.get("session_id", "unknown"))
    if not isinstance(value, str) or not value:
        return "unknown"
    return value[:128]


def _state(root: Path) -> str:
    """Mint an adapter-scoped token from regular files, without writing a sidecar."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_dir() or ".git" in path.parts:
            continue
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        if path.is_symlink():
            digest.update(b"link\0" + path.readlink().as_posix().encode())
        else:
            digest.update(path.read_bytes())
    return "copilot-state:" + digest.hexdigest()


def _paths(value: object) -> list[str]:
    """Extract explicit edit targets from documented tool arguments, conservatively."""
    if not isinstance(value, Mapping):
        return []
    candidates = [value.get(name) for name in ("path", "file", "filePath", "file_path")]
    paths = [
        item
        for item in candidates
        if isinstance(item, str) and item and not item.startswith("/")
    ]
    return sorted(set(paths))


def _changed_paths(root: Path) -> list[str]:
    """Read Git's path list only; it never invokes user hooks or diff conversion."""
    completed = subprocess.run(
        ["git", "-C", str(root), "diff", "--name-only", "--no-ext-diff", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if completed.returncode not in {0, 1}:
        return []
    return sorted({line for line in completed.stdout.splitlines() if line})


def _is_git_commit(value: object) -> bool:
    """Recognize the only shell invocation that this pre-tool hook gates."""
    if isinstance(value, Mapping):
        value = value.get("command")
    return isinstance(value, str) and "git commit" in value


def _outcome(result: Mapping[str, Any]) -> Mapping[str, Any]:
    outcome = result.get("outcome")
    return outcome if isinstance(outcome, Mapping) else {}


def _packet(outcomes: Sequence[Mapping[str, Any]]) -> str:
    """Forward compact, structured remediation without impersonating approval."""
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
    text = json.dumps(packet, separators=(",", ":"), sort_keys=True)
    return text[:MAX_CONTEXT_BYTES]


def _run(root: Path, event: str, paths: Sequence[str], correlation: str) -> list[Mapping[str, Any]]:
    """Run the typed, read-only operations required by the lifecycle event."""
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


def handle(event: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Translate one documented Copilot hook event into a safe hook response."""
    try:
        root = _root(payload)
    except FileNotFoundError:
        return {}
    correlation = f"copilot:{_session(payload)}:{event}"
    if len(correlation) > 128:
        correlation = correlation[:128]
    paths = _paths(payload.get("toolArgs", payload.get("tool_input")))
    if event == "prospective-impact":
        if not paths:
            return {"permissionDecision": "allow"}
        packet = _packet(_run(root, event, paths, correlation))
        if packet:
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": packet,
            }
        return {"permissionDecision": "allow"}
    if event == "pre-commit":
        tool_input = payload.get("toolArgs", payload.get("tool_input"))
        if not _is_git_commit(tool_input):
            return {"permissionDecision": "allow"}
        result = run_hook("pre-commit", root, correlation_id=correlation, deadline_ms=10_000)
        outcome = result.payload.get("outcome")
        packet = _packet([outcome] if isinstance(outcome, Mapping) else [])
        if result.exit_code:
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": packet or "Murlocs pre-commit gate failed.",
            }
        return {"permissionDecision": "allow"}
    if event == "pre-completion":
        paths = _changed_paths(root)
    outcomes = _run(root, event, paths, correlation)
    packet = _packet(outcomes)
    blocking = any(item.get("blocking") is True for item in outcomes)
    if event == "pre-completion":
        unavailable = not outcomes or outcomes[0].get("code") == "MURLOCS_ACTIVATION_UNAVAILABLE"
        if blocking or unavailable:
            return {
                "decision": "block",
                "reason": packet or "Murlocs completion evidence is unavailable.",
            }
        return {"decision": "allow"}
    return {"additionalContext": packet} if packet else {}


def main(argv: list[str] | None = None) -> None:
    """Entrypoint used by repository-local Copilot command hooks."""
    args = sys.argv[1:] if argv is None else argv
    events = {
        "task-start",
        "prospective-impact",
        "post-edit",
        "pre-commit",
        "pre-completion",
    }
    if len(args) != 1 or args[0] not in events:
        raise SystemExit("usage: murlocs-copilot-adapter EVENT")
    try:
        response = handle(args[0], _payload())
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        # Copilot documents non-zero hook exits as fail-open for these three events;
        # preserve that behavior while returning a visible decision packet when possible.
        if args[0] == "pre-completion":
            response = {"decision": "block", "reason": f"Murlocs adapter unavailable: {exc}"}
        else:
            response = {"additionalContext": f"Murlocs adapter unavailable: {exc}"}
    print(json.dumps(response, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
