from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from murlocs.adapter_conformance import (
    ADAPTER_CONTRACT,
    REQUIRED_CAPABILITIES,
    AdapterConformanceError,
    AdapterDriver,
    ConformanceContext,
    assert_adapter_conformance,
    default_suite_path,
    load_adapter_suite,
    opaque_file_token,
    run_adapter_conformance,
)
from murlocs.outcome import bind_integration_tokens, merge_outcomes, parse_outcome

ROOT = Path(__file__).parents[1]
OUTCOME_FIXTURE = ROOT / "tests/fixtures/outcome-envelope/v1/conformance.json"


def load_outcomes() -> dict[str, dict[str, Any]]:
    value = json.loads(OUTCOME_FIXTURE.read_text(encoding="utf-8"))
    return {case["id"]: case["outcome"] for case in value["cases"]}


class FixtureAdapter(AdapterDriver):
    def __init__(self) -> None:
        self.outcomes = load_outcomes()

    def descriptor(self) -> Mapping[str, Any]:
        return {
            "contract": ADAPTER_CONTRACT,
            "schema_version": 1,
            "adapter_id": "fixture-adapter",
            "adapter_version": "1",
            "lifecycle_versions": [1],
            "outcome_versions": [1],
            "required_capabilities": list(REQUIRED_CAPABILITIES),
            "optional_capabilities": [
                "exact-proof-cache",
                "deterministic-repair-dispatch",
                "native-task-start",
                "native-post-edit",
                "native-pre-completion",
            ],
            "events": {
                "task-start": "host-enforced",
                "prospective-impact": "prompt-mediated",
                "post-edit": "host-enforced",
                "pre-commit": "host-enforced",
                "pre-completion": "host-enforced",
            },
            "root_formats": ["posix", "windows-drive", "windows-unc"],
            "fallbacks": ["generated-guidance", "git-hook", "ci"],
            "deprecated_versions": [],
        }

    def invoke(
        self, request: Mapping[str, Any], context: ConformanceContext
    ) -> Mapping[str, Any]:
        control = context.control
        fault = control["fault"]
        event = request["event"]
        root = self._root(control["root_format"], context.root)
        scope = {
            "adapter_id": "fixture-adapter",
            "adapter_version": "1",
            "session_id": "fixture-session",
        }
        state_before = opaque_file_token(
            context.root, exclude=(".adapter-conformance/impact-dependency",)
        )
        dependency_before = self._dependency_token(context.root)
        calls = self._calls(event, fault, control["cache"])
        trusted: dict[str, Any] = {
            "root": root,
            "manifest": ".murlocs/manifest.toml",
            "view": "index" if event == "pre-commit" else "worktree",
            "token_scope": scope,
            "state_id": state_before,
        }
        if "impact" in calls:
            trusted["impact_dependency_id"] = dependency_before
        if control["cache"] in {"hit", "rejected"}:
            trusted["manifest_identity"] = "manifest:fixture"
            proof = self._cache_proof(request, trusted)
            if control["cache"] == "rejected":
                proof["state_id"] = "fixture:stale"
            trusted["cache_offer"] = {
                "cache_id": f"cache:{context.scenario_id}",
                "proof": proof,
            }
        if fault in {"agent-token", "unsupported-version"}:
            agent_token = fault == "agent-token"
            return {
                "trusted_context": trusted,
                "response": self._failure(
                    request,
                    trusted,
                    status="invalid",
                    cache="forbidden" if agent_token else "miss",
                    fallback=(
                        ["generated-guidance", "git-hook", "ci"] if agent_token else []
                    ),
                ),
            }
        if fault == "absent":
            return {
                "trusted_context": trusted,
                "response": self._failure(
                    request,
                    trusted,
                    status="not_applicable",
                    cache="miss",
                    fallback=[],
                    silent=True,
                ),
            }
        if fault == "unavailable":
            return {
                "trusted_context": trusted,
                "response": self._failure(
                    request,
                    trusted,
                    status="unavailable",
                    cache="forbidden",
                    fallback=["generated-guidance", "git-hook", "ci"],
                ),
            }

        for operation in calls:
            context.record_operation(operation)
            if fault == "timeout":
                return {
                    "trusted_context": trusted,
                    "response": self._failure(
                        request,
                        trusted,
                        status="timeout",
                        cache=control["cache"],
                        fallback=[],
                        next_actions=[self._retry_action(event)],
                    ),
                }
            if fault == "malformed":
                return {
                    "trusted_context": trusted,
                    "response": self._failure(
                        request,
                        trusted,
                        status="invalid",
                        cache=control["cache"],
                        fallback=["generated-guidance", "git-hook", "ci"],
                    ),
                }
        if calls:
            context.checkpoint(f"after-{calls[-1]}")
        state_after = opaque_file_token(
            context.root, exclude=(".adapter-conformance/impact-dependency",)
        )
        dependency_after = self._dependency_token(context.root)
        if fault in {"state-race", "dependency-race"}:
            execution: dict[str, Any] = {
                "status": "stale",
                "code": "MURLOCS_ACTIVATION_STALE",
                "observed_state_id": state_after,
            }
            if "impact" in calls:
                execution["observed_dependency_id"] = dependency_after
            response = self._base_response(
                request,
                trusted,
                execution=execution,
                operations=[],
                cache=control["cache"],
                outcome=None,
                silent=False,
                fallback=["git-hook", "ci"],
                next_actions=[self._fallback_action("git-hook")],
            )
            return {"trusted_context": trusted, "response": response}

        receipts = [
            self._receipt(
                operation,
                state_before,
                dependency_before if operation == "impact" else None,
            )
            for operation in self._receipt_operations(event)
        ]
        outcome = self._bound_outcome(
            control["outcome"],
            request,
            state_before,
            dependency_before if any(item["operation"] == "impact" for item in receipts) else None,
            scope,
            tuple(item["operation"] for item in receipts),
        )
        resolution = None if outcome is None else outcome["resolution_class"]
        blocking = resolution == "deterministic_repair"
        response = self._base_response(
            request,
            trusted,
            execution={"status": "completed", "code": "MURLOCS_ACTIVATION_OK"},
            operations=receipts,
            cache=control["cache"],
            outcome=outcome,
            silent=resolution == "pass",
            fallback=[],
            next_actions=[],
            blocking=blocking,
        )
        return {"trusted_context": trusted, "response": response}

    @staticmethod
    def _cache_proof(
        request: Mapping[str, Any], trusted: Mapping[str, Any]
    ) -> dict[str, Any]:
        operations = {
            "task-start": ["check"],
            "prospective-impact": ["impact"],
            "post-edit": ["check", "impact"],
            "pre-commit": ["check", "impact"],
            "pre-completion": ["check", "impact"],
        }[request["event"]]
        proof: dict[str, Any] = {
            "contract_version": "1",
            "adapter_id": trusted["token_scope"]["adapter_id"],
            "adapter_version": trusted["token_scope"]["adapter_version"],
            "session_id": trusted["token_scope"]["session_id"],
            "event": request["event"],
            "operations": operations,
            "operation_inputs": {
                field: request[field]
                for field in ("paths", "baseline", "enforcement")
                if field in request
            },
            "manifest_identity": trusted["manifest_identity"],
            "state_id": trusted["state_id"],
        }
        if "impact" in operations:
            proof["impact_dependency_id"] = trusted["impact_dependency_id"]
        return proof

    @staticmethod
    def _calls(event: str, fault: str | None, cache: str) -> tuple[str, ...]:
        if cache == "hit" or fault in {
            "absent",
            "unavailable",
            "agent-token",
            "unsupported-version",
        }:
            return ()
        operations = {
            "task-start": ("check",),
            "prospective-impact": ("impact",),
            "post-edit": ("check", "impact"),
            "pre-commit": ("check", "impact"),
            "pre-completion": ("check", "impact"),
        }[event]
        if fault in {"timeout", "malformed"}:
            return operations[:1]
        return operations

    @staticmethod
    def _receipt_operations(event: str) -> tuple[str, ...]:
        return {
            "task-start": ("check",),
            "prospective-impact": ("impact",),
            "post-edit": ("check", "impact"),
            "pre-commit": ("check", "impact"),
            "pre-completion": ("check", "impact"),
        }[event]

    @staticmethod
    def _dependency_token(root: Path) -> str:
        content = (root / ".adapter-conformance/impact-dependency").read_bytes()
        return "fixture-dependency:" + hashlib.sha256(content).hexdigest()

    @staticmethod
    def _root(kind: str, root: Path) -> dict[str, Any]:
        if kind == "windows-drive":
            return {"format": "windows-drive", "drive": "C", "segments": [root.name]}
        if kind == "windows-unc":
            return {
                "format": "windows-unc",
                "server": "fixture-server",
                "share": "fixture-share",
                "segments": [root.name],
            }
        return {"format": "posix", "segments": [root.name]}

    @staticmethod
    def _receipt(operation: str, state: str, dependency: str | None) -> dict[str, Any]:
        receipt: dict[str, Any] = {
            "operation": operation,
            "exit_code": 0,
            "output_sha256": "sha256:" + "a" * 64,
            "state_before": state,
            "state_after": state,
        }
        if dependency is not None:
            receipt.update(
                dependency_before_id=dependency,
                dependency_after_id=dependency,
            )
        return receipt

    def _bound_outcome(
        self,
        outcome_id: str | None,
        request: Mapping[str, Any],
        state: str,
        dependency: str | None,
        scope: Mapping[str, Any],
        operations: tuple[str, ...],
    ) -> dict[str, Any] | None:
        if outcome_id is None:
            return None
        fixture_id = {
            "pass": "pass",
            "deterministic-repair": "deterministic-repair",
            "agent-action": "agent-action",
            "authority-required": "authority-required",
        }[outcome_id]
        template = copy.deepcopy(self.outcomes[fixture_id])
        template["correlation"] = {
            "correlation_id": request["correlation_id"],
            "state_id": None,
            "dependency_id": None,
            "token_source": "none",
            "token_scope": None,
        }
        template["source"]["operation"] = operations[0]
        bound = bind_integration_tokens(
            template,
            correlation_id=request["correlation_id"],
            state_id=state,
            dependency_id=dependency if operations[0] == "impact" else None,
            token_scope=scope,
        )
        if len(operations) == 1:
            return bound
        impact = copy.deepcopy(self.outcomes["pass"])
        impact["source"]["operation"] = "impact"
        impact["correlation"]["correlation_id"] = request["correlation_id"]
        bound_impact = bind_integration_tokens(
            impact,
            correlation_id=request["correlation_id"],
            state_id=state,
            dependency_id=dependency,
            token_scope=scope,
        )
        return merge_outcomes([bound, bound_impact])

    @staticmethod
    def _base_response(
        request: Mapping[str, Any],
        trusted: Mapping[str, Any],
        *,
        execution: Mapping[str, Any],
        operations: list[dict[str, Any]],
        cache: str,
        outcome: Mapping[str, Any] | None,
        silent: bool,
        fallback: list[str],
        next_actions: list[dict[str, Any]],
        blocking: bool | None = False,
    ) -> dict[str, Any]:
        return {
            "contract": request["contract"],
            "schema_version": request["schema_version"],
            "event": request["event"],
            "correlation_id": request["correlation_id"],
            "execution": dict(execution),
            "repository": {
                "root": trusted["root"],
                "token_scope": trusted["token_scope"],
                "manifest": trusted["manifest"],
                "view": trusted["view"],
                "state_id": trusted["state_id"],
                "blocking": blocking,
            },
            "silent": silent,
            "operations": operations,
            "cache": {
                "decision": cache,
                **(
                    {"key": trusted["cache_offer"]["cache_id"]}
                    if cache in {"hit", "rejected"}
                    else {}
                ),
            },
            "outcome": outcome,
            "writes": [],
            "fallback": fallback,
            "next_actions": next_actions,
            "summary": f"Fixture adapter {execution['status']}.",
        }

    def _failure(
        self,
        request: Mapping[str, Any],
        trusted: Mapping[str, Any],
        *,
        status: str,
        cache: str,
        fallback: list[str],
        silent: bool = False,
        next_actions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return self._base_response(
            request,
            trusted,
            execution={
                "status": status,
                "code": {
                    "not_applicable": "MURLOCS_ACTIVATION_ABSENT",
                    "unavailable": "MURLOCS_ACTIVATION_UNAVAILABLE",
                    "timeout": "MURLOCS_ACTIVATION_TIMEOUT",
                    "invalid": "MURLOCS_ACTIVATION_INVALID",
                }[status],
            },
            operations=[],
            cache=cache,
            outcome=None,
            silent=silent,
            fallback=fallback,
            next_actions=next_actions or (
                [self._fallback_action(fallback[0])] if fallback else []
            ),
            blocking=None,
        )

    @staticmethod
    def _fallback_action(name: str) -> dict[str, Any]:
        return {
            "operation": "use_fallback",
            "arguments": {"fallback": name},
            "effect": "read_repository",
            "authority": "integration",
        }

    @staticmethod
    def _retry_action(event: str) -> dict[str, Any]:
        return {
            "operation": "retry",
            "arguments": {"event": event},
            "effect": "read_repository",
            "authority": "integration",
        }


def test_packaged_suite_is_duplicate_safe_and_versioned(tmp_path: Path):
    suite = load_adapter_suite()
    assert default_suite_path().is_file()
    assert suite["contract"] == "io.murlocs.adapter-conformance"
    assert len(suite["scenarios"]) >= 15

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"contract":"one","contract":"two"}', encoding="utf-8")
    with pytest.raises(AdapterConformanceError, match="duplicate JSON member"):
        load_adapter_suite(duplicate)

    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"value":NaN}', encoding="utf-8")
    with pytest.raises(AdapterConformanceError, match="non-finite JSON number"):
        load_adapter_suite(nonfinite)


