from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from murlocs.cli import build_cli
from murlocs.curation import apply_record, load_record, stable_list_key
from murlocs.curation_transaction import FileUpdate, apply_transaction, plan_recovery
from murlocs.errors import MurlocsError
from murlocs.lockfile import sha256_bytes
from murlocs.serialization import render_manifest_data


def invoke(*argv: str):
    return build_cli().invoke(list(argv))


def initialize(root: Path) -> None:
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    assert invoke("init", "--repo", str(root), "--name", "Lifecycle Test").exit_code == 0
    manifest = root / ".murlocs" / "manifest.toml"
    text = manifest.read_text(encoding="utf-8")
    manifest.write_text(
        text.replace(
            "max_active_bytes = 24576\n",
            'max_active_bytes = 24576\nowners = ["@owners"]\n',
        ),
        encoding="utf-8",
    )
    assert invoke("compile", "--repo", str(root)).exit_code == 0


def propose(root: Path, proposal_id: str, *, intent: str = "add", value: str = "Curated rule."):
    args = [
        "curate",
        "propose",
        proposal_id,
        "--intent",
        intent,
        "--subject-kind",
        "operating_rule",
        "--target-source",
        ".murlocs/manifest.toml",
        "--target-scope",
        "root",
        "--origin",
        "issue-26",
        "--rationale",
        "Exercise governed curation.",
        "--proposer",
        "@author",
        "--evidence-kind",
        "issue",
        "--evidence-reference",
        "issue-26",
        "--evidence-summary",
        "Owner-governed lifecycle acceptance.",
        "--at",
        "2026-08-03T14:00:00Z",
        "--repo",
        str(root),
    ]
    if intent == "remove":
        args.extend(
            [
                "--target-key",
                stable_list_key("Read the applicable AGENTS.md chain before editing."),
            ]
        )
    elif intent == "replace":
        args.extend(["--target-key", stable_list_key("Curated rule."), "--value", value])
    else:
        args.extend(["--value", value])
    result = invoke(*args)
    assert result.exit_code == 0, result.stderr


def propose_check(root: Path, proposal_id: str, key: str, *, intent: str) -> None:
    payload = {
        "invoke": "pytest",
        "location": "src/app.py",
        "proof_contains": "VALUE = 1",
    }
    result = invoke(
        "curate",
        "propose",
        proposal_id,
        "--intent",
        intent,
        "--subject-kind",
        "check",
        "--target-source",
        ".murlocs/manifest.toml",
        "--target-scope",
        "root",
        "--target-key",
        key,
        "--origin",
        "issue-26",
        "--rationale",
        "Exercise exact supersession identity.",
        "--proposer",
        "@author",
        "--evidence-kind",
        "issue",
        "--evidence-reference",
        "issue-26",
        "--evidence-summary",
        "Equal payloads must not collapse structured identity.",
        "--at",
        "2026-08-03T14:00:00Z",
        "--payload-json",
        json.dumps(payload),
        "--repo",
        str(root),
    )
    assert result.exit_code == 0, result.stderr


