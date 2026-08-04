from __future__ import annotations

import json
from pathlib import Path

import pytest

from murlocs.source_annotations import (
    Annotation,
    AnnotationFinding,
    parse_v1_comment,
)

CORPUS = Path(__file__).parent / "fixtures" / "source-annotation-contract" / "v1"
CORPUS_DATA = json.loads((CORPUS / "cases.json").read_text(encoding="utf-8"))
CASES = CORPUS_DATA["cases"]
RESOLUTION_CODES = {
    "annotation.missing",
    "annotation.duplicate",
    "annotation.orphaned",
    "annotation.misplaced",
    "annotation.unsupported",
    "annotation.excluded",
    "annotation.undecodable",
    "annotation.resource-limit",
}


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_v1_corpus_has_a_single_deterministic_grammar_result(case: dict[str, object]):
    comment = case.get("comment")
    expected = case["expect"]
    if comment is None:
        assert expected == {"inert": True}
        return

    result = parse_v1_comment(comment)
    if "identifier" in expected:
        assert result == Annotation(identifier=expected["identifier"])
    elif "finding" in expected:
        assert result == AnnotationFinding(code=expected["finding"])
    else:
        assert result is None


def test_v1_corpus_locations_exist_and_preserve_crlf():
    for case in CASES:
        path = case.get("path")
        line = case.get("line")
        if path is None:
            continue
        source = (CORPUS / path).read_bytes()
        if path == "crlf.py":
            assert b"\r\n" in source
        assert isinstance(line, int)
        lines = source.decode("utf-8").splitlines()
        assert len(lines) >= line
        if comment := case.get("comment"):
            assert comment in lines[line - 1]


def test_v1_corpus_is_not_a_source_annotation_resolver():
    """The reference parser accepts only a comment body, keeping #84 inert."""
    source = (CORPUS / "python.py").read_text(encoding="utf-8")
    assert parse_v1_comment(source) is None


def test_v1_corpus_declares_deterministic_resolver_outcomes_without_implementing_one():
    for case in CORPUS_DATA["resolution_cases"]:
        assert case["expect"] in RESOLUTION_CODES
        for comment in case["comments"]:
            assert isinstance(parse_v1_comment(comment), Annotation)
