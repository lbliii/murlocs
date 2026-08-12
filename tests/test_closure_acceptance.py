from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from murlocs.acceptance import (
    evaluate_closure_acceptance,
    extract_acceptance_exemptions,
    extract_closure_claims,
    format_closure_report,
    issues_with_acceptance_anchors,
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


def test_extract_closure_claims_handles_keywords_lists_and_urls():
    body = "\n".join(
        [
            "Closes #10",
            "This also fixes: #11, #12 and #13",
            "Resolves https://github.com/lbliii/murlocs/issues/14",
            "Closed owner/repo#15",
            "Mentioning issue #99 without a keyword does nothing.",
        ]
    )

    assert extract_closure_claims(body) == frozenset({10, 11, 12, 13, 14, 15})


def test_extract_acceptance_exemptions_parses_na_lines():
    body = "\n".join(
        [
            "Acceptance #20: n/a (docs-only)",
            "Acceptance #21: n/a",
            "Acceptance #22: deferred",
            "not an exemption Acceptance #23: n/a",
        ]
    )

    assert extract_acceptance_exemptions(body) == frozenset({20, 21})


def test_closure_gate_fails_when_claim_lacks_anchor_and_exemption(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "tests").mkdir()
    body = "Closes #50\n\nNo acceptance work here."

    verdict = evaluate_closure_acceptance(body, root, ("tests",))

    assert not verdict.ok
    assert verdict.missing == frozenset({50})
    assert "Missing acceptance anchors: #50" in format_closure_report(verdict)


def test_closure_gate_passes_when_pytest_marker_exists(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    write_issue_test(root, 50)
    body = "Fixes #50"

    verdict = evaluate_closure_acceptance(body, root, ("tests",))

    assert verdict.ok
    assert verdict.anchored == frozenset({50})
    assert verdict.missing == frozenset()


def test_closure_gate_passes_with_acceptance_na_exemption(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "tests").mkdir()
    body = "\n".join(
        [
            "Resolves #50",
            "",
            "Acceptance #50: n/a (chore; no executable criteria)",
        ]
    )

    verdict = evaluate_closure_acceptance(body, root, ("tests",))

    assert verdict.ok
    assert verdict.exempted == frozenset({50})
    assert verdict.missing == frozenset()


def test_closure_gate_vacuous_when_body_has_no_claims(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    verdict = evaluate_closure_acceptance("Just a refactor.", root, ("tests",))

    assert verdict.ok
    assert verdict.claimed == frozenset()


def test_check_closure_acceptance_script_exit_codes(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    write_issue_test(root, 60)
    script = Path(__file__).resolve().parents[1] / "scripts" / "check_closure_acceptance.py"
    python = sys.executable

    failing = subprocess.run(
        [python, str(script), "--repo", str(root), "--test-root", "tests"],
        input="Closes #61\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert failing.returncode == 1
    assert "#61" in failing.stderr

    passing_anchor = subprocess.run(
        [python, str(script), "--repo", str(root), "--test-root", "tests"],
        input="Closes #60\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert passing_anchor.returncode == 0

    body_file = tmp_path / "body.md"
    body_file.write_text(
        "Closes #61\n\nAcceptance #61: n/a (fixture)\n",
        encoding="utf-8",
    )
    passing_na = subprocess.run(
        [
            python,
            str(script),
            "--repo",
            str(root),
            "--test-root",
            "tests",
            "--body-file",
            str(body_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert passing_na.returncode == 0


@pytest.mark.issue(207)
def test_dogfood_closure_gate_for_issue_207():
    """Executable proof that issue #207 closure gating is wired end-to-end."""
    root = Path(__file__).resolve().parents[1]
    assert 207 in issues_with_acceptance_anchors(root, ("tests",))

    failing = evaluate_closure_acceptance("Closes #99998", root, ("tests",))
    assert not failing.ok
    assert failing.missing == frozenset({99998})

    passing = evaluate_closure_acceptance("Closes #207\n", root, ("tests",))
    assert passing.ok
    assert 207 in passing.anchored

    exempted = evaluate_closure_acceptance(
        "Closes #99999\n\nAcceptance #99999: n/a (synthetic)\n",
        root,
        ("tests",),
    )
    assert exempted.ok
    assert 99999 in exempted.exempted
