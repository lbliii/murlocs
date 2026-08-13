from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

import pytest

import murlocs.identity as identity
from murlocs.cli import build_cli


class FakeDistribution:
    def __init__(self, root: Path, *, version: str = "1.2.3", direct_url: str | None = None):
        self.version = version
        self._root = root
        self.files = (
            [PurePosixPath("murlocs-1.2.3.dist-info/direct_url.json")]
            if direct_url is not None
            else [PurePosixPath("murlocs-1.2.3.dist-info/METADATA")]
        )
        if direct_url is not None:
            metadata = root / "murlocs-1.2.3.dist-info"
            metadata.mkdir(exist_ok=True)
            (metadata / "direct_url.json").write_text(direct_url, encoding="utf-8")

    def locate_file(self, path: PurePosixPath) -> Path:
        return self._root / path


def package(tmp_path: Path) -> Path:
    root = tmp_path / "murlocs"
    root.mkdir()
    (root / "z.py").write_text("Z = 1\n", encoding="utf-8")
    (root / "a.py").write_text("A = 1\n", encoding="utf-8")
    cache = root / "__pycache__"
    cache.mkdir()
    (cache / "a.cpython-314.pyc").write_bytes(b"cache")
    return root


def inspect(
    root: Path,
    *,
    version: str = "1.2.3",
    direct_url: str | None = None,
) -> identity.RuntimeIdentity:
    dist = FakeDistribution(root.parent, version=version, direct_url=direct_url)
    return identity.runtime_identity(package_root=root, distribution_provider=lambda _name: dist)  # type: ignore[arg-type]


def test_identity_hash_is_deterministic_and_changes_with_running_package_content(
    tmp_path: Path,
) -> None:
    root = package(tmp_path)
    local = json.dumps({"url": "file:///private/secret/project", "dir_info": {}})

    first = inspect(root, direct_url=local)
    second = inspect(root, direct_url=local)
    (root / "z.py").write_text("Z = 2\n", encoding="utf-8")
    changed = inspect(root, direct_url=local)

    assert first["build"]["id"] == second["build"]["id"]
    assert changed["build"]["id"] != first["build"]["id"]
    assert first["build"]["kind"] == "development"
    assert first["installation"] == {
        "kind": "local-directory",
        "editable": False,
        "source_revision": None,
        "archive_hash": None,
    }
    assert "secret" not in json.dumps(first)


@pytest.mark.parametrize(
    ("direct_url", "expected"),
    [
        (
            {"url": "file:///Users/example/repository", "dir_info": {"editable": True}},
            {"kind": "editable", "editable": True, "source_revision": None, "archive_hash": None},
        ),
        (
            {
                "url": "https://token@example.invalid/repository.git",
                "vcs_info": {"vcs": "git", "commit_id": "a" * 40},
            },
            {"kind": "vcs", "editable": False, "source_revision": "a" * 40, "archive_hash": None},
        ),
        (
            {
                "url": "https://example.invalid/murlocs-1.2.3.whl",
                "archive_info": {"hashes": {"sha512": "c" * 128, "sha256": "b" * 64}},
            },
            {
                "kind": "archive",
                "editable": False,
                "source_revision": None,
                "archive_hash": "sha256=" + "b" * 64,
            },
        ),
    ],
)
def test_pep610_provenance_is_classified_and_redacted(
    tmp_path: Path, direct_url: dict[str, object], expected: dict[str, object]
) -> None:
    result = inspect(package(tmp_path), direct_url=json.dumps(direct_url))

    assert result["installation"] == expected
    assert result["build"]["kind"] == (
        "release" if expected["kind"] == "archive" else "development"
    )
    rendered = json.dumps(result)
    assert "example.invalid" not in rendered
    assert "/Users/example" not in rendered
    assert "token@" not in rendered


def test_final_version_without_direct_url_is_unverified_release_candidate(tmp_path: Path) -> None:
    result = inspect(package(tmp_path))

    assert result["schema_version"] == 1
    assert result["build"]["kind"] == "release"
    assert result["build"]["verification"] == "unverified"
    assert result["installation"]["kind"] == "index-or-unknown"


@pytest.mark.parametrize(
    "version", ["1.2.4.dev3", "1.2.4+gabc123", "1.2.4a1", "1.2.4b1", "1.2.4rc1"]
)
def test_development_or_local_pep440_version_never_claims_release(
    tmp_path: Path, version: str
) -> None:
    result = inspect(package(tmp_path), version=version)

    assert result["build"]["kind"] == "development"
    assert result["installation"]["kind"] == "index-or-unknown"


@pytest.mark.parametrize(
    "direct_url",
    ["not-json", "[]", '{"url":"file:///x"}', '{"url":"file:///x","dir_info":{"editable":"yes"}}'],
)
def test_malformed_direct_url_is_unknown_not_index(tmp_path: Path, direct_url: str) -> None:
    result = inspect(package(tmp_path), direct_url=direct_url)

    assert result["build"]["kind"] == "unknown"
    assert result["installation"]["kind"] == "unknown"


