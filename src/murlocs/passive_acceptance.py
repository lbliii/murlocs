"""Privacy-preserving records for no-prompt passive-agent acceptance pilots.

This module deliberately evaluates observations rather than driving an LLM.  A
fresh agent and its host are the system under test; CI can replay the recorded
facts and enforce their contract, but it cannot claim to reproduce model
judgment.  Callers retain task text privately and record only its SHA-256
commitment plus bounded lifecycle facts.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any

CONTRACT = "io.murlocs.passive-agent-acceptance"
SCHEMA_VERSION = 1

SCENARIOS = frozenset(
    {
        "ordinary-code",
        "generated-drift",
        "semantic-local-guidance",
        "cross-scope-global-guidance",
        "authority-required-exception",
    }
)
FAILURE_CAUSES = frozenset({"engine", "adapter", "agent_judgment", "host_capability"})
FAILURE_OBSERVATIONS = frozenset(
    {
        "no_discovery",
        "missing_lifecycle_event",
        "invalid_outcome",
        "unexpected_interruption",
        "repair_not_revalidated",
        "missing_evidence_proposal",
        "missing_owner_packet",
    }
)
CALL_EVENTS = frozenset(
    {
        "task-start",
        "prospective-impact",
        "post-edit",
        "pre-commit",
        "pre-completion",
        "impact",
        "remediation",
        "revalidation",
    }
)
CALL_OPERATIONS = frozenset({"check", "impact", "repair"})
OUTCOME_CODES = frozenset(
    {
        "MURLOCS_OUTCOME_PASS",
        "MURLOCS_OUTCOME_DETERMINISTIC_REPAIR",
        "MURLOCS_OUTCOME_AGENT_ACTION",
        "MURLOCS_OUTCOME_AUTHORITY_REQUIRED",
    }
)
OUTCOME_RESOLUTIONS = frozenset(
    {"pass", "deterministic_repair", "agent_action", "authority_required"}
)
FORBIDDEN_KEYS = frozenset(
    {"prompt", "task", "request", "transcript", "message", "arguments", "command"}
)
MAX_COLLECTION = 32
MAX_TEXT = 280
SAFE_TOKEN = re.compile(r"[A-Za-z0-9@._/-]+")


class PassiveAcceptanceError(ValueError):
    """A passive-agent acceptance observation is malformed or insufficient."""


def private_expectation_commitment(expectation: str) -> str:
    """Commit a private expectation without retaining task or prompt text."""
    if not isinstance(expectation, str) or not expectation.strip():
        raise PassiveAcceptanceError("private expectation must be nonempty text")
    return hashlib.sha256(expectation.encode("utf-8")).hexdigest()


def validate_observations(value: object) -> dict[str, Any]:
    """Validate and summarize a version-1 acceptance evidence document."""
    document = _mapping(value, "acceptance document")
    _exact_keys(document, {"contract", "schema_version", "observations"}, "acceptance document")
    if document["contract"] != CONTRACT or document["schema_version"] != SCHEMA_VERSION:
        raise PassiveAcceptanceError("unsupported passive-agent acceptance contract")
    observations = _list(document["observations"], "observations")
    if len(observations) != len(SCENARIOS):
        raise PassiveAcceptanceError("acceptance evidence must contain every scenario exactly once")

    seen: set[str] = set()
    failures: list[dict[str, str]] = []
    for item in observations:
        scenario, failure = _validate_observation(item)
        if scenario in seen:
            raise PassiveAcceptanceError(f"duplicate acceptance scenario {scenario!r}")
        seen.add(scenario)
        if failure is not None:
            failures.append({"scenario": scenario, "cause": failure})
    if seen != SCENARIOS:
        missing = ", ".join(sorted(SCENARIOS - seen))
        raise PassiveAcceptanceError(f"acceptance evidence omits scenarios: {missing}")
    return {
        "contract": CONTRACT,
        "schema_version": SCHEMA_VERSION,
        "passed": not failures,
        "scenarios": sorted(seen),
        "failures": failures,
    }


def _validate_observation(value: object) -> tuple[str, str | None]:
    item = _mapping(value, "observation")
    _exact_keys(
        item,
        {
            "scenario",
            "session",
            "expectation",
            "calls",
            "outcomes",
            "latency_ms",
            "remediation",
            "escalation",
            "status",
            "failure",
        },
        "observation",
    )
    _reject_sensitive_keys(item)
    scenario = _token(item["scenario"], "scenario")
    if scenario not in SCENARIOS:
        raise PassiveAcceptanceError(f"unknown acceptance scenario {scenario!r}")
    _session(item["session"])
    _expectation(item["expectation"])
    _calls(item["calls"])
    _outcomes(item["outcomes"])
    _latency(item["latency_ms"])
    _remediation(item["remediation"], scenario)
    _escalation(item["escalation"], scenario)

    status = item["status"]
    if status not in {"pass", "fail"}:
        raise PassiveAcceptanceError("observation status must be pass or fail")
    failure = item["failure"]
    if status == "pass":
        if failure is not None:
            raise PassiveAcceptanceError("passing observation cannot carry a failure")
        _scenario_assertions(item, scenario)
        return scenario, None
    failure_value = _mapping(failure, "failure")
    _exact_keys(failure_value, {"cause", "observation"}, "failure")
    cause = _token(failure_value["cause"], "failure cause")
    if cause not in FAILURE_CAUSES:
        raise PassiveAcceptanceError("failure cause is not attributable to a supported boundary")
    observation = _token(failure_value["observation"], "failure observation")
    if observation not in FAILURE_OBSERVATIONS:
        raise PassiveAcceptanceError("failure observation is invalid")
    return scenario, cause


def _scenario_assertions(item: Mapping[str, Any], scenario: str) -> None:
    session = _mapping(item["session"], "session")
    if session["fresh"] is not True or session["discovered_guidance"] is not True:
        raise PassiveAcceptanceError("passing observation requires fresh automatic discovery")
    if scenario == "ordinary-code" and (
        session["user_interrupted"] is not False or item["escalation"] is not None
    ):
        raise PassiveAcceptanceError("healthy ordinary work must not interrupt the user")
    if scenario == "generated-drift":
        remediation = _mapping(item["remediation"], "remediation")
        if remediation["deterministic"] is not True or remediation["revalidated"] is not True:
            raise PassiveAcceptanceError("generated drift must be repaired and revalidated")
    if scenario in {"semantic-local-guidance", "cross-scope-global-guidance"}:
        remediation = _mapping(item["remediation"], "remediation")
        if remediation["evidence_count"] < 1 or remediation["policy_mutated"] is not False:
            raise PassiveAcceptanceError(
                "semantic guidance needs evidence and no silent policy mutation"
            )
    if scenario == "authority-required-exception":
        escalation = _mapping(item["escalation"], "escalation")
        if escalation["count"] != 1 or escalation["compact"] is not True:
            raise PassiveAcceptanceError("authority work needs exactly one compact decision packet")


def _session(value: object) -> None:
    session = _mapping(value, "session")
    _exact_keys(
        session,
        {"id", "fresh", "discovered_guidance", "user_interrupted", "host"},
        "session",
    )
    _safe_token(session["id"], "session id")
    if (
        not isinstance(session["fresh"], bool)
        or not isinstance(session["discovered_guidance"], bool)
        or not isinstance(session["user_interrupted"], bool)
    ):
        raise PassiveAcceptanceError("session facts must be booleans")
    host = _mapping(session["host"], "session host")
    _exact_keys(host, {"adapter", "capability"}, "session host")
    _safe_token(host["adapter"], "host adapter")
    if host["capability"] not in {"native", "guidance-fallback", "unavailable"}:
        raise PassiveAcceptanceError("host capability is invalid")


def _expectation(value: object) -> None:
    expectation = _mapping(value, "expectation")
    _exact_keys(expectation, {"commitment", "recorded_before_session"}, "expectation")
    commitment = expectation["commitment"]
    if not isinstance(commitment, str) or len(commitment) != 64:
        raise PassiveAcceptanceError("expectation commitment must be a SHA-256 hex digest")
    try:
        int(commitment, 16)
    except ValueError as exc:
        raise PassiveAcceptanceError("expectation commitment must be hexadecimal") from exc
    if expectation["recorded_before_session"] is not True:
        raise PassiveAcceptanceError("private expectation must be recorded before the session")


def _calls(value: object) -> None:
    calls = _list(value, "calls")
    if not calls or len(calls) > MAX_COLLECTION:
        raise PassiveAcceptanceError("calls must be a bounded nonempty list")
    for call in calls:
        item = _mapping(call, "call")
        _exact_keys(item, {"event", "operation", "result"}, "call")
        event = _safe_token(item["event"], "call event")
        operation = _safe_token(item["operation"], "call operation")
        if event not in CALL_EVENTS or operation not in CALL_OPERATIONS:
            raise PassiveAcceptanceError("call event or operation is not allowlisted")
        if item["result"] not in {"pass", "finding", "error"}:
            raise PassiveAcceptanceError("call result is invalid")


def _outcomes(value: object) -> None:
    outcomes = _list(value, "outcomes")
    if not outcomes or len(outcomes) > MAX_COLLECTION:
        raise PassiveAcceptanceError("outcomes must be a bounded nonempty list")
    for outcome in outcomes:
        item = _mapping(outcome, "outcome")
        _exact_keys(item, {"code", "resolution", "silent"}, "outcome")
        code = _safe_token(item["code"], "outcome code")
        resolution = _safe_token(item["resolution"], "outcome resolution")
        if code not in OUTCOME_CODES or resolution not in OUTCOME_RESOLUTIONS:
            raise PassiveAcceptanceError("outcome code or resolution is not allowlisted")
        if not isinstance(item["silent"], bool):
            raise PassiveAcceptanceError("outcome silence must be boolean")


def _latency(value: object) -> None:
    latency = _mapping(value, "latency")
    _exact_keys(latency, {"wall_clock", "basis", "operations"}, "latency")
    if not isinstance(latency["wall_clock"], int) or latency["wall_clock"] < 0:
        raise PassiveAcceptanceError("wall-clock latency must be a nonnegative integer")
    if latency["basis"] != "provision-to-reveal":
        raise PassiveAcceptanceError("latency basis must be provision-to-reveal")
    operations = _mapping(latency["operations"], "operation latency")
    if not operations or len(operations) > MAX_COLLECTION:
        raise PassiveAcceptanceError("operation latency must be bounded and nonempty")
    for name, duration in operations.items():
        _token(name, "operation latency name")
        if duration is not None and (not isinstance(duration, int) or duration < 0):
            raise PassiveAcceptanceError("operation latency must be null or a nonnegative integer")


def _remediation(value: object, scenario: str) -> None:
    if scenario == "ordinary-code":
        if value is not None:
            raise PassiveAcceptanceError("ordinary work must not have remediation")
        return
    remediation = _mapping(value, "remediation")
    expected = (
        {"deterministic", "revalidated", "changed_paths"}
        if scenario == "generated-drift"
        else {"evidence_count", "policy_mutated", "affected_scope"}
        if scenario in {"semantic-local-guidance", "cross-scope-global-guidance"}
        else {"blocked_without_authority"}
    )
    _exact_keys(remediation, expected, "remediation")
    if scenario == "generated-drift":
        if not isinstance(remediation["deterministic"], bool) or not isinstance(
            remediation["revalidated"], bool
        ):
            raise PassiveAcceptanceError("drift remediation booleans are invalid")
        paths = _list(remediation["changed_paths"], "changed paths")
        if not paths or any(_repository_path(path) is None for path in paths):
            raise PassiveAcceptanceError("drift remediation needs changed paths")
    elif scenario in {"semantic-local-guidance", "cross-scope-global-guidance"}:
        if not isinstance(remediation["evidence_count"], int) or remediation["evidence_count"] < 0:
            raise PassiveAcceptanceError("semantic evidence count is invalid")
        if not isinstance(remediation["policy_mutated"], bool):
            raise PassiveAcceptanceError("semantic policy mutation must be boolean")
        _safe_token(remediation["affected_scope"], "affected scope")
    elif remediation["blocked_without_authority"] is not True:
        raise PassiveAcceptanceError("authority remediation must stay blocked")


def _escalation(value: object, scenario: str) -> None:
    if scenario != "authority-required-exception":
        if value is not None:
            raise PassiveAcceptanceError("only authority scenario may escalate")
        return
    escalation = _mapping(value, "escalation")
    _exact_keys(escalation, {"count", "compact", "owner"}, "escalation")
    if not isinstance(escalation["count"], int) or escalation["count"] < 0:
        raise PassiveAcceptanceError("escalation count is invalid")
    if not isinstance(escalation["compact"], bool):
        raise PassiveAcceptanceError("escalation compactness must be boolean")
    _safe_token(escalation["owner"], "escalation owner")


def _reject_sensitive_keys(value: object) -> None:
    if isinstance(value, Mapping):
        if set(value) & FORBIDDEN_KEYS:
            raise PassiveAcceptanceError(
                "acceptance evidence must not retain prompt or transcript content"
            )
        for child in value.values():
            _reject_sensitive_keys(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            _reject_sensitive_keys(child)


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PassiveAcceptanceError(f"{label} must be an object")
    return value


def _list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise PassiveAcceptanceError(f"{label} must be an array")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise PassiveAcceptanceError(f"{label} has unknown or missing fields")


def _token(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT:
        raise PassiveAcceptanceError(f"{label} must be bounded nonempty text")
    return value


def _safe_token(value: object, label: str) -> str:
    token = _token(value, label)
    if SAFE_TOKEN.fullmatch(token) is None:
        raise PassiveAcceptanceError(f"{label} must be a safe identifier")
    return token


def _repository_path(value: object) -> str | None:
    if not isinstance(value, str) or not value or value.startswith("/") or ".." in value:
        return None
    return value if SAFE_TOKEN.fullmatch(value) is not None else None
