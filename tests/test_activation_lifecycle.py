from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from murlocs.errors import MurlocsError
from murlocs.outcome import parse_outcome

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "tests/fixtures/activation-lifecycle/v1/conformance.json"
CONTRACT_DOC = ROOT / "docs/activation-lifecycle.md"
STATE_ID = re.compile(r"sha256:[0-9a-f]{64}")
TOKEN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}")
CORRELATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")

EVENT_OPERATIONS = {
    "task-start": ["check"],
    "prospective-impact": ["impact"],
    "post-edit": ["check", "impact"],
    "pre-commit": ["check", "impact"],
    "pre-completion": ["check", "impact"],
}
EXECUTION_CODES = {
    "completed": "MURLOCS_ACTIVATION_OK",
    "not_applicable": "MURLOCS_ACTIVATION_ABSENT",
    "unavailable": "MURLOCS_ACTIVATION_UNAVAILABLE",
    "timeout": "MURLOCS_ACTIVATION_TIMEOUT",
    "invalid": "MURLOCS_ACTIVATION_INVALID",
    "stale": "MURLOCS_ACTIVATION_STALE",
}
VIEWS = {"worktree", "index", "commit", "filesystem"}
FALLBACKS = {"generated-guidance", "git-hook", "ci"}
ENFORCEMENT = {"enforcing", "prompt-mediated"}
ACTION_EFFECTS = {"read_repository", "request_authority"}
GIT_OBJECT_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
SNAPSHOT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
FULL_GIT_REF = re.compile(r"refs/(?:heads|tags|remotes)/[A-Za-z0-9][A-Za-z0-9._/-]*")


