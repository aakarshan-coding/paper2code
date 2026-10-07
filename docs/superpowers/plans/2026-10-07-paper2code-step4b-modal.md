# paper2code Step 4b: Modal Sandbox and GPU Test Runner — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the builder's workspace into a Modal sandbox and the test runs into a Modal GPU function, so that the agent's shell cannot reach the manager's machine, the hidden tests are never present where the agent runs, GPU time is spent only inside `run_tests`, and the whole canary pipeline reaches `completed` with the agent working in the cloud.

**Architecture:** Two things move, behind two interfaces that step 4a defined. `ModalWorkspace` implements `Workspace` by proxying `exec`, `read_file`, `write_file`, `list_files` into a `modal.Sandbox` (CPU only, no secrets, outbound network limited to the package index and any dataset hosts named in `spec.md`). `ModalTestRunner` implements `TestRunner` by tarring a workspace snapshot plus a tests directory into one payload and calling a deployed Modal function `run_tests_remote` that unpacks it, runs pytest exactly as the local runner does (same bootstrap, same JUnit parsing), and returns the result as a dict; the function's wall time is the GPU-seconds charge. The build stage, when not in `--no-gpu` mode, creates the sandbox, seeds it with any earlier workspace (resume), runs the unchanged `AgentBuilder` against it, and on any exit exports `/work` back to `run_dir/workspace` and terminates the sandbox. The inspect stage runs the hidden tests through the same remote function against the exported workspace. The Agent SDK, the subscription login, and the Modal token all stay on the manager's machine.

**Tech Stack:** `modal` 1.6.1 (installed; token verified for workspace `aakarshank2007`), `modal.Sandbox.create(..., outbound_domain_allowlist=..., workdir=..., timeout=...)`, `sandbox.exec(*args, timeout=, workdir=)`, `sandbox.filesystem.{write_text, write_bytes, read_text, read_bytes, make_directory, list_files}`, `@app.function(gpu=, timeout=, image=)`, `modal.Function.from_name(app, name).remote(...)`, `modal deploy`. Existing `run_killable`, JUnit parsers, `hash_tree`/`tree_digest`, `AgentBuilder`, `BuildSession`.

**Spec:** `docs/superpowers/specs/2026-09-30-paper2code-design.md` (sections 9.1, 9.2, 10.1, 12, 13, 14)

**Facts established before planning (2026-10-07, live probe against the real account):**
- `Sandbox.create("sleep", "600", app=modal.App.lookup(name, create_if_missing=True), image=modal.Image.debian_slim(python_version="3.12"), timeout=600, cpu=1.0, memory=1024, outbound_domain_allowlist=["pypi.org", "files.pythonhosted.org"], workdir="/work")` created in 1.5 s. `sb.exec("python", "-c", ..., timeout=60)` returns a process with `.stdout.read()`, `.wait()`, `.returncode`; a timed-out exec returns `returncode == -1` promptly. `sb.filesystem.write_text(data, path)` and `read_text(path)` / `read_bytes(path)` work; a `tar czf` run inside and read back with `read_bytes` works. With the domain allowlist, `https://pypi.org` succeeds and `https://example.com` is blocked. `sb.terminate()` ends it. Total probe: 7 s.
- Sandboxes take no secrets unless passed; the builder sandbox passes none. `modal.Function.from_name("paper2code", "run_tests_remote")` requires the app to be deployed once with `modal deploy`.
- GPU prices (modal.com/pricing): T4 about 0.59 USD/h, L4 0.80, A10 1.10, A100-40GB 2.10. The GPU type and the function timeout are decorator parameters fixed at deploy time, read from environment variables so a redeploy changes them.
- A sandbox's `exec` with a PTY is needed only for interactive CLIs; plain commands work without it.
- `pip install torch` on Linux pulls the CUDA wheels (large). The builder sandbox image installs the CPU wheel from the PyTorch index; the tests image installs the default wheel so CUDA is available on the GPU.

## Global Constraints

- Builder environment (spec 9.1): CPU-only Modal sandbox, fresh Python; `workspace/` read-write; spec, interface, public tests available read-only (they are inlined in the prompt and, in this plan, also written under `/work/.assignment/` as plain files the agent may read); hidden tests never present; outbound network restricted to package installs and the dataset download named in the spec; sandbox destroyed when the run ends.
- GPU time only inside `run_tests` (spec 9.1), which runs in a separate GPU-backed function the builder's shell cannot reach (spec 9.2); each call has its own timeout from config.
- Inspect environment (spec 10.1): a fresh sandbox is not needed because the inspector in this plan does not execute code; `run_hidden_tests` uses the same GPU function pointed at the hidden suite.
- Secrets (spec 12): the Modal token on the manager; no `ANTHROPIC_API_KEY` anywhere; the subscription login stays with the manager's `claude` binary or `CLAUDE_CODE_OAUTH_TOKEN` (now set in the user environment).
- Spend limit on the Modal account is set in the dashboard by the author (spec 12); the per-run GPU dollar cap is enforced by `BuildSession` from `gpu_seconds`.
- Resume rule: a re-run of the build node seeds the new sandbox from the exported workspace, so earlier work and the test-run count both carry over.
- `--no-gpu` keeps everything local (step 4a behaviour); without it, Modal is used for both the workspace and the tests.
- Commit after every task with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`; tests with `TEMP`/`TMP`/`TMPDIR` on the scratchpad; big edits via a Python patch script; no live Modal or SDK calls inside the unit suite.

## Review Focus

1. **The sandbox dies mid-session** (Modal timeout, container failure, network drop). The build must end as an infrastructure `error` with reason `sandbox_failed`, export whatever can be exported, and leave `build.log` and counters consistent for a resume. Pinned to Task 4 (`test_sandbox_failure_is_an_error_outcome_and_exports_what_it_can`).
2. **The agent fills the workspace with a dataset or a virtual environment.** The snapshot tarball sent to the GPU function must exclude `.venv`, `__pycache__`, `.git` and must refuse to send more than a configured size, failing the `run_tests` call with a clear message instead of a multi-gigabyte upload. Pinned to Task 1 (`test_payload_excludes_junk_and_caps_size`).
3. **The remote function times out** (the function's own timeout, longer than the pytest timeout). The runner must report `timed_out=True` and GPU seconds for the time spent, not raise out of the stage. Pinned to Task 2 (`test_runner_maps_function_timeout_to_timed_out_result`).
4. **A path the agent writes escapes `/work`** through the Modal filesystem API (`../`, absolute `/etc/...`, `/work/../`). Must be refused exactly as the local workspace refuses it. Pinned to Task 3 (`test_modal_workspace_confines_paths`).
5. **A re-run after a crash must not start from an empty sandbox.** The exported workspace from the previous attempt is uploaded before the agent's first prompt, and the prompt says so. Pinned to Task 4 (`test_resume_seeds_sandbox_from_exported_workspace`).

---

### Task 1: Remote test execution core and payloads

**Files:**
- Create: `src/paper2code/sandbox/remote_tests.py`
- Modify: `src/paper2code/config.py`, `config.yaml`
- Test: `tests/test_remote_tests.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: `run_killable`, `parse_junit`, `parse_junit_errors`, `parse_junit_details`, `_BOOTSTRAP`, `scrubbed_environment` from `sandbox/runner.py`; `hash_tree`, `tree_digest` from `manager/freeze.py`.
- Produces:
  - `PAYLOAD_EXCLUDE = (".venv", "venv", "__pycache__", ".git", ".pytest_cache", "*.pyc")`.
  - `tar_directory(root: Path, prefix: str) -> bytes`: gzip tarball of `root` with members under `prefix/`, excluding `PAYLOAD_EXCLUDE`.
  - `build_payload(snapshot_tar: bytes, tests_dir: Path, max_bytes: int) -> bytes`: one gzip tarball containing the members of `snapshot_tar` (which must already be under `snapshot/`) plus `tests_dir` under `tests/`; raises `PayloadTooLarge` if the result exceeds `max_bytes`.
  - `PayloadTooLarge(Exception)`.
  - `execute_tests(payload: bytes, timeout_s: int) -> dict`: unpacks into a temp dir, computes `workspace_sha256 = tree_digest(hash_tree(snapshot))`, runs the same pytest bootstrap as `LocalTestRunner` with `run_killable`, returns `{"passed": [...], "failed": [...], "errored": [...], "skipped": [...], "messages": {...}, "returncode": int, "timed_out": bool, "duration_s": float, "output": str, "workspace_sha256": str}`. Pure Python, no Modal import, so it is unit-tested locally and is the body of the Modal function in Task 2.
  - `result_from_dict(d: dict, gpu_seconds: float) -> TestRunResult`.
  - Config: `modal_app_name: str = "paper2code"`, `sandbox_allowed_domains: list[str] = ["pypi.org", "files.pythonhosted.org", "download.pytorch.org"]`, `sandbox_cpu: float = 2.0`, `sandbox_memory_mb: int = 4096`, `payload_max_mb: int = 50`.

