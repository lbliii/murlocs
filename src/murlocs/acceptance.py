"""Offline discovery of executable acceptance tests for backlog work items.

An acceptance anchor binds a work item to a test that proves its criteria.
Adapters are pluggable so the same manifest contract can target pytest today
and other test runners later without changing the discovery boundary.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from murlocs.model import Manifest, WorkItem
    from murlocs.verify import Finding

DEFAULT_TEST_ROOTS = ("tests", "examples")
_ANCHOR = re.compile(r"^([a-z][a-z0-9_-]*):(.+)\Z")


@dataclass(frozen=True)
class ParsedAcceptanceAnchor:
    """A language-agnostic acceptance reference split into adapter and payload."""

    adapter: str
    reference: str


@dataclass(frozen=True)
class AcceptanceTestLocation:
    """One executable test location discovered without running the suite."""

    location: str


class AcceptanceAdapter(Protocol):
    """Discover executable tests that satisfy adapter-specific anchor references."""

    name: str

    def discover(
        self,
        root: Path,
        test_roots: tuple[str, ...],
    ) -> dict[str, tuple[AcceptanceTestLocation, ...]]:
        """Return ``{reference: locations}`` for every anchor reference found."""


def parse_acceptance_anchor(value: str) -> ParsedAcceptanceAnchor:
    """Parse ``adapter:reference`` such as ``pytest:issue(206)``."""
    match = _ANCHOR.match(value.strip())
    if match is None:
        raise ValueError(
            "acceptance must be adapter:reference, for example pytest:issue(206)"
        )
    adapter, reference = match.groups()
    if not reference:
        raise ValueError("acceptance reference must not be empty")
    return ParsedAcceptanceAnchor(adapter=adapter, reference=reference)


def _issue_args_from_decorator(node: ast.expr) -> list[int]:
    """Return issue numbers from a ``pytest.mark.issue(...)`` decorator."""
    if not isinstance(node, ast.Call):
        return []
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr == "issue"):
        return []
    return [
        arg.value
        for arg in node.args
        if isinstance(arg, ast.Constant) and isinstance(arg.value, int)
    ]


def _module_level_issue_markers(tree: ast.Module) -> list[int]:
    """Issue numbers declared via a module-level ``pytestmark`` assignment."""
    issues: list[int] = []
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if not any(isinstance(target, ast.Name) and target.id == "pytestmark" for target in targets):
            continue
        value = node.value
        marks: Iterable[ast.expr]
        if isinstance(value, (ast.List, ast.Tuple)):
            marks = value.elts
        elif value is not None:
            marks = [value]
        else:
            marks = []
        for mark in marks:
            issues.extend(_issue_args_from_decorator(mark))
    return issues


def _qualname(stack: list[str], name: str) -> str:
    return "::".join([*stack, name])


def _collect_from_body(
    body: list[ast.stmt],
    stack: list[str],
    inherited: set[int],
    rel: str,
    record,
) -> None:
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            own = {number for dec in node.decorator_list for number in _issue_args_from_decorator(dec)}
            for issue in own | inherited:
                record(issue, f"{rel}::{_qualname(stack, node.name)}")
        elif isinstance(node, ast.ClassDef):
            cls_issues = {
                number for dec in node.decorator_list for number in _issue_args_from_decorator(dec)
            }
            _collect_from_body(node.body, [*stack, node.name], inherited | cls_issues, rel, record)


def collect_pytest_issue_tests(
    root: Path,
    test_roots: tuple[str, ...] | None = None,
) -> dict[str, tuple[AcceptanceTestLocation, ...]]:
    """Walk test roots and return ``issue(N) -> [test locations]``."""
    roots = [root / name for name in (test_roots or DEFAULT_TEST_ROOTS)]
    mapping: dict[str, set[str]] = {}

    def _record(issue: int, location: str) -> None:
        mapping.setdefault(f"issue({issue})", set()).add(location)

    for test_root in roots:
        if not test_root.exists():
            continue
        for path in sorted(test_root.rglob("test_*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except OSError, SyntaxError:
                continue
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                rel = path.name

            module_issues = _module_level_issue_markers(tree)
            for issue in module_issues:
                _record(issue, f"{rel}::<module>")

            _collect_from_body(tree.body, [], set(module_issues), rel, _record)

    return {
        reference: tuple(AcceptanceTestLocation(location=item) for item in sorted(locations))
        for reference, locations in sorted(mapping.items())
    }


class PytestIssueAdapter:
    """Discover ``@pytest.mark.issue(N)`` markers in Python test files."""

    name = "pytest"

    def discover(
        self,
        root: Path,
        test_roots: tuple[str, ...],
    ) -> dict[str, tuple[AcceptanceTestLocation, ...]]:
        return collect_pytest_issue_tests(root, test_roots or DEFAULT_TEST_ROOTS)


_ADAPTERS: dict[str, AcceptanceAdapter] = {
    PytestIssueAdapter.name: PytestIssueAdapter(),
}


def get_adapter(name: str) -> AcceptanceAdapter | None:
    return _ADAPTERS.get(name)


def discover_acceptance_tests(
    root: Path,
    adapter: str,
    test_roots: tuple[str, ...],
) -> dict[str, tuple[AcceptanceTestLocation, ...]]:
    """Discover every anchor reference an adapter can see offline."""
    implementation = get_adapter(adapter)
    if implementation is None:
        return {}
    return implementation.discover(root, test_roots)


def resolve_work_item_anchor(
    work_item: WorkItem,
    discovered: dict[str, tuple[AcceptanceTestLocation, ...]],
) -> tuple[AcceptanceTestLocation, ...]:
    """Return discovered test locations for one declared work-item anchor."""
    if work_item.acceptance is None:
        return ()
    return discovered.get(work_item.acceptance.reference, ())


def acceptance_anchor_findings(manifest: Manifest) -> list[Finding]:
    """Report declared work-item anchors that have no offline executable test."""
    from murlocs.verify import Finding

    if not manifest.work_items:
        return []

    test_roots = manifest.coverage_roots or DEFAULT_TEST_ROOTS
    cache: dict[str, dict[str, tuple[AcceptanceTestLocation, ...]]] = {}
    findings: list[Finding] = []

    for work_item in manifest.work_items:
        if work_item.acceptance is None:
            continue
        adapter_name = work_item.acceptance.adapter
        if adapter_name not in cache:
            cache[adapter_name] = discover_acceptance_tests(
                manifest.root,
                adapter_name,
                test_roots,
            )
        discovered = cache[adapter_name]
        if adapter_name not in _ADAPTERS:
            findings.append(
                Finding(
                    "acceptance-anchor",
                    (
                        f"work item {work_item.id} uses unknown acceptance adapter "
                        f"{adapter_name!r}; supported: {', '.join(sorted(_ADAPTERS))}"
                    ),
                )
            )
            continue
        locations = resolve_work_item_anchor(work_item, discovered)
        anchor = f"{adapter_name}:{work_item.acceptance.reference}"
        if not locations:
            issue_text = f" (issue #{work_item.issue})" if work_item.issue is not None else ""
            findings.append(
                Finding(
                    "acceptance-anchor",
                    (
                        f"work item {work_item.id}{issue_text} declares acceptance "
                        f"{anchor} but no executable test was found offline"
                    ),
                )
            )
    return findings
