from __future__ import annotations

import json
import shutil
import tomllib
from pathlib import Path

import pytest

import murlocs.source_annotations as source_annotations
from murlocs.layers import resolve_manifest
from murlocs.manifest import parse_manifest_data
from murlocs.outcome import build_check_outcome
from murlocs.serialization import render_manifest_data
from murlocs.source_annotations import AnnotationResolverFinding, resolve_annotations
from murlocs.verify import annotation_findings

CORPUS = Path(__file__).parent / "fixtures" / "source-annotation-contract" / "v1"


def annotation(identifier: str, file: str, **overrides: str) -> dict[str, str]:
    return {"id": identifier, "kind": "evidence", "file": file, "version": "v1", **overrides}


def manifest(root: Path, annotations: list[dict[str, str]]):
    invariants = [
        {
            "id": f"invariant-{index}",
            "scope": "root",
            "statement": "Reviewed.",
            "severity": "important",
            "verification": "unknown",
            "annotation": item,
        }
        for index, item in enumerate(annotations)
    ]
    return parse_manifest_data(
        root,
        {
            "schema_version": 1,
            "network": "Fixture",
            "protocol": ".murlocs/PROTOCOL.md",
            "coverage": {"roots": [], "source_suffixes": [], "exemptions": {}},
            "policies": {"require_scope_invariants": False},
            "scopes": [
                {
                    "id": "root",
                    "path": ".",
                    "map": "AGENTS.md",
                    "point_of_view": "Fixture.",
                    "owns": [],
                }
            ],
            "invariants": invariants,
        },
    )


def test_resolver_consumes_contract_corpus_and_normalizes_locations(tmp_path):
    cases = json.loads((CORPUS / "cases.json").read_text(encoding="utf-8"))["cases"]
    valid = []
    seen_identifiers = set()
    for case in cases:
        identifier = case["expect"].get("identifier")
        if (
            identifier is not None
            and case["path"] != "unicode.py"
            and identifier not in seen_identifiers
        ):
            seen_identifiers.add(identifier)
            valid.append(case)
    for case in valid:
        shutil.copy2(CORPUS / case["path"], tmp_path / case["path"])
    result = resolve_annotations(
        manifest(
            tmp_path, [annotation(case["expect"]["identifier"], case["path"]) for case in valid]
        )
    )
    assert result.findings == ()
    assert [
        (item.identifier, item.location.file, item.location.line) for item in result.bindings
    ] == [
        (case["expect"]["identifier"], case["path"], case["line"])
        for case in sorted(valid, key=lambda case: (case["expect"]["identifier"], case["path"]))
    ]


def test_resolver_keeps_textual_anchor_independent_and_round_trips(tmp_path):
    item = {
        "id": "proof",
        "scope": "root",
        "statement": "Proof remains textual.",
        "severity": "important",
        "verification": "manual",
        "evidence_file": "docs/proof.md",
        "anchor": "Exact anchor",
        "annotation": annotation("proof.marker", "src/proof.py"),
    }
    data = {
        "schema_version": 1,
        "network": "Compatibility",
        "protocol": ".murlocs/PROTOCOL.md",
        "coverage": {"roots": [], "source_suffixes": [], "exemptions": {}},
        "policies": {"require_scope_invariants": False},
        "scopes": [
            {"id": "root", "path": ".", "map": "AGENTS.md", "point_of_view": "Root.", "owns": []}
        ],
        "invariants": [item],
    }
    invariant = parse_manifest_data(tmp_path, data).invariants[0]
    assert (invariant.evidence_file, invariant.anchor) == ("docs/proof.md", "Exact anchor")
    assert invariant.annotation is not None
    assert (
        tomllib.loads(render_manifest_data(data))["invariants"][0]["annotation"]
        == item["annotation"]
    )


