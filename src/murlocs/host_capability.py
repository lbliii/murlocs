"""Versioned host capability matrix with evidence-gated support tiers.

The matrix records what each agent host or orchestrator can do with Murlocs.
Claims are checked against checked-in evidence paths and a verification date.
Missing or stale evidence forces the effective tier to ``unknown``; a claimed
``native``/``adapted``/``tool-only`` value is never trusted without proof.

This module validates and resolves the matrix. It does not probe live hosts,
install adapters, or execute repository commands.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

CONTRACT = "io.murlocs.host-capability-matrix"
SCHEMA_VERSION = 1

SupportTier = Literal["native", "adapted", "tool-only", "unknown"]
ClaimBasis = Literal["documented", "observed"]
HostKind = Literal["agent-host", "orchestrator"]

TIERS: frozenset[str] = frozenset({"native", "adapted", "tool-only", "unknown"})
CLAIM_BASES: frozenset[str] = frozenset({"documented", "observed"})
HOST_KINDS: frozenset[str] = frozenset({"agent-host", "orchestrator"})

CAPABILITY_FIELDS: tuple[str, ...] = (
    "instruction_discovery",
    "instruction_scoping",
    "refresh_timing",
    "size_constraints",
    "mcp",
    "hooks",
)

# Acceptance for #139 requires these four profiles at minimum.
REQUIRED_PROFILE_IDS: frozenset[str] = frozenset(
    {"openai-codex", "claude-code", "cursor", "github-copilot"}
)

PORTABLE_FALLBACKS: frozenset[str] = frozenset(
    {
        "generated-guidance",
        "cli",
        "mcp-tools",
        "git-hook",
        "ci",
        "none-required",
    }
)

DEFAULT_MATRIX_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "host-capability-matrix"
    / "v1"
    / "matrix.json"
)

_PROFILE_KEYS = frozenset(
    {
        "id",
        "display_name",
        "host_kind",
        "claimed_tier",
        "tested_version",
        "verification_date",
        "instruction_discovery",
        "instruction_scoping",
        "refresh_timing",
        "size_constraints",
        "mcp",
        "hooks",
        "limitations",
        "evidence",
    }
)

_CAPABILITY_KEYS = frozenset(
    {"summary", "claim_basis", "evidence", "portable_fallback"}
)

_ROOT_KEYS = frozenset(
    {
        "contract",
        "schema_version",
        "evidence_max_age_days",
        "portable_fallbacks",
        "profiles",
    }
)


class HostCapabilityError(ValueError):
    """The host capability matrix is malformed or internally inconsistent."""


def default_matrix_path() -> Path:
    """Return the checked-in version-1 matrix fixture path."""
    return DEFAULT_MATRIX_PATH


def load_host_capability_matrix(
    path: Path | None = None,
    *,
    repository_root: Path | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Load, validate, and resolve the host capability matrix.

    ``effective_tier`` is ``unknown`` when profile evidence is missing, a cited
    path is absent from the repository, or ``verification_date`` is older than
    ``evidence_max_age_days`` relative to ``as_of`` (UTC today by default).
    """
    matrix_path = path if path is not None else default_matrix_path()
    root = repository_root if repository_root is not None else _repository_root()
    reference = as_of if as_of is not None else datetime.now(UTC).date()
    raw = json.loads(matrix_path.read_text(encoding="utf-8"))
    return resolve_host_capability_matrix(
        raw, repository_root=root, as_of=reference
    )


