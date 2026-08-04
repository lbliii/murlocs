from __future__ import annotations

import json
from pathlib import Path

from milo import generate_llms_txt
from milo.testing import MCPClient

from murlocs.cli import build_cli
from murlocs.manifest import load_manifest
from murlocs.render import render_outputs


def invoke(*argv: str):
    return build_cli().invoke(list(argv))


def _repo(root: Path) -> None:
    assert invoke("init", "--repo", str(root)).exit_code == 0
    (root / "src").mkdir()
    (root / "src" / "proof.py").write_text(
        "# murlocs:annotation/v1 evidence \"guidance.marker\"\nVALUE = 1\n",
        encoding="utf-8",
    )
    manifest = root / ".murlocs" / "manifest.toml"
    text = manifest.read_text(encoding="utf-8")
    text = text.replace("max_active_bytes = 24576", 'max_active_bytes = 24576\nowners = ["@guide"]')
    text = text.replace(
        'anchor = "Use this protocol"',
        'anchor = "Use this protocol"\n'
        'annotation = { id = "guidance.marker", kind = "evidence", '
        'file = "src/proof.py", version = "v1" }',
    )
    manifest.write_text(text, encoding="utf-8")
    assert invoke("compile", "--repo", str(root)).exit_code == 0


def test_valid_annotation_provenance_has_one_additive_record_on_every_surface(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    _repo(root)
    expected = {
        "id": "guidance.marker",
        "kind": "evidence",
        "version": "v1",
        "invariant": "guidance-stays-verified",
        "scope": "root",
        "file": "src/proof.py",
        "line": 1,
        "declaring_layer": "manifest",
        "owners": ["@guide"],
        "verification": "manual",
    }

    terminal = invoke("check", "--repo", str(root), "--format", "json")
    assert terminal.exit_code == 0
    checked = json.loads(terminal.output)
    programmatic = build_cli().call("check", repo=str(root))
    client = MCPClient(build_cli())
    mcp = client.call("check", repo=str(root)).structured
    tool = next(item for item in client.list_tools() if item.name == "check")

    assert checked == programmatic == mcp
    assert checked["annotations"] == [expected]
    assert checked["outcome"]["annotations"] == [expected]
    assert "annotations" in tool.output_schema["anyOf"][0]["properties"]
    assert "**check**" in generate_llms_txt(build_cli())
    assert "annotation guidance.marker (evidence)" in invoke(
        "check", "--repo", str(root)
    ).output

    explained = client.call("explain", path="src/proof.py", repo=str(root)).structured
    assert explained["scopes"][0]["invariants"][0]["annotations"] == [expected]
    assert explained["budget"]["active_bytes"] == len(
        (root / "AGENTS.md").read_bytes()
    )
    assert "Evidence provenance: `guidance.marker`" in (root / "AGENTS.md").read_text(
        encoding="utf-8"
    )
    assert "murlocs:annotation/v1" not in (root / "AGENTS.md").read_text(encoding="utf-8")

    inventory = client.call("inventory", repo=str(root)).structured
    status = client.call("status", repo=str(root)).structured
    assert inventory["annotations"] == status["annotations"] == [expected]
    assert "annotation guidance.marker (evidence)" in invoke(
        "inventory", "--repo", str(root)
    ).output
    assert "annotation guidance.marker (evidence)" in invoke(
        "status", "--repo", str(root)
    ).output


def test_invalid_annotation_is_only_a_check_finding_not_active_provenance(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    _repo(root)
    source = root / "src" / "proof.py"
    source.write_text(
        "# murlocs:annotation/v1 evidence \"other.marker\"\nVALUE = 1\n",
        encoding="utf-8",
    )

    result = invoke("check", "--repo", str(root), "--format", "json")
    assert result.exit_code == 1
    payload = json.loads(result.output or result.stderr)
    assert payload["annotations"] == []
    assert payload["outcome"]["annotations"] == []
    assert {"annotation.missing", "annotation.orphaned"} <= {
        item["code"] for item in payload["findings"]
    }
    assert "Evidence provenance" not in render_outputs(load_manifest(root))["AGENTS.md"]
