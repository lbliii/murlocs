"""Deterministic resource probes for Murlocs' passive activation hot path.

This module deliberately records stable work counters alongside latency. The counter
budgets are the CI gate; generous cold/warm time limits only catch a pathological stall.
No registered command is invoked by these probes.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import murlocs.impact as impact_module
from murlocs.cli import check_command, impact_command
from murlocs.gitview import MAX_VIEW_TOTAL_BYTES, Deadline, discover_git
from murlocs.hooks import run_hook
from murlocs.manifest import PROTOCOL_TEMPLATE, load_manifest
from murlocs.render import compile_manifest
from murlocs.serialization import render_fragment_data, render_manifest_data


@dataclass(frozen=True)
class Shape:
    """One representative guidance-network shape."""

    name: str
    domains: int
    leaves_per_domain: int
    history_commits: int = 0


SHAPES = (
    Shape("small", domains=0, leaves_per_domain=0),
    Shape("layered", domains=1, leaves_per_domain=3),
    Shape("multi-domain", domains=4, leaves_per_domain=3),
    Shape("long-history", domains=2, leaves_per_domain=2, history_commits=65),
    Shape("scale-91", domains=9, leaves_per_domain=10),
)

# Structural limits are the regression policy: they are derived from bounded Git
# views and invocation topology, so a faster machine cannot mask unbounded work.
BUDGET = {
    "cold_ms": 8_000,
    "warm_ms": 7_000,
    "git_subprocesses": 24,
    "files_read": 512,
    "peak_memory_bytes": 96 * 1024 * 1024,
}


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True).stdout.strip()


def _commit(root: Path, message: str) -> None:
    _git(root, "add", ".")
    _git(
        root,
        "-c",
        "commit.gpgsign=false",
        "-c",
        f"core.hooksPath={os.devnull}",
        "commit",
        "--quiet",
        "-m",
        message,
    )


def build_fixture(root: Path, shape: Shape) -> tuple[str, tuple[str, ...]]:
    """Build and commit a disposable network without installing a hook."""
    root.mkdir(parents=True)
    _write(root / "README.md", "# Passive hot-path fixture\n")
    _write(root / ".murlocs/PROTOCOL.md", PROTOCOL_TEMPLATE)
    source_paths: list[str] = []
    layers: list[dict[str, Any]] = []
    root_scopes: list[dict[str, Any]] = [
        {
            "id": "root",
            "path": ".",
            "map": "AGENTS.md",
            "point_of_view": "Passive hot-path fixture root.",
            "owns": ["README.md", ".murlocs"],
            "guardrails": ["Keep measurements deterministic."],
            "edges": [],
        }
    ]
    target = "README.md"
    for domain in range(shape.domains):
        identifier = f"domain-{domain:02d}"
        source = f".murlocs/layers/{identifier}.toml"
        source_paths.append(source)
        layers.append({"id": identifier, "kind": "domain", "path": source, "owners": ["@perf"]})
        scopes = []
        for leaf in range(shape.leaves_per_domain):
            relative = f"src/{identifier}/area-{leaf:02d}"
            scope_id = f"{identifier}-area-{leaf:02d}"
            scopes.append(
                {
                    "id": scope_id,
                    "path": relative,
                    "map": f"{relative}/AGENTS.md",
                    "point_of_view": f"Fixture scope {scope_id}.",
                    "owns": [relative],
                    "guardrails": ["Keep the local fixture boundary."],
                    "edges": [],
                }
            )
            _write(root / relative / "unit.py", f'SCOPE = "{scope_id}"\n')
            target = f"{relative}/unit.py"
        _write(
            root / source,
            render_fragment_data(
                {
                    "scopes": scopes,
                }
            ),
        )
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "network": f"Passive hot-path {shape.name}",
        "protocol": ".murlocs/PROTOCOL.md",
        "max_active_bytes": 24576,
        "owners": ["@perf"],
        "pillars": ["Passive reads are bounded."],
        "search_policy": ["Read only the applicable guidance chain."],
        "operating_rules": ["Do not execute registered checks."],
        "stop_and_ask": ["A resource budget has been exceeded."],
        "done_criteria": ["The passive checks pass."],
        "coverage": {"roots": [], "source_suffixes": [".py"], "exemptions": {}},
        "checks": {
            "never-run": {
                "invoke": "python -c \"raise SystemExit('registered check ran')\"",
                "location": "README.md",
                "proof_contains": "Passive hot-path fixture",
                "description": "A sentinel registration for passive-read probes.",
            }
        },
        "layers": layers,
        "scopes": root_scopes,
    }
    _write(root / ".murlocs/manifest.toml", render_manifest_data(manifest))
    compile_manifest(load_manifest(root))
    _git(root, "init", "--quiet")
    _git(root, "config", "user.name", "Performance Fixture")
    _git(root, "config", "user.email", "performance@example.invalid")
    _commit(root, "compiled fixture")
    return target, tuple(source_paths)


def _install_inert_execution_traps(root: Path) -> Path:
    """Configure commands that would leave evidence if Git escaped its read-only path."""
    sentinel = root / "UNEXPECTED_EXECUTION"
    driver = root / "driver.sh"
    driver.write_text("#!/bin/sh\nprintf x > UNEXPECTED_EXECUTION\nexit 97\n", encoding="utf-8")
    driver.chmod(0o755)
    hooks = root / "hooks"
    hooks.mkdir()
    for name in ("pre-commit", "pre-push", "post-checkout", "reference-transaction"):
        hook = hooks / name
        hook.write_text("#!/bin/sh\nprintf x > UNEXPECTED_EXECUTION\nexit 97\n", encoding="utf-8")
        hook.chmod(0o755)
    _write(root / ".gitattributes", "*.toml diff=trap\n")
    for key, value in (
        ("diff.external", str(driver)),
        ("diff.trap.textconv", str(driver)),
        ("core.hooksPath", str(hooks)),
    ):
        _git(root, "config", key, value)
    return sentinel


def _measure(operation: Callable[[], tuple[dict[str, Any], int, int]]) -> dict[str, Any]:
    """Collect cold/warm latency and deterministic work counters."""
    samples: list[tuple[float, dict[str, Any], int, int]] = []
    for _ in range(2):
        started = time.perf_counter_ns()
        payload, git_calls, files_read = operation()
        elapsed = (time.perf_counter_ns() - started) / 1_000_000
        samples.append((elapsed, payload, git_calls, files_read))
    cold, warm = samples
    return {
        "cold_ms": round(cold[0], 3),
        "warm_ms": round(warm[0], 3),
        "git_subprocesses": max(cold[2], warm[2]),
        "files_read": max(cold[3], warm[3]),
        # Hooks materialize raw views in the parent process but run check/impact
        # in child interpreters, so tracemalloc cannot report a meaningful whole
        # operation RSS. The enforced bound is the raw view cap instead.
        "peak_memory_bound_bytes": MAX_VIEW_TOTAL_BYTES,
        "payload": warm[1],
    }


def _file_inputs(root: Path, operation: str) -> int:
    """Count unique repository files an operation may read for this fixture."""
    manifest = load_manifest(root)
    paths = {source.path for source in manifest.sources}
    if operation == "check":
        paths.update(scope.map for scope in manifest.scopes)
        paths.update(check.location for check in manifest.checks.values())
        paths.add(".murlocs/lock.json")
    return len(paths)


def _activation_work(payload: dict[str, Any], root: Path) -> tuple[int, int]:
    if payload.get("execution", {}).get("status") != "completed":
        raise RuntimeError("measured hook activation did not complete")
    if [item.get("operation") for item in payload.get("operations", [])] != [
        "check",
        "impact",
    ]:
        raise RuntimeError("measured hook skipped its check/impact operation pair")
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        raise RuntimeError("measured hook omitted structural metrics")
    git_calls = metrics.get("git_subprocesses")
    entries = metrics.get("entries")
    if not isinstance(git_calls, int) or not isinstance(entries, int):
        raise RuntimeError("measured hook returned malformed structural metrics")
    files_read = entries + _file_inputs(root, "check") + _file_inputs(root, "impact")
    return git_calls, files_read


def _hook_operation(event: str, root: Path, update: bytes = b"") -> tuple[dict[str, Any], int, int]:
    result = run_hook(event, root, correlation_id=f"benchmark:{event}", pre_push_input=update)
    if result.exit_code not in {0, 1}:
        raise RuntimeError(f"unexpected hook exit: {result.exit_code}")
    if event == "pre-commit":
        git_calls, files_read = _activation_work(result.payload, root)
        return result.payload, git_calls, files_read
    results = result.payload.get("results")
    if not isinstance(results, list) or not results:
        raise RuntimeError("completion benchmark returned no activation results")
    work = [_activation_work(payload, root) for payload in results]
    return (
        result.payload,
        max(git_calls for git_calls, _ in work),
        sum(files_read for _, files_read in work),
    )


def _task_start(root: Path) -> tuple[dict[str, Any], int, int]:
    deadline = Deadline.start(10_000)
    context = discover_git(root, deadline)
    checked = check_command(repo=str(root))
    if not checked["ok"]:
        raise RuntimeError("task-start check failed")
    return (
        {"object_format": context.object_format, "check": checked["ok"]},
        deadline.git_calls,
        _file_inputs(root, "check"),
    )


def _explicit_impact(root: Path, target: str) -> tuple[dict[str, Any], int, int]:
    report = impact_command(path=[target], repo=str(root))
    if not report["ok"]:
        raise RuntimeError("explicit impact failed")
    return report, 0, len(load_manifest(root).sources)


def _long_history_probe(root: Path, source_paths: tuple[str, ...]) -> dict[str, Any] | None:
    """Exercise #53's conservative history path only on the long-history shape."""
    if len(source_paths) < 2:
        return None
    historic, global_source = source_paths[:2]
    path = root / historic
    for index in range(65):
        path.write_text(path.read_text(encoding="utf-8") + f"# history {index}\n", encoding="utf-8")
        _commit(root, f"history {index}")
    global_path = root / global_source
    global_path.write_text(
        'operating_rules = ["Global stale-source ambiguity."]\n\n'
        + global_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    calls: list[list[str]] = []
    original = impact_module.subprocess.run

    def tracked(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        return original(argv, **kwargs)

    impact_module.subprocess.run = tracked
    try:
        report = impact_command(path=[historic], repo=str(root))
    finally:
        impact_module.subprocess.run = original
    if not report["ok"] or len(calls) > 3:
        raise RuntimeError("bounded history probe did not fail closed")
    return {
        "git_subprocesses": len(calls),
        "history_limit": impact_module.GIT_SOURCE_HISTORY_LIMIT,
        "conservative": all(item["status"] == "required" for item in report["scopes"]),
    }


def run_suite(root: Path) -> dict[str, Any]:
    """Run every passive operation against every required network shape."""
    results: dict[str, Any] = {}
    for shape in SHAPES:
        fixture = root / shape.name
        target, source_paths = build_fixture(fixture, shape)
        _write(fixture / target, (fixture / target).read_text(encoding="utf-8") + "# staged\n")
        _git(fixture, "add", "--", target)
        sentinel = _install_inert_execution_traps(fixture)
        head = _git(fixture, "rev-parse", "HEAD").decode("ascii")
        update = f"refs/heads/main {head} refs/heads/main {'0' * len(head)}\n".encode()
        measurements: dict[str, dict[str, Any]] = {
            "task_start_discovery": _measure(lambda fixture=fixture: _task_start(fixture)),
            "explicit_impact": _measure(
                lambda fixture=fixture, target=target: _explicit_impact(fixture, target)
            ),
        }
        # The layered fixture is the complete operation matrix. The other shapes
        # exercise discovery and focused impact, plus one hook path where that
        # shape adds a distinct fan-out characteristic, avoiding a 5x redundant
        # process benchmark in the normal test suite.
        if shape.name in {"layered", "multi-domain", "scale-91"}:
            measurements["healthy_pre_commit"] = _measure(
                lambda fixture=fixture: _hook_operation("pre-commit", fixture)
            )
        if shape.name == "layered":
            measurements["staged_impact"] = _measure(
                lambda fixture=fixture: _hook_operation("pre-commit", fixture)
            )
            measurements["completion_gating"] = _measure(
                lambda fixture=fixture, update=update: _hook_operation("pre-push", fixture, update)
            )
            agents = fixture / "AGENTS.md"
            agents.write_text(
                agents.read_text(encoding="utf-8") + "\nintentional drift\n",
                encoding="utf-8",
            )
            _git(fixture, "add", "AGENTS.md")
            drifted = _measure(lambda fixture=fixture: _hook_operation("pre-commit", fixture))
            if drifted["payload"]["repository"]["blocking"] is not True:
                raise RuntimeError("drifted checks did not block")
            measurements["drifted_checks"] = drifted
        history = _long_history_probe(fixture, source_paths) if shape.history_commits else None
        if sentinel.exists():
            raise RuntimeError("passive benchmark executed a hook, filter, or external driver")
        results[shape.name] = {
            "maps": len(load_manifest(fixture).scopes),
            "measurements": measurements,
            "long_history": history,
        }
    return {"schema_version": 1, "budget": BUDGET, "shapes": results}


def main() -> int:
    import argparse
    import tempfile

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="murlocs-passive-hot-path-") as temporary:
        result = run_suite(Path(temporary))
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