- [ ] **Step 1: Write the failing tests**

`tests/test_remote_tests.py`:

```python
import io
import tarfile
from pathlib import Path

import pytest

from paper2code.sandbox.remote_tests import PayloadTooLarge, build_payload, execute_tests, result_from_dict, tar_directory


def _members(data: bytes) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        return sorted(m.name for m in tf.getmembers() if m.isfile())


def test_tar_directory_prefixes_and_excludes_junk(tmp_path):
    (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "mod.cpython-312.pyc").write_bytes(b"\x00")
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "big.so").write_bytes(b"\x00" * 10)
    (tmp_path / "stray.pyc").write_bytes(b"\x00")
    data = tar_directory(tmp_path, "snapshot")
    assert _members(data) == ["snapshot/mod.py", "snapshot/pkg/__init__.py"]


def test_payload_excludes_junk_and_caps_size(tmp_path, canary_dir):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "canary_method.py").write_text("x = 1\n", encoding="utf-8")
    payload = build_payload(tar_directory(ws, "snapshot"), canary_dir / "scope" / "tests" / "public", max_bytes=10_000_000)
    names = _members(payload)
    assert "snapshot/canary_method.py" in names and "tests/test_claim.py" in names and "tests/test_units.py" in names
    (ws / "data.bin").write_bytes(b"\xff" * 300_000)  # incompressible
    with pytest.raises(PayloadTooLarge, match="payload"):
        build_payload(tar_directory(ws, "snapshot"), canary_dir / "scope" / "tests" / "public", max_bytes=100_000)


def test_execute_tests_runs_the_canary(canary_dir):
    payload = build_payload(tar_directory(canary_dir / "reference", "snapshot"), canary_dir / "scope" / "tests" / "hidden", max_bytes=10_000_000)
    d = execute_tests(payload, timeout_s=120)
    assert d["timed_out"] is False and d["returncode"] == 0
    assert len(d["passed"]) == 5 and d["failed"] == [] and d["errored"] == []
    assert len(d["workspace_sha256"]) == 64 and d["duration_s"] > 0
    r = result_from_dict(d, gpu_seconds=12.5)
    assert r.all_passed and r.gpu_seconds == 12.5 and r.workspace_sha256 == d["workspace_sha256"]


def test_execute_tests_reports_failures_and_messages(canary_dir):
    payload = build_payload(tar_directory(canary_dir / "hardcoded", "snapshot"), canary_dir / "scope" / "tests" / "hidden", max_bytes=10_000_000)
    d = execute_tests(payload, timeout_s=120)
    assert len(d["failed"]) == 5 and all("assert" in m or "AssertionError" in m for m in d["messages"].values())
    r = result_from_dict(d, gpu_seconds=1.0)
    assert not r.all_passed and set(r.failed) == set(d["failed"]) and r.messages == d["messages"]


def test_execute_tests_times_out(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_slow.py").write_text("import time\ndef test_slow():\n    time.sleep(30)\n", encoding="utf-8")
    d = execute_tests(build_payload(tar_directory(ws, "snapshot"), tests, max_bytes=10_000_000), timeout_s=2)
    assert d["timed_out"] is True and result_from_dict(d, 2.0).all_passed is False
```

Append to `tests/test_config.py`:

```python
def test_step4b_config_defaults():
    cfg = Config()
    assert cfg.modal_app_name == "paper2code"
    assert cfg.sandbox_allowed_domains == ["pypi.org", "files.pythonhosted.org", "download.pytorch.org"]
    assert (cfg.sandbox_cpu, cfg.sandbox_memory_mb, cfg.payload_max_mb) == (2.0, 4096, 50)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_remote_tests.py tests/test_config.py -q`
Expected: `ModuleNotFoundError: No module named 'paper2code.sandbox.remote_tests'` and an `AttributeError` for `modal_app_name`.

- [ ] **Step 3: Write `src/paper2code/sandbox/remote_tests.py`**

```python
"""The test run as a pure function of (payload bytes, timeout): unpack, run pytest the way the local
runner does, return a dict. The Modal GPU function in modal_app.py is a thin wrapper around this,
so the real logic is unit-tested here without Modal."""
from __future__ import annotations

import fnmatch
import io
import sys
import tarfile
import tempfile
import time
from pathlib import Path

from paper2code.manager.freeze import hash_tree, tree_digest
from paper2code.sandbox.runner import (
    _BOOTSTRAP,
    TestRunResult,
    parse_junit,
    parse_junit_details,
    parse_junit_errors,
    run_killable,
    scrubbed_environment,
)

PAYLOAD_EXCLUDE = (".venv", "venv", "__pycache__", ".git", ".pytest_cache", "*.pyc")


class PayloadTooLarge(Exception):
    pass


def _excluded(rel: Path) -> bool:
    return any(fnmatch.fnmatch(part, pat) for part in rel.parts for pat in PAYLOAD_EXCLUDE)


def tar_directory(root: Path, prefix: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root)
            if not p.is_file() or _excluded(rel):
                continue
            tf.add(p, arcname=f"{prefix}/{rel.as_posix()}", recursive=False)
    return buf.getvalue()


def build_payload(snapshot_tar: bytes, tests_dir: Path, max_bytes: int) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as out:
        with tarfile.open(fileobj=io.BytesIO(snapshot_tar), mode="r:gz") as src:
            for m in src.getmembers():
                if m.isfile() and m.name.startswith("snapshot/"):
                    out.addfile(m, src.extractfile(m))
        for p in sorted(tests_dir.rglob("*")):
            rel = p.relative_to(tests_dir)
            if p.is_file() and not _excluded(rel):
                out.add(p, arcname=f"tests/{rel.as_posix()}", recursive=False)
    data = buf.getvalue()
    if len(data) > max_bytes:
        raise PayloadTooLarge(f"test payload is {len(data)} bytes, over the {max_bytes} byte limit; is a dataset or a venv in the workspace?")
    return data


def _safe_extract(tf: tarfile.TarFile, dest: Path) -> None:
    for m in tf.getmembers():
        target = (dest / m.name).resolve()
        if dest.resolve() not in target.parents and target != dest.resolve():
            raise ValueError(f"tar member escapes destination: {m.name}")
    tf.extractall(dest, filter="data")


def execute_tests(payload: bytes, timeout_s: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="p2c-remote-", ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tf:
            _safe_extract(tf, root)
        snapshot = root / "snapshot"
        tests = root / "tests"
        snapshot.mkdir(exist_ok=True)
        tests.mkdir(exist_ok=True)
        report = root / "report.xml"
        bootstrap = root / "bootstrap.py"
        bootstrap.write_text(_BOOTSTRAP, encoding="utf-8")
        digest = tree_digest(hash_tree(snapshot))
        env = scrubbed_environment()
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        cmd = [
            sys.executable, "-I", "-B", str(bootstrap), str(snapshot), str(tests),
            "-q", "-p", "no:cacheprovider", f"--junitxml={report}", "--rootdir", str(tests),
        ]
        start = time.monotonic()
        returncode, stdout, stderr, timed_out = run_killable(cmd, cwd=root, env=env, timeout_s=timeout_s)
        duration = time.monotonic() - start
        if timed_out or not report.exists():
            passed, failed, errored, skipped, messages = (), (), (), (), {}
        else:
            passed, failed = parse_junit(report)
            errored = parse_junit_errors(report)
            skipped, messages = parse_junit_details(report)
        return {
            "passed": list(passed), "failed": list(failed), "errored": list(errored), "skipped": list(skipped),
            "messages": dict(messages), "returncode": returncode, "timed_out": timed_out,
            "duration_s": round(duration, 3), "output": (stdout + stderr)[-20_000:], "workspace_sha256": digest,
        }


def result_from_dict(d: dict, gpu_seconds: float) -> TestRunResult:
    return TestRunResult(
        tuple(d["passed"]), tuple(d["failed"]), int(d["returncode"]), bool(d["timed_out"]), float(d["duration_s"]),
        float(gpu_seconds), str(d.get("output", "")), str(d.get("workspace_sha256", "")),
        tuple(d.get("errored", ())), tuple(d.get("skipped", ())), dict(d.get("messages", {})),
    )
```

- [ ] **Step 4: Config additions**

In `src/paper2code/config.py` add to `Config` after `builder_tool_timeout_s`:

