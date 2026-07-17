from __future__ import annotations

from pathlib import Path

from kodama.errors import KodamaError


def repo_path(root: Path, raw: str, *, field: str) -> Path:
    candidate = Path(raw)
    if candidate.is_absolute():
        raise KodamaError(f"{field} must be repository-relative: {raw}")
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise KodamaError(f"{field} escapes the repository: {raw}") from exc
    return resolved


def relative_posix(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()