@pytest.mark.parametrize(
    ("path", "content", "code"),
    [
        ("../escape.py", None, "annotation.excluded"),
        (
            "vendor/dependency.py",
            '# murlocs:annotation/v1 evidence "vendor.marker"\n',
            "annotation.excluded",
        ),
        (
            "src/generated/file.py",
            '# murlocs:annotation/v1 evidence "generated.marker"\n',
            "annotation.excluded",
        ),
        ("bad.py", b"\xff", "annotation.undecodable"),
    ],
)
def test_resolver_fails_closed_at_path_and_decode_boundaries(tmp_path, path, content, code):
    if content is not None:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
    result = resolve_annotations(manifest(tmp_path, [annotation("boundary.marker", path)]))
    assert result.bindings == ()
    assert result.findings[0].code == code


def test_resolver_excludes_declared_gitignored_and_submodule_files(tmp_path):
    (tmp_path / ".gitignore").write_text("ignored.py\ncache/\n", encoding="utf-8")
    (tmp_path / "ignored.py").write_text(
        '# murlocs:annotation/v1 evidence "ignored.marker"\n', encoding="utf-8"
    )
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / ".git").write_text("gitdir: ../.git/modules/nested\n", encoding="utf-8")
    (nested / "source.py").write_text(
        '# murlocs:annotation/v1 evidence "submodule.marker"\n', encoding="utf-8"
    )
    cached = tmp_path / "output" / "cache"
    cached.mkdir(parents=True)
    (cached / "source.py").write_text(
        '# murlocs:annotation/v1 evidence "nested-ignore.marker"\n', encoding="utf-8"
    )

    result = resolve_annotations(
        manifest(
            tmp_path,
            [
                annotation("ignored.marker", "ignored.py"),
                annotation("submodule.marker", "nested/source.py"),
                annotation("nested-ignore.marker", "output/cache/source.py"),
            ],
        )
    )

    assert {finding.code for finding in result.findings} == {"annotation.excluded"}
    assert {finding.identifier for finding in result.findings} == {
        "ignored.marker",
        "submodule.marker",
        "nested-ignore.marker",
    }


def test_resolver_reports_grammar_and_duplicate_without_source_prose(tmp_path):
    (tmp_path / "source.py").write_text(
        "\n".join(
            [
                '# murlocs:annotation/v1 evidence "one.marker"',
                '# murlocs:annotation/v1 evidence "one.marker"',
                "# murlocs:annotation/v1 evidence unquoted",
            ]
        ),
        encoding="utf-8",
    )
    result = resolve_annotations(manifest(tmp_path, [annotation("one.marker", "source.py")]))
    assert result.bindings == ()
    assert {finding.code for finding in result.findings} == {
        "annotation.duplicate",
        "annotation.malformed",
    }
    assert all("unquoted" not in repr(finding) for finding in result.findings)


@pytest.mark.parametrize(
    ("path", "content"),
    [
        (
            "source.py",
            'payload = """\n# murlocs:annotation/v1 evidence "quoted.marker"\n"""\n',
        ),
        (
            "source.js",
            '`\n// murlocs:annotation/v1 evidence "quoted.marker"\n`;\n',
        ),
        (
            "source.rs",
            'let payload = r#"\n// murlocs:annotation/v1 evidence "quoted.marker"\n"#;\n',
        ),
    ],
)
def test_resolver_never_promotes_markers_inside_multiline_literals(tmp_path, path, content):
    (tmp_path / path).write_text(content, encoding="utf-8")

    result = resolve_annotations(manifest(tmp_path, [annotation("quoted.marker", path)]))

    assert result.bindings == ()
    assert result.findings == (
        AnnotationResolverFinding("annotation.missing", "quoted.marker", "invariant-0"),
    )


@pytest.mark.parametrize(
    ("path", "content", "identifier"),
    [
        (
            "config.yaml",
            'service: |\n  # murlocs:annotation/v1 evidence "block.marker"\n',
            "block.marker",
        ),
        (
            "settings.toml",
            'value = """\n  # murlocs:annotation/v1 evidence "block.marker"\n"""\n',
            "block.marker",
        ),
        (
            "query.sql",
            "select '\n  -- murlocs:annotation/v1 evidence \"block.marker\"\n';\n",
            "block.marker",
        ),
        (
            "script.sh",
            "cat <<'EOF'\n  # murlocs:annotation/v1 evidence \"block.marker\"\nEOF\n",
            "block.marker",
        ),
    ],
)
def test_resolver_never_promotes_markers_inside_multiline_noncomment_content(
    tmp_path, path, content, identifier
):
    (tmp_path / path).write_text(content, encoding="utf-8")

    result = resolve_annotations(manifest(tmp_path, [annotation(identifier, path)]))

    assert result.bindings == ()
    assert result.findings == (
        AnnotationResolverFinding("annotation.missing", identifier, "invariant-0"),
    )


