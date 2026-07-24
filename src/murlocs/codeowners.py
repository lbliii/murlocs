"""Minimal, opt-in `.github/CODEOWNERS` reader for exact layer-file ownership.

This stays deliberately small: it parses the standard `pattern owner...` line format
and exposes only exact-path lookups. Murlocs never uses it unless a repository opts in
through the `validate_codeowners` policy, so repositories without a CODEOWNERS file are
unaffected.
"""

from __future__ import annotations

from pathlib import Path

CODEOWNERS_LOCATIONS = (
    ".github/CODEOWNERS",
    "CODEOWNERS",
    "docs/CODEOWNERS",
)


def find_codeowners(root: Path) -> Path | None:
    for relative in CODEOWNERS_LOCATIONS:
        candidate = root / relative
        if candidate.is_file():
            return candidate
    return None


def parse_codeowners(text: str) -> dict[str, tuple[str, ...]]:
    """Return the last-wins exact-path → owners map, mirroring CODEOWNERS precedence."""
    owners: dict[str, tuple[str, ...]] = {}
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        parts = stripped.split()
        pattern = _normalize(parts[0])
        owners[pattern] = tuple(parts[1:])
    return owners


def _normalize(pattern: str) -> str:
    return pattern.lstrip("/").rstrip("/")


def normalize_path(path: str) -> str:
    return _normalize(path)