def action(root: Path, name: str, proposal_id: str, *extra: str, dry_run: bool = False):
    prefix = ["--dry-run"] if dry_run else []
    return invoke(
        *prefix,
        "curate",
        name,
        proposal_id,
        *extra,
        "--actor",
        "@owners",
        "--at",
        "2026-08-03T15:00:00Z",
        "--rationale",
        f"{name} after owner review.",
        "--review-ref",
        "pull-request-26",
        "--repo",
        str(root),
        "--format",
        "json",
    )


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_accept_dry_run_then_promote_changes_only_source_and_record(tmp_path):
    root = tmp_path / "repo"
    initialize(root)
    propose(root, "add-rule")
    before_accept = snapshot(root)

    preview = action(root, "accept", "add-rule", dry_run=True)
    assert preview.exit_code == 0, preview.stderr
    payload = json.loads(preview.output)
    assert payload["identity_assurance"] == "not_authenticated"
    assert [item["path"] for item in payload["patches"]] == [
        ".murlocs/curation/add-rule.toml"
    ]
    assert snapshot(root) == before_accept

    accepted = action(root, "accept", "add-rule")
    assert accepted.exit_code == 0, accepted.stderr
    after_accept = snapshot(root)
    changed = {path for path in after_accept if after_accept[path] != before_accept[path]}
    assert changed == {".murlocs/curation/add-rule.toml"}

    before_promote = snapshot(root)
    promotion_preview = action(root, "promote", "add-rule", dry_run=True)
    assert promotion_preview.exit_code == 0, promotion_preview.stderr
    assert snapshot(root) == before_promote
    promoted = action(root, "promote", "add-rule")
    assert promoted.exit_code == 0, promoted.stderr
    payload = json.loads(promoted.output)
    assert payload["patches"] == json.loads(promotion_preview.output)["patches"]
    assert [item["path"] for item in payload["patches"]] == [
        ".murlocs/manifest.toml",
        ".murlocs/curation/add-rule.toml",
    ]
    after_promote = snapshot(root)
    changed = {path for path in after_promote if after_promote[path] != before_promote[path]}
    assert changed == {".murlocs/manifest.toml", ".murlocs/curation/add-rule.toml"}
    assert after_promote["AGENTS.md"] == before_promote["AGENTS.md"]
    assert after_promote[".murlocs/lock.json"] == before_promote[".murlocs/lock.json"]
    record = load_record(root / ".murlocs/curation/add-rule.toml")
    assert record.state == "promoted"
    assert record.events[-1].before_sha256
    assert record.events[-1].source_after_sha256
    assert invoke("curate", "check", "--repo", str(root)).exit_code == 0


def test_current_owner_and_replay_rules_block_before_writes(tmp_path):
    root = tmp_path / "repo"
    initialize(root)
    propose(root, "owner-rule")
    before = snapshot(root)
    denied = invoke(
        "curate",
        "accept",
        "owner-rule",
        "--actor",
        "@stranger",
        "--at",
        "now",
        "--rationale",
        "No authority.",
        "--repo",
        str(root),
    )
    assert denied.exit_code == 1
    assert "not a current required owner" in denied.stderr
    assert snapshot(root) == before
    assert action(root, "accept", "owner-rule").exit_code == 0
    accepted = snapshot(root)
    replay = action(root, "accept", "owner-rule")
    assert json.loads(replay.output)["ok"] is False
    assert snapshot(root) == accepted


def test_ownership_change_blocks_acceptance_before_record_write(tmp_path):
    root = tmp_path / "repo"
    initialize(root)
    propose(root, "ownership-drift")
    record = root / ".murlocs/curation/ownership-drift.toml"
    record_before = record.read_bytes()
    manifest = root / ".murlocs/manifest.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            'owners = ["@owners"]', 'owners = ["@new-owners"]'
        ),
        encoding="utf-8",
    )
    denied = invoke(
        "curate",
        "accept",
        "ownership-drift",
        "--actor",
        "@new-owners",
        "--at",
        "now",
        "--rationale",
        "Ownership moved.",
        "--repo",
        str(root),
    )
    assert denied.exit_code == 1
    assert "owners_changed" in denied.stderr
    assert record.read_bytes() == record_before


def test_reject_withdraw_and_prune_are_terminal_and_inert(tmp_path):
    root = tmp_path / "repo"
    initialize(root)
    propose(root, "rejected")
    assert action(root, "reject", "rejected").exit_code == 0
    propose(root, "withdrawn")
    wrong = action(root, "withdraw", "withdrawn")
    assert json.loads(wrong.output)["ok"] is False
    withdrawn = invoke(
        "curate",
        "withdraw",
        "withdrawn",
        "--actor",
        "@author",
        "--at",
        "now",
        "--rationale",
        "No longer wanted.",
        "--repo",
        str(root),
    )
    assert withdrawn.exit_code == 0, withdrawn.stderr

    propose(root, "remove-rule", intent="remove")
    assert action(root, "accept", "remove-rule").exit_code == 0
    assert action(root, "prune", "remove-rule").exit_code == 0
    assert "Read the applicable AGENTS.md chain before editing." not in (
        root / ".murlocs/manifest.toml"
    ).read_text(encoding="utf-8")
    assert invoke("curate", "check", "--repo", str(root)).exit_code == 0
    assert load_record(root / ".murlocs/curation/rejected.toml").state == "rejected"
    assert load_record(root / ".murlocs/curation/withdrawn.toml").state == "withdrawn"
    assert load_record(root / ".murlocs/curation/remove-rule.toml").state == "pruned"


