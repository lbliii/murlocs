from __future__ import annotations

import tomllib
from copy import deepcopy
from pathlib import Path

import pytest

from murlocs.manifest import parse_manifest_data
from murlocs.render import render_outputs
from murlocs.serialization import render_manifest_data
from murlocs.stewards import translate_stewards_manifest
from murlocs.verify import normalize_severity, validate

FIXTURES = Path(__file__).parent / "fixtures" / "stewards"


def load_fixture(name: str) -> dict:
    return tomllib.loads((FIXTURES / f"{name}.toml").read_text(encoding="utf-8"))


def test_chirp_translation_preserves_structured_intent(tmp_path):
    source = load_fixture("chirp")
    before = deepcopy(source)

    result = translate_stewards_manifest(source)
    manifest = parse_manifest_data(tmp_path, result.manifest)
    templating = next(scope for scope in manifest.scopes if scope.id == "templating")

    assert source == before
    assert manifest.search_policy == ("Read the root map before repository discovery.",)
    assert templating.path == "src/chirp/templating"
    assert templating.map == "src/chirp/templating/AGENTS.md"
    assert [(group.kind, group.paths) for group in templating.owns.groups] == [
        ("code", ("src/chirp/templating/",)),
        ("tests", ("tests/test_templating.py",)),
        ("docs", ("docs/hypermedia.md",)),
    ]
    assert templating.judgment.advocate == ("DOM-level assertions with actionable diagnostics.",)
    assert manifest.invariants[0].verification == "command"
    assert result.findings[0].code == "missing-proof-anchor"
    assert result.findings[0].subjects == ("templating-suite",)


def test_kida_translation_preserves_severity_and_judgment(tmp_path):
    result = translate_stewards_manifest(load_fixture("kida"), require_scope_invariants=True)
    manifest = parse_manifest_data(tmp_path, result.manifest)
    compiler = next(scope for scope in manifest.scopes if scope.id == "compiler")

    assert manifest.require_scope_invariants is True
    assert manifest.invariants[1].severity == "P2"
    assert normalize_severity(manifest.invariants[1].severity) == "advisory"
    assert manifest.invariants[1].verification == "unknown"
    assert compiler.judgment.serves == ("Template authors and framework integrators.",)
    assert [finding.code for finding in result.findings] == ["legacy-severity"]


@pytest.mark.parametrize(
    ("legacy", "canonical"),
    [
        ("P0", "critical"),
        ("P1", "important"),
        ("P2", "advisory"),
        ("P3", "advisory"),
    ],
)
def test_legacy_severity_meanings_are_explicit(legacy, canonical):
    assert normalize_severity(legacy) == canonical


def test_translated_maps_expose_context_network_commands_and_judgment(tmp_path):
    translated = translate_stewards_manifest(load_fixture("kida"))
    manifest = parse_manifest_data(tmp_path, translated.manifest)
    outputs = render_outputs(manifest)

    root = outputs["AGENTS.md"]
    compiler = outputs["src/kida/compiler/AGENTS.md"]
    assert "## Search discipline" in root
    assert "## Network" in root
    assert "`uv run pytest tests/test_compiler.py -q` (`compiler-suite`)" in root
    assert "Do not open `.stewards/PROTOCOL.md`" in compiler
    assert "- **code:** `src/kida/compiler/`" in compiler
    assert "## Advocate" in compiler
    assert "## Do not" in compiler
    assert "## Serves" in compiler


def test_translation_reports_unknown_fields_as_cumulative_blocking_loss():
    source = load_fixture("chirp")
    source["silent_loss"] = True
    source["also_unknown"] = 1

    result = translate_stewards_manifest(source)

    # The translator no longer fail-fasts on the first unknown key; it accumulates
    # every unsupported field into one deterministic blocking loss finding.
    blocking = [f for f in result.findings if f.level == "blocking"]
    assert [f.code for f in blocking] == ["unsupported-field"]
    assert blocking[0].subjects == (
        "legacy manifest: also_unknown",
        "legacy manifest: silent_loss",
    )


