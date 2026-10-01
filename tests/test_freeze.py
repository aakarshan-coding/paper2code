import hashlib
import json

from paper2code.manager.freeze import (
    MANIFEST_NAME,
    hash_tree,
    read_manifest,
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
