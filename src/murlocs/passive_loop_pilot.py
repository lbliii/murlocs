"""Multi-repository passive-loop pilot observation sheet.

Issue #68 needs a longitudinal pilot across materially different repositories.
This module is the offline, repository-governed boundary: CI validates sheet
shape, diversity, baselines, metrics, findings, rollback facts, and
recommendation buckets. It never claims live multi-week host success. Sheets
must set ``observation_status`` to ``simulated`` or ``executed`` per repository
and keep ``live_execution_complete`` honest.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

CONTRACT = "io.murlocs.passive-loop-pilot"
SCHEMA_VERSION = 1

PILOT_STATUSES = frozenset({"harness-only", "in-progress", "complete"})
SIZE_CLASSES = frozenset({"small", "medium", "large"})
SCOPE_TOPOLOGIES = frozenset({"shallow", "layered", "multi-domain"})
GUIDANCE_MATURITIES = frozenset({"bootstrap", "migrating", "mature"})
AGENT_WORKFLOWS = frozenset({"cli-hooks", "copilot", "claude", "generated-guidance", "mixed-host"})
FINDING_KINDS = frozenset({"useful-silence", "useful-intervention", "noise", "missed-opportunity"})
OBSERVATION_STATUSES = frozenset({"simulated", "executed"})
ROLLBACK_METHODS = frozenset(
    {"adapter-remove", "hook-disable", "hooks-uninstall", "manifest-absent"}
)
RECOMMENDATION_BUCKETS = ("graduate", "change", "remain_experimental", "remove")
RATE_FIELDS = (
    "false_positive_routing",
    "missed_findings",
    "deterministic_repair_rate",
    "agent_resolution_rate",
    "authority_escalation_rate",
)
BASELINE_FIELDS = (
    "guidance_drift_findings",
    "instruction_bytes",
    "agent_remediation_events",
    "routing_accuracy",
    "human_interventions",
)
FORBIDDEN_KEYS = frozenset(
    {"prompt", "task", "request", "transcript", "message", "arguments", "command"}
)
MAX_COLLECTION = 64
MAX_TEXT = 280
MAX_REPOS = 16
MIN_REPOS = 2


class PassiveLoopPilotError(ValueError):
    """A passive-loop pilot observation sheet is malformed or insufficient."""


def validate_pilot_sheet(value: object) -> dict[str, Any]:
    """Validate a version-1 multi-repository passive-loop pilot sheet."""
    document = _mapping(value, "pilot sheet")
    _exact_keys(
        document,
        {
            "contract",
            "schema_version",
            "pilot",
            "repositories",
            "recommendations",
            "acceptance",
        },
        "pilot sheet",
    )
    _reject_sensitive_keys(document)
    if document["contract"] != CONTRACT or document["schema_version"] != SCHEMA_VERSION:
        raise PassiveLoopPilotError("unsupported passive-loop pilot contract")

    pilot = _pilot(document["pilot"])
    repositories = _repositories(document["repositories"])
    recommendations = _recommendations(document["recommendations"])
    acceptance = _acceptance(document["acceptance"], repositories, pilot)

    diversity = _diversity_axes(repositories)
    finding_kinds = {
        finding["kind"] for repository in repositories for finding in repository["findings"]
    }
    missing_findings = sorted(FINDING_KINDS - finding_kinds)
    if missing_findings:
        raise PassiveLoopPilotError(
            "pilot findings must include each kind: " + ", ".join(sorted(FINDING_KINDS))
        )

    executed = sum(1 for item in repositories if item["observation_status"] == "executed")
    simulated = sum(1 for item in repositories if item["observation_status"] == "simulated")
    return {
        "contract": CONTRACT,
        "schema_version": SCHEMA_VERSION,
        "pilot_id": pilot["id"],
        "pilot_status": pilot["status"],
        "repository_count": len(repositories),
        "repository_ids": [item["id"] for item in repositories],
        "diversity_axes": sorted(diversity),
        "finding_kinds": sorted(finding_kinds),
        "executed_repositories": executed,
        "simulated_repositories": simulated,
        "live_execution_complete": acceptance["live_execution_complete"],
        "telemetry_required": pilot["telemetry_required"],
        "recommendation_buckets": {
            bucket: list(recommendations[bucket]) for bucket in RECOMMENDATION_BUCKETS
        },
    }


def _pilot(value: object) -> dict[str, Any]:
    pilot = dict(_mapping(value, "pilot"))
    _exact_keys(
        pilot,
        {
            "id",
            "status",
            "protocol_version",
            "started_on",
            "ended_on",
            "review_cadence",
            "data_handling",
            "telemetry_required",
            "rollback_conditions",
        },
        "pilot",
    )
    _safe_token(pilot["id"], "pilot id")
    if pilot["status"] not in PILOT_STATUSES:
        raise PassiveLoopPilotError("pilot status is invalid")
    if pilot["protocol_version"] != 1:
        raise PassiveLoopPilotError("pilot protocol_version must be 1")
    _optional_date(pilot["started_on"], "started_on")
    _optional_date(pilot["ended_on"], "ended_on")
    if pilot["review_cadence"] not in {"weekly", "biweekly", "milestone"}:
        raise PassiveLoopPilotError("review cadence is invalid")
    if pilot["data_handling"] != "repository-governed":
        raise PassiveLoopPilotError("data handling must be repository-governed")
    if pilot["telemetry_required"] is not False:
        raise PassiveLoopPilotError("pilot must not require telemetry or a hosted dependency")
    conditions = _list(pilot["rollback_conditions"], "rollback conditions")
    if not conditions or len(conditions) > MAX_COLLECTION:
        raise PassiveLoopPilotError("rollback conditions must be a bounded nonempty list")
    for condition in conditions:
        _safe_token(condition, "rollback condition")
    return pilot


def _repositories(value: object) -> list[dict[str, Any]]:
    repositories = _list(value, "repositories")
    if len(repositories) < MIN_REPOS or len(repositories) > MAX_REPOS:
        raise PassiveLoopPilotError("pilot requires between two and sixteen repositories")
    seen: set[str] = set()
    parsed: list[dict[str, Any]] = []
    for item in repositories:
        repository = _repository(item)
        if repository["id"] in seen:
            raise PassiveLoopPilotError(f"duplicate repository id {repository['id']!r}")
        seen.add(repository["id"])
        parsed.append(repository)
    diversity = _diversity_axes(parsed)
    required = {
        "size_class",
        "scope_topology",
        "guidance_maturity",
        "primary_agent_workflow",
    }
    if diversity != required:
        missing = ", ".join(sorted(required - diversity))
        raise PassiveLoopPilotError(
            "repositories must differ across size, topology, maturity, and workflow: "
            f"missing diversity in {missing}"
        )
    return parsed


def _repository(value: object) -> dict[str, Any]:
    repository = dict(_mapping(value, "repository"))
    _exact_keys(
        repository,
        {
            "id",
            "label",
            "profile",
            "baseline",
            "metrics",
            "findings",
            "rollback",
            "observation_status",
        },
        "repository",
    )
    _safe_token(repository["id"], "repository id")
    _safe_token(repository["label"], "repository label")
    repository["profile"] = _profile(repository["profile"])
    repository["baseline"] = _baseline(repository["baseline"])
    repository["metrics"] = _metrics(repository["metrics"])
    repository["findings"] = _findings(repository["findings"])
    repository["rollback"] = _rollback(repository["rollback"])
    if repository["observation_status"] not in OBSERVATION_STATUSES:
        raise PassiveLoopPilotError("observation_status must be simulated or executed")
    return repository


def _profile(value: object) -> dict[str, Any]:
    profile = dict(_mapping(value, "profile"))
    _exact_keys(
        profile,
        {
            "size_class",
            "scope_topology",
            "guidance_maturity",
            "primary_agent_workflow",
        },
        "profile",
    )
    if profile["size_class"] not in SIZE_CLASSES:
        raise PassiveLoopPilotError("size_class is invalid")
    if profile["scope_topology"] not in SCOPE_TOPOLOGIES:
        raise PassiveLoopPilotError("scope_topology is invalid")
    if profile["guidance_maturity"] not in GUIDANCE_MATURITIES:
        raise PassiveLoopPilotError("guidance_maturity is invalid")
    if profile["primary_agent_workflow"] not in AGENT_WORKFLOWS:
        raise PassiveLoopPilotError("primary_agent_workflow is invalid")
    return profile


def _baseline(value: object) -> dict[str, Any]:
    baseline = dict(_mapping(value, "baseline"))
    _exact_keys(baseline, set(BASELINE_FIELDS), "baseline")
    for field in (
        "guidance_drift_findings",
        "instruction_bytes",
        "agent_remediation_events",
        "human_interventions",
    ):
        _nonnegative_int(baseline[field], field)
    _optional_rate(baseline["routing_accuracy"], "routing_accuracy")
    return baseline


def _metrics(value: object) -> dict[str, Any]:
    metrics = dict(_mapping(value, "metrics"))
    _exact_keys(
        metrics,
        {
            "hot_path_latency_ms",
            "false_positive_routing",
            "missed_findings",
            "deterministic_repair_rate",
            "agent_resolution_rate",
            "authority_escalation_rate",
            "retained_integration",
            "operator_feedback",
        },
        "metrics",
    )
    latency = dict(_mapping(metrics["hot_path_latency_ms"], "hot_path_latency_ms"))
    _exact_keys(latency, {"median", "p95", "basis"}, "hot_path_latency_ms")
    for field in ("median", "p95"):
        sample = latency[field]
        if sample is not None and (not isinstance(sample, int) or sample < 0):
            raise PassiveLoopPilotError(f"{field} latency must be null or a nonnegative integer")
    if latency["basis"] not in {"measured", "unavailable", "simulated"}:
        raise PassiveLoopPilotError("latency basis is invalid")
    if latency["basis"] == "unavailable" and (
        latency["median"] is not None or latency["p95"] is not None
    ):
        raise PassiveLoopPilotError("unavailable latency cannot carry numeric samples")
    metrics["hot_path_latency_ms"] = latency
    for field in RATE_FIELDS:
        _optional_rate(metrics[field], field)
    if not isinstance(metrics["retained_integration"], bool):
        raise PassiveLoopPilotError("retained_integration must be boolean")
    feedback = dict(_mapping(metrics["operator_feedback"], "operator_feedback"))
    _exact_keys(feedback, {"responses", "summary_codes"}, "operator_feedback")
    _nonnegative_int(feedback["responses"], "operator feedback responses")
    codes = _list(feedback["summary_codes"], "operator feedback summary codes")
    if len(codes) > MAX_COLLECTION:
        raise PassiveLoopPilotError("operator feedback summary codes exceed bound")
    for code in codes:
        _safe_token(code, "operator feedback summary code")
    metrics["operator_feedback"] = feedback
    return metrics


def _findings(value: object) -> list[dict[str, Any]]:
    findings = _list(value, "findings")
    if not findings or len(findings) > MAX_COLLECTION:
        raise PassiveLoopPilotError("findings must be a bounded nonempty list")
    parsed: list[dict[str, Any]] = []
    for item in findings:
        finding = dict(_mapping(item, "finding"))
        _exact_keys(finding, {"kind", "example_id", "note"}, "finding")
        if finding["kind"] not in FINDING_KINDS:
            raise PassiveLoopPilotError("finding kind is invalid")
        _safe_token(finding["example_id"], "finding example id")
        _safe_token(finding["note"], "finding note")
        parsed.append(finding)
    return parsed


def _rollback(value: object) -> dict[str, Any]:
    rollback = dict(_mapping(value, "rollback"))
    _exact_keys(rollback, {"exercised", "safe", "method"}, "rollback")
    if not isinstance(rollback["exercised"], bool) or not isinstance(rollback["safe"], bool):
        raise PassiveLoopPilotError("rollback booleans are invalid")
    if rollback["method"] not in ROLLBACK_METHODS:
        raise PassiveLoopPilotError("rollback method is invalid")
    if rollback["exercised"] is True and rollback["safe"] is not True:
        raise PassiveLoopPilotError("exercised rollback must be recorded as safe")
    return rollback


def _recommendations(value: object) -> dict[str, list[str]]:
    recommendations = dict(_mapping(value, "recommendations"))
    _exact_keys(recommendations, set(RECOMMENDATION_BUCKETS), "recommendations")
    parsed: dict[str, list[str]] = {}
    for bucket in RECOMMENDATION_BUCKETS:
        items = _list(recommendations[bucket], f"recommendations.{bucket}")
        if len(items) > MAX_COLLECTION:
            raise PassiveLoopPilotError(f"recommendations.{bucket} exceeds bound")
        tokens = [_safe_token(item, f"recommendations.{bucket} item") for item in items]
        parsed[bucket] = tokens
    return parsed


def _acceptance(
    value: object, repositories: Sequence[Mapping[str, Any]], pilot: Mapping[str, Any]
) -> dict[str, Any]:
    acceptance = dict(_mapping(value, "acceptance"))
    _exact_keys(
        acceptance,
        {
            "journeys_still_pass",
            "integration_retained",
            "live_execution_complete",
            "execution_notes",
        },
        "acceptance",
    )
    for field in ("journeys_still_pass", "integration_retained", "live_execution_complete"):
        if acceptance[field] is not None and not isinstance(acceptance[field], bool):
            raise PassiveLoopPilotError(f"{field} must be boolean or null")
    _safe_token(acceptance["execution_notes"], "execution notes")

    all_executed = all(item["observation_status"] == "executed" for item in repositories)
    if acceptance["live_execution_complete"] is True and not all_executed:
        raise PassiveLoopPilotError(
            "live_execution_complete cannot be true while any repository is simulated"
        )
    if pilot["status"] == "complete" and acceptance["live_execution_complete"] is not True:
        raise PassiveLoopPilotError("complete pilot status requires live_execution_complete")
    if pilot["status"] == "harness-only" and acceptance["live_execution_complete"] is not False:
        raise PassiveLoopPilotError("harness-only pilots must set live_execution_complete false")
    return acceptance


def _diversity_axes(repositories: Sequence[Mapping[str, Any]]) -> set[str]:
    axes = {
        "size_class": {item["profile"]["size_class"] for item in repositories},
        "scope_topology": {item["profile"]["scope_topology"] for item in repositories},
        "guidance_maturity": {item["profile"]["guidance_maturity"] for item in repositories},
        "primary_agent_workflow": {
            item["profile"]["primary_agent_workflow"] for item in repositories
        },
    }
    return {name for name, values in axes.items() if len(values) >= 2}


def _optional_date(value: object, label: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or len(value) != 10 or value[4] != "-" or value[7] != "-":
        raise PassiveLoopPilotError(f"{label} must be YYYY-MM-DD or null")
    year, month, day = value.split("-")
    if not (year.isdigit() and month.isdigit() and day.isdigit()):
        raise PassiveLoopPilotError(f"{label} must be YYYY-MM-DD or null")


def _optional_rate(value: object, label: str) -> None:
    if value is None:
        return
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise PassiveLoopPilotError(f"{label} must be null or a number in [0, 1]")
    if value < 0 or value > 1:
        raise PassiveLoopPilotError(f"{label} must be null or a number in [0, 1]")


def _nonnegative_int(value: object, label: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise PassiveLoopPilotError(f"{label} must be a nonnegative integer")


def _reject_sensitive_keys(value: object) -> None:
    if isinstance(value, Mapping):
        if set(value) & FORBIDDEN_KEYS:
            raise PassiveLoopPilotError("pilot sheet must not retain prompt or transcript content")
        for child in value.values():
            _reject_sensitive_keys(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            _reject_sensitive_keys(child)


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PassiveLoopPilotError(f"{label} must be an object")
    return cast(dict[str, Any], value)


def _list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise PassiveLoopPilotError(f"{label} must be an array")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise PassiveLoopPilotError(f"{label} has unknown or missing fields")


def _token(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT:
        raise PassiveLoopPilotError(f"{label} must be bounded nonempty text")
    return value


def _safe_token(value: object, label: str) -> str:
    token = _token(value, label)
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789@._/-")
    if any(char not in allowed for char in token):
        raise PassiveLoopPilotError(f"{label} must be a safe identifier")
    return token
