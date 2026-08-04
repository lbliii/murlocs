from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from murlocs.cli import build_cli

FIXTURES = Path(__file__).parent / "fixtures" / "textual-evidence-markers"


def invoke(*argv: str):
    return build_cli().invoke(list(argv))


@pytest.mark.parametrize(
    ("fixture", "passes"),
    [
        ("presence", True),
        ("deletion", False),
        ("movement", False),
        ("duplication", True),
    ],
)
def test_existing_textual_anchor_marker_fixtures(tmp_path: Path, fixture: str, passes: bool):
    """Show exactly what the current file-plus-substring evidence model observes."""
    root = tmp_path / fixture
    shutil.copytree(FIXTURES / fixture, root)

    if passes:
        compile_result = invoke("compile", "--repo", str(root))
        assert compile_result.exit_code == 0
    result = invoke("check", "--repo", str(root))

    assert (result.exit_code == 0) is passes
    if passes:
        assert "murlocs check passed" in result.output
    else:
        assert "sample-proof manual evidence was not found" in result.stderr


def test_duplicate_marker_remains_accepted_by_existing_substring_semantics(tmp_path: Path):
    root = tmp_path / "duplication"
    shutil.copytree(FIXTURES / "duplication", root)

    compile_result = invoke("compile", "--repo", str(root))
    check_result = invoke("check", "--repo", str(root))

    assert compile_result.exit_code == 0
    assert check_result.exit_code == 0
    assert (root / "AGENTS.md").read_text(encoding="utf-8").count(
        "murlocs:evidence sample-proof"
    ) == 1
