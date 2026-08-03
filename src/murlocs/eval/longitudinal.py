"""Deterministic longitudinal joins between curation records and recorded runs."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from murlocs.curation import CurationEvent, CurationRecord, load_record
from murlocs.errors import MurlocsError
from murlocs.eval.harness import compare_runs, guidance_bytes
from murlocs.eval.model import METRIC_DEFINITIONS, ComparisonSummary, RunRecord, TaskDefinition
from murlocs.eval.store import load_runs, load_task

LONGITUDINAL_SCHEMA_VERSION = 1
SERIES_ID_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
RFC3339_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\Z"
)

LONGITUDINAL_METRIC_DEFINITIONS = {
    **METRIC_DEFINITIONS,
    "active_bytes_before": "UTF-8 bytes in one affected active guidance chain before apply.",
    "active_bytes_after": "UTF-8 bytes in one affected active guidance chain after apply.",
    "active_bytes_delta": (
        "Sum of active_bytes_after minus active_bytes_before across one proposal's affected chains."
    ),
    "acceptance_rate": (
        "Proposals with an accepted lifecycle event divided by all proposals in this series."
    ),
    "replacement_to_addition_ratio": (
        "Applied replace proposals divided by applied add proposals; null when none were added."
    ),
    "time_to_decision_seconds": (
        "Seconds from proposed to the first accepted, rejected, or withdrawn event."
    ),
    "recorded_active_bytes_delta": (
        "Sum of per-proposal, per-affected-chain byte deltas in this series. Shared maps may "
        "appear in more than one chain, so this is recorded chain growth, not unique file bytes."
    ),
    "cumulative_recorded_active_bytes_delta": (
        "Running sum of proposal affected-chain deltas ordered by apply or terminal outcome time."
    ),
    "efficiency_delta": (
        "After minus before for a recorded efficiency metric, emitted only when both runs pass "
        "the task correctness threshold. Negative values mean lower recorded cost."
    ),
    "causal_limit": (
        "Longitudinal association on pinned recorded runs is correlation, not proof that guidance "
        "caused an outcome or will generalize. Results never authorize automatic curation."
    ),
}


@dataclass(frozen=True)
class RevisionLink:
    repository_before: str
    repository_after: str | None
    source_before: str
    source_after: str | None
    guidance_before: str
    guidance_after: str | None


@dataclass(frozen=True)
class ProposalLink:
    record_path: str
    record: CurationRecord
    revisions: RevisionLink
    affected_chains: tuple[AffectedChainLink, ...]

    @property
    def affected_scopes(self) -> tuple[str, ...]:
        return tuple(sorted({item.scope for item in self.affected_chains}))


@dataclass(frozen=True)
class AffectedChainLink:
    scope: str
    chain: tuple[str, ...]
    active_bytes_before: int
    active_bytes_after: int | None


@dataclass(frozen=True)
class RunObservation:
    proposal_id: str
    phase: str
    scope: str
    chain: tuple[str, ...]
    source_revision: str
    task_path: str
    runs_path: str
    task: TaskDefinition
    records: tuple[RunRecord, ...]
    summary: ComparisonSummary

    @property
    def key(self) -> tuple[str, str, tuple[str, ...]]:
        return self.task.id, self.scope, self.chain


@dataclass(frozen=True)
class LongitudinalDataset:
    series_id: str
    proposals: tuple[ProposalLink, ...]
    observations: tuple[RunObservation, ...]


def load_longitudinal(path: Path) -> LongitudinalDataset:
    """Load and cross-validate a versioned, read-only longitudinal link manifest."""
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read longitudinal file {path}: {exc}") from exc
    payload = _mapping(data, str(path))
    _exact_fields(payload, {"schema_version", "series_id", "proposals", "observations"}, str(path))
    if payload["schema_version"] != LONGITUDINAL_SCHEMA_VERSION:
        raise ValueError(
            f"{path}: unsupported schema_version {payload['schema_version']!r}; "
            f"expected {LONGITUDINAL_SCHEMA_VERSION}"
        )
    series_id = _portable_id(_nonempty(payload, "series_id", str(path)), f"{path}: series_id")
    base = path.parent

    proposals = tuple(
        _load_proposal(base, item, f"{path}: proposals[{index}]")
        for index, item in enumerate(_array(payload, "proposals", str(path)))
    )
    if not proposals:
        raise ValueError(f"{path}: proposals must contain at least one link")
    proposal_index: dict[str, ProposalLink] = {}
    for item in proposals:
        if item.record.id in proposal_index:
            raise ValueError(f"{path}: ambiguous duplicate proposal link {item.record.id!r}")
        proposal_index[item.record.id] = item
    _validate_supersession_links(proposal_index, str(path))

    observations = tuple(
        _load_observation(base, item, proposal_index, f"{path}: observations[{index}]")
        for index, item in enumerate(_array(payload, "observations", str(path)))
    )
    _validate_observation_links(proposals, observations, str(path))
    return LongitudinalDataset(series_id, proposals, observations)


def analyze_longitudinal(dataset: LongitudinalDataset) -> dict[str, Any]:
    """Produce deterministic lifecycle and correctness-gated longitudinal summaries."""
    ordered_proposals = sorted(
        dataset.proposals,
        key=lambda item: (_event_time(item.record.events[0], item.record.id), item.record.id),
    )
    state_counts = Counter(item.record.state for item in ordered_proposals)
    intent_counts = Counter(item.record.intent for item in ordered_proposals)
    accepted_count = sum(
        any(event.state == "accepted" for event in item.record.events)
        for item in ordered_proposals
    )
    applied_additions = sum(
        item.record.intent == "add" and _apply_event(item.record) is not None
        for item in ordered_proposals
    )
    applied_replacements = sum(
        item.record.intent == "replace" and _apply_event(item.record) is not None
        for item in ordered_proposals
    )
    timeline = [
        _timeline_entry(item)
        for item in sorted(
            ordered_proposals,
            key=lambda value: (_timeline_time(value.record), value.record.id),
        )
    ]
    cumulative_delta = 0
    for item in timeline:
        cumulative_delta += item["active_bytes_delta"] or 0
        item["cumulative_recorded_active_bytes_delta"] = cumulative_delta
    observations_by_proposal: dict[str, list[RunObservation]] = defaultdict(list)
    for observation in dataset.observations:
        observations_by_proposal[observation.proposal_id].append(observation)

    comparisons: list[dict[str, Any]] = []
    for proposal in ordered_proposals:
        proposal_observations = observations_by_proposal[proposal.record.id]
        before = {item.key: item for item in proposal_observations if item.phase == "before"}
        after = {item.key: item for item in proposal_observations if item.phase == "after"}
        for key in sorted(before):
            if key not in after:
                continue
            comparisons.append(_comparison(proposal.record.id, before[key], after[key]))

    evidence = [
        _proposal_evidence(item, observations_by_proposal[item.record.id])
        for item in ordered_proposals
    ]
    total_delta = sum(
        item["active_bytes_delta"] for item in timeline if item["active_bytes_delta"] is not None
    )
    return {
        "schema_version": LONGITUDINAL_SCHEMA_VERSION,
        "series_id": dataset.series_id,
        "summary": {
            "proposal_count": len(ordered_proposals),
            "states": dict(sorted(state_counts.items())),
            "intents": dict(sorted(intent_counts.items())),
            "accepted": accepted_count,
            "acceptance_rate": accepted_count / len(ordered_proposals),
            "applied_additions": applied_additions,
            "applied_replacements": applied_replacements,
            "supersessions": state_counts["superseded"],
            "rejections": state_counts["rejected"],
            "pruning": state_counts["pruned"],
            "replacement_to_addition_ratio": (
                applied_replacements / applied_additions if applied_additions else None
            ),
            "recorded_active_bytes_delta": total_delta,
        },
        "active_bytes_timeline": timeline,
        "comparisons": comparisons,
        "metric_definitions": dict(sorted(LONGITUDINAL_METRIC_DEFINITIONS.items())),
        "raw_evidence": evidence,
    }


def save_longitudinal_results(directory: Path, result: dict[str, Any]) -> Path:
    """Write an explicit result artifact; inputs and repositories remain untouched."""
    series_id = _portable_id(str(result.get("series_id", "")), "series id")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{series_id}.json"
    target.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def render_longitudinal_summary(result: dict[str, Any]) -> str:
    summary = result["summary"]
    ratio = summary["replacement_to_addition_ratio"]
    ratio_text = "n/a" if ratio is None else f"{ratio:.2f}"
    lines = [
        f"series: {result['series_id']}",
        f"proposals: {summary['proposal_count']}",
        f"states: {json.dumps(summary['states'], sort_keys=True)}",
        f"acceptance rate: {summary['acceptance_rate']:.1%}",
        f"replacement/addition ratio: {ratio_text}",
        f"recorded active-byte delta: {summary['recorded_active_bytes_delta']:+d}",
        f"correctness-gated comparisons: {len(result['comparisons'])}",
    ]
    return "\n".join(lines)


def _load_proposal(base: Path, value: Any, context: str) -> ProposalLink:
    item = _mapping(value, context)
    _exact_fields(
        item,
        {
            "record",
            "revisions",
            "affected_chains",
        },
        context,
    )
    record_path = _nonempty(item, "record", context)
    resolved = _referenced_file(base, record_path, f"{context}.record")
    try:
        record = load_record(resolved, expected_id=resolved.stem)
    except MurlocsError as exc:
        raise ValueError(f"{context}.record: {exc}") from exc
    revisions = _load_revisions(item["revisions"], f"{context}.revisions")
    raw_chains = _chain_links(item, context)
    chain_identities = [(value.scope, value.chain) for value in raw_chains]
    if len(set(chain_identities)) != len(chain_identities):
        raise ValueError(f"{context}: ambiguous duplicate affected guidance chain")
    chains = tuple(sorted(raw_chains, key=lambda value: (value.scope, value.chain)))
    if not chains:
        raise ValueError(f"{context}.affected_chains must not be empty")
    _validate_revision_link(record, revisions, chains, context)
    _validate_event_times(record, context)
    return ProposalLink(record_path, record, revisions, chains)


def _load_revisions(value: Any, context: str) -> RevisionLink:
    item = _mapping(value, context)
    fields = {
        "repository_before",
        "repository_after",
        "source_before",
        "source_after",
        "guidance_before",
        "guidance_after",
    }
    _exact_fields(item, fields, context)
    return RevisionLink(
        repository_before=_nonempty(item, "repository_before", context),
        repository_after=_optional_nonempty(
            item.get("repository_after"), f"{context}.repository_after"
        ),
        source_before=_nonempty(item, "source_before", context),
        source_after=_optional_nonempty(item.get("source_after"), f"{context}.source_after"),
        guidance_before=_nonempty(item, "guidance_before", context),
        guidance_after=_optional_nonempty(item.get("guidance_after"), f"{context}.guidance_after"),
    )


def _load_observation(
    base: Path,
    value: Any,
    proposals: dict[str, ProposalLink],
    context: str,
) -> RunObservation:
    item = _mapping(value, context)
    _exact_fields(
        item,
        {"proposal_id", "phase", "scope", "chain", "source_revision", "task", "runs"},
        context,
    )
    proposal_id = _nonempty(item, "proposal_id", context)
    if proposal_id not in proposals:
        raise ValueError(f"{context}: missing proposal link {proposal_id!r}")
    proposal = proposals[proposal_id]
    phase = _nonempty(item, "phase", context)
    if phase not in {"before", "after"}:
        raise ValueError(f"{context}.phase must be 'before' or 'after'")
    scope = _nonempty(item, "scope", context)
    chain = tuple(_string_array(item, "chain", context))
    matching_chains = [
        item for item in proposal.affected_chains if item.scope == scope and item.chain == chain
    ]
    if scope not in proposal.affected_scopes:
        raise ValueError(f"{context}: scope {scope!r} is not affected by proposal {proposal_id!r}")
    if not matching_chains:
        raise ValueError(f"{context}: chain does not exactly match an affected guidance chain")
    if scope not in chain:
        raise ValueError(f"{context}: scope {scope!r} is absent from its guidance chain")

    expected_source = getattr(proposal.revisions, f"source_{phase}")
    expected_repository = getattr(proposal.revisions, f"repository_{phase}")
    expected_guidance = getattr(proposal.revisions, f"guidance_{phase}")
    if expected_source is None or expected_repository is None or expected_guidance is None:
        raise ValueError(f"{context}: proposal {proposal_id!r} has no {phase} promotion revision")
    source_revision = _nonempty(item, "source_revision", context)
    if source_revision != expected_source:
        raise ValueError(
            f"{context}: source_revision {source_revision!r} does not match proposal "
            f"{phase} revision {expected_source!r}"
        )
    task_path = _nonempty(item, "task", context)
    runs_path = _nonempty(item, "runs", context)
    task = load_task(_referenced_file(base, task_path, f"{context}.task"))
    records = tuple(load_runs(_referenced_file(base, runs_path, f"{context}.runs"), task))
    if task.repository_revision != expected_repository:
        raise ValueError(
            f"{context}: task repository_revision {task.repository_revision!r} does not match "
            f"proposal {phase} revision {expected_repository!r}"
        )
    murlocs_runs = [record for record in records if record.arm == "murlocs"]
    if len(murlocs_runs) != 1:
        raise ValueError(f"{context}: expected exactly one murlocs recorded run")
    if murlocs_runs[0].guidance_revision != expected_guidance:
        raise ValueError(
            f"{context}: murlocs guidance_revision {murlocs_runs[0].guidance_revision!r} "
            f"does not match proposal {phase} revision {expected_guidance!r}"
        )
    expected_bytes = getattr(matching_chains[0], f"active_bytes_{phase}")
    if expected_bytes is None or guidance_bytes(murlocs_runs[0].guidance_text) != expected_bytes:
        raise ValueError(
            f"{context}: murlocs guidance bytes do not match affected-chain "
            f"active_bytes_{phase}"
        )
    return RunObservation(
        proposal_id,
        phase,
        scope,
        chain,
        source_revision,
        task_path,
        runs_path,
        task,
        records,
        compare_runs(task, list(records)),
    )


def _validate_revision_link(
    record: CurationRecord,
    revisions: RevisionLink,
    chains: tuple[AffectedChainLink, ...],
    context: str,
) -> None:
    if revisions.source_before != record.base_source_sha256:
        raise ValueError(
            f"{context}: source_before does not match record base_source_sha256"
        )
    event = _apply_event(record)
    after_values = (
        revisions.repository_after,
        revisions.source_after,
        revisions.guidance_after,
        *(item.active_bytes_after for item in chains),
    )
    if event is None:
        if any(value is not None for value in after_values):
            raise ValueError(
                f"{context}: unapplied proposal may not declare after revisions or bytes"
            )
        return
    if any(value is None for value in after_values):
        raise ValueError(f"{context}: applied proposal requires all after revisions and bytes")
    if event.source_before_sha256 != revisions.source_before:
        raise ValueError(f"{context}: source_before does not match apply event")
    if event.source_after_sha256 != revisions.source_after:
        raise ValueError(f"{context}: source_after does not match apply event")


def _validate_supersession_links(proposals: dict[str, ProposalLink], context: str) -> None:
    for proposal in proposals.values():
        if proposal.record.state != "superseded":
            continue
        related_id = proposal.record.events[-1].related_proposal_id
        related = proposals.get(related_id or "")
        if related is None:
            raise ValueError(
                f"{context}: superseded proposal {proposal.record.id!r} has missing related "
                f"proposal link {related_id!r}"
            )
        related_event = _apply_event(related.record)
        if (
            related.record.intent != "replace"
            or related_event is None
            or related_event.related_proposal_id != proposal.record.id
        ):
            raise ValueError(
                f"{context}: supersession link {proposal.record.id!r} -> {related.record.id!r} "
                "is not reciprocal"
            )


def _validate_observation_links(
    proposals: tuple[ProposalLink, ...],
    observations: tuple[RunObservation, ...],
    context: str,
) -> None:
    seen: set[tuple[str, str, str, str, tuple[str, ...]]] = set()
    grouped: dict[
        str, dict[str, dict[tuple[str, str, tuple[str, ...]], RunObservation]]
    ] = defaultdict(lambda: {"before": {}, "after": {}})
    for item in observations:
        identity = (item.proposal_id, item.phase, item.task.id, item.scope, item.chain)
        if identity in seen:
            raise ValueError(
                f"{context}: ambiguous duplicate observation for proposal {item.proposal_id!r}, "
                f"phase {item.phase!r}, task {item.task.id!r}, scope {item.scope!r}"
            )
        seen.add(identity)
        grouped[item.proposal_id][item.phase][item.key] = item
    for proposal in proposals:
        phases = grouped[proposal.record.id]
        if not phases["before"]:
            raise ValueError(
                f"{context}: missing before recorded-run link for proposal {proposal.record.id!r}"
            )
        applied = _apply_event(proposal.record) is not None
        if applied and set(phases["before"]) != set(phases["after"]):
            raise ValueError(
                f"{context}: proposal {proposal.record.id!r} requires matching before/after "
                "task, scope, and chain links"
            )
        if not applied and phases["after"]:
            raise ValueError(
                f"{context}: unapplied proposal {proposal.record.id!r} may not have after links"
            )
        for key in sorted(phases["before"]):
            if key in phases["after"]:
                _require_compatible_runs(phases["before"][key], phases["after"][key], context)


def _require_compatible_runs(before: RunObservation, after: RunObservation, context: str) -> None:
    before_task = asdict(before.task)
    after_task = asdict(after.task)
    before_task.pop("repository_revision")
    after_task.pop("repository_revision")
    if before_task != after_task:
        raise ValueError(
            f"{context}: revision-incompatible task definitions for proposal "
            f"{before.proposal_id!r}, task {before.task.id!r}"
        )
    before_run = _murlocs_run(before)
    after_run = _murlocs_run(after)
    if (before_run.model, before_run.ade) != (after_run.model, after_run.ade):
        raise ValueError(
            f"{context}: revision-incompatible model/ADE for proposal "
            f"{before.proposal_id!r}, task {before.task.id!r}"
        )


def _comparison(proposal_id: str, before: RunObservation, after: RunObservation) -> dict[str, Any]:
    before_score = _murlocs_score(before.summary)
    after_score = _murlocs_score(after.summary)
    gate = before_score.correctness.passed and after_score.correctness.passed
    delta = None
    if gate:
        assert before_score.efficiency is not None and after_score.efficiency is not None
        delta = {
            field: getattr(after_score.efficiency, field) - getattr(before_score.efficiency, field)
            for field in (
                "files_inspected",
                "lines_inspected",
                "tool_calls",
                "executable_steps",
                "active_guidance_bytes",
                "estimated_prompt_tokens",
            )
        }
    return {
        "proposal_id": proposal_id,
        "task_id": before.task.id,
        "scope": before.scope,
        "chain": list(before.chain),
        "before": _score_payload(before, before_score, include_efficiency=gate),
        "after": _score_payload(after, after_score, include_efficiency=gate),
        "correctness_gate_passed": gate,
        "efficiency_delta": delta,
        "gate_reason": (
            None if gate else "efficiency withheld until both revisions pass correctness"
        ),
    }


def _score_payload(
    observation: RunObservation, score: Any, *, include_efficiency: bool
) -> dict[str, Any]:
    return {
        "repository_revision": observation.task.repository_revision,
        "source_revision": observation.source_revision,
        "guidance_revision": score.guidance_revision,
        "correctness": asdict(score.correctness),
        "efficiency": (
            asdict(score.efficiency)
            if include_efficiency and score.efficiency is not None
            else None
        ),
    }


def _timeline_entry(proposal: ProposalLink) -> dict[str, Any]:
    before = sum(item.active_bytes_before for item in proposal.affected_chains)
    has_after = all(item.active_bytes_after is not None for item in proposal.affected_chains)
    after = (
        sum(item.active_bytes_after or 0 for item in proposal.affected_chains)
        if has_after
        else None
    )
    return {
        "proposal_id": proposal.record.id,
        "proposed_at": proposal.record.events[0].at,
        "outcome_at": (_apply_event(proposal.record) or proposal.record.events[-1]).at,
        "state": proposal.record.state,
        "intent": proposal.record.intent,
        "active_bytes_before": before,
        "active_bytes_after": after,
        "active_bytes_delta": None if after is None else after - before,
        "affected_chains": [_chain_payload(item) for item in proposal.affected_chains],
        "time_to_decision_seconds": _time_to_decision(proposal.record),
    }


def _proposal_evidence(
    proposal: ProposalLink, observations: list[RunObservation]
) -> dict[str, Any]:
    return {
        "proposal_id": proposal.record.id,
        "lifecycle_state": proposal.record.state,
        "record_path": proposal.record_path,
        "record": asdict(proposal.record),
        "revisions": asdict(proposal.revisions),
        "affected_scopes": list(proposal.affected_scopes),
        "affected_chains": [_chain_payload(item) for item in proposal.affected_chains],
        "observations": [
            {
                "phase": item.phase,
                "scope": item.scope,
                "chain": list(item.chain),
                "source_revision": item.source_revision,
                "task_path": item.task_path,
                "runs_path": item.runs_path,
                "task": asdict(item.task),
                "records": [asdict(record) for record in item.records],
            }
            for item in sorted(observations, key=lambda value: (value.phase, value.key))
        ],
    }


def _apply_event(record: CurationRecord) -> CurationEvent | None:
    expected = "pruned" if record.intent == "remove" else "promoted"
    return next((event for event in record.events if event.state == expected), None)


def _timeline_time(record: CurationRecord) -> datetime:
    event = _apply_event(record) or record.events[-1]
    return _event_time(event, record.id)


def _chain_payload(item: AffectedChainLink) -> dict[str, Any]:
    return {
        "scope": item.scope,
        "chain": list(item.chain),
        "active_bytes_before": item.active_bytes_before,
        "active_bytes_after": item.active_bytes_after,
    }


def _event_time(event: CurationEvent, proposal_id: str) -> datetime:
    if RFC3339_PATTERN.fullmatch(event.at) is None:
        raise ValueError(
            f"proposal {proposal_id!r} event {event.state!r} has invalid RFC3339 time "
            f"{event.at!r}"
        )
    try:
        parsed = datetime.fromisoformat(event.at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"proposal {proposal_id!r} event {event.state!r} has invalid RFC3339 time {event.at!r}"
        ) from exc
    if parsed.tzinfo is None:
        raise ValueError(
            f"proposal {proposal_id!r} event {event.state!r} time must include an offset"
        )
    return parsed


def _validate_event_times(record: CurationRecord, context: str) -> None:
    times = [_event_time(event, record.id) for event in record.events]
    if times != sorted(times):
        raise ValueError(f"{context}: proposal {record.id!r} lifecycle times are not monotonic")


def _time_to_decision(record: CurationRecord) -> float | None:
    decision = next(
        (event for event in record.events if event.state in {"accepted", "rejected", "withdrawn"}),
        None,
    )
    if decision is None:
        return None
    elapsed = _event_time(decision, record.id) - _event_time(record.events[0], record.id)
    return elapsed.total_seconds()


def _murlocs_run(observation: RunObservation) -> RunRecord:
    return next(record for record in observation.records if record.arm == "murlocs")


def _murlocs_score(summary: ComparisonSummary) -> Any:
    return next(score for score in summary.scores if score.arm == "murlocs")


def _referenced_file(base: Path, raw: str, context: str) -> Path:
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError(f"{context} must be a safe path relative to the longitudinal file")
    current = base
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{context} may not traverse a symlink: {raw}")
    if not current.is_file():
        raise ValueError(f"{context} is not a readable regular file: {raw}")
    return current


def _portable_id(value: str, context: str) -> str:
    if len(value) > 128 or ".." in value or SERIES_ID_PATTERN.fullmatch(value) is None:
        raise ValueError(
            f"{context} must be 1-128 portable ASCII letters, digits, dots, underscores, or hyphens"
        )
    return value


def _exact_fields(data: dict[str, Any], fields: set[str], context: str) -> None:
    missing = sorted(fields - set(data))
    unknown = sorted(set(data) - fields)
    if missing:
        raise ValueError(f"{context}: missing fields: {', '.join(missing)}")
    if unknown:
        raise ValueError(f"{context}: unknown fields: {', '.join(unknown)}")


def _mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object")
    return value


def _array(data: dict[str, Any], key: str, context: str) -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{context}.{key} must be an array")
    return value


def _nonempty(data: dict[str, Any], key: str, context: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}.{key} must be a non-empty string")
    return value


def _optional_nonempty(value: Any, context: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must be null or a non-empty string")
    return value


def _string_array(data: dict[str, Any], key: str, context: str) -> list[str]:
    values = data.get(key)
    if not isinstance(values, list) or any(
        not isinstance(value, str) or not value.strip() for value in values
    ):
        raise ValueError(f"{context}.{key} must be an array of non-empty strings")
    return values


def _chain_links(data: dict[str, Any], context: str) -> list[AffectedChainLink]:
    values = data.get("affected_chains")
    if not isinstance(values, list):
        raise ValueError(f"{context}.affected_chains must be an array")
    chains: list[AffectedChainLink] = []
    for index, value in enumerate(values):
        item_context = f"{context}.affected_chains[{index}]"
        item = _mapping(value, item_context)
        _exact_fields(
            item,
            {"scope", "chain", "active_bytes_before", "active_bytes_after"},
            item_context,
        )
        scope = _nonempty(item, "scope", item_context)
        chain = tuple(_string_array(item, "chain", item_context))
        if not chain:
            raise ValueError(f"{item_context}.chain must not be empty")
        if scope not in chain:
            raise ValueError(f"{item_context}: scope {scope!r} is absent from its chain")
        chains.append(
            AffectedChainLink(
                scope,
                chain,
                _nonnegative(item["active_bytes_before"], f"{item_context}.active_bytes_before"),
                _optional_nonnegative(
                    item["active_bytes_after"], f"{item_context}.active_bytes_after"
                ),
            )
        )
    return chains


def _nonnegative(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{context} must be a non-negative integer")
    return value


def _optional_nonnegative(value: Any, context: str) -> int | None:
    if value is None:
        return None
    return _nonnegative(value, context)
