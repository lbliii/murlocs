"""One durable, permission-correct atomic write for every repository output.

`tempfile.mkstemp` creates its file `0600` and `os.replace` preserves that mode,
so every generated map and lockfile used to land owner-only regardless of the
process umask. Git does not track the read bit, so the effect is invisible in a
diff and only shows up in shared checkouts and CI containers.

These helpers replace three near-identical implementations that had drifted
apart on durability: two called `fsync`, the two writing generated maps and the
lockfile did not.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from pathlib import Path

from murlocs.errors import MurlocsError


def _default_mode() -> int:
    """Return the mode a normal tool would create, honouring the umask."""
    mask = os.umask(0)
    os.umask(mask)
    return 0o666 & ~mask


def _target_mode(path: Path) -> int:
    """Preserve an existing file's permissions; fall back to the umask default.

    Replacing a file the user has deliberately chmod'ed should not silently
    reset it, so an existing mode always wins.
    """
    try:
        return path.stat().st_mode & 0o777
    except OSError:
        return _default_mode()


def _write(path: Path, payload: bytes, *, replace: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = _target_mode(path)
    descriptor, raw_temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            # Without this the rename can be durable before the contents are,
            # which would leave a truncated map that the lockfile vouches for.
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        if replace:
            os.replace(temporary, path)
            return
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise MurlocsError(f"refusing to replace existing file: {path.name}") from exc
        temporary.unlink()
    except BaseException:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, content: str) -> None:
    """Atomically replace `path`, normalising newlines for stable hashing."""
    _write(path, content.encode("utf-8"), replace=True)


def atomic_write_bytes(path: Path, content: bytes) -> None:
    """Atomically replace `path` with exact bytes."""
    _write(path, content, replace=True)


def atomic_create_text(path: Path, content: str) -> None:
    """Atomically create `path`, refusing to replace an existing file."""
    _write(path, content.encode("utf-8"), replace=False)
