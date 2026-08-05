from __future__ import annotations

from pathlib import Path

from murlocs.errors import MurlocsError


def resolve_root(root: Path) -> Path:
    """Resolve the repository root once so callers can hoist it out of loops.

    `Path.resolve()` walks and `lstat`s every component. Doing that per path
    check made containment validation the dominant cost of `murlocs check`.
    """
    return root.resolve()


def repo_path_within(root_resolved: Path, raw: str, *, field: str) -> Path:
    """Resolve `raw` against an already-resolved root and confine it there.

    Identical to `repo_path` but skips re-resolving the root, which is the only
    part worth hoisting when validating many paths against the same repository.
    """
    candidate = Path(raw)
    if candidate.is_absolute():
        raise MurlocsError(f"{field} must be repository-relative: {raw}")
    resolved = (root_resolved / candidate).resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise MurlocsError(f"{field} escapes the repository: {raw}") from exc
    return resolved


def repo_path(root: Path, raw: str, *, field: str) -> Path:
    """Resolve a repository-relative path and refuse anything outside the root."""
    return repo_path_within(resolve_root(root), raw, field=field)


def relative_posix(root: Path, path: Path) -> str:
    return path.resolve().relative_to(resolve_root(root)).as_posix()