```python
    modal_app_name: str = "paper2code"
    sandbox_allowed_domains: list[str] = field(default_factory=lambda: ["pypi.org", "files.pythonhosted.org", "download.pytorch.org"])
    sandbox_cpu: float = 2.0
    sandbox_memory_mb: int = 4096
    payload_max_mb: int = 50
```

and the `load_config` keywords (`str(...)`, `list(...)`, `float(...)`, `int(...)`, `int(...)`). Append to `config.yaml`:

```yaml
modal_app_name: paper2code     # deployed with `modal deploy src/paper2code/sandbox/modal_app.py`
sandbox_allowed_domains: [pypi.org, files.pythonhosted.org, download.pytorch.org]   # plus hosts named in spec.md
sandbox_cpu: 2.0
sandbox_memory_mb: 4096
payload_max_mb: 50             # a run_tests snapshot bigger than this is refused
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_remote_tests.py tests/test_config.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/paper2code/sandbox/remote_tests.py src/paper2code/config.py config.yaml tests/test_remote_tests.py tests/test_config.py
git commit -m "Remote test execution core: payload tarballs and a pure execute_tests

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: The Modal app and `ModalTestRunner`

**Files:**
- Create: `src/paper2code/sandbox/modal_app.py`
- Create: `src/paper2code/sandbox/modal_runner.py`
- Test: `tests/test_modal_runner.py`

**Interfaces:**
- Consumes: `execute_tests`, `build_payload`, `tar_directory`, `result_from_dict`, `PayloadTooLarge` (Task 1); `TestRunner` protocol.
- Produces:
  - `modal_app.py`: `app = modal.App("paper2code")`; `tests_image` (debian_slim 3.12, `pip_install("pytest>=8", "numpy", "scipy", "scikit-learn", "torch")`, `.add_local_python_source("paper2code")`); `builder_image` (same base, CPU torch via `.run_commands("pip install torch --index-url https://download.pytorch.org/whl/cpu")`, plus `pip_install("pytest>=8", "numpy", "scipy", "scikit-learn")`); `GPU = os.environ.get("PAPER2CODE_GPU", "T4")`; `FUNCTION_TIMEOUT_S = int(os.environ.get("PAPER2CODE_TEST_FUNCTION_TIMEOUT", "1800"))`; `@app.function(name="run_tests_remote", image=tests_image, gpu=GPU, timeout=FUNCTION_TIMEOUT_S) def run_tests_remote(payload: bytes, timeout_s: int) -> dict: return execute_tests(payload, timeout_s)`. The module imports `modal` at top; it is never imported by the unit suite except through `ModalTestRunner`'s lazy default.
  - `ModalTestRunner(timeout_s: int, max_payload_bytes: int, remote: Callable[[bytes, int], dict] | None = None, snapshot_source: Callable[[], bytes] | None = None, app_name: str = "paper2code")`: `run(workspace: Path, tests_dir: Path) -> TestRunResult`. `snapshot_source` provides the snapshot tarball (a sandbox export); when `None`, `tar_directory(workspace, "snapshot")` is used. `remote` defaults to `modal.Function.from_name(app_name, "run_tests_remote").remote` (looked up lazily on first use). `gpu_seconds = d["duration_s"]`. A `PayloadTooLarge` becomes a `TestRunResult` with `timed_out=False`, `returncode=-2`, `failed=()`, `passed=()`, and the message in `output` (so `all_passed` is False and the agent reads why). A `modal.exception.FunctionTimeoutError` (or any exception whose class name ends in `TimeoutError`) becomes `timed_out=True` with `gpu_seconds` equal to the elapsed wall time.
  - `RemoteError(Exception)` for any other remote failure, raised to the stage (it becomes the stage's exception and is logged).

- [ ] **Step 1: Write the failing tests**

`tests/test_modal_runner.py`:

```python
import time

import pytest

from paper2code.sandbox.modal_runner import ModalTestRunner, RemoteError
from paper2code.sandbox.remote_tests import execute_tests


def _local_remote(payload: bytes, timeout_s: int) -> dict:
    return execute_tests(payload, timeout_s)


def test_runner_round_trips_through_a_remote_callable(canary_dir):
    runner = ModalTestRunner(timeout_s=120, max_payload_bytes=10_000_000, remote=_local_remote)
    r = runner.run(canary_dir / "reference", canary_dir / "scope" / "tests" / "public")
    assert r.all_passed and len(r.passed) == 7 and r.gpu_seconds == r.duration_s > 0
    assert len(r.workspace_sha256) == 64


def test_runner_uses_snapshot_source_when_given(canary_dir, tmp_path):
    from paper2code.sandbox.remote_tests import tar_directory

    calls = {"n": 0}

    def source():
        calls["n"] += 1
        return tar_directory(canary_dir / "reference", "snapshot")

    runner = ModalTestRunner(timeout_s=120, max_payload_bytes=10_000_000, remote=_local_remote, snapshot_source=source)
    r = runner.run(tmp_path / "ignored", canary_dir / "scope" / "tests" / "hidden")
    assert r.all_passed and calls["n"] == 1


def test_payload_too_large_is_a_failed_result_not_an_exception(canary_dir, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "blob.bin").write_bytes(b"\xff" * 200_000)
    runner = ModalTestRunner(timeout_s=120, max_payload_bytes=50_000, remote=_local_remote)
    r = runner.run(ws, canary_dir / "scope" / "tests" / "public")
    assert r.all_passed is False and r.returncode == -2 and "payload" in r.output and r.gpu_seconds == 0.0


def test_runner_maps_function_timeout_to_timed_out_result(canary_dir):
    class FunctionTimeoutError(Exception):
        pass

    def remote(payload, timeout_s):
        time.sleep(0.2)
        raise FunctionTimeoutError("function timed out")

    r = ModalTestRunner(timeout_s=120, max_payload_bytes=10_000_000, remote=remote).run(canary_dir / "reference", canary_dir / "scope" / "tests" / "public")
    assert r.timed_out is True and r.all_passed is False and r.gpu_seconds >= 0.2


def test_other_remote_failures_raise_remote_error(canary_dir):
    def remote(payload, timeout_s):
        raise RuntimeError("container crashed")

    with pytest.raises(RemoteError, match="container crashed"):
        ModalTestRunner(timeout_s=120, max_payload_bytes=10_000_000, remote=remote).run(canary_dir / "reference", canary_dir / "scope" / "tests" / "public")


def test_modal_app_module_declares_the_function():
    import importlib.util
    from pathlib import Path

    src = Path("src/paper2code/sandbox/modal_app.py").read_text(encoding="utf-8")
    assert 'modal.App("paper2code")' in src and 'name="run_tests_remote"' in src and "execute_tests(payload, timeout_s)" in src
    assert "add_local_python_source" in src and "download.pytorch.org/whl/cpu" in src
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_modal_runner.py -q`
Expected: `ModuleNotFoundError: No module named 'paper2code.sandbox.modal_runner'`.

- [ ] **Step 3: Write `src/paper2code/sandbox/modal_app.py`**

```python
"""The deployed Modal app: the GPU function that runs tests, and the images. Deploy once with
`modal deploy src/paper2code/sandbox/modal_app.py`; the manager looks the function up by name.

The GPU type and the function timeout are fixed at deploy time from environment variables, so a
different GPU means a redeploy, not a code change.
"""
from __future__ import annotations

import os

import modal

from paper2code.sandbox.remote_tests import execute_tests

GPU = os.environ.get("PAPER2CODE_GPU", "T4")
FUNCTION_TIMEOUT_S = int(os.environ.get("PAPER2CODE_TEST_FUNCTION_TIMEOUT", "1800"))

app = modal.App("paper2code")

_base = modal.Image.debian_slim(python_version="3.12").apt_install("git")

# Where the tests run: CUDA-capable torch, the package source so execute_tests is importable.
tests_image = (
    _base.pip_install("pytest>=8", "numpy", "scipy", "scikit-learn", "torch", "pypdf")
    .add_local_python_source("paper2code")
)

# Where the builder works: CPU-only torch keeps the image small; no package source, no secrets.
builder_image = (
    _base.pip_install("pytest>=8", "numpy", "scipy", "scikit-learn")
    .run_commands("pip install torch --index-url https://download.pytorch.org/whl/cpu")
)


@app.function(name="run_tests_remote", image=tests_image, gpu=GPU, timeout=FUNCTION_TIMEOUT_S)
def run_tests_remote(payload: bytes, timeout_s: int) -> dict:
    return execute_tests(payload, timeout_s)
