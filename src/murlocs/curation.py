"""Inert curation records and deterministic prospective review.

Records in ``.murlocs/curation`` are deliberately outside the manifest source
graph.  This module may read and validate them, but only an explicit CLI proposal
operation writes them and nothing here mutates an active manifest or layer.
"""

from __future__ import annotations

import copy
import difflib
import json
import os
import re
import tempfile
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from murlocs.codeowners import (
    CODEOWNERS_LOCATIONS,
    find_codeowners,
    normalize_path,
    parse_codeowners,
)
from murlocs.curation_transaction import (
    FileGuard,
    FileUpdate,
    RecoveryPlan,
    TreeGuard,
    apply_recovery,
    apply_transaction,
    plan_recovery,
    source_tree_sha256,
    transaction_pending,
)
from murlocs.errors import MurlocsError
from murlocs.layers import DiskSources, compose, read_disk_sources
from murlocs.lockfile import LOCK_PATH, sha256_bytes
from murlocs.manifest import parse_manifest_data
from murlocs.model import LayerSource, Manifest
from murlocs.paths import relative_posix, repo_path
from murlocs.render import prepare_manifest, render_outputs
from murlocs.serialization import render_fragment_data, render_manifest_data
from murlocs.verify import Finding, validate

CURATION_DIRECTORY = ".murlocs/curation"
CURATION_SCHEMA_VERSION = 1
ID_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?\Z")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
LIST_SUBJECT_FIELDS = {
    "pillar": "pillars",
    "search_policy": "search_policy",
    "operating_rule": "operating_rules",
    "stop_and_ask": "stop_and_ask",
    "done_criterion": "done_criteria",
}
SUBJECT_KINDS = tuple(
    [*LIST_SUBJECT_FIELDS, "scope", "invariant", "check", "judgment", "coverage_exemption"]
)
INTENTS = ("add", "replace", "remove")
EVIDENCE_KINDS = (
    "file_anchor",
    "command",
    "issue",
    "pull_request",
    "evaluation",
    "note",
)
EVENT_STATES = (
    "proposed",
    "accepted",
    "promoted",
    "superseded",
    "pruned",
    "rejected",
    "withdrawn",
)
TRANSITIONS = {
    "proposed": {"accepted", "rejected", "withdrawn"},
    "accepted": {"promoted", "pruned", "rejected"},
    "promoted": {"superseded"},
    "superseded": set(),
    "pruned": set(),
    "rejected": set(),
    "withdrawn": set(),
}
ROOT_FIELDS = {
    "curation_schema_version",
    "id",
    "intent",
    "subject_kind",
    "target_source",
    "target_scope",
    "target_key",
    "base_source_sha256",
    "origin",
    "rationale",
    "proposer",
    "required_owners",
    "evidence",
    "payload",
    "events",
}
EVIDENCE_FIELDS = {"kind", "reference", "summary"}
EVENT_FIELDS = {
    "state",
    "actor",
    "at",
    "rationale",
    "review_ref",
    "before_sha256",
    "after_sha256",
    "source_before_sha256",
    "source_after_sha256",
    "related_proposal_id",
}
TERMINAL_STATES = {"promoted", "superseded", "pruned", "rejected", "withdrawn"}


@dataclass(frozen=True)
class CurationEvidence:
    kind: str
    reference: str
    summary: str


@dataclass(frozen=True)
class CurationEvent:
    state: str
    actor: str
    at: str
    rationale: str
    review_ref: str | None = None
    before_sha256: str | None = None
    after_sha256: str | None = None
    source_before_sha256: str | None = None
    source_after_sha256: str | None = None
    related_proposal_id: str | None = None


@dataclass(frozen=True)
class CurationRecord:
    schema_version: int
    id: str
    intent: str
    subject_kind: str
    target_source: str
    target_scope: str | None
    target_key: str | None
    base_source_sha256: str
    origin: str
    rationale: str
    proposer: str
    required_owners: tuple[str, ...]
    evidence: tuple[CurationEvidence, ...]
    payload: dict[str, Any] | None
    events: tuple[CurationEvent, ...]

    @property
    def state(self) -> str:
        return self.events[-1].state


@dataclass(frozen=True)
class CurationFinding:
    code: str
    message: str
    blocking: bool = True

    def payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "blocking": self.blocking}


def proposal_path(root: Path, proposal_id: str) -> Path:
    _validate_id(proposal_id)
    _reject_curation_symlinks(root, proposal_id)
    return repo_path(root, f"{CURATION_DIRECTORY}/{proposal_id}.toml", field="proposal path")


def load_record(path: Path, *, expected_id: str | None = None) -> CurationRecord:
    if path.is_symlink():
        raise MurlocsError(f"curation record may not be a symlink: {path.name}")
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise MurlocsError(f"curation record not found: {path.name}") from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise MurlocsError(f"invalid curation TOML in {path.name}: {exc}") from exc
    return parse_record_data(data, expected_id=expected_id, filename=path.name)


def parse_record_data(
    data: dict[str, Any], *, expected_id: str | None = None, filename: str = "record"
) -> CurationRecord:
    _strict_fields(data, ROOT_FIELDS, filename)
    version = _integer(data, "curation_schema_version", filename)
    if version != CURATION_SCHEMA_VERSION:
        raise MurlocsError(
            f"{filename} uses unsupported curation_schema_version {version}; expected 1"
        )
    proposal_id = _string(data, "id", filename)
    _validate_id(proposal_id)
    if expected_id is not None and proposal_id != expected_id:
        raise MurlocsError(
            f"{filename} id {proposal_id!r} does not match filename id {expected_id!r}"
        )
    intent = _choice(data, "intent", INTENTS, filename)
    subject_kind = _choice(data, "subject_kind", SUBJECT_KINDS, filename)
    target_source = _string(data, "target_source", filename)
    target_scope = _optional_string(data, "target_scope", filename)
    target_key = _optional_string(data, "target_key", filename)
    base_hash = _string(data, "base_source_sha256", filename)
    if not SHA256_PATTERN.fullmatch(base_hash):
        raise MurlocsError(f"{filename}.base_source_sha256 must be 64 lowercase hex characters")
    required_owners = _string_array(data, "required_owners", filename)

    evidence_raw = _array(data, "evidence", filename)
    if not evidence_raw:
        raise MurlocsError(f"{filename}.evidence must contain at least one item")
    evidence: list[CurationEvidence] = []
    for index, raw in enumerate(evidence_raw):
        context = f"{filename}.evidence[{index}]"
        table = _table(raw, context)
        _strict_fields(table, EVIDENCE_FIELDS, context)
        kind = _choice(table, "kind", EVIDENCE_KINDS, context)
        evidence.append(
            CurationEvidence(
                kind=kind,
                reference=_string(table, "reference", context),
                summary=_string(table, "summary", context),
            )
        )

    events_raw = _array(data, "events", filename)
    if not events_raw:
        raise MurlocsError(f"{filename}.events must begin with a proposed event")
    events: list[CurationEvent] = []
    for index, raw in enumerate(events_raw):
        context = f"{filename}.events[{index}]"
        table = _table(raw, context)
        _strict_fields(table, EVENT_FIELDS, context)
        events.append(
            CurationEvent(
                state=_choice(table, "state", EVENT_STATES, context),
                actor=_string(table, "actor", context),
                at=_string(table, "at", context),
                rationale=_string(table, "rationale", context),
                review_ref=_optional_string(table, "review_ref", context),
                before_sha256=_optional_sha256(table, "before_sha256", context),
                after_sha256=_optional_sha256(table, "after_sha256", context),
                source_before_sha256=_optional_sha256(
                    table, "source_before_sha256", context
                ),
                source_after_sha256=_optional_sha256(
                    table, "source_after_sha256", context
                ),
                related_proposal_id=_optional_id(
                    table, "related_proposal_id", context
                ),
            )
        )
    _validate_events(events, filename, intent)

    raw_payload = data.get("payload")
    payload = None if raw_payload is None else dict(_table(raw_payload, f"{filename}.payload"))
    _validate_target_and_payload(subject_kind, intent, target_key, payload, filename)

    return CurationRecord(
        schema_version=version,
        id=proposal_id,
        intent=intent,
        subject_kind=subject_kind,
        target_source=target_source,
        target_scope=target_scope,
        target_key=target_key,
        base_source_sha256=base_hash,
        origin=_string(data, "origin", filename),
        rationale=_string(data, "rationale", filename),
        proposer=_string(data, "proposer", filename),
        required_owners=required_owners,
        evidence=tuple(evidence),
        payload=payload,
        events=tuple(events),
    )


