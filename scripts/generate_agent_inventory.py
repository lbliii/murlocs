#!/usr/bin/env python
"""Regenerate the checked-in agent-surface inventory fixture."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from murlocs.agent_inventory import write_agent_inventory  # noqa: E402


def main() -> int:
    target = write_agent_inventory()
    print(f"Wrote {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
