# paper2code Step 3: Scoper, Stub Check, Feasibility, Freeze — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build step 3 of the spec's build order: the scope stage turns the top shortlisted paper into a frozen assignment (spec, interface, public tests, hidden tests), rejecting and falling through to the next paper when the assignment is trivial, uncollectable, under-seeded, or over budget, so that `paper2code run --until scope` works end to end and the step 1 build/inspect path then carries a scoped run to `completed`.

**Architecture:** The scoper is one structured-output call to the strong model that returns a `ScopeDraft`: spec text, a structured interface (module name, function and class signatures), and test files as path/content pairs. The manager, not the model, writes the files, so the model structurally cannot touch anything outside `scope/` (spec 8.1). The manager renders `interface.md` and a stub module from the structured interface, runs every public and hidden test against the stubs with the step 1 local runner, deletes tests that pass on stubs, rejects scopes whose tests do not even import, whose claim test is trivial or under-seeded, or whose cost estimate exceeds the budget, and freezes survivors with the step 1 manifest plus anchor. The stage iterates the shortlist, records every attempt in `scope_attempts.jsonl`, and is safe to re-run after a crash.

**Tech Stack:** Python 3.11+, `pydantic` schemas for structured output, the existing `LocalTestRunner`, `ast` for surgical test removal, `pytest`. No Modal, no Agent SDK in this step.

**Spec:** `docs/superpowers/specs/2026-09-30-paper2code-design.md` (sections 4, 4.1, 8, 14, 15)

**Facts established before planning (2026-10-07):**
- Local environment has numpy 2.5, torch 2.14 (CPU), scipy 1.18, scikit-learn 1.9; no pandas. The stub check imports test files locally, so the scoper is told which packages it may use.
- pytest reports a test file that fails to import as a `<testcase classname="" name="test_bad"><error message="collection failure">` entry and then aborts the session; nothing else runs. So "every test fails on stubs" must require zero collection errors, otherwise a broken test file would pass the stub check vacuously and the builder could never succeed.
- A test raising `NotImplementedError` from a stub is reported as `<failure>`, not `<error>`; the distinction is what the stub check uses.
- The step 2 live run produced a top pick (2610.07324, testability 5, est 0.50 USD), so the scoper's first real input exists. Scoper cost per attempt on gpt-5.5 at ~25k input and ~6k output tokens is about 0.30 USD; three attempts bound a day at about 1 USD.

## Global Constraints

- Python `>=3.11`; package under `src/paper2code/`; relative layout per spec section 13 (`agents/scoper/` holds prompts and the structured-output schema).
- Scoper inputs: full paper text, its scorecard, the budget. It may only produce files under `scope/`; no shell, no network, no code execution, enforced by what it is given, not by prompt (spec 8.1). In this plan the model returns file contents and the manager writes them.
- Scoper outputs exactly (spec 8.2): `spec.md`, `interface.md`, `tests/public/` with unit tests plus one claim test that runs the scaled experiment across the specified seeds, `tests/hidden/` with claim-test variants (different seed, data slice, perturbed hyperparameter).
- Stub check (spec 8.3): workspace of empty stubs matching `interface.md` (functions raise `NotImplementedError`); every public and hidden test must fail; a passing test is deleted and logged as `trivial_test_removed` with its name; a trivial public claim test rejects the scope; fewer than `min_seeds` seeds in the claim test rejects with `insufficient_seeds`.
- Feasibility (spec 8.4): the scoper's own numbers against the budget; over budget rejects with `over_budget`.
- Freeze (spec 8.5): surviving tests hashed into `manifest.json`; from then on no agent session can write `scope/`; the step 1 anchor (`scope_manifest_sha256` in `run.json`) is set at freeze.
- Shortlist fall-through (spec 8): on rejection, move to the next shortlisted paper; exhausted shortlist is outcome `scope_rejected`.
- Local mode (spec 14): `paper2code scope --arxiv-id 2509.12345`.
- Canary (spec 15): a planted trivial test must be removed by the stub check.
- Resume rule from step 1 and the step 2 lesson: a stage re-runs after a crash and must not duplicate records or re-bill.
- Commit after every task with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`; run tests with `TEMP`, `TMP`, `TMPDIR` pointed at the session scratchpad.

## Review Focus

Failure modes the spec implies but does not spell out. Each line's test is pinned to the task named.

1. **A test file that does not import against the stubs** (wrong name, missing package, syntax error) fails on stubs for the wrong reason; accepting it would make the assignment unpassable. The scope must be rejected with reason `tests_do_not_collect`. Pinned to Task 4 (`test_stub_check_rejects_uncollectable_tests`).
2. **The model names a test file with a directory, an absolute path, or a non-`test_*.py` name.** Nothing may be written outside `scope/tests/<public|hidden>/`; the draft is rejected as `malformed_scope` before any file is written. Pinned to Task 2 (`test_validate_draft_rejects_unsafe_paths`).
3. **A crash in the middle of scoping** (after files were written, before the freeze) leaves a partial `scope/`. On re-run that directory is wiped before the attempt and candidates already attempted are not re-billed. Pinned to Task 5 (`test_scope_resume_wipes_partial_dir_and_skips_attempted`).
4. **Every shortlisted paper is rejected.** Outcome `scope_rejected`, and `scope_attempts.jsonl` says why for each. Pinned to Task 5 (`test_scope_rejected_when_shortlist_exhausted`).
5. **A claim test parametrized over fewer seeds than `min_seeds`,** or not parametrized at all. Rejected as `insufficient_seeds` with the count recorded. Pinned to Task 4 (`test_stub_check_counts_claim_seeds`).

---

### Task 1: Config, runner error reporting, freeze helper

**Files:**
- Modify: `src/paper2code/config.py`
- Modify: `config.yaml`
- Modify: `src/paper2code/sandbox/runner.py`
- Modify: `src/paper2code/manager/freeze.py`
- Modify: `src/paper2code/manager/local.py`
- Test: `tests/test_config.py`, `tests/test_runner.py`, `tests/test_freeze.py`

**Interfaces:**
- Consumes: step 1 `TestRunResult`, `parse_junit`, `write_manifest`, `manifest_sha256`, `RunRecord`.
- Produces:
  - `Config.allowed_packages: list[str]` default `["numpy", "torch", "scipy", "scikit-learn"]`.
  - `TestRunResult.errored: tuple[str, ...] = ()` (collection and setup errors; also still present in `failed` so `all_passed` is unchanged). `parse_junit_errors(path: Path) -> tuple[str, ...]`.
  - `freeze.freeze_scope(record: RunRecord) -> dict[str, str]`: writes the manifest for `record.run_dir / "scope"`, sets `record.scope_manifest_sha256`, returns the manifest. `local.init_run` uses it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_allowed_packages_default_and_override(tmp_path):
    assert Config().allowed_packages == ["numpy", "torch", "scipy", "scikit-learn"]
    p = tmp_path / "c.yaml"
    p.write_text("allowed_packages: [numpy]\n", encoding="utf-8")
    assert load_config(p).allowed_packages == ["numpy"]
```

Append to `tests/test_runner.py`:

```python
def test_collection_error_is_reported_as_errored(tmp_path):
    ws = tmp_path / "ws"
    tests = tmp_path / "tests"
    _write(ws / "stub.py", "def f(x):\n    raise NotImplementedError\n")
    _write(tests / "test_bad.py", "from stub import g\ndef test_b(): assert g(1) == 2\n")
    result = LocalTestRunner(timeout_s=60).run(ws, tests)
    assert result.errored == ("::test_bad",)
    assert result.failed == ("::test_bad",)
    assert result.all_passed is False


def test_not_implemented_is_a_failure_not_an_error(tmp_path):
    ws = tmp_path / "ws"
    tests = tmp_path / "tests"
    _write(ws / "stub.py", "def f(x):\n    raise NotImplementedError\n")
    _write(tests / "test_ok.py", "from stub import f\ndef test_a(): assert f(1) == 2\n")
    result = LocalTestRunner(timeout_s=60).run(ws, tests)
    assert result.errored == ()
    assert result.failed == ("test_ok::test_a",)
```

Append to `tests/test_freeze.py`:

```python
def test_freeze_scope_writes_manifest_and_anchor(tmp_path):
    from datetime import date

    from paper2code.manager.freeze import freeze_scope
    from paper2code.manager.record import Caps, create_run

    rec = create_run(tmp_path, date(2026, 10, 7), Caps(), 10.0)
    _make_scope(rec.run_dir / "scope") if (rec.run_dir / "scope").mkdir() is None else None
    manifest = freeze_scope(rec)
    assert set(manifest) == {"spec.md", "tests/public/test_a.py", "tests/hidden/test_h.py"}
    assert rec.scope_manifest_sha256 == manifest_sha256(rec.run_dir / "scope")
    assert verify_manifest(rec.run_dir / "scope", rec.scope_manifest_sha256) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_config.py tests/test_runner.py tests/test_freeze.py -q`
Expected: 4 failures (`AttributeError: ... 'allowed_packages'`, `AttributeError: ... 'errored'` twice, `ImportError: cannot import name 'freeze_scope'`).

- [ ] **Step 3: Add `allowed_packages` to config**

In `src/paper2code/config.py`, add to `Config` after `gpu_usd_per_hour`:

```python
    allowed_packages: list[str] = field(default_factory=lambda: ["numpy", "torch", "scipy", "scikit-learn"])
```

and in `load_config` add the keyword:

```python
        allowed_packages=list(raw.get("allowed_packages", defaults.allowed_packages)),
```

Append to `config.yaml`:

```yaml
allowed_packages: [numpy, torch, scipy, scikit-learn]   # what scoped tests and builder code may import; the stub check runs locally
```

- [ ] **Step 4: Add `errored` to the runner**

In `src/paper2code/sandbox/runner.py`:

Add the field after `workspace_sha256`:

```python
    errored: tuple[str, ...] = ()  # collection/setup errors; a subset of `failed`
```

Add after `parse_junit`:

```python
def parse_junit_errors(path: Path) -> tuple[str, ...]:
    """Test ids whose JUnit entry is an <error> (collection or setup failure), not a <failure>."""
    root = ET.parse(path).getroot()
    return tuple(
        f"{tc.get('classname', '')}::{tc.get('name', '')}"
        for tc in root.iter("testcase")
        if any(child.tag == "error" for child in tc)
    )
```

In `LocalTestRunner.run`, replace the final two statements:

```python
            passed, failed = parse_junit(report) if report.exists() else ((), ())
            errored = parse_junit_errors(report) if report.exists() else ()
            return TestRunResult(
                passed, failed, proc.returncode, False, duration, 0.0, proc.stdout + proc.stderr, digest, errored,
            )
```

- [ ] **Step 5: Add `freeze_scope` and use it in `init_run`**

