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

# GitHub closing keywords (close/fix/resolve + tense variants), then one or more
# issue references (#N, owner/repo#N, or github.com/.../issues/N).
_CLOSURE_KEYWORD = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b\s*:?",
)
_ISSUE_REF = re.compile(
    r"(?:"
    r"https://github\.com/[\w.-]+/[\w.-]+/issues/(\d+)"
    r"|[\w.-]+/[\w.-]+#(\d+)"
    r"|#(\d+)"
    r")"
)
_LIST_CONNECTOR = re.compile(r"(?i)^(?:\s*[,]\s*|\s+and\s+|\s+)")
# Explicit exemption line in a PR body; reason in parentheses is recommended.
_ACCEPTANCE_NA = re.compile(
    r"(?im)^[ \t]*Acceptance[ \t]+#(\d+):[ \t]*n/a(?:[ \t]*\([^)\n]*\))?[ \t]*$"
)


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
        raise ValueError("acceptance must be adapter:reference, for example pytest:issue(206)")
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
        if not any(
            isinstance(target, ast.Name) and target.id == "pytestmark" for target in targets
        ):
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
            own = {
                number for dec in node.decorator_list for number in _issue_args_from_decorator(dec)
            }
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
            except (OSError, SyntaxError):
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


@dataclass(frozen=True)
class ClosureVerdict:
    """Outcome of checking PR closure claims against offline acceptance anchors."""

    claimed: frozenset[int]
    anchored: frozenset[int]
    exempted: frozenset[int]
    missing: frozenset[int]

    @property
    def ok(self) -> bool:
        return not self.missing


def extract_closure_claims(body: str) -> frozenset[int]:
    """Return issue numbers a PR body claims to close/fix/resolve."""
    claims: set[int] = set()
    for keyword in _CLOSURE_KEYWORD.finditer(body):
        pos = keyword.end()
        # Leading whitespace before the first issue reference.
        leading = re.match(r"^\s+", body[pos:])
        if leading is not None:
            pos += leading.end()
        while pos < len(body):
            ref = _ISSUE_REF.match(body, pos)
            if ref is None:
                break
            number = next(group for group in ref.groups() if group is not None)
            claims.add(int(number))
            pos = ref.end()
            connector = _LIST_CONNECTOR.match(body[pos:])
            if connector is None:
                break
            pos += connector.end()
            # Stop if the connector did not leave us at another issue ref.
            if _ISSUE_REF.match(body, pos) is None:
                break
    return frozenset(claims)


def extract_acceptance_exemptions(body: str) -> frozenset[int]:
    """Return issue numbers declared ``Acceptance #N: n/a`` in a PR body."""
    return frozenset(int(match.group(1)) for match in _ACCEPTANCE_NA.finditer(body))


def issues_with_acceptance_anchors(
    root: Path,
    test_roots: tuple[str, ...] | None = None,
) -> frozenset[int]:
    """Return issue numbers that have at least one offline pytest acceptance marker."""
    discovered = collect_pytest_issue_tests(root, test_roots)
    numbers: set[int] = set()
    for reference in discovered:
        if reference.startswith("issue(") and reference.endswith(")"):
            payload = reference.removeprefix("issue(").removesuffix(")")
            if payload.isdigit():
                numbers.add(int(payload))
    return frozenset(numbers)


def evaluate_closure_acceptance(
    body: str,
    root: Path,
    test_roots: tuple[str, ...] | None = None,
) -> ClosureVerdict:
    """Fail closed when a closure claim lacks an anchor and lacks an n/a exemption."""
    claimed = extract_closure_claims(body)
    exempted = extract_acceptance_exemptions(body)
    if not claimed:
        return ClosureVerdict(
            claimed=frozenset(),
            anchored=frozenset(),
            exempted=exempted,
            missing=frozenset(),
        )
    anchored = issues_with_acceptance_anchors(root, test_roots)
    missing = frozenset(
        number for number in claimed if number not in anchored and number not in exempted
    )
    return ClosureVerdict(
        claimed=claimed,
        anchored=frozenset(number for number in claimed if number in anchored),
        exempted=frozenset(number for number in claimed if number in exempted),
        missing=missing,
    )


def format_closure_report(verdict: ClosureVerdict) -> str:
    """Render a human-readable closure-gate report."""
    if not verdict.claimed:
        return "No Closes/Fixes/Resolves claims in PR body; closure gate is vacuously ok."
    lines = [
        f"Closure claims: {', '.join(f'#{n}' for n in sorted(verdict.claimed))}",
    ]
    if verdict.anchored:
        lines.append("Anchored: " + ", ".join(f"#{n}" for n in sorted(verdict.anchored)))
    if verdict.exempted:
        lines.append(
            "Exempted (Acceptance #N: n/a): "
            + ", ".join(f"#{n}" for n in sorted(verdict.exempted))
        )
    if verdict.missing:
        lines.append(
            "Missing acceptance anchors: "
            + ", ".join(f"#{n}" for n in sorted(verdict.missing))
        )
        lines.append(
            "Add @pytest.mark.issue(N) (or a work-item acceptance anchor that "
            "resolves offline), or declare `Acceptance #N: n/a (reason)` in the PR body."
        )
    else:
        lines.append("All closure claims have an acceptance anchor or n/a exemption.")
    return "\n".join(lines)
