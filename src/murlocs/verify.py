from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

from murlocs.codeowners import find_codeowners, normalize_path, parse_codeowners
from murlocs.errors import MurlocsError
from murlocs.lockfile import Lock, read_lock, sha256_bytes, sha256_text
from murlocs.model import Invariant, LayerSource, Manifest, Scope
from murlocs.paths import relative_posix, repo_path, repo_path_within, resolve_root
from murlocs.render import render_outputs
from murlocs.source_annotations import (
    AnnotationLocation,
    AnnotationResolverFinding,
    resolve_annotations,
)

SEVERITY_EQUIVALENTS = {
    "critical": "critical",
    "important": "important",
    "advisory": "advisory",
    "P0": "critical",
    "P1": "important",
    "P2": "advisory",
    "P3": "advisory",
}


@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    annotation_id: str | None = None
    invariant_ids: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    locations: tuple[AnnotationLocation, ...] = ()
    declaration_sources: tuple[str, ...] = ()
    source_paths: tuple[str, ...] = ()
    annotation_boundary: str | None = None

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


def validate(manifest: Manifest) -> list[Finding]:
    findings: list[Finding] = []
    if manifest.schema_version != 1:
        findings.append(Finding("schema", f"unsupported schema_version {manifest.schema_version}"))
    if manifest.max_active_bytes <= 0:
        findings.append(Finding("budget", "max_active_bytes must be positive"))

    scope_ids = [scope.id for scope in manifest.scopes]
    _duplicates(scope_ids, "scope", findings)
    map_paths = [scope.map for scope in manifest.scopes]
    _duplicates(map_paths, "map path", findings)
    invariant_ids = [item.id for item in manifest.invariants]
    _duplicates(invariant_ids, "invariant", findings)

    known_scopes = set(scope_ids)
    for scope in manifest.scopes:
        _safe_path(manifest.root, scope.path, "scope path", findings)
        _safe_path(manifest.root, scope.map, "map path", findings)
        for owned in scope.owns.all_paths:
            _safe_path(manifest.root, owned, f"scope {scope.id} ownership path", findings)
        for edge in scope.edges:
            if edge.to not in known_scopes:
                findings.append(
                    Finding("edge", f"scope {scope.id} points to unknown scope {edge.to}")
                )

    if "root" not in known_scopes:
        findings.append(Finding("scope", "a root scope is required"))
    else:
        root_scope = next(scope for scope in manifest.scopes if scope.id == "root")
        if root_scope.path != "." or root_scope.map != "AGENTS.md":
            findings.append(Finding("scope", "root scope must map . to AGENTS.md"))
    invariant_scopes = {item.scope for item in manifest.invariants}
    if manifest.require_scope_invariants:
        for scope_id in sorted(known_scopes - invariant_scopes):
            findings.append(Finding("invariant", f"scope has no invariant: {scope_id}"))
    for invariant in manifest.invariants:
        if invariant.scope not in known_scopes:
            findings.append(
                Finding("invariant", f"{invariant.id} references unknown scope {invariant.scope}")
            )
        if normalize_severity(invariant.severity) is None:
            findings.append(Finding("severity", f"{invariant.id} has invalid severity"))
        if invariant.verification == "command":
            if not invariant.enforced_by or invariant.enforced_by not in manifest.checks:
                findings.append(
                    Finding("proof", f"{invariant.id} names no registered check in enforced_by")
                )
        elif invariant.verification == "manual":
            if not invariant.evidence_file or not invariant.anchor:
                findings.append(Finding("proof", f"{invariant.id} needs evidence_file and anchor"))
            elif not _contains(manifest.root, invariant.evidence_file, invariant.anchor, findings):
                findings.append(Finding("proof", f"{invariant.id} manual evidence was not found"))
        elif invariant.verification != "unknown":
            findings.append(Finding("proof", f"{invariant.id} has invalid verification mode"))

    for name, check in manifest.checks.items():
        if not check.proof_contains:
            findings.append(Finding("proof-debt", f"check {name} has no proof_contains anchor"))
            location = _safe_path(
                manifest.root,
                check.location,
                f"check {name} proof location",
                findings,
            )
            if location is not None and not location.is_file():
                findings.append(
                    Finding("check", f"{name} proof location does not exist: {check.location}")
                )
        elif not _contains(manifest.root, check.location, check.proof_contains, findings):
            findings.append(Finding("check", f"{name} proof was not found at {check.location}"))
        findings.extend(_command_path_findings(manifest.root, name, check.invoke))

    protocol = _safe_path(manifest.root, manifest.protocol, "protocol", findings)
    if protocol is not None and not protocol.is_file():
        findings.append(Finding("protocol", f"protocol file does not exist: {manifest.protocol}"))

    findings.extend(_coverage_findings(manifest))
    findings.extend(_ownership_findings(manifest))
    findings.extend(_drift_findings(manifest))
    return findings