def check_records(root: Path) -> dict[str, Any]:
    _reject_curation_symlinks(root)
    directory = repo_path(root, CURATION_DIRECTORY, field="curation directory")
    if not directory.exists():
        return {"ok": True, "records": [], "findings": []}
    if not directory.is_dir():
        finding = CurationFinding(
            "storage", f"curation path is not a directory: {CURATION_DIRECTORY}"
        )
        return {"ok": False, "records": [], "findings": [finding.payload()]}

    records: list[dict[str, Any]] = []
    findings: list[CurationFinding] = []
    if transaction_pending(root):
        findings.append(
            CurationFinding(
                "pending_transaction",
                "an interrupted curation transaction requires recovery by the next "
                "explicit curation write",
            )
        )
    ids: dict[str, str] = {}
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if path.is_symlink():
            findings.append(
                CurationFinding(
                    "record_symlink",
                    f"curation record may not be a symlink: {path.name}",
                )
            )
            continue
        if not path.is_file() or path.suffix != ".toml":
            continue
        file_id = path.stem
        try:
            _validate_id(file_id)
            record = load_record(path)
            if record.id != file_id:
                findings.append(
                    CurationFinding(
                        "filename_id",
                        f"{path.name}: record id {record.id!r} does not match filename id "
                        f"{file_id!r}",
                    )
                )
            prior = ids.get(record.id)
            if prior is not None:
                findings.append(
                    CurationFinding(
                        "duplicate_id",
                        f"proposal id {record.id!r} appears in both {prior} and {path.name}",
                    )
                )
            ids[record.id] = path.name
            report = review_record(root, record)
            records.append(
                {"id": record.id, "path": relative_posix(root, path), "state": record.state}
            )
            findings.extend(
                CurationFinding(item["code"], f"{path.name}: {item['message']}", item["blocking"])
                for item in report["findings"]
            )
        except (MurlocsError, OSError, ValueError) as exc:
            findings.append(CurationFinding("record", f"{path.name}: {exc}"))
    return {
        "ok": not any(item.blocking for item in findings),
        "records": records,
        "findings": [item.payload() for item in findings],
    }


def review_proposal(root: Path, proposal_id: str) -> dict[str, Any]:
    path = proposal_path(root, proposal_id)
    record = load_record(path, expected_id=proposal_id)
    return review_record(root, record)


def review_record(root: Path, record: CurationRecord) -> dict[str, Any]:
    if record.state in TERMINAL_STATES:
        return _terminal_review(root, record)
    disk = read_disk_sources(root)
    manifest = _manifest_from_disk(root, disk)
    source_index = _source_index(disk, record.target_source)
    source = disk.sources[source_index]
    current_owners = _current_required_owners(manifest, source)
    findings: list[CurationFinding] = []
    if tuple(sorted(record.required_owners)) != current_owners:
        findings.append(
            CurationFinding(
                "owners_changed",
                "recorded required owners "
                f"{list(record.required_owners)!r} do not match current owners "
                f"{list(current_owners)!r}",
            )
        )
    if record.target_scope is not None:
        scope_ids = {scope.id for scope in manifest.scopes}
        if record.target_scope not in scope_ids:
            findings.append(
                CurationFinding(
                    "target_scope", f"target scope does not exist: {record.target_scope}"
                )
            )

    current_hash = source.sha256
    stale = current_hash != record.base_source_sha256
    if stale:
        findings.append(
            CurationFinding(
                "stale_base",
                f"target source changed: recorded {record.base_source_sha256}, "
                f"current {current_hash}",
            )
        )
    findings.extend(_decision_owner_findings(record, current_owners))

    fragments = copy.deepcopy(disk.fragments)
    before: Any = None
    after: Any = None
    duplicate_findings: list[dict[str, Any]] = []
    collision_findings: list[dict[str, Any]] = []
    shadow_findings: list[dict[str, Any]] = []
    prospective: Manifest | None = None
    proposed_source_hash = current_hash
    ordinary_findings: list[Finding] = []
    for item in _effective_structural_findings(manifest, disk, source_index, record):
        if item.code == "exact_duplicate":
            duplicate_findings.append(item.payload())
        elif item.code == "key_collision":
            collision_findings.append(item.payload())
        elif item.code == "shadowing":
            shadow_findings.append(item.payload())
        findings.append(item)
    try:
        before, after, structural = _apply_proposal(
            fragments[source_index], record, manifest
        )
        for item in structural:
            if item.code == "exact_duplicate" and item.payload() not in duplicate_findings:
                duplicate_findings.append(item.payload())
            elif item.code == "key_collision" and item.payload() not in collision_findings:
                collision_findings.append(item.payload())
            elif item.code == "shadowing" and item.payload() not in shadow_findings:
                shadow_findings.append(item.payload())
            if item.payload() not in [existing.payload() for existing in findings]:
                findings.append(item)
        rendered = _render_source(disk, source_index, fragments[source_index])
        proposed_source_hash = sha256_bytes(rendered.encode("utf-8"))
        sources = list(disk.sources)
        sources[source_index] = LayerSource(
            id=source.id,
            kind=source.kind,
            path=source.path,
            sha256=proposed_source_hash,
            owners=source.owners,
        )
        resolved = compose(disk.root_data, sources, fragments)
        prospective = parse_manifest_data(
            root,
            resolved.data,
            layered=resolved.layered,
            sources=resolved.sources,
            scope_layers=resolved.scope_layers,
            overrides=resolved.overrides,
        )
        ordinary_findings = [
            item for item in validate(prospective) if item.code not in {"drift", "lock"}
        ]
        findings.extend(
            CurationFinding("prospective_" + item.code, item.message) for item in ordinary_findings
        )
        shadow_findings.extend(_override_shadow_findings(manifest, prospective))
    except MurlocsError as exc:
        findings.append(CurationFinding("prospective", str(exc)))

    chains = _affected_chains(manifest, prospective)
    return {
        "ok": not any(item.blocking for item in findings),
        "proposal": {
            "id": record.id,
            "state": record.state,
            "intent": record.intent,
            "subject_kind": record.subject_kind,
            "target_source": record.target_source,
            "target_scope": record.target_scope,
            "target_key": record.target_key,
            "proposer": record.proposer,
            "origin": record.origin,
            "rationale": record.rationale,
        },
        "owners": {
            "recorded": list(record.required_owners),
            "current": list(current_owners),
        },
        "decisions": [
            {
                "state": event.state,
                "actor": event.actor,
                "at": event.at,
                "rationale": event.rationale,
                "review_ref": event.review_ref,
                "before_sha256": event.before_sha256,
                "after_sha256": event.after_sha256,
                "source_before_sha256": event.source_before_sha256,
                "source_after_sha256": event.source_after_sha256,
                "related_proposal_id": event.related_proposal_id,
            }
            for event in record.events
        ],
        "evidence": [
            {"kind": item.kind, "reference": item.reference, "summary": item.summary}
            for item in record.evidence
        ],
        "change": {
            "operation": record.intent,
            "before": before,
            "after": after,
        },
        "source": {
            "recorded_sha256": record.base_source_sha256,
            "current_sha256": current_hash,
            "proposed_sha256": proposed_source_hash,
            "stale_base": stale,
            "active": True,
        },
        "exact_duplicates": duplicate_findings,
        "key_collisions": collision_findings,
        "shadowing": shadow_findings,
        "affected_chains": chains,
        "validation_findings": [
            {"code": item.code, "message": item.message} for item in ordinary_findings
        ],
        "findings": [item.payload() for item in findings],
    }


