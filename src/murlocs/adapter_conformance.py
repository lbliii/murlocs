"""Portable black-box conformance checks for passive Murlocs adapters.

The harness standardizes observable lifecycle behavior, not repository snapshot
algorithms.  State and impact-dependency tokens remain opaque values minted by
the adapter under test.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, TypedDict, cast

from murlocs.errors import MurlocsError
from murlocs.outcome import parse_outcome

ADAPTER_CONTRACT = "io.murlocs.adapter"
ADAPTER_SCHEMA_VERSION = 1
SUITE_CONTRACT = "io.murlocs.adapter-conformance"
REPORT_CONTRACT = "io.murlocs.adapter-conformance-report"

EVENTS = (
    "task-start",
    "prospective-impact",
    "post-edit",
    "pre-commit",
    "pre-completion",
)
CONTINUOUS_SURFACES = frozenset(
    {"task-start", "prospective-impact", "post-edit", "pre-completion"}
)
DISCRETE_BOUNDARIES = frozenset({"pre-commit"})
EMPTY_CHANGE_SET = "empty"
EVENT_MODES = {"host-enforced", "prompt-mediated", "unavailable"}
ROOT_FORMATS = {"posix", "windows-drive", "windows-unc"}
FALLBACKS = {"generated-guidance", "git-hook", "ci"}
REQUIRED_CAPABILITIES = (
    "exact-manifest-discovery",
    "out-of-band-trusted-context",
    "opaque-state-freshness",
    "impact-dependency-freshness",
    "read-only-operation-runner",
    "deadline-enforcement",
    "strict-structured-output",
    "typed-outcome-forwarding",
)
OPTIONAL_CAPABILITIES = {
    "exact-proof-cache",
    "deterministic-repair-dispatch",
    "native-task-start",
    "native-post-edit",
    "native-pre-completion",
}
EXECUTION_CODES = {
    "completed": "MURLOCS_ACTIVATION_OK",
    "not_applicable": "MURLOCS_ACTIVATION_ABSENT",
    "no_changed_paths": "MURLOCS_NO_CHANGED_PATHS",
    "unavailable": "MURLOCS_ACTIVATION_UNAVAILABLE",
    "timeout": "MURLOCS_ACTIVATION_TIMEOUT",
    "invalid": "MURLOCS_ACTIVATION_INVALID",
    "stale": "MURLOCS_ACTIVATION_STALE",
}
EVENT_OPERATIONS = {
    "task-start": ("check",),
    "prospective-impact": ("impact",),
    "post-edit": ("check", "impact"),
    "pre-commit": ("check", "impact"),
    "pre-completion": ("check", "impact"),
}
OPAQUE_COMMAND_FIELDS = {"argv", "command", "cwd", "env", "shell"}


class AdapterConformanceError(MurlocsError):
    """An adapter descriptor, suite, or observation is invalid."""


class AdapterScenarioResult(TypedDict):
    id: str
    passed: bool
    errors: list[str]


class AdapterConformanceReport(TypedDict):
    contract: str
    schema_version: int
    adapter_id: str
    adapter_version: str
    passed: bool
    scenarios: list[AdapterScenarioResult]


class AdapterDriver(Protocol):
    """Host-specific test driver consumed by the portable harness.

    ``request`` is the agent-callable wire object. Trusted repository context is
    deliberately absent. The driver obtains its root and deterministic fault
    controls from ``context``, just as a production adapter obtains root and
    lifecycle timing from its host rather than from the active agent.
    """

    def descriptor(self) -> Mapping[str, Any]: ...

    def invoke(
        self, request: Mapping[str, Any], context: ConformanceContext
    ) -> Mapping[str, Any]: ...


@dataclass
class ConformanceContext:
    """Isolated repository and deterministic test seams for one invocation."""

    root: Path
    scenario_id: str
    control: Mapping[str, Any]
    _expected_files: dict[str, bytes]
    _expected_modes: dict[str, int]
    _expected_directories: set[str]
    operations: list[str] = dataclass_field(default_factory=list)
    checkpoints: list[str] = dataclass_field(default_factory=list)
    agent_prompted: bool = False

    def record_operation(self, operation: str) -> None:
        """Record one typed Murlocs operation performed by the adapter."""
        if operation not in {"check", "impact"}:
            raise AdapterConformanceError(f"unsupported adapter operation {operation!r}")
        self.operations.append(operation)

    def request_agent_input(self) -> None:
        """Record an adapter prompt; no-prompt scenarios reject this."""
        self.agent_prompted = True

    def checkpoint(self, name: str) -> None:
        """Expose a deterministic race point and apply declared external mutations."""
        if not isinstance(name, str) or not name:
            raise AdapterConformanceError("checkpoint name must be a nonempty string")
        if name in self.checkpoints:
            raise AdapterConformanceError(f"adapter repeated checkpoint {name!r}")
        self.checkpoints.append(name)
        for mutation in self.control.get("mutations", []):
            if mutation.get("checkpoint") == name:
                self._apply_mutation(mutation)

    def _apply_mutation(self, mutation: Mapping[str, Any]) -> None:
        if set(mutation) != {"checkpoint", "operation", "path", "content"}:
            raise AdapterConformanceError("fixture mutation has unknown or missing fields")
        raw_path = mutation.get("path")
        if not isinstance(raw_path, str):
            raise AdapterConformanceError("fixture mutation path must be a string")
        relative = _fixture_path(raw_path)
        target = self.root / relative
        operation = mutation.get("operation")
        if operation == "write":
            content = mutation.get("content")
            if not isinstance(content, str):
                raise AdapterConformanceError("fixture mutation content must be text")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            self._expected_files[relative] = content.encode()
            self._expected_modes.setdefault(relative, target.stat().st_mode & 0o7777)
            parent = PurePosixPath(relative).parent
            while parent.as_posix() != ".":
                self._expected_directories.add(parent.as_posix())
                parent = parent.parent
            return
        if operation == "remove":
            if mutation.get("content") is not None:
                raise AdapterConformanceError("remove mutation content must be null")
            if target.exists():
                target.unlink()
            self._expected_files.pop(relative, None)
            self._expected_modes.pop(relative, None)
            return
        raise AdapterConformanceError(f"unsupported fixture mutation operation {operation!r}")


def default_suite_path() -> Path:
    """Return the installed version-1 adapter suite."""
    return Path(__file__).with_name("adapter_fixtures") / "v1" / "conformance.json"


def load_adapter_suite(path: Path | None = None) -> dict[str, Any]:
    """Load a bounded duplicate-safe conformance suite."""
    selected = path or default_suite_path()
    try:
        raw = selected.read_bytes()
    except OSError as exc:
        raise AdapterConformanceError(f"could not read adapter suite: {exc}") from exc
    if len(raw) > 1024 * 1024:
        raise AdapterConformanceError("adapter suite exceeds 1 MiB")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, AdapterConformanceError) as exc:
        raise AdapterConformanceError(f"invalid adapter suite JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise AdapterConformanceError("adapter suite must be an object")
    _validate_suite(value)
    return value


def run_adapter_conformance(
    driver: AdapterDriver,
    *,
    suite: Mapping[str, Any] | None = None,
    temporary_parent: Path | None = None,
) -> AdapterConformanceReport:
    """Run the same isolated scenarios against any adapter test driver."""
    descriptor = _validate_descriptor(driver.descriptor())
    selected = dict(suite) if suite is not None else load_adapter_suite()
    _validate_suite(selected)
    scenario_results: list[AdapterScenarioResult] = []
    for scenario in cast(list[dict[str, Any]], selected["scenarios"]):
        errors: list[str] = []
        with tempfile.TemporaryDirectory(
            prefix="murlocs-adapter-", dir=temporary_parent
        ) as raw_root:
            root = Path(raw_root)
            expected_files, expected_modes, expected_directories = _materialize_repository(
                root, selected["repository"], scenario
            )
            context = ConformanceContext(
                root=root,
                scenario_id=scenario["id"],
                control=scenario["control"],
                _expected_files=expected_files,
                _expected_modes=expected_modes,
                _expected_directories=expected_directories,
            )
            request = cast(dict[str, Any], scenario["request"])
            before = _inventory_repository(root)
            try:
                observation = driver.invoke(request, context)
                _validate_observation(descriptor, scenario, observation, context)
            except Exception as exc:  # A crashing adapter is a conformance failure.
                errors.append(f"{type(exc).__name__}: {exc}")
            try:
                after = _inventory_repository(root)
                repository_matches = _repository_matches(context)
            except (AdapterConformanceError, OSError) as exc:
                after = {}
                repository_matches = False
                errors.append(str(exc))
            if not repository_matches:
                errors.append(
                    "adapter changed repository state outside declared external mutations"
                )
            if before != after and not context.control.get("mutations"):
                errors.append("read-only scenario changed repository bytes")
        scenario_results.append({"id": scenario["id"], "passed": not errors, "errors": errors})
    return {
        "contract": REPORT_CONTRACT,
        "schema_version": 1,
        "adapter_id": descriptor["adapter_id"],
        "adapter_version": descriptor["adapter_version"],
        "passed": all(item["passed"] for item in scenario_results),
        "scenarios": scenario_results,
    }


def assert_adapter_conformance(report: Mapping[str, Any]) -> None:
    """Raise with stable scenario ids when a report contains failures."""
    failed = [
        item["id"]
        for item in report.get("scenarios", [])
        if isinstance(item, Mapping) and not item.get("passed")
    ]
    if report.get("contract") != REPORT_CONTRACT or report.get("schema_version") != 1:
        raise AdapterConformanceError("unsupported adapter conformance report")
    if failed or report.get("passed") is not True:
        raise AdapterConformanceError(
            "adapter conformance failed: " + ", ".join(sorted(str(item) for item in failed))
        )


def _validate_descriptor(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "contract",
        "schema_version",
        "adapter_id",
        "adapter_version",
        "lifecycle_versions",
        "outcome_versions",
        "required_capabilities",
        "optional_capabilities",
        "events",
        "root_formats",
        "fallbacks",
        "deprecated_versions",
    }
    if set(value) != required:
        raise AdapterConformanceError("adapter descriptor has unknown or missing fields")
    if value.get("contract") != ADAPTER_CONTRACT:
        raise AdapterConformanceError("unsupported adapter contract")
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] != ADAPTER_SCHEMA_VERSION
    ):
        raise AdapterConformanceError("unsupported adapter schema version")
    adapter_id = _bounded_token(value.get("adapter_id"), "adapter_id")
    adapter_version = _bounded_token(value.get("adapter_version"), "adapter_version")
    if value.get("lifecycle_versions") != [1] or value.get("outcome_versions") != [1]:
        raise AdapterConformanceError("adapter does not negotiate lifecycle and outcome v1")
    if value.get("required_capabilities") != list(REQUIRED_CAPABILITIES):
        raise AdapterConformanceError("adapter required capabilities do not match v1")
    optional = value.get("optional_capabilities")
    if (
        not isinstance(optional, list)
        or any(not isinstance(item, str) for item in optional)
        or len(optional) != len(set(optional))
        or set(optional) - OPTIONAL_CAPABILITIES
    ):
        raise AdapterConformanceError("adapter optional capabilities are invalid")
    events = value.get("events")
    if (
        not isinstance(events, dict)
        or set(events) != set(EVENTS)
        or any(mode not in EVENT_MODES for mode in events.values())
    ):
        raise AdapterConformanceError("adapter event enforcement map is invalid")
    roots = value.get("root_formats")
    if (
        not isinstance(roots, list)
        or any(not isinstance(item, str) for item in roots)
        or set(roots) != ROOT_FORMATS
    ):
        raise AdapterConformanceError("adapter must understand all portable root formats")
    fallbacks = value.get("fallbacks")
    if (
        not isinstance(fallbacks, list)
        or any(not isinstance(item, str) for item in fallbacks)
        or len(fallbacks) != len(set(fallbacks))
        or any(item not in FALLBACKS for item in fallbacks)
        or fallbacks
        != [item for item in ("generated-guidance", "git-hook", "ci") if item in fallbacks]
    ):
        raise AdapterConformanceError("adapter fallback declaration is invalid")
    deprecated = value.get("deprecated_versions")
    if (
        not isinstance(deprecated, list)
        or any(
            not isinstance(item, int) or isinstance(item, bool) or item < 1 for item in deprecated
        )
        or deprecated != sorted(set(deprecated))
    ):
        raise AdapterConformanceError("adapter deprecated versions are invalid")
    if 1 in deprecated:
        raise AdapterConformanceError("adapter cannot deprecate its active schema version")
    return {**value, "adapter_id": adapter_id, "adapter_version": adapter_version}


def _validate_suite(value: Mapping[str, Any]) -> None:
    if set(value) != {"contract", "schema_version", "repository", "scenarios"}:
        raise AdapterConformanceError("adapter suite has unknown or missing fields")
    if (
        value.get("contract") != SUITE_CONTRACT
        or type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
    ):
        raise AdapterConformanceError("unsupported adapter conformance suite")
    repository = value.get("repository")
    if not isinstance(repository, dict) or set(repository) != {"files"}:
        raise AdapterConformanceError("adapter suite repository is invalid")
    _validate_files(repository.get("files"))
    scenarios = value.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios or len(scenarios) > 64:
        raise AdapterConformanceError("adapter suite scenarios must be a bounded array")
    ids: list[str] = []
    required_shapes = {
        "id",
        "request",
        "control",
        "setup",
        "expected",
    }
    for scenario in scenarios:
        if not isinstance(scenario, dict) or set(scenario) != required_shapes:
            raise AdapterConformanceError("adapter scenario has unknown or missing fields")
        ids.append(_bounded_token(scenario.get("id"), "scenario id"))
        if not isinstance(scenario.get("request"), dict):
            raise AdapterConformanceError("adapter scenario request must be an object")
        control = scenario.get("control")
        if not isinstance(control, dict) or set(control) != {
            "outcome",
            "fault",
            "cache",
            "root_format",
            "mutations",
            "change_set",
        }:
            raise AdapterConformanceError("adapter scenario control must be an object")
        if control.get("change_set") not in {EMPTY_CHANGE_SET, "nonempty"}:
            raise AdapterConformanceError("adapter scenario change_set control is invalid")
        if control.get("outcome") not in {
            None,
            "pass",
            "deterministic-repair",
            "agent-action",
            "authority-required",
        }:
            raise AdapterConformanceError("adapter scenario outcome control is invalid")
        if control.get("fault") not in {
            None,
            "absent",
            "unavailable",
            "timeout",
            "malformed",
            "state-race",
            "dependency-race",
            "agent-token",
            "unsupported-version",
        }:
            raise AdapterConformanceError("adapter scenario fault control is invalid")
        if control.get("cache") not in {"miss", "hit", "rejected", "forbidden"}:
            raise AdapterConformanceError("adapter scenario cache control is invalid")
        if control.get("root_format") not in ROOT_FORMATS:
            raise AdapterConformanceError("adapter scenario root format is invalid")
        mutations = control.get("mutations")
        if not isinstance(mutations, list):
            raise AdapterConformanceError("adapter scenario mutations must be an array")
        for mutation in mutations:
            if not isinstance(mutation, dict) or set(mutation) != {
                "checkpoint",
                "operation",
                "path",
                "content",
            }:
                raise AdapterConformanceError("adapter scenario mutation is invalid")
            _bounded_token(mutation.get("checkpoint"), "mutation checkpoint")
            path = mutation.get("path")
            if not isinstance(path, str) or _fixture_path(path) != path:
                raise AdapterConformanceError("adapter scenario mutation path is invalid")
            operation = mutation.get("operation")
            content = mutation.get("content")
            if operation == "write" and not isinstance(content, str):
                raise AdapterConformanceError("write mutation content must be text")
            if operation == "remove" and content is not None:
                raise AdapterConformanceError("remove mutation content must be null")
            if operation not in {"write", "remove"}:
                raise AdapterConformanceError("adapter scenario mutation operation is invalid")
        setup = scenario.get("setup")
        if not isinstance(setup, dict) or set(setup) != {"files", "remove"}:
            raise AdapterConformanceError("adapter scenario setup is invalid")
        _validate_files(setup.get("files"))
        removals = setup.get("remove")
        if not isinstance(removals, list) or any(
            not isinstance(item, str) or _fixture_path(item) != item for item in removals
        ):
            raise AdapterConformanceError("adapter scenario removals are invalid")
        expected = scenario.get("expected")
        if not isinstance(expected, dict) or set(expected) != {
            "status",
            "code",
            "operation_calls",
            "receipts",
            "cache",
            "resolution_class",
            "silent",
            "agent_prompted",
            "event_mode",
            "fallbacks",
        }:
            raise AdapterConformanceError("adapter scenario expectation is invalid")
        status = expected.get("status")
        if status not in EXECUTION_CODES or expected.get("code") != EXECUTION_CODES[status]:
            raise AdapterConformanceError("adapter scenario execution expectation is invalid")
        for field in ("operation_calls", "receipts"):
            operations = expected.get(field)
            if not isinstance(operations, list) or any(
                item not in {"check", "impact"} for item in operations
            ):
                raise AdapterConformanceError(f"adapter scenario {field} are invalid")
        event = scenario["request"].get("event")
        required_operations = EVENT_OPERATIONS.get(event)
        if required_operations is None:
            raise AdapterConformanceError("adapter scenario event is invalid")
        calls = tuple(expected["operation_calls"])
        receipts = tuple(expected["receipts"])
        if calls != required_operations[: len(calls)]:
            raise AdapterConformanceError("adapter scenario operation calls are out of order")
        if expected["status"] == "completed":
            if receipts != required_operations:
                raise AdapterConformanceError("completed adapter scenario lacks required receipts")
        elif receipts:
            raise AdapterConformanceError("failed adapter scenario retains receipts")
        if expected["cache"] == "hit" and calls:
            raise AdapterConformanceError("cache-hit scenario performs fresh operations")
        if expected.get("cache") not in {"miss", "hit", "rejected", "forbidden"}:
            raise AdapterConformanceError("adapter scenario cache expectation is invalid")
        if expected.get("resolution_class") not in {
            None,
            "pass",
            "deterministic_repair",
            "agent_action",
            "authority_required",
        }:
            raise AdapterConformanceError("adapter scenario outcome expectation is invalid")
        if not isinstance(expected.get("silent"), bool) or not isinstance(
            expected.get("agent_prompted"), bool
        ):
            raise AdapterConformanceError("adapter scenario boolean expectation is invalid")
        if expected.get("event_mode") not in EVENT_MODES:
            raise AdapterConformanceError("adapter scenario event mode is invalid")
        expected_fallbacks = expected.get("fallbacks")
        if (
            not isinstance(expected_fallbacks, list)
            or len(expected_fallbacks) != len(set(expected_fallbacks))
            or any(item not in FALLBACKS for item in expected_fallbacks)
        ):
            raise AdapterConformanceError("adapter scenario fallbacks are invalid")
    if len(ids) != len(set(ids)):
        raise AdapterConformanceError("adapter scenario ids must be unique")


def _validate_observation(
    descriptor: Mapping[str, Any],
    scenario: Mapping[str, Any],
    observation: Mapping[str, Any],
    context: ConformanceContext,
) -> None:
    if set(observation) != {"trusted_context", "response"}:
        raise AdapterConformanceError("adapter observation has unknown or missing fields")
    trusted = observation.get("trusted_context")
    response = observation.get("response")
    if not isinstance(trusted, dict) or not isinstance(response, dict):
        raise AdapterConformanceError("adapter observation fields must be objects")
    request = cast(Mapping[str, Any], scenario["request"])
    expected = cast(Mapping[str, Any], scenario["expected"])
    adversarial_wire = scenario["control"].get("fault") in {
        "agent-token",
        "unsupported-version",
    }
    _validate_wire_request(request, adversarial=adversarial_wire)
    if descriptor["events"][request["event"]] != expected["event_mode"]:
        raise AdapterConformanceError("adapter event mode does not match scenario")
    _validate_trusted_context(descriptor, trusted, request, expected)
    _validate_lifecycle_response(request, trusted, response, expected)
    _validate_continuous_surface_gating(request, response, scenario)
    if context.operations != expected["operation_calls"]:
        raise AdapterConformanceError("adapter operation trace does not match expectation")
    if context.agent_prompted != expected["agent_prompted"]:
        raise AdapterConformanceError("adapter prompt behavior does not match expectation")
    required_checkpoints = {item["checkpoint"] for item in scenario["control"].get("mutations", [])}
    if not required_checkpoints <= set(context.checkpoints):
        raise AdapterConformanceError("adapter did not expose a required mutation checkpoint")


def _validate_wire_request(request: Mapping[str, Any], *, adversarial: bool = False) -> None:
    forbidden = {
        "host_context",
        "repository",
        "state_id",
        "dependency_id",
        "token_scope",
        "cache_offer",
        "manifest_identity",
    }
    injected = forbidden.intersection(request)
    if injected and not adversarial:
        raise AdapterConformanceError("agent wire request contains trusted host fields")
    unsupported_version = (
        request.get("contract") != "io.murlocs.activation"
        or type(request.get("schema_version")) is not int
        or request["schema_version"] != 1
    )
    if adversarial and not injected and not unsupported_version:
        raise AdapterConformanceError("agent-token scenario does not inject a trusted field")
    if unsupported_version and not adversarial:
        raise AdapterConformanceError("adapter wire request has unsupported activation version")
    if request.get("event") not in EVENTS:
        raise AdapterConformanceError("adapter wire request event is invalid")


def _validate_trusted_context(
    descriptor: Mapping[str, Any],
    trusted: Mapping[str, Any],
    request: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> None:
    required = {"root", "manifest", "view", "token_scope", "state_id"}
    optional = {
        "impact_dependency_id",
        "manifest_identity",
        "baseline_resolution",
        "cache_offer",
    }
    if not required <= set(trusted) or set(trusted) - required - optional:
        raise AdapterConformanceError("adapter trusted context has unknown or missing fields")
    if trusted.get("manifest") != ".murlocs/manifest.toml":
        raise AdapterConformanceError("adapter discovered a noncanonical manifest path")
    scope = trusted.get("token_scope")
    if (
        not isinstance(scope, dict)
        or set(scope) != {"adapter_id", "adapter_version", "session_id"}
        or scope.get("adapter_id") != descriptor["adapter_id"]
        or scope.get("adapter_version") != descriptor["adapter_version"]
        or not isinstance(scope.get("session_id"), str)
        or not scope["session_id"]
    ):
        raise AdapterConformanceError("adapter token scope is invalid")
    if not isinstance(trusted.get("state_id"), str) or not trusted["state_id"]:
        raise AdapterConformanceError("adapter state token must be opaque nonempty text")
    if not _valid_root(trusted.get("root")):
        raise AdapterConformanceError("adapter portable root is invalid")
    has_impact = "impact" in expected["operation_calls"]
    dependency = trusted.get("impact_dependency_id")
    if has_impact and (not isinstance(dependency, str) or not dependency):
        raise AdapterConformanceError("impact event lacks a trusted dependency token")
    if not has_impact and "impact_dependency_id" in trusted:
        raise AdapterConformanceError("check-only event probed impact dependencies")


def _validate_lifecycle_response(
    request: Mapping[str, Any],
    trusted: Mapping[str, Any],
    response: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> None:
    if _keys_below(response).intersection(OPAQUE_COMMAND_FIELDS):
        raise AdapterConformanceError("adapter response contains an opaque command field")
    for field in ("contract", "schema_version", "event", "correlation_id"):
        if response.get(field) != request.get(field):
            raise AdapterConformanceError(f"adapter response {field} does not echo request")
    execution = response.get("execution")
    if not isinstance(execution, dict):
        raise AdapterConformanceError("adapter response execution is missing")
    if execution.get("status") != expected["status"] or execution.get("code") != expected["code"]:
        raise AdapterConformanceError("adapter execution result does not match scenario")
    repository = response.get("repository")
    if not isinstance(repository, dict):
        raise AdapterConformanceError("adapter response repository is missing")
    for field in ("root", "manifest", "view", "token_scope"):
        if repository.get(field) != trusted.get(field):
            raise AdapterConformanceError(
                f"adapter repository {field} does not echo trusted context"
            )
    if not isinstance(repository.get("state_id"), str) or not repository["state_id"]:
        raise AdapterConformanceError("adapter response state token is invalid")
    blocking = repository.get("blocking")
    if blocking is not None and not isinstance(blocking, bool):
        raise AdapterConformanceError("adapter repository blocking state is invalid")
    if response.get("writes") != []:
        raise AdapterConformanceError("adapter lifecycle attempted a repository write")
    if response.get("silent") is not expected["silent"]:
        raise AdapterConformanceError("adapter silence does not match scenario")
    if not isinstance(response.get("summary"), str) or not response["summary"]:
        raise AdapterConformanceError("adapter response summary is missing")
    cache = response.get("cache")
    if not isinstance(cache, dict) or cache.get("decision") != expected["cache"]:
        raise AdapterConformanceError("adapter cache decision does not match scenario")
    _validate_cache(request, trusted, cache)
    fallback = response.get("fallback")
    if fallback != expected["fallbacks"]:
        raise AdapterConformanceError("adapter fallback list is invalid")
    _validate_next_actions(response.get("next_actions"))
    operations = response.get("operations")
    if not isinstance(operations, list):
        raise AdapterConformanceError("adapter operation receipts are invalid")
    expected_operations = tuple(expected["receipts"])
    receipt_operations = tuple(
        item.get("operation") for item in operations if isinstance(item, dict)
    )
    if receipt_operations != expected_operations:
        raise AdapterConformanceError("adapter receipt order does not match scenario")
    if expected["status"] == "completed":
        _validate_fresh_receipts(operations, trusted)
    elif operations:
        raise AdapterConformanceError("failed or stale activation retained operation receipts")
    if expected["status"] == "stale":
        observed_state = execution.get("observed_state_id")
        observed_dependency = execution.get("observed_dependency_id")
        if observed_state == trusted["state_id"] and observed_dependency == trusted.get(
            "impact_dependency_id"
        ):
            raise AdapterConformanceError("stale response does not identify a changed token")
    outcome = response.get("outcome")
    expected_resolution = expected["resolution_class"]
    if expected_resolution is None:
        if outcome is not None:
            parse_outcome(outcome)
    else:
        parsed = parse_outcome(outcome)
        if parsed["resolution_class"] != expected_resolution:
            raise AdapterConformanceError("adapter outcome resolution does not match scenario")
        lifecycle_blocking = repository["blocking"]
        if parsed["blocking"] != lifecycle_blocking:
            if request["event"] in CONTINUOUS_SURFACES:
                if lifecycle_blocking is not False:
                    raise AdapterConformanceError(
                        "continuous surface must defer blocking to a discrete boundary"
                    )
            else:
                raise AdapterConformanceError("adapter outcome overrides lifecycle blocking")
        correlation = parsed["correlation"]
        if (
            correlation["correlation_id"] != request["correlation_id"]
            or correlation["state_id"] != trusted["state_id"]
            or correlation["token_scope"] != trusted["token_scope"]
        ):
            raise AdapterConformanceError(
                "adapter outcome is not bound to trusted lifecycle tokens"
            )
        expected_dependency = (
            trusted.get("impact_dependency_id") if "impact" in expected_operations else None
        )
        if correlation["dependency_id"] != expected_dependency:
            raise AdapterConformanceError("adapter outcome dependency token does not match receipt")


def _validate_continuous_surface_gating(
    request: Mapping[str, Any],
    response: Mapping[str, Any],
    scenario: Mapping[str, Any],
) -> None:
    """Continuous surfaces must never gate; empty change sets must stay silent."""
    event = request["event"]
    if event not in CONTINUOUS_SURFACES:
        return
    repository = response.get("repository")
    if not isinstance(repository, dict):
        return
    blocking = repository.get("blocking")
    if blocking is True:
        raise AdapterConformanceError(
            f"continuous surface {event!r} emitted a blocking repository decision"
        )
    if scenario["control"].get("change_set") != EMPTY_CHANGE_SET:
        return
    if response.get("silent") is not True:
        raise AdapterConformanceError(
            f"continuous surface {event!r} must be silent on an empty change set"
        )
    execution = response.get("execution")
    if not isinstance(execution, dict):
        raise AdapterConformanceError("adapter response execution is missing")
    if execution.get("status") not in {"completed", "not_applicable", "no_changed_paths"}:
        raise AdapterConformanceError(
            f"continuous surface {event!r} must not fail on an empty change set"
        )
    if response.get("operations"):
        raise AdapterConformanceError(
            f"continuous surface {event!r} must not retain receipts on an empty change set"
        )


def _validate_fresh_receipts(
    operations: Sequence[Mapping[str, Any]], trusted: Mapping[str, Any]
) -> None:
    for receipt in operations:
        if (
            receipt.get("state_before") != trusted["state_id"]
            or receipt.get("state_after") != trusted["state_id"]
        ):
            raise AdapterConformanceError("adapter receipt state tokens are not fresh")
        if receipt.get("operation") == "impact":
            dependency = trusted.get("impact_dependency_id")
            if (
                receipt.get("dependency_before_id") != dependency
                or receipt.get("dependency_after_id") != dependency
            ):
                raise AdapterConformanceError("adapter impact dependency tokens are not fresh")
        elif "dependency_before_id" in receipt or "dependency_after_id" in receipt:
            raise AdapterConformanceError("check receipt carries impact dependency tokens")


def _validate_next_actions(value: Any) -> None:
    if not isinstance(value, list):
        raise AdapterConformanceError("adapter next_actions must be an array")
    allowed = {"inspect_findings", "request_authority", "retry", "use_fallback"}
    for action in value:
        if not isinstance(action, dict) or set(action) != {
            "operation",
            "arguments",
            "effect",
            "authority",
        }:
            raise AdapterConformanceError("adapter next action has unknown or missing fields")
        if action["operation"] not in allowed or not isinstance(action["arguments"], dict):
            raise AdapterConformanceError("adapter next action is not allowlisted")
        if action["effect"] not in {"read_repository", "request_authority"}:
            raise AdapterConformanceError("adapter next action effect is invalid")
        if action["authority"] not in {"integration", "agent", "human"}:
            raise AdapterConformanceError("adapter next action authority is invalid")


def _validate_cache(
    request: Mapping[str, Any], trusted: Mapping[str, Any], cache: Mapping[str, Any]
) -> None:
    decision = cache["decision"]
    offer = trusted.get("cache_offer")
    if decision == "miss":
        if offer is not None:
            raise AdapterConformanceError("adapter ignored an offered cache proof")
        return
    if decision == "forbidden":
        return
    if not isinstance(offer, dict) or set(offer) != {"cache_id", "proof"}:
        raise AdapterConformanceError("adapter cache decision lacks a trusted proof")
    cache_id = offer.get("cache_id")
    proof = offer.get("proof")
    if not isinstance(cache_id, str) or not cache_id or not isinstance(proof, dict):
        raise AdapterConformanceError("adapter cache offer is invalid")
    if cache.get("key") != cache_id:
        raise AdapterConformanceError("adapter cache response does not echo its trusted id")
    scope = trusted["token_scope"]
    expected: dict[str, Any] = {
        "contract_version": "1",
        "adapter_id": scope["adapter_id"],
        "adapter_version": scope["adapter_version"],
        "session_id": scope["session_id"],
        "event": request["event"],
        "operations": list(EVENT_OPERATIONS[request["event"]]),
        "operation_inputs": {
            field: request[field]
            for field in ("paths", "baseline", "enforcement")
            if field in request
        },
        "manifest_identity": trusted.get("manifest_identity"),
        "state_id": trusted["state_id"],
    }
    if "impact" in EVENT_OPERATIONS[request["event"]]:
        expected["impact_dependency_id"] = trusted.get("impact_dependency_id")
    exact = proof == expected and isinstance(expected["manifest_identity"], str)
    if decision == "hit" and not exact:
        raise AdapterConformanceError("adapter accepted an incomplete or mismatched cache proof")
    if decision == "rejected" and exact:
        raise AdapterConformanceError("adapter rejected an exact trusted cache proof")


def _valid_root(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    kind = value.get("format")
    segments = value.get("segments")
    if not isinstance(segments, list) or any(not _valid_root_part(item) for item in segments):
        return False
    if kind == "posix":
        return set(value) == {"format", "segments"}
    if kind == "windows-drive":
        drive = value.get("drive")
        return (
            set(value) == {"format", "drive", "segments"}
            and isinstance(drive, str)
            and len(drive) == 1
            and "A" <= drive <= "Z"
        )
    if kind == "windows-unc":
        return (
            set(value) == {"format", "server", "share", "segments"}
            and _valid_root_part(value.get("server"))
            and _valid_root_part(value.get("share"))
        )
    return False


def _valid_root_part(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value not in {".", ".."}
        and "\0" not in value
        and "/" not in value
        and "\\" not in value
        and all(not 0xD800 <= ord(char) <= 0xDFFF for char in value)
    )


def _validate_files(value: Any) -> None:
    if not isinstance(value, list):
        raise AdapterConformanceError("fixture files must be an array")
    paths: list[str] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"path", "content"}:
            raise AdapterConformanceError("fixture file has unknown or missing fields")
        path = item.get("path")
        content = item.get("content")
        if not isinstance(path, str) or _fixture_path(path) != path:
            raise AdapterConformanceError("fixture file path is invalid")
        if not isinstance(content, str):
            raise AdapterConformanceError("fixture file content must be text")
        paths.append(path)
    if len(paths) != len(set(paths)):
        raise AdapterConformanceError("fixture file paths must be unique")


def _materialize_repository(
    root: Path, repository: Mapping[str, Any], scenario: Mapping[str, Any]
) -> tuple[dict[str, bytes], dict[str, int], set[str]]:
    files = {
        item["path"]: item["content"].encode()
        for item in cast(list[dict[str, str]], repository["files"])
    }
    setup = cast(Mapping[str, Any], scenario["setup"])
    for path in cast(list[str], setup["remove"]):
        files.pop(path, None)
    for item in cast(list[dict[str, str]], setup["files"]):
        files[item["path"]] = item["content"].encode()
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return files, _inventory_modes(root), _inventory_directories(root)


def _inventory_repository(root: Path) -> dict[str, bytes]:
    inventory: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise AdapterConformanceError("adapter repository contains an unexpected symlink")
        if path.is_file():
            inventory[path.relative_to(root).as_posix()] = path.read_bytes()
    return inventory


def _inventory_modes(root: Path) -> dict[str, int]:
    return {
        path.relative_to(root).as_posix(): path.stat().st_mode & 0o7777
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def _inventory_directories(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_dir() and not path.is_symlink()
    }


def _repository_matches(context: ConformanceContext) -> bool:
    return (
        _inventory_repository(context.root) == context._expected_files
        and _inventory_modes(context.root) == context._expected_modes
        and _inventory_directories(context.root) == context._expected_directories
    )


def _fixture_path(raw: str) -> str:
    candidate = PurePosixPath(raw)
    if (
        not raw
        or candidate.is_absolute()
        or candidate.as_posix() != raw
        or any(part in {"", ".", ".."} for part in raw.split("/"))
    ):
        raise AdapterConformanceError(f"fixture path is not repository-relative: {raw!r}")
    return raw


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AdapterConformanceError(f"duplicate JSON member: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> Any:
    raise AdapterConformanceError(f"non-finite JSON number: {value}")


def _bounded_token(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 255:
        raise AdapterConformanceError(f"{field} must be bounded nonempty text")
    return value


def _keys_below(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        return set(value).union(*(_keys_below(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_keys_below(item) for item in value))
    return set()


def opaque_file_token(root: Path, *, exclude: Sequence[str] = ()) -> str:
    """Deterministic fixture helper; not a normative adapter token algorithm."""
    excluded = set(exclude)
    digest = hashlib.sha256()
    for path, content in _inventory_repository(root).items():
        if path in excluded:
            continue
        digest.update(len(path.encode()).to_bytes(8, "big"))
        digest.update(path.encode())
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return "fixture:" + digest.hexdigest()
