"""Pin the decided equality/hashing contract for `Manifest`.

`Manifest` is a `frozen=True` dataclass that also carries plain `dict` fields
(`coverage_exemptions`, `checks`, `scope_layers`, `invariant_layers`). The
dataclass-synthesized `__hash__` would raise `TypeError: unhashable type:
\'dict\'` the first time a manifest were used as a set member or dict key -- a
latent landmine for any threaded host that memoizes handler inputs. `model.py`
overrides `__hash__` to fold those dicts into sorted item tuples so hashing is
total and stays consistent with the field-wise `__eq__`. These tests lock that
in so the surprise cannot silently return.
"""

from __future__ import annotations

from pathlib import Path

from murlocs.manifest import load_manifest
from tests.support import initialize_repo


def _loaded_repo(tmp_path: Path, name: str = "Hash Probe") -> Path:
    root = tmp_path / name.replace(" ", "_").lower()
    root.mkdir()
    initialize_repo(root, "--name", name)
    return root


def test_manifest_is_hashable(tmp_path: Path) -> None:
    manifest = load_manifest(_loaded_repo(tmp_path))

    # Must not raise TypeError, and must be a stable int across calls.
    first = hash(manifest)
    assert isinstance(first, int)
    assert hash(manifest) == first


def test_equal_manifests_hash_equal_and_dedupe(tmp_path: Path) -> None:
    root = _loaded_repo(tmp_path)
    one = load_manifest(root)
    two = load_manifest(root)

    # Two independent loads of the same reviewed manifest are equal values.
    assert one == two
    assert hash(one) == hash(two)

    # Equality + a consistent hash means set/dict use collapses duplicates and
    # round-trips as a key -- the behavior a threaded host would rely on.
    assert len({one, two}) == 1
    assert {one: "ok"}[two] == "ok"


def test_manifests_from_distinct_networks_are_distinct(tmp_path: Path) -> None:
    left = load_manifest(_loaded_repo(tmp_path, "Left Network"))
    right = load_manifest(_loaded_repo(tmp_path, "Right Network"))

    assert left != right
    assert len({left, right}) == 2
