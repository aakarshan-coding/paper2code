"""ModalWorkspace against a FakeSandbox that mimics the Modal API over a local directory."""
import io
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper2code.sandbox.modal_workspace import ROOT, ModalWorkspace, extract_domains
from paper2code.sandbox.remote_tests import PayloadTooLarge
from paper2code.sandbox.workspace import InfrastructureError, WorkspaceError


class FakeSandbox:
    """Just enough of modal.Sandbox: exec(*args, timeout, workdir) and filesystem.{read,write,make_directory}.
    Remote absolute paths map under `base`, so /work is base/work and /tmp is base/tmp. Set `dead`
    to make every call fail the way a lost sandbox does."""

    def __init__(self, base: Path):
        self.base = base
        (base / "work").mkdir(parents=True, exist_ok=True)
        (base / "tmp").mkdir(exist_ok=True)
        self.filesystem = SimpleNamespace(
            write_text=lambda data, path: self._alive() or self._p(path).write_text(data, encoding="utf-8"),
            write_bytes=lambda data, path: self._alive() or self._p(path).write_bytes(data),
            read_text=lambda path: self._alive() or self._p(path).read_text(encoding="utf-8"),
            read_bytes=lambda path: self._alive() or self._read_bytes(path),
            make_directory=lambda path, create_parents=True: self._alive() or self._p(path).mkdir(parents=create_parents, exist_ok=True),
        )
        self.terminated = False
        self.dead = False
        self.bytes_read = 0
        self.object_id = "sb-fake"
        # Full path: a bare "bash" lets Windows pick System32's WSL launcher, which cannot see these paths.
        self.bash = shutil.which("bash") or "bash"
        # The sandbox's /tmp is base/tmp; ask this bash how it spells that directory.
        self.tmp_posix = subprocess.run([self.bash, "-c", "pwd"], cwd=base / "tmp", capture_output=True, text=True,
                                        stdin=subprocess.DEVNULL).stdout.strip()

    def _alive(self):
        if self.dead:
            raise ConnectionError("sandbox gone")
        return None

    def _read_bytes(self, path):
        data = self._p(path).read_bytes()
        self.bytes_read += len(data)
        return data

    def _p(self, remote: str) -> Path:
        local = Path(remote)
        if local.is_absolute() and (self.base.resolve() in local.resolve().parents or local.resolve() == self.base.resolve()):
            p = local.resolve()  # the workspace root is passed as a real local path in these tests
        elif remote.startswith("/"):
            p = (self.base / remote.lstrip("/")).resolve()  # a sandbox path such as /work or /tmp/x
        else:
            p = Path(remote).resolve()
            assert self.base.resolve() in p.parents, remote
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def work_root(self) -> str:
        return str(self.base / "work").replace(os.sep, "/")

    def exec(self, *args, timeout=None, workdir=None, **kw):
        self._alive()
        cmd = list(args)
        if cmd[:2] == ["bash", "-lc"] or cmd[:2] == ["bash", "-c"]:
            # The real sandbox runs in /work; locally the same command runs in base/work with /tmp mapped.
            cmd = [self.bash, "-c", cmd[2].replace("/tmp/", self.tmp_posix + "/")]
        cwd = self._p(workdir or ROOT)
        try:
            proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
            rc, out, err = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            rc, out, err = -1, "", ""
        return SimpleNamespace(stdout=io.StringIO(out), stderr=io.StringIO(err), wait=lambda: None, returncode=rc)

    def terminate(self):
        self._alive()  # terminating a sandbox that is already gone fails too
        self.terminated = True


@pytest.fixture
def ws(tmp_path):
    sb = FakeSandbox(tmp_path)
    return ModalWorkspace(sb, root=sb.work_root()), sb


def test_extract_domains():
    text = "Download https://huggingface.co/datasets/x and http://Example.com/a; see https://huggingface.co/y"
    assert extract_domains(text) == ["example.com", "huggingface.co"]
    assert extract_domains("no links") == []


