"""Version 1 source-annotation grammar reference.

``parse_v1_comment`` deliberately validates a *comment body*, not a source file.
``resolve_annotations`` separately performs bounded declared-file selection and
wrapper recognition. Keeping the grammar narrow lets the v1 corpus exercise one
portable contract without making source comments authoritative.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from murlocs.errors import MurlocsError
from murlocs.paths import repo_path

if TYPE_CHECKING:
    from murlocs.model import Manifest

NAMESPACE = "murlocs:annotation"
VERSION = "v1"
KIND = "evidence"
MAX_COMMENT_BYTES = 256
MAX_IDENTIFIER_BYTES = 128
MAX_DECLARED_FILES = 256
MAX_PATH_COMPONENTS = 16
MAX_FILE_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_CANDIDATE_COMMENTS = 1024
MAX_RESOLUTION_SECONDS = 2.0
_IDENTIFIER = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")

_LINE_WRAPPERS = {
    ".py": "#",
    ".sh": "#",
    ".bash": "#",
    ".zsh": "#",
    ".yaml": "#",
    ".yml": "#",
    ".toml": "#",
    ".go": "//",
    ".js": "//",
    ".jsx": "//",
    ".ts": "//",
    ".tsx": "//",
    ".rs": "//",
    ".c": "//",
    ".h": "//",
    ".cc": "//",
    ".cpp": "//",
    ".java": "//",
    ".sql": "--",
    ".ini": ";",
    ".cfg": ";",
}
_BLOCK_WRAPPERS = {
    ".css": ("/*", "*/"),
    ".html": ("<!--", "-->"),
    ".htm": ("<!--", "-->"),
    ".xml": ("<!--", "-->"),
}
_EXCLUDED_PARTS = frozenset(
    {
        ".git",
        "build",
        "dist",
        "gen",
        "generated",
        "node_modules",
        "third_party",
        "third-party",
        "vendor",
        "vendors",
    }
)


@dataclass(frozen=True)
class Annotation:
    """A grammar-valid, still-untrusted source attachment candidate."""

    identifier: str
    kind: str = KIND
    version: str = VERSION


@dataclass(frozen=True)
class AnnotationFinding:
    """A stable grammar finding with no retained source prose."""

    code: str


@dataclass(frozen=True)
class AnnotationLocation:
    """A normalized source location with no retained source content."""

    file: str
    line: int


@dataclass(frozen=True)
class AnnotationBinding:
    """One inert declaration-to-source attachment produced by the resolver."""

    identifier: str
    kind: str
    version: str
    invariant: str
    scope: str
    location: AnnotationLocation


@dataclass(frozen=True)
class AnnotationResolverFinding:
    """A stable, bounded resolver diagnostic; it never includes source prose."""

    code: str
    identifier: str | None = None
    invariant: str | None = None
    location: AnnotationLocation | None = None


@dataclass(frozen=True)
class AnnotationResolution:
    """Deterministic output from declared-file-only source annotation resolution."""

    bindings: tuple[AnnotationBinding, ...] = ()
    findings: tuple[AnnotationResolverFinding, ...] = ()


def parse_v1_comment(comment: str) -> Annotation | AnnotationFinding | None:
    """Parse one already-recognized comment body under the v1 grammar.

    ``None`` means the comment is outside the Murlocs namespace. The parser never
    searches source text, follows paths, or promotes a parsed result to guidance.
    """
    if not comment.startswith(NAMESPACE):
        return None
    if len(comment.encode("utf-8")) > MAX_COMMENT_BYTES:
        return AnnotationFinding("annotation.malformed")
    if not comment.startswith(f"{NAMESPACE}/"):
        return AnnotationFinding("annotation.malformed")

    remainder = comment.removeprefix(f"{NAMESPACE}/")
    version, separator, directive = remainder.partition(" ")
    if not separator:
        return AnnotationFinding("annotation.malformed")
    if version != VERSION:
        return AnnotationFinding("annotation.unknown-version")
    if not directive.startswith(f"{KIND} "):
        return AnnotationFinding("annotation.unknown-kind")

    quoted_identifier = directive.removeprefix(f"{KIND} ")
    if (
        len(quoted_identifier) < 2
        or not quoted_identifier.startswith('"')
        or not quoted_identifier.endswith('"')
    ):
        return AnnotationFinding("annotation.malformed")
    identifier = quoted_identifier[1:-1]
    if '"' in identifier or "\\" in identifier:
        return AnnotationFinding("annotation.malformed")
    if len(identifier.encode("utf-8")) > MAX_IDENTIFIER_BYTES or not _IDENTIFIER.fullmatch(
        identifier
    ):
        return AnnotationFinding("annotation.malformed")
    return Annotation(identifier=identifier)


def resolve_annotations(manifest: Manifest) -> AnnotationResolution:
    """Resolve reviewed v1 declarations against their finite declared file set.

    This function is intentionally read-only and local.  It does not walk source
    directories, parse arbitrary prose, execute commands, or promote bindings to
    guidance.  Callers receive only normalized ids and file/line locations.
    """
    declarations = [item for item in manifest.invariants if item.annotation is not None]
    if not declarations:
        return AnnotationResolution()
    started = time.monotonic()
    if (
        len({item.annotation.file for item in declarations if item.annotation is not None})
        > MAX_DECLARED_FILES
    ):
        return _limit_resolution()

    declared_by_id: dict[str, object] = {}
    files: dict[str, list[object]] = {}
    findings: list[AnnotationResolverFinding] = []
    for invariant in declarations:
        annotation = invariant.annotation
        assert annotation is not None
        if annotation.version != VERSION or annotation.kind != KIND:
            findings.append(
                AnnotationResolverFinding(
                    code="annotation.unsupported",
                    identifier=annotation.identifier,
                    invariant=invariant.id,
                )
            )
            continue
        if parse_v1_comment(
            f'{NAMESPACE}/{annotation.version} {annotation.kind} "{annotation.identifier}"'
        ) != Annotation(identifier=annotation.identifier):
            findings.append(
                AnnotationResolverFinding(
                    code="annotation.malformed",
                    identifier=annotation.identifier,
                    invariant=invariant.id,
                )
            )
            continue
        if annotation.identifier in declared_by_id:
            findings.append(
                AnnotationResolverFinding(
                    code="annotation.duplicate", identifier=annotation.identifier
                )
            )
            continue
        declared_by_id[annotation.identifier] = invariant
        files.setdefault(annotation.file, []).append(invariant)

    if findings:
        return AnnotationResolution(findings=tuple(_ordered_findings(findings)))

    comments: dict[str, list[tuple[Annotation, AnnotationLocation]]] = {}
    total_bytes = 0
    candidates = 0
    for raw_path in sorted(files):
        if time.monotonic() - started > MAX_RESOLUTION_SECONDS:
            return _limit_resolution()
        candidate, boundary = _declared_file(manifest.root, raw_path)
        if boundary is not None:
            findings.extend(
                AnnotationResolverFinding(
                    code=boundary,
                    identifier=invariant.annotation.identifier,
                    invariant=invariant.id,
                )
                for invariant in files[raw_path]
            )
            continue
        assert candidate is not None
        try:
            size = candidate.stat().st_size
        except OSError:
            findings.extend(
                AnnotationResolverFinding(
                    code="annotation.excluded",
                    identifier=invariant.annotation.identifier,
                    invariant=invariant.id,
                )
                for invariant in files[raw_path]
            )
            continue
        if size > MAX_FILE_BYTES or total_bytes + size > MAX_TOTAL_BYTES:
            return _limit_resolution()
        total_bytes += size
        try:
            raw = candidate.read_bytes()
        except OSError:
            findings.extend(
                AnnotationResolverFinding(
                    code="annotation.undecodable",
                    identifier=invariant.annotation.identifier,
                    invariant=invariant.id,
                )
                for invariant in files[raw_path]
            )
            continue
        if len(raw) != size:
            return _limit_resolution()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            findings.extend(
                AnnotationResolverFinding(
                    code="annotation.undecodable",
                    identifier=invariant.annotation.identifier,
                    invariant=invariant.id,
                )
                for invariant in files[raw_path]
            )
            continue
        if text.startswith("\ufeff"):
            findings.extend(
                AnnotationResolverFinding(
                    code="annotation.undecodable",
                    identifier=invariant.annotation.identifier,
                    invariant=invariant.id,
                )
                for invariant in files[raw_path]
            )
            continue
        scanned, scanned_findings, count = _scan_comments(raw_path, text)
        candidates += count
        if (
            candidates > MAX_CANDIDATE_COMMENTS
            or time.monotonic() - started > MAX_RESOLUTION_SECONDS
        ):
            return _limit_resolution()
        comments[raw_path] = scanned
        for finding in scanned_findings:
            if finding.code == "annotation.unsupported" and finding.location is None:
                findings.extend(
                    AnnotationResolverFinding(
                        code=finding.code,
                        identifier=invariant.annotation.identifier,
                        invariant=invariant.id,
                    )
                    for invariant in files[raw_path]
                )
            else:
                findings.append(finding)

    bindings: list[AnnotationBinding] = []
    for raw_path in sorted(files):
        for annotation, location in comments.get(raw_path, []):
            invariant = declared_by_id.get(annotation.identifier)
            if invariant is None:
                findings.append(
                    AnnotationResolverFinding(
                        code="annotation.orphaned",
                        identifier=annotation.identifier,
                        location=location,
                    )
                )
            elif invariant.annotation.file != raw_path:
                findings.append(
                    AnnotationResolverFinding(
                        code="annotation.misplaced",
                        identifier=annotation.identifier,
                        invariant=invariant.id,
                        location=location,
                    )
                )
            else:
                bindings.append(
                    AnnotationBinding(
                        identifier=annotation.identifier,
                        kind=annotation.kind,
                        version=annotation.version,
                        invariant=invariant.id,
                        scope=invariant.scope,
                        location=location,
                    )
                )

    for invariant in declarations:
        annotation = invariant.annotation
        assert annotation is not None
        matches = [binding for binding in bindings if binding.identifier == annotation.identifier]
        if not matches:
            if not any(finding.identifier == annotation.identifier for finding in findings):
                findings.append(
                    AnnotationResolverFinding(
                        code="annotation.missing",
                        identifier=annotation.identifier,
                        invariant=invariant.id,
                    )
                )
        elif len(matches) > 1:
            findings.append(
                AnnotationResolverFinding(
                    code="annotation.duplicate",
                    identifier=annotation.identifier,
                    invariant=invariant.id,
                )
            )

    if findings:
        # An error in this finite relationship domain never becomes partial evidence.
        return AnnotationResolution(findings=tuple(_ordered_findings(findings)))
    return AnnotationResolution(bindings=tuple(sorted(bindings, key=_binding_key)))


def _declared_file(root: Path, raw: str) -> tuple[Path | None, str | None]:
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts or len(path.parts) > MAX_PATH_COMPONENTS:
        return None, "annotation.excluded"
    if any(part.casefold() in _EXCLUDED_PARTS for part in path.parts):
        return None, "annotation.excluded"
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            return None, "annotation.excluded"
    try:
        candidate = repo_path(root, raw, field="annotation file")
    except MurlocsError:
        return None, "annotation.excluded"
    if not candidate.is_file():
        return None, "annotation.excluded"
    return candidate, None


def _scan_comments(
    raw_path: str, text: str
) -> tuple[list[tuple[Annotation, AnnotationLocation]], list[AnnotationResolverFinding], int]:
    suffix = Path(raw_path).suffix.casefold()
    line_wrapper = _LINE_WRAPPERS.get(suffix)
    block_wrapper = _BLOCK_WRAPPERS.get(suffix)
    if line_wrapper is None and block_wrapper is None:
        return [], [AnnotationResolverFinding(code="annotation.unsupported")], 0
    found: list[tuple[Annotation, AnnotationLocation]] = []
    findings: list[AnnotationResolverFinding] = []
    candidates = 0
    for number, line in enumerate(text.splitlines(), start=1):
        body = _comment_body(line, line_wrapper, block_wrapper)
        if body is None:
            continue
        parsed = parse_v1_comment(body)
        if parsed is None:
            continue
        candidates += 1
        if isinstance(parsed, Annotation):
            found.append((parsed, AnnotationLocation(file=raw_path, line=number)))
        else:
            findings.append(
                AnnotationResolverFinding(
                    code=parsed.code,
                    location=AnnotationLocation(file=raw_path, line=number),
                )
            )
    return found, findings, candidates


def _comment_body(
    line: str, line_wrapper: str | None, block_wrapper: tuple[str, str] | None
) -> str | None:
    stripped = line.strip(" \t")
    if line_wrapper and stripped.startswith(line_wrapper):
        return stripped[len(line_wrapper) :].removeprefix(" ")
    if (
        block_wrapper
        and stripped.startswith(block_wrapper[0])
        and stripped.endswith(block_wrapper[1])
    ):
        body = stripped[len(block_wrapper[0]) : -len(block_wrapper[1])]
        return body.removeprefix(" ").removesuffix(" ")
    return None


def _limit_resolution() -> AnnotationResolution:
    return AnnotationResolution(
        findings=(AnnotationResolverFinding(code="annotation.resource-limit"),)
    )


def _binding_key(binding: AnnotationBinding) -> tuple[str, str, int]:
    return binding.identifier, binding.location.file, binding.location.line


def _finding_key(
    finding: AnnotationResolverFinding,
) -> tuple[str, str, str, int]:
    location = finding.location
    return (
        finding.code,
        finding.identifier or "",
        location.file if location else "",
        location.line if location else 0,
    )


def _ordered_findings(
    findings: list[AnnotationResolverFinding],
) -> list[AnnotationResolverFinding]:
    return sorted(set(findings), key=_finding_key)