@pytest.mark.parametrize(
    ("path", "content", "identifier"),
    [
        ("config.yaml", '  # murlocs:annotation/v1 evidence "yaml.indented"\n', "yaml.indented"),
        ("settings.toml", '  # murlocs:annotation/v1 evidence "toml.indented"\n', "toml.indented"),
        ("query.sql", '  -- murlocs:annotation/v1 evidence "sql.indented"\n', "sql.indented"),
        ("script.sh", '  # murlocs:annotation/v1 evidence "shell.indented"\n', "shell.indented"),
        ("settings.ini", '  ; murlocs:annotation/v1 evidence "ini.indented"\n', "ini.indented"),
    ],
)
def test_resolver_accepts_indented_real_comments(tmp_path, path, content, identifier):
    (tmp_path / path).write_text(content, encoding="utf-8")

    result = resolve_annotations(manifest(tmp_path, [annotation(identifier, path)]))

    assert [(item.identifier, item.location.line) for item in result.bindings] == [(identifier, 1)]


def test_resolver_accepts_a_real_inline_python_comment_but_not_a_quoted_prefix(tmp_path):
    (tmp_path / "source.py").write_text(
        'value = "# murlocs:annotation/v1 evidence \\\"quoted.marker\\\""\n'
        'value = 1  # murlocs:annotation/v1 evidence "actual.marker"\n',
        encoding="utf-8",
    )

    result = resolve_annotations(manifest(tmp_path, [annotation("actual.marker", "source.py")]))

    assert [(item.identifier, item.location.line) for item in result.bindings] == [
        ("actual.marker", 2)
    ]


def test_resolver_fails_closed_when_declared_file_changes_during_open(tmp_path, monkeypatch):
    target = tmp_path / "source.py"
    outside = tmp_path / "outside.py"
    target.write_text('# murlocs:annotation/v1 evidence "race.marker"\n', encoding="utf-8")
    outside.write_text('# murlocs:annotation/v1 evidence "race.marker"\n', encoding="utf-8")
    real_open = source_annotations.os.open

    def replace_then_open(path, flags):
        target.unlink()
        target.symlink_to(outside)
        return real_open(path, flags)

    monkeypatch.setattr(source_annotations.os, "open", replace_then_open)

    result = resolve_annotations(manifest(tmp_path, [annotation("race.marker", "source.py")]))

    assert result.bindings == ()
    assert result.findings == (
        AnnotationResolverFinding("annotation.excluded", "race.marker", "invariant-0"),
    )
def test_resolver_does_not_guess_markers_inside_strings_or_multiline_comments(tmp_path):
    (tmp_path / "source.py").write_text(
        "\n".join(
            [
                'doc = """',
                '# murlocs:annotation/v1 evidence "inside.docstring"',
                '"""',
                '# murlocs:annotation/v1 evidence "outside.comment"',
            ]
        ),
        encoding="utf-8",
    )
    result = resolve_annotations(
        manifest(tmp_path, [annotation("outside.comment", "source.py")])
    )
    assert [(item.identifier, item.location.line) for item in result.bindings] == [
        ("outside.comment", 4)
    ]

    (tmp_path / "source.js").write_text(
        "\n".join(
            [
                "/*",
                '// murlocs:annotation/v1 evidence "inside.block"',
                "*/",
                '// murlocs:annotation/v1 evidence "outside.comment"',
            ]
        ),
        encoding="utf-8",
    )
    result = resolve_annotations(
        manifest(tmp_path, [annotation("outside.comment", "source.js")])
    )
    assert [(item.identifier, item.location.line) for item in result.bindings] == [
        ("outside.comment", 4)
    ]