```

- [ ] **Step 4: Write `src/paper2code/sandbox/modal_runner.py`**

```python
"""TestRunner backed by the deployed Modal GPU function. The hidden tests travel inside the call's
payload and never touch the builder's sandbox."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from paper2code.sandbox.remote_tests import PayloadTooLarge, build_payload, result_from_dict, tar_directory
from paper2code.sandbox.runner import TestRunResult


class RemoteError(Exception):
    """The remote test function failed for a reason other than a timeout."""


def _default_remote(app_name: str) -> Callable[[bytes, int], dict]:
    import modal

    fn = modal.Function.from_name(app_name, "run_tests_remote")
    return lambda payload, timeout_s: fn.remote(payload, timeout_s)


class ModalTestRunner:
    def __init__(
        self,
        timeout_s: int,
        max_payload_bytes: int,
        remote: Callable[[bytes, int], dict] | None = None,
        snapshot_source: Callable[[], bytes] | None = None,
        app_name: str = "paper2code",
    ) -> None:
        self.timeout_s = timeout_s
        self.max_payload_bytes = max_payload_bytes
        self._remote = remote
        self.snapshot_source = snapshot_source
        self.app_name = app_name

    @property
    def remote(self) -> Callable[[bytes, int], dict]:
        if self._remote is None:
            self._remote = _default_remote(self.app_name)
        return self._remote

    def run(self, workspace: Path, tests_dir: Path) -> TestRunResult:
        snapshot = self.snapshot_source() if self.snapshot_source else tar_directory(workspace, "snapshot")
        try:
            payload = build_payload(snapshot, tests_dir, self.max_payload_bytes)
        except PayloadTooLarge as exc:
            return TestRunResult((), (), -2, False, 0.0, 0.0, f"run_tests refused: {exc}", "")
        start = time.monotonic()
        try:
            d = self.remote(payload, self.timeout_s)
        except Exception as exc:
            elapsed = time.monotonic() - start
            if type(exc).__name__.endswith("TimeoutError"):
                return TestRunResult((), (), -1, True, elapsed, elapsed, f"remote test function timed out: {exc}", "")
            raise RemoteError(f"{type(exc).__name__}: {exc}") from exc
        return result_from_dict(d, gpu_seconds=float(d.get("duration_s", 0.0)))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_modal_runner.py -q`
Expected: 6 PASS. Importing `modal_app.py` is not required by any test (the source is read as text) so the suite never touches Modal.

- [ ] **Step 6: Commit**

```bash
git add src/paper2code/sandbox/modal_app.py src/paper2code/sandbox/modal_runner.py tests/test_modal_runner.py
git commit -m "Modal app with the GPU test function, and ModalTestRunner over it

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `ModalWorkspace` and the builder sandbox

**Files:**
- Create: `src/paper2code/sandbox/modal_workspace.py`
- Test: `tests/test_modal_workspace.py`

**Interfaces:**
- Consumes: `Workspace`, `ExecResult`, `WorkspaceError`, `MAX_OUTPUT_CHARS`/`_truncate` from `sandbox/workspace.py`; `Config`.
- Produces:
  - `ROOT = "/work"`, `ASSIGNMENT_DIR = "/work/.assignment"`.
  - `extract_domains(text: str) -> list[str]`: hostnames of `http(s)://` URLs found in text, lowercase, deduplicated, sorted.
  - `ModalWorkspace(sandbox, root: str = ROOT)` implementing `Workspace`: `exec(command, timeout_s)` runs `sandbox.exec("bash", "-lc", command, timeout=timeout_s, workdir=root)`, reads stdout and stderr, waits, maps `returncode == -1` to `timed_out=True`, truncates output; `read_file`/`write_file`/`list_files` use `sandbox.filesystem` with posix path confinement (`_resolve(path) -> str` rejects absolute paths and anything normalising outside `root`); `list_files` runs `find` inside the sandbox (files only, `__pycache__` skipped, sorted, relative posix); `export_tarball() -> bytes` runs `tar czf /tmp/p2c-ws.tgz --exclude=__pycache__ --exclude=.venv --exclude=.git -C /work --transform 's,^\./,snapshot/,' .` and reads the bytes back; `import_tarball(data: bytes, prefix: str = "snapshot")` writes the bytes to `/tmp/p2c-seed.tgz` and extracts the `prefix/` members into `/work`; `write_assignment(spec_md, interface_md, public_tests: dict[str, str])` writes read-only copies under `/work/.assignment/` (so `list_files`/`read_file` can see them, and `export_tarball` excludes `.assignment`).
  - `create_builder_sandbox(config, spec_md: str, wall_clock_s: int)`: `modal.Sandbox.create("sleep", str(wall_clock_s + 900), app=modal.App.lookup(config.modal_app_name, create_if_missing=True), image=builder_image, timeout=wall_clock_s + 900, cpu=config.sandbox_cpu, memory=config.sandbox_memory_mb, workdir=ROOT, outbound_domain_allowlist=sorted(set(config.sandbox_allowed_domains) | set(extract_domains(spec_md))))` with `builder_image` imported lazily from `modal_app`. No secrets.
  - `FakeSandbox` is a test helper (in the test file): a local directory standing in for `/work`, with `exec` via `LocalWorkspace`-style subprocess and a `filesystem` object with the Modal method names.

- [ ] **Step 1: Write the failing tests**

`tests/test_modal_workspace.py`:

```python
"""ModalWorkspace against a FakeSandbox that mimics the Modal API over a local directory."""
import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper2code.sandbox.modal_workspace import ASSIGNMENT_DIR, ROOT, ModalWorkspace, extract_domains
from paper2code.sandbox.workspace import WorkspaceError


class FakeSandbox:
    """Just enough of modal.Sandbox: exec(*args, timeout, workdir) and filesystem.{read,write,make_directory}."""

    def __init__(self, base: Path):
        self.base = base
        (base / "work").mkdir(parents=True, exist_ok=True)
        (base / "tmp").mkdir(exist_ok=True)
        self.filesystem = SimpleNamespace(
            write_text=lambda data, path: self._p(path).write_text(data, encoding="utf-8"),
            write_bytes=lambda data, path: self._p(path).write_bytes(data),
            read_text=lambda path: self._p(path).read_text(encoding="utf-8"),
            read_bytes=lambda path: self._p(path).read_bytes(),
            make_directory=lambda path, create_parents=True: self._p(path).mkdir(parents=create_parents, exist_ok=True),
        )
        self.terminated = False

    def _p(self, remote: str) -> Path:
        assert remote.startswith("/"), remote
        p = (self.base / remote.lstrip("/")).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def exec(self, *args, timeout=None, workdir=None, **kw):
        cmd = list(args)
        if cmd[:2] == ["bash", "-lc"]:
            cmd = ["bash", "-c", cmd[2]]
        cwd = self._p(workdir or ROOT)
        env = {**os.environ, "FAKE_ROOT": str(self.base)}
        try:
            proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
            rc, out, err = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            rc, out, err = -1, "", ""
        return SimpleNamespace(stdout=io.StringIO(out), stderr=io.StringIO(err), wait=lambda: None, returncode=rc)

    def terminate(self):
        self.terminated = True


@pytest.fixture
def ws(tmp_path):
    sb = FakeSandbox(tmp_path)
    return ModalWorkspace(sb, root=str(tmp_path / "work").replace(os.sep, "/")), sb


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


def test_modal_workspace_confines_paths(ws):
    w, sb = ws
    for bad in ("../secret", "/etc/passwd", "a/../../x", "/work/../etc/passwd"):
        with pytest.raises(WorkspaceError):
            w.read_file(bad)
        with pytest.raises(WorkspaceError):
            w.write_file(bad, "x")


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
    w.write_file("__pycache__/junk.pyc", "")
    w.write_file(".assignment/spec.md", "spec")
    data = w.export_tarball()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        names = sorted(m.name for m in tf.getmembers() if m.isfile())
    assert names == ["snapshot/canary_method.py"]
    other = ModalWorkspace(FakeSandbox(tmp_path / "other"), root=str(tmp_path / "other" / "work").replace(os.sep, "/"))
    other.import_tarball(data)
    assert other.read_file("canary_method.py") == "x = 1\n"


def test_write_assignment_is_readable_but_not_exported(ws):
    w, sb = ws
    w.write_assignment("SPEC", "IFACE", {"test_claim.py": "CLAIM"})
    assert w.read_file(".assignment/spec.md") == "SPEC"
    assert w.read_file(".assignment/tests/public/test_claim.py") == "CLAIM"
    assert ".assignment/spec.md" in w.list_files()
    with tarfile.open(fileobj=io.BytesIO(w.export_tarball()), mode="r:gz") as tf:
        assert not any(".assignment" in m.name for m in tf.getmembers())
```

The fake maps `/work` under `tmp_path`, so `ROOT`-relative confinement is exercised against real paths. Note the test passes `root=` with forward slashes; `ModalWorkspace` treats `root` as a posix string.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_modal_workspace.py -q`
Expected: `ModuleNotFoundError: No module named 'paper2code.sandbox.modal_workspace'`.

- [ ] **Step 3: Write `src/paper2code/sandbox/modal_workspace.py`**

```python
"""Workspace backed by a Modal sandbox. The agent's four file/shell tools land here; nothing on the
manager's machine is reachable from inside."""
from __future__ import annotations

