"""Freeze scope/ by hashing every file into manifest.json, and verify it later."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

MANIFEST_NAME = "manifest.json"
_IGNORED_DIRS = {"__pycache__", ".pytest_cache"}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def hash_tree(root: Path) -> dict[str, str]:
    """sha256 of every file under root, keyed by posix relative path.

    Skips the top-level manifest.json (it cannot contain its own hash) and
    interpreter caches, which pytest may create inside tests/.
    """
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if len(rel.parts) == 1 and rel.name == MANIFEST_NAME:
            continue
        if _IGNORED_DIRS & set(rel.parts):
            continue
        out[rel.as_posix()] = _sha256(p)
    return out


def write_manifest(scope_dir: Path) -> dict[str, str]:
    manifest = hash_tree(scope_dir)
    (scope_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def read_manifest(scope_dir: Path) -> dict[str, str]:
    return json.loads((scope_dir / MANIFEST_NAME).read_text(encoding="utf-8"))


def verify_manifest(scope_dir: Path) -> list[str]:
    """Relative paths whose hash differs from the manifest, plus added and missing files. Empty means intact."""
    expected = read_manifest(scope_dir)
    actual = hash_tree(scope_dir)
    return sorted(p for p in set(expected) | set(actual) if expected.get(p) != actual.get(p))
