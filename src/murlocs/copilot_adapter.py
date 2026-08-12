"""Repository-local GitHub Copilot lifecycle adapter.

The bridge deliberately accepts only Copilot's hook payload on stdin.  It never
accepts a repository root, freshness token, command, or authority decision from
the active agent.  Hooks invoke the installed Murlocs API directly, so no
repository-registered command is ever executed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from murlocs import __version__
from murlocs.adapter_conformance import (
    ADAPTER_CONTRACT,
    CONTINUOUS_SURFACES,
    EMPTY_CHANGE_SET,
    REQUIRED_CAPABILITIES,
    ConformanceContext,
    opaque_file_token,
)
from murlocs.cli import check_command, impact_command
from murlocs.hooks import run_hook
from murlocs.outcome import bind_integration_tokens, merge_outcomes

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


def _required_operations(event: str) -> tuple[str, ...]:
    """Return the lifecycle-v1 operation sequence shared by both host transports."""
    return {
        "task-start": ("check",),
        "prospective-impact": ("impact",),
        "post-edit": ("check", "impact"),
        "pre-commit": ("check", "impact"),
        "pre-completion": ("check", "impact"),
    }[event]


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
    candidates = [value.get(name) for name in ("path", "file", "filePath", "file_path")]
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


def _conformance_outcome(kind: str) -> dict[str, Any]:
    """Build one strict outcome used by the portable black-box fault scenarios."""
    base: dict[str, Any] = {
        "contract": "io.murlocs.outcome",
        "schema_version": 1,
        "code": "MURLOCS_OUTCOME_PASS",
        "status": "pass",
        "severity": "none",
        "blocking": False,
        "resolution_class": "pass",
        "source": {"operation": "check", "exit_code": 0, "murlocs_version": __version__},
        "correlation": {
            "correlation_id": "copilot-conformance",
            "state_id": None,
            "dependency_id": None,
            "token_source": "none",
            "token_scope": None,
        },
        "findings": [],
        "next_actions": [],
        "change": {"repository_state_changed": False, "paths": []},
        "silent": True,
        "summary": "Murlocs passed.",
    }
    if kind == "pass":
        return base
    specs = {
        "deterministic-repair": {
            "code": "MURLOCS_OUTCOME_DETERMINISTIC_REPAIR",
            "status": "blocking",
            "severity": "important",
            "blocking": True,
            "resolution": "deterministic_repair",
            "finding": "MURLOCS_CHECK_DRIFT",
            "source_code": "drift",
            "operation": "check",
            "action_id": "outcome.compile-managed-guidance",
            "action": "compile_managed_guidance",
            "effect": "write_managed_guidance",
            "authority": "integration",
        },
        "agent-action": {
            "code": "MURLOCS_OUTCOME_AGENT_ACTION",
            "status": "advisory",
            "severity": "advisory",
            "blocking": False,
            "resolution": "agent_action",
            "finding": "MURLOCS_IMPACT_REVIEW_RECOMMENDED",
            "source_code": "recommended",
            "operation": "impact",
            "action_id": "outcome.inspect-findings",
            "action": "inspect_findings",
            "effect": "read_repository",
            "authority": "agent",
        },
        "authority-required": {
            "code": "MURLOCS_OUTCOME_AUTHORITY_REQUIRED",
            "status": "advisory",
            "severity": "critical",
            "blocking": False,
            "resolution": "authority_required",
            "finding": "MURLOCS_IMPACT_REVIEW_REQUIRED",
            "source_code": "required",
            "operation": "impact",
            "action_id": "outcome.request-authority",
            "action": "request_authority",
            "effect": "request_authority",
            "authority": "human",
        },
    }
    spec = specs[kind]
    base.update(
        code=spec["code"],
        status=spec["status"],
        severity=spec["severity"],
        blocking=spec["blocking"],
        resolution_class=spec["resolution"],
        silent=False,
        summary=f"Murlocs produced {spec['resolution']}.",
    )
    base["source"] = {
        "operation": spec["operation"],
        "exit_code": 1 if kind == "deterministic-repair" else 0,
        "murlocs_version": __version__,
    }
    arguments = {
        "codes": [spec["finding"]],
        "scopes": ["root"],
        "maps": ["AGENTS.md"],
        "owners": ["@owner"] if kind == "authority-required" else [],
    }
    base["findings"] = [
        {
            "code": spec["finding"],
            "status": spec["status"],
            "severity": spec["severity"],
            "message": base["summary"],
            "evidence": [
                {
                    "kind": "diagnostic" if spec["operation"] == "check" else "reason",
                    "reference": spec["source_code"],
                    "detail": base["summary"],
                }
            ],
            "provenance": {
                "operation": spec["operation"],
                "source_codes": [spec["source_code"]],
                "source_paths": [".murlocs/manifest.toml"],
            },
            "affected": {
                "scopes": ["root"],
                "maps": ["AGENTS.md"],
                "owners": arguments["owners"],
            },
            "resolution_class": spec["resolution"],
            "action_ids": [spec["action_id"]],
        }
    ]
    base["next_actions"] = [
        {
            "id": spec["action_id"],
            "operation": spec["action"],
            "arguments": arguments,
            "effect": spec["effect"],
            "authority": spec["authority"],
        }
    ]
    return base


class LifecycleAdapterDriver:
    """Shared production-driver architecture for independent host transports.

    ``ConformanceContext`` supplies only black-box fault seams and trusted root
    state. The active-agent request never supplies either value.
    """

    adapter_id: str
    adapter_version: str
    conformance_name: str
    host_name: str

    def descriptor(self) -> Mapping[str, Any]:
        raise NotImplementedError

    def invoke(self, request: Mapping[str, Any], context: ConformanceContext) -> Mapping[str, Any]:
        control = context.control
        fault = control["fault"]
        event = request["event"]
        root = self._root(control["root_format"], context.root)
        scope = {
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "session_id": f"{self.conformance_name}-conformance-session",
        }
        state_before = opaque_file_token(
            context.root, exclude=(".adapter-conformance/impact-dependency",)
        )
        dependency_before = self._dependency_token(context.root)
        calls = self._calls(event, fault, control["cache"])
        trusted: dict[str, Any] = {
            "root": root,
            "manifest": ".murlocs/manifest.toml",
            "view": "index" if event == "pre-commit" else "worktree",
            "token_scope": scope,
            "state_id": state_before,
        }
        if control.get("change_set") == EMPTY_CHANGE_SET:
            return {
                "trusted_context": trusted,
                "response": self._empty_change_set_response(
                    request, trusted, cache=control["cache"]
                ),
            }
        if "impact" in calls:
            trusted["impact_dependency_id"] = dependency_before
        if control["cache"] in {"hit", "rejected"}:
            trusted["manifest_identity"] = f"manifest:{self.conformance_name}-conformance"
            proof = self._cache_proof(request, trusted)
            if control["cache"] == "rejected":
                proof["state_id"] = "copilot:stale"
            trusted["cache_offer"] = {
                "cache_id": f"cache:{context.scenario_id}",
                "proof": proof,
            }
        if fault in {"agent-token", "unsupported-version"}:
            return {
                "trusted_context": trusted,
                "response": self._failure(
                    request,
                    trusted,
                    status="invalid",
                    cache="forbidden" if fault == "agent-token" else "miss",
                    fallback=(
                        ["generated-guidance", "git-hook", "ci"] if fault == "agent-token" else []
                    ),
                ),
            }
        if fault == "absent":
            return {
                "trusted_context": trusted,
                "response": self._failure(
                    request,
                    trusted,
                    status="not_applicable",
                    cache="miss",
                    fallback=[],
                    silent=True,
                ),
            }
        if fault == "unavailable":
            return {
                "trusted_context": trusted,
                "response": self._failure(
                    request,
                    trusted,
                    status="unavailable",
                    cache="forbidden",
                    fallback=["generated-guidance", "git-hook", "ci"],
                ),
            }

        for operation in calls:
            context.record_operation(operation)
            if fault == "timeout":
                return {
                    "trusted_context": trusted,
                    "response": self._failure(
                        request,
                        trusted,
                        status="timeout",
                        cache=control["cache"],
                        fallback=[],
                        next_actions=[self._retry_action(event)],
                    ),
                }
            if fault == "malformed":
                return {
                    "trusted_context": trusted,
                    "response": self._failure(
                        request,
                        trusted,
                        status="invalid",
                        cache=control["cache"],
                        fallback=["generated-guidance", "git-hook", "ci"],
                    ),
                }
        if calls:
            context.checkpoint(f"after-{calls[-1]}")
        state_after = opaque_file_token(
            context.root, exclude=(".adapter-conformance/impact-dependency",)
        )
        dependency_after = self._dependency_token(context.root)
        if fault in {"state-race", "dependency-race"}:
            execution: dict[str, Any] = {
                "status": "stale",
                "code": "MURLOCS_ACTIVATION_STALE",
                "observed_state_id": state_after,
            }
            if "impact" in calls:
                execution["observed_dependency_id"] = dependency_after
            response = self._base_response(
                request,
                trusted,
                execution=execution,
                operations=[],
                cache=control["cache"],
                outcome=None,
                silent=False,
                fallback=["git-hook", "ci"],
                next_actions=[self._fallback_action("git-hook")],
            )
            return {"trusted_context": trusted, "response": response}

        receipts = [
            self._receipt(
                operation,
                state_before,
                dependency_before if operation == "impact" else None,
            )
            for operation in _required_operations(event)
        ]
        outcome = self._bound_outcome(
            control["outcome"],
            request,
            state_before,
            dependency_before if "impact" in _required_operations(event) else None,
            scope,
            _required_operations(event),
        )
        resolution = None if outcome is None else outcome["resolution_class"]
        response = self._base_response(
            request,
            trusted,
            execution={"status": "completed", "code": "MURLOCS_ACTIVATION_OK"},
            operations=receipts,
            cache=control["cache"],
            outcome=outcome,
            silent=resolution == "pass",
            fallback=[],
            next_actions=[],
            blocking=(
                resolution == "deterministic_repair" and request["event"] not in CONTINUOUS_SURFACES
            ),
        )
        return {"trusted_context": trusted, "response": response}

    @classmethod
    def _empty_change_set_response(
        cls,
        request: Mapping[str, Any],
        trusted: Mapping[str, Any],
        *,
        cache: str,
    ) -> dict[str, Any]:
        return cls._base_response(
            request,
            trusted,
            execution={"status": "no_changed_paths", "code": "MURLOCS_NO_CHANGED_PATHS"},
            operations=[],
            cache=cache,
            outcome=None,
            silent=True,
            fallback=[],
            next_actions=[],
            blocking=False,
        )

    @staticmethod
    def _calls(event: str, fault: str | None, cache: str) -> tuple[str, ...]:
        if cache == "hit" or fault in {
            "absent",
            "unavailable",
            "agent-token",
            "unsupported-version",
        }:
            return ()
        operations = _required_operations(event)
        return operations[:1] if fault in {"timeout", "malformed"} else operations

    @classmethod
    def _dependency_token(cls, root: Path) -> str:
        content = (root / ".adapter-conformance/impact-dependency").read_bytes()
        return f"{cls.conformance_name}-dependency:" + hashlib.sha256(content).hexdigest()

    @classmethod
    def _root(cls, kind: str, root: Path) -> dict[str, Any]:
        if kind == "windows-drive":
            return {"format": "windows-drive", "drive": "C", "segments": [root.name]}
        if kind == "windows-unc":
            return {
                "format": "windows-unc",
                "server": f"{cls.conformance_name}-server",
                "share": f"{cls.conformance_name}-share",
                "segments": [root.name],
            }
        return {"format": "posix", "segments": [root.name]}

    @classmethod
    def _cache_proof(cls, request: Mapping[str, Any], trusted: Mapping[str, Any]) -> dict[str, Any]:
        operations = list(_required_operations(request["event"]))
        proof: dict[str, Any] = {
            "contract_version": "1",
            "adapter_id": cls.adapter_id,
            "adapter_version": cls.adapter_version,
            "session_id": trusted["token_scope"]["session_id"],
            "event": request["event"],
            "operations": operations,
            "operation_inputs": {
                field: request[field]
                for field in ("paths", "baseline", "enforcement")
                if field in request
            },
            "manifest_identity": trusted["manifest_identity"],
            "state_id": trusted["state_id"],
        }
        if "impact" in operations:
            proof["impact_dependency_id"] = trusted["impact_dependency_id"]
        return proof

    @staticmethod
    def _receipt(operation: str, state: str, dependency: str | None) -> dict[str, Any]:
        receipt: dict[str, Any] = {
            "operation": operation,
            "exit_code": 0,
            "output_sha256": "sha256:" + "a" * 64,
            "state_before": state,
            "state_after": state,
        }
        if dependency is not None:
            receipt.update(
                dependency_before_id=dependency,
                dependency_after_id=dependency,
            )
        return receipt

    @staticmethod
    def _bound_outcome(
        outcome_id: str | None,
        request: Mapping[str, Any],
        state: str,
        dependency: str | None,
        scope: Mapping[str, Any],
        operations: tuple[str, ...],
    ) -> dict[str, Any] | None:
        if outcome_id is None:
            return None
        template = copy.deepcopy(_conformance_outcome(outcome_id))
        template["source"]["operation"] = operations[0]
        template["correlation"]["correlation_id"] = request["correlation_id"]
        bound = bind_integration_tokens(
            template,
            correlation_id=request["correlation_id"],
            state_id=state,
            dependency_id=dependency if operations[0] == "impact" else None,
            token_scope=scope,
        )
        if len(operations) == 1:
            return bound
        impact = _conformance_outcome("pass")
        impact["source"]["operation"] = "impact"
        impact["correlation"]["correlation_id"] = request["correlation_id"]
        bound_impact = bind_integration_tokens(
            impact,
            correlation_id=request["correlation_id"],
            state_id=state,
            dependency_id=dependency,
            token_scope=scope,
        )
        return merge_outcomes([bound, bound_impact])

    @classmethod
    def _base_response(
        cls,
        request: Mapping[str, Any],
        trusted: Mapping[str, Any],
        *,
        execution: Mapping[str, Any],
        operations: list[dict[str, Any]],
        cache: str,
        outcome: Mapping[str, Any] | None,
        silent: bool,
        fallback: list[str],
        next_actions: list[dict[str, Any]],
        blocking: bool | None = False,
    ) -> dict[str, Any]:
        return {
            "contract": request["contract"],
            "schema_version": request["schema_version"],
            "event": request["event"],
            "correlation_id": request["correlation_id"],
            "execution": dict(execution),
            "repository": {
                "root": trusted["root"],
                "token_scope": trusted["token_scope"],
                "manifest": trusted["manifest"],
                "view": trusted["view"],
                "state_id": trusted["state_id"],
                "blocking": blocking,
            },
            "silent": silent,
            "operations": operations,
            "cache": {
                "decision": cache,
                **(
                    {"key": trusted["cache_offer"]["cache_id"]}
                    if cache in {"hit", "rejected"}
                    else {}
                ),
            },
            "outcome": outcome,
            "writes": [],
            "fallback": fallback,
            "next_actions": next_actions,
            "summary": f"{cls.host_name} adapter {execution['status']}.",
        }

    def _failure(
        self,
        request: Mapping[str, Any],
        trusted: Mapping[str, Any],
        *,
        status: str,
        cache: str,
        fallback: list[str],
        silent: bool = False,
        next_actions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return self._base_response(
            request,
            trusted,
            execution={
                "status": status,
                "code": {
                    "not_applicable": "MURLOCS_ACTIVATION_ABSENT",
                    "unavailable": "MURLOCS_ACTIVATION_UNAVAILABLE",
                    "timeout": "MURLOCS_ACTIVATION_TIMEOUT",
                    "invalid": "MURLOCS_ACTIVATION_INVALID",
                }[status],
            },
            operations=[],
            cache=cache,
            outcome=None,
            silent=silent,
            fallback=fallback,
            next_actions=next_actions or ([self._fallback_action(fallback[0])] if fallback else []),
            blocking=None,
        )

    @staticmethod
    def _fallback_action(name: str) -> dict[str, Any]:
        return {
            "operation": "use_fallback",
            "arguments": {"fallback": name},
            "effect": "read_repository",
            "authority": "integration",
        }

    @staticmethod
    def _retry_action(event: str) -> dict[str, Any]:
        return {
            "operation": "retry",
            "arguments": {"event": event},
            "effect": "read_repository",
            "authority": "integration",
        }


class CopilotAdapterDriver(LifecycleAdapterDriver):
    """Portable production driver with Copilot's trusted adapter identity."""

    adapter_id = ADAPTER_ID
    adapter_version = ADAPTER_VERSION
    conformance_name = "copilot"
    host_name = "Copilot"

    def descriptor(self) -> Mapping[str, Any]:
        return descriptor()