def test_transaction_failure_rolls_back_and_crash_recovers(tmp_path):
    root = tmp_path / "repo"
    initialize(root)
    manifest = root / ".murlocs/manifest.toml"
    manifest.write_text(
        render_manifest_data(tomllib.loads(manifest.read_text(encoding="utf-8"))),
        encoding="utf-8",
    )
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    propose(root, "transaction-rule")
    assert action(root, "accept", "transaction-rule").exit_code == 0
    before = snapshot(root)

    def fail(phase: str) -> None:
        if phase == "after_write:0":
            raise RuntimeError("injected failure")

    with pytest.raises(RuntimeError, match="injected failure"):
        apply_record(
            root,
            "transaction-rule",
            operation="promote",
            actor="@owners",
            at="now",
            rationale="failure test",
            review_ref=None,
            dry_run=False,
            failure_hook=fail,
        )
    assert snapshot(root) == before

    class SimulatedCrash(BaseException):
        pass

    def crash(phase: str) -> None:
        if phase == "after_write:0":
            raise SimulatedCrash

    with pytest.raises(SimulatedCrash):
        apply_record(
            root,
            "transaction-rule",
            operation="promote",
            actor="@owners",
            at="now",
            rationale="crash test",
            review_ref=None,
            dry_run=False,
            failure_hook=crash,
        )
    transaction = root / ".murlocs/curation/.transaction"
    assert transaction.is_dir()
    attacker_image = b"attacker-selected rollback bytes\n"
    (transaction / "0.before").write_bytes(attacker_image)
    metadata_path = transaction / "transaction.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["updates"][0]["before_sha256"] = sha256_bytes(attacker_image)
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    mixed = snapshot(root)
    checked = invoke("curate", "check", "--repo", str(root), "--format", "json")
    checked_payload = json.loads(checked.output)
    assert checked_payload["ok"] is False
    assert any(
        item["code"] == "pending_transaction" for item in checked_payload["findings"]
    )
    ordinary = invoke("check", "--repo", str(root), "--format", "json")
    assert any(
        item["code"] == "curation_transaction"
        for item in json.loads(ordinary.output)["findings"]
    )
    compile_result = invoke("compile", "--repo", str(root))
    assert compile_result.exit_code == 1
    assert "must be recovered" in compile_result.stderr
    assert snapshot(root) == mixed
    blocked = action(root, "promote", "transaction-rule")
    assert json.loads(blocked.output)["ok"] is False
    assert snapshot(root) == mixed
    preview = invoke(
        "--dry-run",
        "curate",
        "recover",
        "transaction-rule",
        "--repo",
        str(root),
        "--format",
        "json",
    )
    preview_payload = json.loads(preview.output)
    assert preview_payload["status"] == (
        "roll back exact accepted addition reconstructed from lifecycle semantics"
    )
    assert [item["path"] for item in preview_payload["patches"]] == [
        ".murlocs/manifest.toml"
    ]
    assert "attacker-selected" not in preview_payload["patches"][0]["diff"]
    assert snapshot(root) == mixed
    recovered = invoke(
        "curate", "recover", "transaction-rule", "--repo", str(root)
    )
    assert recovered.exit_code == 0, recovered.stderr
    assert snapshot(root) == before
    applied = apply_record(
        root,
        "transaction-rule",
        operation="promote",
        actor="@owners",
        at="later",
        rationale="recover and apply",
        review_ref=None,
        dry_run=False,
    )
    assert applied["ok"] is True
    assert not (root / ".murlocs/curation/.transaction").exists()
    assert load_record(root / ".murlocs/curation/transaction-rule.toml").state == "promoted"


