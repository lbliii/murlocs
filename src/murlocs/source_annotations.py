"""Version 1 source-annotation grammar reference.

``parse_v1_comment`` deliberately validates a *comment body*, not a source file.
``resolve_annotations`` separately performs bounded declared-file selection and
wrapper recognition. Keeping the grammar narrow lets the v1 corpus exercise one
portable contract without making source comments authoritative.
"""

from __future__ import annotations

import io
import os
import re
import stat
import time
import tokenize
from dataclasses import dataclass
from fnmatch import fnmatchcase
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
_C_STYLE_SUFFIXES = frozenset(
    {".go", ".js", ".jsx", ".ts", ".tsx", ".rs", ".c", ".h", ".cc", ".cpp", ".java"}
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
        raw, boundary = _read_declared_file(candidate)
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
        assert raw is not None
        if len(raw) > MAX_FILE_BYTES or total_bytes + len(raw) > MAX_TOTAL_BYTES:
            return _limit_resolution()
        total_bytes += len(raw)
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
    if (
        "\\" in raw
        or path.is_absolute()
        or ".." in path.parts
        or len(path.parts) > MAX_PATH_COMPONENTS
    ):
        return None, "annotation.excluded"
    if any(part.casefold() in _EXCLUDED_PARTS for part in path.parts):
        return None, "annotation.excluded"
    if _is_ignored(root, path):
        return None, "annotation.excluded"
    current = root
    for index, part in enumerate(path.parts):
        current = current / part
        if current.is_symlink():
            return None, "annotation.excluded"
        if index < len(path.parts) - 1 and (current / ".git").exists():
            return None, "annotation.excluded"
    try:
        candidate = repo_path(root, raw, field="annotation file")
    except MurlocsError:
        return None, "annotation.excluded"
    if not candidate.is_file():
        return None, "annotation.excluded"
    return candidate, None


def _is_ignored(root: Path, path: Path) -> bool:
    """Apply bounded repository-local ignore files without asking Git to execute.

    This deliberately implements the portable subset needed for a declared-file
    safety boundary: ordered comments, negation, directory rules, basename rules,
    and slash paths.  Unknown or unreadable ignore input fails closed.
    """
    ignored = False
    parts = path.parts
    for depth in range(len(parts)):
        directory = root.joinpath(*parts[:depth])
        ignore_file = directory / ".gitignore"
        if not ignore_file.exists():
            continue
        if ignore_file.is_symlink():
            return True
        try:
            raw = ignore_file.read_bytes()
            if len(raw) > MAX_FILE_BYTES:
                return True
            lines = raw.decode("utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return True
        relative = "/".join(parts[depth:])
        for line in lines:
            if not line or line.startswith("#"):
                continue
            negated = line.startswith("!")
            pattern = line[1:] if negated else line
            if not pattern or "\\" in pattern:
                return True
            if _ignore_pattern_matches(pattern, relative):
                ignored = not negated
    return ignored


def _ignore_pattern_matches(pattern: str, relative: str) -> bool:
    anchored = pattern.startswith("/")
    directory = pattern.endswith("/")
    pattern = pattern.strip("/")
    if not pattern:
        return False
    if directory:
        if anchored or "/" in pattern:
            return relative == pattern or relative.startswith(pattern + "/")
        return any(
            fnmatchcase(part, pattern)
            for part in relative.split("/")[:-1]
        )
    if anchored or "/" in pattern:
        return fnmatchcase(relative, pattern)
    return any(fnmatchcase(part, pattern) for part in relative.split("/"))


def _read_declared_file(candidate: Path) -> tuple[bytes | None, str | None]:
    """Read one regular file through a no-follow descriptor and recheck its identity.

    Path checks alone cannot close the interval between ``is_file`` and opening the
    file.  A descriptor pins the object we inspect; ``O_NOFOLLOW`` is used when the
    platform supplies it, and the post-open lstat comparison is a conservative
    fallback for platforms that do not.
    """
    try:
        before = candidate.stat(follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            return None, "annotation.excluded"
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(candidate, flags)
    except OSError:
        return None, "annotation.excluded"
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode):
                return None, "annotation.excluded"
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                return None, "annotation.resource-limit"
            raw = handle.read(MAX_FILE_BYTES + 1)
            after = os.fstat(handle.fileno())
        current = candidate.stat(follow_symlinks=False)
    except OSError:
        return None, "annotation.excluded"
    if (
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
    ):
        return None, "annotation.resource-limit"
    return raw, None


def _scan_comments(
    raw_path: str, text: str
) -> tuple[list[tuple[Annotation, AnnotationLocation]], list[AnnotationResolverFinding], int]:
    suffix = Path(raw_path).suffix.casefold()
    if suffix == ".py":
        bodies = _python_comment_bodies(text)
    elif suffix in _C_STYLE_SUFFIXES:
        bodies = _c_style_comment_bodies(text, line_wrapper="//", block_wrapper=("/*", "*/"))
    elif suffix == ".css":
        bodies = _c_style_comment_bodies(text, line_wrapper=None, block_wrapper=("/*", "*/"))
    elif suffix in {".html", ".htm", ".xml"}:
        bodies = _html_comment_bodies(text)
    elif suffix in {".sh", ".bash", ".zsh"}:
        bodies = _shell_comment_bodies(text)
    elif suffix in {".yaml", ".yml"}:
        bodies = _yaml_comment_bodies(text)
    elif suffix == ".toml":
        bodies = _toml_comment_bodies(text)
    elif suffix == ".sql":
        bodies = _sql_comment_bodies(text)
    elif suffix in {".ini", ".cfg"}:
        bodies = _indented_comment_bodies(text, ";")
    else:
        return [], [AnnotationResolverFinding(code="annotation.unsupported")], 0
    found: list[tuple[Annotation, AnnotationLocation]] = []
    findings: list[AnnotationResolverFinding] = []
    candidates = 0
    for number, body in bodies:
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


def _python_comment_bodies(text: str) -> list[tuple[int, str]]:
    """Return actual Python COMMENT tokens, excluding quoted and triple-quoted text."""
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        return [
            (token.start[0], token.string[1:].removeprefix(" "))
            for token in tokens
            if token.type == tokenize.COMMENT
        ]
    except (tokenize.TokenError, IndentationError):
        # An incomplete source file cannot safely establish a comment boundary.
        return []


def _indented_comment_bodies(text: str, wrapper: str) -> list[tuple[int, str]]:
    """Return comment-only lines, accepting indentation but no inline guessing."""
    return [
        (number, stripped[len(wrapper) :].removeprefix(" "))
        for number, line in enumerate(text.splitlines(), start=1)
        if (stripped := line.lstrip(" \t")).startswith(wrapper)
    ]


def _shell_comment_bodies(text: str) -> list[tuple[int, str]]:
    """Recognize indented shell comments while excluding heredoc and quote content."""
    comments: list[tuple[int, str]] = []
    heredoc: tuple[str, bool] | None = None
    quote: str | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip(" \t")
        if heredoc is not None:
            terminator, strip_tabs = heredoc
            candidate = line.lstrip("\t") if strip_tabs else line
            if candidate == terminator:
                heredoc = None
            continue
        if quote is None and stripped.startswith("#"):
            comments.append((number, stripped[1:].removeprefix(" ")))
            continue
        if quote is None:
            match = re.search(
                r"<<(?P<tabs>-)?\s*(?P<quote>['\"]?)(?P<name>[A-Za-z_][A-Za-z0-9_]*)",
                line,
            )
            if match is not None:
                heredoc = (match.group("name"), bool(match.group("tabs")))
                continue
        escaped = False
        for character in line:
            if escaped:
                escaped = False
                continue
            if character == "\\" and quote == '"':
                escaped = True
                continue
            if quote is None and character in {"'", '"'}:
                quote = character
            elif quote == character:
                quote = None
    return comments


def _yaml_comment_bodies(text: str) -> list[tuple[int, str]]:
    """Recognize YAML comment-only lines without treating block scalar text as code."""
    comments: list[tuple[int, str]] = []
    scalar_indent: int | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip(" \t")
        indent = len(line) - len(stripped)
        if scalar_indent is not None:
            if stripped and indent <= scalar_indent:
                scalar_indent = None
            else:
                continue
        if stripped.startswith("#"):
            comments.append((number, stripped[1:].removeprefix(" ")))
            continue
        if re.match(r"^[^#]*:\s*[>|][0-9+\-]*(?:\s+#.*)?$", stripped):
            scalar_indent = indent
    return comments


def _toml_comment_bodies(text: str) -> list[tuple[int, str]]:
    """Recognize TOML comment-only lines without accepting multiline string text."""
    comments: list[tuple[int, str]] = []
    delimiter: str | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        if delimiter is not None:
            if delimiter in line:
                delimiter = None
            continue
        stripped = line.lstrip(" \t")
        if stripped.startswith("#"):
            comments.append((number, stripped[1:].removeprefix(" ")))
            continue
        for candidate in ('"""', "'''"):
            if candidate in line:
                delimiter = candidate
                break
    return comments


def _sql_comment_bodies(text: str) -> list[tuple[int, str]]:
    """Recognize SQL comment-only lines while respecting multiline quoted literals."""
    comments: list[tuple[int, str]] = []
    quote: str | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        index = 0
        while index < len(line):
            if quote is not None:
                if line[index] == quote:
                    if index + 1 < len(line) and line[index + 1] == quote:
                        index += 2
                        continue
                    quote = None
                index += 1
                continue
            if line.startswith("--", index):
                if not line[:index].strip(" \t"):
                    comments.append((number, line[index + 2 :].removeprefix(" ")))
                break
            if line[index] in {"'", '"'}:
                quote = line[index]
            index += 1
    return comments


def _c_style_comment_bodies(
    text: str, *, line_wrapper: str | None, block_wrapper: tuple[str, str]
) -> list[tuple[int, str]]:
    """A bounded lexer for real C-style comments, deliberately conservative in strings."""
    comments: list[tuple[int, str]] = []
    quote: str | None = None
    raw_end: str | None = None
    in_block = False
    for number, line in enumerate(text.splitlines(), start=1):
        index = 0
        while index < len(line):
            if in_block:
                end = line.find(block_wrapper[1], index)
                if end < 0:
                    break
                in_block = False
                index = end + len(block_wrapper[1])
                continue
            if raw_end is not None:
                end = line.find(raw_end, index)
                if end < 0:
                    break
                terminator = raw_end
                raw_end = None
                index = end + len(terminator)
                continue
            if quote is not None:
                if line[index] == "\\":
                    index += 2
                    continue
                if line[index] == quote:
                    quote = None
                index += 1
                continue
            if line_wrapper and line.startswith(line_wrapper, index):
                comments.append((number, line[index + len(line_wrapper) :].removeprefix(" ")))
                break
            if line.startswith(block_wrapper[0], index):
                end = line.find(block_wrapper[1], index + len(block_wrapper[0]))
                if end < 0:
                    in_block = True
                    break
                comments.append(
                    (
                        number,
                        line[index + len(block_wrapper[0]) : end]
                        .removeprefix(" ")
                        .removesuffix(" "),
                    )
                )
                index = end + len(block_wrapper[1])
                continue
            if line[index] in {'"', "'", "`"}:
                quote = line[index]
                index += 1
                continue
            raw = re.match(r'r(#{0,16})"', line[index:])
            if raw is not None:
                raw_end = '"' + raw.group(1)
                index += len(raw.group(0))
                continue
            index += 1
        if quote in {'"', "'"}:
            quote = None
    return comments


def _html_comment_bodies(text: str) -> list[tuple[int, str]]:
    """Recognize standalone HTML/XML comments while ignoring script element text."""
    comments: list[tuple[int, str]] = []
    in_script = False
    for number, line in enumerate(text.splitlines(), start=1):
        lowered = line.casefold()
        if "</script" in lowered:
            in_script = False
        if not in_script:
            stripped = line.strip(" \t")
            if stripped.startswith("<!--") and stripped.endswith("-->"):
                comments.append(
                    (number, stripped[4:-3].removeprefix(" ").removesuffix(" "))
                )
        if "<script" in lowered and "</script" not in lowered:
            in_script = True
    return comments


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
