from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kodama import __version__
from kodama.errors import KodamaError
from kodama.manifest import PROTOCOL_TEMPLATE, load_manifest, render_manifest
from kodama.paths import repo_path
from kodama.render import compile_manifest
from kodama.verify import Finding, validate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kodama",
        description="Compile and verify repository-local guidance networks.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subcommands = parser.add_subparsers(dest="command", required=True)

    init = subcommands.add_parser("init", help="create a starter manifest and compile it")
    init.add_argument("--repo", type=Path, default=Path.cwd(), help="repository root")
    init.add_argument("--name", help="network name (defaults to repository directory name)")

    compile_command = subcommands.add_parser("compile", help="render managed AGENTS.md maps")
    compile_command.add_argument("--repo", type=Path, default=Path.cwd(), help="repository root")

    check = subcommands.add_parser(
        "check", help="validate the manifest, proofs, coverage, and drift"
    )
    check.add_argument("--repo", type=Path, default=Path.cwd(), help="repository root")

    explain = subcommands.add_parser("explain", help="show guidance applicable to a path")
    explain.add_argument("path", type=Path, help="path inside the repository")
    explain.add_argument("--repo", type=Path, default=Path.cwd(), help="repository root")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            return _init(args.repo, args.name)
        if args.command == "compile":
            return _compile(args.repo)
        if args.command == "check":
            return _check(args.repo)
        if args.command == "explain":
            return _explain(args.repo, args.path)
    except KodamaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


def _root(path: Path) -> Path:
    root = path.expanduser().resolve()
    if not root.is_dir():
        raise KodamaError(f"repository root is not a directory: {root}")
    return root


def _init(path: Path, name: str | None) -> int:
    root = _root(path)
    manifest_path = root / ".kodama" / "manifest.toml"
    protocol_path = root / ".kodama" / "PROTOCOL.md"
    if manifest_path.exists():
        raise KodamaError(f"manifest already exists: {manifest_path}")
    if (root / "AGENTS.md").exists():
        raise KodamaError(
            "AGENTS.md already exists and is unmanaged; "
            "migrate it into the manifest before compiling"
        )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(render_manifest(name or root.name), encoding="utf-8")
    protocol_path.write_text(PROTOCOL_TEMPLATE, encoding="utf-8")
    manifest = load_manifest(root)
    blocking = _precompile_findings(manifest)
    if blocking:
        _print_findings(blocking)
        raise KodamaError("starter manifest is not valid; edit it before compiling")
    written = compile_manifest(manifest)
    print(f"initialized {manifest.network} with {len(written)} managed map(s)")
    return 0


def _compile(path: Path) -> int:
    manifest = load_manifest(_root(path))
    blocking = _precompile_findings(manifest)
    if blocking:
        _print_findings(blocking)
        raise KodamaError("manifest validation failed")
    written = compile_manifest(manifest)
    for relative in written:
        print(f"wrote {relative}")
    return 0


def _precompile_findings(manifest) -> list[Finding]:
    return [item for item in validate(manifest) if item.code not in {"drift", "lock"}]


def _check(path: Path) -> int:
    manifest = load_manifest(_root(path))
    findings = validate(manifest)
    if findings:
        _print_findings(findings)
        print(f"kodama found {len(findings)} issue(s)", file=sys.stderr)
        return 1
    print(
        f"kodama check passed: {len(manifest.scopes)} scope(s), "
        f"{len(manifest.invariants)} invariant(s), {len(manifest.checks)} check(s)"
    )
    return 0


def _print_findings(findings: list[Finding]) -> None:
    for finding in findings:
        print(finding, file=sys.stderr)


def _explain(path: Path, target: Path) -> int:
    root = _root(path)
    manifest = load_manifest(root)
    absolute = target.resolve() if target.is_absolute() else (root / target).resolve()
    try:
        absolute.relative_to(root)
    except ValueError as exc:
        raise KodamaError(f"path is outside repository: {target}") from exc

    applicable = []
    for scope in manifest.scopes:
        scope_root = repo_path(root, scope.path, field="scope path")
        try:
            absolute.relative_to(scope_root)
            applicable.append((len(scope_root.parts), scope))
        except ValueError:
            continue
    applicable.sort(key=lambda item: item[0])
    print(f"Guidance for {absolute.relative_to(root).as_posix() or '.'}")
    for _, scope in applicable:
        print(f"\n[{scope.id}] {scope.map}\n  {scope.point_of_view}")
        for invariant in manifest.invariants:
            if invariant.scope == scope.id:
                print(f"  - {invariant.id} ({invariant.severity}): {invariant.statement}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
