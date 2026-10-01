"""Freeze scope/ by hashing every file into manifest.json, and verify it later."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

MANIFEST_NAME = "manifest.json"
# Must agree with the runner's copy ignore list (sandbox/runner.py _IGNORE): anything hash_tree
# skips must also be something the runner never copies into a snapshot, or a planted file could
# influence a test run without changing a hash.
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


def tree_digest(tree: dict[str, str]) -> str:
    """One hash for a whole hash_tree() result, for anchoring in run.json."""
    return hashlib.sha256(json.dumps(tree, sort_keys=True).encode("utf-8")).hexdigest()


def write_manifest(scope_dir: Path) -> dict[str, str]:
    manifest = hash_tree(scope_dir)
    (scope_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def read_manifest(scope_dir: Path) -> dict[str, str]:
    return json.loads((scope_dir / MANIFEST_NAME).read_text(encoding="utf-8"))


def manifest_sha256(scope_dir: Path) -> str:
    """Hash of the manifest file itself. Stored in run.json at freeze so a re-freeze is detectable."""
    return _sha256(scope_dir / MANIFEST_NAME)


def verify_manifest(scope_dir: Path, expected_manifest_sha256: str | None = None) -> list[str]:
    """Relative paths whose hash differs from the manifest, plus added and missing files. Empty means intact.

    A missing or unreadable manifest, or one whose own hash differs from the anchor recorded
    at freeze time, is reported as a mismatch on "manifest.json". The manifest lives inside the
    directory it protects, so without the anchor a writer with access to scope/ could simply
    re-freeze after editing.
    """
    manifest_path = scope_dir / MANIFEST_NAME
    if not manifest_path.exists():
        return [MANIFEST_NAME]
    mismatches: set[str] = set()
    if expected_manifest_sha256 is not None and _sha256(manifest_path) != expected_manifest_sha256:
        mismatches.add(MANIFEST_NAME)
    try:
        expected = read_manifest(scope_dir)
    except (ValueError, OSError):
        return [MANIFEST_NAME]
    actual = hash_tree(scope_dir)
    mismatches |= {p for p in set(expected) | set(actual) if expected.get(p) != actual.get(p)}
    return sorted(mismatches)
