"""Host capability matrix schema, evidence gating, and unknown defaults (#139)."""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import pytest

from murlocs.host_capability import (
    CONTRACT,
    SCHEMA_VERSION,
    HostCapabilityError,
    default_matrix_path,
    effective_tiers,
    load_host_capability_matrix,
    resolve_host_capability_matrix,
    stale_after,
)

pytestmark = pytest.mark.issue(139)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "host-capability-matrix" / "v1"
MATRIX_PATH = FIXTURE_DIR / "matrix.json"
SCHEMA_PATH = FIXTURE_DIR / "schema.json"
DOC_PATH = ROOT / "docs" / "host-capability-matrix.md"


def _matrix() -> dict[str, object]:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def test_checked_in_matrix_resolves_required_hosts_with_evidence_gated_tiers():
    resolved = load_host_capability_matrix(
        MATRIX_PATH, repository_root=ROOT, as_of=date(2026, 8, 12)
    )

    assert resolved["contract"] == CONTRACT
    assert resolved["schema_version"] == SCHEMA_VERSION
    assert resolved["as_of"] == "2026-08-12"
    assert default_matrix_path() == MATRIX_PATH

    tiers = effective_tiers(resolved)
    assert tiers == {
        "openai-codex": "tool-only",
        "claude-code": "adapted",
        "cursor": "unknown",
        "github-copilot": "adapted",
    }

    by_id = {profile["id"]: profile for profile in resolved["profiles"]}
    assert by_id["github-copilot"]["host_kind"] == "orchestrator"
    assert by_id["claude-code"]["hooks"]["claim_basis"] == "observed"
    assert by_id["openai-codex"]["hooks"]["claim_basis"] == "documented"
    assert by_id["cursor"]["claimed_tier"] == "unknown"
    assert by_id["cursor"]["effective_tier"] == "unknown"
    assert by_id["cursor"]["evidence_gaps"] == []
    assert all(profile["claimed_tier"] != "native" for profile in resolved["profiles"])


def test_schema_fixture_and_docs_are_present_for_reviewable_updates():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    doc = DOC_PATH.read_text(encoding="utf-8")

    assert schema["properties"]["contract"]["const"] == CONTRACT
    assert schema["properties"]["schema_version"]["const"] == SCHEMA_VERSION
    assert "native" in schema["$defs"]["support_tier"]["enum"]
    assert "documented" in schema["$defs"]["claim_basis"]["enum"]
    assert "observed" in schema["$defs"]["claim_basis"]["enum"]

    assert "host-capability-matrix/v1/matrix.json" in doc
    assert "`unknown`" in doc
    assert "Portable fallback" in doc
    for host in ("Codex", "Claude Code", "Cursor", "GitHub Copilot"):
        assert host in doc


def test_missing_evidence_path_forces_unknown_effective_tier():
    document = _matrix()
    claude = next(
        profile for profile in document["profiles"] if profile["id"] == "claude-code"
    )
    claude["evidence"] = ["docs/claude-code-adapter.md", "docs/does-not-exist.md"]

    resolved = resolve_host_capability_matrix(
        document, repository_root=ROOT, as_of=date(2026, 8, 12)
    )
    profile = next(item for item in resolved["profiles"] if item["id"] == "claude-code")

    assert profile["claimed_tier"] == "adapted"
    assert profile["effective_tier"] == "unknown"
    assert any("does-not-exist.md" in gap for gap in profile["evidence_gaps"])


def test_stale_verification_date_forces_unknown_effective_tier():
    document = _matrix()
    copilot = next(
        profile
        for profile in document["profiles"]
        if profile["id"] == "github-copilot"
    )
    max_age = document["evidence_max_age_days"]
    verified = date.fromisoformat(copilot["verification_date"])
    as_of = stale_after(verification_date=verified, max_age_days=max_age)

    resolved = resolve_host_capability_matrix(
        document, repository_root=ROOT, as_of=as_of
    )
    profile = next(
        item for item in resolved["profiles"] if item["id"] == "github-copilot"
    )

    assert profile["claimed_tier"] == "adapted"
    assert profile["effective_tier"] == "unknown"
    assert any("stale" in gap for gap in profile["evidence_gaps"])