def test_missing_check_anchor_is_explicit_proof_debt(tmp_path):
    translated = translate_stewards_manifest(load_fixture("chirp"))
    manifest = parse_manifest_data(tmp_path, translated.manifest)
    (tmp_path / ".stewards").mkdir()
    (tmp_path / ".stewards" / "PROTOCOL.md").write_text("review\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_templating.py").write_text(
        "def test_contract(): pass\n", encoding="utf-8"
    )
    (tmp_path / "src" / "chirp" / "templating").mkdir(parents=True)
    (tmp_path / "src" / "chirp" / "templating" / "render.py").write_text(
        "VALUE = 1\n", encoding="utf-8"
    )
    (tmp_path / "src" / "chirp" / "errors.py").write_text(
        "class BlockNotFoundError: pass\n", encoding="utf-8"
    )

    findings = validate(manifest)

    assert any(
        finding.code == "proof-debt" and "templating-suite" in finding.message
        for finding in findings
    )


def test_unanchored_check_still_requires_a_live_location(tmp_path):
    translated = translate_stewards_manifest(load_fixture("chirp"))
    manifest = parse_manifest_data(tmp_path, translated.manifest)

    findings = validate(manifest)

    assert any(finding.code == "proof-debt" for finding in findings)
    assert any(
        finding.code == "check"
        and "proof location does not exist: tests/test_templating.py" in finding.message
        for finding in findings
    )


def test_registered_command_paths_are_checked_without_execution(tmp_path):
    translated = translate_stewards_manifest(load_fixture("kida"))
    translated.manifest["checks"]["compiler-suite"]["invoke"] = (
        "uv run pytest tests/test_missing.py -q"
    )
    manifest = parse_manifest_data(tmp_path, translated.manifest)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_compiler.py").write_text(
        "def test_compile_time_contract(): pass\n", encoding="utf-8"
    )

    findings = validate(manifest)

    assert any(
        finding.code == "check"
        and "command path does not exist: tests/test_missing.py" in finding.message
        for finding in findings
    )


def test_registered_commands_are_never_executed(tmp_path):
    translated = translate_stewards_manifest(load_fixture("kida"))
    translated.manifest["checks"]["compiler-suite"]["invoke"] = "touch tests/side-effect.txt"
    manifest = parse_manifest_data(tmp_path, translated.manifest)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_compiler.py").write_text(
        "def test_compile_time_contract(): pass\n", encoding="utf-8"
    )

    findings = validate(manifest)

    assert not (tmp_path / "tests" / "side-effect.txt").exists()
    assert any("tests/side-effect.txt" in finding.message for finding in findings)


def test_scope_invariant_requirement_is_configurable(tmp_path):
    translated = translate_stewards_manifest(load_fixture("kida"))
    translated.manifest["scopes"].append(
        {
            "id": "docs",
            "path": "docs",
            "map": "docs/AGENTS.md",
            "point_of_view": "Published behavior.",
            "owns": {"docs": ["docs/"]},
        }
    )
    translated.manifest["policies"]["require_scope_invariants"] = True
    manifest = parse_manifest_data(tmp_path, translated.manifest)

    findings = validate(manifest)

    assert any(
        finding.code == "invariant" and finding.message == "scope has no invariant: docs"
        for finding in findings
    )


def load_exotic() -> dict:
    return tomllib.loads(
        (FIXTURES / "legacy_exotic" / "manifest.toml").read_text(encoding="utf-8")
    )


def test_exotic_manifest_names_every_unsupported_field_in_one_pass():
    result = translate_stewards_manifest(load_exotic())

    blocking = [f for f in result.findings if f.level == "blocking"]
    assert [f.code for f in blocking] == ["unsupported-field"]
    # Every offending (construct, field) pair is named, not just the first check the
    # translator happens to reach. The report is deterministic (sorted).
    assert blocking[0].subjects == (
        "legacy check arch-isolation: kind",
        "legacy check arch-isolation: proves",
        "legacy check contract-suite: kind",
        "legacy check contract-suite: proves",
    )


def test_invariant_proof_contains_is_supported_and_round_trips(tmp_path):
    result = translate_stewards_manifest(load_exotic())

    # proof_contains on invariants is preserved (the allowlist is reconciled with
    # checks), rather than dropped silently or reported as loss.
    anchors = {inv["id"]: inv.get("proof_contains") for inv in result.manifest["invariants"]}
    assert anchors == {
        "arch-boundaries": "ArchBoundaryError",
        "contract-enforced": "assert_contract",
    }
    # It survives serialization and parses back onto the typed model.
    toml = render_manifest_data(result.manifest)
    assert 'proof_contains = "ArchBoundaryError"' in toml
    manifest = parse_manifest_data(tmp_path, tomllib.loads(toml))
    parsed = {inv.id: inv.proof_contains for inv in manifest.invariants}
    assert parsed == {
        "arch-boundaries": "ArchBoundaryError",
        "contract-enforced": "assert_contract",
    }