def propose_record(
    root: Path,
    *,
    proposal_id: str,
    intent: str,
    subject_kind: str,
    target_source: str,
    target_scope: str | None,
    target_key: str | None,
    origin: str,
    rationale: str,
    proposer: str,
    evidence_kind: str,
    evidence_reference: str,
    evidence_summary: str,
    at: str,
    value: str | None,
    payload_json: str | None,
    dry_run: bool,
) -> dict[str, Any]:
    _prepare_transaction(root, dry_run)
    path = proposal_path(root, proposal_id)
    if path.exists():
        raise MurlocsError(
            f"refusing to replace existing curation record: {relative_posix(root, path)}"
        )
    disk = read_disk_sources(root)
    manifest = _manifest_from_disk(root, disk)
    index = _source_index(disk, target_source)
    source = disk.sources[index]
    if value is not None and payload_json is not None:
        raise MurlocsError("provide either --value or --payload-json, not both")
    payload: dict[str, Any] | None
    if payload_json is not None:
        try:
            parsed = json.loads(payload_json)
        except json.JSONDecodeError as exc:
            raise MurlocsError(f"invalid --payload-json: {exc}") from exc
        if not isinstance(parsed, dict):
            raise MurlocsError("--payload-json must decode to an object")
        payload = parsed
    elif value is not None:
        payload = {"value": value}
    else:
        payload = None
    data: dict[str, Any] = {
        "curation_schema_version": CURATION_SCHEMA_VERSION,
        "id": proposal_id,
        "intent": intent,
        "subject_kind": subject_kind,
        "target_source": target_source,
        "base_source_sha256": source.sha256,
        "origin": origin,
        "rationale": rationale,
        "proposer": proposer,
        "required_owners": list(_current_required_owners(manifest, source)),
        "evidence": [
            {
                "kind": evidence_kind,
                "reference": evidence_reference,
                "summary": evidence_summary,
            }
        ],
        "events": [
            {
                "state": "proposed",
                "actor": proposer,
                "at": at,
                "rationale": rationale,
            }
        ],
    }
    if target_scope is not None:
        data["target_scope"] = target_scope
    if target_key is not None:
        data["target_key"] = target_key
    if payload is not None:
        data["payload"] = payload
    record = parse_record_data(data, expected_id=proposal_id, filename=f"{proposal_id}.toml")
    report = review_record(root, record)
    text = render_record(record)
    if not dry_run:
        _atomic_write_new(path, text)
    return {
        "ok": True,
        "id": proposal_id,
        "path": relative_posix(root, path),
        "dry_run": dry_run,
        "record": text,
        "review": report,
    }


def render_record(record: CurationRecord) -> str:
    lines = [
        f"curation_schema_version = {record.schema_version}",
        f"id = {_toml(record.id)}",
        f"intent = {_toml(record.intent)}",
        f"subject_kind = {_toml(record.subject_kind)}",
        f"target_source = {_toml(record.target_source)}",
    ]
    if record.target_scope is not None:
        lines.append(f"target_scope = {_toml(record.target_scope)}")
    if record.target_key is not None:
        lines.append(f"target_key = {_toml(record.target_key)}")
    lines.extend(
        [
            f"base_source_sha256 = {_toml(record.base_source_sha256)}",
            f"origin = {_toml(record.origin)}",
            f"rationale = {_toml(record.rationale)}",
            f"proposer = {_toml(record.proposer)}",
            f"required_owners = {_toml(list(record.required_owners))}",
            "",
        ]
    )
    for item in record.evidence:
        lines.extend(
            [
                "[[evidence]]",
                f"kind = {_toml(item.kind)}",
                f"reference = {_toml(item.reference)}",
                f"summary = {_toml(item.summary)}",
                "",
            ]
        )
    if record.payload is not None:
        lines.append("[payload]")
        for key in sorted(record.payload):
            lines.append(f"{_toml_key(key)} = {_toml(record.payload[key])}")
        lines.append("")
    for event in record.events:
        lines.extend(
            [
                "[[events]]",
                f"state = {_toml(event.state)}",
                f"actor = {_toml(event.actor)}",
                f"at = {_toml(event.at)}",
                f"rationale = {_toml(event.rationale)}",
            ]
        )
        if event.review_ref is not None:
            lines.append(f"review_ref = {_toml(event.review_ref)}")
        for field in (
            "before_sha256",
            "after_sha256",
            "source_before_sha256",
            "source_after_sha256",
            "related_proposal_id",
        ):
            value = getattr(event, field)
            if value is not None:
                lines.append(f"{field} = {_toml(value)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def decide_record(
    root: Path,
    proposal_id: str,
    *,
    decision: str,
    actor: str,
    at: str,
    rationale: str,
    review_ref: str | None,
    dry_run: bool,
) -> dict[str, Any]:
    """Append an attributed lifecycle decision without changing active guidance."""
    _prepare_transaction(root, dry_run)
    if decision not in {"accepted", "rejected", "withdrawn"}:
        raise MurlocsError(f"unsupported curation decision: {decision}")
    path = proposal_path(root, proposal_id)
    before_bytes = path.read_bytes()
    record = load_record(path, expected_id=proposal_id)
    if decision not in TRANSITIONS[record.state]:
        raise MurlocsError(
            f"cannot transition proposal {proposal_id} to {decision} from {record.state}"
        )

    guards: tuple[FileGuard, ...] = ()
    tree_guards: tuple[TreeGuard, ...] = ()
    if decision == "withdrawn":
        if actor != record.proposer:
            raise MurlocsError(
                f"withdraw actor must match attributed proposer {record.proposer!r}"
            )
    else:
        disk = read_disk_sources(root)
        manifest = _manifest_from_disk(root, disk)
        source = disk.sources[_source_index(disk, record.target_source)]
        current_owners = _current_required_owners(manifest, source)
        guards = _preflight_guards(root, disk, manifest, ())
        tree_guards = _preflight_tree_guards(root, manifest)
        if actor not in current_owners:
            raise MurlocsError(
                f"decision actor {actor!r} is not a current required owner; "
                "actor values are audit attribution, not authenticated identity"
            )
        if decision == "accepted":
            report = review_record(root, record)
            if not report["ok"]:
                _raise_findings("proposal cannot be accepted", report["findings"])

    event = CurationEvent(
        state=decision,
        actor=actor,
        at=at,
        rationale=rationale,
        review_ref=review_ref,
    )
    updated = replace(record, events=(*record.events, event))
    after_bytes = render_record(updated).encode("utf-8")
    return _execute_plan(
        root,
        operation=decision,
        proposal_ids=(proposal_id,),
        actor=actor,
        updates=(FileUpdate(path, before_bytes, after_bytes, "record", proposal_id),),
        events=(event,),
        dry_run=dry_run,
        guards=guards,
        tree_guards=tree_guards,
    )


def apply_record(
    root: Path,
    proposal_id: str,
    *,
    operation: str,
    actor: str,
    at: str,
    rationale: str,
    review_ref: str | None,
    dry_run: bool,
    failure_hook: Any = None,
) -> dict[str, Any]:
    """Promote or prune an accepted proposal through one recoverable transaction."""
    _prepare_transaction(root, dry_run)
    if operation not in {"promote", "prune"}:
        raise MurlocsError(f"unsupported curation apply operation: {operation}")
    record_path = proposal_path(root, proposal_id)
    record_before = record_path.read_bytes()
    record = load_record(record_path, expected_id=proposal_id)
    expected_intents = {"promote": {"add", "replace"}, "prune": {"remove"}}
    if record.state != "accepted" or record.intent not in expected_intents[operation]:
        raise MurlocsError(
            f"{operation} requires an accepted "
            + ("add or replace" if operation == "promote" else "remove")
            + " proposal"
        )
    plan = _active_source_plan(root, record, actor)
    event_state = "promoted" if operation == "promote" else "pruned"
    event = _apply_event(
        event_state,
        actor,
        at,
        rationale,
        review_ref,
        plan["before"],
        plan["after"],
        plan["source_before"],
        plan["source_after"],
    )
    updated = replace(record, events=(*record.events, event))
    updates = (
        FileUpdate(
            plan["source_path"], plan["source_bytes"], plan["rendered_bytes"], "source"
        ),
        FileUpdate(
            record_path,
            record_before,
            render_record(updated).encode("utf-8"),
            "record",
            proposal_id,
        ),
    )
    return _execute_plan(
        root,
        operation=operation,
        proposal_ids=(proposal_id,),
        actor=actor,
        updates=updates,
        events=(event,),
        dry_run=dry_run,
        guards=plan["guards"],
        tree_guards=plan["tree_guards"],
        expected_source=record.target_source,
        failure_hook=failure_hook,
    )


