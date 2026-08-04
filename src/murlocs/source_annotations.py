"""Version 1 source-annotation grammar reference.

This module deliberately validates a *comment body*, not a source file. Comment
recognition, declared-file selection, and binding to manifest/layer declarations
remain future resolver work. Keeping this narrow lets the v1 corpus exercise one
portable grammar without accidentally making source comments authoritative.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

NAMESPACE = "murlocs:annotation"
VERSION = "v1"
KIND = "evidence"
MAX_COMMENT_BYTES = 256
MAX_IDENTIFIER_BYTES = 128
_IDENTIFIER = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")


@dataclass(frozen=True)
class Annotation:
    """A grammar-valid, still-untrusted source attachment candidate."""

    identifier: str
    kind: str = KIND
    version: str = VERSION


@dataclass(frozen=True)
class AnnotationFinding:
    """A stable grammar finding with no retained source prose."""

    code: str


def parse_v1_comment(comment: str) -> Annotation | AnnotationFinding | None:
    """Parse one already-recognized comment body under the v1 grammar.

    ``None`` means the comment is outside the Murlocs namespace. The parser never
    searches source text, follows paths, or promotes a parsed result to guidance.
    """
    if not comment.startswith(NAMESPACE):
        return None
    if len(comment.encode("utf-8")) > MAX_COMMENT_BYTES:
        return AnnotationFinding("annotation.malformed")
    if not comment.startswith(f"{NAMESPACE}/"):
        return AnnotationFinding("annotation.malformed")

    remainder = comment.removeprefix(f"{NAMESPACE}/")
    version, separator, directive = remainder.partition(" ")
    if not separator:
        return AnnotationFinding("annotation.malformed")
    if version != VERSION:
        return AnnotationFinding("annotation.unknown-version")
    if not directive.startswith(f"{KIND} "):
        return AnnotationFinding("annotation.unknown-kind")

    quoted_identifier = directive.removeprefix(f"{KIND} ")
    if (
        len(quoted_identifier) < 2
        or not quoted_identifier.startswith('"')
        or not quoted_identifier.endswith('"')
    ):
        return AnnotationFinding("annotation.malformed")
    identifier = quoted_identifier[1:-1]
    if '"' in identifier or "\\" in identifier:
        return AnnotationFinding("annotation.malformed")
    if len(identifier.encode("utf-8")) > MAX_IDENTIFIER_BYTES or not _IDENTIFIER.fullmatch(
        identifier
    ):
        return AnnotationFinding("annotation.malformed")
    return Annotation(identifier=identifier)
