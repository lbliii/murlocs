"""Bounded, filter-free Git views for passive integrations."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from murlocs.errors import MurlocsError

MAX_VIEW_ENTRIES = 50_000
MAX_VIEW_BLOB_BYTES = 8 * 1024 * 1024
MAX_VIEW_TOTAL_BYTES = 64 * 1024 * 1024
MAX_DEPENDENCY_SOURCES = 1024
MAX_PRE_PUSH_BYTES = 1024 * 1024


class HookTimeout(MurlocsError):
    """A passive integration exhausted its caller-owned deadline."""


@dataclass
class Deadline:
    expires_at: float
    git_calls: int = 0

    @classmethod
    def start(cls, milliseconds: int) -> Deadline:
        if isinstance(milliseconds, bool) or milliseconds <= 0 or milliseconds > 60_000:
            raise MurlocsError("hook deadline must be between 1 and 60000 milliseconds")
        return cls(time.monotonic() + milliseconds / 1000)

    def remaining_seconds(self) -> float:
        remaining = self.expires_at - time.monotonic()
        if remaining <= 0:
            raise HookTimeout("Murlocs hook integration timed out")
        return remaining

    def check(self) -> None:
        self.remaining_seconds()


@dataclass(frozen=True)
class GitContext:
    root: Path
    git_dir: Path
    common_dir: Path
    object_format: str


@dataclass(frozen=True)
class GitEntry:
    mode: str
    oid: str
    path_bytes: bytes


@dataclass(frozen=True)
class GitSnapshot:
    view: str
    object_id: str | None
    entries: tuple[GitEntry, ...]
    state_id: str


def discover_git(root: Path, deadline: Deadline) -> GitContext:
    """Resolve one non-bare repository without consulting or changing hooks."""
    root = root.resolve()
    top = _git(deadline, root, ["rev-parse", "--show-toplevel"]).stdout.rstrip(b"\n")
    git_dir = _git(
        deadline,
        root,
        ["rev-parse", "--path-format=absolute", "--absolute-git-dir"],
    ).stdout.rstrip(b"\n")
    common_dir = _git(
        deadline,
        root,
        ["rev-parse", "--path-format=absolute", "--git-common-dir"],
    ).stdout.rstrip(b"\n")
    object_format = _git(deadline, root, ["rev-parse", "--show-object-format"]).stdout.rstrip(b"\n")
    try:
        top_path = Path(os.fsdecode(top)).resolve()
        git_path = Path(os.fsdecode(git_dir)).resolve()
        common_path = Path(os.fsdecode(common_dir)).resolve()
        format_text = object_format.decode("ascii")
    except (OSError, UnicodeError) as exc:
        raise MurlocsError(f"Git repository metadata is not representable: {exc}") from exc
    if top_path != root:
        raise MurlocsError(f"hook repository must be the Git worktree root: {top_path}")
    if format_text not in {"sha1", "sha256"}:
        raise MurlocsError(f"unsupported Git object format: {format_text}")
    return GitContext(root, git_path, common_path, format_text)


def capture_index(context: GitContext, deadline: Deadline) -> GitSnapshot:
    completed = _git(deadline, context.root, ["ls-files", "--stage", "-z"])
    entries = _parse_index_entries(completed.stdout, context.object_format)
    return _snapshot("index", None, entries, context.object_format)


def capture_head(context: GitContext, deadline: Deadline) -> GitSnapshot | None:
    resolved = _git(
        deadline,
        context.root,
        ["rev-parse", "--verify", "HEAD^{commit}"],
        allow_failure=True,
    )
    if resolved.returncode:
        return None
    oid = _validated_oid(resolved.stdout.rstrip(b"\n"), context.object_format)
    return capture_commit(context, oid, deadline)


def capture_commit(context: GitContext, commit_oid: str, deadline: Deadline) -> GitSnapshot:
    _validated_oid(commit_oid.encode("ascii", errors="strict"), context.object_format)
    completed = _git(
        deadline,
        context.root,
        ["ls-tree", "-rz", "--full-tree", commit_oid],
    )
    entries = _parse_tree_entries(completed.stdout, context.object_format)
    return _snapshot("commit", commit_oid, entries, context.object_format)


def resolve_commit(context: GitContext, object_id: str, deadline: Deadline) -> str:
    _validated_oid(object_id.encode("ascii", errors="strict"), context.object_format)
    completed = _git(
        deadline,
        context.root,
        ["rev-parse", "--verify", f"{object_id}^{{commit}}"],
    )
    return _validated_oid(completed.stdout.rstrip(b"\n"), context.object_format)


def changed_paths(before: GitSnapshot | None, after: GitSnapshot) -> tuple[str, ...]:
    """Return a deterministic delete/add representation of one tree delta."""
    old = {} if before is None else {entry.path_bytes: entry for entry in before.entries}
    new = {entry.path_bytes: entry for entry in after.entries}
    changed = sorted(path for path in set(old) | set(new) if old.get(path) != new.get(path))
    return tuple(_decode_path(path) for path in changed)


def manifest_mode(snapshot: GitSnapshot) -> str | None:
    target = b".murlocs/manifest.toml"
    entry = next((item for item in snapshot.entries if item.path_bytes == target), None)
    return None if entry is None else entry.mode


def materialize(
    context: GitContext,
    snapshot: GitSnapshot,
    destination: Path,
    deadline: Deadline,
) -> dict[str, int]:
    """Materialize raw Git blobs without checkout filters or network access."""
    destination.mkdir(parents=True, exist_ok=False)
    collision_keys: dict[str, bytes] = {}
    blob_entries: list[GitEntry] = []
    for entry in snapshot.entries:
        relative = _decode_path(entry.path_bytes)
        collision = unicodedata.normalize("NFD", relative).casefold()
        previous = collision_keys.get(collision)
        if previous is not None and previous != entry.path_bytes:
            raise MurlocsError("Git view has a host-portability path collision")
        collision_keys[collision] = entry.path_bytes
        if entry.mode in {"100644", "100755", "120000"}:
            blob_entries.append(entry)
        elif entry.mode == "160000":
            (destination / relative).mkdir(parents=True, exist_ok=True)
        else:
            raise MurlocsError(f"unsupported Git index mode {entry.mode} for {relative}")

    blobs = _read_blobs(context, blob_entries, deadline)
    total = 0
    for entry, content in zip(blob_entries, blobs, strict=True):
        deadline.check()
        relative = _decode_path(entry.path_bytes)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if entry.mode == "120000":
            try:
                target.symlink_to(os.fsdecode(content))
            except (OSError, ValueError) as exc:
                raise MurlocsError(f"could not materialize Git symlink {relative}: {exc}") from exc
        else:
            try:
                target.write_bytes(content)
                target.chmod(0o755 if entry.mode == "100755" else 0o644)
            except OSError as exc:
                raise MurlocsError(f"could not materialize Git path {relative}: {exc}") from exc
        total += len(content)
    (destination / ".git").write_text(f"gitdir: {context.git_dir}\n", encoding="utf-8")
    return {
        "entries": len(snapshot.entries),
        "blob_bytes": total,
        "git_subprocesses": deadline.git_calls,
    }


def impact_dependency_id(
    context: GitContext,
    source_paths: tuple[str, ...],
    deadline: Deadline,
) -> str:
    """Fingerprint the bounded Git facts that may influence impact analysis."""
    if len(source_paths) > MAX_DEPENDENCY_SOURCES:
        raise MurlocsError(
            f"impact dependency has more than {MAX_DEPENDENCY_SOURCES} guidance sources"
        )
    digest = hashlib.sha256(b"murlocs-impact-dependency-v1\0")
    for path in sorted(source_paths):
        encoded = os.fsencode(path)
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    for args in (
        ["for-each-ref", "--format=%(objectname)%00%(refname)"],
        ["count-objects", "-v"],
        [
            "rev-list",
            "--all",
            "--max-count=64",
            "--",
            *sorted(source_paths),
        ],
    ):
        output = _git(deadline, context.root, args).stdout
        digest.update(len(output).to_bytes(8, "big"))
        digest.update(output)
    shallow = context.common_dir / "shallow"
    if shallow.is_file():
        try:
            raw = shallow.read_bytes()
        except OSError as exc:
            raise MurlocsError(f"could not read Git shallow boundary: {exc}") from exc
        if len(raw) > 1024 * 1024:
            raise MurlocsError("Git shallow boundary exceeds 1 MiB")
        digest.update(raw)
    return "sha256:" + digest.hexdigest()


def _snapshot(
    view: str,
    object_id: str | None,
    entries: tuple[GitEntry, ...],
    object_format: str,
) -> GitSnapshot:
    digest = hashlib.sha256(f"murlocs-{view}-view-v1\0{object_format}\0".encode())
    for entry in entries:
        digest.update(entry.mode.encode("ascii") + b"\0")
        digest.update(entry.oid.encode("ascii") + b"\0")
        digest.update(len(entry.path_bytes).to_bytes(4, "big"))
        digest.update(entry.path_bytes)
    return GitSnapshot(view, object_id, entries, "sha256:" + digest.hexdigest())


def _parse_index_entries(raw: bytes, object_format: str) -> tuple[GitEntry, ...]:
    entries: list[GitEntry] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, separator, path = record.partition(b"\t")
        fields = metadata.split(b" ")
        if not separator or len(fields) != 3 or fields[2] != b"0":
            raise MurlocsError("Git index has malformed or unmerged entries")
        mode = fields[0].decode("ascii", errors="strict")
        oid = _validated_oid(fields[1], object_format)
        if set(fields[1]) == {ord("0")}:
            raise MurlocsError("Git index contains an intent-to-add entry")
        _validate_path_bytes(path)
        entries.append(GitEntry(mode, oid, path))
    return _validated_entries(entries)


def _parse_tree_entries(raw: bytes, object_format: str) -> tuple[GitEntry, ...]:
    entries: list[GitEntry] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, separator, path = record.partition(b"\t")
        fields = metadata.split(b" ")
        if not separator or len(fields) != 3 or fields[1] not in {b"blob", b"commit"}:
            raise MurlocsError("Git tree has malformed entries")
        mode = fields[0].decode("ascii", errors="strict")
        oid = _validated_oid(fields[2], object_format)
        _validate_path_bytes(path)
        entries.append(GitEntry(mode, oid, path))
    return _validated_entries(entries)


def _validated_entries(entries: list[GitEntry]) -> tuple[GitEntry, ...]:
    if len(entries) > MAX_VIEW_ENTRIES:
        raise MurlocsError(f"Git view exceeds {MAX_VIEW_ENTRIES} entries")
    ordered = tuple(sorted(entries, key=lambda item: item.path_bytes))
    if len({item.path_bytes for item in ordered}) != len(ordered):
        raise MurlocsError("Git view contains duplicate paths")
    return ordered


def _read_blobs(
    context: GitContext, entries: list[GitEntry], deadline: Deadline
) -> tuple[bytes, ...]:
    if not entries:
        return ()
    request = b"".join(entry.oid.encode("ascii") + b"\n" for entry in entries)
    checked = _git(
        deadline,
        context.root,
        ["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
        input_bytes=request,
    )
    sizes: list[int] = []
    for entry, line in zip(entries, checked.stdout.splitlines(), strict=False):
        fields = line.split(b" ")
        if len(fields) != 3 or fields[0] != entry.oid.encode() or fields[1] != b"blob":
            raise MurlocsError("Git blob metadata response is malformed")
        if len(fields[2]) > 20 or not fields[2].isdigit():
            raise MurlocsError("Git blob size is malformed")
        size = int(fields[2])
        if size > MAX_VIEW_BLOB_BYTES:
            raise MurlocsError(f"Git blob exceeds {MAX_VIEW_BLOB_BYTES} bytes")
        sizes.append(size)
    if len(sizes) != len(entries) or sum(sizes) > MAX_VIEW_TOTAL_BYTES:
        raise MurlocsError("Git view blob metadata is incomplete or over limit")
    completed = _git(
        deadline,
        context.root,
        ["cat-file", "--batch"],
        input_bytes=request,
    )
    position = 0
    blobs: list[bytes] = []
    for entry, expected_size in zip(entries, sizes, strict=True):
        end = completed.stdout.find(b"\n", position)
        if end < 0:
            raise MurlocsError("Git blob response is truncated")
        fields = completed.stdout[position:end].split(b" ")
        if (
            len(fields) != 3
            or fields[0] != entry.oid.encode()
            or fields[1] != b"blob"
            or not fields[2].isdigit()
            or int(fields[2]) != expected_size
        ):
            raise MurlocsError("Git blob response sequence is malformed")
        position = end + 1
        content_end = position + expected_size
        if completed.stdout[content_end : content_end + 1] != b"\n":
            raise MurlocsError("Git blob response content is truncated")
        blobs.append(completed.stdout[position:content_end])
        position = content_end + 1
    if position != len(completed.stdout):
        raise MurlocsError("Git blob response has trailing data")
    return tuple(blobs)


def run_git(
    deadline: Deadline,
    root: Path,
    args: list[str],
    *,
    input_bytes: bytes | None = None,
    allow_failure: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    deadline.git_calls += 1
    env = os.environ.copy()
    env.update(
        {
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "LC_ALL": "C",
        }
    )

    def _command(*, no_lazy_fetch: bool) -> list[str]:
        command = ["git"]
        if no_lazy_fetch:
            command.append("--no-lazy-fetch")
        command.extend(
            [
                "--no-pager",
                "--no-replace-objects",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                "-c",
                "core.preloadIndex=false",
                *args,
            ]
        )
        return command

    try:
        completed = subprocess.run(
            _command(no_lazy_fetch=True),
            cwd=root,
            check=False,
            capture_output=True,
            input=input_bytes,
            env=env,
            timeout=deadline.remaining_seconds(),
        )
        if completed.returncode and b"unknown option: --no-lazy-fetch" in completed.stderr:
            completed = subprocess.run(
                _command(no_lazy_fetch=False),
                cwd=root,
                check=False,
                capture_output=True,
                input=input_bytes,
                env=env,
                timeout=deadline.remaining_seconds(),
            )
    except subprocess.TimeoutExpired as exc:
        raise HookTimeout("Murlocs hook Git operation timed out") from exc
    except OSError as exc:
        raise MurlocsError(f"could not execute bounded Git operation: {exc}") from exc
    if completed.returncode and not allow_failure:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise MurlocsError(message or f"Git {args[0]} failed")
    return completed


_git = run_git


def _validated_oid(raw: bytes, object_format: str) -> str:
    size = 40 if object_format == "sha1" else 64
    if len(raw) != size or re.fullmatch(rb"[0-9a-f]+", raw) is None:
        raise MurlocsError("Git returned an invalid object id")
    return raw.decode("ascii")


def _validate_path_bytes(path: bytes) -> None:
    if (
        not path
        or path.startswith(b"/")
        or b"\0" in path
        or any(part in {b"", b".", b".."} for part in path.split(b"/"))
    ):
        raise MurlocsError("Git returned an unsafe repository path")


def _decode_path(path: bytes) -> str:
    _validate_path_bytes(path)
    decoded = os.fsdecode(path)
    if os.fsencode(decoded) != path:
        raise MurlocsError("Git path is not representable on this host")
    return decoded
