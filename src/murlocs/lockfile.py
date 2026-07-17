from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from murlocs import __version__
from murlocs.errors import MurlocsError

LOCK_PATH = Path(".murlocs/lock.json")


@dataclass(frozen=True)
class Lock:
    generated: dict[str, str]
    manifest_sha256: str


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def read_lock(root: Path) -> Lock | None:
    path = root / LOCK_PATH
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        generated = {
            str(name): str(metadata["sha256"])
            for name, metadata in data.get("generated", {}).items()
        }
        return Lock(generated=generated, manifest_sha256=str(data["manifest_sha256"]))
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise MurlocsError(f"invalid lockfile: {path}") from exc


def render_lock(manifest_bytes: bytes, outputs: dict[str, str]) -> str:
    data = {
        "lock_version": 1,
        "schema_version": 1,
        "tool_version": __version__,
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "generated": {
            name: {"sha256": sha256_text(content)} for name, content in sorted(outputs.items())
        },
    }
    return json.dumps(data, indent=2, sort_keys=True) + "\n"