def test_duplicate_direct_url_keys_are_rejected(tmp_path: Path) -> None:
    result = inspect(
        package(tmp_path),
        direct_url='{"url":"file:///safe","url":"file:///secret","dir_info":{}}',
    )

    assert result["build"]["kind"] == "unknown"
    assert result["installation"]["kind"] == "unknown"


@pytest.mark.parametrize(
    "archive_info, expected_hash",
    [
        ({"hash": "sha256=" + "d" * 64}, "sha256=" + "d" * 64),
        ({"hashes": {"blake2b": "e" * 64}}, "blake2b=" + "e" * 64),
    ],
)
def test_archive_hashes_prefer_modern_sha256_and_fall_back_deterministically(
    tmp_path: Path, archive_info: dict[str, object], expected_hash: str
) -> None:
    result = inspect(
        package(tmp_path),
        direct_url=json.dumps(
            {"url": "https://example.invalid/wheel", "archive_info": archive_info}
        ),
    )

    assert result["installation"]["archive_hash"] == expected_hash


def test_oversized_provenance_is_unknown(tmp_path: Path) -> None:
    result = inspect(
        package(tmp_path),
        direct_url=json.dumps({"url": "file:///x", "vcs_info": {"commit_id": "a" * 513}}),
    )

    assert result["build"]["kind"] == "unknown"
    assert result["installation"]["kind"] == "unknown"


@pytest.mark.parametrize(
    "provenance",
    [
        {"url": "https://example.invalid/repo", "vcs_info": {"commit_id": "a" * 40}},
        {
            "url": "https://example.invalid/repo",
            "vcs_info": {"vcs": "other", "commit_id": "a" * 40},
        },
        {
            "url": "https://example.invalid/repo",
            "vcs_info": {"vcs": "git", "commit_id": "a" * 39 + "\n"},
        },
        {
            "url": "https://example.invalid/wheel",
            "archive_info": {"hashes": {"sha256\n": "a" * 64}},
        },
        {
            "url": "https://example.invalid/wheel",
            "archive_info": {"hashes": {"sha256": "a" * 63 + "\x1b"}},
        },
        {
            "url": "https://example.invalid/wheel",
            "archive_info": {"hash": "sha256\n=" + "a" * 64},
        },
    ],
)
def test_exposed_vcs_and_archive_components_reject_control_or_invalid_values(
    tmp_path: Path, provenance: dict[str, object]
) -> None:
    result = inspect(package(tmp_path), direct_url=json.dumps(provenance))

    assert result["build"]["kind"] == "unknown"
    assert result["installation"] == {
        "kind": "unknown",
        "editable": False,
        "source_revision": None,
        "archive_hash": None,
    }


def test_missing_distribution_metadata_is_unknown(tmp_path: Path) -> None:
    result = identity.runtime_identity(
        package_root=package(tmp_path),
        distribution_provider=lambda _name: (_ for _ in ()).throw(identity.PackageNotFoundError),
    )

    assert result["version"] == "0.2.0"
    assert result["build"]["kind"] == "unknown"
    assert result["installation"]["kind"] == "unknown"


def test_symlinked_package_content_refuses_a_content_identity(tmp_path: Path) -> None:
    root = package(tmp_path)
    try:
        (root / "linked.py").symlink_to(root / "a.py")
    except OSError:
        pytest.skip("symlinks unavailable")

    result = inspect(root)
    assert result["build"]["kind"] == "unknown"


def test_file_count_limit_stops_package_collection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = package(tmp_path)
    (root / "extra.py").write_text("EXTRA = 1\n", encoding="utf-8")
    monkeypatch.setattr(identity, "MAX_PACKAGE_FILES", 2)

    result = inspect(root)

    assert result["build"]["kind"] == "unknown"


def test_version_command_has_typed_json_and_human_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    payload: identity.RuntimeIdentity = {
        "schema_version": 1,
        "project": "murlocs",
        "version": "1.2.3",
        "build": {"kind": "development", "id": "sha256:" + "a" * 64, "verification": "unverified"},
        "installation": {
            "kind": "editable",
            "editable": True,
            "source_revision": None,
            "archive_hash": None,
        },
    }
    monkeypatch.setattr("murlocs.cli.runtime_identity", lambda: payload)

    human = build_cli().invoke(["version"])
    structured = build_cli().invoke(["version", "--format", "json"])

    assert human.output == (
        "murlocs 1.2.3\n"
        f"build: development (sha256:{'a' * 64})\n"
        "verification: unverified\n"
        "installation: editable\n"
    )
    assert json.loads(structured.output) == payload


def test_global_version_flag_remains_backward_compatible() -> None:
    result = build_cli().invoke(["--version"])

    assert result.exit_code == 0
    assert result.output == "murlocs 0.2.0\n"
