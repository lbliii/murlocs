from __future__ import annotations

import json
from pathlib import Path

import pytest

from murlocs.acceptance import (
    acceptance_anchor_findings,
    collect_pytest_issue_tests,
    discover_acceptance_tests,
    parse_acceptance_anchor,
    resolve_work_item_anchor,
)
from murlocs.manifest import load_manifest, parse_manifest_data
from murlocs.model import AcceptanceAnchor, WorkItem
from murlocs.verify import Finding
from tests.support import invoke


def manifest(root: Path, work_items: list[dict[str, object]] | None = None):
    return parse_manifest_data(
        root,
        {
            "schema_version": 1,
            "network": "Fixture",
            "protocol": ".murlocs/PROTOCOL.md",
            "coverage": {"roots": ["tests"], "source_suffixes": [".py"], "exemptions": {}},
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
            "work_items": work_items or [],
        },
    )


def write_issue_test(root: Path, issue: int, *, name: str = "test_proves_issue") -> Path:
    tests = root / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    path = tests / f"test_issue_{issue}.py"
    path.write_text(
        "\n".join(
            [
                "import pytest",
                "",
                f"@pytest.mark.issue({issue})",
                f"def {name}():",
                "    assert True",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_parse_acceptance_anchor_splits_adapter_and_reference():
    parsed = parse_acceptance_anchor("pytest:issue(206)")

    assert parsed.adapter == "pytest"
    assert parsed.reference == "issue(206)"


def test_collect_pytest_issue_tests_discovers_function_class_and_module_markers(tmp_path: Path):
    root = tmp_path / "repo"
    tests = root / "tests"
    tests.mkdir(parents=True)
    (tests / "test_module_marker.py").write_text(
        "\n".join(
            [
                "import pytest",
                "",
                "pytestmark = pytest.mark.issue(10)",
                "",
                "def test_module_level():",
                "    assert True",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (tests / "test_class_marker.py").write_text(
        "\n".join(
            [
                "import pytest",
                "",
                "@pytest.mark.issue(11)",
                "class TestIssue:",
                "    def test_in_class(self):",
                "        assert True",
                "",
            ]
        ),
        encoding="utf-8",
    )
    write_issue_test(root, 12)

    discovered = collect_pytest_issue_tests(root, ("tests",))

    assert "issue(10)" in discovered
    assert "issue(11)" in discovered
    assert "issue(12)" in discovered
    assert any("test_module_marker.py::test_module_level" in item.location for item in discovered["issue(10)"])
    assert any("test_class_marker.py::TestIssue::test_in_class" in item.location for item in discovered["issue(11)"])
    assert any("test_issue_12.py::test_proves_issue" in item.location for item in discovered["issue(12)"])


def test_acceptance_anchor_findings_reports_missing_executable_test(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    item = manifest(
        root,
        [
            {
                "id": "206",
                "issue": 206,
                "acceptance": "pytest:issue(206)",
            }
        ],
    )

    findings = acceptance_anchor_findings(item)

    assert findings == [
        Finding(
            "acceptance-anchor",
            "work item 206 (issue #206) declares acceptance pytest:issue(206) "
            "but no executable test was found offline",
        )
    ]


def test_acceptance_anchor_findings_resolves_declared_work_item(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    write_issue_test(root, 206, name="test_acceptance_resolves")
    item = manifest(
        root,
        [
            {
                "id": "206",
                "issue": 206,
                "acceptance": "pytest:issue(206)",
            }
        ],
    )

    assert acceptance_anchor_findings(item) == []


def test_renamed_test_breaks_offline_acceptance_resolution(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    path = write_issue_test(root, 99, name="test_before_rename")
    work_item = WorkItem(
        id="99",
        issue=99,
        acceptance=AcceptanceAnchor(adapter="pytest", reference="issue(99)"),
    )
    discovered = discover_acceptance_tests(root, "pytest", ("tests",))

    assert resolve_work_item_anchor(work_item, discovered)

    path.write_text(
        "\n".join(
            [
                "import pytest",
                "",
                "@pytest.mark.issue(99)",
                "def test_after_rename():",
                "    assert True",
                "",
            ]
        ),
        encoding="utf-8",
    )
    rediscovered = discover_acceptance_tests(root, "pytest", ("tests",))
    locations = [item.location for item in rediscovered["issue(99)"]]

    assert any("test_after_rename" in location for location in locations)
    assert all("test_before_rename" not in location for location in locations)


def test_check_command_surfaces_missing_acceptance_anchor(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".murlocs").mkdir()
    (root / ".murlocs" / "PROTOCOL.md").write_text("# Protocol\n", encoding="utf-8")
    (root / ".murlocs" / "manifest.toml").write_text(
        "\n".join(
            [
                'schema_version = 1',
                'network = "Fixture"',
                'protocol = ".murlocs/PROTOCOL.md"',
                "",
                "[coverage]",
                'roots = ["tests"]',
                'source_suffixes = [".py"]',
                "",
                "[[scopes]]",
                'id = "root"',
                'path = "."',
                'map = "AGENTS.md"',
                'point_of_view = "Fixture."',
                "owns = []",
                "",
                "[[work_items]]",
                'id = "206"',
                "issue = 206",
                'acceptance = "pytest:issue(206)"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text("# Root\n", encoding="utf-8")

    result = invoke("check", "--repo", str(root), "--format", "json")

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert any(item["code"] == "acceptance-anchor" for item in payload["findings"])


@pytest.mark.issue(206)
def test_dogfood_acceptance_anchor_for_issue_206():
    """Executable proof that issue #206 acceptance discovery is wired end-to-end."""
    root = Path(__file__).resolve().parents[1]
    discovered = collect_pytest_issue_tests(root, ("tests",))

    assert "issue(206)" in discovered
    assert any("test_acceptance_anchors.py" in item.location for item in discovered["issue(206)"])

    findings = acceptance_anchor_findings(load_manifest(root))
    assert not any(item.code == "acceptance-anchor" and "206" in item.message for item in findings)