def test_modified_map_and_toctou_source_change_block_record_write(tmp_path):
    root = tmp_path / "repo"
    initialize(root)
    propose(root, "blocked-rule")
    assert action(root, "accept", "blocked-rule").exit_code == 0
    record_path = root / ".murlocs/curation/blocked-rule.toml"
    record_before = record_path.read_bytes()
    generated = root / "AGENTS.md"
    generated.write_text(generated.read_text(encoding="utf-8") + "\nmanual edit\n")
    denied = action(root, "promote", "blocked-rule")
    assert json.loads(denied.output)["ok"] is False
    assert "modified generated file" in json.loads(denied.output)["error"]["message"]
    assert record_path.read_bytes() == record_before

    assert invoke("compile", "--repo", str(root)).exit_code == 1
    # Restore only the deliberately modified generated map from the source model.
    generated.write_bytes(snapshot(root)["AGENTS.md"].replace(b"\nmanual edit\n", b""))

    source = root / ".murlocs/manifest.toml"

    def race(phase: str) -> None:
        if phase == "before_commit":
            source.write_text(source.read_text(encoding="utf-8") + "\n# raced\n")

    with pytest.raises(Exception, match="plan is stale"):
        apply_record(
            root,
            "blocked-rule",
            operation="promote",
            actor="@owners",
            at="now",
            rationale="race test",
            review_ref=None,
            dry_run=False,
            failure_hook=race,
        )
    assert record_path.read_bytes() == record_before
    assert "Curated rule." not in source.read_text(encoding="utf-8")
    assert not (root / ".murlocs/curation/.transaction").exists()

    generated_before = generated.read_bytes()

    def race_generated(phase: str) -> None:
        if phase == "before_commit":
            generated.write_text(
                generated.read_text(encoding="utf-8") + "\nraced map\n",
                encoding="utf-8",
            )

    source.write_text(source.read_text(encoding="utf-8").replace("\n# raced\n", ""))
    with pytest.raises(MurlocsError, match="preflight dependency changed"):
        apply_record(
            root,
            "blocked-rule",
            operation="promote",
            actor="@owners",
            at="now",
            rationale="generated race test",
            review_ref=None,
            dry_run=False,
            failure_hook=race_generated,
        )
    assert record_path.read_bytes() == record_before
    assert "Curated rule." not in source.read_text(encoding="utf-8")
    assert generated.read_bytes() != generated_before


def test_coverage_topology_and_codeowners_precedence_are_commit_guards(tmp_path):
    root = tmp_path / "repo"
    initialize(root)
    manifest = root / ".murlocs/manifest.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        .replace("roots = []", 'roots = ["src"]')
        .replace(
            "[coverage.exemptions]\n",
            '[coverage.exemptions]\n"src" = "Root source unit is explicitly exempt."\n',
        )
        .replace(
            "require_scope_invariants = false",
            "require_scope_invariants = false\nvalidate_codeowners = true",
        ),
        encoding="utf-8",
    )
    (root / "CODEOWNERS").write_text(
        "/.murlocs/manifest.toml @owners\n", encoding="utf-8"
    )
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    propose(root, "guarded-dependencies")
    assert action(root, "accept", "guarded-dependencies").exit_code == 0
    source_before = manifest.read_bytes()
    record = root / ".murlocs/curation/guarded-dependencies.toml"
    record_before = record.read_bytes()

    def add_source(phase: str) -> None:
        if phase == "before_commit":
            unit = root / "src/new-unit"
            unit.mkdir()
            (unit / "new.py").write_text("VALUE = 2\n", encoding="utf-8")

    with pytest.raises(MurlocsError, match="coverage topology changed"):
        apply_record(
            root,
            "guarded-dependencies",
            operation="promote",
            actor="@owners",
            at="now",
            rationale="coverage race",
            review_ref=None,
            dry_run=False,
            failure_hook=add_source,
        )
    assert manifest.read_bytes() == source_before
    assert record.read_bytes() == record_before
    (root / "src/new-unit/new.py").unlink()
    (root / "src/new-unit").rmdir()

    def add_preferred_codeowners(phase: str) -> None:
        if phase == "before_commit":
            directory = root / ".github"
            directory.mkdir(exist_ok=True)
            (directory / "CODEOWNERS").write_text(
                "/.murlocs/manifest.toml @attacker\n", encoding="utf-8"
            )

    with pytest.raises(MurlocsError, match="preflight dependency changed"):
        apply_record(
            root,
            "guarded-dependencies",
            operation="promote",
            actor="@owners",
            at="now",
            rationale="owner discovery race",
            review_ref=None,
            dry_run=False,
            failure_hook=add_preferred_codeowners,
        )
    assert manifest.read_bytes() == source_before
    assert record.read_bytes() == record_before


