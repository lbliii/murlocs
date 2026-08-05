from __future__ import annotations

import json
from pathlib import Path

from milo import generate_llms_txt
from milo.testing import MCPClient

from murlocs.cli import build_cli
from murlocs.curation import load_record, stable_list_key


def invoke(*argv: str):
    return build_cli().invoke(list(argv))


def initialize(root: Path) -> None:
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    result = invoke("init", "--repo", str(root), "--name", "Curation Test")
    assert result.exit_code == 0, result.stderr


def proposal_args(root: Path, proposal_id: str = "add-review-rule") -> list[str]:
    return [
        "curate",
        "propose",
        proposal_id,
        "--intent",
        "add",
        "--subject-kind",
        "operating_rule",
        "--target-source",
        ".murlocs/manifest.toml",
        "--target-scope",
        "root",
        "--origin",
        "issue-25",
        "--rationale",
        "Make review behavior explicit.",
        "--proposer",
        "@contributor",
        "--evidence-kind",
        "issue",
        "--evidence-reference",
        "issue-25",
        "--evidence-summary",
        "The accepted RFC requires deterministic review.",
        "--at",
        "2026-08-03T14:00:00Z",
        "--value",
        "Review inert guidance proposals before changing active sources.",
        "--repo",
        str(root),
    ]


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_propose_dry_run_and_apply_only_create_inert_record(tmp_path):
    root = tmp_path / "repo"
    initialize(root)
    before = snapshot(root)

    preview = invoke("--dry-run", *proposal_args(root), "--format", "json")
    assert preview.exit_code == 0, preview.stderr
    preview_payload = json.loads(preview.output)
    assert preview_payload["dry_run"] is True
    assert preview_payload["review"]["change"] == {
        "operation": "add",
        "before": None,
        "after": "Review inert guidance proposals before changing active sources.",
    }
    assert snapshot(root) == before

    applied = invoke(*proposal_args(root))
    assert applied.exit_code == 0, applied.stderr
    after = snapshot(root)
    assert set(after) - set(before) == {".murlocs/curation/add-review-rule.toml"}
    for path, content in before.items():
        assert after[path] == content


def test_structured_payload_with_unicode_key_round_trips_from_storage(tmp_path):
    root = tmp_path / "repo"
    initialize(root)
    proposal = proposal_args(root, "unicode-ownership")
    proposal[proposal.index("operating_rule")] = "scope"
    value_index = proposal.index("--value")
    del proposal[value_index : value_index + 2]
    proposal.extend(
        [
            "--target-key",
            "unicode-scope",
            "--payload-json",
            json.dumps(
                {
                    "id": "unicode-scope",
                    "path": "unicode",
                    "map": "unicode/AGENTS.md",
                    "point_of_view": "Unicode ownership category.",
                    "owns": {"équipe": ["unicode"]},
                }
            ),
        ]
    )
    assert invoke(*proposal).exit_code == 0

    path = root / ".murlocs" / "curation" / "unicode-ownership.toml"
    loaded = load_record(path, expected_id="unicode-ownership")
    assert loaded.payload is not None
    assert loaded.payload["owns"] == {"équipe": ["unicode"]}
    assert '"équipe" = ["unicode"]' in path.read_text(encoding="utf-8")


def test_review_is_deterministic_read_only_and_has_human_json_mcp_parity(tmp_path):
    root = tmp_path / "repo"
    initialize(root)
    assert invoke(*proposal_args(root)).exit_code == 0
    before = snapshot(root)

    first = invoke("curate", "review", "add-review-rule", "--repo", str(root), "--format", "json")
    second = invoke("curate", "review", "add-review-rule", "--repo", str(root), "--format", "json")
    human = invoke("curate", "review", "add-review-rule", "--repo", str(root))
    structured = (
        MCPClient(build_cli())
        .call("curate.review", id="add-review-rule", repo=str(root))
        .structured
    )
    programmatic = build_cli().call("curate.review", id="add-review-rule", repo=str(root))
    tool = next(
        item for item in MCPClient(build_cli()).list_tools() if item.name == "curate.review"
    )

    assert first.exit_code == second.exit_code == human.exit_code == 0
    assert json.loads(first.output) == json.loads(second.output) == programmatic == structured
    assert structured["required_scopes"] == {
        "recorded": ["root"],
        "current": ["root"],
    }
    success_schema = tool.output_schema["anyOf"][0]
    assert "required_scopes" in success_schema["required"]
    assert success_schema["properties"]["required_scopes"]["required"] == [
        "current",
        "recorded",
    ]
    assert "intent: add operating_rule" in human.output
    assert "Before:\n  (none)" in human.output
    assert "After:" in human.output
    assert "recorded source:" in human.output
    assert "current source:" in human.output
    assert "proposed source:" in human.output
    assert "Decisions:\n  - proposed by @contributor" in human.output
    assert "Affected guidance chains:" in human.output
    assert "current required scopes: root" in human.output
    assert "recorded required scopes: root" in human.output
    assert "Exact duplicates:\n  - none" in human.output
    assert snapshot(root) == before


