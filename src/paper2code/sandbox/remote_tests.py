"""The test run as a pure function of (payload bytes, timeout): unpack, run pytest the way the local
runner does, return a dict. The Modal GPU function in modal_app.py is a thin wrapper around this,
so the real logic is unit-tested here without Modal.

A payload is one gzip tarball with two top-level directories: `snapshot/` (the workspace being
tested) and `tests/` (the suite to run against it)."""
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
    cross_check,
    parse_junit,
    parse_junit_details,
    parse_junit_errors,
    run_killable,
    scrubbed_environment,
)

PAYLOAD_EXCLUDE = (".venv", "venv", "__pycache__", ".git", ".pytest_cache", "*.pyc")
MAX_OUTPUT_CHARS = 20_000


class PayloadTooLarge(Exception):
    pass


def _excluded(rel: Path) -> bool:
    return any(fnmatch.fnmatch(part, pat) for part in rel.parts for pat in PAYLOAD_EXCLUDE)


def tar_directory(root: Path, prefix: str) -> bytes:
    """Gzip tarball of the files under `root`, stored under `prefix/`, junk excluded."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root)
            if not p.is_file() or _excluded(rel):
                continue
            tf.add(p, arcname=f"{prefix}/{rel.as_posix()}", recursive=False)
    return buf.getvalue()


def build_payload(snapshot_tar: bytes, tests_dir: Path, max_bytes: int) -> bytes:
    """Combine a `snapshot/` tarball with `tests_dir` (stored under `tests/`) into one payload."""
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
        raise PayloadTooLarge(
            f"test payload is {len(data)} bytes, over the {max_bytes} byte limit; is a dataset or a venv in the workspace?"
        )
    return data


def _safe_extract(tf: tarfile.TarFile, dest: Path) -> None:
    base = dest.resolve()
    for m in tf.getmembers():
        target = (dest / m.name).resolve()
        if target != base and base not in target.parents:
            raise ValueError(f"tar member escapes destination: {m.name}")
    tf.extractall(dest, filter="data")


def execute_tests(payload: bytes, timeout_s: int) -> dict:
    """Run `tests/` against `snapshot/` from the payload. Returns a JSON-friendly dict."""
    with tempfile.TemporaryDirectory(prefix="p2c-remote-", ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tf:
            _safe_extract(tf, root)
        snapshot = root / "snapshot"
        tests = root / "tests"
        snapshot.mkdir(exist_ok=True)
        tests.mkdir(exist_ok=True)
        report = root / "report.xml"
        outcomes = root / "outcomes.json"
        bootstrap = root / "bootstrap.py"
        bootstrap.write_text(_BOOTSTRAP, encoding="utf-8")
        digest = tree_digest(hash_tree(snapshot))
        env = scrubbed_environment()
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        cmd = [
            sys.executable, "-I", "-B", str(bootstrap), str(snapshot), str(outcomes), str(tests),
            "-q", "-p", "no:cacheprovider", f"--junitxml={report}", "--rootdir", str(tests),
        ]
        start = time.monotonic()
        returncode, stdout, stderr, timed_out = run_killable(cmd, cwd=root, env=env, timeout_s=timeout_s)
        duration = time.monotonic() - start
        if timed_out or not report.exists():
            passed, failed, errored, skipped, messages, altered = (), (), (), (), {}, ()
        else:
            passed, failed = parse_junit(report)
            errored = parse_junit_errors(report)
            skipped, messages = parse_junit_details(report)
            passed, failed, errored, skipped, messages, altered = cross_check(outcomes, passed, failed, errored, skipped, messages)
        return {
            "passed": list(passed), "failed": list(failed), "errored": list(errored), "skipped": list(skipped),
            "messages": dict(messages), "returncode": returncode, "timed_out": timed_out,
            "duration_s": round(duration, 3), "output": (stdout + stderr)[-MAX_OUTPUT_CHARS:],
            "workspace_sha256": digest, "altered": list(altered),
        }


def result_from_dict(d: dict, gpu_seconds: float) -> TestRunResult:
    return TestRunResult(
        tuple(d["passed"]), tuple(d["failed"]), int(d["returncode"]), bool(d["timed_out"]), float(d["duration_s"]),
        float(gpu_seconds), str(d.get("output", "")), str(d.get("workspace_sha256", "")),
        tuple(d.get("errored", ())), tuple(d.get("skipped", ())), dict(d.get("messages", {})), tuple(d.get("altered", ())),
    )