def test_reference_adapter_passes_every_black_box_scenario(tmp_path: Path):
    report = run_adapter_conformance(FixtureAdapter(), temporary_parent=tmp_path)
    assert report["passed"] is True
    assert all(item["passed"] for item in report["scenarios"])
    assert_adapter_conformance(report)


def test_reports_are_deterministic_and_do_not_expose_opaque_tokens(tmp_path: Path):
    first = run_adapter_conformance(FixtureAdapter(), temporary_parent=tmp_path)
    second = run_adapter_conformance(FixtureAdapter(), temporary_parent=tmp_path)
    assert first == second
    assert "fixture:" not in json.dumps(first, sort_keys=True)


class PromptingAdapter(FixtureAdapter):
    def invoke(
        self, request: Mapping[str, Any], context: ConformanceContext
    ) -> Mapping[str, Any]:
        context.request_agent_input()
        return super().invoke(request, context)


class WritingAdapter(FixtureAdapter):
    def invoke(
        self, request: Mapping[str, Any], context: ConformanceContext
    ) -> Mapping[str, Any]:
        (context.root / "adapter-wrote.txt").write_text("not allowed\n", encoding="utf-8")
        return super().invoke(request, context)


class DirectoryWritingAdapter(FixtureAdapter):
    def invoke(
        self, request: Mapping[str, Any], context: ConformanceContext
    ) -> Mapping[str, Any]:
        (context.root / "adapter-created-directory").mkdir()
        return super().invoke(request, context)