def test_validation_reports_stable_annotation_context_and_outcome_parity(tmp_path):
    (tmp_path / "source.py").write_text(
        "\n".join(
            [
                '# murlocs:annotation/v1 evidence "same.marker"',
                '# murlocs:annotation/v1 evidence "same.marker"',
            ]
        ),
        encoding="utf-8",
    )
    parsed = manifest(tmp_path, [annotation("same.marker", "source.py")])
    findings = annotation_findings(parsed)
    duplicate = [item for item in findings if item.code == "annotation.duplicate"]
    assert [(item.annotation_id, item.invariant_ids, item.scopes) for item in duplicate] == [
        ("same.marker", ("invariant-0",), ("root",)),
        ("same.marker", ("invariant-0",), ("root",)),
    ]
    assert {item.locations[0].line for item in duplicate} == {1, 2}
    outcome = build_check_outcome(parsed, findings)
    payload = next(
        item for item in outcome["findings"] if item["code"] == "MURLOCS_CHECK_ANNOTATION_DUPLICATE"
    )
    assert payload["status"] == "blocking"
    assert payload["severity"] == "important"
    assert payload["resolution_class"] == "agent_action"
    assert payload["action_ids"] == ["outcome.inspect-findings"]
    assert payload["affected"]["scopes"] == ["root"]
    assert {(item["reference"], item["detail"]) for item in payload["evidence"]} == {
        (item.code, item.message) for item in duplicate
    }


def test_validation_reports_marker_deletion_without_a_partial_binding(tmp_path):
    source = tmp_path / "source.py"
    source.write_text(
        '# murlocs:annotation/v1 evidence "deletion.marker"\nVALUE = 1\n',
        encoding="utf-8",
    )
    parsed = manifest(tmp_path, [annotation("deletion.marker", "source.py")])
    assert resolve_annotations(parsed).findings == ()

    source.write_text("VALUE = 1\n", encoding="utf-8")

    resolution = resolve_annotations(parsed)
    assert resolution.bindings == ()
    assert resolution.findings == (
        AnnotationResolverFinding(
            "annotation.missing", "deletion.marker", "invariant-0"
        ),
    )
    finding = annotation_findings(parsed)[0]
    assert (finding.code, finding.annotation_id, finding.locations) == (
        "annotation.missing",
        "deletion.marker",
        (),
    )


def test_validation_reports_copy_paste_duplication_at_every_location(tmp_path):
    source = tmp_path / "source.py"
    marker = '# murlocs:annotation/v1 evidence "copied.marker"'
    source.write_text(f"{marker}\nVALUE = 1\n", encoding="utf-8")
    parsed = manifest(tmp_path, [annotation("copied.marker", "source.py")])
    assert resolve_annotations(parsed).findings == ()

    source.write_text(f"{marker}\nVALUE = 1\n{marker}\n", encoding="utf-8")

    resolution = resolve_annotations(parsed)
    assert resolution.bindings == ()
    assert [
        (finding.code, finding.identifier, finding.location.line)
        for finding in resolution.findings
    ] == [
        ("annotation.duplicate", "copied.marker", 1),
        ("annotation.duplicate", "copied.marker", 3),
    ]
    assert [finding.locations[0].line for finding in annotation_findings(parsed)] == [1, 3]


def test_validation_reports_a_declared_file_rename_as_excluded(tmp_path):
    source = tmp_path / "source.py"
    source.write_text(
        '# murlocs:annotation/v1 evidence "rename.marker"\n', encoding="utf-8"
    )
    parsed = manifest(tmp_path, [annotation("rename.marker", "source.py")])
    assert resolve_annotations(parsed).findings == ()

    source.rename(tmp_path / "renamed.py")

    resolution = resolve_annotations(parsed)
    assert resolution.bindings == ()
    assert resolution.findings == (
        AnnotationResolverFinding(
            "annotation.excluded", "rename.marker", "invariant-0"
        ),
    )
    finding = annotation_findings(parsed)[0]
    assert (finding.annotation_id, finding.source_paths, finding.annotation_boundary) == (
        "rename.marker",
        ("source.py",),
        "excluded",
    )