def annotation_findings(manifest: Manifest) -> list[Finding]:
    """Convert bounded inert annotation diagnostics into check findings.

    Resolution deliberately never selects a partial binding.  This layer only
    reports relationship debt: it does not mutate source, reinterpret a marker,
    or execute a repository-provided command.
    """
    resolution = resolve_annotations(manifest)
    if not resolution.findings:
        return []

    declarations: dict[str, list[Invariant]] = {}
    declared_files: dict[str, list[Invariant]] = {}
    for invariant in manifest.invariants:
        if invariant.annotation is None:
            continue
        declarations.setdefault(invariant.annotation.identifier, []).append(invariant)
        declared_files.setdefault(invariant.annotation.file, []).append(invariant)

    return [
        _annotation_finding(manifest, finding, declarations, declared_files)
        for finding in resolution.findings
    ]


def _annotation_finding(
    manifest: Manifest,
    finding: AnnotationResolverFinding,
    declarations: dict[str, list[Invariant]],
    declared_files: dict[str, list[Invariant]],
) -> Finding:
    related = list(declarations.get(finding.identifier or "", ()))
    if not related and finding.location is not None:
        related = list(declared_files.get(finding.location.file, ()))
    related.sort(key=lambda item: item.id)
    invariant_ids = tuple(item.id for item in related)
    scopes = tuple(sorted({item.scope for item in related}))
    declaration_sources = tuple(
        sorted(
            {
                f"{source.id}@{source.path}"
                for item in related
                if (source := manifest.source_for_invariant(item.id)) is not None
            }
        )
    )
    locations = (finding.location,) if finding.location is not None else ()
    source_paths = tuple(
        sorted(
            {
                *(location.file for location in locations),
                *(item.annotation.file for item in related if item.annotation is not None),
            }
        )
    )
    identifiers = {item.annotation.identifier for item in related if item.annotation is not None}
    inferred_identifier = (
        next(iter(identifiers)) if finding.identifier is None and len(identifiers) == 1 else None
    )
    annotation_id = finding.identifier or inferred_identifier
    identifier = annotation_id or "<unknown>"
    location_text = ", ".join(f"{item.file}:{item.line}" for item in locations) or "<none>"
    invariant_text = ", ".join(invariant_ids) or "<unknown>"
    scope_text = ", ".join(scopes) or "<unknown>"
    source_text = ", ".join(declaration_sources) or "<unknown>"
    boundary = _annotation_boundary(finding, related)
    boundary_text = f"; boundary={boundary}" if boundary is not None else ""
    return Finding(
        finding.code,
        (
            f"annotation id={identifier}; invariants={invariant_text}; scopes={scope_text}; "
            f"locations={location_text}; declarations={source_text}{boundary_text}"
        ),
        annotation_id=annotation_id,
        invariant_ids=invariant_ids,
        scopes=scopes,
        locations=locations,
        declaration_sources=declaration_sources,
        source_paths=source_paths,
        annotation_boundary=boundary,
    )


def _annotation_boundary(
    finding: AnnotationResolverFinding, related: list[Invariant]
) -> str | None:
    """Name the finite exclusion category without exposing source contents."""
    if finding.code != "annotation.excluded":
        return None
    parts = {
        part.casefold()
        for item in related
        if item.annotation is not None
        for part in Path(item.annotation.file).parts
    }
    if parts & {"vendor", "vendors", "third_party", "third-party"}:
        return "vendored"
    if parts & {"build", "dist", "gen", "generated"}:
        return "generated"
    return "excluded"


def _ownership_findings(manifest: Manifest) -> list[Finding]:
    """Enforce source-owner and opt-in CODEOWNERS policies for layered manifests."""
    if not manifest.layered:
        return []
    findings: list[Finding] = []
    if manifest.require_layer_owners:
        for source in manifest.sources:
            if not source.owners:
                findings.append(Finding("ownership", f"{_source_label(source)} declares no owner"))
    if manifest.validate_codeowners:
        findings.extend(_codeowners_findings(manifest, manifest.sources))
    return findings