import posixpath
import re
import shlex
import time
from typing import Any

from paper2code.sandbox.workspace import ExecResult, WorkspaceError, _truncate

ROOT = "/work"
ASSIGNMENT_DIR = "/work/.assignment"
_URL_RE = re.compile(r"https?://([A-Za-z0-9.-]+)", re.IGNORECASE)
_EXPORT_EXCLUDES = ("__pycache__", ".venv", "venv", ".git", ".pytest_cache", ".assignment")


def extract_domains(text: str) -> list[str]:
    return sorted({m.group(1).lower() for m in _URL_RE.finditer(text)})


class ModalWorkspace:
    def __init__(self, sandbox: Any, root: str = ROOT) -> None:
        self.sandbox = sandbox
        self.root = root.rstrip("/") or "/"

    def _resolve(self, path: str) -> str:
        if posixpath.isabs(path) or path.startswith(("\\", "//")):
            raise WorkspaceError(f"absolute paths are not allowed: {path}")
        full = posixpath.normpath(posixpath.join(self.root, path))
        if full != self.root and not full.startswith(self.root + "/"):
            raise WorkspaceError(f"path escapes the workspace: {path}")
        return full

    def read_file(self, path: str) -> str:
        full = self._resolve(path)
        try:
            return self.sandbox.filesystem.read_text(full)
        except Exception as exc:
            raise WorkspaceError(f"file not found: {path} ({type(exc).__name__})") from exc

    def write_file(self, path: str, content: str) -> None:
        full = self._resolve(path)
        self.sandbox.filesystem.make_directory(posixpath.dirname(full), create_parents=True)
        self.sandbox.filesystem.write_text(content, full)

    def list_files(self, path: str = "") -> list[str]:
        base = self._resolve(path) if path else self.root
        r = self.exec(f"cd {shlex.quote(self.root)} && find {shlex.quote(base)} -type f -not -path '*/__pycache__/*' | sort", timeout_s=60)
        prefix = self.root + "/"
        return [line[len(prefix):] if line.startswith(prefix) else line for line in r.stdout.split("\n") if line.strip()]

    def exec(self, command: str, timeout_s: int) -> ExecResult:
        start = time.monotonic()
        proc = self.sandbox.exec("bash", "-lc", command, timeout=timeout_s, workdir=self.root)
        out = proc.stdout.read()
        err = proc.stderr.read()
        proc.wait()
        rc = proc.returncode
        timed_out = rc == -1
        return ExecResult(rc, _truncate(out or ""), _truncate(err or ""), timed_out, time.monotonic() - start)

    def export_tarball(self) -> bytes:
        excludes = " ".join(f"--exclude={e}" for e in _EXPORT_EXCLUDES)
        r = self.exec(f"tar czf /tmp/p2c-ws.tgz {excludes} -C {shlex.quote(self.root)} --transform 's,^\\./,snapshot/,' .", timeout_s=120)
        if r.returncode != 0:
            raise WorkspaceError(f"export failed: {r.stderr[:500]}")
        return self.sandbox.filesystem.read_bytes("/tmp/p2c-ws.tgz")

    def import_tarball(self, data: bytes, prefix: str = "snapshot") -> None:
        self.sandbox.filesystem.write_bytes(data, "/tmp/p2c-seed.tgz")
        r = self.exec(f"tar xzf /tmp/p2c-seed.tgz -C {shlex.quote(self.root)} --strip-components=1 {shlex.quote(prefix)}", timeout_s=120)
        if r.returncode != 0:
            raise WorkspaceError(f"import failed: {r.stderr[:500]}")

    def write_assignment(self, spec_md: str, interface_md: str, public_tests: dict[str, str]) -> None:
        self.write_file(".assignment/spec.md", spec_md)
        self.write_file(".assignment/interface.md", interface_md)
        for name, body in public_tests.items():
            self.write_file(f".assignment/tests/public/{name}", body)


