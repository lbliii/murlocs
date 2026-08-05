"""Passive Git-hook integration for exact staged and outgoing views."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from murlocs import __version__
from murlocs.errors import MurlocsError
from murlocs.gitview import (
    MAX_PRE_PUSH_BYTES,
    Deadline,
    GitContext,
    GitSnapshot,
    HookTimeout,
    capture_commit,
    capture_head,
    capture_index,
    changed_paths,
    discover_git,
    impact_dependency_id,
    manifest_mode,
    materialize,
    resolve_commit,
    run_git,
)
from murlocs.identity import RuntimeIdentity
from murlocs.manifest import load_manifest
from murlocs.outcome import (
    bind_integration_tokens,
    merge_outcomes,
    render_compact_outcome,
    validate_correlation_id,
)

HookEvent = Literal["pre-commit", "pre-push"]
HOOK_CONTRACT = "io.murlocs.activation"
HOOK_SCHEMA_VERSION = 1
HOOK_ADAPTER_ID = "murlocs-git-hook"
HOOK_ADAPTER_VERSION = "1"
HOOK_MARKER = "# Managed by Murlocs hook integration v1."
_RUNNER_PREFIX = "# Murlocs runner: "
_REF = re.compile(rb"(?:refs/[!-~]+|HEAD)")
_BUILD_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_MAX_RUNNER_BYTES = 4 * 1024 * 1024
_MAX_RUNNER_IDENTITY_BYTES = 16 * 1024


@dataclass(frozen=True)
class HookResult:
    payload: dict[str, Any]
    exit_code: int
    terminal_text: str


@dataclass(frozen=True)
class PushUpdate:
    local_ref: str
    local_oid: str
    remote_ref: str
    remote_oid: str


@dataclass(frozen=True)
class HookRunner:
    """A verified executable retained by a byte-owned dispatcher."""

    path: Path
    version: str
    build_id: str | None = None
    sha256: str | None = None

    @property
    def has_v2_identity(self) -> bool:
        return self.build_id is not None and self.sha256 is not None


def run_hook(
    event: HookEvent,
    root: Path,
    *,
    correlation_id: str | None = None,
    deadline_ms: int = 10_000,
    pre_push_input: bytes = b"",
    explicit_paths: tuple[str, ...] = (),
    expected_build_id: str | None = None,
) -> HookResult:
    """Run a hook against raw Git data under one total fail-closed deadline."""
    try:
        correlation_id = validate_correlation_id(correlation_id)
        _validate_expected_build_id(expected_build_id)
        if expected_build_id is not None:
            from murlocs.identity import runtime_identity

            actual_build_id = runtime_identity()["build"]["id"]
            if actual_build_id != expected_build_id:
                return _failed(
                    event,
                    correlation_id,
                    "invalid",
                    "Murlocs hook runner build identity changed; "
                    "run 'murlocs hook install' to repair it.",
                    root,
                )
    except MurlocsError as exc:
        return _failed(event, None, "invalid", str(exc), root)
    try:
        deadline = Deadline.start(deadline_ms)
        context = discover_git(root, deadline)
        if event == "pre-commit":
            after = capture_index(context, deadline)
            before = capture_head(context, deadline)
            paths = changed_paths(before, after)
            _validate_explicit_paths(explicit_paths, paths)
            correlation = correlation_id or _adapter_correlation(event, after.state_id)
            return _run_snapshot(
                context,
                after,
                paths,
                prior_manifest_present=(before is not None and manifest_mode(before) is not None),
                event=event,
                correlation_id=correlation,
                deadline=deadline,
                cache_decision="miss",
            )
        updates = parse_pre_push(pre_push_input, context.object_format)
        correlation = correlation_id or _adapter_correlation(
            event, hashlib.sha256(pre_push_input).hexdigest()
        )
        return _run_pre_push(context, updates, correlation, deadline)
    except HookTimeout as exc:
        return _failed(event, correlation_id, "timeout", str(exc), root)
    except (MurlocsError, UnicodeError, OSError, ValueError) as exc:
        return _failed(event, correlation_id, "invalid", str(exc), root)


def parse_pre_push(raw: bytes, object_format: str) -> tuple[PushUpdate, ...]:
    """Parse bounded pre-push stdin strictly, retaining values only as data."""
    if len(raw) > MAX_PRE_PUSH_BYTES:
        raise MurlocsError("pre-push input exceeds 1 MiB")
    oid_size = 40 if object_format == "sha1" else 64
    updates: list[PushUpdate] = []
    for line in raw.splitlines():
        fields = line.split(b" ")
        if len(fields) != 4 or any(not field for field in fields):
            raise MurlocsError("pre-push input is malformed")
        local_ref, local_oid, remote_ref, remote_oid = fields
        if _REF.fullmatch(local_ref) is None or _REF.fullmatch(remote_ref) is None:
            raise MurlocsError("pre-push input contains an invalid ref")
        for oid in (local_oid, remote_oid):
            if len(oid) != oid_size or re.fullmatch(rb"[0-9a-f]+", oid) is None:
                raise MurlocsError("pre-push input contains an invalid object id")
        updates.append(
            PushUpdate(
                local_ref.decode("ascii"),
                local_oid.decode("ascii"),
                remote_ref.decode("ascii"),
                remote_oid.decode("ascii"),
            )
        )
    return tuple(updates)


def install_hooks(
    root: Path,
    events: tuple[HookEvent, ...],
    *,
    runner: str | None = None,
) -> dict[str, Any]:
    """Install only into absent or exactly Murlocs-owned default hook slots."""
    context, hooks = _installation_context(root)
    selected = _normalized_events(events)
    states = {event: _hook_state(hooks / event, event) for event in selected}
    conflicts = [
        event for event, state_name in states.items() if state_name in {"modified", "occupied"}
    ]
    if conflicts:
        raise MurlocsError("refusing to replace existing Git hook(s): " + ", ".join(conflicts))
    # A byte-owned dispatcher may be left untouched even if its runner has moved;
    # this preserves idempotency and lets status describe the repair needed.
    if all(states[event] == "installed" for event in selected) and runner is None:
        return {
            "ok": True,
            "git_dir": str(context.git_dir),
            "events": list(selected),
            "changed": [],
        }
    resolved_runner = _resolve_runner(runner)
    hooks.mkdir(parents=True, exist_ok=True)
    changed: list[str] = []
    for event in selected:
        target = hooks / event
        expected = _hook_bytes(event, resolved_runner)
        if target.exists() and target.read_bytes() == expected:
            continue
        if target.exists():
            _replace_owned_hook(target, expected, event)
        else:
            _atomic_hook_write(target, expected)
        changed.append(event)
    return {
        "ok": True,
        "git_dir": str(context.git_dir),
        "events": list(selected),
        "changed": changed,
    }


def uninstall_hooks(root: Path, events: tuple[HookEvent, ...]) -> dict[str, Any]:
    """Remove exact Murlocs-owned hook bytes and preserve every other file."""
    context, hooks = _installation_context(root)
    selected = _normalized_events(events)
    conflicts = []
    owned: dict[HookEvent, bool] = {}
    for event in selected:
        target = hooks / event
        try:
            content = target.read_bytes()
            owned[event] = target.is_file() and _is_owned_hook(content, event)
        except OSError:
            owned[event] = False
        if target.exists() and not owned[event]:
            conflicts.append(event)
    if conflicts:
        raise MurlocsError("refusing to remove modified Git hook(s): " + ", ".join(conflicts))
    changed: list[str] = []
    for event in selected:
        target = hooks / event
        if owned[event]:
            target.unlink()
            changed.append(event)
    return {
        "ok": True,
        "git_dir": str(context.git_dir),
        "events": list(selected),
        "changed": changed,
    }


def hook_status(root: Path) -> dict[str, Any]:
    """Report exact ownership without modifying Git configuration or hook files."""
    context, hooks = _installation_context(root)
    states = {event: _hook_state(hooks / event, event) for event in _all_events()}
    return {
        "ok": True,
        "git_dir": str(context.git_dir),
        "hooks_dir": str(hooks),
        "events": states,
    }


def _run_pre_push(
    context: GitContext,
    updates: tuple[PushUpdate, ...],
    correlation_id: str,
    deadline: Deadline,
) -> HookResult:
    zero = "0" * (40 if context.object_format == "sha1" else 64)
    results: list[dict[str, Any]] = []
    messages: list[str] = []
    exit_code = 0
    for index, update in enumerate(updates):
        if update.local_oid == zero:
            continue
        commit = resolve_commit(context, update.local_oid, deadline)
        after = capture_commit(context, commit, deadline)
        before = None
        if update.remote_oid != zero:
            old = resolve_commit(context, update.remote_oid, deadline)
            before = capture_commit(context, old, deadline)
        result = _run_snapshot(
            context,
            after,
            changed_paths(before, after),
            prior_manifest_present=(before is not None and manifest_mode(before) is not None),
            event="pre-completion",
            correlation_id=f"{correlation_id[:112]}:{index}",
            deadline=deadline,
            cache_decision="forbidden",
        )
        results.append(result.payload)
        if result.terminal_text:
            messages.append(result.terminal_text)
        exit_code = max(exit_code, result.exit_code)
    payload = {
        "contract": "io.murlocs.hook-batch",
        "schema_version": 1,
        "event": "pre-push",
        "correlation_id": correlation_id,
        "results": results,
    }
    return HookResult(payload, exit_code, "\n".join(messages))


def _run_snapshot(
    context: GitContext,
    snapshot: GitSnapshot,
    paths: tuple[str, ...],
    *,
    prior_manifest_present: bool,
    event: Literal["pre-commit", "pre-completion"],
    correlation_id: str,
    deadline: Deadline,
    cache_decision: Literal["miss", "forbidden"],
) -> HookResult:
    mode = manifest_mode(snapshot)
    if mode is None:
        if prior_manifest_present:
            return _snapshot_failure(
                context,
                snapshot,
                event,
                correlation_id,
                cache_decision,
                "invalid",
                "Removing Murlocs activation requires explicit repository authority.",
            )
        if any(entry.path_bytes.startswith(b".murlocs/") for entry in snapshot.entries):
            return _snapshot_failure(
                context,
                snapshot,
                event,
                correlation_id,
                cache_decision,
                "invalid",
                "Murlocs metadata is incomplete: .murlocs/manifest.toml is absent.",
            )
        return _absent(context, snapshot, event, correlation_id, cache_decision)
    if mode not in {"100644", "100755"}:
        return _snapshot_failure(
            context,
            snapshot,
            event,
            correlation_id,
            cache_decision,
            "invalid",
            "Murlocs manifest is not a regular file in the selected Git view.",
        )
    if not paths:
        return _snapshot_failure(
            context,
            snapshot,
            event,
            correlation_id,
            cache_decision,
            "invalid",
            "Murlocs hook received no changed paths to assess.",
        )

    state_before = snapshot.state_id
    with tempfile.TemporaryDirectory(prefix="murlocs-hook-") as temporary:
        view = Path(temporary) / "view"
        metrics = materialize(context, snapshot, view, deadline)
        manifest = load_manifest(view)
        sources = tuple(source.path for source in manifest.sources)

        check_payload, check_exit, check_bytes = _run_operation(
            "check", view, correlation_id, (), deadline
        )
        deadline.check()
        check_after = _recapture(context, snapshot, deadline)
        if check_after.state_id != state_before:
            return _snapshot_failure(
                context,
                snapshot,
                event,
                correlation_id,
                cache_decision,
                "stale",
                "Repository state changed while Murlocs was checking the Git view.",
            )
        dependency_before = impact_dependency_id(context, sources, deadline)
        impact_payload, impact_exit, impact_bytes = _run_operation(
            "impact", view, correlation_id, paths, deadline
        )
        deadline.check()
        impact_after = _recapture(context, snapshot, deadline)
        dependency_after = impact_dependency_id(context, sources, deadline)
    if impact_after.state_id != state_before or dependency_after != dependency_before:
        return _snapshot_failure(
            context,
            snapshot,
            event,
            correlation_id,
            cache_decision,
            "stale",
            "Repository state changed while Murlocs was assessing the Git view.",
        )

    if impact_exit != 0 or check_exit not in {0, 1}:
        failed_payload = impact_payload if impact_exit != 0 else check_payload
        return _snapshot_failure(
            context,
            snapshot,
            event,
            correlation_id,
            cache_decision,
            "invalid",
            _operation_error(failed_payload),
        )
    token_scope = {
        "adapter_id": HOOK_ADAPTER_ID,
        "adapter_version": HOOK_ADAPTER_VERSION,
        "session_id": correlation_id,
    }
    check_outcome = bind_integration_tokens(
        check_payload["outcome"],
        correlation_id=correlation_id,
        state_id=state_before,
        token_scope=token_scope,
    )
    impact_outcome = bind_integration_tokens(
        impact_payload["outcome"],
        correlation_id=correlation_id,
        state_id=state_before,
        dependency_id=dependency_before,
        token_scope=token_scope,
    )
    outcome = merge_outcomes([check_outcome, impact_outcome])
    operations = [
        _receipt("check", check_exit, check_bytes, state_before),
        _receipt(
            "impact",
            impact_exit,
            impact_bytes,
            state_before,
            dependency_id=dependency_before,
        ),
    ]
    blocking = bool(check_payload.get("ok") is not True)
    payload = _response_base(
        context,
        snapshot,
        event,
        correlation_id,
        cache_decision,
        blocking=blocking,
    )
    payload.update(
        {
            "execution": {"status": "completed", "code": "MURLOCS_ACTIVATION_OK"},
            "silent": not blocking,
            "operations": operations,
            "outcome": outcome,
            "summary": render_compact_outcome(outcome) or outcome["summary"],
            "metrics": {
                **metrics,
                "git_subprocesses": deadline.git_calls,
                "changed_paths": len(paths),
                "impact_dependency_probes": 2,
                "operation_subprocesses": 2,
            },
        }
    )
    text = ""
    if blocking:
        text = render_compact_outcome(outcome)
    return HookResult(payload, 1 if blocking else 0, text)


def _recapture(context: GitContext, snapshot: GitSnapshot, deadline: Deadline) -> GitSnapshot:
    if snapshot.view == "index":
        return capture_index(context, deadline)
    return capture_commit(context, snapshot.object_id or "", deadline)


def _receipt(
    operation: str,
    exit_code: int,
    output_bytes: bytes,
    state_id: str,
    *,
    dependency_id: str | None = None,
) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "operation": operation,
        "exit_code": exit_code,
        "output_sha256": "sha256:" + hashlib.sha256(output_bytes).hexdigest(),
        "state_before": state_id,
        "state_after": state_id,
    }
    if dependency_id is not None:
        receipt["dependency_before_id"] = dependency_id
        receipt["dependency_after_id"] = dependency_id
    return receipt


def _run_operation(
    operation: Literal["check", "impact"],
    view: Path,
    correlation_id: str,
    paths: tuple[str, ...],
    deadline: Deadline,
) -> tuple[dict[str, Any], int, bytes]:
    package_root = Path(__file__).resolve().parents[1]
    bootstrap = (
        "import sys;sys.path.insert(0,sys.argv[1]);from murlocs.cli import main;main(sys.argv[2:])"
    )
    argv = [
        sys.executable,
        "-I",
        "-c",
        bootstrap,
        str(package_root),
        operation,
        "--repo",
        str(view),
        "--correlation-id",
        correlation_id,
        "--format",
        "json",
    ]
    if operation == "impact":
        argv.extend(f"--path={path}" for path in paths)
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            timeout=deadline.remaining_seconds(),
        )
    except subprocess.TimeoutExpired as exc:
        raise HookTimeout(f"Murlocs {operation} operation timed out") from exc
    except OSError as exc:
        raise MurlocsError(f"could not run Murlocs {operation}: {exc}") from exc
    if not completed.stdout:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise MurlocsError(detail or f"Murlocs {operation} returned no structured output")
    try:
        payload = json.loads(completed.stdout, object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError, MurlocsError) as exc:
        raise MurlocsError(f"Murlocs {operation} output is invalid: {exc}") from exc
    if not isinstance(payload, dict):
        raise MurlocsError(f"Murlocs {operation} output must be an object")
    return payload, completed.returncode, completed.stdout


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MurlocsError(f"duplicate structured output member: {key}")
        result[key] = value
    return result


def _operation_error(payload: Mapping[str, Any]) -> str:
    error = payload.get("error")
    if isinstance(error, Mapping):
        message = error.get("message")
        if isinstance(message, str) and message:
            return message
    return "Murlocs operations returned an unsupported exit status."


def _response_base(
    context: GitContext,
    snapshot: GitSnapshot,
    event: str,
    correlation_id: str,
    cache_decision: str,
    *,
    blocking: bool | None,
) -> dict[str, Any]:
    return {
        "contract": HOOK_CONTRACT,
        "schema_version": HOOK_SCHEMA_VERSION,
        "event": event,
        "correlation_id": correlation_id,
        "repository": {
            "root": _portable_root(context.root),
            "token_scope": {
                "adapter_id": HOOK_ADAPTER_ID,
                "adapter_version": HOOK_ADAPTER_VERSION,
                "session_id": correlation_id,
            },
            "manifest": ".murlocs/manifest.toml",
            "view": snapshot.view,
            "state_id": snapshot.state_id,
            "blocking": blocking,
        },
        "cache": {"decision": cache_decision},
        "writes": [],
        "fallback": [],
        "next_actions": [],
    }


def _absent(
    context: GitContext,
    snapshot: GitSnapshot,
    event: str,
    correlation_id: str,
    cache_decision: str,
) -> HookResult:
    payload = _response_base(
        context, snapshot, event, correlation_id, cache_decision, blocking=None
    )
    payload.update(
        {
            "execution": {
                "status": "not_applicable",
                "code": "MURLOCS_ACTIVATION_ABSENT",
            },
            "silent": True,
            "operations": [],
            "outcome": None,
            "summary": "Murlocs is not present.",
        }
    )
    return HookResult(payload, 0, "")


def _snapshot_failure(
    context: GitContext,
    snapshot: GitSnapshot,
    event: str,
    correlation_id: str,
    cache_decision: str,
    status_name: Literal["invalid", "stale"],
    message: str,
) -> HookResult:
    payload = _response_base(
        context, snapshot, event, correlation_id, cache_decision, blocking=None
    )
    payload.update(
        {
            "execution": {
                "status": status_name,
                "code": f"MURLOCS_ACTIVATION_{status_name.upper()}",
            },
            "silent": False,
            "operations": [],
            "outcome": None,
            "summary": message,
        }
    )
    return HookResult(payload, 1, message)


def _failed(
    event: str,
    correlation_id: str | None,
    status_name: Literal["timeout", "invalid"],
    message: str,
    root: Path,
) -> HookResult:
    correlation = correlation_id or "git-hook:unavailable"
    payload = {
        "contract": HOOK_CONTRACT,
        "schema_version": HOOK_SCHEMA_VERSION,
        "event": event,
        "correlation_id": correlation,
        "execution": {
            "status": status_name,
            "code": f"MURLOCS_ACTIVATION_{status_name.upper()}",
        },
        "repository": {
            "root": _portable_root(root.resolve()),
            "token_scope": {
                "adapter_id": HOOK_ADAPTER_ID,
                "adapter_version": HOOK_ADAPTER_VERSION,
                "session_id": correlation,
            },
            "manifest": ".murlocs/manifest.toml",
            "view": "index" if event == "pre-commit" else "commit",
            "state_id": "unavailable",
            "blocking": None,
        },
        "silent": False,
        "operations": [],
        "cache": {"decision": "miss" if event == "pre-commit" else "forbidden"},
        "outcome": None,
        "writes": [],
        "fallback": [],
        "next_actions": [],
        "summary": message,
    }
    return HookResult(payload, 1, message)


def _installation_context(root: Path) -> tuple[GitContext, Path]:
    deadline = Deadline.start(10_000)
    context = discover_git(root, deadline)
    configured = run_git(
        deadline,
        context.root,
        ["config", "--local", "--get", "core.hooksPath"],
        allow_failure=True,
    )
    if configured.returncode == 0:
        raise MurlocsError(
            "core.hooksPath is configured; use the documented manager snippet instead"
        )
    if configured.returncode not in {0, 1}:
        raise MurlocsError("could not inspect core.hooksPath")
    if context.git_dir != context.common_dir:
        raise MurlocsError(
            "linked worktree hook installation is not automatic; use a manager snippet"
        )
    hooks = context.common_dir / "hooks"
    if hooks.is_symlink() or (hooks.exists() and not hooks.is_dir()):
        raise MurlocsError("default Git hooks directory is not a regular directory")
    return context, hooks


def _hook_bytes(event: HookEvent, runner: HookRunner) -> bytes:
    """Render the current v2 byte-owned dispatcher."""
    if not runner.has_v2_identity:
        raise ValueError("v2 hook dispatcher requires a complete runner identity")
    quoted_runner = shlex.quote(str(runner.path))
    quoted_build_id = shlex.quote(runner.build_id)
    command = (
        f"exec {quoted_runner} hook run pre-commit --expected-build-id={quoted_build_id}\n"
        if event == "pre-commit"
        else (
            f'exec {quoted_runner} hook run pre-push --remote-name="$1" --remote-url="$2" '
            f"--expected-build-id={quoted_build_id}\n"
        )
    )
    metadata = json.dumps(
        {
            "path": str(runner.path),
            "version": runner.version,
            "build_id": runner.build_id,
            "sha256": runner.sha256,
        },
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return (
        "#!/bin/sh\n"
        f"{HOOK_MARKER}\n"
        f"{_RUNNER_PREFIX}{metadata}\n"
        f"if [ ! -x {quoted_runner} ]; then\n"
        "  echo \"Murlocs hook runner is missing; run 'murlocs hook install' to repair it.\" >&2\n"
        "  exit 1\n"
        "fi\n"
        f"{command}"
    ).encode()


def _v1_hook_bytes(event: HookEvent, runner: HookRunner) -> bytes:
    """Render the exact pinned-runner dispatcher emitted before build identity."""
    quoted_runner = shlex.quote(str(runner.path))
    command = (
        f"exec {quoted_runner} hook run pre-commit\n"
        if event == "pre-commit"
        else f'exec {quoted_runner} hook run pre-push --remote-name="$1" --remote-url="$2"\n'
    )
    metadata = json.dumps(
        {"path": str(runner.path), "version": runner.version},
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return (
        "#!/bin/sh\n"
        f"{HOOK_MARKER}\n"
        f"{_RUNNER_PREFIX}{metadata}\n"
        f"if [ ! -x {quoted_runner} ]; then\n"
        "  echo \"Murlocs hook runner is missing; run 'murlocs hook install' to repair it.\" >&2\n"
        "  exit 1\n"
        "fi\n"
        f"{command}"
    ).encode()


def _legacy_hook_bytes(event: HookEvent) -> bytes:
    """Return the exact v1 dispatcher emitted before runner pinning existed."""
    command = (
        "exec murlocs hook run pre-commit\n"
        if event == "pre-commit"
        else 'exec murlocs hook run pre-push --remote-name="$1" --remote-url="$2"\n'
    )
    return f"#!/bin/sh\n{HOOK_MARKER}\n{command}".encode()


def _hook_state(path: Path, event: HookEvent) -> str:
    if path.is_symlink():
        return "occupied"
    if not path.exists():
        return "absent"
    try:
        if not path.is_file():
            return "occupied"
        content = path.read_bytes()
        runner = _owned_runner(content, event)
        if runner is None:
            if content == _legacy_hook_bytes(event):
                return "legacy"
            return "modified" if _has_murlocs_marker(content) else "occupied"
        return _runner_state(runner)
    except OSError:
        return "occupied"


def _owned_runner(content: bytes, event: HookEvent) -> HookRunner | None:
    """Return runner metadata only when every managed byte is still exact."""
    try:
        lines = content.decode("utf-8").splitlines()
        if len(lines) < 3 or lines[0] != "#!/bin/sh" or lines[1] != HOOK_MARKER:
            return None
        if not lines[2].startswith(_RUNNER_PREFIX):
            return None
        data = json.loads(lines[2][len(_RUNNER_PREFIX) :], object_pairs_hook=_unique_object)
        if set(data) == {"path", "version"}:
            schema = "v1"
        elif set(data) == {"path", "version", "build_id", "sha256"}:
            schema = "v2"
        else:
            return None
        path, version = data["path"], data["version"]
        if not isinstance(path, str) or not isinstance(version, str):
            return None
        if schema == "v1":
            runner = HookRunner(Path(path), version)
            return runner if content == _v1_hook_bytes(event, runner) else None
        build_id, sha256 = data["build_id"], data["sha256"]
        if (
            not isinstance(build_id, str)
            or _BUILD_ID.fullmatch(build_id) is None
            or not isinstance(sha256, str)
            or _BUILD_ID.fullmatch(sha256) is None
        ):
            return None
        runner = HookRunner(Path(path), version, build_id, sha256)
        return runner if content == _hook_bytes(event, runner) else None
    except MurlocsError, UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError:
        return None


def _has_murlocs_marker(content: bytes) -> bool:
    return content.startswith(f"#!/bin/sh\n{HOOK_MARKER}\n".encode())


def _is_owned_hook(content: bytes, event: HookEvent) -> bool:
    return _owned_runner(content, event) is not None or content == _legacy_hook_bytes(event)


def _runner_state(runner: HookRunner) -> str:
    runner_hash = _runner_sha256(runner.path)
    if runner_hash is None or not os.access(runner.path, os.X_OK):
        return "missing runner"
    # Hook metadata is mutable local repository state.  Status deliberately
    # never executes a runner named by it; v1 therefore remains owned but its
    # build cannot be validated until an explicit reinstall upgrades it.
    if not runner.has_v2_identity:
        return "legacy runner identity"
    if runner_hash != runner.sha256:
        return "runner changed"
    return "installed"


def _resolve_runner(explicit_runner: str | None) -> HookRunner:
    """Resolve a stable command now, so the later Git process has no PATH guess."""
    if explicit_runner is None:
        candidate = shutil.which("murlocs")
        if candidate is None:
            raise MurlocsError(
                "could not resolve a durable Murlocs runner; install Murlocs as a user-level tool "
                "or pass --runner /absolute/path/to/murlocs"
            )
        path = Path(candidate).absolute()
    else:
        path = Path(explicit_runner)
        if not path.is_absolute():
            raise MurlocsError("hook runner must be an absolute executable path")
    if "\n" in str(path) or "\r" in str(path):
        raise MurlocsError("hook runner path must not contain a newline")
    try:
        # A selected install-time runner is an explicit trust boundary.  Pin
        # its final regular-file target so later status never has to follow a
        # symlink named by mutable hook metadata.
        path = path.resolve(strict=True)
    except OSError as exc:
        raise MurlocsError("hook runner could not be resolved") from exc
    if explicit_runner is None and _is_project_virtual_environment_runner(path):
        raise MurlocsError(
            "refusing to install a runner from a project virtual environment; install Murlocs "
            "as a user-level tool or pass --runner /absolute/path/to/murlocs with an explicit "
            "durability contract"
        )
    if not path.is_file() or not os.access(path, os.X_OK):
        raise MurlocsError("hook runner is not an executable file")
    identity = _probe_runner_identity(path)
    reported = identity["version"]
    if reported != __version__:
        actual = reported or "an unknown version"
        raise MurlocsError(
            f"hook runner must report Murlocs {__version__}, but {path} reports {actual}"
        )
    if identity["build"]["kind"] == "unknown":
        raise MurlocsError(
            "hook runner build identity is unknown; install a packaged or "
            "development Murlocs build "
            "whose package contents can be read safely"
        )
    runner_hash = _runner_sha256(path)
    if runner_hash is None:
        raise MurlocsError("hook runner could not be read safely")
    return HookRunner(path, reported, identity["build"]["id"], runner_hash)


def _is_project_virtual_environment_runner(path: Path) -> bool:
    """Reject conventional project venvs without rejecting detached user tools."""
    try:
        parents = (path.parent, *path.parents)
        for root in parents:
            if not root.joinpath("pyvenv.cfg").is_file():
                continue
            return _is_project_venv_root(root)
        return False
    except OSError:
        return True


def _is_project_venv_root(root: Path) -> bool:
    """Classify only conventional or project-adjacent venv roots as ephemeral."""
    if root.name.lower() in {".venv", "venv", "env"}:
        return True
    parent = root.parent
    return any(
        candidate.exists()
        for candidate in (
            parent / ".git",
            parent / "pyproject.toml",
            parent / "setup.py",
            parent / "setup.cfg",
        )
    )


def _probe_runner_identity(path: Path) -> RuntimeIdentity:
    """Probe only the runner selected by the caller for this installation.

    This is intentionally not used by status or by inspection of existing hook
    bytes, where a path is repository-controlled metadata.
    """
    output, returncode = _bounded_runner_output(path)
    if returncode != 0:
        raise MurlocsError("hook runner did not return a valid build identity")
    try:
        data = json.loads(output, object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError, MurlocsError) as exc:
        raise MurlocsError("hook runner returned malformed build identity") from exc
    return _parse_runner_identity(data)


def _bounded_runner_output(path: Path) -> tuple[bytes, int]:
    """Read one selected runner's identity response without unbounded capture.

    The runner is an install-time user-selected executable, never a path read
    from existing hook bytes.  Its working directory and inherited environment
    are deliberately not repository-controlled.
    """
    try:
        process = subprocess.Popen(
            [str(path), "version", "--format", "json"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=tempfile.gettempdir(),
            env={"PATH": os.defpath, "PYTHONNOUSERSITE": "1"},
            start_new_session=os.name == "posix",
        )
    except OSError as exc:
        raise MurlocsError("hook runner did not return a build identity") from exc
    assert process.stdout is not None
    descriptor = process.stdout.fileno()
    try:
        os.set_blocking(descriptor, False)
        deadline = time.monotonic() + 5
        output = bytearray()
        eof = False
        while not eof:
            if time.monotonic() >= deadline:
                raise MurlocsError("hook runner build identity timed out")
            try:
                chunk = os.read(descriptor, _MAX_RUNNER_IDENTITY_BYTES + 1 - len(output))
            except BlockingIOError:
                chunk = None
            except OSError as exc:
                raise MurlocsError("hook runner did not return a build identity") from exc
            if chunk is None:
                if process.poll() is not None:
                    # The process may have exited just before the final pipe
                    # drain; retry once through the regular read path.
                    time.sleep(0.001)
                else:
                    time.sleep(0.005)
                continue
            if not chunk:
                eof = True
                continue
            output.extend(chunk)
            if len(output) > _MAX_RUNNER_IDENTITY_BYTES:
                raise MurlocsError("hook runner build identity exceeds 16 KiB")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MurlocsError("hook runner build identity timed out")
        try:
            return bytes(output), process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            raise MurlocsError("hook runner build identity timed out") from exc
    finally:
        _terminate_runner_process_group(process)
        with suppress(OSError):
            process.stdout.close()


def _terminate_runner_process_group(process: subprocess.Popen[bytes]) -> None:
    """Kill the selected runner's isolated process group and reap its leader."""
    if os.name == "posix":
        with suppress(ProcessLookupError, OSError):
            os.killpg(process.pid, signal.SIGKILL)
    elif process.poll() is None:
        with suppress(OSError):
            process.kill()
    with suppress(subprocess.TimeoutExpired, OSError):
        process.wait(timeout=1)