def _codeowners_findings(manifest: Manifest, sources: tuple[LayerSource, ...]) -> list[Finding]:
    codeowners = find_codeowners(manifest.root)
    if codeowners is None:
        return [
            Finding(
                "ownership",
                "validate_codeowners is enabled but no CODEOWNERS file was found",
            )
        ]
    entries = parse_codeowners(codeowners.read_text(encoding="utf-8"))
    findings: list[Finding] = []
    for source in sources:
        label = _source_label(source)
        path = normalize_path(source.path)
        if path not in entries:
            findings.append(
                Finding(
                    "ownership",
                    f"{label} has no exact CODEOWNERS entry: {source.path} "
                    f"(expected owners: {sorted(source.owners)})",
                )
            )
            continue
        if set(entries[path]) != set(source.owners):
            findings.append(
                Finding(
                    "ownership",
                    f"{label} owners do not match CODEOWNERS: "
                    f"expected={sorted(source.owners)} actual={sorted(entries[path])}",
                )
            )
    return findings


def _source_label(source: LayerSource) -> str:
    return "root manifest" if source.id == "manifest" else f"layer {source.id}"


def _duplicates(values: list[str], label: str, findings: list[Finding]) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            findings.append(Finding("duplicate", f"duplicate {label}: {value}"))
        seen.add(value)


def _safe_path(root: Path, raw: str, label: str, findings: list[Finding]) -> Path | None:
    try:
        return repo_path(root, raw, field=label)
    except MurlocsError as exc:
        findings.append(Finding("path", str(exc)))
        return None


def _contains(root: Path, raw: str, needle: str, findings: list[Finding]) -> bool:
    path = _safe_path(root, raw, "evidence path", findings)
    if path is None or not path.is_file():
        return False
    try:
        return needle in path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return False


def normalize_severity(value: str) -> str | None:
    """Return the canonical meaning while preserving the manifest's original spelling."""
    return SEVERITY_EQUIVALENTS.get(value)


def _command_path_findings(root: Path, name: str, invoke: str) -> list[Finding]:
    try:
        tokens = shlex.split(invoke)
    except ValueError as exc:
        return [Finding("check", f"{name} command cannot be parsed: {exc}")]

    top_level = {path.name for path in root.iterdir()}
    findings: list[Finding] = []
    checked: set[str] = set()
    for token in tokens:
        candidate = token.split("::", 1)[0]
        if (
            not candidate
            or candidate.startswith("-")
            or "://" in candidate
            or "=" in candidate
            or any(character in candidate for character in "*?[]{}")
        ):
            continue
        path = Path(candidate)
        parts = path.parts
        looks_local = candidate == "." or candidate.startswith("../")
        if parts and parts[0] in top_level:
            looks_local = True
        if path.is_absolute():
            try:
                candidate = path.resolve().relative_to(root.resolve()).as_posix()
                looks_local = True
            except ValueError:
                continue
        if not looks_local or candidate in checked:
            continue
        checked.add(candidate)
        try:
            local = repo_path(root, candidate, field=f"check {name} command path")
        except MurlocsError as exc:
            findings.append(Finding("check", str(exc)))
            continue
        if not local.exists():
            findings.append(Finding("check", f"{name} command path does not exist: {candidate}"))
    return findings


def _coverage_findings(manifest: Manifest) -> list[Finding]:
    findings: list[Finding] = []
    mapped_dirs = {
        relative_posix(manifest.root, repo_path(manifest.root, scope.map, field="map").parent)
        for scope in manifest.scopes
        if _is_safe(manifest.root, scope.map)
    }
    exemptions = manifest.coverage_exemptions
    for root_name in manifest.coverage_roots:
        coverage_root = _safe_path(manifest.root, root_name, "coverage root", findings)
        if coverage_root is None or not coverage_root.exists():
            findings.append(Finding("coverage", f"coverage root does not exist: {root_name}"))
            continue
        candidates = [(coverage_root, False)]
        candidates.extend(
            (path, True)
            for path in sorted(coverage_root.iterdir(), key=lambda item: item.name)
            if path.is_dir()
        )
        for candidate, recursive in candidates:
            children = candidate.rglob("*") if recursive else candidate.iterdir()
            has_source = any(
                child.is_file() and child.suffix in manifest.source_suffixes for child in children
            )
            if not has_source:
                continue
            relative = relative_posix(manifest.root, candidate)
            if relative in mapped_dirs:
                continue
            reason = exemptions.get(relative, "").strip()
            if not reason:
                findings.append(Finding("coverage", f"source-bearing unit has no map: {relative}"))
    for path, reason in sorted(exemptions.items()):
        if not reason.strip():
            findings.append(Finding("coverage", f"exemption has no reason: {path}"))
    findings.sort(key=lambda item: item.message)
    return findings


