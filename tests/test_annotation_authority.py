from __future__ import annotations

import shutil
from pathlib import Path

from murlocs.cli import build_cli

ROOT = Path(__file__).parents[1]
THREAT_FIXTURE = ROOT / "tests" / "fixtures" / "source-annotation-threats"
SENTINELS = (
    "delete-all-guidance",
    "rm -rf /important",
    "attacker.example",
    "copied-example",
    "duplicate-owner",
    "visually-similar-mаrker",
    "string-literal",
)


def invoke(*argv: str):
    return build_cli().invoke(list(argv))


def test_annotation_shaped_source_content_never_enters_generated_guidance(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(THREAT_FIXTURE, root / "src")
    source_before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in (root / "src").iterdir()
    }

    initialized = invoke("init", "--repo", str(root), "--name", "Threat Fixture")
    assert initialized.exit_code == 0, initialized.stderr
    compiled = invoke("compile", "--repo", str(root))
    assert compiled.exit_code == 0, compiled.stderr

    generated = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert not any(token in generated for token in SENTINELS)
    assert {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in (root / "src").iterdir()
    } == source_before