def test_validation_reresolves_formatter_movement_to_the_new_physical_line(tmp_path):
    source = tmp_path / "source.py"
    marker = '# murlocs:annotation/v1 evidence "formatter.marker"'
    source.write_text(f"{marker}\nVALUE = 1\n", encoding="utf-8")
    parsed = manifest(tmp_path, [annotation("formatter.marker", "source.py")])
    before = resolve_annotations(parsed)
    assert annotation_findings(parsed) == []
    assert [(binding.identifier, binding.location.line) for binding in before.bindings] == [
        ("formatter.marker", 1)
    ]

    source.write_text(f"def f():\n    return 1\n\n\n{marker}\n", encoding="utf-8")

    after = resolve_annotations(parsed)
    assert after.findings == ()
    assert annotation_findings(parsed) == []
    assert [(binding.identifier, binding.location.line) for binding in after.bindings] == [
        ("formatter.marker", 5)
    ]


def test_validation_preserves_location_across_line_ending_conversion(tmp_path):
    source = tmp_path / "source.py"
    marker = '# murlocs:annotation/v1 evidence "line-ending.marker"'
    parsed = manifest(tmp_path, [annotation("line-ending.marker", "source.py")])
    expected = [("line-ending.marker", "source.py", 3)]

    source.write_bytes(f"VALUE = 1\r\n\r\n{marker}\r\n".encode())
    crlf = resolve_annotations(parsed)
    assert crlf.findings == ()
    assert annotation_findings(parsed) == []
    assert [
        (binding.identifier, binding.location.file, binding.location.line)
        for binding in crlf.bindings
    ] == expected

    source.write_text(f"VALUE = 1\n\n{marker}\n", encoding="utf-8")
    lf = resolve_annotations(parsed)
    assert lf.findings == ()
    assert annotation_findings(parsed) == []
    assert [
        (binding.identifier, binding.location.file, binding.location.line)
        for binding in lf.bindings
    ] == expected


def test_validation_reresolves_concurrent_manifest_and_source_edits(tmp_path):
    source = tmp_path / "source.py"
    source.write_text(
        '# murlocs:annotation/v1 evidence "before.marker"\n', encoding="utf-8"
    )
    before = manifest(tmp_path, [annotation("before.marker", "source.py")])
    assert resolve_annotations(before).findings == ()

    source.write_text(
        '# murlocs:annotation/v1 evidence "after.marker"\n', encoding="utf-8"
    )
    stale = resolve_annotations(before)
    assert stale.bindings == ()
    assert [finding.code for finding in stale.findings] == [
        "annotation.missing",
        "annotation.orphaned",
    ]
    assert [finding.code for finding in annotation_findings(before)] == [
        "annotation.missing",
        "annotation.orphaned",
    ]

    after = manifest(tmp_path, [annotation("after.marker", "source.py")])
    refreshed = resolve_annotations(after)
    assert refreshed.findings == ()
    assert annotation_findings(after) == []
    assert [
        (binding.identifier, binding.location.file, binding.location.line)
        for binding in refreshed.bindings
    ] == [("after.marker", "source.py", 1)]