def _is_safe(root: Path, raw: str) -> bool:
    try:
        repo_path(root, raw, field="path")
        return True
    except MurlocsError:
        return False


def _drift_findings(manifest: Manifest) -> list[Finding]:
    findings: list[Finding] = []
    expected = render_outputs(manifest)
    active_bytes = _maximum_active_bytes(manifest, expected)
    if active_bytes > manifest.max_active_bytes:
        findings.append(
            Finding(
                "budget",
                f"generated guidance is {active_bytes} bytes; "
                f"budget is {manifest.max_active_bytes}",
            )
        )
    try:
        lock = read_lock(manifest.root)
    except MurlocsError as exc:
        return [Finding("lock", str(exc))]
    if lock is None:
        return findings + [Finding("lock", "lockfile is missing; run murlocs compile")]
    if lock.manifest_sha256 != sha256_bytes(manifest.manifest_path.read_bytes()):
        findings.append(Finding("drift", "manifest changed since the last compile"))
    findings.extend(_source_drift(manifest, lock))
    for relative, content in expected.items():
        if not _is_safe(manifest.root, relative):
            continue
        path = repo_path(manifest.root, relative, field="map")
        if not path.is_file():
            findings.append(Finding("drift", f"generated map is missing: {relative}"))
            continue
        actual = sha256_bytes(path.read_bytes())
        if lock.generated.get(relative) != actual or actual != sha256_text(content):
            findings.append(Finding("drift", f"generated map is stale or modified: {relative}"))
    for orphaned in sorted(set(lock.generated) - set(expected)):
        findings.append(Finding("drift", f"lockfile owns undeclared map: {orphaned}"))
    return findings


def _source_drift(manifest: Manifest, lock: Lock) -> list[Finding]:
    """Verify the ordered layer set and its content hashes against the lockfile."""
    if not lock.sources:
        # A pre-layering lockfile only records the root manifest hash, which the
        # caller already checks. Nothing more to compare for a single-file manifest.
        return []
    findings: list[Finding] = []
    locked = [(item.path, item.sha256) for item in lock.sources]
    current = [(item.path, item.sha256) for item in manifest.sources]
    if [path for path, _ in locked] != [path for path, _ in current]:
        findings.append(Finding("drift", "layer set changed since the last compile"))
        return findings
    for (path, expected), (_, actual) in zip(locked, current, strict=True):
        if expected != actual and path != ".murlocs/manifest.toml":
            findings.append(Finding("drift", f"layer changed since the last compile: {path}"))
    return findings


def _maximum_active_bytes(manifest: Manifest, outputs: dict[str, str]) -> int:
    """Return the largest root-to-leaf chain size, in bytes, over all scopes.

    Resolution is hoisted deliberately. This used to call `repo_path` from
    inside the nested loop, so an n-scope network performed O(n²) `realpath`
    walks — 110k `lstat` calls for 91 scopes, and 92% of `murlocs check`. The
    comparison itself is pure string work once the paths are resolved.
    """
    root_resolved = resolve_root(manifest.root)
    resolved: list[tuple[Scope, Path]] = []
    for scope in manifest.scopes:
        try:
            scope_root = repo_path_within(root_resolved, scope.path, field="scope path")
            repo_path_within(root_resolved, scope.map, field="scope map")
        except MurlocsError:
            continue
        resolved.append((scope, scope_root))

    sizes = {scope.map: len(outputs[scope.map].encode("utf-8")) for scope, _ in resolved}
    maximum = 0
    for _, target_root in resolved:
        active = 0
        for candidate, candidate_root in resolved:
            try:
                target_root.relative_to(candidate_root)
            except ValueError:
                continue
            active += sizes[candidate.map]
        maximum = max(maximum, active)
    return maximum