def supersede_record(
    root: Path,
    old_proposal_id: str,
    new_proposal_id: str,
    *,
    actor: str,
    at: str,
    rationale: str,
    review_ref: str | None,
    dry_run: bool,
    failure_hook: Any = None,
) -> dict[str, Any]:
    """Apply an accepted replacement and link it to the promoted record it replaces."""
    _prepare_transaction(root, dry_run)
    if old_proposal_id == new_proposal_id:
        raise MurlocsError("supersession requires two different proposal ids")
    old_path = proposal_path(root, old_proposal_id)
    new_path = proposal_path(root, new_proposal_id)
    old_before = old_path.read_bytes()
    new_before = new_path.read_bytes()
    old = load_record(old_path, expected_id=old_proposal_id)
    new = load_record(new_path, expected_id=new_proposal_id)
    if old.state != "promoted":
        raise MurlocsError("superseded proposal must currently be promoted")
    if new.state != "accepted" or new.intent != "replace":
        raise MurlocsError("replacement proposal must currently be accepted")
    def identity(item: CurationRecord) -> tuple[str, str | None, str]:
        return (
            item.target_source,
            item.target_scope,
            item.subject_kind,
        )
    if identity(old) != identity(new):
        raise MurlocsError("superseding proposal must target the same active subject")
    if old.subject_kind not in LIST_SUBJECT_FIELDS and old.target_key != new.target_key:
        raise MurlocsError("superseding proposal must target the same exact structured key")
    plan = _active_source_plan(root, new, actor)
    old_digest = old.events[-1].after_sha256
    if old_digest is None or old_digest != _subject_digest(plan["before"]):
        raise MurlocsError(
            "superseding proposal does not replace the subject produced by its predecessor"
        )
    promoted = _apply_event(
        "promoted",
        actor,
        at,
        rationale,
        review_ref,
        plan["before"],
        plan["after"],
        plan["source_before"],
        plan["source_after"],
        related_proposal_id=old_proposal_id,
    )
    superseded = _apply_event(
        "superseded",
        actor,
        at,
        rationale,
        review_ref,
        plan["before"],
        plan["after"],
        plan["source_before"],
        plan["source_after"],
        related_proposal_id=new_proposal_id,
    )
    updates = (
        FileUpdate(
            plan["source_path"], plan["source_bytes"], plan["rendered_bytes"], "source"
        ),
        FileUpdate(
            old_path,
            old_before,
            render_record(replace(old, events=(*old.events, superseded))).encode("utf-8"),
            "record",
            old_proposal_id,
        ),
        FileUpdate(
            new_path,
            new_before,
            render_record(replace(new, events=(*new.events, promoted))).encode("utf-8"),
            "record",
            new_proposal_id,
        ),
    )
    return _execute_plan(
        root,
        operation="supersede",
        proposal_ids=(old_proposal_id, new_proposal_id),
        actor=actor,
        updates=updates,
        events=(superseded, promoted),
        dry_run=dry_run,
        guards=plan["guards"],
        tree_guards=plan["tree_guards"],
        expected_source=new.target_source,
        failure_hook=failure_hook,
    )


def recover_record_transaction(
    root: Path,
    proposal_id: str,
    *,
    with_proposal_id: str | None,
    dry_run: bool,
) -> dict[str, Any]:
    """Preview or explicitly apply recovery of one exact curation transaction."""
    proposal_ids = (
        (proposal_id, with_proposal_id)
        if with_proposal_id is not None
        else (proposal_id,)
    )
    records = [
        load_record(proposal_path(root, item), expected_id=item) for item in proposal_ids
    ]
    sources = {record.target_source for record in records}
    if len(sources) != 1:
        raise MurlocsError("recovery records must name the same exact active source")
    source = sources.pop()
    _reject_path_symlinks(root, source, label="recovery source")
    disk = read_disk_sources(root)
    _source_index(disk, source)
    plan = plan_recovery(
        root,
        expected_source=source,
        proposal_ids=proposal_ids,
    )
    plan = _semantic_recovery_plan(root, disk, records, plan)
    patches = [_patch_payload(root, update) for update in plan.updates]
    if not dry_run:
        apply_recovery(root, plan)
    return {
        "ok": True,
        "operation": "recover",
        "proposal_ids": list(proposal_ids),
        "source": source,
        "status": plan.status,
        "dry_run": dry_run,
        "patches": patches,
    }


def _semantic_recovery_plan(
    root: Path,
    disk: DiskSources,
    records: list[CurationRecord],
    plan: RecoveryPlan,
) -> RecoveryPlan:
    """Derive recovery only from current lifecycle semantics, never journal images."""
    if plan.operation == "incomplete":
        return plan
    record = records[-1]
    source_index = _source_index(disk, record.target_source)
    source_path = repo_path(root, record.target_source, field="recovery source")
    current_bytes = source_path.read_bytes()
    current_hash = sha256_bytes(current_bytes)
    semantic_guards = [FileGuard(source_path, current_bytes)]
    semantic_guards.extend(
        FileGuard(proposal_path(root, item.id), proposal_path(root, item.id).read_bytes())
        for item in records
    )
    plan = replace(plan, guards=tuple(semantic_guards))

    if plan.operation == "supersede":
        old, new = records
        if (
            old.state == "promoted"
            and new.state == "accepted"
            and current_hash == new.base_source_sha256
        ):
            return replace(plan, status="remove semantically unapplied transaction journal")
        if old.state == "superseded" and new.state == "promoted":
            old_event = old.events[-1]
            new_event = new.events[-1]
            if (
                old_event.related_proposal_id == new.id
                and new_event.related_proposal_id == old.id
                and old_event.source_before_sha256 == new.base_source_sha256
                and new_event.source_before_sha256 == new.base_source_sha256
                and old_event.source_after_sha256 == current_hash
                and new_event.source_after_sha256 == current_hash
            ):
                return replace(plan, status="remove semantically completed transaction journal")
        raise MurlocsError(
            "interrupted supersession cannot be recovered safely from current lifecycle "
            "semantics; leave the journal in place for manual remediation"
        )

    expected_state = "promoted" if plan.operation == "promote" else "pruned"
    if record.state == expected_state:
        event = record.events[-1]
        if (
            event.source_before_sha256 == record.base_source_sha256
            and event.source_after_sha256 == current_hash
        ):
            return replace(plan, status="remove semantically completed transaction journal")
        raise MurlocsError(
            "terminal lifecycle audit does not match the current active source; "
            "manual remediation is required"
        )
    if record.state != "accepted":
        raise MurlocsError(
            "journal operation does not match the current proposal lifecycle; "
            "manual remediation is required"
        )
    if current_hash == record.base_source_sha256:
        return replace(plan, status="remove semantically unapplied transaction journal")
    if plan.operation != "promote" or record.intent != "add":
        raise MurlocsError(
            "partial replacement or removal cannot be reconstructed without trusting journal "
            "images; leave the journal in place for manual remediation"
        )

    inverse_key = record.target_key
    if record.subject_kind in LIST_SUBJECT_FIELDS:
        if record.payload is None:
            raise MurlocsError("accepted addition is missing its payload")
        inverse_key = stable_list_key(str(record.payload["value"]))
    inverse = replace(record, intent="remove", target_key=inverse_key, payload=None)
    manifest = _manifest_from_disk(root, disk)
    fragments = copy.deepcopy(disk.fragments)
    try:
        _before, _after, structural = _apply_proposal(
            fragments[source_index], inverse, manifest
        )
    except MurlocsError as exc:
        raise MurlocsError(
            "accepted addition is not the exact current subject; manual remediation is required"
        ) from exc
    if any(item.blocking for item in structural):
        raise MurlocsError("accepted addition cannot be inverted deterministically")
    reconstructed = _render_source(disk, source_index, fragments[source_index]).encode("utf-8")
    if sha256_bytes(reconstructed) != record.base_source_sha256:
        raise MurlocsError(
            "reconstructed pre-addition source does not match the recorded base hash; "
            "manual remediation is required"
        )
    update = FileUpdate(source_path, current_bytes, reconstructed, "source")
    return replace(
        plan,
        updates=(update,),
        status="roll back exact accepted addition reconstructed from lifecycle semantics",
    )