class ContractViolation(ValueError):
    """A checked-in activation fixture violates the normative v1 contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractViolation(message)


def reject_duplicate_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractViolation(f"duplicate JSON member: {key}")
        result[key] = value
    return result


def parse_json_strict(raw: str) -> Any:
    return json.loads(raw, object_pairs_hook=reject_duplicate_members)


def load_fixture() -> dict[str, Any]:
    return parse_json_strict(FIXTURE.read_text(encoding="utf-8"))


def by_id(data: dict[str, Any], fixture_id: str) -> dict[str, Any]:
    return next(item for item in data["cases"] if item["id"] == fixture_id)


def keys_below(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(keys_below(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(keys_below(item) for item in value))
    return set()


def valid_scalar(value: Any) -> bool:
    return isinstance(value, str) and "\0" not in value and all(
        not 0xD800 <= ord(char) <= 0xDFFF for char in value
    )


def valid_root(root: Any) -> bool:
    if not isinstance(root, dict):
        return False
    kind = root.get("format")
    segments = root.get("segments")
    if (
        not isinstance(segments, list)
        or any(not valid_scalar(segment) or not segment for segment in segments)
        or any(segment in {".", ".."} or "/" in segment or "\\" in segment for segment in segments)
    ):
        return False
    if kind == "posix":
        return set(root) == {"format", "segments"}
    if kind == "windows-drive":
        drive = root.get("drive")
        return set(root) == {"format", "drive", "segments"} and bool(
            isinstance(drive, str) and re.fullmatch(r"[A-Z]", drive)
        )
    if kind == "windows-unc":
        return (
            set(root) == {"format", "server", "share", "segments"}
            and valid_scalar(root.get("server"))
            and bool(root["server"])
            and root["server"] not in {".", ".."}
            and not any(separator in root["server"] for separator in "/\\")
            and valid_scalar(root.get("share"))
            and bool(root["share"])
            and root["share"] not in {".", ".."}
            and not any(separator in root["share"] for separator in "/\\")
        )
    return False


def valid_baseline(baseline: Any) -> bool:
    if not isinstance(baseline, dict):
        return False
    kind = baseline.get("kind")
    if kind == "git-head":
        return set(baseline) == {"kind"}
    if kind == "git-ref":
        return set(baseline) == {"kind", "name"} and bool(
            isinstance(baseline.get("name"), str) and FULL_GIT_REF.fullmatch(baseline["name"])
        )
    if kind == "git-oid":
        object_format = baseline.get("object_format")
        oid = baseline.get("oid")
        length = 40 if object_format == "sha1" else 64 if object_format == "sha256" else 0
        return (
            set(baseline) == {"kind", "object_format", "oid"}
            and isinstance(oid, str)
            and len(oid) == length
            and GIT_OBJECT_ID.fullmatch(oid) is not None
        )
    if kind == "adapter-snapshot":
        value = baseline.get("value")
        return (
            set(baseline) == {"kind", "value"}
            and isinstance(value, str)
            and SNAPSHOT_ID.fullmatch(value) is not None
        )
    return False


def valid_baseline_resolution(baseline: dict[str, Any], resolution: Any) -> bool:
    if baseline["kind"] == "adapter-snapshot":
        return resolution is None
    if not isinstance(resolution, dict) or set(resolution) != {"object_format", "commit_oid"}:
        return False
    object_format = resolution.get("object_format")
    oid = resolution.get("commit_oid")
    length = 40 if object_format == "sha1" else 64 if object_format == "sha256" else 0
    if not isinstance(oid, str) or len(oid) != length or GIT_OBJECT_ID.fullmatch(oid) is None:
        return False
    return baseline["kind"] != "git-oid" or (
        baseline["object_format"] == object_format and baseline["oid"] == oid
    )


def request_errors(request: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    allowed = {
        "contract",
        "schema_version",
        "event",
        "correlation_id",
        "paths",
        "baseline",
        "deadline_ms",
        "enforcement",
    }
    errors.extend(sorted(set(request) - allowed))
    if request.get("contract") != "io.murlocs.activation":
        errors.append("contract")
    if request.get("schema_version") != 1:
        errors.append("schema_version")
    event = request.get("event")
    if event not in EVENT_OPERATIONS:
        errors.append("event")
    correlation = request.get("correlation_id")
    if not isinstance(correlation, str) or not CORRELATION_ID.fullmatch(correlation):
        errors.append("correlation_id")

    paths = request.get("paths")
    paths_required = event in {"post-edit", "pre-commit", "pre-completion"}
    if (event == "task-start" and "paths" in request) or (
        (paths_required or event == "prospective-impact" and "paths" in request)
        and (
            not isinstance(paths, list)
            or not paths
            or any(not isinstance(path, str) for path in paths)
        )
    ):
        errors.append("paths")
    elif isinstance(paths, list):
        if len(paths) != len(set(paths)):
            errors.append("paths")
        for path in paths:
            candidate = PurePosixPath(path)
            if (
                not valid_scalar(path)
                or not path
                or candidate.is_absolute()
                or candidate.as_posix() != path
                or any(part in {"", ".", ".."} for part in candidate.parts)
            ):
                errors.append("paths")
                break

    baseline = request.get("baseline")
    if baseline is not None and (event != "prospective-impact" or not valid_baseline(baseline)):
        errors.append("baseline")
    if event == "prospective-impact" and "paths" not in request and baseline is None:
        errors.append("impact_inputs")

    deadline = request.get("deadline_ms")
    if deadline is not None and (
        isinstance(deadline, bool) or not isinstance(deadline, int) or deadline <= 0
    ):
        errors.append("deadline_ms")
    enforcement = request.get("enforcement")
    if (event == "pre-commit" or enforcement is not None) and enforcement not in ENFORCEMENT:
        errors.append("enforcement")
    return errors


def validate_host_context(context: Any, event: str, baseline: Any) -> None:
    require(isinstance(context, dict), "missing host context")
    allowed = {
        "root",
        "token_scope",
        "manifest",
        "manifest_identity",
        "view",
        "state_id",
        "cache_offer",
        "baseline_resolution",
        "impact_dependency_id",
    }
    require(set(context) <= allowed, "host context shape")
    require(valid_root(context.get("root")), "host root")
    token_scope = context.get("token_scope")
    require(
        isinstance(token_scope, dict)
        and set(token_scope) == {"adapter_id", "adapter_version", "session_id"}
        and all(
            isinstance(value, str) and TOKEN_ID.fullmatch(value) is not None
            for value in token_scope.values()
        ),
        "host token scope",
    )
    require(context.get("manifest") == ".murlocs/manifest.toml", "host manifest")
    view = context.get("view")
    require(
        view in VIEWS
        and (event != "pre-commit" or view == "index")
        and (event == "pre-commit" or view != "index"),
        "host view",
    )
    state_id = context.get("state_id")
    require(
        isinstance(state_id, str) and TOKEN_ID.fullmatch(state_id) is not None,
        "host state",
    )
    manifest_identity = context.get("manifest_identity")
    require(
        manifest_identity is None
        or isinstance(manifest_identity, str)
        and TOKEN_ID.fullmatch(manifest_identity) is not None,
        "host manifest identity",
    )
    if baseline is not None:
        require(
            valid_baseline_resolution(baseline, context.get("baseline_resolution")),
            "host baseline resolution",
        )
    else:
        require("baseline_resolution" not in context, "unexpected baseline resolution")
    dependency_id = context.get("impact_dependency_id")
    require(
        "impact_dependency_id" not in context
        or "impact" in EVENT_OPERATIONS[event]
        and isinstance(dependency_id, str)
        and TOKEN_ID.fullmatch(dependency_id) is not None,
        "host impact dependency",
    )
    offered = context.get("cache_offer")
    require(
        offered is None
        or isinstance(offered, dict)
        and set(offered) == {"cache_id", "proof"}
        and isinstance(offered.get("cache_id"), str)
        and TOKEN_ID.fullmatch(offered["cache_id"]) is not None
        and isinstance(offered.get("proof"), dict),
        "host cache offer",
    )


def normalized_operation_inputs(request: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    if "paths" in request:
        inputs["paths"] = sorted(request["paths"])
    baseline = request.get("baseline")
    if baseline is not None:
        if baseline["kind"] == "adapter-snapshot":
            inputs["baseline_identity"] = {"snapshot_value": baseline["value"]}
        else:
            inputs["baseline_identity"] = context["baseline_resolution"]
    return inputs


def cache_proof_matches(
    proof: dict[str, Any], request: dict[str, Any], context: dict[str, Any]
) -> bool:
    expected_fields = {
        "contract_version",
        "adapter_id",
        "adapter_version",
        "session_id",
        "event",
        "operations",
        "operation_inputs",
        "manifest_identity",
        "state_id",
    }
    scope = context["token_scope"]
    expected = {
        "contract_version": "1",
        **scope,
        "event": request["event"],
        "operations": EVENT_OPERATIONS[request["event"]],
        "operation_inputs": normalized_operation_inputs(request, context),
        "manifest_identity": context.get("manifest_identity"),
        "state_id": context["state_id"],
    }
    if "impact_dependency_id" in context:
        expected_fields.add("impact_dependency_id")
        expected["impact_dependency_id"] = context["impact_dependency_id"]
    manifest_identity = context.get("manifest_identity")
    return (
        isinstance(manifest_identity, str)
        and TOKEN_ID.fullmatch(manifest_identity) is not None
        and set(proof) == expected_fields
        and proof == expected
    )


def validate_case(data: dict[str, Any], case: dict[str, Any]) -> None:
    driver_request = case["request"]
    host_context = driver_request.get("host_context")
    request = {key: value for key, value in driver_request.items() if key != "host_context"}
    response = case["response"]
    errors = request_errors(request)
    expected_valid = case.get("request_valid", True)
    if expected_valid:
        require(not errors, f"{case['id']}: invalid request fields: {errors}")
    else:
        require(errors == [case["request_error"]], f"{case['id']}: wrong request error")

    validate_host_context(host_context, request["event"], request.get("baseline"))

    for field in ("contract", "schema_version", "event", "correlation_id"):
        require(response.get(field) == request.get(field), f"{case['id']}: identity mismatch")
    response_repo = response["repository"]
    for field in ("root", "token_scope", "manifest", "view", "state_id"):
        require(
            response_repo.get(field) == host_context.get(field),
            f"{case['id']}: repository {field} mismatch",
        )

    execution = response["execution"]
    status = execution.get("status")
    require(status in EXECUTION_CODES, f"{case['id']}: invalid execution status")
    require(
        execution.get("code") == EXECUTION_CODES[status],
        f"{case['id']}: execution code mismatch",
    )
    if expected_valid:
        require(status != "invalid", f"{case['id']}: valid request reported invalid")
    else:
        require(status == "invalid", f"{case['id']}: invalid request was accepted")

    blocking = response_repo.get("blocking")
    if status == "completed":
        require(isinstance(blocking, bool), f"{case['id']}: completed blocking is not boolean")
    else:
        require(blocking is None, f"{case['id']}: unassessed blocking must be null")

    operations = response.get("operations")
    require(isinstance(operations, list), f"{case['id']}: operations must be a list")
    if status == "completed":
        required = EVENT_OPERATIONS[request["event"]]
        require(
            [receipt.get("operation") for receipt in operations] == required,
            f"{case['id']}: required operation order mismatch",
        )
    else:
        require(operations == [], f"{case['id']}: discarded execution retained receipts")

    outcome = response.get("outcome")
    require(
        outcome is None or isinstance(outcome, dict),
        f"{case['id']}: outcome must be null, omitted, or an object",
    )
    parsed_outcome = None
    if isinstance(outcome, dict) and outcome.get("contract") == "io.murlocs.outcome":
        try:
            parsed_outcome = parse_outcome(outcome)
        except MurlocsError as exc:
            raise ContractViolation(f"{case['id']}: invalid outcome: {exc}") from exc
        correlation = parsed_outcome["correlation"]
        require(
            correlation["correlation_id"] == response["correlation_id"],
            f"{case['id']}: outcome correlation mismatch",
        )
        require(
            correlation["state_id"] == response_repo["state_id"],
            f"{case['id']}: outcome state mismatch",
        )
        require(
            correlation["token_scope"] == response_repo["token_scope"],
            f"{case['id']}: outcome token scope mismatch",
        )
        receipt_operations = [receipt["operation"] for receipt in operations]
        source_operation = parsed_outcome["source"]["operation"]
        provenance_operations = {
            finding["provenance"]["operation"]
            for finding in parsed_outcome["findings"]
        }
        if source_operation == "aggregate":
            require(
                len(receipt_operations) > 1,
                f"{case['id']}: aggregate outcome requires multiple receipts",
            )
            require(
                provenance_operations.issubset(set(receipt_operations)),
                f"{case['id']}: aggregate provenance lacks a receipt",
            )
        else:
            require(
                source_operation in receipt_operations,
                f"{case['id']}: outcome operation lacks a receipt",
            )
            require(
                provenance_operations.issubset({source_operation}),
                f"{case['id']}: outcome provenance mismatches its receipt",
            )
        require(
            parsed_outcome["source"]["exit_code"]
            == max(receipt["exit_code"] for receipt in operations),
            f"{case['id']}: outcome exit does not match receipts",
        )
        if "impact" in receipt_operations and source_operation in {"impact", "aggregate"}:
            require(
                correlation["dependency_id"]
                == host_context.get("impact_dependency_id"),
                f"{case['id']}: outcome dependency mismatch",
            )
    require(response.get("writes") == [], f"{case['id']}: lifecycle attempted a write")
    command_scan = (
        {key: value for key, value in response.items() if key != "outcome"}
        if parsed_outcome is not None
        else response
    )
    require(
        not {"command", "argv", "shell"}.intersection(keys_below(command_scan)),
        f"{case['id']}: opaque command field",
    )

    dependency_present = "impact_dependency_id" in host_context
    observed_dependency_present = "observed_dependency_id" in execution
    if status == "completed":
        require(
            dependency_present == ("impact" in EVENT_OPERATIONS[request["event"]]),
            f"{case['id']}: impact dependency does not match executed operations",
        )
    elif status == "stale":
        require(
            dependency_present == observed_dependency_present,
            f"{case['id']}: stale dependency does not match attempted impact",
        )
    else:
        require(
            not dependency_present,
            f"{case['id']}: dependency supplied without an impact operation",
        )

    offered = host_context.get("cache_offer")
    if offered is not None:
        require(
            ("impact_dependency_id" in offered["proof"]) == dependency_present,
            f"{case['id']}: cache proof dependency does not match impact operation",
        )

    for receipt in operations:
        operation = receipt.get("operation")
        require(operation in data["operation_allowlist"], f"{case['id']}: unknown operation")
        require(
            receipt.get("state_before") == host_context["state_id"]
            and receipt.get("state_after") == host_context["state_id"],
            f"{case['id']}: receipt is not state-bound",
        )
        if operation == "impact":
            dependency_id = host_context.get("impact_dependency_id")
            require(
                dependency_id is not None
                and receipt.get("dependency_before_id") == dependency_id
                and receipt.get("dependency_after_id") == dependency_id,
                f"{case['id']}: impact receipt is not dependency-bound",
            )
        else:
            require(
                "dependency_before_id" not in receipt and "dependency_after_id" not in receipt,
                f"{case['id']}: check performed an impact dependency probe",
            )
        require(
            isinstance(receipt.get("output_sha256"), str)
            and STATE_ID.fullmatch(receipt["output_sha256"]) is not None,
            f"{case['id']}: invalid output digest",
        )
        exit_code = receipt.get("exit_code")
        if operation == "impact":
            require(exit_code == 0, f"{case['id']}: impact exit is inconsistent")
        else:
            require(exit_code in {0, 1}, f"{case['id']}: check exit is inconsistent")
    if status == "completed":
        expected_blocking = any(
            receipt["operation"] == "check" and receipt["exit_code"] == 1 for receipt in operations
        )
        require(blocking is expected_blocking, f"{case['id']}: blocking/exit mismatch")

    if status == "stale":
        observed_state = execution.get("observed_state_id")
        observed_dependency = execution.get("observed_dependency_id")
        require(
            (
                isinstance(observed_state, str)
                and TOKEN_ID.fullmatch(observed_state) is not None
                and observed_state != host_context["state_id"]
            )
            or (
                "impact" in EVENT_OPERATIONS[request["event"]]
                and isinstance(host_context.get("impact_dependency_id"), str)
                and isinstance(observed_dependency, str)
                and TOKEN_ID.fullmatch(observed_dependency) is not None
                and observed_dependency != host_context.get("impact_dependency_id")
            ),
            f"{case['id']}: stale is not tied to a state or dependency mutation",
        )
    else:
        require("observed_state_id" not in execution, f"{case['id']}: unexpected observed state")
        require(
            "observed_dependency_id" not in execution,
            f"{case['id']}: unexpected observed dependency",
        )

    cache = response["cache"]
    decision = cache.get("decision")
    require(decision in {"miss", "hit", "rejected", "forbidden"}, "invalid cache decision")
    proof_matches = offered is not None and cache_proof_matches(
        offered["proof"], request, host_context
    )
    if request["event"] == "pre-completion":
        require(decision == "forbidden", f"{case['id']}: completion cache was not forbidden")
    elif offered is not None:
        require(
            decision == ("hit" if proof_matches else "rejected"),
            f"{case['id']}: cache proof decision mismatch",
        )
        if decision == "rejected":
            require(status == "completed", f"{case['id']}: cache rejection skipped fresh execution")
    else:
        require(decision == "miss", f"{case['id']}: unoffered cache did not miss")
    if decision in {"hit", "rejected"}:
        require(
            cache.get("cache_id") == offered.get("cache_id"),
            f"{case['id']}: cache identity mismatch",
        )

    fallbacks = response.get("fallback")
    require(isinstance(fallbacks, list), f"{case['id']}: fallbacks must be a list")
    require(len(fallbacks) == len(set(fallbacks)), f"{case['id']}: duplicate fallback")
    require(set(fallbacks) <= FALLBACKS, f"{case['id']}: invented fallback")
    actions = response.get("next_actions")
    require(isinstance(actions, list), f"{case['id']}: actions must be a list")
    for action in actions:
        require(
            set(action) == {"operation", "arguments", "effect", "authority"},
            f"{case['id']}: action shape mismatch",
        )
        require(
            action["operation"] in data["next_action_allowlist"],
            f"{case['id']}: unknown action",
        )
        require(isinstance(action["arguments"], dict), f"{case['id']}: action arguments")
        require(action["effect"] in ACTION_EFFECTS, f"{case['id']}: forbidden action effect")
        require(
            action["authority"] in {"integration", "agent", "human"},
            f"{case['id']}: unknown action authority",
        )
        if action["operation"] == "use_fallback":
            require(
                action["arguments"].get("fallback") in fallbacks,
                f"{case['id']}: action names unavailable fallback",
            )

    if request["event"] == "pre-commit" and status == "timeout":
        require(request.get("enforcement") == "enforcing", "pre-commit timeout not enforcing")
        require(fallbacks == [], "pre-commit timeout implies an unauthorized bypass")
        require(
            actions and actions[0]["operation"] == "retry",
            "pre-commit timeout must keep the gate closed and offer retry",
        )
    if status == "completed" and blocking is False:
        require(response.get("silent") is True, f"{case['id']}: healthy result is noisy")
    require(isinstance(response.get("summary"), str), f"{case['id']}: missing summary")


def validate_contract_fixture(data: dict[str, Any]) -> None:
    require(data.get("contract") == "io.murlocs.activation", "invalid contract")
    require(data.get("schema_version") == 1, "invalid schema version")
    require(data.get("operation_allowlist") == ["check", "impact"], "operation allowlist")
    require(
        data.get("token_contract")
        == {
            "minted_by": "trusted-adapter",
            "scope": ["adapter_id", "adapter_version", "session_id"],
            "wire_input": "forbidden",
            "state_semantics": "same-materialized-view",
            "impact_dependency_semantics": "same-operation-dependencies",
            "comparability": "same-adapter-version-session",
        },
        "token trust contract",
    )
    require(
        data.get("cache_proof_contract")
        == {
            "default": "off",
            "required_matches": [
                "contract_version",
                "adapter_id",
                "adapter_version",
                "session_id",
                "event",
                "operations",
                "operation_inputs",
                "manifest_identity",
                "state_id",
            ],
            "impact_required_match": "impact_dependency_id",
            "missing_or_mismatch": "fresh-execution",
        },
        "cache proof contract",
    )
    require(
        data.get("prohibitions")
        == [
            "model",
            "network",
            "hosted-service",
            "registered-command-execution",
            "repository-write",
        ],
        "prohibition set mismatch",
    )
    cases = data.get("cases")
    require(isinstance(cases, list) and cases, "missing cases")
    require(len({case["id"] for case in cases}) == len(cases), "duplicate case id")
    for case in cases:
        validate_case(data, case)
    require(
        {case["request"]["event"] for case in cases} == set(EVENT_OPERATIONS),
        "event coverage mismatch",
    )


Mutation = Callable[[dict[str, Any]], None]


def mutate(case_id: str, callback: Callable[[dict[str, Any]], None]) -> Mutation:
    def apply(data: dict[str, Any]) -> None:
        callback(by_id(data, case_id))

    return apply


MUTATIONS: list[tuple[str, Mutation]] = [
    (
        "response identity mismatch",
        mutate("task-start-healthy", lambda c: c["response"].update(correlation_id="other")),
    ),
    (
        "response state mismatch",
        mutate(
            "task-start-healthy",
            lambda c: c["response"]["repository"].update(state_id="state:other"),
        ),
    ),
    (
        "response token scope mismatch",
        mutate(
            "task-start-healthy",
            lambda c: c["response"]["repository"]["token_scope"].update(session_id="other"),
        ),
    ),
    (
        "receipt state mismatch",
        mutate(
            "task-start-healthy",
            lambda c: c["response"]["operations"][0].update(state_after="state:other"),
        ),
    ),
    (
        "impact dependency mismatch",
        mutate(
            "prospective-impact-focused",
            lambda c: c["response"]["operations"][0].update(
                dependency_after_id="dependency:other"
            ),
        ),
    ),
    (
        "check probes impact dependency",
        mutate(
            "task-start-healthy",
            lambda c: c["response"]["operations"][0].update(
                dependency_before_id="dependency:invented",
                dependency_after_id="dependency:invented",
            ),
        ),
    ),
    (
        "task-start host supplies impact dependency",
        mutate(
            "task-start-healthy",
            lambda c: c["request"]["host_context"].update(
                impact_dependency_id="dependency:invented"
            ),
        ),
    ),
    (
        "task-start cache proof supplies impact dependency",
        mutate(
            "task-start-healthy",
            lambda c: c["request"]["host_context"]["cache_offer"]["proof"].update(
                impact_dependency_id=None
            ),
        ),
    ),
    (
        "task-start stale invents impact dependency mutation",
        mutate(
            "task-start-healthy",
            lambda c: (
                c["response"].update(
                    execution={
                        "status": "stale",
                        "code": "MURLOCS_ACTIVATION_STALE",
                        "observed_dependency_id": "dependency:invented",
                    },
                    operations=[],
                ),
                c["response"]["repository"].update(blocking=None),
            ),
        ),
    ),
    (
        "task-start paths supplied",
        mutate("task-start-healthy", lambda c: c["request"].update(paths=[])),
    ),
    (
        "prospective empty paths",
        mutate("prospective-impact-focused", lambda c: c["request"].update(paths=[])),
    ),
    (
        "prospective has no inputs",
        mutate(
            "prospective-impact-focused",
            lambda c: (c["request"].pop("paths"), c["request"].pop("baseline")),
        ),
    ),
    (
        "duplicate path",
        mutate(
            "prospective-impact-focused",
            lambda c: c["request"].update(paths=["src/app.py", "src/app.py"]),
        ),
    ),
    (
        "absolute path",
        mutate("prospective-impact-focused", lambda c: c["request"].update(paths=["/src/app.py"])),
    ),
    (
        "NUL path",
        mutate("prospective-impact-focused", lambda c: c["request"].update(paths=["src/\0app.py"])),
    ),
    (
        "baseline is untyped option",
        mutate("prospective-impact-focused", lambda c: c["request"].update(baseline="--output")),
    ),
    (
        "baseline resolution mismatch",
        mutate(
            "prospective-impact-focused",
            lambda c: c["request"].update(
                baseline={"kind": "git-oid", "object_format": "sha1", "oid": "b" * 40}
            ),
        ),
    ),
    (
        "precommit enforcement absent",
        mutate("pre-commit-staged-manifest-add", lambda c: c["request"].pop("enforcement")),
    ),
    (
        "host scope malformed",
        mutate(
            "task-start-healthy",
            lambda c: c["request"]["host_context"]["token_scope"].update(session_id="not valid"),
        ),
    ),
    (
        "host adapter version is not a string",
        mutate(
            "task-start-healthy",
            lambda c: c["request"]["host_context"]["token_scope"].update(adapter_version=1),
        ),
    ),
    (
        "host state token is not a string",
        mutate(
            "task-start-healthy",
            lambda c: c["request"]["host_context"].update(state_id=1),
        ),
    ),
    (
        "host dependency token is not a string",
        mutate(
            "prospective-impact-focused",
            lambda c: c["request"]["host_context"].update(impact_dependency_id=1),
        ),
    ),
    (
        "host cache token is not a string",
        mutate(
            "task-start-healthy",
            lambda c: c["request"]["host_context"]["cache_offer"].update(cache_id=1),
        ),
    ),
    (
        "host manifest identity is not a string",
        mutate(
            "task-start-healthy",
            lambda c: c["request"]["host_context"].update(manifest_identity=1),
        ),
    ),
    (
        "receipt digest is not a string",
        mutate(
            "task-start-healthy",
            lambda c: c["response"]["operations"][0].update(output_sha256=1),
        ),
    ),
    (
        "response attempted write",
        mutate("task-start-healthy", lambda c: c["response"].update(writes=["AGENTS.md"])),
    ),
    (
        "operation order",
        mutate("post-edit-healthy", lambda c: c["response"]["operations"].reverse()),
    ),
    (
        "required operation missing",
        mutate("post-edit-healthy", lambda c: c["response"]["operations"].pop()),
    ),
    (
        "completion cache reused",
        mutate(
            "pre-completion-healthy-fresh",
            lambda c: c["response"].update(cache={"decision": "hit"}),
        ),
    ),
    (
        "outcome sidecar overrides lifecycle blocking",
        mutate(
            "task-start-healthy",
            lambda c: (
                c["response"].update(outcome={"schema_version": 1}),
                c["response"]["repository"].update(blocking=True),
            ),
        ),
    ),
    (
        "cache hit lacks a non-null manifest identity",
        mutate(
            "task-start-healthy",
            lambda c: (
                c["request"]["host_context"].update(manifest_identity=None),
                c["request"]["host_context"]["cache_offer"]["proof"].update(
                    manifest_identity=None
                ),
            ),
        ),
    ),
    (
        "precommit wrong view",
        mutate(
            "pre-commit-timeout",
            lambda c: c["request"]["host_context"].update(view="worktree"),
        ),
    ),
    (
        "cache rejection skips fresh execution",
        mutate(
            "post-edit-stale-cache-rejected",
            lambda c: (
                c["response"].update(
                    execution={"status": "stale", "code": "MURLOCS_ACTIVATION_STALE"},
                    operations=[],
                ),
                c["response"]["repository"].update(blocking=None),
            ),
        ),
    ),
]


def test_checked_in_contract_fixture_is_semantically_valid():
    validate_contract_fixture(load_fixture())


@pytest.mark.parametrize(("name", "mutation"), MUTATIONS, ids=[item[0] for item in MUTATIONS])
def test_normative_mutation_is_rejected(name: str, mutation: Mutation):
    data = copy.deepcopy(load_fixture())
    mutation(data)
    with pytest.raises(ContractViolation, match=".+"):
        validate_contract_fixture(data)


def test_outcome_sidecar_is_forward_compatible_and_unknown_fields_are_ignored():
    data = copy.deepcopy(load_fixture())
    healthy = by_id(data, "task-start-healthy")["response"]
    healthy["outcome"]["future_extension"] = {
        "shell": "ignored metadata, never an action"
    }
    healthy["unknown_activation_extension"] = {"also": "ignored"}
    by_id(data, "prospective-impact-focused")["response"].pop("outcome")
    validate_contract_fixture(data)

    unparsed = copy.deepcopy(load_fixture())
    by_id(unparsed, "task-start-healthy")["response"]["outcome"] = {
        "schema_version": 1,
        "shell": "not protected by a parsed contract",
    }
    with pytest.raises(ContractViolation, match="opaque command field"):
        validate_contract_fixture(unparsed)


def test_versioned_outcomes_bind_to_lifecycle_without_overriding_blocking():
    data = load_fixture()
    healthy = by_id(data, "task-start-healthy")["response"]
    impact = by_id(data, "prospective-impact-focused")["response"]

    assert parse_outcome(healthy["outcome"])["resolution_class"] == "pass"
    parsed_impact = parse_outcome(impact["outcome"])
    aggregate = parse_outcome(by_id(data, "post-edit-healthy")["response"]["outcome"])
    assert parsed_impact["resolution_class"] == "authority_required"
    assert parsed_impact["status"] == "advisory"
    assert parsed_impact["blocking"] is False
    assert impact["repository"]["blocking"] is False
    assert parsed_impact["correlation"]["dependency_id"]
    assert aggregate["source"]["operation"] == "aggregate"


def test_aggregate_outcome_requires_receipt_provenance_and_impact_dependency():
    data = copy.deepcopy(load_fixture())
    post_edit = by_id(data, "post-edit-healthy")
    impact = copy.deepcopy(by_id(data, "prospective-impact-focused")["response"]["outcome"])
    host = post_edit["request"]["host_context"]
    impact["source"]["operation"] = "aggregate"
    impact["correlation"].update(
        correlation_id=post_edit["response"]["correlation_id"],
        state_id=host["state_id"],
        dependency_id=host["impact_dependency_id"],
        token_scope=host["token_scope"],
    )
    post_edit["response"]["outcome"] = impact
    validate_contract_fixture(data)

    bad_provenance = copy.deepcopy(data)
    by_id(bad_provenance, "post-edit-healthy")["response"]["outcome"]["findings"][0][
        "provenance"
    ]["operation"] = "aggregate"
    with pytest.raises(ContractViolation, match="provenance lacks a receipt"):
        validate_contract_fixture(bad_provenance)

    bad_dependency = copy.deepcopy(data)
    by_id(bad_dependency, "post-edit-healthy")["response"]["outcome"]["correlation"][
        "dependency_id"
    ] = "dependency:wrong"
    with pytest.raises(ContractViolation, match="dependency mismatch"):
        validate_contract_fixture(bad_dependency)

    single_receipt = copy.deepcopy(load_fixture())
    by_id(single_receipt, "prospective-impact-focused")["response"]["outcome"]["source"][
        "operation"
    ] = "aggregate"
    with pytest.raises(ContractViolation, match="requires multiple receipts"):
        validate_contract_fixture(single_receipt)


def test_trusted_tokens_are_out_of_band_and_impact_dependencies_are_operation_local():
    data = load_fixture()
    healthy = by_id(data, "task-start-healthy")
    wire = {key: value for key, value in healthy["request"].items() if key != "host_context"}
    assert "repository" not in wire
    assert "state_id" not in keys_below(wire)
    assert "impact_dependency_id" not in healthy["request"]["host_context"]
    assert (
        "impact_dependency_id"
        not in healthy["request"]["host_context"]["cache_offer"]["proof"]
    )
    assert "dependency_before_id" not in healthy["response"]["operations"][0]
    injected = {**wire, "host_context": healthy["request"]["host_context"]}
    assert request_errors(injected) == ["host_context"]

    absent = by_id(data, "task-start-absent")
    assert "impact_dependency_id" not in absent["request"]["host_context"]
    assert absent["response"]["operations"] == []

    impact = by_id(data, "prospective-impact-focused")
    assert impact["request"]["host_context"]["impact_dependency_id"]
    assert impact["response"]["operations"][0]["dependency_before_id"]
    assert valid_baseline({"kind": "git-ref", "name": "refs/heads/main"})
    assert valid_baseline({"kind": "adapter-snapshot", "value": "snapshot-1"})

    path_only = copy.deepcopy(data)
    path_case = by_id(path_only, "prospective-impact-focused")
    path_case["request"].pop("baseline")
    path_case["request"]["host_context"].pop("baseline_resolution")
    validate_contract_fixture(path_only)

    baseline_only = copy.deepcopy(data)
    by_id(baseline_only, "prospective-impact-focused")["request"].pop("paths")
    validate_contract_fixture(baseline_only)


@pytest.mark.parametrize(
    "field",
    [
        "contract_version",
        "adapter_id",
        "adapter_version",
        "session_id",
        "event",
        "operations",
        "operation_inputs",
        "manifest_identity",
        "state_id",
    ],
)
def test_cache_proof_mismatch_rejects_and_executes_fresh(field: str):
    data = copy.deepcopy(load_fixture())
    case = by_id(data, "task-start-healthy")
    proof = case["request"]["host_context"]["cache_offer"]["proof"]
    proof[field] = "mismatch"
    case["response"]["cache"]["decision"] = "rejected"
    validate_contract_fixture(data)
    assert case["response"]["operations"]


def test_impact_dependency_cache_proof_mismatch_rejects_and_executes_fresh():
    data = copy.deepcopy(load_fixture())
    case = by_id(data, "post-edit-stale-cache-rejected")
    proof = case["request"]["host_context"]["cache_offer"]["proof"]
    proof["state_id"] = case["request"]["host_context"]["state_id"]
    proof["impact_dependency_id"] = "dependency:mismatch"
    validate_contract_fixture(data)
    assert case["response"]["cache"]["decision"] == "rejected"
    assert case["response"]["operations"]


def test_missing_cache_proof_defaults_to_fresh_execution():
    data = copy.deepcopy(load_fixture())
    case = by_id(data, "task-start-healthy")
    case["request"]["host_context"].pop("cache_offer")
    case["response"]["cache"] = {"decision": "miss"}
    validate_contract_fixture(data)
    assert case["response"]["operations"]


@pytest.mark.parametrize("missing", [True, False], ids=["missing", "null"])
def test_missing_or_null_manifest_identity_rejects_cache_and_executes_fresh(missing: bool):
    data = copy.deepcopy(load_fixture())
    case = by_id(data, "task-start-healthy")
    context = case["request"]["host_context"]
    proof = context["cache_offer"]["proof"]
    if missing:
        context.pop("manifest_identity")
        proof.pop("manifest_identity")
    else:
        context["manifest_identity"] = None
        proof["manifest_identity"] = None
    case["response"]["cache"]["decision"] = "rejected"
    validate_contract_fixture(data)
    assert case["response"]["operations"]


def test_impact_dependency_race_is_stale_without_receipts():
    data = copy.deepcopy(load_fixture())
    case = by_id(data, "prospective-impact-focused")
    case["response"]["execution"] = {
        "status": "stale",
        "code": "MURLOCS_ACTIVATION_STALE",
        "observed_dependency_id": "dependency:changed",
    }
    case["response"]["repository"]["blocking"] = None
    case["response"]["operations"] = []
    case["response"]["outcome"] = None
    validate_contract_fixture(data)


def test_full_oid_baseline_must_match_host_resolution():
    data = copy.deepcopy(load_fixture())
    case = by_id(data, "prospective-impact-focused")
    case["request"]["baseline"] = {
        "kind": "git-oid",
        "object_format": "sha1",
        "oid": "a" * 40,
    }
    validate_contract_fixture(data)
    case["request"]["baseline"]["oid"] = "b" * 40
    with pytest.raises(ContractViolation, match="host baseline resolution"):
        validate_contract_fixture(data)


def test_staged_manifest_discovery_and_operations_use_the_same_index_view():
    data = load_fixture()
    added = by_id(data, "pre-commit-staged-manifest-add")
    assert added["setup"] == {
        "worktree_manifest": "absent",
        "index_manifest": "regular",
    }
    assert added["response"]["execution"]["status"] == "completed"
    assert [item["operation"] for item in added["response"]["operations"]] == [
        "check",
        "impact",
    ]

    deleted = by_id(data, "pre-commit-staged-manifest-delete")
    assert deleted["setup"] == {
        "worktree_manifest": "regular",
        "index_manifest": "absent",
    }
    assert deleted["response"]["execution"]["status"] == "not_applicable"
    assert deleted["response"]["operations"] == []


def test_stale_is_reserved_for_repository_mutation_during_invocation():
    stale = by_id(load_fixture(), "post-edit-repository-mutated")["response"]
    assert stale["execution"]["status"] == "stale"
    assert stale["execution"]["observed_state_id"] != stale["repository"]["state_id"]
    rejected = by_id(load_fixture(), "post-edit-stale-cache-rejected")["response"]
    assert rejected["execution"]["status"] == "completed"
    assert rejected["cache"]["decision"] == "rejected"
    assert [item["operation"] for item in rejected["operations"]] == ["check", "impact"]


def test_document_examples_and_diagrams_are_machine_readable():
    doc = CONTRACT_DOC.read_text(encoding="utf-8")
    assert "stateDiagram-v2" in doc
    assert "sequenceDiagram" in doc
    assert "pre-commit" in doc[doc.index("sequenceDiagram") : doc.index("## Fallbacks")]
    examples = re.findall(r"```json\n(.*?)\n```", doc, re.DOTALL)
    assert len(examples) == 3
    for example in examples:
        parse_json_strict(example)


def test_contract_json_rejects_duplicate_members():
    duplicated_receipt = (
        '{"operation":"impact","output_sha256":"sha256:first",'
        '"output_sha256":"sha256:second"}'
    )
    with pytest.raises(ContractViolation, match="duplicate JSON member: output_sha256"):
        parse_json_strict(duplicated_receipt)


def test_contract_is_integrated_with_compact_honest_prompt_fallback():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs/architecture.md").read_text(encoding="utf-8")
    journeys = (ROOT / "docs/journeys.md").read_text(encoding="utf-8")
    root_layer = (ROOT / ".murlocs/layers/base.toml").read_text(encoding="utf-8")

    assert str(FIXTURE.relative_to(ROOT)) in CONTRACT_DOC.read_text(encoding="utf-8")
    assert "docs/activation-lifecycle.md" in readme
    assert "activation-lifecycle.md" in architecture
    assert "murlocs impact --path" in journeys
    activation_rules = [line for line in root_layer.splitlines() if "Murlocs activation:" in line]
    assert len(activation_rules) == 1
    activation_rule = activation_rules[0]
    assert len(activation_rule) < 320
    assert "fresh `murlocs check` and `murlocs impact`" in activation_rule
    assert "not host-enforced" in activation_rule
    assert "read docs/activation-lifecycle.md" not in activation_rule
    generated_root = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "not host-enforced" in generated_root