def resolve_host_capability_matrix(
    value: object,
    *,
    repository_root: Path,
    as_of: date,
) -> dict[str, Any]:
    """Validate a matrix document and attach evidence-gated effective tiers."""
    document = _mapping(value, "host capability matrix")
    _exact_keys(document, _ROOT_KEYS, "host capability matrix")
    if document["contract"] != CONTRACT:
        raise HostCapabilityError(
            f"unsupported host capability contract {document['contract']!r}"
        )
    if document["schema_version"] != SCHEMA_VERSION:
        raise HostCapabilityError(
            f"unsupported host capability schema_version "
            f"{document['schema_version']!r}; expected {SCHEMA_VERSION}"
        )

    max_age = document["evidence_max_age_days"]
    if type(max_age) is not int or max_age < 1:
        raise HostCapabilityError("evidence_max_age_days must be a positive integer")

    portable = _mapping(document["portable_fallbacks"], "portable_fallbacks")
    _validate_portable_fallbacks(portable)

    profiles_raw = _list(document["profiles"], "profiles")
    if not profiles_raw:
        raise HostCapabilityError(
            "host capability matrix must declare at least one profile"
        )

    profiles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in profiles_raw:
        profile = _validate_profile(
            item, repository_root=repository_root, as_of=as_of, max_age=max_age
        )
        if profile["id"] in seen:
            raise HostCapabilityError(
                f"duplicate host capability profile {profile['id']!r}"
            )
        seen.add(profile["id"])
        profiles.append(profile)

    missing = REQUIRED_PROFILE_IDS - seen
    if missing:
        raise HostCapabilityError(
            "host capability matrix omits required profiles: "
            + ", ".join(sorted(missing))
        )

    return {
        "contract": CONTRACT,
        "schema_version": SCHEMA_VERSION,
        "evidence_max_age_days": max_age,
        "as_of": as_of.isoformat(),
        "portable_fallbacks": {key: portable[key] for key in sorted(portable)},
        "profiles": profiles,
    }


def effective_tiers(matrix: Mapping[str, Any]) -> dict[str, SupportTier]:
    """Return ``{profile_id: effective_tier}`` from a resolved matrix."""
    profiles = matrix.get("profiles")
    if not isinstance(profiles, list):
        raise HostCapabilityError("resolved matrix is missing profiles")
    result: dict[str, SupportTier] = {}
    for profile in profiles:
        if not isinstance(profile, Mapping):
            raise HostCapabilityError("resolved matrix profile must be an object")
        profile_id = profile.get("id")
        tier = profile.get("effective_tier")
        if not isinstance(profile_id, str) or tier not in TIERS:
            raise HostCapabilityError(
                "resolved matrix profile is missing id or effective_tier"
            )
        result[profile_id] = tier  # type: ignore[assignment]
    return result


def _validate_portable_fallbacks(portable: Mapping[str, Any]) -> None:
    required = frozenset(CAPABILITY_FIELDS)
    _exact_keys(portable, required, "portable_fallbacks")
    for field in CAPABILITY_FIELDS:
        value = portable[field]
        if not isinstance(value, str) or value not in PORTABLE_FALLBACKS:
            raise HostCapabilityError(
                f"portable_fallbacks.{field} must be one of "
                f"{sorted(PORTABLE_FALLBACKS)}"
            )


def _validate_profile(
    value: object,
    *,
    repository_root: Path,
    as_of: date,
    max_age: int,
) -> dict[str, Any]:
    profile = _mapping(value, "profile")
    _exact_keys(profile, _PROFILE_KEYS, "profile")

    profile_id = _nonempty_string(profile["id"], "profile.id")
    display_name = _nonempty_string(profile["display_name"], "profile.display_name")
    host_kind = profile["host_kind"]
    if host_kind not in HOST_KINDS:
        raise HostCapabilityError(
            f"profile {profile_id!r} has invalid host_kind {host_kind!r}"
        )

    claimed = profile["claimed_tier"]
    if claimed not in TIERS:
        raise HostCapabilityError(
            f"profile {profile_id!r} has invalid claimed_tier {claimed!r}"
        )

    tested_version = _nonempty_string(profile["tested_version"], "profile.tested_version")
    verification_date = _optional_date(
        profile["verification_date"], "profile.verification_date"
    )
    limitations = _string_list(profile["limitations"], "profile.limitations")
    evidence = _path_list(profile["evidence"], "profile.evidence")

    capabilities: dict[str, dict[str, Any]] = {}
    for field in CAPABILITY_FIELDS:
        capabilities[field] = _validate_capability(
            profile[field], field=field, profile_id=profile_id
        )

    reasons = _evidence_gap_reasons(
        claimed_tier=claimed,  # type: ignore[arg-type]
        verification_date=verification_date,
        evidence=evidence,
        capabilities=capabilities,
        repository_root=repository_root,
        as_of=as_of,
        max_age=max_age,
    )
    effective: SupportTier = "unknown" if reasons else claimed  # type: ignore[assignment]

    resolved = {
        "id": profile_id,
        "display_name": display_name,
        "host_kind": host_kind,
        "claimed_tier": claimed,
        "effective_tier": effective,
        "tested_version": tested_version,
        "verification_date": (
            None if verification_date is None else verification_date.isoformat()
        ),
        "limitations": limitations,
        "evidence": evidence,
        "evidence_gaps": reasons,
    }
    for field in CAPABILITY_FIELDS:
        resolved[field] = capabilities[field]
    return resolved