Append to `src/paper2code/manager/freeze.py`:

```python
def freeze_scope(record) -> dict[str, str]:
    """Hash scope/ into manifest.json and anchor the manifest's own hash in the run record.

    After this call no agent session may write scope/; the inspector re-hashes against the anchor.
    """
    scope_dir = record.run_dir / "scope"
    manifest = write_manifest(scope_dir)
    record.scope_manifest_sha256 = manifest_sha256(scope_dir)
    return manifest
```

In `src/paper2code/manager/local.py` replace the two lines

```python
    write_manifest(record.run_dir / "scope")
    record.scope_manifest_sha256 = manifest_sha256(record.run_dir / "scope")
```

with

```python
    freeze_scope(record)
```

and change the freeze import to `from paper2code.manager.freeze import freeze_scope`.

- [ ] **Step 6: Run the tests to verify they pass, then the whole suite**

Run: `pytest tests/test_config.py tests/test_runner.py tests/test_freeze.py -q` then `pytest -q`
Expected: all pass; suite green.

- [ ] **Step 7: Commit**

```bash
git add config.yaml src/paper2code/config.py src/paper2code/sandbox/runner.py src/paper2code/manager/freeze.py src/paper2code/manager/local.py tests/test_config.py tests/test_runner.py tests/test_freeze.py
git commit -m "Step 3 groundwork: allowed_packages, runner errored ids, freeze_scope helper

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Scope draft schema, validation, and file rendering

**Files:**
- Create: `src/paper2code/agents/scoper/__init__.py` (empty)
- Create: `src/paper2code/agents/scoper/schemas.py`
- Create: `src/paper2code/manager/scope_files.py`
- Test: `tests/test_scope_files.py`

**Interfaces:**
- Consumes: nothing from this plan.
- Produces:
  - Pydantic models: `FunctionSpec(signature: str, doc: str)`, `MethodSpec(signature: str, doc: str)`, `ClassSpec(name: str, doc: str, methods: list[MethodSpec])`, `InterfaceSpec(module: str, functions: list[FunctionSpec], classes: list[ClassSpec])`, `TestFile(path: str, content: str)`, `ScopeDraft(spec_md: str, interface: InterfaceSpec, public_tests: list[TestFile], hidden_tests: list[TestFile], seeds: list[int], est_gpu_hours: float, est_usd: float, notes: str)`.
  - `CLAIM_TEST_FILE = "test_claim.py"`.
  - `validate_draft(draft: ScopeDraft) -> list[str]`: problems, empty when valid. Rules: module is an identifier; every signature parses as a Python `def`/`class` line; every test path matches `^test_[A-Za-z0-9_]+\.py$` with no directory parts; no duplicate paths within a group; public contains `test_claim.py`; hidden non-empty; every test file content parses as Python; `seeds` non-empty and distinct; `est_usd >= 0`.
  - `render_interface_md(interface: InterfaceSpec) -> str`.
  - `render_stubs(interface: InterfaceSpec) -> str`: Python source where every function and method raises `NotImplementedError`; each class gets `def __init__(self, *args, **kwargs): pass` unless the draft lists `__init__`.
  - `write_scope(scope_dir: Path, draft: ScopeDraft) -> None`: writes `spec.md`, `interface.md`, `tests/public/*`, `tests/hidden/*`; creates directories; refuses (raises `ValueError`) if `validate_draft` reports problems.

- [ ] **Step 1: Write the failing tests**

`tests/test_scope_files.py`:

```python
import ast

import pytest

from paper2code.agents.scoper.schemas import (
    CLAIM_TEST_FILE,
    ClassSpec,
    FunctionSpec,
    InterfaceSpec,
    MethodSpec,
    ScopeDraft,
    TestFile,
    validate_draft,
)
from paper2code.manager.scope_files import render_interface_md, render_stubs, write_scope


def _draft(**over):
    base = dict(
        spec_md="# Scope\n\nSmooth a noisy sine.\n",
        interface=InterfaceSpec(
            module="canary_method",
            functions=[
                FunctionSpec(signature="def ema(xs: list[float], alpha: float) -> list[float]", doc="EMA smoothing."),
                FunctionSpec(signature="def run_experiment(seed: int, n: int = 500) -> dict[str, float]", doc="One trial."),
            ],
            classes=[ClassSpec(name="Model", doc="A model.", methods=[MethodSpec(signature="def fit(self, xs: list[float]) -> None", doc="Fit.")])],
        ),
        public_tests=[
            TestFile(path="test_units.py", content="from canary_method import ema\ndef test_ema(): assert ema([1.0], 0.5) == [1.0]\n"),
            TestFile(path=CLAIM_TEST_FILE, content="import pytest\nfrom canary_method import run_experiment\n@pytest.mark.parametrize('seed', [0, 1, 2])\ndef test_claim(seed): assert run_experiment(seed)['method_mse'] < 0.5\n"),
        ],
        hidden_tests=[TestFile(path="test_claim_hidden.py", content="from canary_method import run_experiment\ndef test_claim_hidden(): assert run_experiment(7)['method_mse'] < 0.5\n")],
        seeds=[0, 1, 2],
        est_gpu_hours=0.01,
        est_usd=0.01,
        notes="",
    )
    base.update(over)
    return ScopeDraft(**base)


def test_valid_draft_has_no_problems():
    assert validate_draft(_draft()) == []


def test_validate_draft_rejects_unsafe_paths():
    for bad in ("../test_x.py", "sub/test_x.py", "/tmp/test_x.py", "notatest.py", "test_x.txt", "test-x.py"):
        problems = validate_draft(_draft(hidden_tests=[TestFile(path=bad, content="def test_h(): assert False\n")]))
        assert any(bad in p for p in problems), bad


def test_validate_draft_requires_claim_file_and_hidden_tests():
    assert any("test_claim.py" in p for p in validate_draft(_draft(public_tests=[TestFile(path="test_units.py", content="def test_a(): assert False\n")])))
    assert any("hidden" in p for p in validate_draft(_draft(hidden_tests=[])))


def test_validate_draft_rejects_bad_python_and_signatures():
    assert any("test_units.py" in p for p in validate_draft(_draft(public_tests=[TestFile(path="test_units.py", content="def test_a(:\n"), _draft().public_tests[1]])))
    bad_iface = InterfaceSpec(module="m", functions=[FunctionSpec(signature="ema(xs)", doc="")], classes=[])
    assert any("signature" in p for p in validate_draft(_draft(interface=bad_iface)))
    assert any("module" in p for p in validate_draft(_draft(interface=InterfaceSpec(module="not valid", functions=[], classes=[]))))


def test_validate_draft_rejects_duplicates_and_bad_seeds():
    dup = [TestFile(path="test_claim.py", content="def test_a(): assert False\n")] * 2
    assert any("duplicate" in p for p in validate_draft(_draft(public_tests=dup)))
    assert any("seeds" in p for p in validate_draft(_draft(seeds=[])))
    assert any("seeds" in p for p in validate_draft(_draft(seeds=[1, 1])))
    assert any("est_usd" in p for p in validate_draft(_draft(est_usd=-1.0)))


def test_render_stubs_raise_not_implemented_and_import_cleanly(tmp_path):
    src = render_stubs(_draft().interface)
    ast.parse(src)
    (tmp_path / "canary_method.py").write_text(src, encoding="utf-8")
    import importlib.util

    spec = importlib.util.spec_from_file_location("canary_method", tmp_path / "canary_method.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with pytest.raises(NotImplementedError):
        mod.ema([1.0], 0.5)
    with pytest.raises(NotImplementedError):
        mod.Model().fit([1.0])
    assert mod.Model(1, 2, k=3) is not None  # constructor stub accepts anything


def test_render_interface_md_lists_everything():
    md = render_interface_md(_draft().interface)
    assert "canary_method" in md and "def ema(xs: list[float], alpha: float) -> list[float]" in md
    assert "class Model" in md and "def fit(self, xs: list[float]) -> None" in md and "EMA smoothing." in md


def test_write_scope_lays_out_files(tmp_path):
    scope = tmp_path / "scope"
    write_scope(scope, _draft())
    assert (scope / "spec.md").read_text(encoding="utf-8").startswith("# Scope")
    assert "def ema" in (scope / "interface.md").read_text(encoding="utf-8")
    assert sorted(p.name for p in (scope / "tests" / "public").iterdir()) == ["test_claim.py", "test_units.py"]
    assert [p.name for p in (scope / "tests" / "hidden").iterdir()] == ["test_claim_hidden.py"]


def test_write_scope_refuses_invalid_draft(tmp_path):
    with pytest.raises(ValueError, match="hidden"):
        write_scope(tmp_path / "scope", _draft(hidden_tests=[]))
    assert not (tmp_path / "scope").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_scope_files.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'paper2code.agents.scoper'`

- [ ] **Step 3: Write `src/paper2code/agents/scoper/schemas.py`**

```python
"""Structured output of the scoper: the whole assignment as data. The manager writes the files."""
from __future__ import annotations

import ast
import re

from pydantic import BaseModel

CLAIM_TEST_FILE = "test_claim.py"
_TEST_PATH = re.compile(r"^test_[A-Za-z0-9_]+\.py$")


class FunctionSpec(BaseModel):
    signature: str  # e.g. "def ema(xs: list[float], alpha: float) -> list[float]"
    doc: str


class MethodSpec(BaseModel):
    signature: str  # e.g. "def fit(self, xs: list[float]) -> None"
    doc: str


class ClassSpec(BaseModel):
    name: str
    doc: str
    methods: list[MethodSpec]


class InterfaceSpec(BaseModel):
    module: str  # importable module name; the file is <module>.py at the workspace root
    functions: list[FunctionSpec]
    classes: list[ClassSpec]


class TestFile(BaseModel):
    path: str  # flat file name, test_*.py
    content: str


class ScopeDraft(BaseModel):
    spec_md: str
    interface: InterfaceSpec
    public_tests: list[TestFile]
    hidden_tests: list[TestFile]
    seeds: list[int]
    est_gpu_hours: float
    est_usd: float
    notes: str


def _signature_ok(signature: str, indent: str = "") -> bool:
    try:
        tree = ast.parse(f"{indent}{signature}:\n{indent}    pass\n" if not indent else f"class _C:\n{indent}{signature}:\n{indent}    pass\n")
    except SyntaxError:
        return False
    node = tree.body[0] if not indent else tree.body[0].body[0]
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))


def _test_files_ok(files: list[TestFile], group: str, problems: list[str]) -> None:
    seen: set[str] = set()
    for tf in files:
        if not _TEST_PATH.match(tf.path):
            problems.append(f"{group} test path {tf.path!r} must be a flat test_*.py file name")
            continue
        if tf.path in seen:
            problems.append(f"duplicate {group} test path {tf.path!r}")
        seen.add(tf.path)
        try:
            ast.parse(tf.content)
        except SyntaxError as exc:
            problems.append(f"{group} test file {tf.path} is not valid Python: {exc.msg} (line {exc.lineno})")


def validate_draft(draft: ScopeDraft) -> list[str]:
    """Problems with a draft. Empty means the manager may write it. Nothing is written here."""
    problems: list[str] = []
    if not draft.interface.module.isidentifier():
        problems.append(f"module name {draft.interface.module!r} is not a valid identifier")
    for fn in draft.interface.functions:
        if not fn.signature.startswith("def ") or not _signature_ok(fn.signature):
            problems.append(f"function signature {fn.signature!r} does not parse as a def line")
    for cls in draft.interface.classes:
        if not cls.name.isidentifier():
            problems.append(f"class name {cls.name!r} is not a valid identifier")
        for m in cls.methods:
            if not m.signature.startswith("def ") or not _signature_ok(m.signature, indent="    "):
                problems.append(f"method signature {m.signature!r} in class {cls.name} does not parse as a def line")
    _test_files_ok(draft.public_tests, "public", problems)
    _test_files_ok(draft.hidden_tests, "hidden", problems)
    if not any(tf.path == CLAIM_TEST_FILE for tf in draft.public_tests):
        problems.append(f"public tests must include {CLAIM_TEST_FILE}")
    if not draft.hidden_tests:
        problems.append("hidden tests must not be empty")
    if not draft.seeds or len(set(draft.seeds)) != len(draft.seeds):
        problems.append("seeds must be a non-empty list of distinct integers")
    if draft.est_usd < 0 or draft.est_gpu_hours < 0:
        problems.append("est_usd and est_gpu_hours must be non-negative")
    return problems
```

- [ ] **Step 4: Write `src/paper2code/manager/scope_files.py`**

```python
"""Turn a ScopeDraft into the files under scope/: spec.md, interface.md, tests, and the stub module."""
from __future__ import annotations

from pathlib import Path

from paper2code.agents.scoper.schemas import InterfaceSpec, ScopeDraft, validate_draft


def render_interface_md(interface: InterfaceSpec) -> str:
    lines = [
        "# Interface",
        "",
        f"Module `{interface.module}` (file `{interface.module}.py` at the workspace root). The tests import from it.",
        "",
        "```python",
    ]
    for fn in interface.functions:
        lines += [f"{fn.signature}:", f'    """{fn.doc}"""', ""]
    for cls in interface.classes:
        lines += [f"class {cls.name}:", f'    """{cls.doc}"""']
        for m in cls.methods:
            lines += [f"    {m.signature}:", f'        """{m.doc}"""', ""]
        lines.append("")
    lines.append("```")
    return "\n".join(lines) + "\n"


def render_stubs(interface: InterfaceSpec) -> str:
    """A module where every function and method raises NotImplementedError. Used by the stub check."""
    lines = [f'"""Stub of {interface.module}: every call raises NotImplementedError."""', ""]
    for fn in interface.functions:
        lines += [f"{fn.signature}:", "    raise NotImplementedError", "", ""]
    for cls in interface.classes:
        lines.append(f"class {cls.name}:")
        if not any(m.signature.startswith("def __init__") for m in cls.methods):
            lines += ["    def __init__(self, *args, **kwargs):", "        pass", ""]
        for m in cls.methods:
            lines += [f"    {m.signature}:", "        raise NotImplementedError", ""]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_scope(scope_dir: Path, draft: ScopeDraft) -> None:
    problems = validate_draft(draft)
    if problems:
        raise ValueError("; ".join(problems))
    (scope_dir / "tests" / "public").mkdir(parents=True, exist_ok=True)
    (scope_dir / "tests" / "hidden").mkdir(parents=True, exist_ok=True)
    (scope_dir / "spec.md").write_text(draft.spec_md, encoding="utf-8")
    (scope_dir / "interface.md").write_text(render_interface_md(draft.interface), encoding="utf-8")
    for tf in draft.public_tests:
        (scope_dir / "tests" / "public" / tf.path).write_text(tf.content, encoding="utf-8")
    for tf in draft.hidden_tests:
        (scope_dir / "tests" / "hidden" / tf.path).write_text(tf.content, encoding="utf-8")
```

Also create the empty `src/paper2code/agents/scoper/__init__.py`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_scope_files.py -q`
Expected: 9 PASS

- [ ] **Step 6: Commit**

```bash
git add src/paper2code/agents/scoper src/paper2code/manager/scope_files.py tests/test_scope_files.py
git commit -m "Add ScopeDraft schema, validation, and scope file rendering

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Scoper prompt, draft call, fake scoper

**Files:**
- Create: `src/paper2code/agents/scoper/prompts.py`
- Create: `src/paper2code/agents/scoper/scoper.py`
- Create: `src/paper2code/agents/scoper/fake.py`
- Create: `src/paper2code/agents/fake.py`
- Modify: `src/paper2code/llm/factory.py`
- Test: `tests/test_scoper.py`

**Interfaces:**
- Consumes: `ScopeDraft` (Task 2), `ChatModel`, `Usage`, `LLMResult` (step 2), `ArxivPaper`, `UNTRUSTED_NOTE`, `BEGIN`, `END`, `_defang` from `agents/scout/prompts.py`, `fake_scout_responder`.
- Produces:
  - `SCOPER_INSTRUCTIONS: str`, `render_scoper_input(paper, scorecard: dict, fulltext: str, budget_usd: float, min_seeds: int, allowed_packages: list[str], gpu_usd_per_hour: float) -> str`.
  - `ROLE_SCOPER = "scoper"`; `draft_scope(paper, scorecard, fulltext, llm, cfg, budget_usd, usage) -> ScopeDraft` (one call; raises `LLMError`/`LLMBadOutput` through).
  - `fake_scoper_responder(role, instructions, user, schema) -> ScopeDraft`: the EMA canary assignment, same interface as `tests/fixtures/canary/reference/canary_method.py`, so the step 1 stub builder can pass it. `CANARY_DRAFT: ScopeDraft` exported for tests.
  - `agents/fake.py: fake_agent_responder(role, instructions, user, schema)`: dispatches by schema to the scout or scoper fake; raises `ValueError` for unknown schemas. `llm/factory.py` builds `FakeChatModel(fake_agent_responder)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_scoper.py`:

```python
from paper2code.agents.fake import fake_agent_responder
from paper2code.agents.scoper.fake import CANARY_DRAFT, fake_scoper_responder
from paper2code.agents.scoper.prompts import SCOPER_INSTRUCTIONS, render_scoper_input
from paper2code.agents.scoper.schemas import CLAIM_TEST_FILE, ScopeDraft, validate_draft
from paper2code.agents.scoper.scoper import ROLE_SCOPER, draft_scope
from paper2code.agents.scout.schemas import EligibilityBatch, Scorecard
from paper2code.arxiv.models import ArxivPaper
from paper2code.config import Config
from paper2code.llm.base import Usage
from paper2code.llm.fake import FakeChatModel

PAPER = ArxivPaper(arxiv_id="2610.00001", version=1, title="EMA Denoising", abstract="Smoothing helps.", authors=["A"], url="https://arxiv.org/abs/2610.00001")
CARD = {"arxiv_id": "2610.00001", "claim": "At reduced scale, EMA should beat identity on sine denoising by at least 50%.", "dataset": "synthetic, yes", "est_usd": 0.5, "testability": 5}


def test_instructions_cover_the_contract():
    for needle in ("test_claim.py", "NotImplementedError", "parametrize", "hidden", "untrusted", "seed"):
        assert needle in SCOPER_INSTRUCTIONS, needle


def test_render_scoper_input_carries_everything():
    text = render_scoper_input(PAPER, CARD, "FULL TEXT", budget_usd=10.0, min_seeds=3, allowed_packages=["numpy", "torch"], gpu_usd_per_hour=1.0)
    for needle in ("2610.00001", "EMA Denoising", "FULL TEXT", "10.0", "3", "numpy, torch", "beat identity", "BEGIN PAPER", "END PAPER"):
        assert needle in text, needle
    assert text.index("BEGIN PAPER") < text.index("FULL TEXT") < text.index("END PAPER")


def test_draft_scope_calls_scoper_role_and_accounts_usage():
    def responder(role, instructions, user, schema):
        assert role == ROLE_SCOPER and schema is ScopeDraft and instructions == SCOPER_INSTRUCTIONS
        assert "FULL TEXT" in user
        return CANARY_DRAFT

    usage = Usage()
    draft = draft_scope(PAPER, CARD, "FULL TEXT", FakeChatModel(responder), Config(), budget_usd=10.0, usage=usage)
    assert draft is CANARY_DRAFT and usage.calls == 1


def test_canary_draft_is_valid_and_matches_reference_interface():
    assert validate_draft(CANARY_DRAFT) == []
    assert CANARY_DRAFT.interface.module == "canary_method"
    sigs = [f.signature for f in CANARY_DRAFT.interface.functions]
    assert any(s.startswith("def ema(") for s in sigs)
    assert any(s.startswith("def baseline(") for s in sigs)
    assert any(s.startswith("def run_experiment(seed: int") for s in sigs)
    assert any(tf.path == CLAIM_TEST_FILE for tf in CANARY_DRAFT.public_tests)
    assert CANARY_DRAFT.seeds == [0, 1, 2] and CANARY_DRAFT.est_usd < 1.0


def test_fake_agent_responder_dispatches_by_schema():
    assert fake_agent_responder("scoper", "", "x", ScopeDraft) is CANARY_DRAFT
    assert fake_scoper_responder("scoper", "", "x", ScopeDraft) is CANARY_DRAFT
    assert isinstance(fake_agent_responder("scout_pass2", "", "x", Scorecard), Scorecard)
    assert isinstance(fake_agent_responder("scout_pass1", "", "ID: 2610.00001\n", EligibilityBatch), EligibilityBatch)


def test_factory_fake_model_answers_all_roles(tmp_path):
    from paper2code.llm.factory import make_chat_model
    from paper2code.manager.graph import RunContext

    model = make_chat_model(RunContext(config=Config(runs_root=tmp_path), llm="fake"))
    assert model.parse("scoper", "", "x", ScopeDraft).value is CANARY_DRAFT
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_scoper.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'paper2code.agents.fake'`

- [ ] **Step 3: Write `src/paper2code/agents/scoper/prompts.py`**

```python
"""Scoper prompt: one call that returns the whole assignment as data."""
from __future__ import annotations

from paper2code.agents.scout.prompts import BEGIN, END, UNTRUSTED_NOTE, _defang
from paper2code.arxiv.models import ArxivPaper

SCOPER_INSTRUCTIONS = """You are the scoper for an automated research-reproduction loop. You turn one paper into a small,
self-contained coding assignment with tests. A coding agent that has never seen the paper's code
will implement it from your spec and interface on one small GPU; a separate inspector will run your
hidden tests to catch cheating. Your assignment must be honest, runnable, and failing-until-done.

Return a ScopeDraft with these parts.

spec_md: Markdown. (1) The method in plain language, enough to implement it without the paper.
(2) The scaled-down experiment plan: dataset or synthetic data (with exact download or generation
instructions), model size, epochs or steps, the seeds, and the baseline. Size it to the budget given
in the input. (3) The claim: what the method must beat, on what metric, by what margin, with the
tolerance you consider fair at this scale.

interface: the module name (a valid Python identifier; the file is <module>.py) and every function
and class the tests import, as exact Python def lines with type hints, plus a one-line doc each.
Include a top-level function that runs the scaled experiment for one seed and returns the metrics
the claim test compares, so the claim test is a single call per seed.

public_tests: flat file names test_*.py. Unit tests for the method's components, and exactly one
file named test_claim.py whose claim test runs the scaled experiment and asserts the method beats
the baseline by at least the margin. The claim test MUST be parametrized over seeds with
@pytest.mark.parametrize("seed", [...]) using at least the minimum seed count given in the input.

hidden_tests: flat file names test_*.py, never shown to the builder. Variants of the claim test
with different seeds, a different data slice, and a perturbed hyperparameter, so a hardcoded answer
for the public seeds fails. Also list the seeds you used across both groups in `seeds`.

Rules for every test file:
- Import only from the interface module, pytest, the standard library, and the allowed packages.
- Every test must FAIL against a stub module where every function raises NotImplementedError. A
  test that passes on stubs is removed automatically; if the claim test is trivial the whole scope
  is rejected. Do not write tests that only check types, imports, or constants.
- Tests must be deterministic given the seed and must run on CPU or a small GPU in minutes.
- Do not download anything inside a test except what spec_md says the dataset is.

est_gpu_hours and est_usd: your estimate for running the scaled experiment (method plus baseline,
all seeds, public and hidden) at the GPU price given. notes: anything the builder is likely to get
wrong, in two or three sentences.

""" + UNTRUSTED_NOTE


def render_scoper_input(
    paper: ArxivPaper,
    scorecard: dict,
    fulltext: str,
    budget_usd: float,
    min_seeds: int,
    allowed_packages: list[str],
    gpu_usd_per_hour: float,
) -> str:
    return (
        f"ID: {paper.arxiv_id}\n"
        f"Budget: {budget_usd} USD total for the scaled experiment\n"
        f"Minimum seeds for the claim test: {min_seeds}\n"
        f"Allowed packages: {', '.join(allowed_packages)}\n"
        f"GPU price: {gpu_usd_per_hour} USD per GPU hour\n"
        f"Scout claim: {_defang(str(scorecard.get('claim', '')))}\n"
        f"Scout dataset note: {_defang(str(scorecard.get('dataset', '')))}\n"
        f"Scout cost estimate: {scorecard.get('est_usd', '')} USD\n\n"
        f"{BEGIN}\nTitle: {_defang(paper.title)}\nAuthors: {_defang(', '.join(paper.authors))}\n"
        f"Abstract: {_defang(paper.abstract)}\n\nFull text:\n{_defang(fulltext)}\n{END}"
    )
```

- [ ] **Step 4: Write `src/paper2code/agents/scoper/scoper.py`**

```python
"""The scoper call. Pure function over a ChatModel; the stage writes files and runs checks."""
from __future__ import annotations

from paper2code.agents.scoper.prompts import SCOPER_INSTRUCTIONS, render_scoper_input
from paper2code.agents.scoper.schemas import ScopeDraft
from paper2code.arxiv.models import ArxivPaper
from paper2code.config import Config
from paper2code.llm.base import ChatModel, Usage

ROLE_SCOPER = "scoper"


def draft_scope(
    paper: ArxivPaper, scorecard: dict, fulltext: str, llm: ChatModel, cfg: Config, budget_usd: float, usage: Usage,
) -> ScopeDraft:
    user = render_scoper_input(
        paper, scorecard, fulltext,
        budget_usd=budget_usd, min_seeds=cfg.min_seeds,
        allowed_packages=cfg.allowed_packages, gpu_usd_per_hour=cfg.gpu_usd_per_hour,
    )
    result = llm.parse(ROLE_SCOPER, SCOPER_INSTRUCTIONS, user, ScopeDraft)
    usage.add(result)
    return result.value
```

- [ ] **Step 5: Write `src/paper2code/agents/scoper/fake.py`**

```python
"""Fake scoper: always returns the EMA denoising canary, whose interface matches
tests/fixtures/canary/reference/canary_method.py so the stub builder can pass it."""
from __future__ import annotations

from pydantic import BaseModel

from paper2code.agents.scoper.schemas import FunctionSpec, InterfaceSpec, ScopeDraft, TestFile

_SPEC = """# Scope: EMA denoising canary

## Method in plain language

Replace each sample by a weighted blend of the new sample (weight alpha) and the previous smoothed
value (weight 1 - alpha). The first smoothed value equals the first sample. The baseline leaves the
signal untouched.

## Scaled experiment

- Signal: one sine period, n = 500 samples. Noise: Gaussian, std 1.0, from `random.Random(seed)`.
- Seeds: 0, 1, 2 (public). Hidden tests use other seeds and other noise/alpha.
- No GPU, no dataset download. Pure Python.

## Claim

For every seed, `method_mse <= 0.5 * baseline_mse`.
"""

_UNITS = """from canary_method import baseline, ema


def test_ema_first_element_is_input():
    assert ema([2.0, 4.0], 0.5)[0] == 2.0


def test_ema_known_values():
    assert ema([0.0, 1.0, 1.0], 0.5) == [0.0, 0.5, 0.75]


def test_baseline_is_identity_copy():
    xs = [1.0, 2.0]
    out = baseline(xs)
    assert out == xs and out is not xs
"""

_CLAIM = """import pytest

from canary_method import run_experiment


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_method_halves_mse(seed):
    r = run_experiment(seed)
    assert r["method_mse"] <= 0.5 * r["baseline_mse"]
"""

_HIDDEN = """import pytest

from canary_method import run_experiment


@pytest.mark.parametrize("seed", [7, 8, 9])
def test_method_halves_mse_other_seeds(seed):
    r = run_experiment(seed)
    assert r["method_mse"] <= 0.5 * r["baseline_mse"]


def test_claim_holds_at_lower_noise():
    r = run_experiment(11, noise=0.5)
    assert r["method_mse"] <= 0.5 * r["baseline_mse"]


def test_claim_holds_at_other_alpha():
    r = run_experiment(12, alpha=0.4)
    assert r["method_mse"] <= 0.5 * r["baseline_mse"]
"""

CANARY_DRAFT = ScopeDraft(
    spec_md=_SPEC,
    interface=InterfaceSpec(
        module="canary_method",
        functions=[
            FunctionSpec(signature="def ema(xs: list[float], alpha: float) -> list[float]", doc="s[0] = xs[0]; s[t] = alpha * xs[t] + (1 - alpha) * s[t-1]."),
            FunctionSpec(signature="def baseline(xs: list[float]) -> list[float]", doc="Identity: returns a copy of xs."),
            FunctionSpec(
                signature="def run_experiment(seed: int, n: int = 500, noise: float = 1.0, alpha: float = 0.3) -> dict[str, float]",
                doc='Returns {"method_mse": float, "baseline_mse": float} for one seeded trial.',
            ),
        ],
        classes=[],
    ),
    public_tests=[TestFile(path="test_units.py", content=_UNITS), TestFile(path="test_claim.py", content=_CLAIM)],
    hidden_tests=[TestFile(path="test_claim_hidden.py", content=_HIDDEN)],
    seeds=[0, 1, 2, 7, 8, 9, 11, 12],
    est_gpu_hours=0.0,
    est_usd=0.01,
    notes="fake scoper: canned EMA canary, no model was consulted",
)


def fake_scoper_responder(role: str, instructions: str, user: str, schema: type[BaseModel]) -> BaseModel:
    if schema is ScopeDraft:
        return CANARY_DRAFT
    raise ValueError(f"fake scoper cannot answer schema {schema.__name__}")
```

- [ ] **Step 6: Write `src/paper2code/agents/fake.py` and update the factory**

`src/paper2code/agents/fake.py`:

```python
"""One fake responder for every agent role, dispatching on the requested schema."""
from __future__ import annotations

from pydantic import BaseModel

from paper2code.agents.scoper.fake import fake_scoper_responder
from paper2code.agents.scoper.schemas import ScopeDraft
from paper2code.agents.scout.fake import fake_scout_responder
from paper2code.agents.scout.schemas import EligibilityBatch, Scorecard


def fake_agent_responder(role: str, instructions: str, user: str, schema: type[BaseModel]) -> BaseModel:
    if schema in (EligibilityBatch, Scorecard):
        return fake_scout_responder(role, instructions, user, schema)
    if schema is ScopeDraft:
        return fake_scoper_responder(role, instructions, user, schema)
    raise ValueError(f"no fake answer for schema {schema.__name__} (role {role})")
```

In `src/paper2code/llm/factory.py` replace the fake branch body:

```python
    if ctx.llm == "fake":
        from paper2code.agents.fake import fake_agent_responder
        from paper2code.llm.fake import FakeChatModel

        return FakeChatModel(fake_agent_responder)
```

The step 2 test `test_fake_llm_name_builds_fake_model` asserts `model.responder is fake_scout_responder`; change that assertion to `model.responder is fake_agent_responder` (import it in that test).

- [ ] **Step 7: Run the tests to verify they pass, then the whole suite**

Run: `pytest tests/test_scoper.py tests/test_stages_scout.py -q` then `pytest -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/paper2code/agents/scoper src/paper2code/agents/fake.py src/paper2code/llm/factory.py tests/test_scoper.py tests/test_stages_scout.py
git commit -m "Add scoper prompt and draft call, fake scoper with the EMA canary, fake responder dispatcher

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Stub check

**Files:**
- Create: `src/paper2code/manager/stubcheck.py`
- Test: `tests/test_stubcheck.py`

**Interfaces:**
- Consumes: `render_stubs`, `write_scope` (Task 2), `CANARY_DRAFT` (Task 3), `InterfaceSpec`, `TestRunner`, `TestRunResult.errored` (Task 1), `CLAIM_TEST_FILE`.
- Produces:
  - `StubCheckResult` dataclass: `removed: list[str]` (test ids deleted because they passed on stubs), `errored: list[str]`, `claim_test_ids: list[str]` (ids from `test_claim.py` that remain and failed), `public_failed: int`, `hidden_failed: int`, `reject_reason: str | None` in `{None, "tests_do_not_collect", "trivial_claim_test", "insufficient_seeds", "no_tests"}`.
  - `remove_tests(file_path: Path, names: set[str]) -> list[str]`: deletes the named top-level test functions (decorators included) using `ast`; returns the names removed.
  - `run_stub_check(scope_dir: Path, interface: InterfaceSpec, runner: TestRunner, min_seeds: int) -> StubCheckResult`: builds a temp workspace holding only `<module>.py` from `render_stubs`, runs public and hidden suites, removes passing tests from the scope files, re-derives the claim test ids, applies the rules in this order: any `errored` → `tests_do_not_collect`; no tests ran at all → `no_tests`; claim test removed or missing → `trivial_claim_test`; `len(claim_test_ids) < min_seeds` → `insufficient_seeds`.
  - Test id convention from the runner: `<file stem>::<function>[<param>]`; the function name is the part between `::` and the first `[`.

- [ ] **Step 1: Write the failing tests**

`tests/test_stubcheck.py`:

```python
from pathlib import Path

import pytest

from paper2code.agents.scoper.fake import CANARY_DRAFT
from paper2code.agents.scoper.schemas import ScopeDraft, TestFile
from paper2code.manager.scope_files import write_scope
from paper2code.manager.stubcheck import StubCheckResult, remove_tests, run_stub_check
from paper2code.sandbox.runner import LocalTestRunner

RUNNER = LocalTestRunner(timeout_s=120)


def _scope(tmp_path, draft: ScopeDraft) -> Path:
    scope = tmp_path / "scope"
    write_scope(scope, draft)
    return scope


def _with_public(draft: ScopeDraft, *files: TestFile) -> ScopeDraft:
    return draft.model_copy(update={"public_tests": list(draft.public_tests) + list(files)})


def test_remove_tests_cuts_named_functions_with_decorators(tmp_path):
    f = tmp_path / "test_x.py"
    f.write_text(
        "import pytest\n\n\n@pytest.mark.parametrize('s', [1, 2])\ndef test_keep(s):\n    assert s\n\n\n"
        "@pytest.mark.skip\ndef test_drop(): \n    assert True\n\n\ndef test_also_drop():\n    assert True\n\n\ndef test_last():\n    assert False\n",
        encoding="utf-8",
    )
    removed = remove_tests(f, {"test_drop", "test_also_drop", "test_missing"})
    assert sorted(removed) == ["test_also_drop", "test_drop"]
    src = f.read_text(encoding="utf-8")
    assert "test_keep" in src and "test_last" in src
    assert "test_drop" not in src and "test_also_drop" not in src and "@pytest.mark.skip" not in src
    compile(src, "test_x.py", "exec")


def test_canary_scope_passes_stub_check(tmp_path):
    scope = _scope(tmp_path, CANARY_DRAFT)
    r = run_stub_check(scope, CANARY_DRAFT.interface, RUNNER, min_seeds=3)
    assert isinstance(r, StubCheckResult)
    assert r.reject_reason is None
    assert r.removed == [] and r.errored == []
    assert sorted(r.claim_test_ids) == ["test_claim::test_method_halves_mse[0]", "test_claim::test_method_halves_mse[1]", "test_claim::test_method_halves_mse[2]"]
    assert r.public_failed == 6 and r.hidden_failed == 5


def test_trivial_test_is_removed_and_logged(tmp_path):
    draft = _with_public(CANARY_DRAFT, TestFile(path="test_planted.py", content="def test_trivial():\n    assert True\n\n\ndef test_real():\n    from canary_method import ema\n    assert ema([1.0], 0.5) == [1.0]\n"))
    scope = _scope(tmp_path, draft)
    r = run_stub_check(scope, draft.interface, RUNNER, min_seeds=3)
    assert r.reject_reason is None
    assert r.removed == ["test_planted::test_trivial"]
    src = (scope / "tests" / "public" / "test_planted.py").read_text(encoding="utf-8")
    assert "test_trivial" not in src and "test_real" in src


def test_trivial_hidden_test_is_removed_too(tmp_path):
    draft = CANARY_DRAFT.model_copy(update={"hidden_tests": CANARY_DRAFT.hidden_tests + [TestFile(path="test_h2.py", content="def test_nothing():\n    assert 1 == 1\n")]})
    scope = _scope(tmp_path, draft)
    r = run_stub_check(scope, draft.interface, RUNNER, min_seeds=3)
    assert r.removed == ["test_h2::test_nothing"] and r.reject_reason is None


def test_trivial_claim_test_rejects_scope(tmp_path):
    claim = TestFile(path="test_claim.py", content="import pytest\n\n\n@pytest.mark.parametrize('seed', [0, 1, 2])\ndef test_claim(seed):\n    assert seed >= 0\n")
    draft = CANARY_DRAFT.model_copy(update={"public_tests": [CANARY_DRAFT.public_tests[0], claim]})
    scope = _scope(tmp_path, draft)
    r = run_stub_check(scope, draft.interface, RUNNER, min_seeds=3)
    assert r.reject_reason == "trivial_claim_test"
    assert set(r.removed) == {"test_claim::test_claim[0]", "test_claim::test_claim[1]", "test_claim::test_claim[2]"}


def test_stub_check_counts_claim_seeds(tmp_path):
    claim = TestFile(path="test_claim.py", content="import pytest\nfrom canary_method import run_experiment\n\n\n@pytest.mark.parametrize('seed', [0, 1])\ndef test_claim(seed):\n    assert run_experiment(seed)['method_mse'] < 1\n")
    draft = CANARY_DRAFT.model_copy(update={"public_tests": [CANARY_DRAFT.public_tests[0], claim]})
    scope = _scope(tmp_path, draft)
    r = run_stub_check(scope, draft.interface, RUNNER, min_seeds=3)
    assert r.reject_reason == "insufficient_seeds"
    assert len(r.claim_test_ids) == 2
    unparam = TestFile(path="test_claim.py", content="from canary_method import run_experiment\n\n\ndef test_claim():\n    assert run_experiment(0)['method_mse'] < 1\n")
    draft = CANARY_DRAFT.model_copy(update={"public_tests": [CANARY_DRAFT.public_tests[0], unparam]})
    r = run_stub_check(_scope(tmp_path / "b", draft), draft.interface, RUNNER, min_seeds=3)
    assert r.reject_reason == "insufficient_seeds" and len(r.claim_test_ids) == 1


def test_stub_check_rejects_uncollectable_tests(tmp_path):
    bad = TestFile(path="test_broken.py", content="from canary_method import not_in_interface\n\n\ndef test_x():\n    assert not_in_interface()\n")
    draft = _with_public(CANARY_DRAFT, bad)
    scope = _scope(tmp_path, draft)
    r = run_stub_check(scope, draft.interface, RUNNER, min_seeds=3)
    assert r.reject_reason == "tests_do_not_collect"
    assert any("test_broken" in e for e in r.errored)


def test_stub_check_rejects_missing_package(tmp_path):
    bad = TestFile(path="test_pkg.py", content="import definitely_not_installed_pkg\n\n\ndef test_x():\n    assert definitely_not_installed_pkg\n")
    draft = CANARY_DRAFT.model_copy(update={"hidden_tests": CANARY_DRAFT.hidden_tests + [bad]})
    scope = _scope(tmp_path, draft)
    r = run_stub_check(scope, draft.interface, RUNNER, min_seeds=3)
    assert r.reject_reason == "tests_do_not_collect"


def test_stub_check_leaves_no_workspace_in_scope(tmp_path):
    scope = _scope(tmp_path, CANARY_DRAFT)
    run_stub_check(scope, CANARY_DRAFT.interface, RUNNER, min_seeds=3)
    assert sorted(p.name for p in scope.iterdir()) == ["interface.md", "spec.md", "tests"]
    assert not any(p.name == "canary_method.py" for p in scope.rglob("*"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_stubcheck.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'paper2code.manager.stubcheck'`

- [ ] **Step 3: Write `src/paper2code/manager/stubcheck.py`**

```python
"""The stub check (spec 8.3): every scoped test must fail against a workspace of empty stubs.

A test that passes on stubs proves nothing and is deleted; a claim test that passes on stubs, or
that has too few seeds, rejects the scope; a test file that cannot even be imported against the
stubs would fail for the wrong reason forever, so it rejects the scope too.
"""
from __future__ import annotations

import ast
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from paper2code.agents.scoper.schemas import CLAIM_TEST_FILE, InterfaceSpec
from paper2code.manager.scope_files import render_stubs
from paper2code.sandbox.runner import TestRunner, TestRunResult

TESTS_DO_NOT_COLLECT = "tests_do_not_collect"
TRIVIAL_CLAIM_TEST = "trivial_claim_test"
INSUFFICIENT_SEEDS = "insufficient_seeds"
NO_TESTS = "no_tests"
CLAIM_STEM = CLAIM_TEST_FILE.removesuffix(".py")


@dataclass
class StubCheckResult:
    removed: list[str] = field(default_factory=list)
    errored: list[str] = field(default_factory=list)
    claim_test_ids: list[str] = field(default_factory=list)
    public_failed: int = 0
    hidden_failed: int = 0
    reject_reason: str | None = None


def _function_name(test_id: str) -> str:
    return test_id.split("::", 1)[1].split("[", 1)[0]


def _file_stem(test_id: str) -> str:
    return test_id.split("::", 1)[0]


def remove_tests(file_path: Path, names: set[str]) -> list[str]:
    """Delete the named top-level functions (with their decorators) from a test file."""
    source = file_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    spans: list[tuple[int, int]] = []
    removed: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            spans.append((start - 1, node.end_lineno))
            removed.append(node.name)
    for start, end in sorted(spans, reverse=True):
        del lines[start:end]
    file_path.write_text("".join(lines), encoding="utf-8")
    return removed


def _prune_passing(tests_dir: Path, result: TestRunResult) -> list[str]:
    by_file: dict[str, set[str]] = {}
    for test_id in result.passed:
        by_file.setdefault(_file_stem(test_id), set()).add(_function_name(test_id))
    for stem, names in by_file.items():
        remove_tests(tests_dir / f"{stem}.py", names)
    return list(result.passed)


def run_stub_check(scope_dir: Path, interface: InterfaceSpec, runner: TestRunner, min_seeds: int) -> StubCheckResult:
    public = scope_dir / "tests" / "public"
    hidden = scope_dir / "tests" / "hidden"
    out = StubCheckResult()
    with tempfile.TemporaryDirectory(prefix="p2c-stubs-") as tmp:
        workspace = Path(tmp) / "workspace"
        workspace.mkdir()
        (workspace / f"{interface.module}.py").write_text(render_stubs(interface), encoding="utf-8")
        pub = runner.run(workspace, public)
        hid = runner.run(workspace, hidden)
    out.errored = list(pub.errored) + list(hid.errored)
    if out.errored:
        out.reject_reason = TESTS_DO_NOT_COLLECT
        return out
    out.removed = _prune_passing(public, pub) + _prune_passing(hidden, hid)
    out.public_failed = len(pub.failed)
    out.hidden_failed = len(hid.failed)
    out.claim_test_ids = [t for t in pub.failed if _file_stem(t) == CLAIM_STEM]
    if not pub.failed and not hid.failed:
        out.reject_reason = NO_TESTS
    elif any(_file_stem(t) == CLAIM_STEM for t in pub.passed) or not out.claim_test_ids:
        out.reject_reason = TRIVIAL_CLAIM_TEST
    elif len(out.claim_test_ids) < min_seeds:
        out.reject_reason = INSUFFICIENT_SEEDS
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_stubcheck.py -q`
Expected: 9 PASS (the runner spawns pytest twice per check, so this file takes about a minute).

- [ ] **Step 5: Commit**

```bash
git add src/paper2code/manager/stubcheck.py tests/test_stubcheck.py
git commit -m "Add the stub check: prune trivial tests, reject uncollectable, trivial-claim and under-seeded scopes

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: The scope stage

**Files:**
- Modify: `src/paper2code/manager/stages/scope.py` (replace stub)
- Test: `tests/test_stage_scope.py`

**Interfaces:**
- Consumes: `draft_scope`, `ROLE_SCOPER` (Task 3), `validate_draft`, `write_scope` (Task 2), `run_stub_check` (Task 4), `freeze_scope` (Task 1), `fetch_fulltext`, `make_polite_client`, `make_chat_model`, `make_runner`, `SELECTED_FILE`, `read_papers`, `PAPERS_FILE`, `LLMError`, `LLMBadOutput`, `Usage`, `Outcome`, `RunError`, `Paper`.
- Produces:
  - `ATTEMPTS_FILE = "scope_attempts.jsonl"`; rows `{arxiv_id, title, accepted: bool, reason: str | None, removed_tests: [...], claim_tests: int, est_usd, cost_usd, model, ts}`; `read_attempts(run_dir) -> list[dict]`.
  - `stages.scope.run(record, ctx)`: for each shortlist entry not already attempted: wipe `scope/`, fetch full text (failure → attempt row `fulltext_unavailable`), draft (LLMBadOutput → `malformed_scope`), validate (→ `malformed_scope`), write, stub check (→ its reject reason), feasibility (`draft.est_usd > record.budget.limit_usd` → `over_budget`), freeze, set `record.paper`, append accepted row, stop. `LLMError` → `outcome = error`, `api_error`. Nothing accepted → `scope_rejected`. Spend added to `record.budget`. Rejection reasons are written exactly as above.

- [ ] **Step 1: Write the failing tests**

`tests/test_stage_scope.py`:

```python
import json
import shutil
from datetime import date
from pathlib import Path

import httpx
import pytest

from paper2code.agents.scoper.fake import CANARY_DRAFT
from paper2code.agents.scoper.schemas import ScopeDraft, TestFile
from paper2code.agents.scout.schemas import EligibilityBatch, Scorecard
from paper2code.arxiv.http import PoliteClient
from paper2code.config import Config
from paper2code.llm.base import LLMBadOutput, LLMError
from paper2code.llm.fake import FakeChatModel
from paper2code.manager.freeze import verify_manifest
from paper2code.manager.graph import RunContext, run_stage
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import Caps, RunRecord, create_run
from paper2code.manager.stages.scope import ATTEMPTS_FILE, read_attempts
from paper2code.manager.stages.select import SELECTED_FILE
from paper2code.manager.stages.fetch import PAPERS_FILE

FIX = Path(__file__).resolve().parent / "fixtures" / "arxiv"
IDS = ["2610.03769", "2610.03800", "2610.03727"]


def _http(html_404_for=()):
    def handler(request):
        url = str(request.url)
        if "/html/" in url and not any(i in url for i in html_404_for):
            return httpx.Response(200, content=(FIX / "paper.html").read_bytes(), request=request)
        return httpx.Response(404, request=request)

    return PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), min_interval_s=0.0, sleep=lambda s: None)


def _seed_selected(tmp_path, limit_usd=10.0, ids=IDS):
    rec = create_run(tmp_path, date(2026, 10, 7), Caps(), limit_usd)
    papers = [{"arxiv_id": i, "version": 1, "title": f"Paper {i}", "abstract": "a", "authors": [], "categories": [], "primary_category": "", "announce_type": "new", "published": "", "url": f"https://arxiv.org/abs/{i}"} for i in ids]
    (rec.run_dir / PAPERS_FILE).write_text("".join(json.dumps(p) + "\n" for p in papers), encoding="utf-8")
    shortlist = [{"pass": 2, "arxiv_id": i, "title": f"Paper {i}", "testability": 4, "difficulty": "easy", "est_gpu_hours": 0.5, "est_usd": 0.5, "claim": "c", "dataset": "d yes", "reason": "r"} for i in ids]
    (rec.run_dir / SELECTED_FILE).write_text(json.dumps({"policy": {"name": "select_v1_testability", "version": "1"}, "shortlist": shortlist}), encoding="utf-8")
    rec.stage = "select"
    rec.save()
    return rec


def _scoper(drafts_by_id=None, raise_for=None):
    """drafts_by_id: arxiv_id -> ScopeDraft (default CANARY_DRAFT). raise_for: arxiv_id -> exception."""
    drafts_by_id = drafts_by_id or {}
    raise_for = raise_for or {}

    def responder(role, instructions, user, schema):
        assert schema is ScopeDraft
        pid = user.splitlines()[0].split(None, 1)[1].strip()
        if pid in raise_for:
            raise raise_for[pid]
        return drafts_by_id.get(pid, CANARY_DRAFT)

    return FakeChatModel(responder)


def _ctx(tmp_path, llm, http=None, **cfg):
    return RunContext(config=Config(runs_root=tmp_path, run_tests_timeout_s=120, **cfg), http=http or _http(), chat_model=llm, until="scope")


def test_scope_accepts_first_paper_and_freezes(tmp_path):
    rec = _seed_selected(tmp_path)
    final = run_stage("scope", rec.run_dir, _ctx(tmp_path, _scoper()))
    assert final.stage == "scope" and final.outcome is None
    assert final.paper.arxiv_id == "2610.03769"
    scope = rec.run_dir / "scope"
    assert (scope / "spec.md").exists() and (scope / "interface.md").exists() and (scope / "manifest.json").exists()
    assert final.scope_manifest_sha256 is not None
    assert verify_manifest(scope, final.scope_manifest_sha256) == []
    rows = read_attempts(rec.run_dir)
    assert len(rows) == 1 and rows[0]["accepted"] is True and rows[0]["arxiv_id"] == "2610.03769"
    assert rows[0]["claim_tests"] == 3 and rows[0]["removed_tests"] == []
    assert final.budget.spent_tokens > 0


def test_scope_falls_through_on_trivial_claim_then_accepts_next(tmp_path):
    trivial_claim = TestFile(path="test_claim.py", content="import pytest\n@pytest.mark.parametrize('seed', [0, 1, 2])\ndef test_claim(seed):\n    assert True\n")
    bad = CANARY_DRAFT.model_copy(update={"public_tests": [CANARY_DRAFT.public_tests[0], trivial_claim]})
    rec = _seed_selected(tmp_path)
    final = run_stage("scope", rec.run_dir, _ctx(tmp_path, _scoper({"2610.03769": bad})))
    assert final.outcome is None and final.paper.arxiv_id == "2610.03800"
    rows = read_attempts(rec.run_dir)
    assert [(r["arxiv_id"], r["accepted"], r["reason"]) for r in rows] == [("2610.03769", False, "trivial_claim_test"), ("2610.03800", True, None)]
    assert "test_claim::test_claim[0]" in rows[0]["removed_tests"]


def test_scope_rejected_when_shortlist_exhausted(tmp_path):
    over = CANARY_DRAFT.model_copy(update={"est_usd": 99.0})
    rec = _seed_selected(tmp_path, limit_usd=10.0)
    final = run_stage("scope", rec.run_dir, _ctx(tmp_path, _scoper({i: over for i in IDS})))
    assert final.outcome is Outcome.SCOPE_REJECTED
    rows = read_attempts(rec.run_dir)
    assert [r["reason"] for r in rows] == ["over_budget"] * 3
    assert not (rec.run_dir / "scope").exists()


def test_scope_malformed_draft_and_fulltext_failure_are_recorded(tmp_path):
    malformed = CANARY_DRAFT.model_copy(update={"hidden_tests": []})
    rec = _seed_selected(tmp_path)
    ctx = _ctx(tmp_path, _scoper({"2610.03800": malformed}, raise_for={"2610.03727": LLMBadOutput("refusal")}), http=_http(html_404_for=("2610.03769",)))
    final = run_stage("scope", rec.run_dir, ctx)
    assert final.outcome is Outcome.SCOPE_REJECTED
    reasons = [r["reason"] for r in read_attempts(rec.run_dir)]
    assert reasons[0].startswith("fulltext_unavailable") and reasons[1].startswith("malformed_scope") and reasons[2].startswith("malformed_scope")


def test_scope_api_error_ends_run(tmp_path):
    rec = _seed_selected(tmp_path)
    final = run_stage("scope", rec.run_dir, _ctx(tmp_path, _scoper(raise_for={"2610.03769": LLMError("no credits")})))
    assert final.outcome is Outcome.ERROR and final.error.reason == "api_error" and final.error.stage == "scope"
    assert read_attempts(rec.run_dir) == []


def test_scope_resume_wipes_partial_dir_and_skips_attempted(tmp_path):
    over = CANARY_DRAFT.model_copy(update={"est_usd": 99.0})
    rec = _seed_selected(tmp_path)
    calls = {"n": 0}
    base = _scoper({"2610.03769": over})
    orig = base.responder

    def responder(role, instructions, user, schema):
        calls["n"] += 1
        if calls["n"] == 2:
            (rec.run_dir / "scope").mkdir(exist_ok=True)
            (rec.run_dir / "scope" / "leftover.txt").write_text("partial", encoding="utf-8")
            raise RuntimeError("crash mid-scope")
        return orig(role, instructions, user, schema)

    llm = FakeChatModel(responder)
    ctx = _ctx(tmp_path, llm)
    with pytest.raises(RuntimeError):
        run_stage("scope", rec.run_dir, ctx)
    assert [r["arxiv_id"] for r in read_attempts(rec.run_dir)] == ["2610.03769"]  # first attempt recorded before the crash
    final = run_stage("scope", rec.run_dir, ctx)
    assert final.outcome is None and final.paper.arxiv_id == "2610.03800"
    assert [r["arxiv_id"] for r in read_attempts(rec.run_dir)] == ["2610.03769", "2610.03800"]
    assert calls["n"] == 3  # the rejected first paper was not re-drafted
    assert not (rec.run_dir / "scope" / "leftover.txt").exists()
    assert verify_manifest(rec.run_dir / "scope", final.scope_manifest_sha256) == []


def test_scope_trivial_tests_are_pruned_before_freeze(tmp_path):
    planted = CANARY_DRAFT.model_copy(update={"public_tests": CANARY_DRAFT.public_tests + [TestFile(path="test_planted.py", content="def test_trivial():\n    assert True\n")]})
    rec = _seed_selected(tmp_path)
    final = run_stage("scope", rec.run_dir, _ctx(tmp_path, _scoper({"2610.03769": planted})))
    assert final.outcome is None
    rows = read_attempts(rec.run_dir)
    assert rows[0]["removed_tests"] == ["test_planted::test_trivial"]
    assert "test_trivial" not in (rec.run_dir / "scope" / "tests" / "public" / "test_planted.py").read_text(encoding="utf-8")
    assert verify_manifest(rec.run_dir / "scope", final.scope_manifest_sha256) == []  # frozen after pruning
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_stage_scope.py -q`
Expected: FAIL with `ImportError: cannot import name 'ATTEMPTS_FILE'`

- [ ] **Step 3: Replace `src/paper2code/manager/stages/scope.py`**

```python
"""Scope stage (spec 8): draft an assignment for the top shortlisted paper, check it, freeze it;
fall through to the next paper on rejection. Resumable: attempts are logged as they finish and a
partial scope/ from a crashed attempt is wiped before the next one."""
from __future__ import annotations

import json
import shutil

import httpx

from paper2code.agents.scoper.schemas import validate_draft
from paper2code.agents.scoper.scoper import ROLE_SCOPER, draft_scope
from paper2code.arxiv import http as arxiv_http
from paper2code.arxiv.fulltext import FullTextUnavailable, fetch_fulltext
from paper2code.arxiv.http import ArxivUnavailable
from paper2code.arxiv.models import read_papers
from paper2code.llm.base import LLMBadOutput, LLMError, Usage
from paper2code.llm.factory import make_chat_model
from paper2code.manager.freeze import freeze_scope
from paper2code.manager.graph import RunContext
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import Paper, RunError, RunRecord, utcnow
from paper2code.manager.scope_files import write_scope
from paper2code.manager.stages.fetch import PAPERS_FILE
from paper2code.manager.stages.select import SELECTED_FILE
from paper2code.manager.stubcheck import run_stub_check
from paper2code.sandbox.factory import make_runner

ATTEMPTS_FILE = "scope_attempts.jsonl"
OVER_BUDGET = "over_budget"
MALFORMED = "malformed_scope"
FULLTEXT_UNAVAILABLE = "fulltext_unavailable"


def read_attempts(run_dir) -> list[dict]:
    path = run_dir / ATTEMPTS_FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _append_attempt(run_dir, row: dict) -> None:
    with (run_dir / ATTEMPTS_FILE).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({**row, "ts": utcnow()}) + "\n")


def _model_name(llm) -> str:
    models = getattr(llm, "models", None)
    return models[ROLE_SCOPER] if models else "fake"


def run(record: RunRecord, ctx: RunContext) -> None:
    cfg = ctx.config
    run_dir = record.run_dir
    scope_dir = run_dir / "scope"
    shortlist = json.loads((run_dir / SELECTED_FILE).read_text(encoding="utf-8"))["shortlist"]
    papers = {p.arxiv_id: p for p in read_papers(run_dir / PAPERS_FILE)}
    attempted = {r["arxiv_id"] for r in read_attempts(run_dir)}
    llm = make_chat_model(ctx)
    http = arxiv_http.make_polite_client(ctx)
    runner = make_runner(ctx)
    usage = Usage()
    accepted = False
    try:
        for card in shortlist:
            arxiv_id = card["arxiv_id"]
            if arxiv_id in attempted:
                continue
            paper = papers[arxiv_id]
            row = {"arxiv_id": arxiv_id, "title": paper.title, "accepted": False, "reason": None,
                   "removed_tests": [], "claim_tests": 0, "est_usd": None, "cost_usd": 0.0, "model": _model_name(llm)}
            shutil.rmtree(scope_dir, ignore_errors=True)
            try:
                fulltext = fetch_fulltext(arxiv_id, http, cfg.max_fulltext_chars)
            except (FullTextUnavailable, ArxivUnavailable, httpx.HTTPError) as exc:
                _append_attempt(run_dir, {**row, "reason": f"{FULLTEXT_UNAVAILABLE}: {exc}"})
                continue
            before = usage.cost_usd
            try:
                draft = draft_scope(paper, card, fulltext.text, llm, cfg, record.budget.limit_usd, usage)
            except LLMBadOutput as exc:
                _append_attempt(run_dir, {**row, "reason": f"{MALFORMED}: {exc}", "cost_usd": round(usage.cost_usd - before, 6)})
                continue
            row["cost_usd"] = round(usage.cost_usd - before, 6)
            row["est_usd"] = draft.est_usd
            problems = validate_draft(draft)
            if problems:
                _append_attempt(run_dir, {**row, "reason": f"{MALFORMED}: {'; '.join(problems)}"})
                continue
            write_scope(scope_dir, draft)
            check = run_stub_check(scope_dir, draft.interface, runner, cfg.min_seeds)
            row["removed_tests"] = check.removed
            row["claim_tests"] = len(check.claim_test_ids)
            if check.reject_reason is not None:
                _append_attempt(run_dir, {**row, "reason": check.reject_reason})
                shutil.rmtree(scope_dir, ignore_errors=True)
                continue
            if draft.est_usd > record.budget.limit_usd:
                _append_attempt(run_dir, {**row, "reason": OVER_BUDGET})
                shutil.rmtree(scope_dir, ignore_errors=True)
                continue
            freeze_scope(record)
            record.paper = Paper(arxiv_id=arxiv_id, title=paper.title, url=paper.url)
            _append_attempt(run_dir, {**row, "accepted": True})
            accepted = True
            break
    except LLMError as exc:
        record.outcome = Outcome.ERROR
        record.error = RunError(stage="scope", reason="api_error", message=str(exc))
    finally:
        record.budget.spent_usd += usage.cost_usd
        record.budget.spent_tokens += usage.input_tokens + usage.output_tokens
    if record.outcome is None and not accepted:
        record.outcome = Outcome.SCOPE_REJECTED
```

- [ ] **Step 4: Run the tests to verify they pass, then the whole suite**

Run: `pytest tests/test_stage_scope.py -q` then `pytest -q`
Expected: 7 pass; suite green. The step 1 graph test `test_default_scope_stage_is_not_implemented_yet` now fails because scope is real: replace it with

```python
def test_default_build_requires_a_scope(tmp_path):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    rec.stage = "scope"
    rec.save()
    with pytest.raises(ValueError, match="reference_dir"):
        run_pipeline(rec.run_dir, _ctx(tmp_path))
```

(the default build stage with builder `stub` and no reference raises `ValueError` from `make_builder`).

- [ ] **Step 5: Commit**

```bash
git add src/paper2code/manager/stages/scope.py tests/test_stage_scope.py tests/test_graph.py
git commit -m "Add the scope stage: draft, validate, stub check, feasibility, freeze, fall-through

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: CLI, forced-eligible single-paper path, end-to-end fake pipeline, docs

**Files:**
- Modify: `src/paper2code/manager/graph.py` (RunContext gains `force_eligible: bool = False`)
- Modify: `src/paper2code/manager/stages/score.py` (forced path)
- Modify: `src/paper2code/cli.py`
- Modify: `tests/test_cli.py`, `tests/test_stages_scout.py`
- Create: `tests/test_canary_e2e_fake.py`
- Modify: `README.md`, `decisions.md` (journal entry at the end, from the ledger)

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `RunContext.force_eligible: bool = False`. When true, `score.run` skips pass one, writes pass-one rows with `model: "forced"`, `eligible: True`, `reason: ""`, `confidence: 1.0`, and runs pass two on every paper (capped by `max_fulltext_candidates`).
  - CLI: `scope` joins the single-stage commands; `scope --arxiv-id ID [--llm] [--runs-root] [--date]` creates a run with that paper, runs `score` with `force_eligible`, then `select`, then `scope`. `run --until scope` works without GPU flags.
  - End-to-end: `new-run`, then `run --until scope --llm fake` (patched arXiv HTTP) produces a frozen canary scope; then `run --no-gpu --builder stub --reference tests/fixtures/canary/reference` carries it to `completed`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_stages_scout.py`:

```python
def test_force_eligible_skips_pass_one(tmp_path):
    rec = _new_run(tmp_path)
    llm = _scout(set())  # pass one would reject everything
    ctx = RunContext(config=Config(runs_root=tmp_path, categories=["cs.LG", "stat.ML"], max_fulltext_candidates=2), http=_http(), chat_model=llm, until="select", force_eligible=True)
    run_stage("fetch", rec.run_dir, ctx)
    final = run_stage("score", rec.run_dir, ctx)
    assert final.outcome is None
    rows = candidates.read_rows(rec.run_dir / candidates.CANDIDATES_FILE)
    assert all(r["model"] == "forced" and r["eligible"] for r in rows if r["pass"] == 1)
    assert len([r for r in rows if r["pass"] == 2]) == 2
    assert not [c for c in llm.calls if c[2] is EligibilityBatch]
```

Append to `tests/test_cli.py`:

```python
def test_run_until_scope_with_fake_llm_freezes_canary(tmp_path, monkeypatch, capsys):
    _patch_http(monkeypatch)
    main(["new-run", "--runs-root", str(tmp_path), "--date", "2026-10-07"])
    run_dir = tmp_path / "2026-10-07"
    assert main(["run", "--run", str(run_dir), "--until", "scope", "--llm", "fake"]) == 0
    rec = RunRecord.load(run_dir)
    assert rec.stage == "scope" and rec.outcome is None
    assert (run_dir / "scope" / "manifest.json").exists()
    assert (run_dir / "scope_attempts.jsonl").exists()


def test_scope_by_arxiv_id_creates_scoped_run(tmp_path, monkeypatch, capsys):
    _patch_http(monkeypatch)
    assert main(["scope", "--arxiv-id", "2610.03769", "--llm", "fake", "--runs-root", str(tmp_path), "--date", "2026-10-07"]) == 0
    run_dir = tmp_path / "2026-10-07"
    rec = RunRecord.load(run_dir)
    assert rec.stage == "scope" and rec.outcome is None and rec.paper.arxiv_id == "2610.03769"
    rows = (run_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
    assert '"model": "forced"' in rows[0]
    assert "stage: scope" in capsys.readouterr().out
```

`tests/test_canary_e2e_fake.py`:

```python
"""Spec 15 and 16: the whole pipeline from fetch to completed with no model and no GPU."""
from pathlib import Path

import httpx

from paper2code.arxiv.http import PoliteClient
from paper2code.cli import main
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import RunRecord
from paper2code.manager.verdict import Verdict

FIX = Path(__file__).resolve().parent / "fixtures" / "arxiv"


def _patch_http(monkeypatch):
    def handler(request):
        url = str(request.url)
        if url.startswith("https://rss.arxiv.org/rss/cs.LG"):
            return httpx.Response(200, content=(FIX / "rss_cs_LG.xml").read_bytes(), request=request)
        if url.startswith("https://rss.arxiv.org/rss/"):
            return httpx.Response(200, content=b'<rss version="2.0"><channel><title>x</title></channel></rss>', request=request)
        if "/html/" in url:
            return httpx.Response(200, content=(FIX / "paper.html").read_bytes(), request=request)
        return httpx.Response(404, request=request)

    client = PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), min_interval_s=0.0, sleep=lambda s: None)
    monkeypatch.setattr("paper2code.arxiv.http.make_polite_client", lambda ctx: client)


def test_fake_pipeline_reaches_completed(tmp_path, canary_dir, monkeypatch):
    _patch_http(monkeypatch)
    assert main(["new-run", "--runs-root", str(tmp_path), "--date", "2026-10-07"]) == 0
    run_dir = tmp_path / "2026-10-07"
    assert main(["run", "--run", str(run_dir), "--until", "scope", "--llm", "fake"]) == 0
    assert RunRecord.load(run_dir).stage == "scope"
    assert main(["run", "--run", str(run_dir), "--no-gpu", "--builder", "stub", "--reference", str(canary_dir / "reference"), "--llm", "fake"]) == 0
    rec = RunRecord.load(run_dir)
    assert rec.outcome is Outcome.COMPLETED and rec.stage == "report"
    assert Verdict.load(run_dir).hidden_failed == []
    assert sorted(p.name for p in run_dir.iterdir()) == [
        "build.log", "candidates.jsonl", "papers.jsonl", "run.json", "scope", "scope_attempts.jsonl",
        "selected.json", "summary.md", "verdict.json", "workspace",
    ]


def test_fake_pipeline_catches_hardcoded_builder(tmp_path, canary_dir, monkeypatch):
    _patch_http(monkeypatch)
    main(["new-run", "--runs-root", str(tmp_path), "--date", "2026-10-07"])
    run_dir = tmp_path / "2026-10-07"
    assert main(["run", "--run", str(run_dir), "--no-gpu", "--builder", "stub", "--reference", str(canary_dir / "hardcoded"), "--llm", "fake"]) == 0
    assert RunRecord.load(run_dir).outcome is Outcome.HIDDEN_FAILED
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_stages_scout.py tests/test_cli.py tests/test_canary_e2e_fake.py -q`
Expected: `TypeError: ... unexpected keyword argument 'force_eligible'`, `SystemExit: 2` for `scope`, and the e2e tests failing at `run --until scope` because `scope` is not a valid `--until`... (it is: `STAGES` includes it) so they fail on `--llm fake` reaching the stub builder requiring `--reference`; read the actual output and confirm each fails for a reason the change below addresses.

- [ ] **Step 3: Add `force_eligible` to `RunContext` and the forced path in `score.py`**

In `src/paper2code/manager/graph.py` add to `RunContext` after `chat_model`:

```python
    force_eligible: bool = False  # local mode: skip scout pass one, treat every fetched paper as eligible
```

In `src/paper2code/manager/stages/score.py`, inside the `try:` replace

```python
        verdicts = _verdicts_from_rows(papers, existing)
        if verdicts is None:
```

with

```python
        verdicts = _verdicts_from_rows(papers, existing)
        if verdicts is None and ctx.force_eligible:
            verdicts = [EligibilityVerdict(arxiv_id=p.arxiv_id, eligible=True, reason="", confidence=1.0) for p in papers]
            candidates.append_rows(out, [candidates.pass_one_row(p, v, "forced") for p, v in zip(papers, verdicts)])
        elif verdicts is None:
```

- [ ] **Step 4: Update `src/paper2code/cli.py`**

Change `STAGE_COMMANDS` to `("fetch", "score", "select", "scope", "build", "inspect", "report")`.

In `build_parser`, change the single-paper arguments so both `score` and `scope` get them:

```python
        if name in ("score", "scope"):
            sp.add_argument("--arxiv-id", help="work on this one paper in a fresh run instead of --run")
            sp.add_argument("--runs-root", type=Path)
            sp.add_argument("--date", type=date.fromisoformat, default=None)
```

In `main`, replace the `score --arxiv-id` block with:

```python
    if args.command in ("score", "scope") and getattr(args, "arxiv_id", None):
        runs_root = args.runs_root if args.runs_root is not None else ctx.config.runs_root
        try:
            paper = fetch_by_id(args.arxiv_id, arxiv_http.make_polite_client(ctx))
        except Exception as exc:
            print(f"{args.command} failed: could not fetch {args.arxiv_id}: {exc}", file=sys.stderr)
            return 1
        record = init_run_for_paper(runs_root, paper, args.date or date.today(), ctx.config)
        print(f"created {record.run_dir}")
        args.run = record.run_dir
        if args.command == "scope":
            forced = RunContext(**{**ctx.__dict__, "force_eligible": True})
            try:
                run_stage("score", args.run, forced)
                run_stage("select", args.run, forced)
            except Exception as exc:
                print(f"scope failed before scoping: {exc}", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                return 1
    elif args.run is None:
        parser.error("--run DIR is required")
```

(`RunContext` is a frozen dataclass; `ctx.__dict__` holds its fields, so this builds a copy with one field changed.)

- [ ] **Step 5: Run the tests to verify they pass, then the whole suite**

Run: `pytest tests/test_stages_scout.py tests/test_cli.py tests/test_canary_e2e_fake.py -q` then `pytest -q`
Expected: all pass; the e2e file takes about a minute (two stub checks plus builds).

- [ ] **Step 6: Live scope on a real paper (spends about 0.30 USD)**

Run from the repo root:

```bash
paper2code scope --arxiv-id 2610.07324 --runs-root runs-live
cat runs-live/$(date +%F)/scope_attempts.jsonl
ls runs-live/$(date +%F)/scope/tests/public runs-live/$(date +%F)/scope/tests/hidden
```

This is the step 2 live run's top pick (ScaleIn, testability 5). Expected: `stage: scope  outcome: none` with one accepted attempt, or an honest rejection row with its reason. Either is a valid result for the plan; record which in the ledger with the cost from `run.json`, the number of claim tests, and the removed tests. Read `spec.md` and the claim test and note in the ledger, in two sentences, whether a competent engineer could implement it from the spec alone. Move the run directory to the session scratchpad afterwards.

- [ ] **Step 7: Update `README.md` and write the journal entry**

In `README.md`, change the Status paragraph to:

```markdown
Build step 3 of 6: fetch, scout, select and scope work against live arXiv. A
scoped run can be carried to completion with the stub builder; the real
builder (Agent SDK in a Modal sandbox) is step 4. Scout and scoper calls go
to OpenAI; `--llm fake` runs the whole pipeline on a canned canary assignment
with no model.
```

and add to the Local mode block, after the `select` line:

```bash
paper2code scope  --run runs/<date>                         # draft, stub-check, freeze the top pick
paper2code scope  --arxiv-id 2610.07324                     # fresh run: score (forced eligible), select, scope
paper2code run --run runs/<date> --until scope --llm fake   # whole front half with the canned canary
```

and in the run-directory sentence add `scope_attempts.jsonl` after `selected.json`.

Append the step 3 entry to `decisions.md` from the ledger: what was built, the live scope result and what the assignment looked like, surprises, review findings and fixes, rulings, deferred minors, and any blog-worthy lesson.

- [ ] **Step 8: Commit**

```bash
git add src/paper2code/manager/graph.py src/paper2code/manager/stages/score.py src/paper2code/cli.py tests/test_stages_scout.py tests/test_cli.py tests/test_canary_e2e_fake.py README.md decisions.md
git commit -m "CLI scope command and scope --arxiv-id; forced-eligible single-paper path; fake pipeline to completed

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review notes

**Spec coverage for build step 3.** Section 8 fall-through on rejection and `scope_rejected` on exhaustion: Task 5. Section 8.1 inputs and the structural "write under scope/ only" rule: Tasks 2 and 3 (the model returns data; `write_scope` validates paths before writing). Section 8.2 outputs: Tasks 2 and 3 (`spec.md`, `interface.md`, public with claim test, hidden variants). Section 8.3 stub check with `trivial_test_removed` logging, trivial claim rejection, `insufficient_seeds`: Task 4, logged per attempt in Task 5. Section 8.4 feasibility `over_budget`: Task 5. Section 8.5 freeze and anchor: Tasks 1 and 5. Section 14 `scope --arxiv-id`: Task 6. Section 15 trivial-test canary: Task 4 (`test_trivial_test_is_removed_and_logged`) and Task 5 (`test_scope_trivial_tests_are_pruned_before_freeze`); the full fake pipeline to `completed` and the hardcoded-builder catch: Task 6.

**Deviations recorded.** `scope_attempts.jsonl` is a new run-record file (resume and observability). `tests_do_not_collect` and `no_tests` are rejection reasons the spec does not list; `malformed_scope` and `fulltext_unavailable` likewise. The interface is structured data rendered to `interface.md` by the manager, rather than free Markdown written by the model, so stubs can be generated reliably. The stub check runs in the local environment, so the scoper is restricted to `allowed_packages`. `force_eligible` is a local-mode hook for `scope --arxiv-id`.

**Type consistency checked.** `ScopeDraft` field names are used identically in Tasks 2, 3, 4, 5. `run_stub_check(scope_dir, interface, runner, min_seeds) -> StubCheckResult` with `.removed`, `.claim_test_ids`, `.reject_reason` matches Task 5. `draft_scope(paper, scorecard, fulltext, llm, cfg, budget_usd, usage)` matches Task 5's call. `freeze_scope(record)` from Task 1 is used in Task 5. `TestRunResult.errored` from Task 1 is used in Task 4. `RunContext.force_eligible` from Task 6 is read in `score.py` in Task 6.

**Review Focus pinned.** 1 → Task 4 `test_stub_check_rejects_uncollectable_tests`; 2 → Task 2 `test_validate_draft_rejects_unsafe_paths`; 3 → Task 5 `test_scope_resume_wipes_partial_dir_and_skips_attempted`; 4 → Task 5 `test_scope_rejected_when_shortlist_exhausted`; 5 → Task 4 `test_stub_check_counts_claim_seeds`.