def create_builder_sandbox(config, spec_md: str, wall_clock_s: int):
    import modal

    from paper2code.sandbox.modal_app import builder_image

    domains = sorted(set(config.sandbox_allowed_domains) | set(extract_domains(spec_md)))
    lifetime = wall_clock_s + 900
    return modal.Sandbox.create(
        "sleep", str(lifetime),
        app=modal.App.lookup(config.modal_app_name, create_if_missing=True),
        image=builder_image, timeout=lifetime, cpu=config.sandbox_cpu, memory=config.sandbox_memory_mb,
        workdir=ROOT, outbound_domain_allowlist=domains,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_modal_workspace.py -q`
Expected: 6 PASS. If the `tar --transform` form is rejected by the local tar (Git Bash ships GNU tar, which supports it), note the tar version in the ledger; the real sandbox is Debian GNU tar.

- [ ] **Step 5: Commit**

```bash
git add src/paper2code/sandbox/modal_workspace.py tests/test_modal_workspace.py
git commit -m "ModalWorkspace over a sandbox: confined files, exec, export/import, assignment copy

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Wiring the build and inspect stages to Modal

**Files:**
- Modify: `src/paper2code/agents/builder/base.py` (`BuildContext.workspace_api`)
- Modify: `src/paper2code/agents/builder/agent.py` (use `ctx.workspace_api` when set)
- Modify: `src/paper2code/sandbox/factory.py` (`make_runner` Modal branch)
- Create: `src/paper2code/sandbox/modal_session.py`
- Modify: `src/paper2code/manager/stages/build.py` (Modal path)
- Modify: `src/paper2code/manager/record.py`? No: `RunError` reason `sandbox_failed` is a string, no change.
- Modify: `src/paper2code/cli.py` (`--no-gpu` no longer required; without it, Modal)
- Test: `tests/test_modal_session.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `ModalWorkspace`, `create_builder_sandbox` (Task 3), `ModalTestRunner` (Task 2), `tar_directory` (Task 1), `BuildSession`, `AgentBuilder`.
- Produces:
  - `BuildContext.workspace_api: Any = None`; `AgentBuilder.build` uses `ctx.workspace_api` if set, else `self.workspace_factory(ctx.workspace)`.
  - `modal_session.py`: `SandboxFailed(Exception)`; `@contextmanager def modal_build_session(record, ctx, sandbox_factory=create_builder_sandbox, workspace_cls=ModalWorkspace, runner_cls=ModalTestRunner)` yielding `(workspace, runner)`: creates the sandbox (failure → `SandboxFailed`), wraps it in `workspace_cls`, seeds it from `run_dir/workspace` if that directory has files (`import_tarball(tar_directory(...))`), writes the assignment copy, builds `runner_cls(timeout_s=config.run_tests_timeout_s, max_payload_bytes=config.payload_max_mb * 2**20, snapshot_source=workspace.export_tarball, app_name=config.modal_app_name)`, logs `{"event": "sandbox", "action": "created", "id": ...}`; on exit (normal or exception) exports `/work` to `run_dir/workspace` (replacing it; export errors are logged, not raised), terminates the sandbox, logs `{"event": "sandbox", "action": "terminated"}`. Any exception from inside the sandbox API during the session that is not `BuildFinished`/`RateLimited` is wrapped as `SandboxFailed` by the caller.
  - `make_runner(ctx)`: `no_gpu` → `LocalTestRunner`; else `ModalTestRunner(timeout_s=cfg.run_tests_timeout_s, max_payload_bytes=cfg.payload_max_mb * 2**20, app_name=cfg.modal_app_name)` (local-tar snapshot; used by inspect, and by the build stage with the stub builder).
  - `run_with_builder`: when `ctx.no_gpu` is False and the builder is the agent, runs inside `modal_build_session`, passing `workspace_api` and the session's runner into `BuildSession`; a `SandboxFailed` (or `RemoteError`) ends the run as `error` with reason `sandbox_failed`, logs `session_end` with that reason, and keeps everything exported so far. With the stub builder (no sandbox needed) the local workspace is used and only the runner is remote.
  - CLI: `_context` no longer errors without `--no-gpu`; `RunContext(no_gpu=args.no_gpu)`. `--builder stub` still requires `--reference`.

- [ ] **Step 1: Write the failing tests**

`tests/test_modal_session.py`:

```python
import json
from datetime import date

import pytest

from paper2code.agents.builder.base import BuildContext
from paper2code.config import Config
from paper2code.manager.buildlog import BuildLog
from paper2code.manager.graph import RunContext
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import RunRecord
from paper2code.manager.stages import build as build_stage
from paper2code.sandbox.modal_runner import ModalTestRunner
from paper2code.sandbox.modal_session import SandboxFailed, modal_build_session
from paper2code.sandbox.modal_workspace import ModalWorkspace
from paper2code.sandbox.remote_tests import execute_tests
from tests.test_build_stage import _seed_run
from tests.test_modal_workspace import FakeSandbox


def _ctx(tmp_path, **cfg):
    return RunContext(config=Config(runs_root=tmp_path, run_tests_timeout_s=120, **cfg), no_gpu=False, builder="agent")


def _factory(tmp_path, holder, fail=False):
    def make(config, spec_md, wall_clock_s):
        if fail:
            raise RuntimeError("no capacity")
        sb = FakeSandbox(tmp_path / "sb")
        holder["sandbox"] = sb
        return sb

    return make


def _runner_cls(**_):
    """ModalTestRunner with the remote replaced by the local executor; snapshot_source is wired by the session."""
    def make(timeout_s, max_payload_bytes, snapshot_source=None, app_name="paper2code"):
        return ModalTestRunner(timeout_s=timeout_s, max_payload_bytes=max_payload_bytes, remote=execute_tests, snapshot_source=snapshot_source)
    return make


def _workspace_cls(tmp_path):
    def make(sandbox):
        import os

        return ModalWorkspace(sandbox, root=str(tmp_path / "sb" / "work").replace(os.sep, "/"))
    return make


def test_session_creates_seeds_exports_and_terminates(tmp_path, canary_dir):
    rec = _seed_run(tmp_path, canary_dir)
    (rec.run_dir / "workspace").mkdir(exist_ok=True)
    (rec.run_dir / "workspace" / "earlier.py").write_text("# from a previous attempt\n", encoding="utf-8")
    holder = {}
    log = BuildLog(rec.run_dir / "build.log")
    with modal_build_session(rec, _ctx(tmp_path), log, sandbox_factory=_factory(tmp_path, holder), workspace_cls=_workspace_cls(tmp_path), runner_cls=_runner_cls()) as (ws, runner):
        assert ws.read_file("earlier.py") == "# from a previous attempt\n"  # resume seed
        assert ws.read_file(".assignment/spec.md").startswith("# Scope")
        src = (canary_dir / "reference" / "canary_method.py").read_text(encoding="utf-8")
        ws.write_file("canary_method.py", src)
        r = runner.run(rec.run_dir / "workspace", rec.run_dir / "scope" / "tests" / "public")
        assert r.all_passed
    assert holder["sandbox"].terminated
    exported = rec.run_dir / "workspace" / "canary_method.py"
    assert exported.exists() and "def ema" in exported.read_text(encoding="utf-8")
    assert not (rec.run_dir / "workspace" / ".assignment").exists()
    events = [e for e in log.read() if e["event"] == "sandbox"]
    assert [e["action"] for e in events] == ["created", "terminated"]


def test_resume_seeds_sandbox_from_exported_workspace(tmp_path, canary_dir):
    rec = _seed_run(tmp_path, canary_dir)
    holder = {}
    log = BuildLog(rec.run_dir / "build.log")
    with modal_build_session(rec, _ctx(tmp_path), log, sandbox_factory=_factory(tmp_path, holder), workspace_cls=_workspace_cls(tmp_path), runner_cls=_runner_cls()) as (ws, runner):
        ws.write_file("canary_method.py", "# attempt one\n")
    import shutil

    shutil.rmtree(tmp_path / "sb")  # the old sandbox is gone; a new one must be seeded from the export
    with modal_build_session(rec, _ctx(tmp_path), log, sandbox_factory=_factory(tmp_path, holder), workspace_cls=_workspace_cls(tmp_path), runner_cls=_runner_cls()) as (ws, runner):
        assert ws.read_file("canary_method.py") == "# attempt one\n"


def test_sandbox_failure_is_an_error_outcome_and_exports_what_it_can(tmp_path, canary_dir, monkeypatch):
    import paper2code.sandbox.modal_session as ms

    rec = _seed_run(tmp_path, canary_dir)
    holder = {}
    monkeypatch.setattr(ms, "create_builder_sandbox", _factory(tmp_path, holder))
    monkeypatch.setattr(ms, "ModalWorkspace", _workspace_cls(tmp_path))
    monkeypatch.setattr(ms, "ModalTestRunner", _runner_cls())

    class DiesMidway:
        def build(self, ctx):
            ctx.workspace_api.write_file("canary_method.py", "# half done\n")
            raise ConnectionError("sandbox connection lost")

    build_stage.run_with_builder(RunRecord.load(rec.run_dir), _ctx(tmp_path), DiesMidway())
    final = RunRecord.load(rec.run_dir)
    assert final.outcome is Outcome.ERROR and final.error.reason == "sandbox_failed" and "connection lost" in final.error.message
    assert (rec.run_dir / "workspace" / "canary_method.py").read_text(encoding="utf-8") == "# half done\n"
    assert holder["sandbox"].terminated
    rows = BuildLog(rec.run_dir / "build.log").read()
    assert rows[-1]["event"] == "session_end" and rows[-1]["reason"] == "sandbox_failed"


def test_sandbox_creation_failure_is_sandbox_failed(tmp_path, canary_dir, monkeypatch):
    import paper2code.sandbox.modal_session as ms

    rec = _seed_run(tmp_path, canary_dir)
    monkeypatch.setattr(ms, "create_builder_sandbox", _factory(tmp_path, {}, fail=True))

    class Never:
        def build(self, ctx):
            raise AssertionError("must not run")

    build_stage.run_with_builder(RunRecord.load(rec.run_dir), _ctx(tmp_path), Never())
    final = RunRecord.load(rec.run_dir)
    assert final.outcome is Outcome.ERROR and final.error.reason == "sandbox_failed" and "no capacity" in final.error.message


def test_agent_builder_uses_workspace_api_when_given(tmp_path, canary_dir):
    from paper2code.agents.builder.agent import AgentBuilder
    from paper2code.manager.stages.build import BuildSession
    from paper2code.sandbox.runner import LocalTestRunner
    from paper2code.sandbox.workspace import LocalWorkspace

    rec = _seed_run(tmp_path, canary_dir)
    log = BuildLog(rec.run_dir / "build.log")
    session = BuildSession(rec, LocalTestRunner(60), log)
    other = LocalWorkspace(tmp_path / "elsewhere")
    ctx = BuildContext(workspace=rec.run_dir / "workspace", spec_path=rec.run_dir / "scope" / "spec.md",
                       interface_path=rec.run_dir / "scope" / "interface.md", public_tests=rec.run_dir / "scope" / "tests" / "public",
                       run_tests=session.run_tests, give_up=session.give_up, session=session, workspace_api=other)
    holder = {}
    builder = AgentBuilder(Config(), client_factory=None)
    builder.on_tools_ready = lambda tools: holder.__setitem__("ws", tools.workspace)
    builder.client_factory = __import__("tests.test_agent_builder", fromlist=["_factory"])._factory(__import__("tests.test_agent_builder", fromlist=["FakeClient"]).FakeClient([]))
    builder.build(ctx)
    assert holder["ws"] is other
```

Append to `tests/test_cli.py`:

```python
def test_without_no_gpu_the_context_selects_modal(tmp_path, canary_dir, capsys, monkeypatch):
    import paper2code.agents.builder.factory as factory

    seen = {}

    class Probe:
        def build(self, ctx):
            seen["runner"] = type(ctx.session.runner).__name__

    monkeypatch.setattr(factory, "make_builder", lambda ctx: Probe())
    run_dir = _init(tmp_path, canary_dir, capsys)
    rc = main(["build", "--run", str(run_dir), "--builder", "stub", "--reference", str(canary_dir / "reference"), "--config", str(tmp_path / "absent.yaml")])
    assert rc == 0 and seen["runner"] == "ModalTestRunner"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_modal_session.py tests/test_cli.py -q`
Expected: `ModuleNotFoundError: No module named 'paper2code.sandbox.modal_session'`; the CLI test exits 2 on the missing `--no-gpu`.

- [ ] **Step 3: Implement**

`src/paper2code/agents/builder/base.py`: add `workspace_api: Any = None  # a Workspace to use instead of a LocalWorkspace over `workspace` (Modal mode)` as the last field of `BuildContext`.

`src/paper2code/agents/builder/agent.py`, in `build`: replace `workspace = self.workspace_factory(ctx.workspace)` with `workspace = ctx.workspace_api or self.workspace_factory(ctx.workspace)`.

`src/paper2code/sandbox/factory.py`:

```python
from __future__ import annotations

from paper2code.manager.graph import RunContext
from paper2code.sandbox.runner import LocalTestRunner, TestRunner


def make_runner(ctx: RunContext) -> TestRunner:
    if ctx.no_gpu:
        return LocalTestRunner(timeout_s=ctx.config.run_tests_timeout_s)
    from paper2code.sandbox.modal_runner import ModalTestRunner

    cfg = ctx.config
    return ModalTestRunner(timeout_s=cfg.run_tests_timeout_s, max_payload_bytes=cfg.payload_max_mb * 2**20, app_name=cfg.modal_app_name)
```

`src/paper2code/sandbox/modal_session.py`:

```python
"""The builder's sandbox lifetime: create, seed from any earlier workspace, hand the agent a
Workspace and the manager a runner that snapshots from it, and on any exit export the workspace
back into the run record and destroy the sandbox."""
from __future__ import annotations

import shutil
import tarfile
import io
from contextlib import contextmanager
from pathlib import Path

from paper2code.manager.buildlog import BuildLog
from paper2code.sandbox.modal_runner import ModalTestRunner
from paper2code.sandbox.modal_workspace import ModalWorkspace, create_builder_sandbox
from paper2code.sandbox.remote_tests import tar_directory


class SandboxFailed(Exception):
    pass


def _has_files(path: Path) -> bool:
    return path.exists() and any(p.is_file() for p in path.rglob("*"))


def _export_to(run_dir: Path, data: bytes) -> None:
    dest = run_dir / "workspace"
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for m in tf.getmembers():
            if not m.isfile() or not m.name.startswith("snapshot/"):
                continue
            rel = Path(*Path(m.name).parts[1:])
            target = (dest / rel).resolve()
            if dest.resolve() not in target.parents:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(tf.extractfile(m).read())


@contextmanager
def modal_build_session(record, ctx, log: BuildLog, sandbox_factory=None, workspace_cls=None, runner_cls=None):
    sandbox_factory = sandbox_factory or create_builder_sandbox
    workspace_cls = workspace_cls or ModalWorkspace
    runner_cls = runner_cls or ModalTestRunner
    cfg = ctx.config
    run_dir = record.run_dir
    scope = run_dir / "scope"
    spec_md = (scope / "spec.md").read_text(encoding="utf-8")
    try:
        sandbox = sandbox_factory(cfg, spec_md, record.caps.wall_clock_s)
    except Exception as exc:
        raise SandboxFailed(f"could not create the builder sandbox: {type(exc).__name__}: {exc}") from exc
    log.append({"event": "sandbox", "action": "created", "id": getattr(sandbox, "object_id", None)})
    workspace = workspace_cls(sandbox)
    try:
        if _has_files(run_dir / "workspace"):
            workspace.import_tarball(tar_directory(run_dir / "workspace", "snapshot"))
            log.append({"event": "sandbox", "action": "seeded_from_export"})
        public = {p.name: p.read_text(encoding="utf-8") for p in sorted((scope / "tests" / "public").glob("test_*.py"))}
        workspace.write_assignment(spec_md, (scope / "interface.md").read_text(encoding="utf-8"), public)
        runner = runner_cls(
            timeout_s=cfg.run_tests_timeout_s, max_payload_bytes=cfg.payload_max_mb * 2**20,
            snapshot_source=workspace.export_tarball, app_name=cfg.modal_app_name,
        )
        yield workspace, runner
    finally:
        try:
            _export_to(run_dir, workspace.export_tarball())
            log.append({"event": "sandbox", "action": "exported"})
        except Exception as exc:
            log.append({"event": "sandbox", "action": "export_failed", "message": f"{type(exc).__name__}: {exc}"})
        try:
            sandbox.terminate()
        finally:
            log.append({"event": "sandbox", "action": "terminated"})
```

`src/paper2code/manager/stages/build.py`, replace `run_with_builder`:

```python
def run_with_builder(record: RunRecord, ctx: RunContext, builder: Builder) -> None:
    from paper2code.agents.builder.agent import RateLimited
    from paper2code.sandbox.factory import make_runner

    run_dir = record.run_dir
    scope = run_dir / "scope"
    workspace = run_dir / "workspace"
    workspace.mkdir(exist_ok=True)
    log = BuildLog(run_dir / "build.log")
    use_sandbox = not ctx.no_gpu and ctx.builder == "agent"

    def _run(session: BuildSession, workspace_api) -> None:
        build_ctx = BuildContext(
            workspace=workspace, spec_path=scope / "spec.md", interface_path=scope / "interface.md",
            public_tests=scope / "tests" / "public", run_tests=session.run_tests, give_up=session.give_up,
            session=session, workspace_api=workspace_api,
        )
        builder.build(build_ctx)

    log.append({"event": "session_start", "builder": ctx.builder, "test_runs_used": record.counters.test_runs_used,
                "sandbox": use_sandbox})
    session: BuildSession | None = None
    try:
        if use_sandbox:
            from paper2code.sandbox.modal_session import modal_build_session

            with modal_build_session(record, ctx, log) as (workspace_api, runner):
                session = BuildSession(record, runner, log, gpu_usd_per_hour=ctx.config.gpu_usd_per_hour)
                try:
                    _run(session, workspace_api)
                except (BuildFinished, RateLimited):
                    raise
                except Exception as exc:
                    raise _SandboxFailure(exc) from exc
        else:
            session = BuildSession(record, make_runner(ctx), log, gpu_usd_per_hour=ctx.config.gpu_usd_per_hour)
            _run(session, None)
    except BuildFinished:
        pass
    except RateLimited as exc:
        _end_as_error(record, log, session, "rate_limited", str(exc))
        return
    except _SandboxFailure as exc:
        _end_as_error(record, log, session, "sandbox_failed", f"{type(exc.cause).__name__}: {exc.cause}")
        return
    except Exception as exc:
        from paper2code.sandbox.modal_session import SandboxFailed

        if isinstance(exc, SandboxFailed):
            _end_as_error(record, log, session, "sandbox_failed", str(exc))
            return
        log.append({"event": "error", "message": f"{type(exc).__name__}: {exc}"})
        raise
    if not session.finished:
        session.finish(BUILDER_RETURNED)
    log.append({"event": "session_end", "reason": session.finish_reason, "elapsed_s": round(session.elapsed_s, 1)})
    outcome = outcome_for(session.finish_reason)
    if outcome is not None:
        record.outcome = outcome
    record.save()


class _SandboxFailure(Exception):
    def __init__(self, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.cause = cause


def _end_as_error(record: RunRecord, log: BuildLog, session: BuildSession | None, reason: str, message: str) -> None:
    elapsed = round(session.elapsed_s, 1) if session else 0.0
    log.append({"event": "session_end", "reason": reason, "elapsed_s": elapsed})
    record.outcome = Outcome.ERROR
    record.error = RunError(stage="build", reason=reason, message=message)
    record.save()
```

(`RemoteError` from the runner propagates out of `run_tests` inside the builder as a plain exception and therefore becomes `sandbox_failed` in Modal mode; in local mode it cannot occur.)

`src/paper2code/cli.py`: in `_context`, delete the two lines that `parser.error(...)` when `--no-gpu` is absent, and pass `no_gpu=args.no_gpu` to `RunContext`. Update the `--no-gpu` help text to "run tests and the agent's workspace locally instead of on Modal".

- [ ] **Step 4: Run the tests to verify they pass, then the whole suite**

Run: `pytest tests/test_modal_session.py tests/test_cli.py tests/test_build_stage.py tests/test_agent_builder.py -q` then `pytest -q`
Expected: all pass. `test_run_requires_no_gpu_flag` (step 2) and `test_run_to_build_still_requires_no_gpu` (step 2) now contradict the new behaviour: delete both and note the ruling in the ledger (the flag is optional by design now).

- [ ] **Step 5: Commit**

```bash
git add src/paper2code/agents/builder/base.py src/paper2code/agents/builder/agent.py src/paper2code/sandbox/factory.py src/paper2code/sandbox/modal_session.py src/paper2code/manager/stages/build.py src/paper2code/cli.py tests/test_modal_session.py tests/test_cli.py
git commit -m "Build and inspect stages on Modal: sandbox session with seed/export/terminate, remote runner, optional --no-gpu

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Deploy, live runs, docs, journal

**Files:**
- Create: `tests/test_live_modal.py`
- Modify: `README.md`, `decisions.md`, `CLAUDE.md` (one line on deploying)

**Interfaces:** consumes everything above through the CLI.

- [ ] **Step 1: Deploy the app**

Run from the repo root: `python -m modal deploy src/paper2code/sandbox/modal_app.py`
Expected: both images build (the tests image pulls CUDA torch; several minutes the first time), then `Created function run_tests_remote` and a dashboard URL. Record the build time and any pip resolution trouble in the ledger. If `add_local_python_source("paper2code")` cannot find the package because it is installed in editable mode under `src/`, pass the source directory explicitly with `.add_local_dir("src/paper2code", remote_path="/root/paper2code")` and note it.

- [ ] **Step 2: Write the opt-in live tests**

`tests/test_live_modal.py`:

```python
"""Live Modal tests. Opt in with PAPER2CODE_LIVE_MODAL=1 after `modal deploy`. Spends a little GPU time."""
import os

import pytest

from paper2code.cli import main
from paper2code.manager.buildlog import BuildLog
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import RunRecord
from paper2code.sandbox.modal_runner import ModalTestRunner

pytestmark = pytest.mark.skipif(os.environ.get("PAPER2CODE_LIVE_MODAL") != "1", reason="set PAPER2CODE_LIVE_MODAL=1 to use Modal")


def test_remote_runner_runs_the_canary_on_the_gpu_function(canary_dir):
    r = ModalTestRunner(timeout_s=300, max_payload_bytes=50 * 2**20).run(canary_dir / "reference", canary_dir / "scope" / "tests" / "hidden")
    assert r.all_passed and len(r.passed) == 5 and r.gpu_seconds > 0
    print("remote hidden suite: passed", len(r.passed), "| gpu_seconds", round(r.gpu_seconds, 1), "| duration", round(r.duration_s, 1))


def test_stub_builder_with_remote_tests_reaches_completed(tmp_path, canary_dir, capsys):
    assert main(["init-run", "--scope", str(canary_dir / "scope"), "--paper-id", "canary-0001", "--title", "EMA denoising canary", "--runs-root", str(tmp_path), "--date", "2026-10-07"]) == 0
    run_dir = tmp_path / "2026-10-07"
    assert main(["run", "--run", str(run_dir), "--builder", "stub", "--reference", str(canary_dir / "reference"), "--llm", "fake"]) == 0
    rec = RunRecord.load(run_dir)
    assert rec.outcome is Outcome.COMPLETED and rec.budget.gpu_seconds > 0
    print("stub+remote: outcome", rec.outcome, "| gpu_seconds", round(rec.budget.gpu_seconds, 1))


def test_agent_in_sandbox_builds_the_canary(tmp_path, canary_dir, capsys):
    """The full cloud path: agent in a Modal sandbox, tests on the GPU function, hidden tests never in the sandbox."""
    assert main(["init-run", "--scope", str(canary_dir / "scope"), "--paper-id", "canary-0001", "--title", "EMA denoising canary", "--runs-root", str(tmp_path), "--date", "2026-10-07"]) == 0
    run_dir = tmp_path / "2026-10-07"
    assert main(["run", "--run", str(run_dir), "--builder", "agent", "--llm", "fake"]) == 0
    rec = RunRecord.load(run_dir)
    events = [e for e in BuildLog(run_dir / "build.log").read()]
    assert [e["action"] for e in events if e["event"] == "sandbox"][0] == "created"
    assert [e["action"] for e in events if e["event"] == "sandbox"][-1] == "terminated"
    assert rec.outcome in (Outcome.COMPLETED, Outcome.COMPLETED_SUSPICIOUS, Outcome.HIDDEN_FAILED, Outcome.INCOMPLETE_STUCK, Outcome.INCOMPLETE_BUDGET), rec
    print("agent+sandbox: outcome", rec.outcome, "| test runs", rec.counters.test_runs_used, "| gpu_seconds", round(rec.budget.gpu_seconds, 1), "| tokens", rec.budget.spent_tokens)
```

- [ ] **Step 3: Run the live tests one at a time**

Run: `PAPER2CODE_LIVE_MODAL=1 pytest tests/test_live_modal.py::test_remote_runner_runs_the_canary_on_the_gpu_function -q -s`, then the stub+remote test, then the agent+sandbox test. Expected: all pass; the first call includes a cold start (tens of seconds), later calls are faster. Record in the ledger for each: outcome, GPU seconds, wall time, and for the agent run the sandbox id and the sequence of sandbox actions. A `RemoteError` naming `NotFoundError` means the app was not deployed. A payload refusal means the CPU torch install landed in `/work`; the builder image must preinstall it.

- [ ] **Step 4: README, CLAUDE.md, journal**

README Status becomes:

```markdown
Build step 4b of 6: the builder runs in a Modal sandbox (CPU only, no secrets, outbound network
limited to the package index and hosts named in the spec) and every test run executes in a Modal
GPU function that receives a snapshot of the workspace plus the tests; the hidden tests are never
present where the agent runs. `--no-gpu` keeps everything local. Step 5 is the inspector.
```

Add a "Modal" section:

````markdown
## Modal

```bash
modal setup                                             # once: login, writes ~/.modal.toml
python -m modal deploy src/paper2code/sandbox/modal_app.py   # once per change to the images or GPU type
PAPER2CODE_GPU=L4 python -m modal deploy ...             # pick a different GPU at deploy time
PAPER2CODE_LIVE_MODAL=1 pytest tests/test_live_modal.py -q -s   # opt-in live checks
```

Set a spend limit in the Modal dashboard; the per-run GPU budget in `config.yaml` is a soft cap.
````

Replace the local-mode caveat sentence with: "In local mode (`--no-gpu`) the agent's shell runs on this machine with credentials scrubbed; without the flag it runs in a Modal sandbox and cannot reach this machine at all."

CLAUDE.md "Money and secrets": add one line: "Modal: the token is in `~/.modal.toml`; `python -m modal deploy src/paper2code/sandbox/modal_app.py` after changing images; GPU time is billed per `run_tests` call."

Append the step 4b journal entry to `decisions.md` from the ledger.

- [ ] **Step 5: Commit**

```bash
git add tests/test_live_modal.py README.md CLAUDE.md decisions.md
git commit -m "Live Modal tests (opt-in), README/CLAUDE.md Modal section, journal for step 4b

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review notes

**Spec coverage.** 9.1 sandbox environment: Task 3 (CPU sandbox, fresh image, `/work` read-write, assignment copy, hidden tests absent, domain allowlist from config plus the spec's URLs, destroyed on exit via Task 4's context manager). 9.2 `run_tests` in a separate GPU function out of the builder's reach, GPU seconds recorded: Tasks 1, 2, 4. 10.1 `run_hidden_tests` on the same function: Task 4's `make_runner`. 12 secrets and spend limit: Task 3 (no secrets in the sandbox), Task 5 docs. 13 `sandbox/modal_app.py`, `sandbox/tools.py` equivalents: Tasks 2, 3 (the spec's `tools.py` is split into `remote_tests.py`, `modal_runner.py`, `modal_workspace.py`, `modal_session.py`; recorded). 14 `--no-gpu` swaps Modal for local with the same interface: Task 4. 16 step 4 complete with 4a: Task 5's agent-in-sandbox live test.

**Deviations recorded.** `sandbox_failed` outcome reason is from the spec's list. New build-log `sandbox` events. The assignment copy at `/work/.assignment/` is a convenience beyond "mounted read-only" and is excluded from exports and snapshots. The GPU type is fixed at deploy time. The payload size cap is new. The `inspect` stage runs no sandbox because it executes no agent code in this plan.

**Type consistency checked.** `ModalTestRunner(timeout_s, max_payload_bytes, remote, snapshot_source, app_name)` matches Task 4's `runner_cls(...)` call. `ModalWorkspace(sandbox, root)` and `export_tarball()/import_tarball()/write_assignment()` match Task 4. `execute_tests(payload, timeout_s) -> dict` is both the Modal function body and the test double. `BuildContext.workspace_api` is read by `AgentBuilder.build`.

**Review Focus pinned.** 1 → Task 4 `test_sandbox_failure_is_an_error_outcome_and_exports_what_it_can`; 2 → Task 1 `test_payload_excludes_junk_and_caps_size`; 3 → Task 2 `test_runner_maps_function_timeout_to_timed_out_result`; 4 → Task 3 `test_modal_workspace_confines_paths`; 5 → Task 4 `test_resume_seeds_sandbox_from_exported_workspace`.