def test_supersede_links_both_terminal_records(tmp_path):
    root = tmp_path / "repo"
    initialize(root)
    propose(root, "original")
    assert action(root, "accept", "original").exit_code == 0
    assert action(root, "promote", "original").exit_code == 0
    propose(root, "replacement", intent="replace", value="Replacement rule.")
    assert action(root, "accept", "replacement").exit_code == 0
    result = action(root, "supersede", "original", "--with", "replacement")
    assert result.exit_code == 0, result.stderr
    old = load_record(root / ".murlocs/curation/original.toml")
    new = load_record(root / ".murlocs/curation/replacement.toml")
    assert old.state == "superseded"
    assert new.state == "promoted"
    assert old.events[-1].related_proposal_id == "replacement"
    assert new.events[-1].related_proposal_id == "original"
    source = (root / ".murlocs/manifest.toml").read_text(encoding="utf-8")
    assert "Replacement rule." in source
    assert "Curated rule." not in source


def test_supersede_rejects_equal_payloads_on_different_structured_keys(tmp_path):
    root = tmp_path / "repo"
    initialize(root)
    propose_check(root, "check-a-add", "check-a", intent="add")
    assert action(root, "accept", "check-a-add").exit_code == 0
    assert action(root, "promote", "check-a-add").exit_code == 0
    propose_check(root, "check-b-add", "check-b", intent="add")
    assert action(root, "accept", "check-b-add").exit_code == 0
    assert action(root, "promote", "check-b-add").exit_code == 0
    propose_check(root, "check-b-replace", "check-b", intent="replace")
    assert action(root, "accept", "check-b-replace").exit_code == 0
    before = snapshot(root)
    denied = action(
        root,
        "supersede",
        "check-a-add",
        "--with",
        "check-b-replace",
    )
    payload = json.loads(denied.output)
    assert payload["ok"] is False
    assert "same exact structured key" in payload["error"]["message"]
    assert snapshot(root) == before


def test_terminal_review_reports_current_truth_and_rejects_intent_state_tampering(
    tmp_path,
):
    root = tmp_path / "repo"
    initialize(root)
    propose(root, "terminal-truth")
    assert action(root, "accept", "terminal-truth").exit_code == 0
    assert action(root, "promote", "terminal-truth").exit_code == 0
    manifest = root / ".murlocs/manifest.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            'owners = ["@owners"]', 'owners = ["@new-owners"]'
        )
        + "\n# later source revision\n",
        encoding="utf-8",
    )
    reviewed = invoke(
        "curate",
        "review",
        "terminal-truth",
        "--repo",
        str(root),
        "--format",
        "json",
    )
    payload = json.loads(reviewed.output)
    assert payload["ok"] is True
    assert payload["owners"]["current"] == ["@new-owners"]
    assert payload["source"]["active"] is True
    assert payload["source"]["stale_base"] is True
    assert payload["source"]["current_sha256"] == sha256_bytes(manifest.read_bytes())

    record = root / ".murlocs/curation/terminal-truth.toml"
    record.write_text(
        record.read_text(encoding="utf-8").replace(
            'state = "promoted"', 'state = "pruned"'
        ),
        encoding="utf-8",
    )
    checked = invoke("curate", "check", "--repo", str(root), "--format", "json")
    checked_payload = json.loads(checked.output)
    assert checked_payload["ok"] is False
    assert any(
        "pruned state is invalid for add intent" in item["message"]
        for item in checked_payload["findings"]
    )


@pytest.mark.parametrize(
    "metadata",
    [
        {"version": 2, "updates": []},
        {"version": 1, "updates": [], "future": True},
    ],
)
def test_transaction_recovery_rejects_unknown_journal_schema_without_writes(
    tmp_path, metadata
):
    root = tmp_path / "repo"
    initialize(root)
    source = root / ".murlocs/manifest.toml"
    source_before = source.read_bytes()
    journal = root / ".murlocs/curation/.transaction"
    journal.mkdir(parents=True)
    (journal / "transaction.json").write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(MurlocsError, match="invalid curation transaction journal"):
        plan_recovery(
            root,
            expected_source=".murlocs/manifest.toml",
            proposal_ids=("missing",),
        )
    assert source.read_bytes() == source_before
    assert journal.is_dir()


