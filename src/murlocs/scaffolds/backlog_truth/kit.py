"""Stamp and drift-check the backlog-truth kit.

The kit is opt-in and piece-wise adoptable. Templates live under
``murlocs.scaffolds.backlog_truth.templates`` and are copied into a target
repository. Install state is recorded in ``.murlocs/kits/backlog_truth.toml``
and, when a guidance manifest exists, mirrored under ``[kits.backlog_truth]``
so ``murlocs check`` can report present/current vs drift.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Literal

from murlocs.atomic import atomic_write_text
from murlocs.errors import MurlocsError
from murlocs.lockfile import sha256_text
from murlocs.paths import repo_path
from murlocs.verify import Finding

KIT_ID = "backlog_truth"
KIT_VERSION = 1
RECEIPT_PATH = f".murlocs/kits/{KIT_ID}.toml"
MANIFEST_SECTION = f"kits.{KIT_ID}"

PieceName = Literal["templates", "labels", "workflows", "conventions", "process"]

_TEMPLATE_ROOT = resources.files(__package__).joinpath("templates")


@dataclass(frozen=True)
class KitFile:
    """One file the kit can stamp into a repository."""

    source: str
    destination: str
    piece: PieceName
    process: bool = False


@dataclass(frozen=True)
class KitPiece:
    name: PieceName
    description: str


PIECES: tuple[KitPiece, ...] = (
    KitPiece("templates", "Saga/Epic/Investigation/Task GitHub issue templates"),
    KitPiece("labels", "Label taxonomy for kind, priority, workflow, and automation"),
    KitPiece("workflows", "Closure-gate and reconcile workflow stubs plus scripts"),
    KitPiece("conventions", "Backlog-automation conventions document"),
    KitPiece("process", "Issue-lifecycle and BACKLOG harness outside compile"),
)

KIT_FILES: tuple[KitFile, ...] = (
    KitFile("saga.yml", ".github/ISSUE_TEMPLATE/saga.yml", "templates"),
    KitFile("epic.yml", ".github/ISSUE_TEMPLATE/epic.yml", "templates"),
    KitFile("task.yml", ".github/ISSUE_TEMPLATE/task.yml", "templates"),
    KitFile("investigation.yml", ".github/ISSUE_TEMPLATE/investigation.yml", "templates"),
    KitFile("config.yml", ".github/ISSUE_TEMPLATE/config.yml", "templates"),
    KitFile("labels.yml", ".github/labels.yml", "labels"),
    KitFile(
        "issue-closure-gate.yml",
        ".github/workflows/issue-closure-gate.yml",
        "workflows",
    ),
    KitFile(
        "backlog-reconciliation.yml",
        ".github/workflows/backlog-reconciliation.yml",
        "workflows",
    ),
    KitFile(
        "check_closure_acceptance.py",
        "scripts/check_closure_acceptance.py",
        "workflows",
    ),
    KitFile("reconcile_backlog.py", "scripts/reconcile_backlog.py", "workflows"),
    KitFile("backlog-automation.md", "docs/backlog-automation.md", "conventions"),
    KitFile(
        "issue-lifecycle.md",
        "docs/plan/issue-lifecycle.md",
        "process",
        process=True,
    ),
    KitFile("BACKLOG.md", "docs/plan/BACKLOG.md", "process", process=True),
)

_SECTION_RE = re.compile(rf"(?ms)^\[{re.escape(MANIFEST_SECTION)}\]\n(?:^(?!\[).*\n?)*")


@dataclass(frozen=True)
class KitStatus:
    present: bool
    current: bool
    version: int | None
    pieces: tuple[str, ...]
    files: tuple[str, ...]
    process_docs: tuple[str, ...]
    missing: tuple[str, ...]
    modified: tuple[str, ...]
    unexpected_receipt: bool = False

    @property
    def state(self) -> Literal["absent", "drifted", "current"]:
        if not self.present:
            return "absent"
        if self.current:
            return "current"
        return "drifted"


@dataclass(frozen=True)
class ScaffoldPlan:
    files: tuple[KitFile, ...]
    pieces: tuple[str, ...]
    would_write: tuple[str, ...]
    would_skip: tuple[str, ...]
    conflicts: tuple[str, ...]


def list_kit_files(pieces: Iterable[str] | None = None) -> tuple[KitFile, ...]:
    """Return kit files, optionally filtered to selected pieces."""
    selected = _normalize_pieces(pieces)
    return tuple(item for item in KIT_FILES if item.piece in selected)


def read_template(source: str) -> str:
    """Read one packaged template as text."""
    path = _TEMPLATE_ROOT.joinpath(source)
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise MurlocsError(f"scaffold template missing: {source}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise MurlocsError(f"scaffold template unreadable: {source}: {exc}") from exc


def plan_kit(
    root: Path,
    *,
    pieces: Iterable[str] | None = None,
    force: bool = False,
) -> ScaffoldPlan:
    """Preview which kit files would be written without mutating the repository."""
    files = list_kit_files(pieces)
    would_write: list[str] = []
    would_skip: list[str] = []
    conflicts: list[str] = []
    for item in files:
        target = repo_path(root, item.destination, field="scaffold path")
        expected = read_template(item.source)
        if not target.exists():
            would_write.append(item.destination)
            continue
        actual = _read_text(target)
        if actual == expected:
            would_skip.append(item.destination)
            continue
        if force:
            would_write.append(item.destination)
        else:
            conflicts.append(item.destination)
    return ScaffoldPlan(
        files=files,
        pieces=tuple(dict.fromkeys(item.piece for item in files)),
        would_write=tuple(would_write),
        would_skip=tuple(would_skip),
        conflicts=tuple(conflicts),
    )


def apply_kit(
    root: Path,
    *,
    pieces: Iterable[str] | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, object]:
    """Stamp selected kit pieces and record install state.

    Refuses to overwrite divergent existing files unless ``force`` is set.
    Dry-run returns the same payload shape without writing.
    """
    root = root.resolve()
    if not root.is_dir():
        raise MurlocsError(f"repository root is not a directory: {root}")
    plan = plan_kit(root, pieces=pieces, force=force)
    if plan.conflicts:
        raise MurlocsError(
            "refusing to overwrite modified scaffold files "
            f"(pass --force to replace): {', '.join(plan.conflicts)}"
        )

    written: list[str] = []
    skipped = list(plan.would_skip)
    if not dry_run:
        for item in plan.files:
            if item.destination in plan.would_skip:
                continue
            content = read_template(item.source)
            target = repo_path(root, item.destination, field="scaffold path")
            atomic_write_text(target, content)
            written.append(item.destination)
        _write_receipt(root, plan.pieces, plan.files)
        _mirror_manifest_section(root, plan.pieces, plan.files)
    else:
        written = list(plan.would_write)

    status = kit_status(root) if not dry_run else None
    return {
        "ok": True,
        "kit": KIT_ID,
        "version": KIT_VERSION,
        "dry_run": dry_run,
        "pieces": list(plan.pieces),
        "written": written,
        "skipped": skipped,
        "receipt": RECEIPT_PATH,
        "process_docs": [item.destination for item in plan.files if item.process],
        "status": None
        if status is None
        else {
            "present": status.present,
            "current": status.current,
            "state": status.state,
        },
    }


def kit_status(root: Path, *, pieces: Iterable[str] | None = None) -> KitStatus:
    """Report whether the kit is present and byte-current for selected pieces."""
    root = root.resolve()
    receipt = _read_receipt(root)
    selected = _normalize_pieces(pieces or (receipt.pieces if receipt else None))
    files = list_kit_files(selected)
    missing: list[str] = []
    modified: list[str] = []
    present_files: list[str] = []
    for item in files:
        target = repo_path(root, item.destination, field="scaffold path")
        if not target.is_file():
            missing.append(item.destination)
            continue
        present_files.append(item.destination)
        if _read_text(target) != read_template(item.source):
            modified.append(item.destination)

    process_docs = tuple(item.destination for item in files if item.process)
    present = receipt is not None or bool(present_files)
    current = (
        present
        and not missing
        and not modified
        and (receipt is not None and set(receipt.pieces) >= set(selected))
    )
    return KitStatus(
        present=present,
        current=current,
        version=None if receipt is None else receipt.version,
        pieces=tuple(selected),
        files=tuple(present_files),
        process_docs=process_docs,
        missing=tuple(missing),
        modified=tuple(modified),
        unexpected_receipt=False,
    )


def kit_findings(root: Path) -> list[Finding]:
    """Emit check findings for an installed kit that is missing or drifted."""
    receipt = _read_receipt(root)
    if receipt is None and not _manifest_declares_kit(root):
        return []
    status = kit_status(root)
    findings: list[Finding] = []
    if not status.present:
        findings.append(Finding("kit", f"kit {KIT_ID} is declared but no stamped files were found"))
        return findings
    if status.current:
        return findings
    for path in status.missing:
        findings.append(Finding("kit-drift", f"kit file missing: {path}"))
    for path in status.modified:
        findings.append(Finding("kit-drift", f"kit file drifted from scaffold: {path}"))
    findings.extend(_process_doc_compile_findings(root, status.process_docs))
    return findings


def render_kit_receipt(
    pieces: Iterable[str],
    files: Iterable[KitFile],
) -> str:
    """Render the install receipt TOML written under ``.murlocs/kits/``."""
    piece_list = list(dict.fromkeys(pieces))
    file_rows = [
        {
            "path": item.destination,
            "piece": item.piece,
            "sha256": sha256_text(read_template(item.source)),
            "process": item.process,
        }
        for item in files
    ]
    lines = [
        f"# Generated by murlocs scaffold {KIT_ID}. Re-run scaffold to refresh.",
        f"kit = {_toml(KIT_ID)}",
        f"version = {KIT_VERSION}",
        f"pieces = {_toml(piece_list)}",
        "compile = false",
        "",
    ]
    for row in file_rows:
        lines.append("[[files]]")
        lines.append(f"path = {_toml(row['path'])}")
        lines.append(f"piece = {_toml(row['piece'])}")
        lines.append(f"sha256 = {_toml(row['sha256'])}")
        lines.append(f"process = {'true' if row['process'] else 'false'}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _write_receipt(root: Path, pieces: tuple[str, ...], files: tuple[KitFile, ...]) -> None:
    existing = _read_receipt(root)
    merged_pieces = list(dict.fromkeys([*(existing.pieces if existing else ()), *pieces]))
    # Keep previously installed files for pieces not being rewritten this run.
    selected = set(pieces)
    retained: list[KitFile] = []
    if existing is not None:
        by_dest = {item.destination: item for item in KIT_FILES}
        for path in existing.files:
            item = by_dest.get(path)
            if item is not None and item.piece not in selected:
                retained.append(item)
    merged_files = tuple(dict.fromkeys([*retained, *files]))
    content = render_kit_receipt(merged_pieces, merged_files)
    target = repo_path(root, RECEIPT_PATH, field="kit receipt")
    atomic_write_text(target, content)


def _mirror_manifest_section(
    root: Path,
    pieces: tuple[str, ...],
    files: tuple[KitFile, ...],
) -> None:
    manifest_path = root / ".murlocs" / "manifest.toml"
    if not manifest_path.is_file():
        return
    receipt = _read_receipt(root)
    piece_list = list(receipt.pieces if receipt is not None else pieces)
    process_docs = [item.destination for item in files if item.process]
    if receipt is not None:
        process_docs = [
            item.destination
            for item in KIT_FILES
            if item.process and item.destination in set(receipt.files)
        ]
    section = "\n".join(
        [
            f"[{MANIFEST_SECTION}]",
            f"version = {KIT_VERSION}",
            f"receipt = {_toml(RECEIPT_PATH)}",
            f"pieces = {_toml(piece_list)}",
            f"process_docs = {_toml(process_docs)}",
            "compile = false",
            "",
        ]
    )
    text = manifest_path.read_text(encoding="utf-8")
    if _SECTION_RE.search(text):
        updated = _SECTION_RE.sub(section, text)
    else:
        updated = text.rstrip() + "\n\n" + section
    if updated != text:
        atomic_write_text(manifest_path, updated)


@dataclass(frozen=True)
class _Receipt:
    version: int
    pieces: tuple[str, ...]
    files: tuple[str, ...]
    process_docs: tuple[str, ...]


def _read_receipt(root: Path) -> _Receipt | None:
    path = root / RECEIPT_PATH
    if not path.is_file():
        return None
    try:
        import tomllib

        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise MurlocsError(f"invalid kit receipt {RECEIPT_PATH}: {exc}") from exc
    files_raw = data.get("files", [])
    files: list[str] = []
    process_docs: list[str] = []
    if isinstance(files_raw, list):
        for entry in files_raw:
            if not isinstance(entry, dict):
                continue
            rel = str(entry.get("path", ""))
            if not rel:
                continue
            files.append(rel)
            if entry.get("process") is True:
                process_docs.append(rel)
    pieces = tuple(str(item) for item in data.get("pieces", []))
    return _Receipt(
        version=int(data.get("version", 0)),
        pieces=pieces,
        files=tuple(files),
        process_docs=tuple(process_docs),
    )


def _manifest_declares_kit(root: Path) -> bool:
    path = root / ".murlocs" / "manifest.toml"
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return f"[{MANIFEST_SECTION}]" in text


def _process_doc_compile_findings(root: Path, process_docs: tuple[str, ...]) -> list[Finding]:
    """Warn when process harness paths are registered as compiled scope maps."""
    if not process_docs:
        return []
    manifest_path = root / ".murlocs" / "manifest.toml"
    if not manifest_path.is_file():
        return []
    try:
        from murlocs.manifest import load_manifest

        manifest = load_manifest(root)
    except MurlocsError:
        return []
    findings: list[Finding] = []
    process_set = set(process_docs)
    for scope in manifest.scopes:
        if scope.map in process_set:
            findings.append(
                Finding(
                    "kit-process",
                    f"process harness {scope.map} is registered as scope map "
                    f"{scope.id}; keep backlog harness outside compile",
                )
            )
    return findings


def _normalize_pieces(pieces: Iterable[str] | None) -> tuple[str, ...]:
    known = {piece.name for piece in PIECES}
    if pieces is None:
        return tuple(piece.name for piece in PIECES)
    selected: list[str] = []
    unknown: list[str] = []
    for raw in pieces:
        name = str(raw).strip().lower().replace("-", "_")
        # Accept both "process" and aliases.
        aliases = {
            "template": "templates",
            "issue_templates": "templates",
            "issue_template": "templates",
            "workflow": "workflows",
            "docs": "conventions",
            "convention": "conventions",
            "process_docs": "process",
            "harness": "process",
            "label": "labels",
        }
        name = aliases.get(name, name)
        if name not in known:
            unknown.append(raw)
            continue
        if name not in selected:
            selected.append(name)
    if unknown:
        raise MurlocsError(
            "unknown scaffold piece(s): "
            + ", ".join(unknown)
            + f"; choose from {', '.join(sorted(known))}"
        )
    if not selected:
        raise MurlocsError("at least one scaffold piece is required")
    return tuple(selected)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise MurlocsError(f"could not read {path}: {exc}") from exc


def _toml(value: object) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml(item) for item in value) + "]"
    raise MurlocsError(f"unsupported TOML value: {type(value).__name__}")
