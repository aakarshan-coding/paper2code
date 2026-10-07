import hashlib
import json

from paper2code.manager.freeze import (
    MANIFEST_NAME,
    hash_tree,
    manifest_sha256,
    read_manifest,
    tree_digest,
    verify_manifest,
    write_manifest,
)


def _make_scope(root):
    (root / "spec.md").write_text("spec", encoding="utf-8")
    (root / "tests" / "public").mkdir(parents=True)
    (root / "tests" / "hidden").mkdir(parents=True)
    (root / "tests" / "public" / "test_a.py").write_text("def test_a(): pass\n", encoding="utf-8")
    (root / "tests" / "hidden" / "test_h.py").write_text("def test_h(): pass\n", encoding="utf-8")
    return root


def test_hash_tree_uses_posix_relative_paths_and_sha256(tmp_path):
    scope = _make_scope(tmp_path)
    tree = hash_tree(scope)
    assert set(tree) == {"spec.md", "tests/public/test_a.py", "tests/hidden/test_h.py"}
    assert tree["spec.md"] == hashlib.sha256(b"spec").hexdigest()


def test_hash_tree_ignores_manifest_and_caches(tmp_path):
    scope = _make_scope(tmp_path)
    (scope / MANIFEST_NAME).write_text("{}", encoding="utf-8")
    (scope / "tests" / "public" / "__pycache__").mkdir()
    (scope / "tests" / "public" / "__pycache__" / "x.pyc").write_bytes(b"\x00")
    (scope / ".pytest_cache").mkdir()
    (scope / ".pytest_cache" / "v").write_text("x", encoding="utf-8")
    assert MANIFEST_NAME not in hash_tree(scope)
    assert not any("__pycache__" in p or ".pytest_cache" in p for p in hash_tree(scope))


def test_write_then_verify_is_clean(tmp_path):
    scope = _make_scope(tmp_path)
    written = write_manifest(scope)
    assert json.loads((scope / MANIFEST_NAME).read_text(encoding="utf-8")) == written
    assert read_manifest(scope) == written
    assert verify_manifest(scope) == []


def test_modified_file_detected(tmp_path):
    scope = _make_scope(tmp_path)
    write_manifest(scope)
    (scope / "tests" / "hidden" / "test_h.py").write_text("def test_h(): assert False\n", encoding="utf-8")
    assert verify_manifest(scope) == ["tests/hidden/test_h.py"]


def test_added_file_detected(tmp_path):
    scope = _make_scope(tmp_path)
    write_manifest(scope)
    (scope / "tests" / "public" / "conftest.py").write_text("", encoding="utf-8")
    assert verify_manifest(scope) == ["tests/public/conftest.py"]


def test_deleted_file_detected(tmp_path):
    scope = _make_scope(tmp_path)
    write_manifest(scope)
    (scope / "tests" / "public" / "test_a.py").unlink()
    assert verify_manifest(scope) == ["tests/public/test_a.py"]


def test_rewriting_manifest_itself_is_not_a_mismatch(tmp_path):
    scope = _make_scope(tmp_path)
    write_manifest(scope)
    write_manifest(scope)
    assert verify_manifest(scope) == []


def test_missing_manifest_is_a_mismatch(tmp_path):
    scope = _make_scope(tmp_path)
    write_manifest(scope)
    (scope / MANIFEST_NAME).unlink()
    assert verify_manifest(scope) == [MANIFEST_NAME]


def test_anchor_detects_rewritten_manifest(tmp_path):
    scope = _make_scope(tmp_path)
    write_manifest(scope)
    anchor = manifest_sha256(scope)
    assert verify_manifest(scope, anchor) == []
    (scope / "tests" / "hidden" / "test_h.py").write_text("def test_h(): assert True\n", encoding="utf-8")
    write_manifest(scope)
    assert verify_manifest(scope) == []  # the unanchored check is fooled by a re-freeze
    assert verify_manifest(scope, anchor) == [MANIFEST_NAME]


def test_tree_digest_is_stable_and_sensitive(tmp_path):
    scope = _make_scope(tmp_path)
    d1 = tree_digest(hash_tree(scope))
    assert d1 == tree_digest(hash_tree(scope))
    (scope / "spec.md").write_text("spec2", encoding="utf-8")
    assert tree_digest(hash_tree(scope)) != d1


def test_freeze_scope_writes_manifest_and_anchor(tmp_path):
    from datetime import date

    from paper2code.manager.freeze import freeze_scope
    from paper2code.manager.record import Caps, create_run

    rec = create_run(tmp_path, date(2026, 10, 7), Caps(), 10.0)
    (rec.run_dir / "scope").mkdir()
    _make_scope(rec.run_dir / "scope")
    manifest = freeze_scope(rec)
    assert set(manifest) == {"spec.md", "tests/public/test_a.py", "tests/hidden/test_h.py"}
    assert rec.scope_manifest_sha256 == manifest_sha256(rec.run_dir / "scope")
    assert verify_manifest(rec.run_dir / "scope", rec.scope_manifest_sha256) == []
