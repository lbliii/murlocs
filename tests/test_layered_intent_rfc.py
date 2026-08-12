from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RFC = ROOT / "docs" / "layered-intent.md"
FIXTURES = ROOT / "tests" / "fixtures" / "layered-intent"

# Headings and phrases the #153 acceptance criteria require the RFC to cover.
REQUIRED_SECTIONS = (
    "## Terms",
    "### `intent`",
    "### `outcome`",
    "### `contribution`",
    "### `success`",
    "### `priorities`",
    "### `non_goals`",
    "## Layer specialization",
    "## Interaction with other planes",
    "## Inheritance, override, provenance, ownership, ambiguity",
    "### Inheritance",
    "### Explicit override",
    "### Provenance",
    "### Ownership",
    "### Structural ambiguity behavior",
    "## Deterministic validation: establishes vs excludes",
    "## Active-intent byte accounting",
    "### Overflow policy",
    "## Byte-compatible absence",
    "## Schema version deferral",
    "## Examples",
    "### Valid chain",
    "### Conflicting (structural)",
    "### Stale",
    "### Overly broad",
    "## Rejected alternatives",
    "## Non-goals (this RFC)",
)

REQUIRED_LAYER_KINDS = ("Root", "Domain", "Package", "Component")

REQUIRED_PLANE_NAMES = (
    "User task",
    "Repository intent chain",
    "Local intent",
    "Hard constraints",
    "Invariants",
    "Checks",
)

REQUIRED_FIXTURES = (
    FIXTURES / "valid" / "root-to-package.toml",
    FIXTURES / "conflicting" / "duplicate-id.toml",
    FIXTURES / "stale" / "missing-scope.toml",
    FIXTURES / "overly-broad" / "component-restates-network.toml",
)


@pytest.mark.issue(153)
def test_layered_intent_rfc_exists_and_covers_acceptance_criteria():
    assert RFC.is_file(), "docs/layered-intent.md must exist for issue #153"
    text = RFC.read_text(encoding="utf-8")

    for section in REQUIRED_SECTIONS:
        assert section in text, f"missing required section heading: {section}"

    for kind in REQUIRED_LAYER_KINDS:
        assert kind in text, f"missing layer kind: {kind}"

    for plane in REQUIRED_PLANE_NAMES:
        assert plane in text, f"missing interaction plane: {plane}"

    assert "max_active_intent_bytes" in text
    assert "intent.budget-overflow" in text
    assert "semantic truth" in text.lower() or "semantic-truth" in text
    assert "deferred until human and agent pilots" in text
    assert "byte-identical" in text or "byte-compatible" in text.lower()

    # Design-only: must not claim a shipped parser/compiler for intent.
    assert "does **not** implement a parser" in text or "does not implement a parser" in text.lower()


@pytest.mark.issue(153)
def test_layered_intent_fixtures_cover_representative_cases():
    rfc_text = RFC.read_text(encoding="utf-8")
    for path in REQUIRED_FIXTURES:
        assert path.is_file(), f"missing fixture: {path.relative_to(ROOT)}"
        body = path.read_text(encoding="utf-8")
        assert "[[intent]]" in body
        assert "outcome" in body
        relative = path.relative_to(ROOT).as_posix()
        assert relative in rfc_text, f"RFC must reference fixture path {relative}"


@pytest.mark.issue(153)
def test_layered_intent_rfc_is_linked_from_docs_index_and_nav():
    readme = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    nav = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    assert "layered-intent.md" in readme
    assert "layered-intent.md" in nav