def test_write_read_list_roundtrip(ws):
    w, sb = ws
    w.write_file("pkg/mod.py", "x = 1\n")
    w.write_file("top.py", "y = 2\n")
    assert w.read_file("pkg/mod.py") == "x = 1\n"
    assert w.list_files() == ["pkg/mod.py", "top.py"]
    assert w.list_files("pkg") == ["pkg/mod.py"]


def test_missing_file_is_a_workspace_error(ws):
    w, sb = ws
    with pytest.raises(WorkspaceError, match="not found"):
        w.read_file("absent.py")


def test_modal_workspace_confines_paths(ws):
    w, sb = ws
    unc = "\\" + "\\server" + "\\share"  # \\server\share, spelled out so no escape is misread
    for bad in ("../secret", "/etc/passwd", "a/../../x", "/work/../etc/passwd", "..", unc):
        with pytest.raises(WorkspaceError):
            w.read_file(bad)
        with pytest.raises(WorkspaceError):
            w.write_file(bad, "x")
        with pytest.raises(WorkspaceError):
            w.list_files(bad)


def test_exec_runs_in_work_and_maps_timeout(ws):
    w, sb = ws
    w.write_file("hello.py", "import pathlib; print(pathlib.Path.cwd().name)\n")
    r = w.exec(f'"{sys.executable}" hello.py', timeout_s=60)
    assert r.returncode == 0 and r.stdout.strip() == "work" and r.timed_out is False
    r = w.exec(f'"{sys.executable}" -c "import time; time.sleep(30)"', timeout_s=1)
    assert r.timed_out is True


def test_export_and_import_tarball(ws, tmp_path):
    w, sb = ws
    w.write_file("canary_method.py", "x = 1\n")
    w.write_file("pkg/__init__.py", "")
    w.write_file("__pycache__/junk.pyc", "")
    w.write_file(".assignment/spec.md", "spec")
    data = w.export_tarball()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        names = sorted(m.name for m in tf.getmembers() if m.isfile())
    assert names == ["snapshot/canary_method.py", "snapshot/pkg/__init__.py"]
    other_sb = FakeSandbox(tmp_path / "other")
    other = ModalWorkspace(other_sb, root=other_sb.work_root())
    other.import_tarball(data)
    assert other.read_file("canary_method.py") == "x = 1\n" and other.read_file("pkg/__init__.py") == ""


def test_write_assignment_is_readable_but_not_exported(ws):
    w, sb = ws
    w.write_assignment("SPEC", "IFACE", {"test_claim.py": "CLAIM"})
    assert w.read_file(".assignment/spec.md") == "SPEC"
    assert w.read_file(".assignment/interface.md") == "IFACE"
    assert w.read_file(".assignment/tests/public/test_claim.py") == "CLAIM"
    assert ".assignment/spec.md" in w.list_files()
    with tarfile.open(fileobj=io.BytesIO(w.export_tarball()), mode="r:gz") as tf:
        assert not any(".assignment" in m.name for m in tf.getmembers())


def test_export_refuses_an_oversize_workspace_before_downloading_it(ws):
    import random

    w, sb = ws
    w.export_max_bytes = 10_000
    w.write_file("data.bin", random.Random(0).randbytes(200_000).decode("latin-1"))
    with pytest.raises(PayloadTooLarge, match="payload"):
        w.export_tarball()
    assert sb.bytes_read == 0  # refused inside the sandbox; nothing was pulled to the manager


def test_dead_sandbox_is_an_infrastructure_error_not_a_workspace_error(ws):
    w, sb = ws
    w.write_file("a.py", "x\n")
    sb.dead = True
    for call in (lambda: w.exec("true", 10), lambda: w.read_file("a.py"), lambda: w.write_file("b.py", "y"),
                 lambda: w.list_files(), lambda: w.export_tarball()):
        with pytest.raises(InfrastructureError):
            call()