def _validate_capability(value: object, *, field: str, profile_id: str) -> dict[str, Any]:
    item = _mapping(value, f"profile.{field}")
    _exact_keys(item, _CAPABILITY_KEYS, f"profile.{field}")
    summary = _nonempty_string(item["summary"], f"profile.{field}.summary")
    claim_basis = item["claim_basis"]
    if claim_basis not in CLAIM_BASES:
        raise HostCapabilityError(
            f"profile {profile_id!r} {field} has invalid claim_basis {claim_basis!r}"
        )
    evidence = _path_list(item["evidence"], f"profile.{field}.evidence")
    fallback = item["portable_fallback"]
    if not isinstance(fallback, str) or fallback not in PORTABLE_FALLBACKS:
        raise HostCapabilityError(
            f"profile {profile_id!r} {field} has invalid portable_fallback "
            f"{fallback!r}"
        )
    return {
        "summary": summary,
        "claim_basis": claim_basis,
        "evidence": evidence,
        "portable_fallback": fallback,
    }


def _evidence_gap_reasons(
    *,
    claimed_tier: SupportTier,
    verification_date: date | None,
    evidence: Sequence[str],
    capabilities: Mapping[str, Mapping[str, Any]],
    repository_root: Path,
    as_of: date,
    max_age: int,
) -> list[str]:
    """Return human-readable reasons the effective tier must be unknown."""
    if claimed_tier == "unknown":
        return []

    reasons: list[str] = []
    if verification_date is None:
        reasons.append("verification_date is absent")
    else:
        age = (as_of - verification_date).days
        if age < 0:
            reasons.append("verification_date is in the future")
        elif age > max_age:
            reasons.append(
                f"verification_date is stale ({age} days old; max {max_age})"
            )

    if not evidence:
        reasons.append("profile evidence list is empty")
    else:
        reasons.extend(
            _missing_paths(evidence, repository_root, prefix="profile evidence")
        )

    for field in CAPABILITY_FIELDS:
        capability = capabilities[field]
        cap_evidence = capability["evidence"]
        if not cap_evidence:
            reasons.append(f"{field} evidence list is empty")
            continue
        reasons.extend(
            _missing_paths(
                cap_evidence, repository_root, prefix=f"{field} evidence"
            )
        )
    return reasons


def _missing_paths(
    paths: Sequence[str], repository_root: Path, *, prefix: str
) -> list[str]:
    missing: list[str] = []
    for relative in paths:
        candidate = repository_root / relative
        if not candidate.is_file():
            missing.append(f"{prefix} path missing: {relative}")
    return missing


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _mapping(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HostCapabilityError(f"{context} must be an object")
    return value


def _list(value: object, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise HostCapabilityError(f"{context} must be a list")
    return value


def _exact_keys(value: Mapping[str, Any], allowed: frozenset[str], context: str) -> None:
    keys = frozenset(value)
    if keys != allowed:
        extra = sorted(keys - allowed)
        missing = sorted(allowed - keys)
        parts: list[str] = []
        if missing:
            parts.append(f"missing {', '.join(missing)}")
        if extra:
            parts.append(f"unknown {', '.join(extra)}")
        raise HostCapabilityError(f"{context} has {' and '.join(parts)}")


def _nonempty_string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HostCapabilityError(f"{context} must be a nonempty string")
    return value


def _optional_date(value: object, context: str) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HostCapabilityError(f"{context} must be an ISO date string or null")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HostCapabilityError(
            f"{context} must be an ISO date string or null"
        ) from exc


def _string_list(value: object, context: str) -> list[str]:
    items = _list(value, context)
    result: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise HostCapabilityError(f"{context} entries must be nonempty strings")
        result.append(item)
    return result


def _path_list(value: object, context: str) -> list[str]:
    items = _string_list(value, context)
    for item in items:
        if item.startswith("/") or item.startswith("\\") or ".." in Path(item).parts:
            raise HostCapabilityError(
                f"{context} paths must be repository-relative without '..'"
            )
    return items


def stale_after(*, verification_date: date, max_age_days: int) -> date:
    """Return the first calendar date on which evidence becomes stale."""
    if max_age_days < 1:
        raise HostCapabilityError("max_age_days must be a positive integer")
    return verification_date + timedelta(days=max_age_days + 1)