def test_untrusted_journal_cannot_rewrite_arbitrary_repo_file(tmp_path):
    root = tmp_path / "repo"
    initialize(root)
    propose(root, "journal-target")
    readme = root / "README.md"
    readme.write_text("trusted content\n", encoding="utf-8")
    readme_before = readme.read_bytes()
    record = root / ".murlocs/curation/journal-target.toml"
    record_before = record.read_bytes()
    journal = root / ".murlocs/curation/.transaction"
    journal.mkdir()
    attacker = b"attacker overwrite\n"
    images = ((attacker, readme_before), (record_before, record_before))
    for index, (before, after) in enumerate(images):
        (journal / f"{index}.before").write_bytes(before)
        (journal / f"{index}.after").write_bytes(after)
    metadata = {
        "version": 1,
        "operation": "promote",
        "proposal_ids": ["journal-target"],
        "source_path": ".murlocs/manifest.toml",
        "updates": [
            {
                "path": "README.md",
                "role": "source",
                "proposal_id": None,
                "before_sha256": sha256_bytes(attacker),
                "after_sha256": sha256_bytes(readme_before),
            },
            {
                "path": ".murlocs/curation/journal-target.toml",
                "role": "record",
                "proposal_id": "journal-target",
                "before_sha256": sha256_bytes(record_before),
                "after_sha256": sha256_bytes(record_before),
            },
        ],
    }
    (journal / "transaction.json").write_text(json.dumps(metadata), encoding="utf-8")
    blocked = action(root, "accept", "journal-target")
    assert json.loads(blocked.output)["ok"] is False
    assert readme.read_bytes() == readme_before
    with pytest.raises(MurlocsError, match="exact target role"):
        plan_recovery(
            root,
            expected_source=".murlocs/manifest.toml",
            proposal_ids=("journal-target",),
        )
    assert readme.read_bytes() == readme_before


def test_transaction_api_rejects_record_role_on_wrong_path(tmp_path):
    root = tmp_path / "repo"
    initialize(root)
    target = root / "README.md"
    target.write_text("trusted\n", encoding="utf-8")
    before = target.read_bytes()
    with pytest.raises(MurlocsError, match="only their named record"):
        apply_transaction(
            root,
            (FileUpdate(target, before, b"attacker\n", "record", "proposal"),),
            operation="accepted",
            proposal_ids=("proposal",),
        )
    assert target.read_bytes() == before


def test_transaction_api_rejects_arbitrary_source_role_path(tmp_path):
    root = tmp_path / "repo"
    initialize(root)
    propose(root, "source-binding")
    readme = root / "README.md"
    readme.write_text("trusted\n", encoding="utf-8")
    readme_before = readme.read_bytes()
    record = root / ".murlocs/curation/source-binding.toml"
    record_before = record.read_bytes()
    with pytest.raises(MurlocsError, match="not exactly one active source"):
        apply_transaction(
            root,
            (
                FileUpdate(readme, readme_before, b"attacker\n", "source"),
                FileUpdate(
                    record,
                    record_before,
                    record_before,
                    "record",
                    "source-binding",
                ),
            ),
            operation="promote",
            proposal_ids=("source-binding",),
            expected_source="README.md",
        )
    assert readme.read_bytes() == readme_before
    assert record.read_bytes() == record_before


