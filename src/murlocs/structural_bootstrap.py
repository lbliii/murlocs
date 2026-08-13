"""Deterministic greenfield structural bootstrap for Murlocs guidance networks.

Bootstrap completes the topology index: initialize when safe, add scopes for every
source-bearing unit reported by coverage validation, and compile until structural
coverage is complete. It does not invent semantic intent; layer content remains
generic until authors or the bootstrap skill enrich it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from murlocs.errors import MurlocsError
from murlocs.manifest import (
    PROTOCOL_TEMPLATE,
    infer_coverage_roots,
    load_manifest,
    render_manifest,
)
from murlocs.model import Manifest
from murlocs.render import compile_manifest
from murlocs.rollout import apply_add_scope, plan_add_scope
from murlocs.verify import Finding, validate

_UNCOVERED_PREFIX = "source-bearing unit has no map: "


@dataclass(frozen=True)
class StructuralBootstrapResult:
    initialized: bool
    network: str
    scopes_added: tuple[str, ...]
    generated: tuple[str, ...]
    coverage_roots: tuple[str, ...]
    findings: tuple[Finding, ...]

    @property
    def structurally_complete(self) -> bool:
        return not any(item.code == "coverage" for item in self.findings)

    @property
    def ok(self) -> bool:
        blocking = [
            item for item in self.findings if item.code not in {"coverage", "drift", "lock"}
        ]
        return not blocking and self.structurally_complete


def uncovered_scope_paths(manifest: Manifest) -> list[str]:
    """Return sorted repository-relative paths that still need scoped maps."""
    paths: list[str] = []
    for item in validate(manifest):
        if item.code != "coverage":
            continue
        message = str(item.message)
        if message.startswith(_UNCOVERED_PREFIX):
            paths.append(message.removeprefix(_UNCOVERED_PREFIX))
    return sorted(set(paths))


def _normalize_coverage_roots(root: Path, entries: list[str]) -> list[str]:
    from murlocs.paths import repo_path

    normalized: list[str] = []
    for entry in entries:
        target = repo_path(root, entry, field="coverage root")
        if not target.is_dir():
            raise MurlocsError(f"coverage root is not a directory: {entry}")
        relative = target.relative_to(root).as_posix()
        if relative not in normalized:
            normalized.append(relative)
    return normalized


def _initialize_manifest(
    root: Path,
    *,
    network: str,
    coverage_roots: list[str],
) -> list[str]:
    manifest_path = root / ".murlocs" / "manifest.toml"
    protocol_path = root / ".murlocs" / "PROTOCOL.md"
    if manifest_path.exists():
        raise MurlocsError(f"manifest already exists: {manifest_path}")
    if (root / "AGENTS.md").exists():
        raise MurlocsError(
            "AGENTS.md already exists and is unmanaged; "
            "migrate it into the manifest before compiling"
        )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(render_manifest(network, tuple(coverage_roots)), encoding="utf-8")
    protocol_path.write_text(PROTOCOL_TEMPLATE, encoding="utf-8")
    manifest = load_manifest(root)
    blocking = [
        item for item in validate(manifest) if item.code not in {"coverage", "drift", "lock"}
    ]
    if blocking:
        messages = "; ".join(str(item) for item in blocking)
        raise MurlocsError(f"starter manifest is not valid: {messages}")
    return compile_manifest(manifest)


def complete_structural_coverage(
    root: Path,
    *,
    max_rounds: int = 64,
) -> tuple[tuple[str, ...], list[str]]:
    """Add scopes until coverage validation reports no uncovered units."""
    if max_rounds < 1:
        raise MurlocsError("max_rounds must be at least 1")

    scopes_added: list[str] = []
    generated: list[str] = []
    for _ in range(max_rounds):
        manifest = load_manifest(root)
        uncovered = uncovered_scope_paths(manifest)
        if not uncovered:
            return tuple(scopes_added), generated
        path = uncovered[0]
        plan, candidate = plan_add_scope(root, path)
        written = apply_add_scope(root, plan, candidate)
        scopes_added.append(plan.scope_id)
        generated.extend(written)
    raise MurlocsError(
        f"structural bootstrap exceeded {max_rounds} scope rounds; "
        f"remaining uncovered: {', '.join(uncovered_scope_paths(load_manifest(root)))}"
    )


def plan_structural_bootstrap(
    root: Path,
    *,
    name: str | None = None,
    coverage_roots: list[str] | None = None,
) -> tuple[bool, str, tuple[str, ...], tuple[str, ...], tuple[Finding, ...]]:
    """Preview structural bootstrap without writing."""
    manifest_path = root / ".murlocs" / "manifest.toml"
    initialized = not manifest_path.exists()
    if initialized:
        if (root / "AGENTS.md").exists():
            raise MurlocsError(
                "AGENTS.md already exists and is unmanaged; "
                "migrate it into the manifest before compiling"
            )
        network = name or root.name
        entries = infer_coverage_roots(root) if coverage_roots is None else coverage_roots
        roots = _normalize_coverage_roots(root, entries)
        import tomllib

        from murlocs.manifest import parse_manifest_data

        preview = parse_manifest_data(
            root,
            tomllib.loads(render_manifest(network, tuple(roots))),
        )
        findings = tuple(validate(preview))
        uncovered = tuple(uncovered_scope_paths(preview))
        return initialized, network, roots, uncovered, findings

    manifest = load_manifest(root)
    network = manifest.network
    roots = tuple(manifest.coverage_roots)
    uncovered = tuple(uncovered_scope_paths(manifest))
    findings = tuple(validate(manifest))
    return initialized, network, roots, uncovered, findings


def run_structural_bootstrap(
    root: Path,
    *,
    name: str | None = None,
    coverage_roots: list[str] | None = None,
    max_rounds: int = 64,
) -> StructuralBootstrapResult:
    """Initialize when needed and complete structural coverage."""
    manifest_path = root / ".murlocs" / "manifest.toml"
    initialized = False
    generated: list[str] = []

    if not manifest_path.exists():
        network = name or root.name
        entries = infer_coverage_roots(root) if coverage_roots is None else coverage_roots
        roots = _normalize_coverage_roots(root, entries)
        generated.extend(_initialize_manifest(root, network=network, coverage_roots=roots))
        initialized = True
    else:
        manifest = load_manifest(root)
        network = manifest.network
        roots = list(manifest.coverage_roots)

    scopes_added, scope_generated = complete_structural_coverage(root, max_rounds=max_rounds)
    generated.extend(scope_generated)
    manifest = load_manifest(root)
    findings = tuple(validate(manifest))
    return StructuralBootstrapResult(
        initialized=initialized,
        network=network,
        scopes_added=scopes_added,
        generated=tuple(dict.fromkeys(generated)),
        coverage_roots=tuple(roots),
        findings=findings,
    )
