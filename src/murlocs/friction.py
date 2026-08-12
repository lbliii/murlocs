"""Versioned inert guidance-friction observations.

Records under ``.murlocs/friction`` are deliberately outside the manifest source
graph and outside the curation proposal lifecycle.  This module parses and
validates observations and offers deterministic analysis helpers.  It never
mutates active guidance, never authenticates a decision, and never captures
raw prompts, hidden reasoning, or source content.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from murlocs.errors import MurlocsError
from murlocs.paths import resolve_root

FRICTION_DIRECTORY = ".murlocs/friction"
FRICTION_SCHEMA_VERSION = 1
FRICTION_CONTRACT = "io.murlocs.friction"
RECORD_KIND = "observation"
ID_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?\Z")
SCOPE_PATTERN = ID_PATTERN
MAX_SUMMARY_CHARS = 1024
MAX_REFERENCE_CHARS = 512
MAX_EVIDENCE_ITEMS = 32
MAX_GUIDANCE_REFS = 32
MAX_COST_VALUE = 10_000_000
SIGNALS = (
    "missing",
    "misleading",
    "conflicting",
    "repetitive",
    "overly_broad",
)
COST_METRICS = (
    "active_context_bytes",
    "tool_calls",
    "files_inspected",
)
EVIDENCE_KINDS = (
    "file_anchor",
    "command",
    "issue",
    "pull_request",
    "evaluation",
    "note",
)
INTENT_HINTS = ("add", "replace", "remove")
SUBJECT_KIND_HINTS = (
    "pillar",
    "search_policy",
    "operating_rule",
    "stop_and_ask",
    "done_criterion",
    "scope",
    "invariant",
    "check",
    "judgment",
    "coverage_exemption",
)
ROOT_FIELDS = {
    "friction_schema_version",
    "record_kind",
    "id",
    "signal",
    "path",
    "scope",
    "guidance_refs",
    "summary",
    "evidence",
    "observed_cost",
    "provenance",
    "proposed_resolution",
}
EVIDENCE_FIELDS = {"kind", "reference", "summary"}
COST_FIELDS = {"metric", "value", "bound"}
PROVENANCE_FIELDS = {"observer", "origin", "observed_at"}
RESOLUTION_FIELDS = {"summary", "intent_hint", "subject_kind_hint"}
FORBIDDEN_CONTENT_FIELDS = frozenset(
    {
        "prompt",
        "raw_prompt",
        "reasoning",
        "hidden_reasoning",
        "chain_of_thought",
        "source_content",
        "transcript",
        "tool_arguments",
    }
)


@dataclass(frozen=True)
class FrictionEvidence:
    kind: str
    reference: str
    summary: str


@dataclass(frozen=True)
class ObservedCost:
    metric: str
    value: int
    bound: int | None = None


@dataclass(frozen=True)
class FrictionProvenance:
    observer: str
    origin: str
    observed_at: str


@dataclass(frozen=True)
class ProposedResolution:
    summary: str
    intent_hint: str | None = None
    subject_kind_hint: str | None = None


@dataclass(frozen=True)
class FrictionObservation:
    """An inert guidance-friction observation (schema v1)."""

    schema_version: int
    id: str
    signal: str
    path: str
    summary: str
    evidence: tuple[FrictionEvidence, ...]
    observed_cost: ObservedCost
    provenance: FrictionProvenance
    scope: str | None = None
    guidance_refs: tuple[str, ...] = ()
    proposed_resolution: ProposedResolution | None = None
    record_kind: str = RECORD_KIND

    @property
    def contract(self) -> str:
        return FRICTION_CONTRACT


@dataclass(frozen=True)
class FrictionFinding:
    code: str
    message: str
    blocking: bool = True

    def payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "blocking": self.blocking}


def observation_path(root: Path, observation_id: str) -> Path:
    _validate_id(observation_id)
    _reject_friction_symlinks(root, observation_id)
    return _repo_relative_path(
        root, f"{FRICTION_DIRECTORY}/{observation_id}.toml", field="observation path"
    )


def load_observation(path: Path, *, expected_id: str | None = None) -> FrictionObservation:
    if path.is_symlink():
        raise MurlocsError(f"friction observation may not be a symlink: {path.name}")
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise MurlocsError(f"friction observation not found: {path.name}") from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise MurlocsError(f"invalid friction TOML in {path.name}: {exc}") from exc
    record = parse_observation_data(data, expected_id=expected_id, filename=path.name)
    root = _infer_repo_root(path)
    if root is not None:
        validate_observation_paths(root, record)
    return record


def parse_observation_json(
    raw: str | bytes, *, filename: str = "observation"
) -> FrictionObservation:
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        data = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise MurlocsError(f"invalid friction JSON in {filename}: {exc}") from exc
    if not isinstance(data, dict):
        raise MurlocsError(f"{filename} must be a JSON object")
    return parse_observation_data(data, filename=filename)


def parse_observation_data(
    data: dict[str, Any], *, expected_id: str | None = None, filename: str = "observation"
) -> FrictionObservation:
    _reject_forbidden_content(data, filename)
    _strict_fields(data, ROOT_FIELDS, filename)
    version = _integer(data, "friction_schema_version", filename)
    if version != FRICTION_SCHEMA_VERSION:
        raise MurlocsError(
            f"{filename} uses unsupported friction_schema_version {version}; "
            f"expected {FRICTION_SCHEMA_VERSION}"
        )
    record_kind = _string(data, "record_kind", filename)
    if record_kind != RECORD_KIND:
        raise MurlocsError(
            f"{filename}.record_kind must be {RECORD_KIND!r} "
            f"(observations are not curation proposals or authenticated decisions); "
            f"got {record_kind!r}"
        )
    observation_id = _string(data, "id", filename)
    _validate_id(observation_id)
    if expected_id is not None and observation_id != expected_id:
        raise MurlocsError(
            f"{filename} id {observation_id!r} does not match filename id {expected_id!r}"
        )

    signal = _choice(data, "signal", SIGNALS, filename)
    path = _safe_relative_string(data, "path", filename)
    scope = _optional_scope(data, filename)
    guidance_refs = (
        _safe_relative_string_array(data, "guidance_refs", filename)
        if "guidance_refs" in data
        else ()
    )
    if len(guidance_refs) > MAX_GUIDANCE_REFS:
        raise MurlocsError(
            f"{filename}.guidance_refs exceeds limit of {MAX_GUIDANCE_REFS} entries"
        )
    summary = _bounded_summary(data, "summary", filename)

    evidence_raw = _array(data, "evidence", filename)
    if not evidence_raw:
        raise MurlocsError(f"{filename}.evidence must contain at least one item")
    if len(evidence_raw) > MAX_EVIDENCE_ITEMS:
        raise MurlocsError(
            f"{filename}.evidence exceeds limit of {MAX_EVIDENCE_ITEMS} items"
        )
    evidence: list[FrictionEvidence] = []
    for index, raw_item in enumerate(evidence_raw):
        context = f"{filename}.evidence[{index}]"
        table = _table(raw_item, context)
        _reject_forbidden_content(table, context)
        _strict_fields(table, EVIDENCE_FIELDS, context)
        kind = _choice(table, "kind", EVIDENCE_KINDS, context)
        reference = _bounded_string(table, "reference", context, MAX_REFERENCE_CHARS)
        if kind == "file_anchor":
            _validate_file_anchor_reference(reference, context)
        evidence.append(
            FrictionEvidence(
                kind=kind,
                reference=reference,
                summary=_bounded_summary(table, "summary", context),
            )
        )

    cost_table = _table(data.get("observed_cost"), f"{filename}.observed_cost")
    _strict_fields(cost_table, COST_FIELDS, f"{filename}.observed_cost")
    metric = _choice(cost_table, "metric", COST_METRICS, f"{filename}.observed_cost")
    value = _integer(cost_table, "value", f"{filename}.observed_cost")
    if value < 0 or value > MAX_COST_VALUE:
        raise MurlocsError(
            f"{filename}.observed_cost.value must be between 0 and {MAX_COST_VALUE}"
        )
    bound: int | None = None
    if "bound" in cost_table:
        bound = _integer(cost_table, "bound", f"{filename}.observed_cost")
        if bound < 0 or bound > MAX_COST_VALUE:
            raise MurlocsError(
                f"{filename}.observed_cost.bound must be between 0 and {MAX_COST_VALUE}"
            )
        if value > bound:
            raise MurlocsError(
                f"{filename}.observed_cost.value {value} exceeds bound {bound}"
            )

    provenance_table = _table(data.get("provenance"), f"{filename}.provenance")
    _reject_forbidden_content(provenance_table, f"{filename}.provenance")
    _strict_fields(provenance_table, PROVENANCE_FIELDS, f"{filename}.provenance")
    provenance = FrictionProvenance(
        observer=_string(provenance_table, "observer", f"{filename}.provenance"),
        origin=_string(provenance_table, "origin", f"{filename}.provenance"),
        observed_at=_string(provenance_table, "observed_at", f"{filename}.provenance"),
    )

    proposed: ProposedResolution | None = None
    if "proposed_resolution" in data and data["proposed_resolution"] is not None:
        resolution_table = _table(
            data["proposed_resolution"], f"{filename}.proposed_resolution"
        )
        _reject_forbidden_content(resolution_table, f"{filename}.proposed_resolution")
        _strict_fields(resolution_table, RESOLUTION_FIELDS, f"{filename}.proposed_resolution")
        intent_hint = None
        if "intent_hint" in resolution_table:
            intent_hint = _choice(
                resolution_table, "intent_hint", INTENT_HINTS, f"{filename}.proposed_resolution"
            )
        subject_kind_hint = None
        if "subject_kind_hint" in resolution_table:
            subject_kind_hint = _choice(
                resolution_table,
                "subject_kind_hint",
                SUBJECT_KIND_HINTS,
                f"{filename}.proposed_resolution",
            )
        proposed = ProposedResolution(
            summary=_bounded_summary(
                resolution_table, "summary", f"{filename}.proposed_resolution"
            ),
            intent_hint=intent_hint,
            subject_kind_hint=subject_kind_hint,
        )

    return FrictionObservation(
        schema_version=version,
        id=observation_id,
        signal=signal,
        path=path,
        scope=scope,
        guidance_refs=guidance_refs,
        summary=summary,
        evidence=tuple(evidence),
        observed_cost=ObservedCost(metric=metric, value=value, bound=bound),
        provenance=provenance,
        proposed_resolution=proposed,
    )


def validate_observation_paths(root: Path, record: FrictionObservation) -> None:
    """Reject absolute, traversing, and symlink-mediated references against ``root``."""
    root_resolved = resolve_root(root)
    _reject_path_symlinks(root_resolved, record.path, label="path")
    for index, ref in enumerate(record.guidance_refs):
        _reject_path_symlinks(root_resolved, ref, label=f"guidance_refs[{index}]")
    for index, item in enumerate(record.evidence):
        if item.kind != "file_anchor":
            continue
        file_part = item.reference.split("#", 1)[0]
        _reject_path_symlinks(
            root_resolved, file_part, label=f"evidence[{index}].reference"
        )


def observation_payload(record: FrictionObservation) -> dict[str, Any]:
    """Stable structured representation of an observation (no secrets or source prose)."""
    payload: dict[str, Any] = {
        "contract": FRICTION_CONTRACT,
        "friction_schema_version": record.schema_version,
        "record_kind": record.record_kind,
        "id": record.id,
        "signal": record.signal,
        "path": record.path,
        "scope": record.scope,
        "guidance_refs": list(record.guidance_refs),
        "summary": record.summary,
        "evidence": [
            {"kind": item.kind, "reference": item.reference, "summary": item.summary}
            for item in record.evidence
        ],
        "observed_cost": {
            "metric": record.observed_cost.metric,
            "value": record.observed_cost.value,
            "bound": record.observed_cost.bound,
        },
        "provenance": {
            "observer": record.provenance.observer,
            "origin": record.provenance.origin,
            "observed_at": record.provenance.observed_at,
        },
        "proposed_resolution": None,
    }
    if record.proposed_resolution is not None:
        payload["proposed_resolution"] = {
            "summary": record.proposed_resolution.summary,
            "intent_hint": record.proposed_resolution.intent_hint,
            "subject_kind_hint": record.proposed_resolution.subject_kind_hint,
        }
    return payload


def render_observation_toml(record: FrictionObservation) -> str:
    """Render a deterministic TOML document for an observation."""
    lines = [
        f"friction_schema_version = {record.schema_version}",
        f'record_kind = "{RECORD_KIND}"',
        f"id = {_toml_string(record.id)}",
        f"signal = {_toml_string(record.signal)}",
        f"path = {_toml_string(record.path)}",
    ]
    if record.scope is not None:
        lines.append(f"scope = {_toml_string(record.scope)}")
    if record.guidance_refs:
        rendered = ", ".join(_toml_string(item) for item in record.guidance_refs)
        lines.append(f"guidance_refs = [{rendered}]")
    lines.append(f"summary = {_toml_string(record.summary)}")
    lines.append("")
    for item in record.evidence:
        lines.extend(
            [
                "[[evidence]]",
                f"kind = {_toml_string(item.kind)}",
                f"reference = {_toml_string(item.reference)}",
                f"summary = {_toml_string(item.summary)}",
                "",
            ]
        )
    lines.extend(
        [
            "[observed_cost]",
            f"metric = {_toml_string(record.observed_cost.metric)}",
            f"value = {record.observed_cost.value}",
        ]
    )
    if record.observed_cost.bound is not None:
        lines.append(f"bound = {record.observed_cost.bound}")
    lines.append("")
    lines.extend(
        [
            "[provenance]",
            f"observer = {_toml_string(record.provenance.observer)}",
            f"origin = {_toml_string(record.provenance.origin)}",
            f"observed_at = {_toml_string(record.provenance.observed_at)}",
            "",
        ]
    )
    if record.proposed_resolution is not None:
        lines.append("[proposed_resolution]")
        lines.append(f"summary = {_toml_string(record.proposed_resolution.summary)}")
        if record.proposed_resolution.intent_hint is not None:
            lines.append(
                f"intent_hint = {_toml_string(record.proposed_resolution.intent_hint)}"
            )
        if record.proposed_resolution.subject_kind_hint is not None:
            lines.append(
                "subject_kind_hint = "
                f"{_toml_string(record.proposed_resolution.subject_kind_hint)}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def analyze_observations(
    records: tuple[FrictionObservation, ...] | list[FrictionObservation],
    *,
    root: Path | None = None,
    known_scopes: frozenset[str] | set[str] | None = None,
) -> dict[str, Any]:
    """Deterministic duplication, scope, stability, evidence-gap, and cost analysis.

    The result is advisory only.  It never writes guidance or creates curation proposals.
    """
    items = tuple(records)
    duplication = _duplication_findings(items)
    scope_pairs = [_scope_analysis(item, known_scopes=known_scopes) for item in items]
    scope_reports = [report for report, _findings in scope_pairs]
    scope_findings = [finding for _report, findings in scope_pairs for finding in findings]
    stability = _stability_findings(items, root=root)
    evidence_gap = _evidence_gap_findings(items)
    projected_pairs = [_projected_context_cost(item) for item in items]
    projected = [report for report, _findings in projected_pairs]
    projected_findings = [
        finding for _report, findings in projected_pairs for finding in findings
    ]
    findings = [
        *duplication,
        *scope_findings,
        *stability,
        *evidence_gap,
        *projected_findings,
    ]
    return {
        "contract": FRICTION_CONTRACT,
        "schema_version": FRICTION_SCHEMA_VERSION,
        "inert": True,
        "applies_guidance": False,
        "observation_ids": [item.id for item in items],
        "duplication": [item.payload() for item in duplication],
        "scope": scope_reports,
        "stability": [item.payload() for item in stability],
        "evidence_gap": [item.payload() for item in evidence_gap],
        "projected_context_cost": projected,
        "findings": [item.payload() for item in findings],
        "ok": not any(item.blocking for item in findings),
    }


def _duplication_findings(
    records: tuple[FrictionObservation, ...],
) -> list[FrictionFinding]:
    findings: list[FrictionFinding] = []
    seen_ids: dict[str, str] = {}
    fingerprints: dict[tuple[str, str, str | None, str], list[str]] = {}
    for record in records:
        if record.id in seen_ids:
            findings.append(
                FrictionFinding(
                    "duplicate_id",
                    f"observation id {record.id!r} is duplicated",
                )
            )
        else:
            seen_ids[record.id] = record.id
        key = (record.signal, record.path, record.scope, record.summary.strip())
        fingerprints.setdefault(key, []).append(record.id)
    for _key, ids in sorted(fingerprints.items(), key=lambda item: item[1]):
        if len(ids) < 2:
            continue
        findings.append(
            FrictionFinding(
                "duplicate_observation",
                "observations "
                + ", ".join(sorted(ids))
                + " share signal/path/scope/summary",
            )
        )
    return findings


def _scope_analysis(
    record: FrictionObservation, *, known_scopes: frozenset[str] | set[str] | None
) -> tuple[dict[str, Any], list[FrictionFinding]]:
    findings: list[FrictionFinding] = []
    addressed = record.scope
    if addressed is None:
        findings.append(
            FrictionFinding(
                "scope_unspecified",
                f"observation {record.id!r} does not name a guidance scope",
                blocking=False,
            )
        )
    elif known_scopes is not None and addressed not in known_scopes:
        findings.append(
            FrictionFinding(
                "scope_unknown",
                f"observation {record.id!r} addresses unknown scope {addressed!r}",
            )
        )
    report = {
        "observation_id": record.id,
        "path": record.path,
        "scope": addressed,
        "guidance_refs": list(record.guidance_refs),
        "findings": [item.payload() for item in findings],
    }
    return report, findings


def _stability_findings(
    records: tuple[FrictionObservation, ...], *, root: Path | None
) -> list[FrictionFinding]:
    findings: list[FrictionFinding] = []
    if root is None:
        return findings
    root_resolved = resolve_root(root)
    for record in records:
        try:
            validate_observation_paths(root_resolved, record)
        except MurlocsError as exc:
            findings.append(
                FrictionFinding(
                    "unstable_reference",
                    f"observation {record.id!r}: {exc}",
                )
            )
            continue
        path = root_resolved / record.path
        if not path.exists():
            findings.append(
                FrictionFinding(
                    "missing_path",
                    f"observation {record.id!r} path does not exist: {record.path}",
                    blocking=False,
                )
            )
        for ref in record.guidance_refs:
            guidance = root_resolved / ref
            if not guidance.exists():
                findings.append(
                    FrictionFinding(
                        "missing_guidance_ref",
                        f"observation {record.id!r} guidance_refs entry missing: {ref}",
                        blocking=False,
                    )
                )
        if record.signal in {"misleading", "conflicting", "repetitive", "overly_broad"}:
            if not record.guidance_refs:
                findings.append(
                    FrictionFinding(
                        "stability_guidance_unanchored",
                        f"observation {record.id!r} signal {record.signal!r} "
                        "has no guidance_refs anchor",
                        blocking=False,
                    )
                )
    return findings


def _evidence_gap_findings(
    records: tuple[FrictionObservation, ...],
) -> list[FrictionFinding]:
    findings: list[FrictionFinding] = []
    for record in records:
        kinds = {item.kind for item in record.evidence}
        if kinds <= {"note"}:
            findings.append(
                FrictionFinding(
                    "evidence_gap_note_only",
                    f"observation {record.id!r} evidence is note-only; "
                    "prefer file_anchor, issue, or evaluation references",
                    blocking=False,
                )
            )
        if record.signal == "missing" and not (
            kinds & {"file_anchor", "issue", "evaluation", "command"}
        ):
            findings.append(
                FrictionFinding(
                    "evidence_gap_missing_signal",
                    f"observation {record.id!r} signal 'missing' lacks "
                    "file_anchor, issue, evaluation, or command evidence",
                    blocking=False,
                )
            )
        for index, item in enumerate(record.evidence):
            if not item.reference.strip():
                findings.append(
                    FrictionFinding(
                        "evidence_gap_empty_reference",
                        f"observation {record.id!r} evidence[{index}] has an empty reference",
                    )
                )
    return findings


def _projected_context_cost(
    record: FrictionObservation,
) -> tuple[dict[str, Any], list[FrictionFinding]]:
    findings: list[FrictionFinding] = []
    cost = record.observed_cost
    utilization: float | None = None
    if cost.bound is not None and cost.bound > 0:
        utilization = cost.value / cost.bound
        if utilization >= 1.0:
            findings.append(
                FrictionFinding(
                    "context_cost_at_bound",
                    f"observation {record.id!r} observed_cost is at or above bound",
                    blocking=False,
                )
            )
    projected_delta = 0
    if record.proposed_resolution is not None:
        # Bounded byte projection from resolution summary length only; never a model.
        projected_delta = len(record.proposed_resolution.summary.encode("utf-8"))
        if record.proposed_resolution.intent_hint == "remove":
            projected_delta = -projected_delta
    projected_after = cost.value + projected_delta
    if cost.metric == "active_context_bytes" and cost.bound is not None:
        if projected_after > cost.bound:
            findings.append(
                FrictionFinding(
                    "projected_context_over_bound",
                    f"observation {record.id!r} projected active context "
                    f"{projected_after} exceeds bound {cost.bound}",
                    blocking=False,
                )
            )
    report = {
        "observation_id": record.id,
        "metric": cost.metric,
        "observed_value": cost.value,
        "bound": cost.bound,
        "utilization": utilization,
        "projected_delta_bytes": projected_delta,
        "projected_value": projected_after,
        "findings": [item.payload() for item in findings],
    }
    return report, findings


def _reject_forbidden_content(data: dict[str, Any], context: str) -> None:
    present = sorted(FORBIDDEN_CONTENT_FIELDS & set(data))
    if present:
        raise MurlocsError(
            f"{context} must not capture prompts, hidden reasoning, or source content; "
            f"forbidden fields: {', '.join(present)}"
        )


def _reject_friction_symlinks(root: Path, observation_id: str | None = None) -> None:
    raw = FRICTION_DIRECTORY
    if observation_id is not None:
        raw += f"/{observation_id}.toml"
    _reject_path_symlinks(root, raw, label="friction storage")


def _reject_path_symlinks(root: Path, raw: str, *, label: str) -> None:
    candidate = Path(raw)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise MurlocsError(f"{label} must be a safe repository-relative path: {raw}")
    if not raw or raw.startswith("\\") or "\\" in raw:
        raise MurlocsError(f"{label} must be a safe repository-relative path: {raw}")
    current = Path(root)
    for part in candidate.parts:
        if part in {"", ".", ".."}:
            raise MurlocsError(f"{label} must be a safe repository-relative path: {raw}")
        current = current / part
        if current.is_symlink():
            raise MurlocsError(f"{label} may not traverse a symlink: {raw}")


def _repo_relative_path(root: Path, raw: str, *, field: str) -> Path:
    _reject_path_symlinks(root, raw, label=field)
    return resolve_root(root) / Path(raw)


def _infer_repo_root(path: Path) -> Path | None:
    current = path.parent
    for _ in range(8):
        if (current / ".murlocs").is_dir() or (current / ".git").exists():
            return current
        if current.parent == current:
            break
        current = current.parent
    return None


def _validate_id(value: str) -> None:
    if not ID_PATTERN.fullmatch(value) or value in {".", ".."}:
        raise MurlocsError(
            "observation ids must be 1-128 lowercase path-safe characters "
            "(letters, digits, '.', '_' or '-')"
        )


def _optional_scope(data: dict[str, Any], filename: str) -> str | None:
    if "scope" not in data or data["scope"] is None:
        return None
    value = _string(data, "scope", filename)
    if not SCOPE_PATTERN.fullmatch(value):
        raise MurlocsError(
            f"{filename}.scope must be a path-safe lowercase id; got {value!r}"
        )
    return value


def _safe_relative_string(data: dict[str, Any], key: str, context: str) -> str:
    value = _string(data, key, context)
    _assert_safe_relative(value, f"{context}.{key}")
    return value


def _safe_relative_string_array(
    data: dict[str, Any], key: str, context: str
) -> tuple[str, ...]:
    values = _string_array(data, key, context)
    for index, value in enumerate(values):
        _assert_safe_relative(value, f"{context}.{key}[{index}]")
    return values


def _assert_safe_relative(raw: str, field: str) -> None:
    candidate = Path(raw)
    if (
        candidate.is_absolute()
        or ".." in candidate.parts
        or raw.startswith("\\")
        or "\\" in raw
    ):
        raise MurlocsError(f"{field} must be a safe repository-relative path: {raw}")
    if not candidate.parts or any(part in {"", ".", ".."} for part in candidate.parts):
        raise MurlocsError(f"{field} must be a safe repository-relative path: {raw}")


def _validate_file_anchor_reference(reference: str, context: str) -> None:
    file_part, separator, fragment = reference.partition("#")
    if not file_part:
        raise MurlocsError(f"{context}.reference file_anchor path is empty")
    _assert_safe_relative(file_part, f"{context}.reference")
    if separator and not fragment:
        raise MurlocsError(f"{context}.reference file_anchor fragment is empty")


def _bounded_summary(data: dict[str, Any], key: str, context: str) -> str:
    return _bounded_string(data, key, context, MAX_SUMMARY_CHARS)


def _bounded_string(data: dict[str, Any], key: str, context: str, maximum: int) -> str:
    value = _string(data, key, context)
    if len(value) > maximum:
        raise MurlocsError(f"{context}.{key} exceeds limit of {maximum} characters")
    return value


def _strict_fields(data: dict[str, Any], allowed: set[str], context: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise MurlocsError(f"{context} has unsupported fields: {', '.join(unknown)}")


def _string(data: dict[str, Any], key: str, context: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MurlocsError(f"{context}.{key} must be a non-empty string")
    return value


def _integer(data: dict[str, Any], key: str, context: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise MurlocsError(f"{context}.{key} must be an integer")
    return value


def _choice(
    data: dict[str, Any], key: str, choices: tuple[str, ...], context: str
) -> str:
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


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)