def test_exact_path_journal_images_cannot_authorize_recovery_writes(tmp_path):
    root = tmp_path / "repo"
    initialize(root)
    propose(root, "exact-journal")
    assert action(root, "accept", "exact-journal").exit_code == 0
    source = root / ".murlocs/manifest.toml"
    source.write_text(
        source.read_text(encoding="utf-8") + "\n# attacker-controlled current state\n",
        encoding="utf-8",
    )
    source_current = source.read_bytes()
    record = root / ".murlocs/curation/exact-journal.toml"
    record_current = record.read_bytes()
    attacker_before = b"attacker-selected manifest replacement\n"
    journal = root / ".murlocs/curation/.transaction"
    journal.mkdir()
    images = ((attacker_before, source_current), (record_current, record_current))
    for index, (before, after) in enumerate(images):
        (journal / f"{index}.before").write_bytes(before)
        (journal / f"{index}.after").write_bytes(after)
    metadata = {
        "version": 1,
        "operation": "promote",
        "proposal_ids": ["exact-journal"],
        "source_path": ".murlocs/manifest.toml",
        "updates": [
            {
                "path": ".murlocs/manifest.toml",
                "role": "source",
                "proposal_id": None,
                "before_sha256": sha256_bytes(attacker_before),
                "after_sha256": sha256_bytes(source_current),
            },
            {
                "path": ".murlocs/curation/exact-journal.toml",
                "role": "record",
                "proposal_id": "exact-journal",
                "before_sha256": sha256_bytes(record_current),
                "after_sha256": sha256_bytes(record_current),
            },
        ],
    }
    (journal / "transaction.json").write_text(json.dumps(metadata), encoding="utf-8")
    for dry_run in (True, False):
        prefix = ["--dry-run"] if dry_run else []
        denied = invoke(
            *prefix,
            "curate",
            "recover",
            "exact-journal",
            "--repo",
            str(root),
            "--format",
            "json",
        )
        payload = json.loads(denied.output)
        assert payload["ok"] is False
        assert "manual remediation" in payload["error"]["message"]
        assert source.read_bytes() == source_current
        assert journal.is_dir()


def test_pending_journal_blocks_propose_and_symlinked_records_are_never_written(
    tmp_path,
):
    root = tmp_path / "repo"
    initialize(root)
    journal = root / ".murlocs/curation/.transaction"
    journal.mkdir(parents=True)
    for dry_run in (False, True):
        args = ["--dry-run"] if dry_run else []
        result = invoke(
            *args,
            "curate",
            "propose",
            "blocked-proposal",
            "--intent",
            "add",
            "--subject-kind",
            "operating_rule",
            "--target-source",
            ".murlocs/manifest.toml",
            "--origin",
            "issue-26",
            "--rationale",
            "Must remain blocked.",
            "--proposer",
            "@author",
            "--evidence-kind",
            "issue",
            "--evidence-reference",
            "issue-26",
            "--evidence-summary",
            "Pending journal is untrusted.",
            "--at",
            "now",
            "--value",
            "Never written.",
            "--repo",
            str(root),
        )
        assert result.exit_code == 1
    assert not (root / ".murlocs/curation/blocked-proposal.toml").exists()
    journal.rmdir()

    propose(root, "template-record")
    curation = root / ".murlocs/curation"
    outside = root / ".murlocs/symlink-target.toml"
    outside.write_text(
        (curation / "template-record.toml")
        .read_text(encoding="utf-8")
        .replace('id = "template-record"', 'id = "linked-record"'),
        encoding="utf-8",
    )
    linked = curation / "linked-record.toml"
    linked.symlink_to(outside)
    outside_before = outside.read_bytes()
    denied = action(root, "accept", "linked-record")
    assert json.loads(denied.output)["ok"] is False
    assert "may not traverse a symlink" in json.loads(denied.output)["error"]["message"]
    assert outside.read_bytes() == outside_before


def test_apply_never_executes_registered_checks(tmp_path):
    root = tmp_path / "repo"
    initialize(root)
    marker = root / "curation-ran-check"
    command = "python -c \"open('curation-ran-check','w').close()\""
    manifest = root / ".murlocs/manifest.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + "\n[checks.must-not-run]\n"
        + f"invoke = {json.dumps(command)}\n"
        + 'location = "src/app.py"\n'
        + 'proof_contains = "VALUE = 1"\n'
        + "\n[[invariants]]\n"
        + 'id = "curation-does-not-run-checks"\n'
        + 'scope = "root"\n'
        + 'statement = "Curation must not execute this registered command."\n'
        + 'severity = "critical"\n'
        + 'verification = "command"\n'
        + 'enforced_by = "must-not-run"\n',
        encoding="utf-8",
    )
    assert invoke("compile", "--repo", str(root)).exit_code == 0
    propose(root, "no-check-execution")
    assert action(root, "accept", "no-check-execution").exit_code == 0
    assert action(root, "promote", "no-check-execution").exit_code == 0
    assert not marker.exists()