def _active_source_plan(root: Path, record: CurationRecord, actor: str) -> dict[str, Any]:
    report = review_record(root, record)
    if not report["ok"]:
        _raise_findings("proposal cannot be applied", report["findings"])
    disk = read_disk_sources(root)
    manifest = _manifest_from_disk(root, disk)
    index = _source_index(disk, record.target_source)
    source = disk.sources[index]
    current_owners = _current_required_owners(manifest, source)
    if actor not in current_owners:
        raise MurlocsError(
            f"apply actor {actor!r} is not a current required owner; "
            "actor values are audit attribution, not authenticated identity"
        )
    accepted = next(event for event in reversed(record.events) if event.state == "accepted")
    if accepted.actor not in current_owners:
        raise MurlocsError(
            f"accepted event actor {accepted.actor!r} is not a current required owner"
        )
    fragments = copy.deepcopy(disk.fragments)
    before, after, structural = _apply_proposal(fragments[index], record, manifest)
    blocking = [item for item in structural if item.blocking]
    if blocking:
        _raise_findings("proposal cannot be applied", [item.payload() for item in blocking])
    rendered = _render_source(disk, index, fragments[index]).encode("utf-8")
    rendered_hash = sha256_bytes(rendered)
    sources = list(disk.sources)
    sources[index] = replace(source, sha256=rendered_hash)
    resolved = compose(disk.root_data, sources, fragments)
    prospective = parse_manifest_data(
        root,
        resolved.data,
        layered=resolved.layered,
        sources=resolved.sources,
        scope_layers=resolved.scope_layers,
        overrides=resolved.overrides,
    )
    findings = [item for item in validate(prospective) if item.code not in {"drift", "lock"}]
    if findings:
        _raise_findings(
            "proposal fails prospective validation",
            [{"code": item.code, "message": item.message} for item in findings],
        )
    outputs = prepare_manifest(prospective)
    _reject_path_symlinks(root, record.target_source, label="target_source")
    source_path = repo_path(root, record.target_source, field="target_source")
    source_bytes = source_path.read_bytes()
    if sha256_bytes(source_bytes) != record.base_source_sha256:
        raise MurlocsError("proposal base source hash is stale")
    return {
        "source_path": source_path,
        "source_bytes": source_bytes,
        "rendered_bytes": rendered,
        "source_before": sha256_bytes(source_bytes),
        "source_after": rendered_hash,
        "before": before,
        "after": after,
        "guards": _preflight_guards(root, disk, prospective, tuple(outputs)),
        "tree_guards": _preflight_tree_guards(root, prospective),
    }


def _preflight_guards(
    root: Path,
    disk: DiskSources,
    manifest: Manifest,
    generated_paths: tuple[str, ...],
) -> tuple[FileGuard, ...]:
    paths = {str(source.path) for source in disk.sources}
    paths.update(str(path) for path in generated_paths)
    paths.add(str(LOCK_PATH))
    paths.add(str(manifest.protocol))
    paths.update(str(check.location) for check in manifest.checks.values())
    paths.update(
        str(invariant.evidence_file)
        for invariant in manifest.invariants
        if invariant.evidence_file is not None
    )
    paths.update(CODEOWNERS_LOCATIONS)
    guards: list[FileGuard] = []
    for raw in sorted(paths):
        _reject_path_symlinks(root, raw, label="preflight dependency")
        path = repo_path(root, raw, field="preflight dependency")
        before = path.read_bytes() if path.is_file() else None
        guards.append(FileGuard(path, before))
    return tuple(guards)


def _preflight_tree_guards(root: Path, manifest: Manifest) -> tuple[TreeGuard, ...]:
    guards = []
    suffixes = tuple(sorted(manifest.source_suffixes))
    for raw_value in sorted(manifest.coverage_roots):
        raw = str(raw_value)
        _reject_path_symlinks(root, raw, label="coverage root")
        path = repo_path(root, raw, field="coverage root")
        guards.append(TreeGuard(path, suffixes, source_tree_sha256(root, path, suffixes)))
    return tuple(guards)


def _apply_event(
    state: str,
    actor: str,
    at: str,
    rationale: str,
    review_ref: str | None,
    before: Any,
    after: Any,
    source_before: str,
    source_after: str,
    *,
    related_proposal_id: str | None = None,
) -> CurationEvent:
    return CurationEvent(
        state=state,
        actor=actor,
        at=at,
        rationale=rationale,
        review_ref=review_ref,
        before_sha256=_subject_digest(before),
        after_sha256=_subject_digest(after),
        source_before_sha256=source_before,
        source_after_sha256=source_after,
        related_proposal_id=related_proposal_id,
    )


def _subject_digest(value: Any) -> str:
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256_bytes(canonical)


def _execute_plan(
    root: Path,
    *,
    operation: str,
    proposal_ids: tuple[str, ...],
    actor: str,
    updates: tuple[FileUpdate, ...],
    events: tuple[CurationEvent, ...],
    dry_run: bool,
    failure_hook: Any = None,
    guards: tuple[FileGuard, ...] = (),
    tree_guards: tuple[TreeGuard, ...] = (),
    expected_source: str | None = None,
) -> dict[str, Any]:
    if dry_run and transaction_pending(root):
        raise MurlocsError("pending curation transaction requires recovery before dry-run")
    patches = [_patch_payload(root, update) for update in updates]
    if not dry_run:
        apply_transaction(
            root,
            updates,
            operation=operation,
            proposal_ids=proposal_ids,
            expected_source=expected_source,
            guards=guards,
            tree_guards=tree_guards,
            failure_hook=failure_hook,
        )
    return {
        "ok": True,
        "operation": operation,
        "proposal_ids": list(proposal_ids),
        "actor": actor,
        "identity_assurance": "not_authenticated",
        "dry_run": dry_run,
        "patches": patches,
        "events": [_event_payload(event) for event in events],
    }


def _prepare_transaction(root: Path, dry_run: bool) -> None:
    if transaction_pending(root):
        raise MurlocsError(
            "pending curation transaction is untrusted repository input; "
            "inspect it with an explicit curation recovery command"
        )


def _patch_payload(root: Path, update: FileUpdate) -> dict[str, str]:
    relative = relative_posix(root, update.path)
    return {
        "path": relative,
        "diff": "".join(
            difflib.unified_diff(
                update.before.decode("utf-8").splitlines(keepends=True),
                update.after.decode("utf-8").splitlines(keepends=True),
                fromfile="a/" + relative,
                tofile="b/" + relative,
            )
        ),
    }


def _reject_curation_symlinks(root: Path, proposal_id: str | None = None) -> None:
    raw = CURATION_DIRECTORY
    if proposal_id is not None:
        raw += f"/{proposal_id}.toml"
    _reject_path_symlinks(root, raw, label="curation storage")


def _reject_path_symlinks(root: Path, raw: str, *, label: str) -> None:
    candidate = Path(raw)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise MurlocsError(f"{label} must be a safe repository-relative path: {raw}")
    current = root
    for part in candidate.parts:
        current = current / part
        if current.is_symlink():
            raise MurlocsError(f"{label} may not traverse a symlink: {raw}")


def _event_payload(event: CurationEvent) -> dict[str, Any]:
    return {
        field: getattr(event, field)
        for field in (
            "state",
            "actor",
            "at",
            "rationale",
            "review_ref",
            "before_sha256",
            "after_sha256",
            "source_before_sha256",
            "source_after_sha256",
            "related_proposal_id",
        )
    }


def _raise_findings(prefix: str, findings: list[dict[str, Any]]) -> None:
    details = "; ".join(f"[{item['code']}] {item['message']}" for item in findings)
    raise MurlocsError(f"{prefix}: {details}")


def _terminal_review(root: Path, record: CurationRecord) -> dict[str, Any]:
    last = record.events[-1]
    expected_current_hash = (
        last.source_after_sha256
        if record.state in {"promoted", "superseded", "pruned"}
        and last.source_after_sha256 is not None
        else record.base_source_sha256
    )
    current_owners: tuple[str, ...] = ()
    current_hash = ""
    active = False
    try:
        disk = read_disk_sources(root)
        manifest = _manifest_from_disk(root, disk)
        source = disk.sources[_source_index(disk, record.target_source)]
        current_owners = _current_required_owners(manifest, source)
        current_hash = source.sha256
        active = True
    except (MurlocsError, OSError, ValueError):
        pass
    return {
        "ok": True,
        "proposal": {
            "id": record.id,
            "state": record.state,
            "intent": record.intent,
            "subject_kind": record.subject_kind,
            "target_source": record.target_source,
            "target_scope": record.target_scope,
            "target_key": record.target_key,
            "proposer": record.proposer,
            "origin": record.origin,
            "rationale": record.rationale,
        },
        "owners": {
            "recorded": list(record.required_owners),
            "current": list(current_owners),
        },
        "decisions": [_event_payload(event) for event in record.events],
        "evidence": [
            {"kind": item.kind, "reference": item.reference, "summary": item.summary}
            for item in record.evidence
        ],
        "change": {"operation": record.intent, "before": None, "after": None},
        "source": {
            "recorded_sha256": record.base_source_sha256,
            "current_sha256": current_hash,
            "proposed_sha256": last.source_after_sha256 or record.base_source_sha256,
            "stale_base": not active or current_hash != expected_current_hash,
            "active": active,
        },
        "exact_duplicates": [],
        "key_collisions": [],
        "shadowing": [],
        "affected_chains": [],
        "validation_findings": [],
        "findings": [],
    }