@pytest.mark.parametrize(
    ("identifier", "path", "content", "code", "boundary"),
    [
        ("missing.marker", "source.py", "VALUE = 1\n", "annotation.missing", None),
        (
            "malformed.marker",
            "source.py",
            "# murlocs:annotation/v1 evidence malformed.marker\n",
            "annotation.malformed",
            None,
        ),
        (
            "future.marker",
            "source.py",
            '# murlocs:annotation/v2 evidence "future.marker"\n',
            "annotation.unknown-version",
            None,
        ),
        (
            "kind.marker",
            "source.py",
            '# murlocs:annotation/v1 applies "kind.marker"\n',
            "annotation.unknown-kind",
            None,
        ),
        (
            "vendored.marker",
            "vendor/source.py",
            '# murlocs:annotation/v1 evidence "vendored.marker"\n',
            "annotation.excluded",
            "vendored",
        ),
        (
            "generated.marker",
            "generated/source.py",
            '# murlocs:annotation/v1 evidence "generated.marker"\n',
            "annotation.excluded",
            "generated",
        ),
        (
            "unsupported.marker",
            "source.md",
            '# murlocs:annotation/v1 evidence "unsupported.marker"\n',
            "annotation.unsupported",
            None,
        ),
        ("binary.marker", "source.py", b"\xff", "annotation.undecodable", None),
    ],
)
def test_validation_covers_contract_states(tmp_path, identifier, path, content, code, boundary):
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        target.write_bytes(content)
    else:
        target.write_text(content, encoding="utf-8")
    findings = annotation_findings(manifest(tmp_path, [annotation(identifier, path)]))
    matched = [item for item in findings if item.code == code]
    assert matched
    assert all(item.annotation_id == identifier for item in matched)
    outcome = build_check_outcome(manifest(tmp_path, [annotation(identifier, path)]), findings)
    payload = next(
        item
        for item in outcome["findings"]
        if item["code"]
        == f"MURLOCS_CHECK_{code.replace('.', '_').replace('-', '_').upper()}"
    )
    assert (payload["status"], payload["severity"], payload["resolution_class"]) == (
        "blocking",
        "important",
        "agent_action",
    )
    assert payload["action_ids"] == ["outcome.inspect-findings"]
    if boundary is not None:
        assert all(f"boundary={boundary}" in item.message for item in matched)


def test_validation_maps_conflicting_candidates_to_precise_v1_codes(tmp_path):
    (tmp_path / "one.py").write_text(
        '# murlocs:annotation/v1 evidence "two.marker"\n', encoding="utf-8"
    )
    (tmp_path / "two.py").write_text("VALUE = 1\n", encoding="utf-8")
    result = resolve_annotations(
        manifest(
            tmp_path,
            [annotation("one.marker", "one.py"), annotation("two.marker", "two.py")],
        )
    )
    assert result.bindings == ()
    assert {item.code for item in result.findings} >= {
        "annotation.misplaced",
        "annotation.missing",
    }

    conflict_manifest = manifest(
        tmp_path,
        [annotation("one.marker", "one.py"), annotation("two.marker", "two.py")],
    )
    conflict = next(
        item
        for item in annotation_findings(conflict_manifest)
        if item.code == "annotation.misplaced"
    )
    assert conflict.annotation_id == "two.marker"
    assert conflict.invariant_ids == ("invariant-1",)
    conflict_outcome = build_check_outcome(
        conflict_manifest, annotation_findings(conflict_manifest)
    )
    for payload in conflict_outcome["findings"]:
        assert (payload["status"], payload["severity"], payload["resolution_class"]) == (
            "blocking",
            "important",
            "agent_action",
        )
        assert payload["action_ids"] == ["outcome.inspect-findings"]

    (tmp_path / "one.py").write_text(
        '# murlocs:annotation/v1 evidence "orphan.marker"\n', encoding="utf-8"
    )
    result = resolve_annotations(manifest(tmp_path, [annotation("one.marker", "one.py")]))
    assert result.bindings == ()
    assert {item.code for item in result.findings} == {
        "annotation.missing",
        "annotation.orphaned",
    }


def test_resolver_reports_unsupported_declared_file_without_guessing(tmp_path):
    (tmp_path / "example.md").write_text(
        '# murlocs:annotation/v1 evidence "unsupported.marker"\n', encoding="utf-8"
    )
    result = resolve_annotations(
        manifest(tmp_path, [annotation("unsupported.marker", "example.md")])
    )
    assert result.findings == (
        AnnotationResolverFinding("annotation.unsupported", "unsupported.marker", "invariant-0"),
    )


def test_resolver_rejects_duplicate_declarations_and_symlinks(tmp_path):
    source = tmp_path / "source.py"
    source.write_text('# murlocs:annotation/v1 evidence "same.marker"\n', encoding="utf-8")
    duplicate = resolve_annotations(
        manifest(
            tmp_path,
            [
                annotation("same.marker", "source.py"),
                annotation("same.marker", "source.py"),
            ],
        )
    )
    assert duplicate.findings == (AnnotationResolverFinding("annotation.duplicate", "same.marker"),)
    (tmp_path / "linked.py").symlink_to(source)
    symlink = resolve_annotations(manifest(tmp_path, [annotation("same.marker", "linked.py")]))
    assert symlink.findings[0].code == "annotation.excluded"