class ModeWritingAdapter(FixtureAdapter):
    def invoke(
        self, request: Mapping[str, Any], context: ConformanceContext
    ) -> Mapping[str, Any]:
        (context.root / "src/app.py").chmod(0o600)
        return super().invoke(request, context)


class StaleReceiptAdapter(FixtureAdapter):
    def invoke(
        self, request: Mapping[str, Any], context: ConformanceContext
    ) -> Mapping[str, Any]:
        observation = copy.deepcopy(super().invoke(request, context))
        receipts = observation["response"]["operations"]
        if receipts:
            receipts[0]["state_after"] = "agent:invented"
        return observation


class CrashingAdapter(FixtureAdapter):
    def invoke(
        self, request: Mapping[str, Any], context: ConformanceContext
    ) -> Mapping[str, Any]:
        raise RuntimeError("injected adapter crash")


@pytest.mark.parametrize(
    ("adapter", "message"),
    [
        (PromptingAdapter(), "prompt behavior"),
        (WritingAdapter(), "changed repository state"),
        (DirectoryWritingAdapter(), "changed repository state"),
        (ModeWritingAdapter(), "changed repository state"),
        (StaleReceiptAdapter(), "not fresh"),
        (CrashingAdapter(), "RuntimeError"),
    ],
)
def test_harness_detects_prompt_write_and_freshness_failures(
    adapter: AdapterDriver, message: str, tmp_path: Path
):
    report = run_adapter_conformance(adapter, temporary_parent=tmp_path)
    assert report["passed"] is False
    assert any(message in error for item in report["scenarios"] for error in item["errors"])
    with pytest.raises(AdapterConformanceError, match="adapter conformance failed"):
        assert_adapter_conformance(report)


