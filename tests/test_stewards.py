from __future__ import annotations

import json
import tomllib
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from murlocs.errors import MurlocsError
from murlocs.manifest import parse_manifest_data
from murlocs.model import Check
from murlocs.render import render_outputs
from murlocs.stewards import translate_stewards_manifest
from murlocs.verify import normalize_severity, proof_anchor_advisories, validate
from tests.support import initialize_repo, invoke

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


def test_translation_rejects_unknown_legacy_fields():
    source = load_fixture("chirp")
    source["silent_loss"] = True

    with pytest.raises(MurlocsError, match="unsupported fields: silent_loss"):
        translate_stewards_manifest(source)


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


def _manifest_with_checks(tmp_path, checks):
    translated = translate_stewards_manifest(load_fixture("chirp"))
    manifest = parse_manifest_data(tmp_path, translated.manifest)
    return replace(manifest, checks=checks, source_suffixes=(".py",))


def test_breadth_advisory_flags_anchors_that_cover_a_minority_of_the_suite(tmp_path):
    checks = {
        "theme-suite": Check(
            name="theme-suite",
            invoke=(
                "uv run pytest tests/test_builtin_layouts.py "
                "tests/test_chirp_docs_theming.py tests/test_theme_lint.py -q"
            ),
            location="tests/test_chirp_docs_theming.py",
            proof_contains="test_home_uses_home_view",
        ),
        "sources-suite": Check(
            name="sources-suite",
            invoke=(
                "uv run pytest tests/test_chirp_docs_sources.py "
                "tests/test_chirp_docs_ast_roundtrip.py -q"
            ),
            location="tests/test_chirp_docs_sources.py",
            proof_contains="test_registered_formats",
        ),
    }

    advisories = proof_anchor_advisories(_manifest_with_checks(tmp_path, checks))

    assert {finding.code for finding in advisories} == {"proof-anchor-breadth"}
    # Deterministic ordering by check name.
    assert [finding.message.split()[1] for finding in advisories] == [
        "sources-suite",
        "theme-suite",
    ]
    messages = {finding.message for finding in advisories}
    assert any("theme-suite" in message and "1 of 3" in message for message in messages)
    assert any("sources-suite" in message and "1 of 2" in message for message in messages)
    assert all("repoint proof_contains" in message for message in messages)


def test_breadth_advisory_leaves_strong_and_single_file_anchors_alone(tmp_path):
    checks = {
        # A single-file suite: the anchor already covers the whole invoked set.
        "references-suite": Check(
            name="references-suite",
            invoke="uv run pytest tests/test_chirp_docs_reference_resolution.py -q",
            location="tests/test_chirp_docs_reference_resolution.py",
            proof_contains="test_longest_prefix_match",
        ),
        # A command that names no source-file set at all.
        "changelog-draft": Check(
            name="changelog-draft",
            invoke="make changelog-draft",
            location="Makefile",
            proof_contains="changelog-draft:",
        ),
    }

    advisories = proof_anchor_advisories(_manifest_with_checks(tmp_path, checks))

    assert advisories == []


def test_check_surfaces_breadth_advisory_without_changing_exit_code(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    initialize_repo(root, "--name", "Anchor Breadth")

    baseline = invoke("check", "--repo", str(root), "--format", "json")
    assert baseline.exit_code == 0, baseline.stderr

    tests_dir = root / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_alpha.py").write_text(
        "def test_headline_contract(): pass\n", encoding="utf-8"
    )
    (tests_dir / "test_beta.py").write_text(
        "def test_secondary(): pass\n", encoding="utf-8"
    )
    manifest_path = root / ".murlocs" / "manifest.toml"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8")
        + (
            "\n[checks.suite]\n"
            'invoke = "pytest tests/test_alpha.py tests/test_beta.py"\n'
            'location = "tests/test_alpha.py"\n'
            'proof_contains = "def test_headline_contract"\n'
        ),
        encoding="utf-8",
    )
    assert invoke("compile", "--repo", str(root)).exit_code == 0

    result = invoke("check", "--repo", str(root), "--format", "json")

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["findings"] == []
    advisories = payload["advisories"]
    assert [item["code"] for item in advisories] == ["proof-anchor-breadth"]
    assert "check suite anchors proof in 1 of 2" in advisories[0]["message"]
    assert "repoint proof_contains" in advisories[0]["message"]