def test_empty_profile_evidence_forces_unknown_even_when_claimed_tool_only():
    document = _matrix()
    codex = next(
        profile for profile in document["profiles"] if profile["id"] == "openai-codex"
    )
    codex["evidence"] = []

    resolved = resolve_host_capability_matrix(
        document, repository_root=ROOT, as_of=date(2026, 8, 12)
    )
    profile = next(
        item for item in resolved["profiles"] if item["id"] == "openai-codex"
    )

    assert profile["claimed_tier"] == "tool-only"
    assert profile["effective_tier"] == "unknown"
    assert "profile evidence list is empty" in profile["evidence_gaps"]


def test_unknown_remains_default_without_inventing_native_claims():
    document = _matrix()
    cursor = next(
        profile for profile in document["profiles"] if profile["id"] == "cursor"
    )
    assert cursor["claimed_tier"] == "unknown"
    assert cursor["verification_date"] is None
    assert cursor["evidence"] == []

    resolved = resolve_host_capability_matrix(
        document, repository_root=ROOT, as_of=date(2026, 8, 12)
    )
    assert effective_tiers(resolved)["cursor"] == "unknown"
    assert all(
        profile["effective_tier"] != "native" for profile in resolved["profiles"]
    )


def test_capability_rows_separate_documented_from_observed_and_name_fallbacks():
    resolved = load_host_capability_matrix(
        MATRIX_PATH, repository_root=ROOT, as_of=date(2026, 8, 12)
    )
    by_id = {profile["id"]: profile for profile in resolved["profiles"]}

    assert by_id["openai-codex"]["instruction_discovery"]["claim_basis"] == "documented"
    assert by_id["claude-code"]["instruction_discovery"]["claim_basis"] == "observed"
    assert by_id["github-copilot"]["hooks"]["portable_fallback"] == "git-hook"
    assert by_id["cursor"]["hooks"]["portable_fallback"] == "git-hook"
    assert resolved["portable_fallbacks"]["hooks"] == "git-hook"
    assert (
        resolved["portable_fallbacks"]["instruction_discovery"] == "generated-guidance"
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda doc: doc.__setitem__("contract", "io.murlocs.other"),
            "unsupported host capability contract",
        ),
        (
            lambda doc: doc.__setitem__("schema_version", 2),
            "unsupported host capability schema_version",
        ),
        (
            lambda doc: doc["profiles"].__setitem__(
                0,
                {
                    key: value
                    for key, value in doc["profiles"][0].items()
                    if key != "hooks"
                },
            ),
            "missing hooks",
        ),
        (
            lambda doc: doc.__setitem__(
                "profiles",
                [profile for profile in doc["profiles"] if profile["id"] != "cursor"],
            ),
            "omits required profiles",
        ),
    ],
)
def test_malformed_matrix_fails_visibly(mutation, message):
    document = _matrix()
    mutation(document)

    with pytest.raises(HostCapabilityError, match=message):
        resolve_host_capability_matrix(
            document, repository_root=ROOT, as_of=date(2026, 8, 12)
        )


def test_duplicate_profile_ids_are_rejected():
    document = _matrix()
    document["profiles"].append(copy.deepcopy(document["profiles"][0]))

    with pytest.raises(HostCapabilityError, match="duplicate host capability profile"):
        resolve_host_capability_matrix(
            document, repository_root=ROOT, as_of=date(2026, 8, 12)
        )


def test_absolute_or_parent_evidence_paths_are_rejected():
    document = _matrix()
    document["profiles"][0]["evidence"] = ["../secrets.txt"]

    with pytest.raises(HostCapabilityError, match="repository-relative"):
        resolve_host_capability_matrix(
            document, repository_root=ROOT, as_of=date(2026, 8, 12)
        )