def test_descriptor_capabilities_versions_and_deprecation_are_closed():
    adapter = FixtureAdapter()
    descriptor = dict(adapter.descriptor())
    descriptor["required_capabilities"] = descriptor["required_capabilities"][:-1]

    class BadDescriptor(FixtureAdapter):
        def descriptor(self) -> Mapping[str, Any]:
            return descriptor

    with pytest.raises(AdapterConformanceError, match="required capabilities"):
        run_adapter_conformance(BadDescriptor())

    descriptor = dict(adapter.descriptor())
    descriptor["deprecated_versions"] = [1]

    class DeprecatedDescriptor(FixtureAdapter):
        def descriptor(self) -> Mapping[str, Any]:
            return descriptor

    with pytest.raises(AdapterConformanceError, match="active schema version"):
        run_adapter_conformance(DeprecatedDescriptor())


def test_typed_outcomes_remain_read_only_and_bound_to_receipts(tmp_path: Path):
    report = run_adapter_conformance(FixtureAdapter(), temporary_parent=tmp_path)
    by_id = {item["id"]: item for item in report["scenarios"]}
    for scenario in (
        "task-start-deterministic-repair",
        "pre-completion-deterministic-repair-blocks",
        "prospective-impact-agent-action",
        "prospective-impact-authority-required",
    ):
        assert by_id[scenario]["passed"] is True

    parsed = parse_outcome(load_outcomes()["deterministic-repair"])
    assert parsed["change"] == {"repository_state_changed": False, "paths": []}
    assert parsed["next_actions"][0]["operation"] == "compile_managed_guidance"
