"""Collect the guidance text each arm would place in front of an agent.

The harness reuses the read-only Murlocs core to measure the *compiled, scoped* arm. It
never executes registered commands and never mutates the repository.
"""

from __future__ import annotations

from pathlib import Path

from murlocs.manifest import load_manifest
from murlocs.paths import repo_path
from murlocs.render import render_outputs


def murlocs_guidance(root: Path, target_path: str) -> str:
    """Concatenate the applicable root-to-target AGENTS.md chain for a path."""
    manifest = load_manifest(root)
    outputs = render_outputs(manifest)
    absolute = (root / target_path).resolve()
    applicable: list[tuple[int, str]] = []
    for scope in manifest.scopes:
        scope_root = repo_path(root, scope.path, field="scope path")
        try:
            absolute.relative_to(scope_root)
        except ValueError:
            continue
        applicable.append((len(scope_root.parts), outputs.get(scope.map, "")))
    applicable.sort(key=lambda item: item[0])
    return "\n\n".join(text for _, text in applicable if text)


def inline_dump_guidance(root: Path, source: str = "AGENTS.md") -> str:
    """A large, unscoped guidance blob: every generated map concatenated."""
    manifest = load_manifest(root)
    outputs = render_outputs(manifest)
    return "\n\n".join(outputs[key] for key in sorted(outputs))


def no_guidance() -> str:
    return ""