def _parse_runner_identity(value: Any) -> RuntimeIdentity:
    """Strictly accept the version-1 identity protocol from a chosen runner."""
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "project",
        "version",
        "build",
        "installation",
    }:
        raise MurlocsError("hook runner returned an unsupported build identity schema")
    if value["schema_version"] != 1 or value["project"] != "murlocs":
        raise MurlocsError("hook runner returned an unsupported build identity schema")
    version = value["version"]
    build = value["build"]
    installation = value["installation"]
    if not isinstance(version, str) or not version or len(version) > 128:
        raise MurlocsError("hook runner returned an invalid build version")
    if not isinstance(build, Mapping) or set(build) != {"kind", "id", "verification"}:
        raise MurlocsError("hook runner returned an invalid build identity")
    if (
        build.get("kind") not in {"development", "release", "unknown"}
        or not isinstance(build.get("id"), str)
        or _BUILD_ID.fullmatch(build["id"]) is None
        or build.get("verification") != "unverified"
    ):
        raise MurlocsError("hook runner returned an invalid build identity")
    if not isinstance(installation, Mapping) or set(installation) != {
        "kind",
        "editable",
        "source_revision",
        "archive_hash",
    }:
        raise MurlocsError("hook runner returned an invalid installation identity")
    if (
        installation.get("kind")
        not in {"local-directory", "editable", "vcs", "archive", "index-or-unknown", "unknown"}
        or not isinstance(installation.get("editable"), bool)
        or installation.get("source_revision") is not None
        and not isinstance(installation.get("source_revision"), str)
        or installation.get("archive_hash") is not None
        and not isinstance(installation.get("archive_hash"), str)
    ):
        raise MurlocsError("hook runner returned an invalid installation identity")
    return {
        "schema_version": 1,
        "project": "murlocs",
        "version": version,
        "build": {
            "kind": build["kind"],
            "id": build["id"],
            "verification": "unverified",
        },
        "installation": {
            "kind": installation["kind"],
            "editable": installation["editable"],
            "source_revision": installation["source_revision"],
            "archive_hash": installation["archive_hash"],
        },
    }


