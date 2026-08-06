from __future__ import annotations

from pathlib import Path

from murlocs.cli import build_cli
from murlocs.manifest import load_manifest
from murlocs.verify import validate


def invoke(*argv: str):
    return build_cli().invoke(list(argv))


def _exemption_paths(text: str) -> set[str]:
    if "[coverage.exemptions]" not in text:
        return set()
    block = text.split("[coverage.exemptions]", 1)[1].split("\n[", 1)[0]
    paths: set[str] = set()
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith('"') and '" =' in stripped:
            paths.add(stripped.split('"', 2)[1])
    return paths


def stabilize_inferred_coverage(root: Path) -> None:
    """Add exemptions for inferred coverage gaps in standard test fixtures."""
    manifest_path = root / ".murlocs" / "manifest.toml"
    if not manifest_path.is_file():
        return
    text = manifest_path.read_text(encoding="utf-8")
    if "[coverage.exemptions]" not in text:
        return
    manifest = load_manifest(root)
    exempted = _exemption_paths(text)
    additions: list[str] = []
    for finding in validate(manifest):
        if finding.code != "coverage" or "has no map: " not in finding.message:
            continue
        relative = finding.message.rsplit(": ", 1)[-1]
        if relative in exempted:
            continue
        additions.append(f'"{relative}" = "covered by root map in tests"')
    if not additions:
        return
    text = text.replace(
        "[coverage.exemptions]",
        "[coverage.exemptions]\n" + "\n".join(additions),
    )
    manifest_path.write_text(text, encoding="utf-8")
    result = invoke("compile", "--repo", str(root))
    if result.exit_code != 0:
        raise AssertionError(result.stderr)


def initialize_repo(root: Path, *init_args: str) -> None:
    argv = ["init", "--repo", str(root), *init_args]
    result = invoke(*argv)
    if result.exit_code != 0:
        raise AssertionError(result.stderr)
    stabilize_inferred_coverage(root)
