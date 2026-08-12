"""Acceptance-anchor strength: mutation/revert proves the test is faithful."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from murlocs.acceptance import (
    AcceptanceStrengthResult,
    acceptance_anchor_findings,
    format_strength_report,
    temporary_path_snapshots,
    verify_acceptance_strength,
)
from murlocs.manifest import load_manifest


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fixture_repo(root: Path, *, issue: int, test_body: str, impl: str) -> None:
    _write(root / "pkg" / "feature.py", impl)
    _write(root / "pkg" / "__init__.py", "")
    _write(
        root / "tests" / "test_feature.py",
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "import sys",
                "from pathlib import Path",
                "",
                "import pytest",
                "",
                "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))",
                "",
                f"@pytest.mark.issue({issue})",
                "def test_feature_acceptance():",
                *[f"    {line}" if line else "" for line in test_body.splitlines()],
                "",
            ]
        ),
    )
    _write(
        root / "pytest.ini",
        "\n".join(
            [
                "[pytest]",
                "markers =",
                "    issue: acceptance anchor marker",
                "",
            ]
        ),
    )


def _run_fixture_pytest(root: Path, node_ids: list[str] | tuple[str, ...]) -> int:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", *node_ids, "-q", "--tb=no"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode


def test_tautological_acceptance_test_is_rejected(tmp_path: Path):
    root = tmp_path / "tautology"
    issue = 9001
    _fixture_repo(
        root,
        issue=issue,
        impl='VALUE = "implemented"\n',
        test_body="assert True",
    )
    baseline = {"pkg/feature.py": 'VALUE = "reverted"\n'}

    result = verify_acceptance_strength(
        root,
        issue,
        baseline_snapshots=baseline,
        run_tests=_run_fixture_pytest,
    )

    assert result.clean_passed is True
    assert result.mutated_failed is False
    assert result.strong is False
    assert "tautological or weak" in result.message


def test_faithful_acceptance_test_is_accepted(tmp_path: Path):
    root = tmp_path / "faithful"
    issue = 9002
    _fixture_repo(
        root,
        issue=issue,
        impl='VALUE = "implemented"\n',
        test_body=('from pkg.feature import VALUE\nassert VALUE == "implemented"'),
    )
    baseline = {"pkg/feature.py": 'VALUE = "reverted"\n'}

    result = verify_acceptance_strength(
        root,
        issue,
        baseline_snapshots=baseline,
        run_tests=_run_fixture_pytest,
    )

    assert result.clean_passed is True
    assert result.mutated_failed is True
    assert result.strong is True
    assert "anchor is strong" in result.message


def test_strength_check_requires_clean_pass(tmp_path: Path):
    root = tmp_path / "broken"
    issue = 9003
    _fixture_repo(
        root,
        issue=issue,
        impl='VALUE = "implemented"\n',
        test_body="assert False",
    )

    result = verify_acceptance_strength(
        root,
        issue,
        baseline_snapshots={"pkg/feature.py": 'VALUE = "reverted"\n'},
        run_tests=_run_fixture_pytest,
    )

    assert result.clean_passed is False
    assert result.strong is False
    assert "must pass on the clean tree" in result.message


def test_strength_check_reports_missing_acceptance_test(tmp_path: Path):
    root = tmp_path / "missing"
    root.mkdir()
    (root / "tests").mkdir()
    (root / "pkg").mkdir()
    _write(root / "pkg" / "feature.py", "VALUE = 1\n")

    result = verify_acceptance_strength(
        root,
        42,
        baseline_snapshots={"pkg/feature.py": "VALUE = 0\n"},
        run_tests=_run_fixture_pytest,
    )

    assert result.strong is False
    assert "no runnable" in result.message


def test_temporary_path_snapshots_restores_tree(tmp_path: Path):
    root = tmp_path / "restore"
    path = root / "pkg" / "feature.py"
    _write(path, "current\n")

    with temporary_path_snapshots(root, {"pkg/feature.py": "baseline\n"}):
        assert path.read_text(encoding="utf-8") == "baseline\n"

    assert path.read_text(encoding="utf-8") == "current\n"


def test_format_strength_report_names_status():
    report = format_strength_report(
        AcceptanceStrengthResult(
            issue=209,
            strong=True,
            clean_passed=True,
            mutated_failed=True,
            locations=("tests/test_acceptance_strength.py::test_x",),
            mutated_paths=("src/murlocs/acceptance.py",),
            message="ok",
        )
    )

    assert "STRONG" in report
    assert "#209" in report


@pytest.mark.issue(209)
def test_dogfood_acceptance_strength_for_issue_209(tmp_path: Path):
    """Executable proof that #209 strength checking rejects tautologies and accepts faithful tests.

    This dogfood test is itself meaningful: it exercises
    ``verify_acceptance_strength`` end-to-end. Reverting the strength API would
    break these assertions, so a Closes #209 strength check against
    ``src/murlocs/acceptance.py`` can honor this marker.
    """
    tautology = tmp_path / "dogfood_tautology"
    _fixture_repo(
        tautology,
        issue=209,
        impl='VALUE = "implemented"\n',
        test_body="assert True",
    )
    weak = verify_acceptance_strength(
        tautology,
        209,
        baseline_snapshots={"pkg/feature.py": 'VALUE = "reverted"\n'},
        run_tests=_run_fixture_pytest,
    )
    assert weak.strong is False

    faithful = tmp_path / "dogfood_faithful"
    _fixture_repo(
        faithful,
        issue=209,
        impl='VALUE = "implemented"\n',
        test_body=('from pkg.feature import VALUE\nassert VALUE == "implemented"'),
    )
    strong = verify_acceptance_strength(
        faithful,
        209,
        baseline_snapshots={"pkg/feature.py": 'VALUE = "reverted"\n'},
        run_tests=_run_fixture_pytest,
    )
    assert strong.strong is True

    # Manifest work item resolves offline (presence half of the contract).
    repo = Path(__file__).resolve().parents[1]
    findings = [
        item
        for item in acceptance_anchor_findings(load_manifest(repo))
        if item.code == "acceptance-anchor" and "209" in item.message
    ]
    assert findings == []
