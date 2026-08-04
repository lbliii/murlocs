"""Versioned, deterministic outcome envelopes for read-only Murlocs operations."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Literal, TypedDict, cast

from murlocs import __version__
from murlocs.errors import MurlocsError
from murlocs.model import Manifest
from murlocs.render import prepare_manifest
from murlocs.verify import Finding

OUTCOME_CONTRACT = "io.murlocs.outcome"
OUTCOME_SCHEMA_VERSION = 1
MAX_OUTCOME_BYTES = 1024 * 1024
CORRELATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
TOKEN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}")
DIAGNOSTIC_CODE = re.compile(r"MURLOCS_[A-Z0-9_]{1,120}")

ResolutionClass = Literal[
    "pass", "deterministic_repair", "agent_action", "authority_required"
]
OutcomeStatus = Literal["pass", "advisory", "blocking"]
OutcomeSeverity = Literal["none", "advisory", "important", "critical"]
OutcomeOperation = Literal["check", "impact", "aggregate"]


class OutcomeTokenScopePayload(TypedDict):
    adapter_id: str
    adapter_version: str
    session_id: str


class OutcomeCorrelationPayload(TypedDict):
    correlation_id: str | None
    state_id: str | None
    dependency_id: str | None
    token_source: Literal["none", "integration"]
    token_scope: OutcomeTokenScopePayload | None


class OutcomeSourcePayload(TypedDict):
    operation: OutcomeOperation
    exit_code: int
    murlocs_version: str


class OutcomeEvidencePayload(TypedDict):
    kind: Literal["diagnostic", "reason"]
    reference: str
    detail: str


class OutcomeProvenancePayload(TypedDict):
    operation: OutcomeOperation
    source_codes: list[str]
    source_paths: list[str]


class OutcomeAffectedPayload(TypedDict):
    scopes: list[str]
    maps: list[str]
    owners: list[str]


class OutcomeFindingPayload(TypedDict):
    code: str
    status: Literal["advisory", "blocking"]
    severity: Literal["advisory", "important", "critical"]
    message: str
    evidence: list[OutcomeEvidencePayload]
    provenance: OutcomeProvenancePayload
    affected: OutcomeAffectedPayload
    resolution_class: Literal[
        "deterministic_repair", "agent_action", "authority_required"
    ]
    action_ids: list[str]


class OutcomeActionArgumentsPayload(TypedDict):
    codes: list[str]
    scopes: list[str]
    maps: list[str]
    owners: list[str]


class OutcomeActionPayload(TypedDict):
    id: str
    operation: Literal[
        "compile_managed_guidance", "inspect_findings", "request_authority"
    ]
    arguments: OutcomeActionArgumentsPayload
    effect: Literal["read_repository", "write_managed_guidance", "request_authority"]
    authority: Literal["integration", "agent", "human"]


class OutcomeChangePayload(TypedDict):
    repository_state_changed: bool
    paths: list[str]


class OutcomeReviewEvidencePayload(TypedDict):
    adapter_id: str
    adapter_version: str
    session_id: str
    review_id: str
    reviewed_state_id: str
    owners: list[str]


class OutcomeDecisionPayload(TypedDict):
    task_authorization: Literal["not_attested", "externally_attested"]
    agent_acknowledgement: Literal["not_recorded"]
    authority_state: Literal["not_required", "unresolved", "externally_satisfied"]
    implementation: Literal["may_continue"]
    gated_boundary: Literal["none", "commit", "push", "merge", "completion"]
    required_owners: list[str]
    review_evidence: OutcomeReviewEvidencePayload | None


class OutcomePayload(TypedDict):
    contract: str
    schema_version: int
    code: str
    status: OutcomeStatus
    severity: OutcomeSeverity
    blocking: bool
    resolution_class: ResolutionClass
    source: OutcomeSourcePayload
    correlation: OutcomeCorrelationPayload
    findings: list[OutcomeFindingPayload]
    next_actions: list[OutcomeActionPayload]
    change: OutcomeChangePayload
    decision: OutcomeDecisionPayload
    silent: bool
    summary: str


_RESOLUTION_RANK: dict[str, int] = {
    "pass": 0,
    "deterministic_repair": 1,
    "agent_action": 2,
    "authority_required": 3,
}
_STATUS_RANK: dict[str, int] = {"pass": 0, "advisory": 1, "blocking": 2}
_SEVERITY_RANK: dict[str, int] = {
    "none": 0,
    "advisory": 1,
    "important": 2,
    "critical": 3,
}
_AUTHORITY_CHECK_CODES = {
    "budget",
    "curation_transaction",
    "duplicate",
    "edge",
    "invariant",
    "lock",
    "ownership",
    "path",
    "protocol",
    "schema",
    "scope",
    "severity",
}
_AGENT_CHECK_CODES = {"check", "coverage", "proof", "proof-debt"}
_CHECK_SEVERITY: dict[str, Literal["important", "critical"]] = {
    **{code: "critical" for code in _AUTHORITY_CHECK_CODES},
    **{code: "important" for code in _AGENT_CHECK_CODES},
    "drift": "important",
}
_ACTION_SPECS = {
    "deterministic_repair": (
        "outcome.compile-managed-guidance",
        "compile_managed_guidance",
        "write_managed_guidance",
        "integration",
    ),
    "agent_action": (
        "outcome.inspect-findings",
        "inspect_findings",
        "read_repository",
        "agent",
    ),
    "authority_required": (
        "outcome.request-authority",
        "request_authority",
        "request_authority",
        "human",
    ),
}
_ACTION_ID_BY_RESOLUTION = {
    resolution: values[0] for resolution, values in _ACTION_SPECS.items()
}


def validate_correlation_id(value: str | None) -> str | None:
    """Validate a caller correlation id without generating or authenticating one."""
    if value is not None and (
        not isinstance(value, str) or CORRELATION_ID.fullmatch(value) is None
    ):
        raise MurlocsError(
            "correlation id must match [A-Za-z0-9][A-Za-z0-9._:-]{0,127}"
        )
    return value


def build_check_outcome(
    manifest: Manifest,
    findings: list[Finding],
    *,
    correlation_id: str | None = None,
) -> OutcomePayload:
    """Classify one read-only check result without executing its proposed repair."""
    correlation_id = validate_correlation_id(correlation_id)
    if not findings:
        return _envelope(
            operation="check",
            exit_code=0,
            correlation_id=correlation_id,
            findings=[],
            actions=[],
            summary=(
                f"murlocs check passed: {len(manifest.scopes)} scope(s), "
                f"{len(manifest.invariants)} invariant(s), {len(manifest.checks)} check(s)"
            ),
        )

    repairable = all(item.code in {"drift", "lock"} for item in findings)
    if repairable:
        try:
            prepare_manifest(manifest)
        except (MurlocsError, OSError):
            repairable = False

    source_paths = sorted(source.path for source in manifest.sources)
    all_scopes = sorted(scope.id for scope in manifest.scopes)
    all_maps = sorted(scope.map for scope in manifest.scopes)
    all_owners = sorted({owner for source in manifest.sources for owner in source.owners})
    item_resolutions = {_check_resolution(item.code, repairable) for item in findings}
    actions_by_resolution = {
        item_resolution: _action_for(
            cast(
                Literal[
                    "deterministic_repair", "agent_action", "authority_required"
                ],
                item_resolution,
            ),
            codes=sorted(
                {
                    _check_code(item.code)
                    for item in findings
                    if _check_resolution(item.code, repairable) == item_resolution
                }
            ),
            scopes=all_scopes if item_resolution != "agent_action" else [],
            maps=all_maps if item_resolution != "agent_action" else [],
            owners=all_owners if item_resolution == "authority_required" else [],
        )
        for item_resolution in item_resolutions
    }
    payloads: list[OutcomeFindingPayload] = []
    grouped: dict[str, list[Finding]] = {}
    for item in sorted(findings, key=lambda finding: (finding.code, finding.message)):
        grouped.setdefault(item.code, []).append(item)
    for item_code, items in grouped.items():
        item = items[0]
        code = _check_code(item.code)
        item_resolution = _check_resolution(item.code, repairable)
        annotation_items = [item for item in items if item.annotation_id is not None]
        finding_source_paths = sorted(
            {
                *source_paths,
                *(path for item in annotation_items for path in item.source_paths),
            }
        )
        finding_scopes = sorted(
            {scope for item in annotation_items for scope in item.scopes}
        )
        finding_maps = sorted(
            {
                scope.map
                for scope in manifest.scopes
                if scope.id in set(finding_scopes)
            }
        )
        finding_owners = sorted(
            {
                owner
                for item in annotation_items
                for invariant_id in item.invariant_ids
                if (source := manifest.source_for_invariant(invariant_id)) is not None
                for owner in source.owners
            }
        )
        evidence = [
            {
                "kind": cast(Literal["diagnostic", "reason"], "diagnostic"),
                "reference": finding.code,
                "detail": finding.message,
            }
            for finding in items[:128]
        ]
        if len(items) > 128:
            evidence[-1] = {
                "kind": "diagnostic",
                "reference": item.code,
                "detail": f"{len(items) - 127} additional {item.code} issue(s)",
            }
        payloads.append(
            {
                "code": code,
                "status": "blocking",
                "severity": _CHECK_SEVERITY.get(item.code, "important"),
                "message": (
                    item.message
                    if len(items) == 1
                    else f"murlocs found {len(items)} {item_code} issue(s)"
                ),
                "evidence": evidence,
                "provenance": {
                    "operation": "check",
                    "source_codes": [item.code],
                    "source_paths": finding_source_paths,
                },
                "affected": {
                    "scopes": (
                        finding_scopes
                        if annotation_items
                        else all_scopes if item_resolution != "agent_action" else []
                    ),
                    "maps": (
                        finding_maps
                        if annotation_items
                        else all_maps if item_resolution != "agent_action" else []
                    ),
                    "owners": (
                        finding_owners
                        if annotation_items
                        else all_owners if item_resolution == "authority_required" else []
                    ),
                },
                "resolution_class": item_resolution,
                "action_ids": [actions_by_resolution[item_resolution]["id"]],
            }
        )
    return _envelope(
        operation="check",
        exit_code=1,
        correlation_id=correlation_id,
        findings=payloads,
        actions=list(actions_by_resolution.values()),
        summary=f"murlocs found {len(findings)} issue(s)",
    )


def build_impact_outcome(
    report: Mapping[str, Any], *, correlation_id: str | None = None
) -> OutcomePayload:
    """Classify one read-only impact report without copying registered commands."""
    correlation_id = validate_correlation_id(correlation_id)
    payloads: list[OutcomeFindingPayload] = []
    required_codes: list[str] = []
    recommended_codes: list[str] = []
    recommended_scopes: set[str] = set()
    recommended_maps: set[str] = set()
    recommended_owners: set[str] = set()
    required_scopes: set[str] = set()
    required_maps: set[str] = set()
    required_owners: set[str] = set()
    for scope in sorted(report.get("scopes", ()), key=lambda item: str(item.get("id", ""))):
        scope_status = scope.get("status")
        if scope_status not in {"required", "recommended"}:
            continue
        required = scope_status == "required"
        code = (
            "MURLOCS_IMPACT_REVIEW_REQUIRED"
            if required
            else "MURLOCS_IMPACT_REVIEW_RECOMMENDED"
        )
        scope_id = str(scope.get("id", ""))
        maps = sorted(
            {
                str(scope.get("map", "")),
                *(
                    str(item.get("map", ""))
                    for item in scope.get("guidance_chain", ())
                ),
            }
            - {""}
        )
        owners = sorted({str(owner) for owner in scope.get("owners", ())})
        source_paths = sorted(
            {str(layer.get("path", "")) for layer in scope.get("layers", ())} - {""}
        )
        severity = _impact_severity(scope, required=required)
        action_id = "outcome.request-authority" if required else "outcome.inspect-findings"
        payloads.append(
            {
                "code": code,
                "status": "advisory",
                "severity": severity,
                "message": (
                    f"Scope {scope_id} requires guidance review."
                    if required
                    else f"Scope {scope_id} is recommended for guidance review."
                ),
                "evidence": [
                    {
                        "kind": "reason",
                        "reference": scope_id,
                        "detail": str(reason),
                    }
                    for reason in sorted(scope.get("reasons", ()))
                ],
                "provenance": {
                    "operation": "impact",
                    "source_codes": [scope_status],
                    "source_paths": source_paths,
                },
                "affected": {
                    "scopes": [scope_id],
                    "maps": maps,
                    "owners": owners,
                },
                "resolution_class": "authority_required" if required else "agent_action",
                "action_ids": [action_id],
            }
        )
        if required:
            required_codes.append(code)
            required_scopes.add(scope_id)
            required_maps.update(maps)
            required_owners.update(owners)
        else:
            recommended_codes.append(code)
            recommended_scopes.add(scope_id)
            recommended_maps.update(maps)
            recommended_owners.update(owners)

    actions: list[OutcomeActionPayload] = []
    if recommended_codes:
        actions.append(
            _action_for(
                "agent_action",
                codes=sorted(set(recommended_codes)),
                scopes=sorted(recommended_scopes),
                maps=sorted(recommended_maps),
                owners=sorted(recommended_owners),
            )
        )
    if required_codes:
        actions.append(
            _action_for(
                "authority_required",
                codes=sorted(set(required_codes)),
                scopes=sorted(required_scopes),
                maps=sorted(required_maps),
                owners=sorted(required_owners),
            )
        )
    summary = report.get("summary", {})
    return _envelope(
        operation="impact",
        exit_code=0,
        correlation_id=correlation_id,
        findings=payloads,
        actions=actions,
        summary=(
            f"Summary: {int(summary.get('required', 0))} required, "
            f"{int(summary.get('recommended', 0))} recommended, "
            f"{int(summary.get('unaffected', 0))} unaffected"
        ),
    )


def build_failure_outcome(
    operation: Literal["check", "impact"],
    code: str,
    message: str,
    *,
    correlation_id: str | None = None,
) -> OutcomePayload:
    """Represent a handled operation failure while preserving exit code one."""
    correlation_id = validate_correlation_id(correlation_id)
    stable = _stable_code(code)
    action = _action_for(
        "agent_action", codes=[stable], scopes=[], maps=[], owners=[]
    )
    finding: OutcomeFindingPayload = {
        "code": stable,
        "status": "blocking",
        "severity": "important",
        "message": message,
        "evidence": [
            {"kind": "diagnostic", "reference": code, "detail": message}
        ],
        "provenance": {
            "operation": operation,
            "source_codes": [code],
            "source_paths": [],
        },
        "affected": {"scopes": [], "maps": [], "owners": []},
        "resolution_class": "agent_action",
        "action_ids": [action["id"]],
    }
    return _envelope(
        operation=operation,
        exit_code=1,
        correlation_id=correlation_id,
        findings=[finding],
        actions=[action],
        summary=f"error: {message}",
    )


def bind_integration_tokens(
    outcome: Mapping[str, Any],
    *,
    correlation_id: str,
    state_id: str,
    token_scope: Mapping[str, Any],
    dependency_id: str | None = None,
) -> OutcomePayload:
    """Echo trusted opaque adapter tokens without claiming freshness or deriving them."""
    parsed = parse_outcome(outcome)
    validate_correlation_id(correlation_id)
    current = parsed["correlation"]
    if (
        current["token_source"] != "none"
        or current["state_id"] is not None
        or current["dependency_id"] is not None
        or current["token_scope"] is not None
    ):
        raise MurlocsError("outcome integration tokens may only be bound once")
    if current["correlation_id"] != correlation_id:
        raise MurlocsError("integration correlation must equal the outcome correlation")
    if not isinstance(state_id, str) or TOKEN_ID.fullmatch(state_id) is None:
        raise MurlocsError("state id must be a nonempty opaque integration token")
    if dependency_id is not None and (
        not isinstance(dependency_id, str) or TOKEN_ID.fullmatch(dependency_id) is None
    ):
        raise MurlocsError("dependency id must be a nonempty opaque integration token")
    if dependency_id is not None and parsed["source"]["operation"] == "check":
        raise MurlocsError("dependency id is only valid for impact or aggregate outcomes")
    parsed_scope = _parse_token_scope(token_scope)
    parsed["correlation"] = {
        "correlation_id": correlation_id,
        "state_id": state_id,
        "dependency_id": dependency_id,
        "token_source": "integration",
        "token_scope": parsed_scope,
    }
    return parsed


def reconcile_external_authority_evidence(
    outcome: Mapping[str, Any],
    evidence: Mapping[str, Any] | None,
    *,
    gated_boundary: Literal["commit", "push", "merge", "completion"] = "merge",
    task_authorized: bool = False,
) -> OutcomePayload:
    """Reconcile one adapter-owned owner-review observation with an outcome.

    This is intentionally not a terminal, programmatic, or MCP input.  An
    adapter calls it after validating its own review integration.  A missing,
    stale, or mismatched observation always returns the authority state to
    unresolved instead of preserving a prior review claim.
    """
    parsed = parse_outcome(outcome)
    decision = _default_decision(parsed)
    if parsed["resolution_class"] != "authority_required":
        if evidence is not None:
            raise MurlocsError("external authority evidence requires an authority outcome")
        parsed["decision"] = decision
        return parsed
    if gated_boundary not in {"commit", "push", "merge", "completion"}:
        raise MurlocsError("authority gate must be commit, push, merge, or completion")
    if not isinstance(task_authorized, bool):
        raise MurlocsError("trusted task authorization must be boolean")
    decision["gated_boundary"] = gated_boundary
    decision["task_authorization"] = (
        "externally_attested" if task_authorized else "not_attested"
    )
    if evidence is None:
        parsed["decision"] = decision
        return parsed
    reviewed = _parse_review_evidence(evidence)
    required = decision["required_owners"]
    if not _external_review_is_current(parsed, reviewed, required):
        parsed["decision"] = decision
        return parsed
    decision["authority_state"] = "externally_satisfied"
    decision["review_evidence"] = reviewed
    parsed["decision"] = decision
    return parsed


def render_compact_outcome(outcome: Mapping[str, Any]) -> str:
    """Render one bounded active-agent instruction from parsed outcome fields."""
    parsed = parse_outcome(outcome)
    if parsed["silent"]:
        return ""
    decision = parsed["decision"]
    action = parsed["next_actions"][0]
    scopes = ", ".join(action["arguments"]["scopes"]) or "none"
    owners = ", ".join(action["arguments"]["owners"]) or "none"
    lines = [
        f"status: {parsed['status']}; scopes: {scopes}; owners: {owners}",
    ]
    if parsed["resolution_class"] == "authority_required":
        boundary = decision["gated_boundary"]
        if decision["authority_state"] == "externally_satisfied":
            lines.append(
                f"lifecycle: {owners} review satisfies the {boundary} gate; "
                f"{boundary} may proceed while evidence remains valid."
            )
            lines.append(
                f"next: retain the trusted {owners} review evidence through {boundary}."
            )
        else:
            lines.append(
                f"lifecycle: implementation may continue; {owners} review gates {boundary}."
            )
            lines.append(f"next: obtain {owners} review before {boundary}.")
    elif action["operation"] == "compile_managed_guidance":
        lines.append("next: ask the authorized integration to compile managed guidance.")
    else:
        lines.append("next: inspect the named findings and affected guidance before proceeding.")
    return "\n".join(lines)


def merge_outcomes(outcomes: list[Mapping[str, Any]]) -> OutcomePayload:
    """Merge ordered operation sidecars under one matching correlation context."""
    if not outcomes:
        raise MurlocsError("at least one outcome is required")
    parsed = [parse_outcome(item) for item in outcomes]
    versions = {item["source"]["murlocs_version"] for item in parsed}
    if len(versions) != 1:
        raise MurlocsError("outcome Murlocs versions do not match")
    correlation = _merge_correlation([item["correlation"] for item in parsed])
    findings = _dedupe_findings(
        finding for item in parsed for finding in item["findings"]
    )
    actions = _dedupe_actions(action for item in parsed for action in item["next_actions"])
    merged = _envelope(
        operation="aggregate",
        exit_code=max(item["source"]["exit_code"] for item in parsed),
        correlation_id=correlation["correlation_id"],
        findings=findings,
        actions=actions,
        summary=_aggregate_summary(findings),
    )
    merged["correlation"] = dict(correlation)
    merged["source"]["murlocs_version"] = versions.pop()
    return merged


def parse_outcome_json(raw: str | bytes) -> OutcomePayload:
    """Strictly decode one bounded envelope, rejecting duplicate JSON members."""
    if not isinstance(raw, (str, bytes)):
        raise MurlocsError("outcome JSON must be text or bytes")
    encoded = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(encoded) > MAX_OUTCOME_BYTES:
        raise MurlocsError("outcome envelope exceeds 1 MiB")

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite number {value}")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON member: {key}")
            result[key] = value
        return result

    try:
        data = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        RecursionError,
    ) as exc:
        raise MurlocsError(f"invalid outcome JSON: {exc}") from exc
    return parse_outcome(data)


def parse_outcome(value: Any) -> OutcomePayload:
    """Validate v1 semantics and normalize every malformed value to MurlocsError."""
    try:
        return _parse_outcome(value)
    except MurlocsError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError) as exc:
        raise MurlocsError(f"invalid outcome envelope: {exc}") from exc


def _parse_outcome(value: Any) -> OutcomePayload:
    """Validate v1 semantics and return known fields, ignoring future metadata."""
    if not isinstance(value, Mapping):
        raise MurlocsError("outcome envelope must be an object")
    if value.get("contract") != OUTCOME_CONTRACT:
        raise MurlocsError("unsupported outcome contract")
    version = value.get("schema_version")
    if isinstance(version, bool) or version != OUTCOME_SCHEMA_VERSION:
        raise MurlocsError(
            f"unsupported outcome schema_version {version!r}; expected 1"
        )
    required = {
        "code",
        "status",
        "severity",
        "blocking",
        "resolution_class",
        "source",
        "correlation",
        "findings",
        "next_actions",
        "change",
        "silent",
        "summary",
    }
    missing = sorted(required - set(value))
    if missing:
        raise MurlocsError(f"outcome envelope is missing: {', '.join(missing)}")
    code = _required_code(value["code"])
    status = _enum(value["status"], _STATUS_RANK, "status")
    severity = _enum(value["severity"], _SEVERITY_RANK, "severity")
    resolution = _enum(value["resolution_class"], _RESOLUTION_RANK, "resolution_class")
    blocking = _boolean(value["blocking"], "blocking")
    silent = _boolean(value["silent"], "silent")
    summary = _bounded_string(value["summary"], "summary", maximum=4096)
    source = _parse_source(value["source"])
    correlation = _parse_correlation(value["correlation"])
    findings = _parse_findings(value["findings"])
    actions = _parse_actions(value["next_actions"])
    change = _parse_change(value["change"])
    expected_resolution = _dominant(
        (item["resolution_class"] for item in findings), _RESOLUTION_RANK, "pass"
    )
    expected_status = _dominant(
        (item["status"] for item in findings), _STATUS_RANK, "pass"
    )
    expected_severity = _dominant(
        (item["severity"] for item in findings), _SEVERITY_RANK, "none"
    )
    if (resolution, status, severity) != (
        expected_resolution,
        expected_status,
        expected_severity,
    ):
        raise MurlocsError("outcome aggregate does not match its findings")
    if blocking != (status == "blocking"):
        raise MurlocsError("outcome blocking does not match status")
    if source["exit_code"] != (1 if blocking else 0):
        raise MurlocsError("outcome source exit_code does not match blocking status")
    if silent != (status == "pass"):
        raise MurlocsError("outcome silent does not match status")
    if code != _outcome_code(cast(ResolutionClass, resolution)):
        raise MurlocsError("outcome code does not match resolution_class")
    action_ids = {action["id"] for action in actions}
    referenced = {item for finding in findings for item in finding["action_ids"]}
    if referenced != action_ids:
        raise MurlocsError("outcome finding action references do not match next_actions")
    expected_arguments = {
        action_id: {"codes": set(), "scopes": set(), "maps": set(), "owners": set()}
        for action_id in action_ids
    }
    for finding in findings:
        if (
            source["operation"] != "aggregate"
            and finding["provenance"]["operation"] != source["operation"]
        ):
            raise MurlocsError("outcome finding provenance does not match source operation")
        expected_action = _ACTION_ID_BY_RESOLUTION[finding["resolution_class"]]
        if finding["action_ids"] != [expected_action]:
            raise MurlocsError("outcome finding action does not match its resolution")
        expected = expected_arguments[expected_action]
        expected["codes"].add(finding["code"])
        for field in ("scopes", "maps", "owners"):
            expected[field].update(finding["affected"][field])
    for action in actions:
        expected = expected_arguments[action["id"]]
        if any(
            action["arguments"][field] != sorted(expected[field])
            for field in ("codes", "scopes", "maps", "owners")
        ):
            raise MurlocsError(
                f"outcome action arguments do not match findings: {action['id']}"
            )
    parsed: OutcomePayload = {
        "contract": OUTCOME_CONTRACT,
        "schema_version": OUTCOME_SCHEMA_VERSION,
        "code": code,
        "status": cast(OutcomeStatus, status),
        "severity": cast(OutcomeSeverity, severity),
        "blocking": blocking,
        "resolution_class": cast(ResolutionClass, resolution),
        "source": source,
        "correlation": correlation,
        "findings": findings,
        "next_actions": actions,
        "change": change,
        "decision": {
            "task_authorization": "not_attested",
            "agent_acknowledgement": "not_recorded",
            "authority_state": "not_required",
            "implementation": "may_continue",
            "gated_boundary": "none",
            "required_owners": [],
            "review_evidence": None,
        },
        "silent": silent,
        "summary": summary,
    }
    parsed["decision"] = _parse_decision(value.get("decision"), parsed)
    return parsed


def outcome_json_bytes(outcome: Mapping[str, Any]) -> bytes:
    """Render canonical UTF-8 JSON for receipts and golden fixtures."""
    parsed = parse_outcome(outcome)
    return json.dumps(
        parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _envelope(
    *,
    operation: OutcomeOperation,
    exit_code: int,
    correlation_id: str | None,
    findings: list[OutcomeFindingPayload],
    actions: list[OutcomeActionPayload],
    summary: str,
) -> OutcomePayload:
    resolution = cast(
        ResolutionClass,
        _dominant(
            (item["resolution_class"] for item in findings), _RESOLUTION_RANK, "pass"
        ),
    )
    status = cast(
        OutcomeStatus,
        _dominant((item["status"] for item in findings), _STATUS_RANK, "pass"),
    )
    severity = cast(
        OutcomeSeverity,
        _dominant((item["severity"] for item in findings), _SEVERITY_RANK, "none"),
    )
    payload: OutcomePayload = {
        "contract": OUTCOME_CONTRACT,
        "schema_version": OUTCOME_SCHEMA_VERSION,
        "code": _outcome_code(resolution),
        "status": status,
        "severity": severity,
        "blocking": status == "blocking",
        "resolution_class": resolution,
        "source": {
            "operation": operation,
            "exit_code": exit_code,
            "murlocs_version": __version__,
        },
        "correlation": {
            "correlation_id": correlation_id,
            "state_id": None,
            "dependency_id": None,
            "token_source": "none",
            "token_scope": None,
        },
        "findings": sorted(findings, key=_finding_key),
        "next_actions": sorted(actions, key=lambda item: item["id"]),
        "change": {"repository_state_changed": False, "paths": []},
        "decision": cast(OutcomeDecisionPayload, {}),
        "silent": status == "pass",
        "summary": summary,
    }
    payload["decision"] = _default_decision(payload)
    return payload


def _action_for(
    resolution: Literal["deterministic_repair", "agent_action", "authority_required"],
    *,
    codes: list[str],
    scopes: list[str],
    maps: list[str],
    owners: list[str],
) -> OutcomeActionPayload:
    arguments: OutcomeActionArgumentsPayload = {
        "codes": sorted(set(codes)),
        "scopes": sorted(set(scopes)),
        "maps": sorted(set(maps)),
        "owners": sorted(set(owners)),
    }
    action_id, operation, effect, authority = _ACTION_SPECS[resolution]
    return {
        "id": action_id,
        "operation": cast(Any, operation),
        "arguments": arguments,
        "effect": cast(Any, effect),
        "authority": cast(Any, authority),
    }


def _parse_source(value: Any) -> OutcomeSourcePayload:
    if not isinstance(value, Mapping):
        raise MurlocsError("outcome source must be an object")
    operation = _enum(
        value.get("operation"),
        {"check": 0, "impact": 1, "aggregate": 2},
        "source.operation",
    )
    exit_code = value.get("exit_code")
    if isinstance(exit_code, bool) or exit_code not in {0, 1}:
        raise MurlocsError("outcome source.exit_code must be 0 or 1")
    version = _bounded_string(value.get("murlocs_version"), "source.murlocs_version", maximum=128)
    return {
        "operation": cast(OutcomeOperation, operation),
        "exit_code": exit_code,
        "murlocs_version": version,
    }


def _parse_correlation(value: Any) -> OutcomeCorrelationPayload:
    if not isinstance(value, Mapping):
        raise MurlocsError("outcome correlation must be an object")
    correlation_id = value.get("correlation_id")
    validate_correlation_id(correlation_id)
    state_id = _optional_token(value.get("state_id"), "state_id")
    dependency_id = _optional_token(value.get("dependency_id"), "dependency_id")
    source = _enum(value.get("token_source"), {"none": 0, "integration": 1}, "token_source")
    raw_scope = value.get("token_scope")
    token_scope = None if raw_scope is None else _parse_token_scope(raw_scope)
    if source == "none" and (
        state_id is not None or dependency_id is not None or token_scope is not None
    ):
        raise MurlocsError("untrusted outcome cannot carry integration tokens")
    if source == "integration" and (
        correlation_id is None or state_id is None or token_scope is None
    ):
        raise MurlocsError(
            "integration-bound outcome requires correlation, state, and scope tokens"
        )
    return {
        "correlation_id": correlation_id,
        "state_id": state_id,
        "dependency_id": dependency_id,
        "token_source": cast(Literal["none", "integration"], source),
        "token_scope": token_scope,
    }


def _parse_token_scope(value: Any) -> OutcomeTokenScopePayload:
    if not isinstance(value, Mapping) or set(value) != {
        "adapter_id",
        "adapter_version",
        "session_id",
    }:
        raise MurlocsError("integration token_scope must name adapter id/version/session")
    return {
        "adapter_id": _bounded_string(value.get("adapter_id"), "adapter_id", maximum=255),
        "adapter_version": _bounded_string(
            value.get("adapter_version"), "adapter_version", maximum=255
        ),
        "session_id": _bounded_string(value.get("session_id"), "session_id", maximum=255),
    }


def _parse_findings(value: Any) -> list[OutcomeFindingPayload]:
    if not isinstance(value, list) or len(value) > 1024:
        raise MurlocsError("outcome findings must be a bounded array")
    parsed: list[OutcomeFindingPayload] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            raise MurlocsError("outcome finding must be an object")
        code = _required_code(raw.get("code"))
        status = _enum(raw.get("status"), {"advisory": 0, "blocking": 1}, "finding.status")
        severity = _enum(
            raw.get("severity"), {"advisory": 0, "important": 1, "critical": 2}, "finding.severity"
        )
        resolution = _enum(
            raw.get("resolution_class"),
            {"deterministic_repair": 0, "agent_action": 1, "authority_required": 2},
            "finding.resolution_class",
        )
        evidence = _parse_evidence(raw.get("evidence"))
        provenance = _parse_provenance(raw.get("provenance"))
        affected = _parse_affected(raw.get("affected"))
        action_ids = _string_list(raw.get("action_ids"), "finding.action_ids", maximum=8)
        parsed.append(
            {
                "code": code,
                "status": cast(Literal["advisory", "blocking"], status),
                "severity": cast(Literal["advisory", "important", "critical"], severity),
                "message": _bounded_string(raw.get("message"), "finding.message", maximum=8192),
                "evidence": evidence,
                "provenance": provenance,
                "affected": affected,
                "resolution_class": cast(
                    Literal["deterministic_repair", "agent_action", "authority_required"],
                    resolution,
                ),
                "action_ids": action_ids,
            }
        )
    ordered = sorted(parsed, key=_finding_key)
    if len({_finding_identity(item) for item in ordered}) != len(ordered):
        raise MurlocsError("outcome findings must not contain duplicates")
    return ordered


def _parse_evidence(value: Any) -> list[OutcomeEvidencePayload]:
    if not isinstance(value, list) or not value or len(value) > 128:
        raise MurlocsError("finding evidence must be a nonempty bounded array")
    parsed = []
    for raw in value:
        if not isinstance(raw, Mapping):
            raise MurlocsError("finding evidence must be an object")
        kind = _enum(raw.get("kind"), {"diagnostic": 0, "reason": 1}, "evidence.kind")
        parsed.append(
            {
                "kind": cast(Literal["diagnostic", "reason"], kind),
                "reference": _bounded_string(
                    raw.get("reference"), "evidence.reference", maximum=1024
                ),
                "detail": _bounded_string(raw.get("detail"), "evidence.detail", maximum=8192),
            }
        )
    return sorted(parsed, key=lambda item: (item["kind"], item["reference"], item["detail"]))


def _parse_provenance(value: Any) -> OutcomeProvenancePayload:
    if not isinstance(value, Mapping):
        raise MurlocsError("finding provenance must be an object")
    operation = _enum(
        value.get("operation"),
        {"check": 0, "impact": 1, "aggregate": 2},
        "provenance.operation",
    )
    return {
        "operation": cast(OutcomeOperation, operation),
        "source_codes": _string_list(value.get("source_codes"), "provenance.source_codes"),
        "source_paths": _string_list(value.get("source_paths"), "provenance.source_paths"),
    }


def _parse_affected(value: Any) -> OutcomeAffectedPayload:
    if not isinstance(value, Mapping):
        raise MurlocsError("finding affected must be an object")
    return {
        "scopes": _string_list(value.get("scopes"), "affected.scopes"),
        "maps": _string_list(value.get("maps"), "affected.maps"),
        "owners": _string_list(value.get("owners"), "affected.owners"),
    }


def _parse_actions(value: Any) -> list[OutcomeActionPayload]:
    if not isinstance(value, list) or len(value) > 8:
        raise MurlocsError("outcome next_actions must be a bounded array")
    parsed: list[OutcomeActionPayload] = []
    expected_keys = {"id", "operation", "arguments", "effect", "authority"}
    allowed = {
        values[1]: (values[0], values[2], values[3])
        for values in _ACTION_SPECS.values()
    }
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != expected_keys:
            raise MurlocsError("outcome action has unknown or missing fields")
        operation = raw.get("operation")
        if operation not in allowed:
            raise MurlocsError(f"unsupported outcome action {operation!r}")
        arguments = raw.get("arguments")
        argument_keys = {"codes", "scopes", "maps", "owners"}
        if not isinstance(arguments, Mapping) or set(arguments) != argument_keys:
            raise MurlocsError("outcome action arguments are not allowlisted")
        expected_id, effect, authority = allowed[cast(str, operation)]
        if (raw.get("id"), raw.get("effect"), raw.get("authority")) != (
            expected_id,
            effect,
            authority,
        ):
            raise MurlocsError("outcome action semantics do not match its operation")
        payload: OutcomeActionPayload = {
            "id": expected_id,
            "operation": cast(Any, operation),
            "arguments": {
                "codes": _string_list(arguments.get("codes"), "action.codes"),
                "scopes": _string_list(arguments.get("scopes"), "action.scopes"),
                "maps": _string_list(arguments.get("maps"), "action.maps"),
                "owners": _string_list(arguments.get("owners"), "action.owners"),
            },
            "effect": cast(Any, effect),
            "authority": cast(Any, authority),
        }
        if not payload["arguments"]["codes"]:
            raise MurlocsError("outcome action requires diagnostic codes")
        parsed.append(payload)
    ordered = sorted(parsed, key=lambda item: item["id"])
    if len({item["id"] for item in ordered}) != len(ordered):
        raise MurlocsError("outcome actions must not contain duplicates")
    return ordered


def _parse_change(value: Any) -> OutcomeChangePayload:
    if not isinstance(value, Mapping):
        raise MurlocsError("outcome change must be an object")
    changed = _boolean(value.get("repository_state_changed"), "repository_state_changed")
    paths = _string_list(value.get("paths"), "change.paths")
    if changed or paths:
        raise MurlocsError("check and impact outcome v1 must be read-only")
    return {"repository_state_changed": False, "paths": []}


def _default_decision(outcome: Mapping[str, Any]) -> OutcomeDecisionPayload:
    authority = outcome.get("resolution_class") == "authority_required"
    owners: list[str] = []
    if authority:
        for action in outcome.get("next_actions", []):
            if isinstance(action, Mapping) and action.get("operation") == "request_authority":
                arguments = action.get("arguments")
                if isinstance(arguments, Mapping):
                    owners = _string_list(arguments.get("owners"), "decision owners")
                break
    return {
        "task_authorization": "not_attested",
        "agent_acknowledgement": "not_recorded",
        "authority_state": "unresolved" if authority else "not_required",
        "implementation": "may_continue",
        "gated_boundary": "merge" if authority else "none",
        "required_owners": owners,
        "review_evidence": None,
    }


def _parse_decision(value: Any, outcome: Mapping[str, Any]) -> OutcomeDecisionPayload:
    expected = _default_decision(outcome)
    if value is None:
        return expected
    if not isinstance(value, Mapping) or set(value) != {
        "task_authorization",
        "agent_acknowledgement",
        "authority_state",
        "implementation",
        "gated_boundary",
        "required_owners",
        "review_evidence",
    }:
        raise MurlocsError("outcome decision has unknown or missing fields")
    task_authorization = _enum(
        value.get("task_authorization"),
        {"not_attested": 0, "externally_attested": 1},
        "decision.task_authorization",
    )
    if value.get("agent_acknowledgement") != "not_recorded":
        raise MurlocsError("outcome does not accept agent acknowledgement")
    if value.get("implementation") != "may_continue":
        raise MurlocsError("outcome implementation decision is invalid")
    gated = _enum(
        value.get("gated_boundary"),
        {"none": 0, "commit": 1, "push": 2, "merge": 3, "completion": 4},
        "decision.gated_boundary",
    )
    state = _enum(
        value.get("authority_state"),
        {"not_required": 0, "unresolved": 1, "externally_satisfied": 2},
        "decision.authority_state",
    )
    owners = _string_list(value.get("required_owners"), "decision.required_owners")
    raw_evidence = value.get("review_evidence")
    evidence = None if raw_evidence is None else _parse_review_evidence(raw_evidence)
    if outcome["resolution_class"] != "authority_required":
        if (state, gated, owners, evidence) != ("not_required", "none", [], None):
            raise MurlocsError("only authority outcomes may carry authority decisions")
    elif owners != expected["required_owners"] or gated == "none":
        raise MurlocsError("outcome authority routing does not match findings")
    elif state == "unresolved" and evidence is not None:
        raise MurlocsError("unresolved authority cannot retain external review evidence")
    elif state == "externally_satisfied" and (
        evidence is None or not _external_review_is_current(outcome, evidence, owners)
    ):
        raise MurlocsError(
            "satisfied authority requires current matching integration review evidence"
        )
    elif state == "not_required":
        raise MurlocsError("authority outcome cannot omit its authority state")
    return {
        "task_authorization": cast(Any, task_authorization),
        "agent_acknowledgement": "not_recorded",
        "authority_state": cast(Any, state),
        "implementation": "may_continue",
        "gated_boundary": cast(Any, gated),
        "required_owners": owners,
        "review_evidence": evidence,
    }


def _parse_review_evidence(value: Any) -> OutcomeReviewEvidencePayload:
    if not isinstance(value, Mapping) or set(value) != {
        "adapter_id",
        "adapter_version",
        "session_id",
        "review_id",
        "reviewed_state_id",
        "owners",
    }:
        raise MurlocsError("external review evidence has unknown or missing fields")
    return {
        "adapter_id": _bounded_string(value.get("adapter_id"), "review.adapter_id", maximum=255),
        "adapter_version": _bounded_string(
            value.get("adapter_version"), "review.adapter_version", maximum=255
        ),
        "session_id": _bounded_string(value.get("session_id"), "review.session_id", maximum=255),
        "review_id": _bounded_string(value.get("review_id"), "review.review_id", maximum=255),
        "reviewed_state_id": _bounded_string(
            value.get("reviewed_state_id"), "review.reviewed_state_id", maximum=255
        ),
        "owners": _string_list(value.get("owners"), "review.owners"),
    }


def _external_review_is_current(
    outcome: Mapping[str, Any],
    evidence: OutcomeReviewEvidencePayload,
    required_owners: list[str],
) -> bool:
    correlation = outcome["correlation"]
    scope = correlation["token_scope"]
    return bool(
        correlation["token_source"] == "integration"
        and scope is not None
        and evidence["adapter_id"] == scope["adapter_id"]
        and evidence["adapter_version"] == scope["adapter_version"]
        and evidence["session_id"] == scope["session_id"]
        and evidence["reviewed_state_id"] == correlation["state_id"]
        and set(required_owners).issubset(evidence["owners"])
    )


def _string_list(value: Any, field: str, *, maximum: int = 1024) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise MurlocsError(f"{field} must be a bounded array")
    result = [_bounded_string(item, field, maximum=4096) for item in value]
    if len(set(result)) != len(result):
        raise MurlocsError(f"{field} must not contain duplicates")
    return sorted(result)


def _optional_token(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or TOKEN_ID.fullmatch(value) is None:
        raise MurlocsError(f"{field} must be an opaque integration token")
    return value


def _bounded_string(value: Any, field: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise MurlocsError(f"{field} must be a nonempty string of at most {maximum} characters")
    if "\0" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise MurlocsError(f"{field} contains an invalid Unicode scalar")
    return value


def _required_code(value: Any) -> str:
    if not isinstance(value, str) or DIAGNOSTIC_CODE.fullmatch(value) is None:
        raise MurlocsError("outcome diagnostic code is invalid")
    return value


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise MurlocsError(f"outcome {field} must be a boolean")
    return value


def _enum(value: Any, choices: Mapping[str, int], field: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise MurlocsError(f"unsupported outcome {field} {value!r}")
    return value


def _dominant(values: Any, ranks: Mapping[str, int], default: str) -> str:
    return max(values, key=ranks.__getitem__, default=default)


def _finding_key(item: OutcomeFindingPayload) -> tuple[Any, ...]:
    return (
        item["code"],
        item["message"],
        tuple(item["affected"]["scopes"]),
        tuple(item["affected"]["maps"]),
        tuple(item["affected"]["owners"]),
    )


def _finding_identity(item: OutcomeFindingPayload) -> tuple[Any, ...]:
    return (
        item["code"],
        tuple(item["affected"]["scopes"]),
        tuple(item["affected"]["maps"]),
        tuple(item["affected"]["owners"]),
    )


def _dedupe_findings(values: Any) -> list[OutcomeFindingPayload]:
    unique: dict[tuple[Any, ...], OutcomeFindingPayload] = {}
    for item in values:
        identity = _finding_identity(item)
        existing = unique.get(identity)
        if existing is not None and existing != item:
            raise MurlocsError(f"conflicting outcome finding {item['code']}")
        unique[identity] = item
    return sorted(unique.values(), key=_finding_key)


def _dedupe_actions(values: Any) -> list[OutcomeActionPayload]:
    unique: dict[str, OutcomeActionPayload] = {}
    for item in values:
        existing = unique.get(item["id"])
        if existing is None:
            unique[item["id"]] = item
            continue
        for field in ("operation", "effect", "authority"):
            if existing[field] != item[field]:
                raise MurlocsError(f"conflicting outcome action {item['id']}")
        for field in ("codes", "scopes", "maps", "owners"):
            existing["arguments"][field] = sorted(
                set(existing["arguments"][field]) | set(item["arguments"][field])
            )
    return [unique[key] for key in sorted(unique)]


def _aggregate_summary(findings: list[OutcomeFindingPayload]) -> str:
    if not findings:
        return "Murlocs outcome passed."
    required = sum(item["resolution_class"] == "authority_required" for item in findings)
    agent = sum(item["resolution_class"] == "agent_action" for item in findings)
    repairs = sum(item["resolution_class"] == "deterministic_repair" for item in findings)
    return f"Murlocs outcome: {required} authority, {agent} agent, {repairs} repair finding(s)."


def _impact_severity(
    scope: Mapping[str, Any], *, required: bool
) -> Literal["advisory", "important", "critical"]:
    if not required:
        return "advisory"
    severities = {str(item.get("severity", "")) for item in scope.get("invariants", ())}
    if "critical" in severities or "P0" in severities:
        return "critical"
    return "important"


def _check_resolution(
    code: str, repairable: bool
) -> Literal["deterministic_repair", "agent_action", "authority_required"]:
    if repairable:
        return "deterministic_repair"
    if code in _AUTHORITY_CHECK_CODES or code == "drift":
        return "authority_required"
    return "agent_action"


def _merge_correlation(
    values: list[OutcomeCorrelationPayload],
) -> OutcomeCorrelationPayload:
    first = values[0]
    shared = (
        first["correlation_id"],
        first["state_id"],
        first["token_source"],
        first["token_scope"],
    )
    if any(
        (
            item["correlation_id"],
            item["state_id"],
            item["token_source"],
            item["token_scope"],
        )
        != shared
        for item in values[1:]
    ):
        raise MurlocsError("outcome correlation and integration tokens do not match")
    dependencies = {
        item["dependency_id"] for item in values if item["dependency_id"] is not None
    }
    if len(dependencies) > 1:
        raise MurlocsError("outcome dependency tokens do not match")
    return {
        "correlation_id": first["correlation_id"],
        "state_id": first["state_id"],
        "dependency_id": next(iter(dependencies), None),
        "token_source": first["token_source"],
        "token_scope": first["token_scope"],
    }


def _check_code(code: str) -> str:
    return _stable_code(f"MURLOCS_CHECK_{code}")


def _stable_code(code: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]+", "_", code.upper()).strip("_")
    if not normalized.startswith("MURLOCS_"):
        normalized = f"MURLOCS_{normalized}"
    return normalized[:128]


def _outcome_code(resolution: ResolutionClass) -> str:
    return f"MURLOCS_OUTCOME_{resolution.upper()}"
