"""Privacy-preserving self-hosting dogfood tranche observations.

Issue #97 asks whether Murlocs supplies novel repository understanding, stays
silent during healthy work, creates noise, or gives an unfamiliar agent a clear
next action. This module is the offline boundary: CI validates a versioned
tranche sheet without retaining prompts or transcripts.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any, cast

CONTRACT = "io.murlocs.self-hosting-dogfood"
SCHEMA_VERSION = 1

TRANCHE_STATUSES = frozenset({"planned", "in-progress", "complete"})
CLASSIFICATIONS = frozenset({"useful-silence", "useful-intervention", "noise", "miss", "mixed"})
CAUSE_FAMILIES = frozenset(
    {
        "generated-guidance",
        "hook",
        "engine",
        "adapter",
        "agent-judgment",
        "packaging",
        "repository-process",
    }
)
TASK_THEMES = frozenset(
    {
        "ordinary-hook-cli",
        "installed-vs-active",
        "dry-run-noop",
        "hook-install-failure",
        "compact-outcomes",
        "authority-semantics",
        "ordinary-observer",
        "deterministic-repair",
    }
)
FORBIDDEN_KEYS = frozenset(
    {"prompt", "task", "request", "transcript", "message", "arguments", "command"}
)
MAX_COLLECTION = 64
MAX_TEXT = 280
MIN_TASKS = 5


class DogfoodError(ValueError):
    """A self-hosting dogfood tranche sheet is malformed or insufficient."""


def private_expectation_commitment(expectation: str) -> str:
    """Commit a private pre-task expectation without retaining its text."""
    if not isinstance(expectation, str) or not expectation.strip():
        raise DogfoodError("private expectation must be nonempty text")
    return hashlib.sha256(expectation.encode("utf-8")).hexdigest()


def validate_dogfood_sheet(value: object) -> dict[str, Any]:
    """Validate a version-1 self-hosting dogfood tranche sheet."""
    document = _mapping(value, "dogfood sheet")
    _exact_keys(
        document,
        {
            "contract",
            "schema_version",
            "tranche",
            "tasks",
            "interviews",
            "cross_check",
            "recommendations",
            "acceptance",
        },
        "dogfood sheet",
    )
    _reject_sensitive_keys(document)
    if document["contract"] != CONTRACT or document["schema_version"] != SCHEMA_VERSION:
        raise DogfoodError("unsupported self-hosting dogfood contract")

    tranche = _tranche(document["tranche"])
    tasks = _tasks(document["tasks"])
    interviews = _interviews(document["interviews"])
    cross_check = _cross_check(document["cross_check"])
    recommendations = _recommendations(document["recommendations"])
    acceptance = _acceptance(document["acceptance"], tranche, tasks)

    classifications = {item["classification"] for item in tasks}
    causes = {item["cause"] for item in tasks}
    themes = {item["theme"] for item in tasks}
    return {
        "contract": CONTRACT,
        "schema_version": SCHEMA_VERSION,
        "tranche_id": tranche["id"],
        "tranche_status": tranche["status"],
        "task_count": len(tasks),
        "task_ids": [item["id"] for item in tasks],
        "themes": sorted(themes),
        "classifications": sorted(classifications),
        "causes": sorted(causes),
        "fresh_sessions": sum(1 for item in tasks if item["fresh_session"]),
        "hooks_retained": tranche["hooks_retained"],
        "proceed_to_multi_repo_pilot": acceptance["proceed_to_multi_repo_pilot"],
        "telemetry_required": tranche["telemetry_required"],
        "recommendation_buckets": {key: list(recommendations[key]) for key in recommendations},
        "cross_check_keys": sorted(cross_check),
        "interviews_complete": interviews["blind_completed"] and interviews["revealed_completed"],
    }


def _tranche(value: object) -> dict[str, Any]:
    tranche = dict(_mapping(value, "tranche"))
    _exact_keys(
        tranche,
        {
            "id",
            "status",
            "started_on",
            "ended_on",
            "data_handling",
            "telemetry_required",
            "hooks_retained",
            "unresolved_false_blocker",
        },
        "tranche",
    )
    _safe_token(tranche["id"], "tranche id")
    if tranche["status"] not in TRANCHE_STATUSES:
        raise DogfoodError("tranche status is invalid")
    _optional_date(tranche["started_on"], "started_on")
    _optional_date(tranche["ended_on"], "ended_on")
    if tranche["data_handling"] != "repository-governed":
        raise DogfoodError("data handling must be repository-governed")
    if tranche["telemetry_required"] is not False:
        raise DogfoodError("dogfood must not require telemetry or a hosted dependency")
    if not isinstance(tranche["hooks_retained"], bool):
        raise DogfoodError("hooks_retained must be boolean")
    if not isinstance(tranche["unresolved_false_blocker"], bool):
        raise DogfoodError("unresolved_false_blocker must be boolean")
    return tranche


def _tasks(value: object) -> list[dict[str, Any]]:
    tasks = _list(value, "tasks")
    if len(tasks) < MIN_TASKS or len(tasks) > MAX_COLLECTION:
        raise DogfoodError("dogfood requires between five and sixty-four tasks")
    seen: set[str] = set()
    parsed: list[dict[str, Any]] = []
    for item in tasks:
        task = _task(item)
        if task["id"] in seen:
            raise DogfoodError(f"duplicate task id {task['id']!r}")
        seen.add(task["id"])
        parsed.append(task)
    if sum(1 for item in parsed if item["fresh_session"]) < MIN_TASKS:
        raise DogfoodError("at least five tasks must be fresh_session true")
    if any(item["murlocs_mentioned_in_prompt"] for item in parsed):
        raise DogfoodError("task prompts must not mention Murlocs")
    return parsed


def _task(value: object) -> dict[str, Any]:
    task = dict(_mapping(value, "task"))
    _exact_keys(
        task,
        {
            "id",
            "theme",
            "fresh_session",
            "murlocs_mentioned_in_prompt",
            "expectation_commitment",
            "classification",
            "cause",
            "evidence_codes",
            "retained_integration_intent",
        },
        "task",
    )
    _safe_token(task["id"], "task id")
    if task["theme"] not in TASK_THEMES:
        raise DogfoodError("task theme is invalid")
    if not isinstance(task["fresh_session"], bool):
        raise DogfoodError("fresh_session must be boolean")
    if task["murlocs_mentioned_in_prompt"] is not False:
        raise DogfoodError("murlocs_mentioned_in_prompt must be false")
    commitment = task["expectation_commitment"]
    if not isinstance(commitment, str) or len(commitment) != 64:
        raise DogfoodError("expectation_commitment must be a sha256 hex digest")
    if any(char not in "0123456789abcdef" for char in commitment):
        raise DogfoodError("expectation_commitment must be lowercase hex")
    if task["classification"] not in CLASSIFICATIONS:
        raise DogfoodError("classification is invalid")
    if task["cause"] not in CAUSE_FAMILIES:
        raise DogfoodError("cause family is invalid")
    codes = _list(task["evidence_codes"], "evidence codes")
    if not codes or len(codes) > MAX_COLLECTION:
        raise DogfoodError("evidence codes must be a bounded nonempty list")
    task["evidence_codes"] = [_safe_token(code, "evidence code") for code in codes]
    intent = task["retained_integration_intent"]
    if intent is not None and not isinstance(intent, bool):
        raise DogfoodError("retained_integration_intent must be boolean or null")
    return task


def _interviews(value: object) -> dict[str, Any]:
    interviews = dict(_mapping(value, "interviews"))
    _exact_keys(
        interviews,
        {
            "blind_completed",
            "revealed_completed",
            "discovery_codes",
            "novelty_codes",
            "noise_codes",
            "keep_enabled_codes",
        },
        "interviews",
    )
    for field in ("blind_completed", "revealed_completed"):
        if not isinstance(interviews[field], bool):
            raise DogfoodError(f"{field} must be boolean")
    for field in (
        "discovery_codes",
        "novelty_codes",
        "noise_codes",
        "keep_enabled_codes",
    ):
        codes = _list(interviews[field], field)
        if len(codes) > MAX_COLLECTION:
            raise DogfoodError(f"{field} exceeds bound")
        interviews[field] = [_safe_token(code, field) for code in codes]
    return interviews


def _cross_check(value: object) -> dict[str, bool]:
    cross_check = dict(_mapping(value, "cross_check"))
    expected = {
        "hook",
        "cli",
        "ci",
        "latency",
        "rerun",
        "repair",
        "escalation",
        "bypass",
    }
    _exact_keys(cross_check, expected, "cross_check")
    for key, item in cross_check.items():
        if not isinstance(item, bool):
            raise DogfoodError(f"cross_check.{key} must be boolean")
    return cast(dict[str, bool], cross_check)


def _recommendations(value: object) -> dict[str, list[str]]:
    recommendations = dict(_mapping(value, "recommendations"))
    expected = {
        "feed_62",
        "feed_63",
        "feed_65",
        "feed_67",
        "feed_68",
        "defer",
        "remove",
    }
    _exact_keys(recommendations, expected, "recommendations")
    parsed: dict[str, list[str]] = {}
    for key in expected:
        items = _list(recommendations[key], f"recommendations.{key}")
        if len(items) > MAX_COLLECTION:
            raise DogfoodError(f"recommendations.{key} exceeds bound")
        parsed[key] = [_safe_token(item, f"recommendations.{key} item") for item in items]
    return parsed


def _acceptance(
    value: object,
    tranche: Mapping[str, Any],
    tasks: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    acceptance = dict(_mapping(value, "acceptance"))
    _exact_keys(
        acceptance,
        {
            "deterministic_intervention_observed",
            "authority_required_journey_observed",
            "proceed_to_multi_repo_pilot",
            "execution_notes",
        },
        "acceptance",
    )
    for field in (
        "deterministic_intervention_observed",
        "authority_required_journey_observed",
        "proceed_to_multi_repo_pilot",
    ):
        if not isinstance(acceptance[field], bool):
            raise DogfoodError(f"{field} must be boolean")
    _safe_token(acceptance["execution_notes"], "execution notes")

    if tranche["status"] == "complete":
        if tranche["hooks_retained"] is not True:
            raise DogfoodError("complete tranche requires hooks_retained true")
        if tranche["unresolved_false_blocker"] is not False:
            raise DogfoodError("complete tranche cannot leave an unresolved false blocker")
        if acceptance["deterministic_intervention_observed"] is not True:
            raise DogfoodError("complete tranche requires a deterministic intervention")
        if acceptance["authority_required_journey_observed"] is not True:
            raise DogfoodError("complete tranche requires an authority-required journey")
        if len(tasks) < MIN_TASKS:
            raise DogfoodError("complete tranche requires at least five tasks")
    return acceptance


def _optional_date(value: object, label: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or len(value) != 10 or value[4] != "-" or value[7] != "-":
        raise DogfoodError(f"{label} must be YYYY-MM-DD or null")
    year, month, day = value.split("-")
    if not (year.isdigit() and month.isdigit() and day.isdigit()):
        raise DogfoodError(f"{label} must be YYYY-MM-DD or null")


def _reject_sensitive_keys(value: object) -> None:
    if isinstance(value, Mapping):
        if set(value) & FORBIDDEN_KEYS:
            raise DogfoodError("dogfood sheet must not retain prompt or transcript content")
        for child in value.values():
            _reject_sensitive_keys(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            _reject_sensitive_keys(child)


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DogfoodError(f"{label} must be an object")
    return cast(dict[str, Any], value)


def _list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise DogfoodError(f"{label} must be an array")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise DogfoodError(f"{label} has unknown or missing fields")


def _token(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT:
        raise DogfoodError(f"{label} must be bounded nonempty text")
    return value


def _safe_token(value: object, label: str) -> str:
    token = _token(value, label)
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789@._/-")
    if any(char not in allowed for char in token):
        raise DogfoodError(f"{label} must be a safe identifier")
    return token
