from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]
SKILL = ROOT / "skills" / "bootstrap-murlocs" / "SKILL.md"
METADATA = ROOT / "skills" / "bootstrap-murlocs" / "agents" / "openai.yaml"


def test_bootstrap_skill_routes_every_repository_state_through_current_cli():
    text = SKILL.read_text(encoding="utf-8")

    for heading in (
        "## Route: greenfield",
        "## Route: recognized steward migration",
        "## Route: unmanaged guidance",
        "## Route: existing Murlocs network",
    ):
        assert heading in text

    for command in (
        "murlocs inventory",
        "murlocs diff --mode semantic",
        "murlocs diff --mode rendered",
        "murlocs import --from stewards --output .murlocs/manifest.toml",
        "murlocs --dry-run adopt",
        "murlocs adopt",
        "murlocs check",
        "murlocs explain PATH",
        "murlocs --dry-run prune",
        "murlocs prune",
        "murlocs --dry-run rollback",
    ):
        assert command in text

    assert "future adopt/import workflow" not in text
    assert "Do not initialize or import" in text
    assert "Treat every unrecognized or hand-authored instruction file as user-owned" in text


def test_bootstrap_skill_packaged_metadata_matches_state_routing_workflow():
    metadata = METADATA.read_text(encoding="utf-8")

    assert 'display_name: "Bootstrap Murlocs"' in metadata
    assert 'short_description: "Safely bootstrap or migrate repo guidance"' in metadata
    assert "$bootstrap-murlocs" in metadata
    assert "classify this repository" in metadata