def stable_list_key(value: str) -> str:
    return "sha256:" + sha256_bytes(value.encode("utf-8"))


def _manifest_from_disk(root: Path, disk: DiskSources) -> Manifest:
    resolved = compose(disk.root_data, disk.sources, copy.deepcopy(disk.fragments))
    return parse_manifest_data(
        root,
        resolved.data,
        layered=resolved.layered,
        sources=resolved.sources,
        scope_layers=resolved.scope_layers,
        overrides=resolved.overrides,
    )


def _source_index(disk: DiskSources, target_source: str) -> int:
    matches = [index for index, source in enumerate(disk.sources) if source.path == target_source]
    if len(matches) != 1:
        raise MurlocsError(
            f"target_source must name exactly one active manifest or layer source: {target_source}"
        )
    return matches[0]


def _current_required_owners(manifest: Manifest, source: LayerSource) -> tuple[str, ...]:
    owners = set(source.owners)
    if manifest.validate_codeowners:
        codeowners = find_codeowners(manifest.root)
        if codeowners is not None:
            entries = parse_codeowners(codeowners.read_text(encoding="utf-8"))
            owners.update(entries.get(normalize_path(source.path), ()))
    return tuple(sorted(owners))


def _decision_owner_findings(
    record: CurationRecord, current_owners: tuple[str, ...]
) -> list[CurationFinding]:
    findings: list[CurationFinding] = []
    for event in record.events:
        if event.state == "accepted" and event.actor not in current_owners:
            findings.append(
                CurationFinding(
                    "acceptance_owner",
                    f"accepted event actor {event.actor!r} is not a current required owner",
                )
            )
    return findings


def _effective_structural_findings(
    manifest: Manifest,
    disk: DiskSources,
    source_index: int,
    record: CurationRecord,
) -> list[CurationFinding]:
    """Report exact effective-model conflicts before composition can reject them."""
    findings: list[CurationFinding] = []
    if record.subject_kind in LIST_SUBJECT_FIELDS:
        field = LIST_SUBJECT_FIELDS[record.subject_kind]
        if record.payload is not None:
            value = str(record.payload["value"])
            effective = tuple(getattr(manifest, field))
            if value in effective:
                findings.append(
                    CurationFinding(
                        "exact_duplicate",
                        f"effective guidance already contains the proposed "
                        f"{record.subject_kind} value",
                    )
                )
            locations = [
                index
                for index, fragment in enumerate(disk.fragments)
                if value in fragment.get(field, [])
            ]
            for index in locations:
                if index < source_index:
                    relation = "the proposal would be inactive behind an earlier identical value"
                elif index > source_index:
                    relation = "the proposal would make a later identical value inactive"
                else:
                    relation = "the proposal duplicates a value in its target source"
                findings.append(
                    CurationFinding(
                        "shadowing",
                        f"{relation}: {disk.sources[index].path}",
                    )
                )
        if record.intent in {"replace", "remove"}:
            target_values = disk.fragments[source_index].get(field, [])
            matches = [
                value
                for value in target_values
                if stable_list_key(value) == record.target_key
            ]
            if len(matches) == 1:
                current = matches[0]
                other_locations = [
                    index
                    for index, fragment in enumerate(disk.fragments)
                    if index != source_index and current in fragment.get(field, [])
                ]
                earlier = [index for index in other_locations if index < source_index]
                later = [index for index in other_locations if index > source_index]
                if earlier:
                    findings.append(
                        CurationFinding(
                            "shadowing",
                            "the targeted value is already inactive behind an earlier identical "
                            f"value in {disk.sources[earlier[0]].path}",
                            blocking=False,
                        )
                    )
                elif later:
                    findings.append(
                        CurationFinding(
                            "shadowing",
                            "the operation would make an identical later value newly active from "
                            f"{disk.sources[later[0]].path}",
                            blocking=False,
                        )
                    )
        return findings

    if record.intent != "add":
        return findings
    key = record.target_key or ""
    collision = False
    if record.subject_kind == "scope":
        collision = any(scope.id == key for scope in manifest.scopes)
    elif record.subject_kind == "invariant":
        collision = any(item.id == key for item in manifest.invariants)
    elif record.subject_kind == "check":
        collision = key in manifest.checks
    elif record.subject_kind == "judgment":
        scope_id, _, field = key.partition(".")
        scope = next((item for item in manifest.scopes if item.id == scope_id), None)
        collision = scope is not None and bool(getattr(scope.judgment, field))
    elif record.subject_kind == "coverage_exemption":
        collision = key in manifest.coverage_exemptions
    if collision:
        findings.append(
            CurationFinding(
                "key_collision",
                f"effective {record.subject_kind} key already exists: {key}",
            )
        )
    return findings


def _apply_proposal(
    fragment: dict[str, Any], record: CurationRecord, manifest: Manifest
) -> tuple[Any, Any, list[CurationFinding]]:
    if record.subject_kind in LIST_SUBJECT_FIELDS:
        return _apply_list(fragment, record, LIST_SUBJECT_FIELDS[record.subject_kind])
    if record.subject_kind == "scope":
        scope = next(
            (item for item in manifest.scopes if item.id == record.target_key),
            None,
        )
        identity = None if scope is None else (scope.path, scope.map)
        return _apply_table_array(
            fragment,
            record,
            "scopes",
            "id",
            immutable_scope_identity=identity,
        )
    if record.subject_kind == "invariant":
        return _apply_table_array(fragment, record, "invariants", "id")
    if record.subject_kind == "check":
        return _apply_keyed_table(fragment, record, "checks")
    if record.subject_kind == "judgment":
        return _apply_judgment(fragment, record)
    if record.subject_kind == "coverage_exemption":
        return _apply_exemption(fragment, record)
    raise MurlocsError(f"unsupported subject kind: {record.subject_kind}")


def _apply_list(
    fragment: dict[str, Any], record: CurationRecord, field: str
) -> tuple[Any, Any, list[CurationFinding]]:
    raw = fragment.setdefault(field, [])
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise MurlocsError(f"target source {field} must be an array of strings")
    values: list[str] = raw
    proposed = None if record.payload is None else str(record.payload["value"])
    if record.intent == "add":
        duplicate = proposed in values
        findings = (
            [CurationFinding("exact_duplicate", f"{field} already contains the proposed value")]
            if duplicate
            else []
        )
        values.append(proposed or "")
        return None, proposed, findings
    matches = [
        index for index, value in enumerate(values) if stable_list_key(value) == record.target_key
    ]
    if len(matches) != 1:
        raise MurlocsError(
            f"target_key must identify exactly one current {record.subject_kind}; "
            f"found {len(matches)}"
        )
    index = matches[0]
    before = values[index]
    if record.intent == "replace":
        findings = (
            [CurationFinding("exact_duplicate", f"{field} already contains the replacement value")]
            if proposed in values and proposed != before
            else []
        )
        values[index] = proposed or ""
        return before, proposed, findings
    values.pop(index)
    return before, None, []


def _apply_table_array(
    fragment: dict[str, Any],
    record: CurationRecord,
    field: str,
    key: str,
    *,
    immutable_scope_identity: tuple[str, str] | None = None,
) -> tuple[Any, Any, list[CurationFinding]]:
    raw = fragment.setdefault(field, [])
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise MurlocsError(f"target source {field} must be an array of tables")
    matches = [
        index for index, item in enumerate(raw) if str(item.get(key, "")) == record.target_key
    ]
    proposed = copy.deepcopy(record.payload)
    if record.intent == "add":
        if matches:
            return (
                None,
                proposed,
                [
                    CurationFinding(
                        "key_collision", f"{field} key already exists: {record.target_key}"
                    )
                ],
            )
        raw.append(proposed)
        return None, proposed, []
    if len(matches) != 1:
        raise MurlocsError(
            f"target_key must identify exactly one current {record.subject_kind}; "
            f"found {len(matches)}"
        )
    before = copy.deepcopy(raw[matches[0]])
    if record.intent == "replace":
        if immutable_scope_identity is not None and proposed is not None:
            current_path, current_map = immutable_scope_identity
            changes = []
            if "path" in proposed and str(proposed["path"]) != current_path:
                changes.append(f"path {current_path!r} -> {proposed['path']!r}")
            if "map" in proposed and str(proposed["map"]) != current_map:
                changes.append(f"map {current_map!r} -> {proposed['map']!r}")
            if changes:
                return (
                    before,
                    proposed,
                    [
                        CurationFinding(
                            "immutable_scope_identity",
                            "scope replacement may not change path or map: "
                            + "; ".join(changes),
                        )
                    ],
                )
        raw[matches[0]] = proposed
        return before, proposed, []
    raw.pop(matches[0])
    return before, None, []


