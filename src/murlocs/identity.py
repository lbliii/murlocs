"""Bounded, local runtime identity for the installed Murlocs package.

This module intentionally does not invoke Git, a package installer, or the
network.  The result is an inspection aid, not a release attestation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Callable
from contextlib import suppress
from importlib.metadata import Distribution, PackageNotFoundError, distribution
from pathlib import Path
from typing import Literal, TypedDict

from murlocs import __version__

IDENTITY_SCHEMA_VERSION = 1
PROJECT_NAME = "murlocs"
MAX_PACKAGE_FILES = 4_096
MAX_PACKAGE_BYTES = 32 * 1024 * 1024
MAX_SINGLE_FILE_BYTES = 4 * 1024 * 1024
MAX_DIRECT_URL_BYTES = 16 * 1024
MAX_PROVENANCE_VALUE = 512
MAX_METADATA_FILES = 4_096
MAX_PACKAGE_ENTRIES = 8_192

# Keep the executable surface in the fingerprint even if distribution metadata
# is unavailable (for example, a source checkout invoked with ``python -m``).
ENTRY_POINTS = (
    "murlocs=murlocs.cli:main",
    "mrr=murlocs.cli:mrr",
    "murlocs-claude-adapter=murlocs.claude_adapter:main",
    "murlocs-copilot-adapter=murlocs.copilot_adapter:main",
)

InstallationKind = Literal[
    "local-directory", "editable", "vcs", "archive", "index-or-unknown", "unknown"
]
BuildKind = Literal["development", "release", "unknown"]


class BuildIdentity(TypedDict):
    kind: BuildKind
    id: str
    verification: Literal["unverified"]


class InstallationIdentity(TypedDict):
    kind: InstallationKind
    editable: bool
    source_revision: str | None
    archive_hash: str | None


class RuntimeIdentity(TypedDict):
    """The stable v1 machine-readable identity contract."""

    schema_version: Literal[1]
    project: str
    version: str
    build: BuildIdentity
    installation: InstallationIdentity


DistributionProvider = Callable[[str], Distribution]


def runtime_identity(
    *,
    package_root: Path | None = None,
    distribution_provider: DistributionProvider = distribution,
) -> RuntimeIdentity:
    """Return local build identity and PEP 610 installation classification.

    The hash is based on the import package currently executed, not a Git
    checkout.  Any unsafe filesystem condition makes the build classification
    ``unknown`` rather than following links or producing an unbounded hash.
    """
    root = package_root if package_root is not None else Path(__file__).parent
    package_hash = _package_hash(root)
    dist = _distribution_or_none(distribution_provider)
    distribution_version = _distribution_version(dist)
    version = distribution_version or __version__
    installation = _installation_identity(dist)

    if (
        package_hash is None
        or dist is None
        or distribution_version is None
        or installation["kind"] == "unknown"
    ):
        kind: BuildKind = "unknown"
    elif installation["kind"] in {"local-directory", "editable", "vcs"} or _is_development_version(
        version
    ):
        kind = "development"
    else:
        kind = "release"

    # The fallback remains a valid, opaque ID while clearly classifying the
    # result as unknown.  It is never presented as a package-content hash.
    build_id = package_hash or _unknown_build_hash()
    return {
        "schema_version": IDENTITY_SCHEMA_VERSION,
        "project": PROJECT_NAME,
        "version": version,
        "build": {"kind": kind, "id": f"sha256:{build_id}", "verification": "unverified"},
        "installation": installation,
    }


def _distribution_or_none(provider: DistributionProvider) -> Distribution | None:
    try:
        return provider(PROJECT_NAME)
    except PackageNotFoundError, ImportError, OSError, ValueError:
        return None


def _distribution_version(dist: Distribution | None) -> str | None:
    if dist is None:
        return None
    try:
        value = dist.version
    except OSError, ValueError:
        return None
    if not isinstance(value, str) or not value or len(value) > MAX_PROVENANCE_VALUE:
        return None
    return value


def _installation_identity(dist: Distribution | None) -> InstallationIdentity:
    unknown: InstallationIdentity = {
        "kind": "unknown",
        "editable": False,
        "source_revision": None,
        "archive_hash": None,
    }
    if dist is None:
        return unknown
    metadata_available, path = _direct_url_path(dist)
    if not metadata_available:
        return unknown
    if path is None:
        # PEP 610 requires installers not to create direct_url.json for a
        # name/version (normally index) requirement. It cannot prove an index.
        return {
            "kind": "index-or-unknown",
            "editable": False,
            "source_revision": None,
            "archive_hash": None,
        }
    raw = _read_regular_file(path, maximum=MAX_DIRECT_URL_BYTES)
    if raw is None:
        return unknown
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_object)
    except TypeError, ValueError, json.JSONDecodeError:
        return unknown
    if not isinstance(value, dict) or not _bounded_provenance_value(value.get("url")):
        return unknown

    dir_info = value.get("dir_info")
    vcs_info = value.get("vcs_info")
    archive_info = value.get("archive_info")
    present = sum(item is not None for item in (dir_info, vcs_info, archive_info))
    if present != 1:
        return unknown
    if dir_info is not None:
        if not isinstance(dir_info, dict):
            return unknown
        editable = dir_info.get("editable", False)
        if not isinstance(editable, bool):
            return unknown
        return {
            "kind": "editable" if editable else "local-directory",
            "editable": editable,
            "source_revision": None,
            "archive_hash": None,
        }
    if vcs_info is not None:
        if not isinstance(vcs_info, dict):
            return unknown
        vcs = vcs_info.get("vcs")
        revision = vcs_info.get("commit_id")
        if vcs not in {"git", "hg", "bzr", "svn"} or not _safe_identifier(revision):
            return unknown
        return {
            "kind": "vcs",
            "editable": False,
            "source_revision": revision,
            "archive_hash": None,
        }
    if not isinstance(archive_info, dict):
        return unknown
    archive_hash = _archive_hash(archive_info)
    if archive_hash is _MALFORMED:
        return unknown
    return {
        "kind": "archive",
        "editable": False,
        "source_revision": None,
        "archive_hash": archive_hash,
    }


def _bounded_provenance_value(value: object) -> bool:
    return isinstance(value, str) and bool(value) and len(value) <= MAX_PROVENANCE_VALUE


class _MalformedProvenance:
    pass


_MALFORMED = _MalformedProvenance()
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,511}\Z")
_HASH_ALGORITHM = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_HASH_DIGEST = re.compile(r"[0-9a-fA-F]{8,512}\Z")


def _archive_hash(archive_info: dict[object, object]) -> str | None | _MalformedProvenance:
    """Read modern PEP 610 hashes first, with the old single hash as fallback."""
    hashes = archive_info.get("hashes")
    if hashes is not None:
        if not isinstance(hashes, dict) or not hashes:
            return _MALFORMED
        normalized: list[tuple[str, str]] = []
        for algorithm, value in hashes.items():
            if not _safe_hash_algorithm(algorithm) or not _safe_hash_digest(value):
                return _MALFORMED
            normalized.append((algorithm, value))
        algorithm, value = next(
            ((name, digest) for name, digest in normalized if name == "sha256"),
            min(normalized),
        )
        return f"{algorithm}={value}"
    legacy = archive_info.get("hash")
    if legacy is None:
        return None
    if not _bounded_provenance_value(legacy) or "=" not in legacy:
        return _MALFORMED
    algorithm, digest = legacy.split("=", 1)
    if not _safe_hash_algorithm(algorithm) or not _safe_hash_digest(digest):
        return _MALFORMED
    return legacy


def _safe_identifier(value: object) -> bool:
    return isinstance(value, str) and bool(_SAFE_IDENTIFIER.fullmatch(value))


def _safe_hash_algorithm(value: object) -> bool:
    return isinstance(value, str) and bool(_HASH_ALGORITHM.fullmatch(value))


def _safe_hash_digest(value: object) -> bool:
    return isinstance(value, str) and bool(_HASH_DIGEST.fullmatch(value))


def _reject_duplicate_object(pairs: list[tuple[object, object]]) -> dict[object, object]:
    result: dict[object, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _direct_url_path(dist: Distribution) -> tuple[bool, Path | None]:
    try:
        files = dist.files
    except OSError, ValueError:
        return False, None
    if files is None:
        return False, None
    matches = []
    for index, item in enumerate(files):
        if index >= MAX_METADATA_FILES:
            return False, None
        if str(item).endswith(".dist-info/direct_url.json"):
            matches.append(item)
    if len(matches) > 1:
        return False, None
    if not matches:
        return True, None
    try:
        path = Path(dist.locate_file(matches[0]))
    except OSError, TypeError, ValueError:
        return False, None
    return True, path


def _is_development_version(version: str) -> bool:
    """Recognize the PEP 440 dev and local-version markers without parsing URLs."""
    normalized = version.lower()
    return bool(
        ".dev" in normalized
        or "+" in normalized
        or re.search(r"\d(?:a|b|c|rc|alpha|beta|pre|preview)\d*(?:$|[.+])", normalized)
    )


def _package_hash(root: Path) -> str | None:
    """Hash package files through pinned directory descriptors only."""
    try:
        root_stat = root.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        return None
    root_fd = _open_directory(root, root_stat)
    if root_fd is None:
        return None
    try:
        digest = hashlib.sha256()
        digest.update(b"murlocs.runtime-identity.v1\0")
        for entry_point in ENTRY_POINTS:
            digest.update(b"entry-point\0")
            digest.update(entry_point.encode("utf-8"))
            digest.update(b"\0")
        _hash_directory(root_fd, "", digest, _HashBudget())
    except _UnsafePackage:
        return None
    finally:
        with suppress(OSError):
            os.close(root_fd)
    return digest.hexdigest()


class _UnsafePackage(Exception):
    pass


class _HashBudget:
    files: int = 0
    bytes: int = 0
    entries: int = 0


def _open_directory(path: Path, expected: os.stat_result) -> int | None:
    """Open a root directory without ever following a replacement symlink."""
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        return None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened = os.fstat(descriptor)
    except OSError:
        return None
    if not _same_file(opened, expected) or not stat.S_ISDIR(opened.st_mode):
        with suppress(OSError):
            os.close(descriptor)
        return None
    return descriptor


def _hash_directory(
    directory_fd: int, prefix: str, digest: hashlib._Hash, budget: _HashBudget
) -> None:
    """Descend using directory FDs, refusing every non-cache symlink and race."""
    try:
        entries = sorted(os.scandir(directory_fd), key=lambda entry: entry.name)
    except OSError as exc:
        raise _UnsafePackage from exc
    for entry in entries:
        # CPython bytecode is an implementation cache, not package content.
        if entry.name == "__pycache__" or entry.name.endswith((".pyc", ".pyo")):
            continue
        budget.entries += 1
        if budget.entries > MAX_PACKAGE_ENTRIES:
            raise _UnsafePackage
        try:
            entry_stat = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise _UnsafePackage from exc
        if stat.S_ISLNK(entry_stat.st_mode):
            raise _UnsafePackage
        if stat.S_ISDIR(entry_stat.st_mode):
            _hash_child_directory(directory_fd, entry.name, entry_stat, prefix, digest, budget)
        elif stat.S_ISREG(entry_stat.st_mode):
            _hash_child_file(directory_fd, entry.name, entry_stat, prefix, digest, budget)
        else:
            raise _UnsafePackage


def _hash_child_directory(
    parent_fd: int,
    name: str,
    expected: os.stat_result,
    prefix: str,
    digest: hashlib._Hash,
    budget: _HashBudget,
) -> None:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        opened = os.fstat(descriptor)
    except OSError as exc:
        raise _UnsafePackage from exc
    try:
        if not stat.S_ISDIR(opened.st_mode) or not _same_file(opened, expected):
            raise _UnsafePackage
        _hash_directory(descriptor, f"{prefix}{name}/", digest, budget)
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not _same_file(current, expected):
            raise _UnsafePackage
    except OSError as exc:
        raise _UnsafePackage from exc
    finally:
        with suppress(OSError):
            os.close(descriptor)


def _hash_child_file(
    parent_fd: int,
    name: str,
    expected: os.stat_result,
    prefix: str,
    digest: hashlib._Hash,
    budget: _HashBudget,
) -> None:
    if expected.st_size > MAX_SINGLE_FILE_BYTES or budget.files >= MAX_PACKAGE_FILES:
        raise _UnsafePackage
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        opened = os.fstat(descriptor)
    except OSError as exc:
        raise _UnsafePackage from exc
    try:
        if not stat.S_ISREG(opened.st_mode) or not _same_file(opened, expected):
            raise _UnsafePackage
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            contents = handle.read(MAX_SINGLE_FILE_BYTES + 1)
        if len(contents) != opened.st_size or len(contents) > MAX_SINGLE_FILE_BYTES:
            raise _UnsafePackage
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not _same_file(current, expected):
            raise _UnsafePackage
    except OSError as exc:
        raise _UnsafePackage from exc
    finally:
        with suppress(OSError):
            os.close(descriptor)
    budget.files += 1
    budget.bytes += len(contents)
    if budget.bytes > MAX_PACKAGE_BYTES:
        raise _UnsafePackage
    relative = f"{prefix}{name}"
    digest.update(b"file\0")
    digest.update(relative.encode("utf-8", "surrogateescape"))
    digest.update(b"\0")
    digest.update(str(len(contents)).encode("ascii"))
    digest.update(b"\0")
    digest.update(contents)


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        left.st_size,
        left.st_mtime_ns,
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_size,
        right.st_mtime_ns,
    )


def _read_regular_file(path: Path, *, maximum: int) -> bytes | None:
    """Read one stable regular file without following symlinks or races."""
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            return None
        if before.st_size > maximum:
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
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            contents = handle.read(maximum + 1)
        if len(contents) > maximum or len(contents) != opened.st_size:
            return None
        after = path.lstat()
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ):
            return None
        return contents
    except OSError:
        return None
    finally:
        with suppress(OSError):
            os.close(descriptor)


def _unknown_build_hash() -> str:
    return hashlib.sha256(b"murlocs.runtime-identity.v1\0unknown").hexdigest()