def _run(root: Path, event: str, paths: Sequence[str], correlation: str) -> list[Mapping[str, Any]]:
    """Run the typed, read-only operations required by the lifecycle event."""
    checks: list[Mapping[str, Any]] = []
    for operation in _required_operations(event):
        if operation == "check":
            checks.append(_outcome(check_command(repo=str(root), correlation_id=correlation)))
            continue
        if not paths:
            return []
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
    tool_input = payload.get("toolArgs", payload.get("tool_input"))
    paths = _paths(root, tool_input)
    if event == "prospective-impact":
        if not paths:
            if _has_path_candidate(tool_input):
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "Murlocs rejected an edit path outside the repository boundary."
                    ),
                }
            return {"permissionDecision": "allow"}
        packet = _packet(_run(root, event, paths, correlation))
        if packet:
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": packet,
            }
        return {"permissionDecision": "allow"}
    if event == "pre-commit":
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
        if not paths:
            return {"decision": "allow"}
        outcomes = _run(root, event, paths, correlation)
        packet = _packet(outcomes)
        if packet:
            return {"decision": "allow", "reason": packet}
        return {"decision": "allow"}
    outcomes = _run(root, event, paths, correlation)
    packet = _packet(outcomes)
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
        # Copilot owns failure policy: command preToolUse errors deny, while errors
        # on the other configured events are logged and skipped. Host timeouts are
        # fail-open for every command hook, including preToolUse.
        print(f"Murlocs adapter unavailable: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(response, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