def _apply_keyed_table(
    fragment: dict[str, Any], record: CurationRecord, field: str
) -> tuple[Any, Any, list[CurationFinding]]:
    raw = fragment.setdefault(field, {})
    if not isinstance(raw, dict):
        raise MurlocsError(f"target source {field} must be a table")
    key = record.target_key or ""
    before = copy.deepcopy(raw.get(key))
    proposed = copy.deepcopy(record.payload)
    if record.intent == "add":
        if key in raw:
            return (
                None,
                proposed,
                [CurationFinding("key_collision", f"{field} key already exists: {key}")],
            )
        raw[key] = proposed
        return None, proposed, []
    if key not in raw:
        raise MurlocsError(f"target_key does not identify a current {record.subject_kind}: {key}")
    if record.intent == "replace":
        raw[key] = proposed
        return before, proposed, []
    del raw[key]
    return before, None, []


def _apply_judgment(
    fragment: dict[str, Any], record: CurationRecord
) -> tuple[Any, Any, list[CurationFinding]]:
    scope, _, field = (record.target_key or "").partition(".")
    judgments = fragment.setdefault("judgments", {})
    if not isinstance(judgments, dict):
        raise MurlocsError("target source judgments must be a table")
    table = judgments.setdefault(scope, {})
    if not isinstance(table, dict):
        raise MurlocsError(f"judgment {scope} must be a table")
    before = copy.deepcopy(table.get(field))
    proposed = None if record.payload is None else copy.deepcopy(record.payload["values"])
    if record.intent == "add" and field in table:
        return (
            None,
            proposed,
            [CurationFinding("key_collision", f"judgment key already exists: {record.target_key}")],
        )
    if record.intent != "add" and field not in table:
        raise MurlocsError(f"target_key does not identify a current judgment: {record.target_key}")
    if record.intent == "remove":
        del table[field]
        if not table:
            del judgments[scope]
        return before, None, []
    table[field] = proposed
    return before, proposed, []


def _apply_exemption(
    fragment: dict[str, Any], record: CurationRecord
) -> tuple[Any, Any, list[CurationFinding]]:
    coverage = fragment.setdefault("coverage", {})
    if not isinstance(coverage, dict):
        raise MurlocsError("target source coverage must be a table")
    exemptions = coverage.setdefault("exemptions", {})
    if not isinstance(exemptions, dict):
        raise MurlocsError("target source coverage.exemptions must be a table")
    key = record.target_key or ""
    before = exemptions.get(key)
    proposed = None if record.payload is None else record.payload["reason"]
    if record.intent == "add" and key in exemptions:
        return (
            None,
            proposed,
            [CurationFinding("key_collision", f"coverage exemption exists: {key}")],
        )
    if record.intent != "add" and key not in exemptions:
        raise MurlocsError(f"target_key does not identify a coverage exemption: {key}")
    if record.intent == "remove":
        del exemptions[key]
        return before, None, []
    exemptions[key] = proposed
    return before, proposed, []


def _override_shadow_findings(before: Manifest, after: Manifest) -> list[dict[str, Any]]:
    old = {
        (item.subject, item.field, item.winner_layer, item.shadowed_layer)
        for item in before.overrides
    }
    findings = []
    for item in after.overrides:
        key = (item.subject, item.field, item.winner_layer, item.shadowed_layer)
        if key not in old:
            findings.append(
                CurationFinding(
                    "shadowing",
                    f"{item.subject}.{item.field} from {item.shadowed_layer} is shadowed by "
                    f"{item.winner_layer}",
                ).payload()
            )
    return findings


def _affected_chains(current: Manifest, proposed: Manifest | None) -> list[dict[str, Any]]:
    if proposed is None:
        return []
    current_outputs = render_outputs(current)
    proposed_outputs = render_outputs(proposed)
    changed_maps = {
        path
        for path in set(current_outputs) | set(proposed_outputs)
        if current_outputs.get(path) != proposed_outputs.get(path)
    }
    targets = {scope.id: scope for scope in current.scopes}
    targets.update({scope.id: scope for scope in proposed.scopes})
    chains: list[dict[str, Any]] = []
    for target in sorted(targets.values(), key=lambda item: (Path(item.path).parts, item.id)):
        current_chain = _scope_chain(current, target.path)
        proposed_chain = _scope_chain(proposed, target.path)
        maps = [scope.map for scope in current_chain]
        maps.extend(scope.map for scope in proposed_chain if scope.map not in maps)
        if not changed_maps.intersection(maps):
            continue
        current_bytes = sum(
            len(current_outputs.get(scope.map, "").encode("utf-8"))
            for scope in current_chain
        )
        proposed_bytes = sum(
            len(proposed_outputs.get(scope.map, "").encode("utf-8"))
            for scope in proposed_chain
        )
        chains.append(
            {
                "scope": target.id,
                "path": target.path,
                "maps": maps,
                "current_bytes": current_bytes,
                "proposed_bytes": proposed_bytes,
                "delta_bytes": proposed_bytes - current_bytes,
                "max_active_bytes": proposed.max_active_bytes,
                "over_budget": proposed_bytes > proposed.max_active_bytes,
            }
        )
    return chains


def _scope_chain(manifest: Manifest, target_path: str) -> list[Any]:
    target_root = repo_path(manifest.root, target_path, field="scope path")
    applicable = []
    for scope in manifest.scopes:
        scope_root = repo_path(manifest.root, scope.path, field="scope path")
        try:
            target_root.relative_to(scope_root)
        except ValueError:
            continue
        applicable.append((len(scope_root.parts), scope))
    return [scope for _, scope in sorted(applicable, key=lambda item: (item[0], item[1].id))]


def _render_source(disk: DiskSources, index: int, fragment: dict[str, Any]) -> str:
    return render_manifest_data(fragment) if index == 0 else render_fragment_data(fragment)