def test_resolver_enforces_file_byte_and_candidate_limits(tmp_path):
    limited_manifest = manifest(
        tmp_path,
        [annotation(f"marker-{number}", f"file-{number}.py") for number in range(257)],
    )
    many_files = resolve_annotations(limited_manifest)
    assert many_files.findings[0].code == "annotation.resource-limit"
    limit_outcome = build_check_outcome(limited_manifest, annotation_findings(limited_manifest))
    payload = next(
        item
        for item in limit_outcome["findings"]
        if item["code"] == "MURLOCS_CHECK_ANNOTATION_RESOURCE_LIMIT"
    )
    assert (payload["status"], payload["severity"], payload["resolution_class"]) == (
        "blocking",
        "important",
        "agent_action",
    )
    assert payload["action_ids"] == ["outcome.inspect-findings"]
    (tmp_path / "large.py").write_bytes(b"#" * (64 * 1024 + 1))
    too_large = resolve_annotations(manifest(tmp_path, [annotation("large.marker", "large.py")]))
    assert too_large.findings[0].code == "annotation.resource-limit"
    (tmp_path / "many.py").write_text(
        '# murlocs:annotation/v1 evidence "many.marker"\n' * 1025,
        encoding="utf-8",
    )
    too_many = resolve_annotations(manifest(tmp_path, [annotation("many.marker", "many.py")]))
    assert too_many.findings[0].code == "annotation.resource-limit"


def test_layered_annotation_declarations_render_and_override_deterministically(tmp_path):
    murlocs = tmp_path / ".murlocs"
    layers = murlocs / "layers"
    layers.mkdir(parents=True)
    (murlocs / "manifest.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                'network = "Layered"',
                'protocol = ".murlocs/PROTOCOL.md"',
                "[coverage]",
                "roots = []",
                "source_suffixes = []",
                "[coverage.exemptions]",
                "[policies]",
                "require_scope_invariants = false",
                "[[layers]]",
                'id = "base"',
                'kind = "base"',
                'path = ".murlocs/layers/base.toml"',
                "[[layers]]",
                'id = "overlay"',
                'kind = "overlay"',
                'path = ".murlocs/layers/overlay.toml"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (layers / "base.toml").write_text(
        "\n".join(
            [
                "[[scopes]]",
                'id = "root"',
                'path = "."',
                'map = "AGENTS.md"',
                'point_of_view = "Root."',
                "owns = []",
                "[[invariants]]",
                'id = "marker"',
                'scope = "root"',
                'statement = "Base."',
                'severity = "important"',
                'verification = "unknown"',
                (
                    'annotation = { id = "base.marker", kind = "evidence", '
                    'file = "base.py", version = "v1" }'
                ),
                "",
            ]
        ),
        encoding="utf-8",
    )
    (layers / "overlay.toml").write_text(
        "\n".join(
            [
                "[[invariants]]",
                'id = "marker"',
                "override = true",
                'scope = "root"',
                'statement = "Overlay."',
                'severity = "important"',
                'verification = "unknown"',
                (
                    'annotation = { id = "overlay.marker", kind = "evidence", '
                    'file = "overlay.py", version = "v1" }'
                ),
                "",
            ]
        ),
        encoding="utf-8",
    )
    resolved = resolve_manifest(tmp_path)
    parsed = parse_manifest_data(
        tmp_path,
        resolved.data,
        layered=resolved.layered,
        sources=resolved.sources,
        scope_layers=resolved.scope_layers,
        invariant_layers=resolved.invariant_layers,
        overrides=resolved.overrides,
    )
    assert parsed.invariants[0].annotation is not None
    assert parsed.invariants[0].annotation.identifier == "overlay.marker"
    assert parsed.source_for_invariant("marker") is not None
    assert parsed.source_for_invariant("marker").id == "overlay"
    (tmp_path / "overlay.py").write_text("VALUE = 1\n", encoding="utf-8")
    finding = next(
        item for item in annotation_findings(parsed) if item.code == "annotation.missing"
    )
    assert finding.declaration_sources == ("overlay@.murlocs/layers/overlay.toml",)
