from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from murlocs.errors import MurlocsError
from murlocs.friction import (
    FRICTION_CONTRACT,
    FRICTION_SCHEMA_VERSION,
    RECORD_KIND,
    analyze_observations,
    load_observation,
    observation_payload,
    parse_observation_data,
    parse_observation_json,
    render_observation_toml,
    validate_observation_paths,
)

pytestmark = pytest.mark.issue(132)

FIXTURE_ROOT = Path(__file__).parent / "fixtures/guidance-friction/v1"
VALID = FIXTURE_ROOT / "valid"
REJECTED = FIXTURE_ROOT / "rejected"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", sorted(VALID.glob("*.json")), ids=lambda p: p.stem)
def test_valid_fixtures_parse_as_inert_observations(path: Path):
    record = parse_observation_json(path.read_text(encoding="utf-8"), filename=path.name)
    assert record.schema_version == FRICTION_SCHEMA_VERSION
    assert record.record_kind == RECORD_KIND
    assert record.contract == FRICTION_CONTRACT
    payload = observation_payload(record)
    assert payload["record_kind"] == "observation"
    rendered = render_observation_toml(record)
    assert "prompt" not in rendered
    assert "reasoning" not in rendered
    reparsed = parse_observation_data(tomllib.loads(rendered), filename=f"{path.stem}.toml")
    assert observation_payload(reparsed) == payload


@pytest.mark.parametrize(
    ("filename", "needle"),
    [
        ("unsupported-version.json", "unsupported friction_schema_version"),
        ("unknown-field.json", "unsupported fields"),
        ("absolute-path.json", "repository-relative"),
        ("path-traversal.json", "repository-relative"),
        ("wrong-record-kind.json", "observation"),
        ("forbidden-prompt.json", "must not capture"),
        ("unsafe-guidance-ref.json", "repository-relative"),
        ("curation-fields-smuggled.json", "unsupported fields"),
    ],
)
def test_rejected_fixtures_fail_visibly(filename: str, needle: str):
    raw = (REJECTED / filename).read_text(encoding="utf-8")
    with pytest.raises(MurlocsError, match=needle):
        parse_observation_json(raw, filename=filename)


def test_symlink_and_absolute_references_rejected(tmp_path: Path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / ".murlocs").mkdir()
    target = root / "src" / "app.py"
    link = root / "src" / "linked.py"
    link.symlink_to(target)

    data = _load_json(VALID / "missing-path-rule.json")
    data["id"] = "symlink-path"
    data["path"] = "src/linked.py"
    data["guidance_refs"] = []
    data["evidence"] = [
        {
            "kind": "issue",
            "reference": "issue-132",
            "summary": "Symlink rejection coverage.",
        }
    ]
    record = parse_observation_data(data, filename="symlink.json")
    with pytest.raises(MurlocsError, match="symlink"):
        validate_observation_paths(root, record)

    data["path"] = "src/app.py"
    data["evidence"] = [
        {
            "kind": "file_anchor",
            "reference": "src/linked.py#top",
            "summary": "Symlink file anchor.",
        }
    ]
    record = parse_observation_data(data, filename="symlink-evidence.json")
    with pytest.raises(MurlocsError, match="symlink"):
        validate_observation_paths(root, record)


def test_load_observation_from_toml_storage(tmp_path: Path):
    root = tmp_path / "repo"
    storage = root / ".murlocs" / "friction"
    storage.mkdir(parents=True)
    (root / "src" / "murlocs").mkdir(parents=True)
    (root / "src" / "murlocs" / "paths.py").write_text("def repo_path():\n    pass\n")
    (root / ".murlocs" / "layers").mkdir(parents=True, exist_ok=True)
    (root / ".murlocs" / "layers" / "core.toml").write_text("owners = []\n")

    record = parse_observation_json((VALID / "missing-path-rule.json").read_text())
    path = storage / f"{record.id}.toml"
    path.write_text(render_observation_toml(record), encoding="utf-8")
    loaded = load_observation(path, expected_id=record.id)
    assert loaded.id == record.id
    assert loaded.signal == "missing"


def test_analysis_covers_duplication_scope_stability_evidence_and_cost(tmp_path: Path):
    root = tmp_path / "repo"
    (root / "src" / "murlocs").mkdir(parents=True)
    (root / "src" / "murlocs" / "paths.py").write_text("x = 1\n")
    (root / ".murlocs" / "layers").mkdir(parents=True)
    (root / ".murlocs" / "layers" / "core.toml").write_text("owners = []\n")
    (root / "AGENTS.md").write_text("# agents\n")
    (root / ".murlocs" / "manifest.toml").write_text("schema_version = 1\n")

    first = parse_observation_json((VALID / "missing-path-rule.json").read_text())
    duplicate_data = _load_json(VALID / "missing-path-rule.json")
    duplicate_data["id"] = "core-missing-path-rule-dup"
    duplicate = parse_observation_data(duplicate_data, filename="dup.json")
    note_only = parse_observation_data(
        {
            "friction_schema_version": 1,
            "record_kind": "observation",
            "id": "note-only-missing",
            "signal": "missing",
            "path": "src/murlocs/paths.py",
            "scope": "unknown-scope",
            "summary": "Note-only missing signal.",
            "evidence": [
                {"kind": "note", "reference": "memory", "summary": "No durable evidence."}
            ],
            "observed_cost": {
                "metric": "active_context_bytes",
                "value": 24576,
                "bound": 24576,
            },
            "provenance": {
                "observer": "@fixture",
                "origin": "fixture",
                "observed_at": "2026-08-12T00:00:00Z",
            },
            "proposed_resolution": {
                "summary": "x" * 200,
                "intent_hint": "add",
            },
        },
        filename="note-only.json",
    )
    report = analyze_observations(
        (first, duplicate, note_only),
        root=root,
        known_scopes={"root", "core"},
    )
    assert report["inert"] is True
    assert report["applies_guidance"] is False
    assert any(item["code"] == "duplicate_observation" for item in report["duplication"])
    assert any(item["scope"] == "core" for item in report["scope"])
    assert any(
        finding["code"] == "scope_unknown"
        for item in report["scope"]
        for finding in item["findings"]
    )
    assert any(
        item["code"] in {"evidence_gap_note_only", "evidence_gap_missing_signal"}
        for item in report["evidence_gap"]
    )
    assert any(
        item["observation_id"] == "note-only-missing" and item["projected_delta_bytes"] == 200
        for item in report["projected_context_cost"]
    )
    assert any(
        finding["code"] == "projected_context_over_bound"
        for item in report["projected_context_cost"]
        for finding in item["findings"]
    )


def test_observation_is_not_a_curation_decision_surface():
    record = parse_observation_json((VALID / "missing-path-rule.json").read_text())
    payload = observation_payload(record)
    for forbidden in (
        "intent",
        "events",
        "required_owners",
        "accepted",
        "promoted",
        "decision",
        "authenticated",
    ):
        assert forbidden not in payload
    assert payload["record_kind"] == "observation"