def _validate_target_and_payload(
    subject_kind: str,
    intent: str,
    target_key: str | None,
    payload: dict[str, Any] | None,
    context: str,
) -> None:
    if intent == "remove":
        if payload is not None:
            raise MurlocsError(f"{context}.payload must be omitted for remove")
    elif payload is None:
        raise MurlocsError(f"{context}.payload is required for {intent}")

    if subject_kind in LIST_SUBJECT_FIELDS:
        if intent == "add" and target_key is not None:
            raise MurlocsError(f"{context}.target_key must be omitted for an unkeyed list addition")
        if intent != "add":
            _validate_list_key(target_key, context)
        if payload is not None:
            _payload_fields(payload, {"value"}, {"value"}, context)
            _nonempty_value(payload["value"], f"{context}.payload.value")
        return

    if not target_key:
        raise MurlocsError(f"{context}.target_key is required for {subject_kind}")
    if subject_kind in {"scope", "invariant", "check"}:
        _validate_id(target_key)
    elif subject_kind == "judgment":
        scope, separator, field = target_key.partition(".")
        _validate_id(scope)
        if not separator or field not in {"advocate", "do_not", "serves"}:
            raise MurlocsError(
                f"{context}.target_key for judgment must be SCOPE.advocate, "
                "SCOPE.do_not, or SCOPE.serves"
            )
    elif subject_kind == "coverage_exemption" and (
        Path(target_key).is_absolute() or ".." in Path(target_key).parts
    ):
        raise MurlocsError(f"{context}.target_key must be a safe repository-relative path")

    if payload is None:
        return
    if subject_kind == "scope":
        allowed = {"id", "path", "map", "point_of_view", "owns", "guardrails", "edges", "override"}
        required = {"id"} if intent == "replace" else {"id", "path", "map", "point_of_view"}
        _payload_fields(payload, allowed, required, context)
        if payload["id"] != target_key:
            raise MurlocsError(f"{context}.payload.id must match target_key")
        _require_payload_strings(payload, {"id", "path", "map", "point_of_view"}, context)
        _optional_boolean(payload, "override", context)
        if "guardrails" in payload:
            _require_string_list(payload["guardrails"], f"{context}.payload.guardrails")
        if "owns" in payload:
            _validate_owns(payload["owns"], context)
        if "edges" in payload:
            _validate_edges(payload["edges"], context)
    elif subject_kind == "invariant":
        allowed = {
            "id",
            "scope",
            "statement",
            "severity",
            "verification",
            "enforced_by",
            "evidence_file",
            "anchor",
            "override",
        }
        required = {"id", "scope", "statement", "severity", "verification"}
        _payload_fields(payload, allowed, required, context)
        if payload["id"] != target_key:
            raise MurlocsError(f"{context}.payload.id must match target_key")
        _require_payload_strings(
            payload,
            {
                "id",
                "scope",
                "statement",
                "severity",
                "verification",
                "enforced_by",
                "evidence_file",
                "anchor",
            },
            context,
        )
        _optional_boolean(payload, "override", context)
    elif subject_kind == "check":
        _payload_fields(
            payload,
            {"invoke", "location", "proof_contains", "description", "override"},
            {"invoke", "location"},
            context,
        )
        _require_payload_strings(
            payload,
            {"invoke", "location", "proof_contains", "description"},
            context,
        )
        _optional_boolean(payload, "override", context)
    elif subject_kind == "judgment":
        _payload_fields(payload, {"values"}, {"values"}, context)
        values = payload["values"]
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(item, str) or not item.strip() for item in values)
        ):
            raise MurlocsError(f"{context}.payload.values must be a non-empty string array")
    elif subject_kind == "coverage_exemption":
        _payload_fields(payload, {"reason"}, {"reason"}, context)
        _nonempty_value(payload["reason"], f"{context}.payload.reason")


def _validate_events(events: list[CurationEvent], context: str, intent: str) -> None:
    if events[0].state != "proposed":
        raise MurlocsError(f"{context}.events[0].state must be proposed")
    for previous, current in zip(events, events[1:], strict=False):
        if current.state not in TRANSITIONS[previous.state]:
            raise MurlocsError(
                f"{context} has invalid event transition {previous.state} -> {current.state}"
            )
    for index, event in enumerate(events):
        audit_hashes = (
            event.before_sha256,
            event.after_sha256,
            event.source_before_sha256,
            event.source_after_sha256,
        )
        if event.state in {"promoted", "superseded", "pruned"} and any(
            value is None for value in audit_hashes
        ):
            raise MurlocsError(
                f"{context}.events[{index}] apply event must include before/after digests"
            )
        if event.state == "superseded" and event.related_proposal_id is None:
            raise MurlocsError(
                f"{context}.events[{index}] superseded event must link a proposal id"
            )
        if event.state == "promoted" and intent not in {"add", "replace"}:
            raise MurlocsError(
                f"{context}.events[{index}] promoted state is invalid for {intent} intent"
            )
        if event.state == "pruned" and intent != "remove":
            raise MurlocsError(
                f"{context}.events[{index}] pruned state is invalid for {intent} intent"
            )


def _validate_id(value: str) -> None:
    if not ID_PATTERN.fullmatch(value) or value in {".", ".."}:
        raise MurlocsError(
            "proposal and structured target ids must be 1-128 lowercase path-safe characters "
            "(letters, digits, '.', '_' or '-')"
        )


def _validate_list_key(value: str | None, context: str) -> None:
    if value is None or not value.startswith("sha256:") or not SHA256_PATTERN.fullmatch(value[7:]):
        raise MurlocsError(f"{context}.target_key must be sha256:<64 lowercase hex characters>")


def _strict_fields(data: dict[str, Any], allowed: set[str], context: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise MurlocsError(f"{context} has unsupported fields: {', '.join(unknown)}")


def _payload_fields(
    payload: dict[str, Any], allowed: set[str], required: set[str], context: str
) -> None:
    _strict_fields(payload, allowed, f"{context}.payload")
    missing = sorted(required - set(payload))
    if missing:
        raise MurlocsError(f"{context}.payload is missing fields: {', '.join(missing)}")


def _require_payload_strings(payload: dict[str, Any], fields: set[str], context: str) -> None:
    for field in sorted(fields & set(payload)):
        _nonempty_value(payload[field], f"{context}.payload.{field}")


def _optional_boolean(payload: dict[str, Any], field: str, context: str) -> None:
    if field in payload and not isinstance(payload[field], bool):
        raise MurlocsError(f"{context}.payload.{field} must be a boolean")


def _require_string_list(value: Any, context: str) -> None:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise MurlocsError(f"{context} must be an array of non-empty strings")


def _validate_owns(value: Any, context: str) -> None:
    if isinstance(value, list):
        _require_string_list(value, f"{context}.payload.owns")
        return
    if not isinstance(value, dict) or not value:
        raise MurlocsError(f"{context}.payload.owns must be a string array or table")
    for key, paths in value.items():
        _nonempty_value(key, f"{context}.payload.owns key")
        _require_string_list(paths, f"{context}.payload.owns.{key}")


def _validate_edges(value: Any, context: str) -> None:
    if not isinstance(value, list):
        raise MurlocsError(f"{context}.payload.edges must be an array of tables")
    for index, edge in enumerate(value):
        table = _table(edge, f"{context}.payload.edges[{index}]")
        _strict_fields(table, {"type", "to", "what"}, f"{context}.payload.edges[{index}]")
        for field in ("type", "to", "what"):
            _string(table, field, f"{context}.payload.edges[{index}]")


def _string(data: dict[str, Any], key: str, context: str) -> str:
    value = data.get(key)
    return _nonempty_value(value, f"{context}.{key}")


def _nonempty_value(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MurlocsError(f"{context} must be a non-empty string")
    return value


def _optional_string(data: dict[str, Any], key: str, context: str) -> str | None:
    if key not in data:
        return None
    return _string(data, key, context)


def _optional_sha256(data: dict[str, Any], key: str, context: str) -> str | None:
    value = _optional_string(data, key, context)
    if value is not None and not SHA256_PATTERN.fullmatch(value):
        raise MurlocsError(f"{context}.{key} must be 64 lowercase hex characters")
    return value


def _optional_id(data: dict[str, Any], key: str, context: str) -> str | None:
    value = _optional_string(data, key, context)
    if value is not None:
        try:
            _validate_id(value)
        except MurlocsError as exc:
            raise MurlocsError(f"{context}.{key}: {exc}") from exc
    return value


def _integer(data: dict[str, Any], key: str, context: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise MurlocsError(f"{context}.{key} must be an integer")
    return value


def _choice(data: dict[str, Any], key: str, choices: tuple[str, ...], context: str) -> str:
    value = _string(data, key, context)
    if value not in choices:
        raise MurlocsError(f"{context}.{key} must be one of {', '.join(choices)}")
    return value


def _array(data: dict[str, Any], key: str, context: str) -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list):
        raise MurlocsError(f"{context}.{key} must be an array")
    return value


def _string_array(data: dict[str, Any], key: str, context: str) -> tuple[str, ...]:
    values = _array(data, key, context)
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise MurlocsError(f"{context}.{key} must contain only non-empty strings")
    if len(values) != len(set(values)):
        raise MurlocsError(f"{context}.{key} contains duplicate values")
    return tuple(values)


def _table(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MurlocsError(f"{context} must be a table")
    return value


def _toml(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml(item) for item in value) + "]"
    if isinstance(value, dict):
        return (
            "{ "
            + ", ".join(
                f"{_toml_key(str(key))} = {_toml(item)}" for key, item in sorted(value.items())
            )
            + " }"
        )
    raise MurlocsError(f"payload contains unsupported TOML value: {type(value).__name__}")


def _toml_key(value: str) -> str:
    # Dynamic keys may contain Unicode or TOML punctuation. Quoting every one is
    # deterministic and avoids Python's Unicode-aware ``isalnum`` accepting keys
    # that TOML's ASCII-only bare-key grammar rejects.
    return _toml(value)


def _atomic_write_new(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise MurlocsError(
                f"refusing to replace existing curation record: {path.name}"
            ) from exc
        Path(temporary).unlink()
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
