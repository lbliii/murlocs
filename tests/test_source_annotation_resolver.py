from __future__ import annotations

import json
import shutil
import tomllib
from pathlib import Path

import pytest

from murlocs.layers import resolve_manifest
from murlocs.manifest import parse_manifest_data
from murlocs.serialization import render_manifest_data
from murlocs.source_annotations import AnnotationResolverFinding, resolve_annotations

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
    many_files = resolve_annotations(
        manifest(
            tmp_path,
            [annotation(f"marker-{number}", f"file-{number}.py") for number in range(257)],
        )
    )
    assert many_files.findings[0].code == "annotation.resource-limit"
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
    parsed = parse_manifest_data(tmp_path, resolve_manifest(tmp_path).data)
    assert parsed.invariants[0].annotation is not None
    assert parsed.invariants[0].annotation.identifier == "overlay.marker"