def test_review_reports_replace_remove_deltas_and_stale_base(tmp_path):
    root = tmp_path / "repo"
    initialize(root)
    manifest = root / ".murlocs" / "manifest.toml"
    current = "Read the applicable AGENTS.md chain before editing."

    replace = proposal_args(root, "replace-rule")
    replace[replace.index("add")] = "replace"
    replace.extend(["--target-key", stable_list_key(current)])
    replace[replace.index("Review inert guidance proposals before changing active sources.")] = (
        "Read scoped guidance before editing."
    )
    assert invoke(*replace).exit_code == 0
    replacement = json.loads(
        invoke("curate", "review", "replace-rule", "--repo", str(root), "--format", "json").output
    )
    assert replacement["change"]["before"] == current
    assert replacement["change"]["after"] == "Read scoped guidance before editing."
    assert replacement["affected_chains"][0]["delta_bytes"] < 0

    remove = proposal_args(root, "remove-rule")
    remove[remove.index("add")] = "remove"
    value_index = remove.index("--value")
    del remove[value_index : value_index + 2]
    remove.extend(["--target-key", stable_list_key(current)])
    assert invoke(*remove).exit_code == 0
    removal = json.loads(
        invoke("curate", "review", "remove-rule", "--repo", str(root), "--format", "json").output
    )
    assert removal["change"] == {"operation": "remove", "before": current, "after": None}
    assert removal["affected_chains"][0]["delta_bytes"] < 0

    manifest.write_text(manifest.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    stale = invoke("curate", "review", "replace-rule", "--repo", str(root), "--format", "json")
    stale_payload = json.loads(stale.output)
    assert stale_payload["ok"] is False
    assert stale_payload["source"]["stale_base"] is True
    assert any(item["code"] == "stale_base" for item in stale_payload["findings"])


def test_removing_scope_reports_removed_map_and_negative_chain_delta(tmp_path):
    root = tmp_path / "repo"
    initialize(root)
    (root / "docs").mkdir()
    (root / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
    assert invoke("add-scope", "docs", "--repo", str(root)).exit_code == 0

    remove = proposal_args(root, "remove-docs-scope")
    remove[remove.index("add")] = "remove"
    remove[remove.index("operating_rule")] = "scope"
    remove[remove.index(".murlocs/manifest.toml")] = ".murlocs/layers/docs.toml"
    remove[remove.index("root")] = "docs"
    value_index = remove.index("--value")
    del remove[value_index : value_index + 2]
    remove.extend(["--target-key", "docs"])
    assert invoke(*remove).exit_code == 0

    report = json.loads(
        invoke(
            "curate",
            "review",
            "remove-docs-scope",
            "--repo",
            str(root),
            "--format",
            "json",
        ).output
    )
    chain = next(item for item in report["affected_chains"] if item["scope"] == "docs")
    assert chain["path"] == "docs"
    assert chain["maps"] == ["AGENTS.md", "docs/AGENTS.md"]
    assert chain["proposed_bytes"] < chain["current_bytes"]
    assert chain["delta_bytes"] < 0


def test_scope_replacement_cannot_change_path_or_map_identity(tmp_path):
    root = tmp_path / "repo"
    initialize(root)
    (root / "docs").mkdir()
    (root / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
    assert invoke("add-scope", "docs", "--repo", str(root)).exit_code == 0

    replace = proposal_args(root, "move-docs-scope")
    replace[replace.index("add")] = "replace"
    replace[replace.index("operating_rule")] = "scope"
    replace[replace.index(".murlocs/manifest.toml")] = ".murlocs/layers/docs.toml"
    replace[replace.index("root")] = "docs"
    value_index = replace.index("--value")
    del replace[value_index : value_index + 2]
    replace.extend(
        [
            "--target-key",
            "docs",
            "--payload-json",
            json.dumps(
                {
                    "id": "docs",
                    "path": "moved-docs",
                    "map": "moved-docs/AGENTS.md",
                    "point_of_view": "Attempted move.",
                }
            ),
        ]
    )
    assert invoke(*replace).exit_code == 0

    report = json.loads(
        invoke(
            "curate",
            "review",
            "move-docs-scope",
            "--repo",
            str(root),
            "--format",
            "json",
        ).output
    )
    assert report["ok"] is False
    finding = next(
        item for item in report["findings"] if item["code"] == "immutable_scope_identity"
    )
    assert finding["blocking"] is True
    assert "path 'docs' -> 'moved-docs'" in finding["message"]
    assert "map 'docs/AGENTS.md' -> 'moved-docs/AGENTS.md'" in finding["message"]
    assert report["change"]["before"]["path"] == "docs"
    assert report["change"]["after"]["path"] == "moved-docs"
    assert report["source"]["proposed_sha256"] == report["source"]["current_sha256"]
    assert report["affected_chains"] == []


def test_exact_duplicates_key_collisions_shadowing_and_budget_are_explicit(tmp_path):
    root = tmp_path / "repo"
    initialize(root)
    duplicate = proposal_args(root, "duplicate-rule")
    duplicate[
        duplicate.index("Review inert guidance proposals before changing active sources.")
    ] = "Read the applicable AGENTS.md chain before editing."
    assert invoke(*duplicate).exit_code == 0
    report = json.loads(
        invoke("curate", "review", "duplicate-rule", "--repo", str(root), "--format", "json").output
    )
    assert report["exact_duplicates"]
    assert report["shadowing"]

    scope = proposal_args(root, "duplicate-scope")
    scope[scope.index("operating_rule")] = "scope"
    scope.extend(["--target-key", "root"])
    value_index = scope.index("--value")
    del scope[value_index : value_index + 2]
    scope.extend(
        [
            "--payload-json",
            json.dumps(
                {
                    "id": "root",
                    "path": ".",
                    "map": "AGENTS.md",
                    "point_of_view": "Replacement disguised as an addition.",
                }
            ),
        ]
    )
    assert invoke(*scope).exit_code == 0
    collision = json.loads(
        invoke(
            "curate", "review", "duplicate-scope", "--repo", str(root), "--format", "json"
        ).output
    )
    assert collision["key_collisions"]

    large = proposal_args(root, "over-budget")
    large[large.index("Review inert guidance proposals before changing active sources.")] = (
        "x" * 25000
    )
    assert invoke(*large).exit_code == 0
    budget = json.loads(
        invoke("curate", "review", "over-budget", "--repo", str(root), "--format", "json").output
    )
    assert any(item["code"] == "budget" for item in budget["validation_findings"])
    assert any(chain["over_budget"] for chain in budget["affected_chains"])


def test_review_reports_when_a_later_duplicate_would_become_active(tmp_path):
    root = tmp_path / "repo"
    initialize(root)
    current = "Read the applicable AGENTS.md chain before editing."
    layers = root / ".murlocs" / "layers"
    layers.mkdir()
    (layers / "later.toml").write_text(
        f"operating_rules = [{json.dumps(current)}]\n", encoding="utf-8"
    )
    manifest = root / ".murlocs" / "manifest.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + '\n[[layers]]\nid = "later"\nkind = "domain"\n'
        + 'path = ".murlocs/layers/later.toml"\n',
        encoding="utf-8",
    )
    assert invoke("compile", "--repo", str(root)).exit_code == 0

    remove = proposal_args(root, "activate-later")
    remove[remove.index("add")] = "remove"
    value_index = remove.index("--value")
    del remove[value_index : value_index + 2]
    remove.extend(["--target-key", stable_list_key(current)])
    assert invoke(*remove).exit_code == 0
    report = json.loads(
        invoke("curate", "review", "activate-later", "--repo", str(root), "--format", "json").output
    )
    assert any("newly active" in item["message"] for item in report["shadowing"])
    assert report["affected_chains"][0]["delta_bytes"] == 0


def test_malformed_unknown_and_duplicate_records_are_actionable(tmp_path):
    root = tmp_path / "repo"
    initialize(root)
    assert invoke(*proposal_args(root, "first")).exit_code == 0
    curation = root / ".murlocs" / "curation"
    first = (curation / "first.toml").read_text(encoding="utf-8")
    (curation / "duplicate.toml").write_text(first, encoding="utf-8")
    (curation / "unknown.toml").write_text(
        first.replace('id = "first"', 'id = "unknown"').replace(
            'origin = "issue-25"', 'origin = "issue-25"\nmystery = true'
        ),
        encoding="utf-8",
    )
    (curation / "bad-state.toml").write_text(
        first.replace('id = "first"', 'id = "bad-state"').replace(
            'state = "proposed"', 'state = "promoted"'
        ),
        encoding="utf-8",
    )
    checked = invoke("curate", "check", "--repo", str(root), "--format", "json")
    assert checked.exit_code == 1
    payload = json.loads(checked.output)
    assert payload["ok"] is False
    findings = payload["findings"]
    assert any(item["code"] == "duplicate_id" for item in findings)
    assert any("unsupported fields: mystery" in item["message"] for item in findings)
    assert any("must be proposed" in item["message"] for item in findings)


def test_structured_missing_proposal_preserves_operational_error_exit(tmp_path):
    root = tmp_path / "repo"
    initialize(root)

    result = invoke("curate", "review", "missing", "--repo", str(root), "--format", "json")

    assert result.exit_code == 1
    assert result.stderr == ""
    assert json.loads(result.output) == {
        "error": {
            "code": "MURLOCS_CURATE_REVIEW",
            "message": "curation record not found: missing.toml",
        },
        "ok": False,
    }


def test_curation_records_are_inert_even_when_malformed_and_layer_shaped(tmp_path):
    root = tmp_path / "repo"
    initialize(root)
    before_map = (root / "AGENTS.md").read_bytes()
    before_lock = (root / ".murlocs" / "lock.json").read_bytes()
    curation = root / ".murlocs" / "curation"
    curation.mkdir()
    (curation / "candidate.toml").write_text(
        'operating_rules = ["THIS MUST NEVER COMPILE"]\n'
        '[[scopes]]\nid = "injected"\npath = "src"\nmap = "src/AGENTS.md"\n',
        encoding="utf-8",
    )

    assert invoke("compile", "--repo", str(root)).exit_code == 0
    assert invoke("check", "--repo", str(root)).exit_code == 0
    assert (root / "AGENTS.md").read_bytes() == before_map
    assert (root / ".murlocs" / "lock.json").read_bytes() == before_lock
    assert not (root / "src" / "AGENTS.md").exists()
    assert invoke("curate", "check", "--repo", str(root)).exit_code == 1


def test_path_escapes_unrelated_targets_and_existing_replacement_are_refused(tmp_path):
    root = tmp_path / "repo"
    initialize(root)
    outside = tmp_path / "escape.toml"

    escaped = proposal_args(root, "../escape")
    result = invoke(*escaped)
    assert result.exit_code == 1
    assert "path-safe" in result.stderr
    assert not outside.exists()

    unrelated = proposal_args(root, "unrelated")
    unrelated[unrelated.index(".murlocs/manifest.toml")] = "README.md"
    result = invoke(*unrelated)
    assert result.exit_code == 1
    assert "active manifest or layer source" in result.stderr

    assert invoke(*proposal_args(root, "existing")).exit_code == 0
    before = (root / ".murlocs" / "curation" / "existing.toml").read_bytes()
    result = invoke(*proposal_args(root, "existing"))
    assert result.exit_code == 1
    assert "refusing to replace" in result.stderr
    assert (root / ".murlocs" / "curation" / "existing.toml").read_bytes() == before


def test_current_owners_are_recomputed_and_propose_is_not_agent_visible(tmp_path):
    root = tmp_path / "repo"
    initialize(root)
    manifest = root / ".murlocs" / "manifest.toml"
    layers = root / ".murlocs" / "layers"
    layers.mkdir()
    (layers / "owned.toml").write_text('pillars = ["Owned layer."]\n', encoding="utf-8")
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        .replace(
            "max_active_bytes = 24576",
            'max_active_bytes = 24576\nowners = ["@platform"]',
        )
        .replace(
            "require_scope_invariants = false",
            "require_scope_invariants = false\nvalidate_codeowners = true",
        )
        + '\n[[layers]]\nid = "owned"\nkind = "domain"\n'
        + 'path = ".murlocs/layers/owned.toml"\nowners = ["@domain"]\n',
        encoding="utf-8",
    )
    codeowners = root / ".github" / "CODEOWNERS"
    codeowners.parent.mkdir()
    codeowners.write_text(
        "/.murlocs/manifest.toml @platform\n/.murlocs/layers/owned.toml @domain\n",
        encoding="utf-8",
    )
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    assert invoke(*proposal_args(root, "owned")).exit_code == 0
    assert "@platform" in (root / ".murlocs" / "curation" / "owned.toml").read_text(
        encoding="utf-8"
    )
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("@platform", "@maintainers"),
        encoding="utf-8",
    )
    codeowners.write_text(
        codeowners.read_text(encoding="utf-8").replace("@platform", "@maintainers"),
        encoding="utf-8",
    )
    report = json.loads(
        invoke("curate", "review", "owned", "--repo", str(root), "--format", "json").output
    )
    assert report["owners"] == {
        "recorded": ["@domain", "@platform"],
        "current": ["@domain", "@maintainers"],
    }
    assert any(item["code"] == "owners_changed" for item in report["findings"])

    tools = {tool.name for tool in MCPClient(build_cli()).list_tools()}
    llms = generate_llms_txt(build_cli())
    assert "curate.review" in tools
    assert "curate.check" in tools
    assert "curate.propose" not in tools
    assert "curate.review" in llms
    assert "curate.propose" not in llms
