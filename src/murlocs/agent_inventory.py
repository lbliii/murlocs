"""Deterministic inventory of every agent-facing command and discovery surface.

Builds a versioned snapshot from the Milo CLI registry, MCP metadata, ``llms.txt``,
skills, generated ``AGENTS.md`` maps, and normative task-command documentation.
The snapshot is checked in under ``tests/fixtures/agent-inventory/`` and guarded by
``tests/test_agent_inventory.py``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from milo import generate_llms_txt
from milo.testing import MCPClient

from murlocs.cli import build_cli
from murlocs.task_commands import TASK_CONTRACT, TASK_SCHEMA_VERSION

INVENTORY_CONTRACT = "io.murlocs.agent-inventory"
INVENTORY_SCHEMA_VERSION = 1

COMPOSITE_COMMANDS = frozenset({"orient", "review-changes", "finish"})
CommandKind = Literal["granular", "composite"]
Audience = Literal["agent", "operator", "host", "product-internal"]
TriggerAssessment = Literal["present", "partial", "missing"]

_REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = _REPO_ROOT / "tests/fixtures/agent-inventory/v1/inventory.json"

_SKILL_ROOT = _REPO_ROOT / "skills"
_GUIDANCE_MAPS = (
    _REPO_ROOT / "AGENTS.md",
    _REPO_ROOT / "src/murlocs/AGENTS.md",
    _REPO_ROOT / "tests/AGENTS.md",
    _REPO_ROOT / "skills/bootstrap-murlocs/AGENTS.md",
)
_TASK_COMMANDS_DOC = _REPO_ROOT / "docs/task-commands.md"

_TRIGGER_SIGNALS = re.compile(
    r"\b("
    r"before|after|when|use when|unfamiliar|explicit|fresh|"
    r"obtain|completion|change set|task"
    r")\b",
    re.IGNORECASE,
)
_MURLOCS_COMMAND = re.compile(r"`murlocs\s+([^`\n]+)`")
_BACKTICK_COMMAND = re.compile(r"`(orient|review-changes|finish|check|impact|explain)`")


@dataclass(frozen=True)
class _CommandRef:
    path: str
    description: str
    surfaces: tuple[str, ...]
    annotations: dict[str, bool]
    input_schema: dict[str, Any]
    mcp_description: str | None
    output_schema: dict[str, Any] | None


def _command_kind(path: str) -> CommandKind:
    leaf = path.rsplit(".", 1)[-1]
    return "composite" if leaf in COMPOSITE_COMMANDS else "granular"


def _assess_trigger_language(description: str) -> tuple[TriggerAssessment, list[str]]:
    signals = sorted({match.group(0).lower() for match in _TRIGGER_SIGNALS.finditer(description)})
    if not signals:
        return "missing", signals
    if any(token in signals for token in ("before", "when", "use when", "unfamiliar")):
        return "present", signals
    return "partial", signals


def _normalize_operator_command(command: str) -> set[str]:
    normalized = re.sub(r"^--dry-run\s+", "", command).strip()
    if not normalized:
        return set()
    if normalized.startswith("curate "):
        return {normalized.replace(" ", ".", 1)}
    return {normalized.split()[0]}


def _collect_operator_paths(skill_text: str, docs_text: str) -> frozenset[str]:
    found: set[str] = set()
    for command in _extract_guidance_commands(skill_text):
        found.update(_normalize_operator_command(command))
    for match in _BACKTICK_COMMAND.finditer(docs_text):
        found.add(match.group(1))
    return frozenset(found)


def _classify_audience(
    path: str,
    surfaces: tuple[str, ...],
    operator_paths: frozenset[str],
) -> Audience:
    if path.startswith("hook."):
        return "host"
    if "mcp" in surfaces or "llms" in surfaces:
        return "agent"
    if path in operator_paths or any(path.startswith(prefix) for prefix in ("curate.",)):
        return "operator"
    return "product-internal"


def _parse_skill_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    _, _, remainder = text.partition("---")
    block, _, _ = remainder.partition("---")
    metadata: dict[str, str] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        metadata[key.strip()] = value.strip().strip('"')
    return metadata


def _extract_guidance_commands(text: str) -> list[str]:
    commands = [match.group(1).strip() for match in _MURLOCS_COMMAND.finditer(text)]
    return sorted(dict.fromkeys(command for command in commands if command))


def _collect_registry(repo_root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    app = build_cli()
    client = MCPClient(app)
    mcp_by_name = {tool.name: tool for tool in client.list_tools()}
    llms_txt = generate_llms_txt(app)

    skill_text = (_SKILL_ROOT / "bootstrap-murlocs/SKILL.md").read_text(encoding="utf-8")
    docs_text = _TASK_COMMANDS_DOC.read_text(encoding="utf-8")
    operator_paths = _collect_operator_paths(skill_text, docs_text)

    commands: list[dict[str, Any]] = []
    for path, command in sorted(app.walk_commands()):
        mcp_tool = mcp_by_name.get(path)
        trigger_assessment, trigger_signals = _assess_trigger_language(command.description)
        contract: dict[str, Any] | None = None
        if _command_kind(path) == "composite":
            contract = {"name": TASK_CONTRACT, "schema_version": TASK_SCHEMA_VERSION}
        entry = {
            "name": path,
            "kind": _command_kind(path),
            "audience": _classify_audience(path, command.surfaces, operator_paths),
            "stable": {
                "name": path,
                "surfaces": {
                    "cli": "cli" in command.surfaces,
                    "mcp": "mcp" in command.surfaces,
                    "llms": "llms" in command.surfaces,
                    "programmatic": "mcp" in command.surfaces,
                },
                "annotations": dict(sorted(command.annotations.items())),
                "input_schema": command.schema,
                "output_schema": mcp_tool.output_schema if mcp_tool is not None else None,
                "contract": contract,
            },
            "volatile": {
                "description": command.description,
                "mcp_description": mcp_tool.description if mcp_tool is not None else None,
            },
            "trigger_language": {
                "assessment": trigger_assessment,
                "signals": trigger_signals,
            },
        }
        commands.append(entry)

    llms_unqualified = re.findall(r"^- \*\*([^*]+)\*\*:", llms_txt, re.MULTILINE)
    llms_registry = {
        "present": True,
        "unqualified_command_names": sorted(dict.fromkeys(llms_unqualified)),
        "unqualified_command_occurrences": llms_unqualified,
        "includes_workflows": "## Workflows" in llms_txt,
        "agent_discoverable_commands": sorted(
            path for path, command in app.walk_commands() if "llms" in command.surfaces
        ),
    }
    return commands, llms_registry


def _collect_skills() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for skill_md in sorted(_SKILL_ROOT.glob("*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8")
        metadata = _parse_skill_frontmatter(text)
        skill_dir = skill_md.parent
        metadata_files = sorted(skill_dir.glob("agents/*"))
        entries.append(
            {
                "name": metadata.get("name", skill_dir.name),
                "path": skill_dir.relative_to(_REPO_ROOT).as_posix(),
                "stable": {
                    "name": metadata.get("name", skill_dir.name),
                    "metadata_paths": [
                        path.relative_to(_REPO_ROOT).as_posix() for path in metadata_files
                    ],
                },
                "volatile": {
                    "description": metadata.get("description", ""),
                    "default_prompt": next(
                        (
                            line.partition(":")[2].strip().strip('"')
                            for path in metadata_files
                            if path.suffix in {".yaml", ".yml"}
                            for line in path.read_text(encoding="utf-8").splitlines()
                            if line.strip().startswith("default_prompt:")
                        ),
                        None,
                    ),
                },
                "referenced_commands": _extract_guidance_commands(text),
            }
        )
    return entries


def _collect_generated_guidance() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for map_path in _GUIDANCE_MAPS:
        if not map_path.is_file():
            continue
        text = map_path.read_text(encoding="utf-8")
        entries.append(
            {
                "map": map_path.relative_to(_REPO_ROOT).as_posix(),
                "stable": {"map": map_path.relative_to(_REPO_ROOT).as_posix()},
                "volatile": {
                    "activation_fallback": "murlocs check" in text and "murlocs impact" in text,
                },
                "referenced_commands": _extract_guidance_commands(text),
            }
        )
    return entries


def _collect_documentation() -> list[dict[str, Any]]:
    task_doc = _TASK_COMMANDS_DOC.read_text(encoding="utf-8")
    documented_commands = sorted({match.group(1) for match in _BACKTICK_COMMAND.finditer(task_doc)})
    return [
        {
            "path": "docs/task-commands.md",
            "stable": {
                "path": "docs/task-commands.md",
                "contract": TASK_CONTRACT,
                "schema_version": TASK_SCHEMA_VERSION,
                "documented_commands": documented_commands,
            },
            "volatile": {
                "summary": (
                    "Normative specification for orient, review-changes, and finish "
                    "over granular check, impact, and curation-validation receipts."
                ),
            },
            "trigger_language": {
                "orient": "before unfamiliar work",
                "review-changes": "route declared changes",
                "finish": "fresh structural completion receipt",
            },
        }
    ]


def _build_analysis(
    commands: list[dict[str, Any]],
    llms_registry: dict[str, Any],
    skills: list[dict[str, Any]],
) -> dict[str, Any]:
    by_description: dict[str, list[str]] = {}
    for entry in commands:
        description = entry["volatile"]["description"]
        by_description.setdefault(description, []).append(entry["name"])

    duplicates: list[dict[str, Any]] = [
        {
            "kind": "shared_description",
            "description": description,
            "commands": names,
        }
        for description, names in sorted(by_description.items())
        if len(names) > 1
    ]

    llms_name_counts: dict[str, int] = {}
    for name in llms_registry["unqualified_command_occurrences"]:
        llms_name_counts[name] = llms_name_counts.get(name, 0) + 1
    for name, count in sorted(llms_name_counts.items()):
        if count > 1:
            duplicates.append(
                {
                    "kind": "llms_unqualified_name",
                    "name": name,
                    "occurrences": count,
                    "note": "Short names collide across groups; workflows use dotted paths.",
                }
            )

    conflicts: list[dict[str, Any]] = []
    for entry in commands:
        cli_description = entry["volatile"]["description"]
        mcp_description = entry["volatile"]["mcp_description"]
        if mcp_description is not None and mcp_description != cli_description:
            conflicts.append(
                {
                    "command": entry["name"],
                    "cli_description": cli_description,
                    "mcp_description": mcp_description,
                }
            )

    product_internal = sorted(
        entry["name"] for entry in commands if entry["audience"] == "product-internal"
    )
    missing_trigger_language = sorted(
        entry["name"]
        for entry in commands
        if entry["audience"] == "agent" and entry["trigger_language"]["assessment"] == "missing"
    )

    skill_commands = {
        command for skill in skills for command in skill["referenced_commands"] if command.strip()
    }
    command_names = {entry["name"] for entry in commands}
    for command in sorted(skill_commands):
        for candidate in _normalize_operator_command(command):
            if candidate in command_names or any(
                name == candidate or name.endswith(f".{candidate}") for name in command_names
            ):
                break
        else:
            conflicts.append(
                {
                    "kind": "skill_without_registry_match",
                    "skill_command": command,
                }
            )

    return {
        "duplicates": duplicates,
        "conflicts": conflicts,
        "product_internal": product_internal,
        "missing_trigger_language": missing_trigger_language,
    }


def build_agent_inventory(*, repo_root: Path | None = None) -> dict[str, Any]:
    """Return the canonical agent-surface inventory for the repository."""
    del repo_root  # Registry is product-global; repo_root reserved for future host-specific scans.
    commands, llms_registry = _collect_registry(_REPO_ROOT)
    skills = _collect_skills()
    generated_guidance = _collect_generated_guidance()
    documentation = _collect_documentation()
    analysis = _build_analysis(commands, llms_registry, skills)
    return {
        "contract": INVENTORY_CONTRACT,
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "registry": {
            "commands": commands,
            "llms_txt": llms_registry,
        },
        "skills": skills,
        "generated_guidance": generated_guidance,
        "documentation": documentation,
        "analysis": analysis,
    }


def render_agent_inventory(payload: dict[str, Any]) -> str:
    """Serialize inventory deterministically for drift comparison."""
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def write_agent_inventory(path: Path | None = None) -> Path:
    """Write the current inventory snapshot to ``path``."""
    target = path or FIXTURE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_agent_inventory(build_agent_inventory()), encoding="utf-8")
    return target
