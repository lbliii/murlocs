"""Atomic persistence helpers for evaluation result artifacts."""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from pathlib import Path


def _default_mode() -> int:
    """Return the mode a normal tool would create, honouring the umask."""
    mask = os.umask(0)
    os.umask(mask)
    return 0o666 & ~mask


def atomic_write_text(target: Path, content: str) -> Path:
    """Atomically replace one result path without following an existing link."""
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(raw_temporary)
    try:
        stream = os.fdopen(descriptor, "w", encoding="utf-8", newline="")
        descriptor = -1
        with stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # mkstemp creates 0600 and os.replace preserves it; honour the umask.
        os.chmod(temporary, _default_mode())
        os.replace(temporary, target)
        return target
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            temporary.unlink()
