"""Generate and measure a deterministic 91-map Murlocs guidance network.

The fixture is synthetic: it exercises scale and governance mechanics without copying
repository guidance from a private project. Runtime results describe only the machine
that produced them and are not release gates.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import platform
import statistics
import tempfile
import time
import tracemalloc
from collections.abc import Callable
from pathlib import Path
from typing import Any

from murlocs.cli import check_command, compile_command, explain_command, impact_command
from murlocs.errors import MurlocsError
from murlocs.eval.__main__ import main as eval_main
from murlocs.eval.guidance import inline_dump_guidance, murlocs_guidance
from murlocs.manifest import PROTOCOL_TEMPLATE, load_manifest
from murlocs.render import compile_manifest, render_outputs
from murlocs.serialization import render_fragment_data, render_manifest_data
from murlocs.verify import validate

DOMAIN_COUNT = 9
SCOPES_PER_DOMAIN = 10
EXPECTED_MAPS = 1 + DOMAIN_COUNT * SCOPES_PER_DOMAIN
TEAM_OWNERS = ("@team-alpha", "@team-beta", "@team-gamma", "@team-delta", "@team-epsilon")
TARGET_SCOPE = "domain-08-http"
TARGET_FILE = "src/domain-08/platform/runtime/adapters/http/unit.py"


def _require_empty(path: Path, label: str) -> None:
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError(f"{label} must not exist or must be empty: {path}")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _scope_specs(domain: int) -> list[tuple[str, str]]:
    prefix = f"domain-{domain:02d}"
    root = f"src/{prefix}"
    return [
        (prefix, root),
        (f"{prefix}-service-a", f"{root}/service-a"),
        (f"{prefix}-service-b", f"{root}/service-b"),
        (f"{prefix}-service-c", f"{root}/service-c"),
        (f"{prefix}-platform", f"{root}/platform"),
        (f"{prefix}-runtime", f"{root}/platform/runtime"),
        (f"{prefix}-adapters", f"{root}/platform/runtime/adapters"),
        (f"{prefix}-http", f"{root}/platform/runtime/adapters/http"),
        (f"{prefix}-queue", f"{root}/platform/runtime/adapters/queue"),
        (f"{prefix}-tests", f"{root}/tests"),
    ]


def build_fixture(root: Path) -> None:
    """Create the same mixed-width/depth, owner-partitioned network every time."""
    _require_empty(root, "fixture repository target")
    root.mkdir(parents=True, exist_ok=True)
    layers: list[dict[str, Any]] = []
    codeowners = ["/.murlocs/manifest.toml @architecture"]

    for domain in range(DOMAIN_COUNT):
        layer_id = f"domain-{domain:02d}"
        owner = TEAM_OWNERS[domain % len(TEAM_OWNERS)]
        layer_path = f".murlocs/layers/{layer_id}.toml"
        layers.append(
            {"id": layer_id, "kind": "domain", "path": layer_path, "owners": [owner]}
        )
        codeowners.append(f"/{layer_path} {owner}")

        scopes: list[dict[str, Any]] = []
        for scope_id, scope_path in _scope_specs(domain):
            edges: list[dict[str, str]] = []
            if scope_id.endswith("-http"):
                edges.append(
                    {
                        "type": "coordinates-with",
                        "to": f"{layer_id}-queue",
                        "what": "HTTP and queue adapters share the domain boundary.",
                    }
                )
            scopes.append(
                {
                    "id": scope_id,
                    "path": scope_path,
                    "map": f"{scope_path}/AGENTS.md",
                    "point_of_view": f"Guidance for {scope_id}.",
                    "owns": [scope_path],
                    "guardrails": [f"Keep {scope_id} changes inside its declared boundary."],
                    "edges": edges,
                }
            )
            _write(
                root / scope_path / "unit.py",
                f'"""Synthetic source owned by {scope_id}."""\n\nSCOPE = "{scope_id}"\n',
            )

        check_name = f"verify-{layer_id}"
        contract_path = f"docs/contracts/{layer_id}.md"
        contract_anchor = f"{layer_id} contract"
        fragment = {
            "checks": {
                check_name: {
                    "invoke": "python -m pytest -q",
                    "location": contract_path,
                    "proof_contains": contract_anchor,
                    "description": f"Verify the {layer_id} boundary.",
                }
            },
            "scopes": scopes,
            "invariants": [
                {
                    "id": f"{layer_id}-contract",
                    "scope": layer_id,
                    "statement": f"Changes preserve the {layer_id} contract.",
                    "severity": "important",
                    "verification": "command",
                    "enforced_by": check_name,
                }
            ],
            "judgments": {
                layer_id: {
                    "advocate": ["Prefer explicit domain boundaries."],
                    "do_not": ["Move domain policy into an unrelated scope."],
                }
            },
        }
        _write(root / layer_path, render_fragment_data(fragment))
        _write(root / contract_path, f"# {contract_anchor}\n\nOwner: {owner}\n")

    overlay_path = ".murlocs/layers/architecture-overlay.toml"
    layers.append(
        {
            "id": "architecture-overlay",
            "kind": "overlay",
            "path": overlay_path,
            "owners": ["@architecture"],
        }
    )
    codeowners.append(f"/{overlay_path} @architecture")
    overlay = {
        "scopes": [
            {
                "id": TARGET_SCOPE,
                "override": True,
                "point_of_view": "HTTP adapter guidance with an architecture-reviewed refinement.",
                "guardrails": ["Preserve transport-independent domain behavior."],
            }
        ]
    }
    _write(root / overlay_path, render_fragment_data(overlay))

    manifest = {
        "schema_version": 1,
        "network": "Synthetic scale network",
        "protocol": ".murlocs/PROTOCOL.md",
        "max_active_bytes": 24576,
        "owners": ["@architecture"],
        "pillars": [
            "Repository guidance is local, layered, and reviewable.",
            "Domain owners review the guidance source for their area.",
        ],
        "search_policy": [
            "Read the root map before repository discovery.",
            "Open only maps on the target path unless a declared edge crosses domains.",
        ],
        "operating_rules": ["Read the applicable AGENTS.md chain before editing."],
        "stop_and_ask": ["A requested change crosses an unreviewed domain boundary."],
        "done_criteria": ["Murlocs reports no ownership, budget, or drift findings."],
        "coverage": {"roots": ["src"], "source_suffixes": [".py"], "exemptions": {}},
        "policies": {
            "require_scope_invariants": False,
            "require_layer_owners": True,
            "validate_codeowners": True,
        },
        "layers": layers,
        "scopes": [
            {
                "id": "root",
                "path": ".",
                "map": "AGENTS.md",
                "point_of_view": "Repository control plane and cross-domain integration.",
                "owns": ["README.md", "docs", ".murlocs"],
                "guardrails": ["Keep domain guidance in its owner-focused layer."],
                "edges": [],
            }
        ],
    }
    _write(root / ".murlocs/manifest.toml", render_manifest_data(manifest))
    _write(root / ".murlocs/PROTOCOL.md", PROTOCOL_TEMPLATE)
    _write(root / ".github/CODEOWNERS", "\n".join(codeowners) + "\n")
    _write(root / "README.md", "# Synthetic scale network\n")


def _snapshot(root: Path) -> dict[str, bytes]:
    manifest = load_manifest(root)
    paths = [scope.map for scope in manifest.scopes]
    paths.append(".murlocs/lock.json")
    return {path: (root / path).read_bytes() for path in sorted(paths)}


def _digest_snapshot(snapshot: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for path, content in sorted(snapshot.items()):
        digest.update(path.encode())
        digest.update(b"\0")
        digest.update(content)
    return digest.hexdigest()


def _fixture_revision(root: Path) -> str:
    digest = hashlib.sha256()
    included = [
        path
        for path in root.rglob("*")
        if path.is_file() and path.name != "AGENTS.md" and path.name != "lock.json"
    ]
    for path in sorted(included):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _measure(operation: Callable[[], Any], samples: int) -> dict[str, Any]:
    timings: list[float] = []
    for _ in range(samples):
        started = time.perf_counter_ns()
        result = operation()
        elapsed = (time.perf_counter_ns() - started) / 1_000_000
        if not result.get("ok"):
            raise RuntimeError(f"measured operation failed: {result}")
        timings.append(round(elapsed, 3))
    # Measure allocation separately so tracemalloc's instrumentation does not distort
    # the reported wall-clock samples.
    tracemalloc.start()
    result = operation()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    if not result.get("ok"):
        raise RuntimeError(f"memory measurement failed: {result}")
    return {
        "samples": samples,
        "median_ms": round(statistics.median(timings), 3),
        "min_ms": min(timings),
        "max_ms": max(timings),
        "peak_memory_bytes": peak,
    }


def _failure_probes(root: Path) -> dict[str, Any]:
    manifest_path = root / ".murlocs/manifest.toml"
    codeowners_path = root / ".github/CODEOWNERS"
    overlay_path = root / ".murlocs/layers/architecture-overlay.toml"
    target_map = root / TARGET_FILE.rsplit("/", 1)[0] / "AGENTS.md"

    original = target_map.read_text(encoding="utf-8")
    try:
        target_map.write_text(original + "\nmodified outside compilation\n", encoding="utf-8")
        drift_codes = sorted({item.code for item in validate(load_manifest(root))})
    finally:
        target_map.write_text(original, encoding="utf-8")

    original = codeowners_path.read_text(encoding="utf-8")
    try:
        codeowners_path.write_text(
            original.replace(
                "/.murlocs/layers/domain-08.toml @team-delta",
                "/.murlocs/layers/domain-08.toml @wrong-owner",
            ),
            encoding="utf-8",
        )
        ownership_codes = sorted({item.code for item in validate(load_manifest(root))})
    finally:
        codeowners_path.write_text(original, encoding="utf-8")

    original = manifest_path.read_text(encoding="utf-8")
    try:
        manifest_path.write_text(
            original.replace("max_active_bytes = 24576", "max_active_bytes = 1"),
            encoding="utf-8",
        )
        budget_codes = sorted({item.code for item in validate(load_manifest(root))})
    finally:
        manifest_path.write_text(original, encoding="utf-8")

    original = overlay_path.read_text(encoding="utf-8")
    try:
        overlay_path.write_text(
            original.replace(
                "override = true\n",
                'override = true\npath = "src/domain-08/unsafe-move"\n',
                1,
            ),
            encoding="utf-8",
        )
        try:
            load_manifest(root)
        except MurlocsError as exc:
            override_error = str(exc)
        else:
            override_error = ""
    finally:
        overlay_path.write_text(original, encoding="utf-8")

    if validate(load_manifest(root)):
        raise RuntimeError("failure probe did not restore the valid fixture")
    return {
        "generated_drift": {"detected": "drift" in drift_codes, "finding_codes": drift_codes},
        "ownership_mismatch": {
            "detected": "ownership" in ownership_codes,
            "finding_codes": ownership_codes,
        },
        "budget_violation": {
            "detected": "budget" in budget_codes,
            "finding_codes": budget_codes,
        },
        "unsafe_override": {
            "detected": "may not change path" in override_error,
            "error": override_error,
        },
    }


def _evaluation(root: Path, artifact_root: Path) -> dict[str, Any]:
    revision = _fixture_revision(root)
    active = murlocs_guidance(root, TARGET_FILE)
    inline = inline_dump_guidance(root)
    answer = (
        "The domain-08 HTTP adapter is reviewed by @team-delta and must preserve the "
        "domain-08-contract invariant."
    )
    task = "\n".join(
        [
            "schema_version = 1",
            'id = "scale-network-guidance"',
            (
                'prompt = "Before changing the domain-08 HTTP adapter, which guidance owner '
                'reviews it and which domain invariant applies?"'
            ),
            f'target_path = "{TARGET_FILE}"',
            f'repository_revision = "{revision}"',
            "correctness_threshold = 1.0",
            "",
            "[[expected_facts]]",
            'id = "owner"',
            'description = "Names the domain guidance owner."',
            'any_of = ["@team-delta"]',
            "",
            "[[expected_facts]]",
            'id = "invariant"',
            'description = "Names the applicable domain invariant."',
            'any_of = ["domain-08-contract"]',
            "",
        ]
    )
    task_path = artifact_root / "scale-network-guidance.toml"
    runs_path = artifact_root / "scale-network-guidance-runs.json"
    results_dir = artifact_root / "results"
    _write(task_path, task)
    common = {
        "model": "deterministic-scripted-fixture-v1",
        "ade": "scale-pilot-walkthrough-v1",
        "answer": answer,
    }
    runs = {
        "schema_version": 1,
        "task_id": "scale-network-guidance",
        "repository_revision": revision,
        "runs": [
            {
                "arm": "no-guidance",
                **common,
                "guidance_revision": "none",
                "guidance_text": "",
                "evidence": {
                    "files_inspected": 12,
                    "lines_inspected": 680,
                    "tool_calls": 12,
                    "executable_steps": 12,
                    "transcript": [
                        "Search authored layer files for the target path.",
                        "Trace the owning layer and its invariant.",
                    ],
                },
            },
            {
                "arm": "inline-dump",
                **common,
                "guidance_revision": hashlib.sha256(inline.encode()).hexdigest(),
                "guidance_text": inline,
                "evidence": {
                    "files_inspected": 1,
                    "lines_inspected": len(inline.splitlines()),
                    "tool_calls": 4,
                    "executable_steps": 4,
                    "transcript": [
                        "Search the complete inline guidance dump for the target scope.",
                        "Extract its owner and ancestor invariant.",
                    ],
                },
            },
            {
                "arm": "murlocs",
                **common,
                "guidance_revision": hashlib.sha256(
                    (root / ".murlocs/lock.json").read_bytes()
                ).hexdigest(),
                "guidance_text": active,
                "evidence": {
                    "files_inspected": 2,
                    "lines_inspected": len(active.splitlines()),
                    "tool_calls": 3,
                    "executable_steps": 3,
                    "transcript": [
                        "Read the compiled root-to-target guidance chain.",
                        "Extract the domain owner and applicable invariant.",
                    ],
                },
            },
        ],
    }
    _write(runs_path, json.dumps(runs, indent=2, sort_keys=True) + "\n")
    with contextlib.redirect_stdout(io.StringIO()):
        exit_code = eval_main(
            ["--task", str(task_path), "--runs", str(runs_path), "--output", str(results_dir)]
        )
    if exit_code:
        raise RuntimeError(f"evaluation ingestion failed with exit code {exit_code}")
    result_path = results_dir / "scale-network-guidance.json"
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    scores = payload["summary"]["scores"]
    return {
        "method": (
            "Deterministic scripted walkthrough; these records validate ingestion and the "
            "correctness-first comparison, not live-model quality."
        ),
        "task_id": payload["task"]["id"],
        "all_arms_correct": all(score["correctness"]["passed"] for score in scores),
        "most_efficient_correct_arm": payload["summary"]["most_efficient_arm"],
        "scores": scores,
        "task_sha256": hashlib.sha256(task_path.read_bytes()).hexdigest(),
        "runs_sha256": hashlib.sha256(runs_path.read_bytes()).hexdigest(),
        "result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
    }


def run_pilot(root: Path, artifact_root: Path, samples: int = 5) -> dict[str, Any]:
    _require_empty(root, "fixture repository target")
    _require_empty(artifact_root, "evaluation artifact target")
    build_fixture(root)
    manifest = load_manifest(root)
    initial = [finding for finding in validate(manifest) if finding.code not in {"drift", "lock"}]
    if initial:
        raise RuntimeError(f"fixture is invalid before compilation: {initial}")

    compile_manifest(manifest)
    first = _snapshot(root)
    compile_manifest(load_manifest(root))
    second = _snapshot(root)
    deterministic = first == second
    if not deterministic:
        raise RuntimeError("repeated compilation changed generated bytes")
    if validate(load_manifest(root)):
        raise RuntimeError("compiled fixture did not pass validation")

    manifest = load_manifest(root)
    outputs = render_outputs(manifest)
    target_explain = explain_command(TARGET_FILE, repo=str(root))
    focused_impact = impact_command(path=[TARGET_FILE], repo=str(root))
    global_impact = impact_command(path=[".murlocs/manifest.toml"], repo=str(root))
    active_by_scope = {
        scope.id: explain_command(scope.path, repo=str(root))["budget"]["active_bytes"]
        for scope in manifest.scopes
    }
    max_scope = max(active_by_scope, key=active_by_scope.get)
    generated_bytes = sum(len(content.encode()) for content in outputs.values())
    source_owners = sorted({owner for source in manifest.sources for owner in source.owners})

    operations: dict[str, Callable[[], Any]] = {
        "compile": lambda: compile_command(repo=str(root)),
        "check": lambda: check_command(repo=str(root)),
        "explain": lambda: explain_command(TARGET_FILE, repo=str(root)),
        "impact": lambda: impact_command(path=[TARGET_FILE], repo=str(root)),
    }
    measurements = {name: _measure(operation, samples) for name, operation in operations.items()}
    probes = _failure_probes(root)
    evaluation = _evaluation(root, artifact_root)
    leaf = next(scope for scope in target_explain["scopes"] if scope["id"] == TARGET_SCOPE)

    return {
        "schema_version": 1,
        "fixture": {
            "kind": "synthetic",
            "generator": "benchmarks/scale_network.py",
            "map_count": len(outputs),
            "domain_count": DOMAIN_COUNT,
            "declared_layer_count": len(manifest.sources) - 1,
            "owner_count": len(source_owners),
            "owners": source_owners,
            "maximum_scope_depth": max(
                0 if scope.path == "." else len(Path(scope.path).parts)
                for scope in manifest.scopes
            ),
            "total_generated_bytes": generated_bytes,
            "maximum_active_chain_bytes": active_by_scope[max_scope],
            "maximum_active_chain_scope": max_scope,
            "maximum_active_to_total_ratio": round(
                active_by_scope[max_scope] / generated_bytes, 4
            ),
            "active_budget_bytes": manifest.max_active_bytes,
            "byte_deterministic_recompile": deterministic,
            "generated_snapshot_sha256": _digest_snapshot(second),
        },
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "measurements": measurements,
        "governance": {
            "codeowners_validation_passed": not any(
                finding.code == "ownership" for finding in validate(manifest)
            ),
            "target_scope": TARGET_SCOPE,
            "target_layers": leaf["layers"],
            "accepted_override_count": len(target_explain["overrides"]),
            "focused_review_fan_out": focused_impact["summary"],
            "control_plane_review_fan_out": global_impact["summary"],
        },
        "failure_probes": probes,
        "evaluation": evaluation,
        "limits": [
            "Synthetic structure exercises Murlocs mechanics, not private-repository semantics.",
            "Runtime and peak-memory figures are observations from one machine, not thresholds.",
            "The evaluation records are deterministic scripted walkthroughs, not live-model runs.",
            "The fixture validates 91 maps; it does not establish behavior at larger scales.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write the compact measured result as JSON")
    parser.add_argument(
        "--artifacts",
        type=Path,
        help="retain the generated fixture and raw evaluation artifacts in this directory",
    )
    parser.add_argument("--samples", type=int, default=5)
    args = parser.parse_args(argv)
    if args.samples < 1:
        parser.error("--samples must be positive")

    if args.artifacts is not None:
        root = args.artifacts / "repository"
        evaluation = args.artifacts / "evaluation"
        result = run_pilot(root, evaluation, samples=args.samples)
    else:
        with tempfile.TemporaryDirectory(prefix="murlocs-scale-") as temporary:
            temporary_root = Path(temporary)
            result = run_pilot(
                temporary_root / "repository",
                temporary_root / "evaluation",
                samples=args.samples,
            )
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        _write(args.output, rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