def _runner_sha256(path: Path) -> str | None:
    """Hash one runner as data, bounded and race-checked without executing it."""
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_RUNNER_BYTES:
            return None
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            return None
        digest = hashlib.sha256()
        remaining = _MAX_RUNNER_BYTES + 1
        while remaining:
            block = os.read(descriptor, min(64 * 1024, remaining))
            if not block:
                break
            digest.update(block)
            remaining -= len(block)
        if remaining == 0:
            return None
        after = path.lstat()
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ):
            return None
        return "sha256:" + digest.hexdigest()
    except OSError:
        return None
    finally:
        with suppress(OSError):
            os.close(descriptor)


def _validate_expected_build_id(value: str | None) -> None:
    if value is not None and _BUILD_ID.fullmatch(value) is None:
        raise MurlocsError("expected hook build id must be a SHA-256 identity")


def _atomic_hook_write(path: Path, content: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        try:
            os.link(temp_path, path)
        except FileExistsError as exc:
            raise MurlocsError(f"Git hook slot changed during installation: {path.name}") from exc
        except OSError as exc:
            raise MurlocsError(f"could not install Git hook {path.name}: {exc}") from exc
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _replace_owned_hook(path: Path, content: bytes, event: HookEvent) -> None:
    """Atomically refresh a hook only after recognizing its exact owned bytes."""
    try:
        current = path.read_bytes()
    except OSError as exc:
        raise MurlocsError(f"could not inspect Git hook {path.name}: {exc}") from exc
    if not _is_owned_hook(current, event):
        raise MurlocsError(f"Git hook slot changed during installation: {path.name}")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        os.replace(temp_path, path)
    except OSError as exc:
        raise MurlocsError(f"could not repair Git hook {path.name}: {exc}") from exc
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _portable_root(root: Path) -> dict[str, Any]:
    if os.name == "nt":
        drive, tail = os.path.splitdrive(str(root))
        if not drive:
            raise MurlocsError("Git repository root has no Windows drive")
        return {
            "format": "windows-drive",
            "drive": drive[0].upper(),
            "segments": list(Path(tail).parts[1:]),
        }
    return {"format": "posix", "segments": list(root.parts[1:])}


def _adapter_correlation(event: HookEvent, seed: str) -> str:
    # The Git adapter is the lifecycle caller; the read-only Murlocs operations only echo it.
    return f"git-hook:{event}:{seed[:32]}"


def _normalized_events(events: tuple[HookEvent, ...]) -> tuple[HookEvent, ...]:
    selected = events or _all_events()
    if len(set(selected)) != len(selected):
        raise MurlocsError("hook event may be selected only once")
    return tuple(event for event in _all_events() if event in selected)


def _all_events() -> tuple[HookEvent, HookEvent]:
    return ("pre-commit", "pre-push")


def _validate_explicit_paths(
    explicit_paths: tuple[str, ...], staged_paths: tuple[str, ...]
) -> None:
    staged = set(staged_paths)
    for value in explicit_paths:
        if (
            not value
            or value.startswith("/")
            or "\0" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
        ):
            raise MurlocsError("explicit hook path is not repository-relative")
        if value not in staged:
            raise MurlocsError(f"explicit hook path is not staged: {value}")
