#!/usr/bin/env python
"""Print the CHANGELOG.md section for a given version.

Used by `.github/workflows/release-notes.yml` to keep a published GitHub
release's body in sync with the changelog. The version argument is the bare
number (`0.1.0`), not the `v`-prefixed tag.

Extraction is deliberately forgiving: an unknown version prints nothing and
exits 0 so the release workflow can leave the existing notes in place instead of
failing the release.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"

# A section header such as `## [0.1.0] - 2026-08-05` or `## [0.1.0]`.
HEADER = re.compile(r"^##\s+\[(?P<version>[^\]]+)\]")
# The reference-link block at the foot of the file, e.g. `[0.1.0]: https://...`.
LINK_DEF = re.compile(r"^\[[^\]]+\]:\s")


def extract(text: str, version: str) -> str:
    """Return the body of the section for `version`, or empty if absent."""
    lines = text.splitlines()
    collected: list[str] = []
    capturing = False
    for line in lines:
        header = HEADER.match(line)
        if header is not None:
            if capturing:
                break
            if header.group("version") == version:
                capturing = True
            continue
        if capturing:
            # The trailing link-reference block belongs to no section.
            if LINK_DEF.match(line):
                break
            collected.append(line)
    return "\n".join(collected).strip()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: changelog_extract.py <version>", file=sys.stderr)
        return 2
    if not CHANGELOG.is_file():
        print(f"::notice::{CHANGELOG} not found", file=sys.stderr)
        return 0
    section = extract(CHANGELOG.read_text(encoding="utf-8"), argv[1])
    if section:
        print(section)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
