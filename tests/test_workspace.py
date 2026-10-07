import sys

import pytest

from paper2code.sandbox.workspace import ExecResult, LocalWorkspace, WorkspaceError


def test_write_read_list_roundtrip(tmp_path):
    ws = LocalWorkspace(tmp_path)
    ws.write_file("pkg/mod.py", "x = 1\n")
    ws.write_file("top.txt", "hi")
    assert ws.read_file("pkg/mod.py") == "x = 1\n"
    assert ws.list_files() == ["pkg/mod.py", "top.txt"]
    assert ws.list_files("pkg") == ["pkg/mod.py"]


def test_paths_outside_root_are_refused(tmp_path):
    ws = LocalWorkspace(tmp_path / "ws")
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    for bad in ("../secret.txt", str(tmp_path / "secret.txt"), "a/../../secret.txt", "/etc/passwd", "C:/Windows/win.ini"):
        with pytest.raises(WorkspaceError):
            ws.read_file(bad)
        with pytest.raises(WorkspaceError):
            ws.write_file(bad, "x")
    assert (tmp_path / "secret.txt").read_text(encoding="utf-8") == "no"
    assert ws.list_files() == []


def test_read_missing_file_is_a_workspace_error(tmp_path):
    with pytest.raises(WorkspaceError, match="not found"):
        LocalWorkspace(tmp_path).read_file("nope.py")


def test_exec_runs_in_root_with_scrubbed_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leak")
    ws = LocalWorkspace(tmp_path)
    ws.write_file("hello.py", "import os, pathlib\nprint(pathlib.Path.cwd().name)\nprint('KEY' if 'OPENAI_API_KEY' in os.environ else 'NOKEY')\n")
    r = ws.exec(f'"{sys.executable}" hello.py', timeout_s=60)
    assert isinstance(r, ExecResult)
    assert r.returncode == 0 and r.timed_out is False
    assert r.stdout.split() == [tmp_path.name, "NOKEY"]


def test_exec_timeout_and_nonzero_exit(tmp_path):
    ws = LocalWorkspace(tmp_path)
    r = ws.exec(f'"{sys.executable}" -c "import time; time.sleep(30)"', timeout_s=2)
    assert r.timed_out is True
    r = ws.exec(f'"{sys.executable}" -c "import sys; sys.exit(3)"', timeout_s=30)
    assert r.returncode == 3


def test_exec_output_is_truncated(tmp_path):
    ws = LocalWorkspace(tmp_path)
    r = ws.exec(f'"{sys.executable}" -c "print(\'x\' * 50000)"', timeout_s=30)
    assert len(r.stdout) <= 20_100 and "truncated" in r.stdout
