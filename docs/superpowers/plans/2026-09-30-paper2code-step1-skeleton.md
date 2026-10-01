# paper2code Step 1: Run Record, Pipeline Skeleton, Local CLI, Canary — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build step 1 of the spec's build order: the run record, a LangGraph pipeline skeleton with resumable stage nodes, a local-mode CLI, and a hand-written canary scope that the pipeline carries from `scope` to `completed` using a stub builder.

**Architecture:** A LangGraph `StateGraph` with one node per stage (fetch, score, select, scope, build, inspect, report). The graph state carries only the run directory path; `run.json` on disk is the single source of truth and is rewritten after every node, so a crashed run resumes at the last completed stage. In this step the fetch, score, select and scope nodes are stubs that raise `NotImplementedError`; a local-mode helper seeds a run directly at the `scope` stage from a pre-written scope directory. The build node drives a `Builder` through a manager-owned `BuildSession` (which alone writes `build.log`), the inspect node re-hashes `scope/`, runs the hidden tests in a local subprocess, and applies the mechanical verdict rule, and the report node writes `summary.md`.

**Tech Stack:** Python 3.11+, `langgraph` (1.x; the `StateGraph`/`START`/`END` API used here is unchanged since 0.2), `pyyaml`, `pytest`. No OpenAI, Agent SDK, or Modal in this step.

**Spec:** `docs/superpowers/specs/2026-09-30-paper2code-design.md`

**Later plans (not this one):** step 2 fetch/scout/select, step 3 scoper + stub check + feasibility, step 4 Agent SDK builder in Modal with caps, step 5 inspector LLM review + adversarial canaries that need it, step 6 scheduling, dashboard, push to the `runs/` repo.

## Global Constraints

- Python `>=3.11` (uses `StrEnum`, `X | None` unions).
- Package code lives under `src/paper2code/` and is imported as `paper2code.*`. The spec's section 13 tree shows `manager/`, `agents/` etc. at the repo root; they live one level down under the package so imports are namespaced. The relative layout within the package matches the spec exactly.
- Run directories are named `runs/YYYY-MM-DD/`; a second run on the same day gets a numeric suffix `runs/YYYY-MM-DD-2/`, then `-3`, and so on (spec section 4).
- `run.json` fields are exactly (spec section 4.2): `run_id, started_at, finished_at, stage, outcome, paper {arxiv_id, title, url}, policy {name, version}, budget {limit_usd, spent_usd, spent_tokens, gpu_seconds}, caps {test_runs, wall_clock_s, stall_n}, counters {test_runs_used, attempts}, error {stage, reason, message} | null`.
- Outcome values are exactly (spec section 4.1): `completed, completed_suspicious, hidden_failed, tests_tampered, incomplete_budget, incomplete_stuck, scope_rejected, no_candidates, error`.
- Verdict rule is mechanical (spec section 10.4): hashes mismatch → `tests_tampered`; else hidden tests fail → `hidden_failed`; else no flags → `completed`; else `completed_suspicious`.
- `build.log` is written only by the manager, one entry per `run_tests` call plus session events (spec section 9.2).
- `manifest.json` holds the sha256 of every file under `scope/` at freeze time (spec section 4), and the inspector re-hashes at the end (spec section 8.5).
- Timestamps are ISO 8601 in UTC.
- Cap defaults (spec section 9.3): `test_runs` 25, `wall_clock_s` 7200, `stall_n` 5. Per-run GPU budget default 10 USD (spec section 12). `max_fulltext_candidates` 10 (spec section 6).
- Commit after every task. Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Review Focus

Failure modes the spec implies but does not spell out. Each line's test is pinned to the task named.

1. **A workspace missing a module named in `interface.md`** makes pytest fail at collection with zero tests run. The runner must report that as not-passed, never as "no failures, therefore passed". Pinned to Task 4 (`test_missing_module_is_not_all_passed`).
2. **A test that hangs** (runaway experiment) must not hang the whole run. The runner enforces a per-call timeout and reports `timed_out=True`, which counts as not-passed. Pinned to Task 4 (`test_timeout_is_reported_not_hung`).
3. **A file added to or deleted from `scope/` after freeze**, not just a modified one, must be caught by integrity verification. Pinned to Task 3 (`test_added_file_detected`, `test_deleted_file_detected`).
4. **A manual rerun on the same day** must not overwrite that day's existing run directory. Pinned to Task 2 (`test_second_run_same_day_gets_suffix`).
5. **A crash in the middle of a stage** must cause that stage to re-run on resume, not be skipped, because `stage` in `run.json` is advanced only after the node's work finishes. Pinned to Task 6 (`test_crash_mid_stage_reruns_that_stage`).

---

### Task 1: Project scaffold and config loader

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `config.yaml`
- Create: `src/paper2code/__init__.py`
- Create: `src/paper2code/config.py`
- Create: `tests/__init__.py` (empty)
- Create: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `paper2code.config.Config` (frozen dataclass with fields below), `CapsConfig`, `BudgetConfig`, `load_config(path: Path) -> Config`. Every field of `Config` has a default so `Config()` is valid in tests.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "paper2code"
version = "0.1.0"
description = "Daily unattended loop: pick an arXiv paper, scope it, build it against tests, inspect the result."
requires-python = ">=3.11"
dependencies = [
    "langgraph>=0.2",
    "pyyaml>=6",
]

[project.optional-dependencies]
dev = ["pytest>=8"]

[project.scripts]
paper2code = "paper2code.cli:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-p no:cacheprovider"
```

- [ ] **Step 2: Write `.gitignore`**

```
__pycache__/
*.pyc
.pytest_cache/
*.egg-info/
build/
dist/
.venv/
runs/
```

- [ ] **Step 3: Write `config.yaml`**

```yaml
# paper2code configuration. Every key here has a matching field on paper2code.config.Config.
runs_root: runs
categories: [cs.LG, cs.CL, stat.ML]
budget:
  limit_usd: 10.0
caps:
  test_runs: 25
  wall_clock_s: 7200      # 2 h on Max 5x; use 14400 on Max 20x
  stall_n: 5
min_seeds: 3
max_fulltext_candidates: 10
run_tests_timeout_s: 900
gpu_type: T4
max_tier: 5x
policy: select_v1_testability
models:
  scout_pass1: ""         # chosen at build step 2 from OpenAI's model list
  scout_pass2: ""
  scoper: ""
  inspector: ""
  builder: ""             # empty = Agent SDK default under the subscription
```

- [ ] **Step 4: Write the failing config test**

`tests/test_config.py`:

```python
from pathlib import Path

from paper2code.config import Config, load_config

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_default_config_is_valid():
    cfg = Config()
    assert cfg.caps.test_runs == 25
    assert cfg.budget.limit_usd == 10.0


def test_load_repo_config_yaml():
    cfg = load_config(REPO_ROOT / "config.yaml")
    assert cfg.categories == ["cs.LG", "cs.CL", "stat.ML"]
    assert cfg.caps.wall_clock_s == 7200
    assert cfg.caps.stall_n == 5
    assert cfg.min_seeds == 3
    assert cfg.max_fulltext_candidates == 10
    assert cfg.run_tests_timeout_s == 900
    assert cfg.policy == "select_v1_testability"
    assert cfg.runs_root == Path("runs")


def test_load_partial_yaml_uses_defaults(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("caps:\n  test_runs: 3\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.caps.test_runs == 3
    assert cfg.caps.stall_n == 5
    assert cfg.budget.limit_usd == 10.0
```

- [ ] **Step 5: Run the test to verify it fails**

Run: `pip install -e ".[dev]"` then `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'paper2code.config'`

- [ ] **Step 6: Write `src/paper2code/__init__.py`**

```python
"""paper2code: daily unattended paper-to-experiment loop."""
```

- [ ] **Step 7: Write `src/paper2code/config.py`**

```python
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class BudgetConfig:
    limit_usd: float = 10.0


@dataclass(frozen=True)
class CapsConfig:
    test_runs: int = 25
    wall_clock_s: int = 7200
    stall_n: int = 5


@dataclass(frozen=True)
class Config:
    runs_root: Path = Path("runs")
    categories: list[str] = field(default_factory=lambda: ["cs.LG", "cs.CL", "stat.ML"])
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    caps: CapsConfig = field(default_factory=CapsConfig)
    min_seeds: int = 3
    max_fulltext_candidates: int = 10
    run_tests_timeout_s: int = 900
    gpu_type: str = "T4"
    max_tier: str = "5x"
    policy: str = "select_v1_testability"
    models: dict[str, str] = field(default_factory=dict)


def load_config(path: Path) -> Config:
    """Load config.yaml. Missing keys fall back to the dataclass defaults."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = Config()
    return Config(
        runs_root=Path(raw.get("runs_root", defaults.runs_root)),
        categories=list(raw.get("categories", defaults.categories)),
        budget=BudgetConfig(**{**asdict(defaults.budget), **(raw.get("budget") or {})}),
        caps=CapsConfig(**{**asdict(defaults.caps), **(raw.get("caps") or {})}),
        min_seeds=int(raw.get("min_seeds", defaults.min_seeds)),
        max_fulltext_candidates=int(raw.get("max_fulltext_candidates", defaults.max_fulltext_candidates)),
        run_tests_timeout_s=int(raw.get("run_tests_timeout_s", defaults.run_tests_timeout_s)),
        gpu_type=str(raw.get("gpu_type", defaults.gpu_type)),
        max_tier=str(raw.get("max_tier", defaults.max_tier)),
        policy=str(raw.get("policy", defaults.policy)),
        models={k: str(v) for k, v in (raw.get("models") or {}).items()},
    )
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: 3 PASS

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml .gitignore config.yaml src/paper2code/__init__.py src/paper2code/config.py tests/__init__.py tests/test_config.py
git commit -m "Scaffold package, config loader and config.yaml

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Outcomes and the run record

**Files:**
- Create: `src/paper2code/manager/__init__.py` (empty)
- Create: `src/paper2code/manager/outcomes.py`
- Create: `src/paper2code/manager/record.py`
- Create: `tests/test_record.py`

**Interfaces:**
- Consumes: nothing. The record has its own `Caps` dataclass (same field names as `CapsConfig`) so the record never depends on config loading.
- Produces:
  - `paper2code.manager.outcomes.Outcome` (`StrEnum`, nine members).
  - `paper2code.manager.record.STAGES: tuple[str, ...]` = `("fetch", "score", "select", "scope", "build", "inspect", "report")`.
  - `stage_index(stage: str | None) -> int` (`-1` for `None`).
  - `utcnow() -> str`.
  - Dataclasses `Paper`, `Policy`, `Budget`, `Caps`, `Counters`, `RunError`, `RunRecord`.
  - `RunRecord.is_done(stage: str) -> bool`, `RunRecord.save() -> None`, `RunRecord.load(run_dir: Path) -> RunRecord`, `RunRecord.to_dict() -> dict`, `RunRecord.from_dict(run_dir: Path, d: dict) -> RunRecord`.
  - `create_run(runs_root: Path, today: date, caps: Caps, limit_usd: float) -> RunRecord`.

- [ ] **Step 1: Write the failing tests**

`tests/test_record.py`:

```python
import json
from datetime import date

import pytest

from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import (
    STAGES,
    Caps,
    Paper,
    RunError,
    RunRecord,
    create_run,
    stage_index,
)

TODAY = date(2026, 9, 30)


def test_outcome_values_match_spec():
    assert {o.value for o in Outcome} == {
        "completed", "completed_suspicious", "hidden_failed", "tests_tampered",
        "incomplete_budget", "incomplete_stuck", "scope_rejected", "no_candidates", "error",
    }


def test_stage_order():
    assert STAGES == ("fetch", "score", "select", "scope", "build", "inspect", "report")
    assert stage_index(None) == -1
    assert stage_index("fetch") == 0
    assert stage_index("report") == 6


def test_create_run_writes_run_json_with_spec_fields(tmp_path):
    rec = create_run(tmp_path, TODAY, Caps(), limit_usd=10.0)
    assert rec.run_dir == tmp_path / "2026-09-30"
    data = json.loads((rec.run_dir / "run.json").read_text(encoding="utf-8"))
    assert set(data) == {
        "run_id", "started_at", "finished_at", "stage", "outcome", "paper", "policy",
        "budget", "caps", "counters", "error",
    }
    assert data["run_id"] == "2026-09-30"
    assert data["stage"] is None
    assert data["outcome"] is None
    assert data["error"] is None
    assert set(data["paper"]) == {"arxiv_id", "title", "url"}
    assert set(data["policy"]) == {"name", "version"}
    assert set(data["budget"]) == {"limit_usd", "spent_usd", "spent_tokens", "gpu_seconds"}
    assert set(data["caps"]) == {"test_runs", "wall_clock_s", "stall_n"}
    assert set(data["counters"]) == {"test_runs_used", "attempts"}
    assert data["budget"]["limit_usd"] == 10.0
    assert data["caps"]["test_runs"] == 25


def test_second_run_same_day_gets_suffix(tmp_path):
    first = create_run(tmp_path, TODAY, Caps(), 10.0)
    (first.run_dir / "marker").write_text("keep me", encoding="utf-8")
    second = create_run(tmp_path, TODAY, Caps(), 10.0)
    third = create_run(tmp_path, TODAY, Caps(), 10.0)
    assert second.run_dir == tmp_path / "2026-09-30-2"
    assert third.run_dir == tmp_path / "2026-09-30-3"
    assert (first.run_dir / "marker").read_text(encoding="utf-8") == "keep me"


def test_save_and_load_roundtrip(tmp_path):
    rec = create_run(tmp_path, TODAY, Caps(test_runs=3), 5.0)
    rec.stage = "build"
    rec.outcome = Outcome.INCOMPLETE_STUCK
    rec.paper = Paper(arxiv_id="2509.12345", title="T", url="https://arxiv.org/abs/2509.12345")
    rec.counters.test_runs_used = 2
    rec.budget.gpu_seconds = 12.5
    rec.error = RunError(stage="build", reason="rate_limited", message="window exhausted")
    rec.save()

    loaded = RunRecord.load(rec.run_dir)
    assert loaded.run_dir == rec.run_dir
    assert loaded.stage == "build"
    assert loaded.outcome is Outcome.INCOMPLETE_STUCK
    assert loaded.paper.arxiv_id == "2509.12345"
    assert loaded.counters.test_runs_used == 2
    assert loaded.budget.gpu_seconds == 12.5
    assert loaded.caps.test_runs == 3
    assert loaded.error == RunError("build", "rate_limited", "window exhausted")


def test_is_done_compares_stage_order(tmp_path):
    rec = create_run(tmp_path, TODAY, Caps(), 10.0)
    assert not rec.is_done("fetch")
    rec.stage = "scope"
    assert rec.is_done("fetch")
    assert rec.is_done("scope")
    assert not rec.is_done("build")


def test_save_leaves_no_temp_file(tmp_path):
    rec = create_run(tmp_path, TODAY, Caps(), 10.0)
    rec.save()
    assert sorted(p.name for p in rec.run_dir.iterdir()) == ["run.json"]


def test_load_missing_run_json_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        RunRecord.load(tmp_path / "nope")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_record.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'paper2code.manager'`

- [ ] **Step 3: Write `src/paper2code/manager/outcomes.py`**

```python
from enum import StrEnum


class Outcome(StrEnum):
    """Fixed outcome vocabulary from spec section 4.1. Incomplete is normal, not an error."""

    COMPLETED = "completed"
    COMPLETED_SUSPICIOUS = "completed_suspicious"
    HIDDEN_FAILED = "hidden_failed"
    TESTS_TAMPERED = "tests_tampered"
    INCOMPLETE_BUDGET = "incomplete_budget"
    INCOMPLETE_STUCK = "incomplete_stuck"
    SCOPE_REJECTED = "scope_rejected"
    NO_CANDIDATES = "no_candidates"
    ERROR = "error"
```

- [ ] **Step 4: Write `src/paper2code/manager/record.py`**

```python
"""The run record: one directory per run, run.json as the single source of truth."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from paper2code.manager.outcomes import Outcome

STAGES: tuple[str, ...] = ("fetch", "score", "select", "scope", "build", "inspect", "report")
RUN_JSON = "run.json"


def stage_index(stage: str | None) -> int:
    """Position of a stage in the pipeline; -1 means no stage has completed."""
    return -1 if stage is None else STAGES.index(stage)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Paper:
    arxiv_id: str = ""
    title: str = ""
    url: str = ""


@dataclass
class Policy:
    name: str = ""
    version: str = ""


@dataclass
class Budget:
    limit_usd: float = 0.0
    spent_usd: float = 0.0
    spent_tokens: int = 0
    gpu_seconds: float = 0.0


@dataclass
class Caps:
    test_runs: int = 25
    wall_clock_s: int = 7200
    stall_n: int = 5


@dataclass
class Counters:
    test_runs_used: int = 0
    attempts: int = 0


@dataclass
class RunError:
    stage: str
    reason: str
    message: str


@dataclass
class RunRecord:
    run_dir: Path
    run_id: str
    started_at: str
    finished_at: str | None = None
    stage: str | None = None
    outcome: Outcome | None = None
    paper: Paper = field(default_factory=Paper)
    policy: Policy = field(default_factory=Policy)
    budget: Budget = field(default_factory=Budget)
    caps: Caps = field(default_factory=Caps)
    counters: Counters = field(default_factory=Counters)
    error: RunError | None = None

    def is_done(self, stage: str) -> bool:
        """True if `stage` (or a later one) has already completed for this run."""
        return stage_index(self.stage) >= stage_index(stage)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("run_dir")
        d["outcome"] = self.outcome.value if self.outcome is not None else None
        return d

    @classmethod
    def from_dict(cls, run_dir: Path, d: dict) -> "RunRecord":
        return cls(
            run_dir=run_dir,
            run_id=d["run_id"],
            started_at=d["started_at"],
            finished_at=d.get("finished_at"),
            stage=d.get("stage"),
            outcome=Outcome(d["outcome"]) if d.get("outcome") else None,
            paper=Paper(**d["paper"]),
            policy=Policy(**d["policy"]),
            budget=Budget(**d["budget"]),
            caps=Caps(**d["caps"]),
            counters=Counters(**d["counters"]),
            error=RunError(**d["error"]) if d.get("error") else None,
        )

    def save(self) -> None:
        """Atomic write: a crash mid-write never leaves a torn run.json."""
        target = self.run_dir / RUN_JSON
        tmp = self.run_dir / (RUN_JSON + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        os.replace(tmp, target)

    @classmethod
    def load(cls, run_dir: Path) -> "RunRecord":
        path = run_dir / RUN_JSON
        if not path.exists():
            raise FileNotFoundError(f"no {RUN_JSON} in {run_dir}")
        return cls.from_dict(run_dir, json.loads(path.read_text(encoding="utf-8")))


def create_run(runs_root: Path, today: date, caps: Caps, limit_usd: float) -> RunRecord:
    """Create runs/YYYY-MM-DD (or -2, -3, ... if that day already has a run) and write run.json."""
    base = today.isoformat()
    run_id = base
    n = 1
    while (runs_root / run_id).exists():
        n += 1
        run_id = f"{base}-{n}"
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True)
    record = RunRecord(
        run_dir=run_dir,
        run_id=run_id,
        started_at=utcnow(),
        caps=caps,
        budget=Budget(limit_usd=limit_usd),
    )
    record.save()
    return record
```

Also create the empty `src/paper2code/manager/__init__.py`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_record.py -v`
Expected: 8 PASS

- [ ] **Step 6: Commit**

```bash
git add src/paper2code/manager tests/test_record.py
git commit -m "Add outcome enum and run record with atomic save and same-day suffixing

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Freeze and integrity verification

**Files:**
- Create: `src/paper2code/manager/freeze.py`
- Create: `tests/test_freeze.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `MANIFEST_NAME = "manifest.json"`.
  - `hash_tree(root: Path) -> dict[str, str]` mapping posix relative path to sha256 hex, excluding the top-level manifest and anything under `__pycache__` or `.pytest_cache`.
  - `write_manifest(scope_dir: Path) -> dict[str, str]`.
  - `read_manifest(scope_dir: Path) -> dict[str, str]`.
  - `verify_manifest(scope_dir: Path) -> list[str]`: sorted relative paths that are modified, added, or missing. Empty list means intact.

- [ ] **Step 1: Write the failing tests**

`tests/test_freeze.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_freeze.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'paper2code.manager.freeze'`

- [ ] **Step 3: Write `src/paper2code/manager/freeze.py`**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_freeze.py -v`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add src/paper2code/manager/freeze.py tests/test_freeze.py
git commit -m "Add scope freeze: manifest hashing and integrity verification

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Local test runner

**Files:**
- Create: `src/paper2code/sandbox/__init__.py` (empty)
- Create: `src/paper2code/sandbox/runner.py`
- Create: `tests/test_runner.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `TestRunResult` frozen dataclass: `passed: tuple[str, ...]`, `failed: tuple[str, ...]`, `returncode: int`, `timed_out: bool`, `duration_s: float`, `gpu_seconds: float`, `output: str`; properties `all_passed: bool` and `failing_set: frozenset[str]`.
  - `TestRunner` Protocol with `run(self, workspace: Path, tests_dir: Path) -> TestRunResult`.
  - `LocalTestRunner(timeout_s: int)` implementing it in a subprocess over a snapshot copy.
  - `parse_junit(path: Path) -> tuple[tuple[str, ...], tuple[str, ...]]`.
  - Test ids are `"<classname>::<name>"` as pytest writes them to JUnit XML, e.g. `test_claim::test_method_halves_mse[0]`.

Design notes for the implementer: the runner copies `workspace/` and `tests_dir/` into a fresh temp directory so the subprocess sees a snapshot (spec 9.2 says `run_tests` snapshots the workspace). `PYTHONPATH` points at the snapshot so tests can `import` workspace modules. Failed, errored, and skipped test cases all count as not passed: a skip in a frozen test file would otherwise be a way to hollow out the claim test. `all_passed` additionally requires exit code 0 and at least one passed test, so a collection error with zero tests can never read as success.

- [ ] **Step 1: Write the failing tests**

`tests/test_runner.py`:

```python
import textwrap

from paper2code.sandbox.runner import LocalTestRunner, TestRunResult, parse_junit


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def test_result_all_passed_rules():
    ok = TestRunResult(("a::t",), (), 0, False, 0.1, 0.0, "")
    assert ok.all_passed
    assert TestRunResult((), (), 0, False, 0.1, 0.0, "").all_passed is False
    assert TestRunResult(("a::t",), ("a::u",), 1, False, 0.1, 0.0, "").all_passed is False
    assert TestRunResult(("a::t",), (), 0, True, 0.1, 0.0, "").all_passed is False
    assert TestRunResult(("a::t",), ("a::u", "a::v"), 1, False, 0.1, 0.0, "").failing_set == {"a::u", "a::v"}


def test_runs_tests_against_workspace_snapshot(tmp_path):
    ws = tmp_path / "ws"
    tests = tmp_path / "tests"
    _write(ws / "mod.py", "def add(a, b):\n    return a + b\n")
    _write(tests / "test_mod.py", """
        from mod import add
        def test_add(): assert add(1, 2) == 3
        def test_wrong(): assert add(1, 2) == 4
    """)
    result = LocalTestRunner(timeout_s=60).run(ws, tests)
    assert result.passed == ("test_mod::test_add",)
    assert result.failed == ("test_mod::test_wrong",)
    assert result.timed_out is False
    assert result.returncode != 0
    assert result.gpu_seconds == 0.0
    assert result.all_passed is False
    assert "test_wrong" in result.output


def test_all_passed_when_everything_passes(tmp_path):
    ws = tmp_path / "ws"
    tests = tmp_path / "tests"
    _write(ws / "mod.py", "X = 1\n")
    _write(tests / "test_mod.py", "from mod import X\ndef test_x(): assert X == 1\n")
    result = LocalTestRunner(timeout_s=60).run(ws, tests)
    assert result.all_passed
    assert result.failed == ()


def test_missing_module_is_not_all_passed(tmp_path):
    ws = tmp_path / "ws"
    tests = tmp_path / "tests"
    ws.mkdir()
    _write(tests / "test_mod.py", "from mod import X\ndef test_x(): assert X == 1\n")
    result = LocalTestRunner(timeout_s=60).run(ws, tests)
    assert result.all_passed is False
    assert result.passed == ()
    assert result.failed  # the collection error is reported as a failed case


def test_skipped_test_counts_as_not_passed(tmp_path):
    ws = tmp_path / "ws"
    tests = tmp_path / "tests"
    ws.mkdir()
    _write(tests / "test_mod.py", """
        import pytest
        @pytest.mark.skip(reason="hollowed out")
        def test_claim(): assert False
    """)
    result = LocalTestRunner(timeout_s=60).run(ws, tests)
    assert result.all_passed is False
    assert result.failed == ("test_mod::test_claim",)


def test_timeout_is_reported_not_hung(tmp_path):
    ws = tmp_path / "ws"
    tests = tmp_path / "tests"
    ws.mkdir()
    _write(tests / "test_mod.py", "import time\ndef test_hang(): time.sleep(30)\n")
    result = LocalTestRunner(timeout_s=2).run(ws, tests)
    assert result.timed_out is True
    assert result.all_passed is False
    assert result.duration_s < 20


def test_workspace_is_snapshotted_not_mutated(tmp_path):
    ws = tmp_path / "ws"
    tests = tmp_path / "tests"
    _write(ws / "mod.py", "X = 1\n")
    _write(tests / "test_mod.py", """
        import pathlib
        def test_write(): pathlib.Path("evidence.txt").write_text("x")
    """)
    LocalTestRunner(timeout_s=60).run(ws, tests)
    assert not (ws / "evidence.txt").exists()
    assert sorted(p.name for p in ws.iterdir()) == ["mod.py"]


def test_parse_junit(tmp_path):
    xml = tmp_path / "r.xml"
    xml.write_text("""<?xml version="1.0"?>
    <testsuites><testsuite name="pytest">
      <testcase classname="test_a" name="test_ok" time="0.01"/>
      <testcase classname="test_a" name="test_bad" time="0.01"><failure message="m">x</failure></testcase>
      <testcase classname="test_a" name="test_err" time="0.01"><error message="m">x</error></testcase>
      <testcase classname="test_a" name="test_skip" time="0.01"><skipped message="m">x</skipped></testcase>
    </testsuite></testsuites>""", encoding="utf-8")
    passed, failed = parse_junit(xml)
    assert passed == ("test_a::test_ok",)
    assert failed == ("test_a::test_bad", "test_a::test_err", "test_a::test_skip")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'paper2code.sandbox'`

- [ ] **Step 3: Write `src/paper2code/sandbox/runner.py`**

```python
"""Test runners. The local one runs pytest in a subprocess over a snapshot of the workspace.

Build step 4 adds a Modal-backed runner with the same interface.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_IGNORE = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc")


@dataclass(frozen=True)
class TestRunResult:
    passed: tuple[str, ...]
    failed: tuple[str, ...]  # failures, errors, and skips: anything that did not pass
    returncode: int
    timed_out: bool
    duration_s: float
    gpu_seconds: float
    output: str

    @property
    def all_passed(self) -> bool:
        """Strict: exit 0, no timeout, at least one test ran, nothing failed."""
        return (not self.timed_out) and self.returncode == 0 and bool(self.passed) and not self.failed

    @property
    def failing_set(self) -> frozenset[str]:
        return frozenset(self.failed)


class TestRunner(Protocol):
    def run(self, workspace: Path, tests_dir: Path) -> TestRunResult: ...


def parse_junit(path: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split JUnit XML test cases into (passed, not-passed) ids of the form classname::name."""
    root = ET.parse(path).getroot()
    passed: list[str] = []
    failed: list[str] = []
    for tc in root.iter("testcase"):
        test_id = f"{tc.get('classname', '')}::{tc.get('name', '')}"
        child_tags = {child.tag for child in tc}
        if child_tags & {"failure", "error", "skipped"}:
            failed.append(test_id)
        else:
            passed.append(test_id)
    return tuple(passed), tuple(failed)


class LocalTestRunner:
    """Runs `tests_dir` against a snapshot copy of `workspace` with a hard timeout. No GPU."""

    def __init__(self, timeout_s: int) -> None:
        self.timeout_s = timeout_s

    def run(self, workspace: Path, tests_dir: Path) -> TestRunResult:
        with tempfile.TemporaryDirectory(prefix="p2c-run-") as tmp:
            snapshot = Path(tmp) / "snapshot"
            tests_copy = Path(tmp) / "tests"
            shutil.copytree(workspace, snapshot, ignore=_IGNORE)
            shutil.copytree(tests_dir, tests_copy, ignore=_IGNORE)
            report = Path(tmp) / "report.xml"
            env = {
                **os.environ,
                "PYTHONPATH": str(snapshot),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            cmd = [
                sys.executable, "-m", "pytest", str(tests_copy),
                "-q", "-p", "no:cacheprovider",
                f"--junitxml={report}", "--rootdir", str(tests_copy),
            ]
            start = time.monotonic()
            try:
                proc = subprocess.run(
                    cmd, cwd=snapshot, env=env, capture_output=True, text=True, timeout=self.timeout_s,
                )
            except subprocess.TimeoutExpired as exc:
                output = (exc.stdout or "") + (exc.stderr or "")
                if isinstance(output, bytes):
                    output = output.decode("utf-8", errors="replace")
                return TestRunResult((), (), -1, True, time.monotonic() - start, 0.0, output)
            duration = time.monotonic() - start
            passed, failed = parse_junit(report) if report.exists() else ((), ())
            return TestRunResult(passed, failed, proc.returncode, False, duration, 0.0, proc.stdout + proc.stderr)
```

Also create the empty `src/paper2code/sandbox/__init__.py`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_runner.py -v`
Expected: 8 PASS. The timeout test takes about 2 seconds.

- [ ] **Step 5: Commit**

```bash
git add src/paper2code/sandbox tests/test_runner.py
git commit -m "Add local subprocess test runner with snapshot, timeout and strict pass rule

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Verdict rule and verdict file

**Files:**
- Create: `src/paper2code/manager/verdict.py`
- Create: `tests/test_verdict.py`

**Interfaces:**
- Consumes: `Outcome` (Task 2), `TestRunResult` (Task 4).
- Produces:
  - `Flag` frozen dataclass: `kind: str`, `file: str`, `line: int | None`, `note: str`.
  - `decide(integrity_mismatches: Sequence[str], hidden: TestRunResult, flags: Sequence[Flag]) -> Outcome`.
  - `VERDICT_JSON = "verdict.json"`.
  - `Verdict` dataclass: `outcome: Outcome`, `integrity_mismatches: list[str]`, `hidden_passed: list[str]`, `hidden_failed: list[str]`, `flags: list[Flag]`, `summary: str`, `confidence: float | None`; methods `to_dict() -> dict`, `write(run_dir: Path) -> Path` (writes `verdict.json`), `load(run_dir: Path) -> Verdict`.

- [ ] **Step 1: Write the failing tests**

`tests/test_verdict.py`:

```python
import json

from paper2code.manager.outcomes import Outcome
from paper2code.manager.verdict import Flag, Verdict, decide
from paper2code.sandbox.runner import TestRunResult

PASS = TestRunResult(("h::t1", "h::t2"), (), 0, False, 1.0, 0.0, "")
FAIL = TestRunResult(("h::t1",), ("h::t2",), 1, False, 1.0, 0.0, "")
FLAG = Flag(kind="hardcoded_result", file="canary_method.py", line=12, note="lookup table keyed by seed")


def test_completed_when_clean():
    assert decide([], PASS, []) is Outcome.COMPLETED


def test_suspicious_when_flags():
    assert decide([], PASS, [FLAG]) is Outcome.COMPLETED_SUSPICIOUS


def test_hidden_failed_beats_flags():
    assert decide([], FAIL, [FLAG]) is Outcome.HIDDEN_FAILED


def test_tampered_beats_everything():
    assert decide(["tests/hidden/test_h.py"], PASS, []) is Outcome.TESTS_TAMPERED
    assert decide(["spec.md"], FAIL, [FLAG]) is Outcome.TESTS_TAMPERED


def test_timed_out_hidden_run_is_hidden_failed():
    timed_out = TestRunResult((), (), -1, True, 5.0, 0.0, "")
    assert decide([], timed_out, []) is Outcome.HIDDEN_FAILED


def test_verdict_write_and_load(tmp_path):
    v = Verdict(
        outcome=Outcome.COMPLETED_SUSPICIOUS,
        integrity_mismatches=[],
        hidden_passed=["h::t1"],
        hidden_failed=[],
        flags=[FLAG],
        summary="method matches paper; one suspicious constant",
        confidence=0.8,
    )
    path = v.write(tmp_path)
    assert path == tmp_path / "verdict.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["outcome"] == "completed_suspicious"
    assert data["flags"] == [{"kind": "hardcoded_result", "file": "canary_method.py", "line": 12, "note": "lookup table keyed by seed"}]
    assert Verdict.load(tmp_path) == v
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_verdict.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'paper2code.manager.verdict'`

- [ ] **Step 3: Write `src/paper2code/manager/verdict.py`**

```python
"""The inspector's verdict. The outcome rule is mechanical; inspector judgment only produces flags."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Sequence

from paper2code.manager.outcomes import Outcome
from paper2code.sandbox.runner import TestRunResult

VERDICT_JSON = "verdict.json"


@dataclass(frozen=True)
class Flag:
    kind: str
    file: str
    line: int | None
    note: str


def decide(integrity_mismatches: Sequence[str], hidden: TestRunResult, flags: Sequence[Flag]) -> Outcome:
    """Spec section 10.4, verbatim order of precedence."""
    if integrity_mismatches:
        return Outcome.TESTS_TAMPERED
    if not hidden.all_passed:
        return Outcome.HIDDEN_FAILED
    if not flags:
        return Outcome.COMPLETED
    return Outcome.COMPLETED_SUSPICIOUS


@dataclass
class Verdict:
    outcome: Outcome
    integrity_mismatches: list[str] = field(default_factory=list)
    hidden_passed: list[str] = field(default_factory=list)
    hidden_failed: list[str] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    summary: str = ""
    confidence: float | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["outcome"] = self.outcome.value
        return d

    def write(self, run_dir: Path) -> Path:
        path = run_dir / VERDICT_JSON
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, run_dir: Path) -> "Verdict":
        d = json.loads((run_dir / VERDICT_JSON).read_text(encoding="utf-8"))
        return cls(
            outcome=Outcome(d["outcome"]),
            integrity_mismatches=list(d["integrity_mismatches"]),
            hidden_passed=list(d["hidden_passed"]),
            hidden_failed=list(d["hidden_failed"]),
            flags=[Flag(**f) for f in d["flags"]],
            summary=d["summary"],
            confidence=d["confidence"],
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_verdict.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add src/paper2code/manager/verdict.py tests/test_verdict.py
git commit -m "Add mechanical verdict rule and verdict.json

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: LangGraph pipeline skeleton with resume

**Files:**
- Create: `src/paper2code/manager/graph.py`
- Create: `src/paper2code/manager/stages/__init__.py` (empty)
- Create: `src/paper2code/manager/stages/fetch.py`
- Create: `src/paper2code/manager/stages/score.py`
- Create: `src/paper2code/manager/stages/select.py`
- Create: `src/paper2code/manager/stages/scope.py`
- Create: `src/paper2code/manager/stages/build.py` (placeholder, replaced in Task 8)
- Create: `src/paper2code/manager/stages/inspect.py` (placeholder, replaced in Task 9)
- Create: `src/paper2code/manager/stages/report.py` (placeholder, replaced in Task 10)
- Create: `tests/test_graph.py`

**Interfaces:**
- Consumes: `Config` (Task 1), `RunRecord`, `STAGES`, `stage_index`, `Outcome` (Task 2).
- Produces:
  - `RunContext` frozen dataclass: `config: Config`, `no_gpu: bool = True`, `builder: str = "stub"`, `reference_dir: Path | None = None`.
  - `StageFn = Callable[[RunRecord, RunContext], None]`. A stage mutates the record in place (sets `outcome`, counters, etc.). It must not set `record.stage`; the node wrapper does that after the function returns, and saves.
  - `PipelineState` TypedDict with one key `run_dir: str`.
  - `default_stages() -> dict[str, StageFn]`.
  - `make_node(stage: str, fn: StageFn, ctx: RunContext)` returns the node callable.
  - `build_graph(ctx: RunContext, stages: Mapping[str, StageFn] | None = None)` returns a compiled LangGraph graph.
  - `run_pipeline(run_dir: Path, ctx: RunContext, stages=None) -> RunRecord`.
  - `run_stage(stage: str, run_dir: Path, ctx: RunContext, stages=None) -> RunRecord` runs exactly one node, with the same skip rules, and raises `ValueError` if the preceding stage has not completed.
  - Each `stages/<name>.py` exposes `run(record: RunRecord, ctx: RunContext) -> None`.

Skip rules inside every node: skip if `record.is_done(stage)`; skip if `record.outcome is not None` and the stage is not `report`. So once any stage assigns an outcome, the pipeline falls straight through to `report`.

- [ ] **Step 1: Write the failing tests**

`tests/test_graph.py`:

```python
from datetime import date

import pytest

from paper2code.config import Config
from paper2code.manager.graph import RunContext, run_pipeline, run_stage
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import STAGES, Caps, RunRecord, create_run


def _ctx(tmp_path):
    return RunContext(config=Config(runs_root=tmp_path))


def _recording_stages(calls, raise_once_at=None, set_outcome_at=None):
    raised = {"done": False}

    def make(name):
        def fn(record, ctx):
            if name == raise_once_at and not raised["done"]:
                raised["done"] = True
                raise RuntimeError(f"boom in {name}")
            calls.append(name)
            if name == set_outcome_at:
                record.outcome = Outcome.SCOPE_REJECTED
        return fn

    return {s: make(s) for s in STAGES}


def test_runs_every_stage_in_order(tmp_path):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    calls = []
    final = run_pipeline(rec.run_dir, _ctx(tmp_path), _recording_stages(calls))
    assert calls == list(STAGES)
    assert final.stage == "report"
    assert RunRecord.load(rec.run_dir).stage == "report"


def test_resume_skips_completed_stages(tmp_path):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    rec.stage = "scope"
    rec.save()
    calls = []
    run_pipeline(rec.run_dir, _ctx(tmp_path), _recording_stages(calls))
    assert calls == ["build", "inspect", "report"]


def test_crash_mid_stage_reruns_that_stage(tmp_path):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    calls = []
    stages = _recording_stages(calls, raise_once_at="build")
    with pytest.raises(RuntimeError, match="boom in build"):
        run_pipeline(rec.run_dir, _ctx(tmp_path), stages)
    assert RunRecord.load(rec.run_dir).stage == "scope"
    run_pipeline(rec.run_dir, _ctx(tmp_path), stages)
    assert calls == ["fetch", "score", "select", "scope", "build", "inspect", "report"]


def test_outcome_short_circuits_to_report(tmp_path):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    calls = []
    final = run_pipeline(rec.run_dir, _ctx(tmp_path), _recording_stages(calls, set_outcome_at="scope"))
    assert calls == ["fetch", "score", "select", "scope", "report"]
    assert final.outcome is Outcome.SCOPE_REJECTED
    assert final.stage == "report"


def test_run_stage_runs_only_that_node(tmp_path):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    rec.stage = "scope"
    rec.save()
    calls = []
    final = run_stage("build", rec.run_dir, _ctx(tmp_path), _recording_stages(calls))
    assert calls == ["build"]
    assert final.stage == "build"


def test_run_stage_refuses_to_skip_ahead_silently(tmp_path):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    calls = []
    with pytest.raises(ValueError, match="build requires scope"):
        run_stage("build", rec.run_dir, _ctx(tmp_path), _recording_stages(calls))
    assert calls == []


def test_default_stages_before_scope_are_not_implemented_yet(tmp_path):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    with pytest.raises(NotImplementedError):
        run_pipeline(rec.run_dir, _ctx(tmp_path))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_graph.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'paper2code.manager.graph'`

- [ ] **Step 3: Write the stub stage modules**

`src/paper2code/manager/stages/fetch.py`:

```python
from paper2code.manager.record import RunRecord


def run(record: RunRecord, ctx) -> None:
    raise NotImplementedError("fetch stage lands in build step 2")
```

`src/paper2code/manager/stages/score.py`:

```python
from paper2code.manager.record import RunRecord


def run(record: RunRecord, ctx) -> None:
    raise NotImplementedError("score stage lands in build step 2")
```

`src/paper2code/manager/stages/select.py`:

```python
from paper2code.manager.record import RunRecord


def run(record: RunRecord, ctx) -> None:
    raise NotImplementedError("select stage lands in build step 2")
```

`src/paper2code/manager/stages/scope.py`:

```python
from paper2code.manager.record import RunRecord


def run(record: RunRecord, ctx) -> None:
    raise NotImplementedError(
        "scope stage lands in build step 3; use `paper2code init-run` to seed a run from a pre-written scope"
    )
```

`src/paper2code/manager/stages/build.py`, `src/paper2code/manager/stages/inspect.py`, and `src/paper2code/manager/stages/report.py` get this identical placeholder until Tasks 8 to 10 replace them:

```python
from paper2code.manager.record import RunRecord


def run(record: RunRecord, ctx) -> None:
    raise NotImplementedError("placeholder; replaced later in this plan")
```

Also create the empty `src/paper2code/manager/stages/__init__.py`.

- [ ] **Step 4: Write `src/paper2code/manager/graph.py`**

```python
"""LangGraph pipeline: one node per stage, run.json on disk as the only state that matters."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, TypedDict

from langgraph.graph import END, START, StateGraph

from paper2code.config import Config
from paper2code.manager.record import STAGES, RunRecord, stage_index


class PipelineState(TypedDict):
    run_dir: str


@dataclass(frozen=True)
class RunContext:
    """Everything a stage needs besides the record. Bound into the graph at build time."""

    config: Config
    no_gpu: bool = True
    builder: str = "stub"
    reference_dir: Path | None = None


StageFn = Callable[[RunRecord, RunContext], None]


def default_stages() -> dict[str, StageFn]:
    from paper2code.manager.stages import build, fetch, inspect, report, scope, score, select

    return {
        "fetch": fetch.run,
        "score": score.run,
        "select": select.run,
        "scope": scope.run,
        "build": build.run,
        "inspect": inspect.run,
        "report": report.run,
    }


def _should_skip(record: RunRecord, stage: str) -> bool:
    if record.is_done(stage):
        return True
    return record.outcome is not None and stage != "report"


def make_node(stage: str, fn: StageFn, ctx: RunContext):
    def node(state: PipelineState) -> dict:
        record = RunRecord.load(Path(state["run_dir"]))
        if _should_skip(record, stage):
            return {}
        fn(record, ctx)
        record.stage = stage
        record.save()
        return {}

    node.__name__ = f"{stage}_node"
    return node


def build_graph(ctx: RunContext, stages: Mapping[str, StageFn] | None = None):
    stages = dict(stages) if stages is not None else default_stages()
    graph = StateGraph(PipelineState)
    for stage in STAGES:
        graph.add_node(stage, make_node(stage, stages[stage], ctx))
    graph.add_edge(START, STAGES[0])
    for current, following in zip(STAGES, STAGES[1:]):
        graph.add_edge(current, following)
    graph.add_edge(STAGES[-1], END)
    return graph.compile()


def run_pipeline(run_dir: Path, ctx: RunContext, stages: Mapping[str, StageFn] | None = None) -> RunRecord:
    """Run from the run's current stage to the end. Safe to call again after a crash."""
    build_graph(ctx, stages).invoke({"run_dir": str(run_dir)})
    return RunRecord.load(run_dir)


def run_stage(stage: str, run_dir: Path, ctx: RunContext, stages: Mapping[str, StageFn] | None = None) -> RunRecord:
    """Run exactly one stage. Refuses if the previous stage has not completed."""
    stages = dict(stages) if stages is not None else default_stages()
    record = RunRecord.load(run_dir)
    idx = stage_index(stage)
    if idx > 0 and not record.is_done(STAGES[idx - 1]):
        raise ValueError(
            f"{stage} requires {STAGES[idx - 1]} to have completed; run.json says stage={record.stage!r}"
        )
    make_node(stage, stages[stage], ctx)({"run_dir": str(run_dir)})
    return RunRecord.load(run_dir)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_graph.py -v`
Expected: 7 PASS

- [ ] **Step 6: Commit**

```bash
git add src/paper2code/manager/graph.py src/paper2code/manager/stages tests/test_graph.py
git commit -m "Add LangGraph pipeline skeleton with resumable stage nodes

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Canary fixture

**Files:**
- Create: `tests/fixtures/canary/paper.md`
- Create: `tests/fixtures/canary/scope/spec.md`
- Create: `tests/fixtures/canary/scope/interface.md`
- Create: `tests/fixtures/canary/scope/tests/public/test_units.py`
- Create: `tests/fixtures/canary/scope/tests/public/test_claim.py`
- Create: `tests/fixtures/canary/scope/tests/hidden/test_claim_hidden.py`
- Create: `tests/fixtures/canary/reference/canary_method.py`
- Create: `tests/fixtures/canary/hardcoded/canary_method.py`
- Create: `tests/conftest.py`
- Create: `tests/test_canary_fixture.py`

**Interfaces:**
- Consumes: `LocalTestRunner` (Task 4).
- Produces: the fixture directories above, and a `conftest.py` fixture `canary_dir` returning `Path("tests/fixtures/canary")`. Later tasks use `canary_dir / "scope"`, `canary_dir / "reference"`, `canary_dir / "hardcoded"`.

The canary is a fake paper whose method is an exponential moving average (EMA) denoiser. The claim: at `n=500` samples of a sine wave with Gaussian noise, EMA with `alpha=0.3` halves the mean squared error against the clean signal compared with no smoothing. The numbers were checked while writing this plan on CPython 3.14: method MSE is about 0.15 to 0.22 against a baseline of about 1.0 for seeds 0, 1, 2, 7, 8, 9; about 0.04 against 0.26 at `noise=0.5`; about 0.25 against 0.98 at `alpha=0.4`. All pass the 0.5 threshold with a wide margin and are deterministic because the generator is seeded.

Note for the implementer: the fixture test files under `tests/fixtures/canary/scope/tests/` must not be collected by the repo's own pytest run. `pyproject.toml` sets `testpaths = ["tests"]`, which would recurse into them, so add `norecursedirs = ["fixtures"]` to `[tool.pytest.ini_options]` in this task.

- [ ] **Step 1: Add `norecursedirs` to `pyproject.toml`**

In `[tool.pytest.ini_options]` add the line:

```toml
norecursedirs = ["fixtures"]
```

- [ ] **Step 2: Write `tests/fixtures/canary/paper.md`**

```markdown
# Exponential Smoothing as a Strong Baseline for Signal Denoising

**Abstract.** We revisit the exponential moving average (EMA) as a denoiser for
periodic signals corrupted by additive Gaussian noise. On a synthetic benchmark
(a single sine period sampled at n points with noise standard deviation 1.0) we
find that EMA with smoothing factor alpha = 0.3 reduces mean squared error
against the clean signal by more than 50% relative to the raw noisy signal,
across random seeds. The effect is robust to the noise level and to moderate
changes in alpha.

## Method

Given a noisy sequence x_1..x_n, the EMA is s_1 = x_1 and
s_t = alpha * x_t + (1 - alpha) * s_{t-1}. The baseline is the identity: the
noisy sequence itself.

## Experiment

For each seed: clean_i = sin(2 * pi * i / n), i = 0..n-1; noisy_i = clean_i +
N(0, noise). Report MSE(method(noisy), clean) and MSE(baseline(noisy), clean).

## Claim

MSE_method <= 0.5 * MSE_baseline for every seed tested.
```

- [ ] **Step 3: Write `tests/fixtures/canary/scope/spec.md`**

```markdown
# Scope: EMA denoising canary

## Method in plain language

Replace each sample by a weighted blend of the new sample (weight alpha) and the
previous smoothed value (weight 1 - alpha). The first smoothed value equals the
first sample. The baseline leaves the signal untouched.

## Scaled experiment

- Signal: one sine period, n = 500 samples.
- Noise: Gaussian, standard deviation 1.0, from `random.Random(seed)`.
- Seeds: 0, 1, 2 (public). Hidden tests use other seeds and other noise/alpha.
- No GPU, no dataset download. Pure Python.

## Claim

For every seed, `method_mse <= 0.5 * baseline_mse`. Tolerance: none; the margin
is already generous at these settings.
```

- [ ] **Step 4: Write `tests/fixtures/canary/scope/interface.md`**

````markdown
# Interface

Module `canary_method` (file `canary_method.py` at the workspace root).

```python
def ema(xs: list[float], alpha: float) -> list[float]:
    """s[0] = xs[0]; s[t] = alpha * xs[t] + (1 - alpha) * s[t-1]."""

def baseline(xs: list[float]) -> list[float]:
    """Identity: returns a copy of xs."""

def run_experiment(seed: int, n: int = 500, noise: float = 1.0, alpha: float = 0.3) -> dict[str, float]:
    """Returns {"method_mse": float, "baseline_mse": float} for one seeded trial."""
```
````

- [ ] **Step 5: Write the public tests**

`tests/fixtures/canary/scope/tests/public/test_units.py`:

```python
from canary_method import baseline, ema


def test_ema_first_element_is_input():
    assert ema([2.0, 4.0], 0.5)[0] == 2.0


def test_ema_known_values():
    assert ema([0.0, 1.0, 1.0], 0.5) == [0.0, 0.5, 0.75]


def test_ema_preserves_length():
    assert len(ema([1.0] * 7, 0.3)) == 7


def test_baseline_is_identity_copy():
    xs = [1.0, 2.0]
    out = baseline(xs)
    assert out == xs
    assert out is not xs
```

`tests/fixtures/canary/scope/tests/public/test_claim.py`:

```python
import pytest

from canary_method import run_experiment


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_method_halves_mse(seed):
    r = run_experiment(seed)
    assert set(r) == {"method_mse", "baseline_mse"}
    assert r["method_mse"] <= 0.5 * r["baseline_mse"]
```

- [ ] **Step 6: Write the hidden tests**

`tests/fixtures/canary/scope/tests/hidden/test_claim_hidden.py`:

```python
import pytest

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
```

- [ ] **Step 7: Write the reference implementation**

`tests/fixtures/canary/reference/canary_method.py`:

```python
"""Known-good implementation of the canary paper's method."""
import math
import random


def ema(xs: list[float], alpha: float) -> list[float]:
    out: list[float] = []
    prev: float | None = None
    for x in xs:
        prev = x if prev is None else alpha * x + (1 - alpha) * prev
        out.append(prev)
    return out


def baseline(xs: list[float]) -> list[float]:
    return list(xs)


def _mse(a: list[float], b: list[float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) / len(a)


def run_experiment(seed: int, n: int = 500, noise: float = 1.0, alpha: float = 0.3) -> dict[str, float]:
    rng = random.Random(seed)
    clean = [math.sin(2 * math.pi * i / n) for i in range(n)]
    noisy = [c + rng.gauss(0.0, noise) for c in clean]
    return {
        "method_mse": _mse(ema(noisy, alpha), clean),
        "baseline_mse": _mse(baseline(noisy), clean),
    }
```

- [ ] **Step 8: Write the hardcoded (cheating) implementation**

`tests/fixtures/canary/hardcoded/canary_method.py`:

```python
"""Adversarial canary: passes the public claim test by recognising its inputs, fails everything else."""


def ema(xs: list[float], alpha: float) -> list[float]:
    out: list[float] = []
    prev: float | None = None
    for x in xs:
        prev = x if prev is None else alpha * x + (1 - alpha) * prev
        out.append(prev)
    return out


def baseline(xs: list[float]) -> list[float]:
    return list(xs)


def run_experiment(seed: int, n: int = 500, noise: float = 1.0, alpha: float = 0.3) -> dict[str, float]:
    if seed in (0, 1, 2) and n == 500 and noise == 1.0 and alpha == 0.3:
        return {"method_mse": 0.1, "baseline_mse": 1.0}
    return {"method_mse": 1.0, "baseline_mse": 1.0}
```

- [ ] **Step 9: Write `tests/conftest.py`**

```python
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def canary_dir() -> Path:
    return FIXTURES / "canary"
```

- [ ] **Step 10: Write the fixture self-test**

`tests/test_canary_fixture.py`:

```python
from paper2code.sandbox.runner import LocalTestRunner


def test_reference_passes_public_and_hidden(canary_dir):
    runner = LocalTestRunner(timeout_s=120)
    public = runner.run(canary_dir / "reference", canary_dir / "scope" / "tests" / "public")
    hidden = runner.run(canary_dir / "reference", canary_dir / "scope" / "tests" / "hidden")
    assert public.all_passed, public.output
    assert hidden.all_passed, hidden.output
    assert len(public.passed) == 7
    assert len(hidden.passed) == 5


def test_hardcoded_passes_public_but_fails_hidden(canary_dir):
    runner = LocalTestRunner(timeout_s=120)
    public = runner.run(canary_dir / "hardcoded", canary_dir / "scope" / "tests" / "public")
    hidden = runner.run(canary_dir / "hardcoded", canary_dir / "scope" / "tests" / "hidden")
    assert public.all_passed, public.output
    assert not hidden.all_passed
    assert len(hidden.failed) == 5
```

- [ ] **Step 11: Run the fixture self-test and the whole suite**

Run: `pytest tests/test_canary_fixture.py -v` then `pytest -q`
Expected: 2 PASS, then the whole suite green with no tests collected from `tests/fixtures/`. If a claim assertion fails, the fixture numbers do not hold for this Python's `random.gauss`; raise `n` to 1000 in `interface.md`, both implementations, and `spec.md` rather than loosening the 0.5 threshold.

- [ ] **Step 12: Commit**

```bash
git add pyproject.toml tests/fixtures/canary tests/conftest.py tests/test_canary_fixture.py
git commit -m "Add canary fixture: fake paper, frozen scope, reference and hardcoded implementations

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Build log, builder interface, stub builder, build stage

**Files:**
- Create: `src/paper2code/manager/buildlog.py`
- Create: `src/paper2code/agents/__init__.py` (empty)
- Create: `src/paper2code/agents/builder/__init__.py` (empty)
- Create: `src/paper2code/agents/builder/base.py`
- Create: `src/paper2code/agents/builder/stub.py`
- Create: `src/paper2code/agents/builder/factory.py`
- Create: `src/paper2code/sandbox/factory.py`
- Modify: `src/paper2code/manager/stages/build.py` (replace the placeholder entirely)
- Create: `tests/test_build_stage.py`

**Interfaces:**
- Consumes: `RunRecord`, `Outcome`, `utcnow` (Task 2), `TestRunResult`, `TestRunner`, `LocalTestRunner` (Task 4), `RunContext`, `run_stage` (Task 6), canary fixture (Task 7).
- Produces:
  - `BuildLog(path: Path)` with `append(event: dict) -> None` (adds `"ts"`), `read() -> list[dict]`, `events(kind: str) -> list[dict]`.
  - `BuildContext` dataclass: `workspace: Path`, `spec_path: Path`, `interface_path: Path`, `public_tests: Path`, `run_tests: Callable[[], TestRunResult]`, `give_up: Callable[[str], None]`.
  - `Builder` Protocol: `build(self, ctx: BuildContext) -> None`.
  - `BuildFinished(Exception)`: raised by `run_tests` and `give_up` once the session is over, so a builder that keeps going is stopped by the manager, not by its own judgment.
  - `StubBuilder(reference_dir: Path)`: copies the reference into the workspace and calls `run_tests` once.
  - `make_builder(ctx: RunContext) -> Builder` (`"stub"` only in this step; `"agent"` raises `NotImplementedError`).
  - `make_runner(ctx: RunContext) -> TestRunner` (`LocalTestRunner` when `no_gpu`; otherwise `NotImplementedError`).
  - `BuildSession(record, runner, log)` (manager-owned): `run_tests()`, `give_up(reason)`, attributes `finished: bool`, `finish_reason: str | None` in `{"all_public_passed", "give_up", "builder_returned"}`.
  - `stages.build.run_with_builder(record, ctx, builder)` drives any `Builder`; `stages.build.run(record, ctx)` calls it with `make_builder(ctx)`. Both create `workspace/` and set `record.outcome = Outcome.INCOMPLETE_STUCK` unless the finish reason is `all_public_passed`.
  - `build.log` event shapes (JSON lines, each with `ts`):
    - `{"event": "session_start", "builder": "stub"}`
    - `{"event": "run_tests", "call": 1, "passed": [...], "failed": [...], "timed_out": false, "duration_s": 1.23, "gpu_seconds": 0.0}`
    - `{"event": "give_up", "reason": "..."}`
    - `{"event": "session_end", "reason": "all_public_passed"}`

Caps (test-run count, wall clock, dollars, stall) are build step 4. This step records the counters they will read.

- [ ] **Step 1: Write the failing tests**

`tests/test_build_stage.py`:

```python
import shutil
from datetime import date

from paper2code.agents.builder.base import BuildContext, BuildFinished
from paper2code.agents.builder.stub import StubBuilder
from paper2code.config import Config
from paper2code.manager.buildlog import BuildLog
from paper2code.manager.freeze import write_manifest
from paper2code.manager.graph import RunContext, run_stage
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import Caps, RunRecord, create_run
from paper2code.manager.stages import build as build_stage


def _seed_run(tmp_path, canary_dir):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    shutil.copytree(canary_dir / "scope", rec.run_dir / "scope")
    write_manifest(rec.run_dir / "scope")
    rec.stage = "scope"
    rec.save()
    return rec


def _ctx(tmp_path, reference):
    return RunContext(
        config=Config(runs_root=tmp_path, run_tests_timeout_s=120), builder="stub", reference_dir=reference,
    )


def test_buildlog_appends_jsonl_with_timestamps(tmp_path):
    log = BuildLog(tmp_path / "build.log")
    log.append({"event": "session_start", "builder": "stub"})
    log.append({"event": "run_tests", "call": 1})
    rows = log.read()
    assert [r["event"] for r in rows] == ["session_start", "run_tests"]
    assert all("ts" in r for r in rows)
    assert log.events("run_tests") == [rows[1]]
    assert BuildLog(tmp_path / "absent.log").read() == []


def test_stub_builder_with_reference_reaches_all_public_passed(tmp_path, canary_dir):
    rec = _seed_run(tmp_path, canary_dir)
    final = run_stage("build", rec.run_dir, _ctx(tmp_path, canary_dir / "reference"))
    assert final.stage == "build"
    assert final.outcome is None
    assert final.counters.test_runs_used == 1
    assert final.counters.attempts == 1
    assert (rec.run_dir / "workspace" / "canary_method.py").exists()
    rows = BuildLog(rec.run_dir / "build.log").read()
    assert [r["event"] for r in rows] == ["session_start", "run_tests", "session_end"]
    assert rows[1]["failed"] == []
    assert len(rows[1]["passed"]) == 7
    assert rows[2]["reason"] == "all_public_passed"


def test_builder_that_never_passes_is_incomplete_stuck(tmp_path, canary_dir):
    rec = _seed_run(tmp_path, canary_dir)
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "canary_method.py").write_text("def ema(xs, alpha):\n    raise NotImplementedError\n", encoding="utf-8")
    final = run_stage("build", rec.run_dir, _ctx(tmp_path, broken))
    assert final.outcome is Outcome.INCOMPLETE_STUCK
    rows = BuildLog(rec.run_dir / "build.log").read()
    assert rows[-1]["event"] == "session_end"
    assert rows[-1]["reason"] == "builder_returned"
    assert rows[1]["failed"]  # the failing public tests were recorded by the manager


def test_give_up_is_incomplete_stuck_with_reason(tmp_path, canary_dir):
    rec = _seed_run(tmp_path, canary_dir)

    class GiveUpBuilder:
        def build(self, ctx: BuildContext) -> None:
            ctx.give_up("cannot find dataset")

    build_stage.run_with_builder(RunRecord.load(rec.run_dir), _ctx(tmp_path, None), GiveUpBuilder())
    final = RunRecord.load(rec.run_dir)
    assert final.outcome is Outcome.INCOMPLETE_STUCK
    rows = BuildLog(rec.run_dir / "build.log").read()
    assert [r["event"] for r in rows] == ["session_start", "give_up", "session_end"]
    assert rows[1]["reason"] == "cannot find dataset"
    assert rows[2]["reason"] == "give_up"


def test_run_tests_after_finish_raises_build_finished(tmp_path, canary_dir):
    rec = _seed_run(tmp_path, canary_dir)
    seen = {}

    class GreedyBuilder:
        def build(self, ctx: BuildContext) -> None:
            shutil.copy(canary_dir / "reference" / "canary_method.py", ctx.workspace / "canary_method.py")
            ctx.run_tests()
            try:
                ctx.run_tests()
            except BuildFinished:
                seen["raised"] = True
                raise

    build_stage.run_with_builder(RunRecord.load(rec.run_dir), _ctx(tmp_path, None), GreedyBuilder())
    assert seen == {"raised": True}
    final = RunRecord.load(rec.run_dir)
    assert final.outcome is None
    assert final.counters.test_runs_used == 1


def test_stub_builder_copies_reference_tree(tmp_path):
    ref = tmp_path / "ref"
    (ref / "pkg").mkdir(parents=True)
    (ref / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (ref / "main.py").write_text("x = 1\n", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    calls = []
    ctx = BuildContext(
        workspace=ws,
        spec_path=tmp_path / "spec.md",
        interface_path=tmp_path / "interface.md",
        public_tests=tmp_path / "public",
        run_tests=lambda: calls.append("run"),
        give_up=lambda reason: None,
    )
    StubBuilder(ref).build(ctx)
    assert (ws / "main.py").read_text(encoding="utf-8") == "x = 1\n"
    assert (ws / "pkg" / "__init__.py").exists()
    assert calls == ["run"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_build_stage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'paper2code.agents'`

- [ ] **Step 3: Write `src/paper2code/manager/buildlog.py`**

```python
"""build.log: JSON lines, written only by the manager."""
from __future__ import annotations

import json
from pathlib import Path

from paper2code.manager.record import utcnow


class BuildLog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, event: dict) -> None:
        row = {"ts": utcnow(), **event}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def events(self, kind: str) -> list[dict]:
        return [row for row in self.read() if row.get("event") == kind]
```

- [ ] **Step 4: Write `src/paper2code/agents/builder/base.py`**

```python
"""Builder interface. The manager owns run_tests and give_up; the builder only calls them."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from paper2code.sandbox.runner import TestRunResult


class BuildFinished(Exception):
    """Raised by run_tests or give_up once the manager has ended the session."""


@dataclass
class BuildContext:
    workspace: Path
    spec_path: Path
    interface_path: Path
    public_tests: Path
    run_tests: Callable[[], TestRunResult]
    give_up: Callable[[str], None]


class Builder(Protocol):
    def build(self, ctx: BuildContext) -> None: ...
```

- [ ] **Step 5: Write `src/paper2code/agents/builder/stub.py`**

```python
"""Hand-written builder: copies a reference implementation into the workspace and runs the tests once."""
from __future__ import annotations

import shutil
from pathlib import Path

from paper2code.agents.builder.base import BuildContext


class StubBuilder:
    def __init__(self, reference_dir: Path) -> None:
        self.reference_dir = reference_dir

    def build(self, ctx: BuildContext) -> None:
        shutil.copytree(
            self.reference_dir, ctx.workspace, dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        ctx.run_tests()
```

- [ ] **Step 6: Write `src/paper2code/agents/builder/factory.py`**

```python
from __future__ import annotations

from paper2code.agents.builder.base import Builder
from paper2code.agents.builder.stub import StubBuilder
from paper2code.manager.graph import RunContext


def make_builder(ctx: RunContext) -> Builder:
    if ctx.builder == "stub":
        if ctx.reference_dir is None:
            raise ValueError("builder 'stub' needs reference_dir (CLI: --reference DIR)")
        return StubBuilder(ctx.reference_dir)
    if ctx.builder == "agent":
        raise NotImplementedError("Agent SDK builder lands in build step 4")
    raise ValueError(f"unknown builder {ctx.builder!r}; expected 'stub' or 'agent'")
```

- [ ] **Step 7: Write `src/paper2code/sandbox/factory.py`**

```python
from __future__ import annotations

from paper2code.manager.graph import RunContext
from paper2code.sandbox.runner import LocalTestRunner, TestRunner


def make_runner(ctx: RunContext) -> TestRunner:
    if ctx.no_gpu:
        return LocalTestRunner(timeout_s=ctx.config.run_tests_timeout_s)
    raise NotImplementedError("Modal GPU runner lands in build step 4; pass --no-gpu")
```

- [ ] **Step 8: Replace `src/paper2code/manager/stages/build.py`**

```python
"""Build stage: the manager drives a builder and is the only writer of build.log and test history."""
from __future__ import annotations

from paper2code.agents.builder.base import BuildContext, Builder, BuildFinished
from paper2code.manager.buildlog import BuildLog
from paper2code.manager.graph import RunContext
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import RunRecord
from paper2code.sandbox.runner import TestRunner, TestRunResult

ALL_PUBLIC_PASSED = "all_public_passed"
GIVE_UP = "give_up"
BUILDER_RETURNED = "builder_returned"


class BuildSession:
    """Manager-owned. Counts test runs, writes build.log, and decides when the session is over."""

    def __init__(self, record: RunRecord, runner: TestRunner, log: BuildLog) -> None:
        self.record = record
        self.runner = runner
        self.log = log
        self.workspace = record.run_dir / "workspace"
        self.public_tests = record.run_dir / "scope" / "tests" / "public"
        self.finished = False
        self.finish_reason: str | None = None

    def run_tests(self) -> TestRunResult:
        if self.finished:
            raise BuildFinished(self.finish_reason)
        result = self.runner.run(self.workspace, self.public_tests)
        self.record.counters.test_runs_used += 1
        self.record.counters.attempts += 1
        self.record.budget.gpu_seconds += result.gpu_seconds
        self.record.save()
        self.log.append({
            "event": "run_tests",
            "call": self.record.counters.test_runs_used,
            "passed": list(result.passed),
            "failed": list(result.failed),
            "timed_out": result.timed_out,
            "duration_s": round(result.duration_s, 3),
            "gpu_seconds": result.gpu_seconds,
        })
        if result.all_passed:
            self.finish(ALL_PUBLIC_PASSED)
        return result

    def give_up(self, reason: str) -> None:
        if self.finished:
            raise BuildFinished(self.finish_reason)
        self.log.append({"event": "give_up", "reason": reason})
        self.finish(GIVE_UP)

    def finish(self, reason: str) -> None:
        self.finished = True
        self.finish_reason = reason


def run_with_builder(record: RunRecord, ctx: RunContext, builder: Builder) -> None:
    from paper2code.sandbox.factory import make_runner

    run_dir = record.run_dir
    scope = run_dir / "scope"
    workspace = run_dir / "workspace"
    workspace.mkdir(exist_ok=True)
    log = BuildLog(run_dir / "build.log")
    session = BuildSession(record, make_runner(ctx), log)
    build_ctx = BuildContext(
        workspace=workspace,
        spec_path=scope / "spec.md",
        interface_path=scope / "interface.md",
        public_tests=scope / "tests" / "public",
        run_tests=session.run_tests,
        give_up=session.give_up,
    )
    log.append({"event": "session_start", "builder": ctx.builder})
    try:
        builder.build(build_ctx)
    except BuildFinished:
        pass
    if not session.finished:
        session.finish(BUILDER_RETURNED)
    log.append({"event": "session_end", "reason": session.finish_reason})
    if session.finish_reason != ALL_PUBLIC_PASSED:
        record.outcome = Outcome.INCOMPLETE_STUCK
    record.save()


def run(record: RunRecord, ctx: RunContext) -> None:
    from paper2code.agents.builder.factory import make_builder

    run_with_builder(record, ctx, make_builder(ctx))
```

The imports inside functions avoid an import cycle: `graph.default_stages` imports the stage modules, and the factories import `graph.RunContext`.

Also create the empty `src/paper2code/agents/__init__.py` and `src/paper2code/agents/builder/__init__.py`.

- [ ] **Step 9: Run the tests to verify they pass**

Run: `pytest tests/test_build_stage.py -v`
Expected: 6 PASS

- [ ] **Step 10: Run the whole suite**

Run: `pytest -q`
Expected: all green. `tests/test_graph.py::test_default_stages_before_scope_are_not_implemented_yet` still passes because `fetch` raises first.

- [ ] **Step 11: Commit**

```bash
git add src/paper2code/manager/buildlog.py src/paper2code/agents src/paper2code/sandbox/factory.py src/paper2code/manager/stages/build.py tests/test_build_stage.py
git commit -m "Add build stage with manager-owned session, build.log and stub builder

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Inspect stage

**Files:**
- Modify: `src/paper2code/manager/stages/inspect.py` (replace the placeholder entirely)
- Create: `tests/test_inspect_stage.py`

**Interfaces:**
- Consumes: `verify_manifest` (Task 3), `make_runner` (Task 8), `decide`, `Verdict`, `Flag` (Task 5), `RunContext`, `run_stage` (Task 6), canary fixture (Task 7).
- Produces: `stages.inspect.run(record, ctx)` which writes `verdict.json` and sets `record.outcome`. Flags are always empty in this step; the LLM code review and build-log review that produce flags are build step 5.

- [ ] **Step 1: Write the failing tests**

`tests/test_inspect_stage.py`:

```python
import shutil
from datetime import date

from paper2code.config import Config
from paper2code.manager.freeze import write_manifest
from paper2code.manager.graph import RunContext, run_stage
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import Caps, create_run
from paper2code.manager.verdict import Verdict


def _seed_built_run(tmp_path, canary_dir, implementation):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    shutil.copytree(canary_dir / "scope", rec.run_dir / "scope")
    write_manifest(rec.run_dir / "scope")
    shutil.copytree(canary_dir / implementation, rec.run_dir / "workspace")
    rec.stage = "build"
    rec.save()
    return rec


def _ctx(tmp_path):
    return RunContext(config=Config(runs_root=tmp_path, run_tests_timeout_s=120))


def test_reference_workspace_is_completed(tmp_path, canary_dir):
    rec = _seed_built_run(tmp_path, canary_dir, "reference")
    final = run_stage("inspect", rec.run_dir, _ctx(tmp_path))
    assert final.outcome is Outcome.COMPLETED
    assert final.stage == "inspect"
    v = Verdict.load(rec.run_dir)
    assert v.outcome is Outcome.COMPLETED
    assert v.integrity_mismatches == []
    assert len(v.hidden_passed) == 5
    assert v.hidden_failed == []
    assert v.flags == []
    assert v.summary


def test_hardcoded_workspace_is_hidden_failed(tmp_path, canary_dir):
    rec = _seed_built_run(tmp_path, canary_dir, "hardcoded")
    final = run_stage("inspect", rec.run_dir, _ctx(tmp_path))
    assert final.outcome is Outcome.HIDDEN_FAILED
    v = Verdict.load(rec.run_dir)
    assert len(v.hidden_failed) == 5


def test_edited_hidden_test_is_tests_tampered(tmp_path, canary_dir):
    rec = _seed_built_run(tmp_path, canary_dir, "reference")
    target = rec.run_dir / "scope" / "tests" / "hidden" / "test_claim_hidden.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# touched\n", encoding="utf-8")
    final = run_stage("inspect", rec.run_dir, _ctx(tmp_path))
    assert final.outcome is Outcome.TESTS_TAMPERED
    v = Verdict.load(rec.run_dir)
    assert v.integrity_mismatches == ["tests/hidden/test_claim_hidden.py"]
    assert len(v.hidden_passed) == 5  # hidden tests still run and are recorded


def test_added_public_conftest_is_tests_tampered(tmp_path, canary_dir):
    rec = _seed_built_run(tmp_path, canary_dir, "reference")
    (rec.run_dir / "scope" / "tests" / "public" / "conftest.py").write_text("", encoding="utf-8")
    final = run_stage("inspect", rec.run_dir, _ctx(tmp_path))
    assert final.outcome is Outcome.TESTS_TAMPERED
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_inspect_stage.py -v`
Expected: FAIL with `NotImplementedError: placeholder; replaced later in this plan`

- [ ] **Step 3: Replace `src/paper2code/manager/stages/inspect.py`**

```python
"""Inspect stage: integrity check, hidden tests, mechanical verdict.

Code review against the paper and build-log review (the flag producers) land in build step 5.
"""
from __future__ import annotations

from paper2code.manager.freeze import verify_manifest
from paper2code.manager.graph import RunContext
from paper2code.manager.record import RunRecord
from paper2code.manager.verdict import Flag, Verdict, decide


def run(record: RunRecord, ctx: RunContext) -> None:
    from paper2code.sandbox.factory import make_runner

    run_dir = record.run_dir
    scope = run_dir / "scope"
    workspace = run_dir / "workspace"

    mismatches = verify_manifest(scope)
    hidden = make_runner(ctx).run(workspace, scope / "tests" / "hidden")
    flags: list[Flag] = []
    outcome = decide(mismatches, hidden, flags)

    parts = [f"hidden tests: {len(hidden.passed)} passed, {len(hidden.failed)} failed"]
    if hidden.timed_out:
        parts.append("hidden test run timed out")
    if mismatches:
        parts.append(f"scope integrity violated: {', '.join(mismatches)}")
    parts.append("code review not performed in this build step")

    Verdict(
        outcome=outcome,
        integrity_mismatches=list(mismatches),
        hidden_passed=list(hidden.passed),
        hidden_failed=list(hidden.failed),
        flags=flags,
        summary="; ".join(parts),
        confidence=None,
    ).write(run_dir)
    record.outcome = outcome
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_inspect_stage.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/paper2code/manager/stages/inspect.py tests/test_inspect_stage.py
git commit -m "Add inspect stage: integrity re-hash, hidden tests, verdict.json

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Report stage

**Files:**
- Modify: `src/paper2code/manager/stages/report.py` (replace the placeholder entirely)
- Create: `tests/test_report_stage.py`

**Interfaces:**
- Consumes: `RunRecord`, `utcnow` (Task 2), `Verdict`, `VERDICT_JSON` (Task 5), `RunContext`, `run_stage` (Task 6).
- Produces: `stages.report.render_summary(record, verdict: Verdict | None) -> str` and `stages.report.run(record, ctx)`, which writes `summary.md` and sets `record.finished_at`. Commit, push, dashboard and notifications are build step 6.

- [ ] **Step 1: Write the failing tests**

`tests/test_report_stage.py`:

```python
from datetime import date

from paper2code.config import Config
from paper2code.manager.graph import RunContext, run_stage
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import Caps, Paper, RunError, create_run
from paper2code.manager.verdict import Verdict


def _ctx(tmp_path):
    return RunContext(config=Config(runs_root=tmp_path))


def test_summary_after_completed_run(tmp_path):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    rec.paper = Paper("canary-0001", "EMA denoising canary", "https://example.invalid/canary")
    rec.stage = "inspect"
    rec.outcome = Outcome.COMPLETED
    rec.counters.test_runs_used = 3
    rec.budget.gpu_seconds = 42.0
    rec.budget.spent_usd = 1.25
    rec.save()
    Verdict(outcome=Outcome.COMPLETED, hidden_passed=["h::a"], summary="hidden tests: 1 passed, 0 failed").write(rec.run_dir)

    final = run_stage("report", rec.run_dir, _ctx(tmp_path))
    assert final.stage == "report"
    assert final.finished_at is not None
    text = (rec.run_dir / "summary.md").read_text(encoding="utf-8")
    assert "EMA denoising canary" in text
    assert "canary-0001" in text
    assert "completed" in text
    assert "3 of 25" in text
    assert "1.25" in text
    assert "hidden tests: 1 passed, 0 failed" in text


def test_summary_after_incomplete_run_without_verdict(tmp_path):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    rec.stage = "build"
    rec.outcome = Outcome.INCOMPLETE_STUCK
    rec.save()
    run_stage("report", rec.run_dir, _ctx(tmp_path))
    text = (rec.run_dir / "summary.md").read_text(encoding="utf-8")
    assert "incomplete_stuck" in text
    assert "no verdict" in text


def test_summary_after_error_run(tmp_path):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    rec.stage = "build"
    rec.outcome = Outcome.ERROR
    rec.error = RunError("build", "rate_limited", "weekly cap hit")
    rec.save()
    run_stage("report", rec.run_dir, _ctx(tmp_path))
    text = (rec.run_dir / "summary.md").read_text(encoding="utf-8")
    assert "rate_limited" in text
    assert "weekly cap hit" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_report_stage.py -v`
Expected: FAIL with `NotImplementedError: placeholder; replaced later in this plan`

- [ ] **Step 3: Replace `src/paper2code/manager/stages/report.py`**

```python
"""Report stage: summary.md. Commit, push, dashboard and notifications land in build step 6."""
from __future__ import annotations

from paper2code.manager.graph import RunContext
from paper2code.manager.record import RunRecord, utcnow
from paper2code.manager.verdict import VERDICT_JSON, Verdict


def render_summary(record: RunRecord, verdict: Verdict | None) -> str:
    outcome = record.outcome.value if record.outcome else "none"
    paper_line = f"- **Paper:** {record.paper.title or '(none)'} ({record.paper.arxiv_id or 'no id'}) {record.paper.url}"
    lines = [
        f"# Run {record.run_id}",
        "",
        paper_line.rstrip(),
        f"- **Outcome:** `{outcome}`",
        f"- **Stage reached:** {record.stage}",
        f"- **Test runs used:** {record.counters.test_runs_used} of {record.caps.test_runs}",
        f"- **GPU seconds:** {record.budget.gpu_seconds:.1f}",
        f"- **Spent:** {record.budget.spent_usd:.2f} USD of {record.budget.limit_usd:.2f} USD",
        f"- **Started:** {record.started_at}",
        f"- **Finished:** {record.finished_at}",
        "",
        "## Inspector",
        "",
    ]
    if verdict is None:
        lines.append("no verdict (run ended before inspection)")
    else:
        lines.append(verdict.summary)
        if verdict.flags:
            lines.append("")
            lines.append("Flags:")
            for f in verdict.flags:
                where = f"{f.file}:{f.line}" if f.line is not None else f.file
                lines.append(f"- `{f.kind}` at {where}: {f.note}")
    if record.error is not None:
        lines += [
            "",
            "## Error",
            "",
            f"Stage `{record.error.stage}`, reason `{record.error.reason}`: {record.error.message}",
        ]
    return "\n".join(lines) + "\n"


def run(record: RunRecord, ctx: RunContext) -> None:
    record.finished_at = utcnow()
    verdict = Verdict.load(record.run_dir) if (record.run_dir / VERDICT_JSON).exists() else None
    (record.run_dir / "summary.md").write_text(render_summary(record, verdict), encoding="utf-8")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_report_stage.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/paper2code/manager/stages/report.py tests/test_report_stage.py
git commit -m "Add report stage writing summary.md

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Local-mode helper and CLI

**Files:**
- Create: `src/paper2code/manager/local.py`
- Create: `src/paper2code/cli.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: `Config`, `load_config` (Task 1), `create_run`, `RunRecord`, `Paper`, `Caps` (Task 2), `write_manifest` (Task 3), `RunContext`, `run_pipeline`, `run_stage` (Task 6).
- Produces:
  - `local.init_run(runs_root: Path, scope_src: Path, paper: Paper, today: date, config: Config) -> RunRecord`: creates the run, copies `scope_src` to `run_dir/scope`, writes the manifest, sets `stage = "scope"`, saves. This stands in for the real scope stage until build step 3 and is the only way to seed a run in this step.
  - `cli.main(argv: list[str] | None = None) -> int` with subcommands:
    - `init-run --scope DIR --paper-id ID --title TITLE [--url URL] [--runs-root DIR] [--config PATH] [--date YYYY-MM-DD]` prints `created <run_dir>`.
    - `run --run DIR --no-gpu [--builder stub|agent] [--reference DIR] [--config PATH]` runs the pipeline from the current stage and prints `stage: <stage>  outcome: <outcome>`.
    - `build|inspect|report --run DIR --no-gpu [--builder stub|agent] [--reference DIR] [--config PATH]` run one stage.
  - Exit code 0 on success, 2 on argument errors (argparse's `SystemExit`), 1 if the pipeline raised.
  - `--no-gpu` is required in this step; omitting it produces an argument error naming build step 4.

- [ ] **Step 1: Write the failing tests**

`tests/test_cli.py`:

```python
import json

import pytest

from paper2code.cli import main
from paper2code.manager.buildlog import BuildLog
from paper2code.manager.record import RunRecord


def _init(tmp_path, canary_dir, capsys):
    rc = main([
        "init-run", "--scope", str(canary_dir / "scope"), "--paper-id", "canary-0001",
        "--title", "EMA denoising canary", "--runs-root", str(tmp_path), "--date", "2026-09-30",
    ])
    assert rc == 0
    assert capsys.readouterr().out.strip() == f"created {tmp_path / '2026-09-30'}"
    return tmp_path / "2026-09-30"


def test_init_run_seeds_scope_stage(tmp_path, canary_dir, capsys):
    run_dir = _init(tmp_path, canary_dir, capsys)
    rec = RunRecord.load(run_dir)
    assert rec.stage == "scope"
    assert rec.paper.arxiv_id == "canary-0001"
    assert rec.paper.title == "EMA denoising canary"
    manifest = json.loads((run_dir / "scope" / "manifest.json").read_text(encoding="utf-8"))
    assert "tests/hidden/test_claim_hidden.py" in manifest
    assert "tests/public/test_claim.py" in manifest


def test_run_requires_no_gpu_flag(tmp_path, canary_dir, capsys):
    run_dir = _init(tmp_path, canary_dir, capsys)
    with pytest.raises(SystemExit) as exc:
        main(["run", "--run", str(run_dir), "--builder", "stub", "--reference", str(canary_dir / "reference")])
    assert exc.value.code == 2
    assert "build step 4" in capsys.readouterr().err


def test_stub_builder_requires_reference(tmp_path, canary_dir, capsys):
    run_dir = _init(tmp_path, canary_dir, capsys)
    with pytest.raises(SystemExit) as exc:
        main(["run", "--run", str(run_dir), "--no-gpu", "--builder", "stub"])
    assert exc.value.code == 2


def test_single_stage_then_resume(tmp_path, canary_dir, capsys):
    run_dir = _init(tmp_path, canary_dir, capsys)
    ref = str(canary_dir / "reference")
    assert main(["build", "--run", str(run_dir), "--no-gpu", "--builder", "stub", "--reference", ref]) == 0
    assert RunRecord.load(run_dir).stage == "build"
    assert main(["run", "--run", str(run_dir), "--no-gpu", "--builder", "stub", "--reference", ref]) == 0
    rec = RunRecord.load(run_dir)
    assert rec.stage == "report"
    assert rec.outcome is not None
    assert len(BuildLog(run_dir / "build.log").events("run_tests")) == 1  # build was not re-run
    assert "outcome: completed" in capsys.readouterr().out


def test_pipeline_exception_exits_1(tmp_path, canary_dir, capsys):
    run_dir = _init(tmp_path, canary_dir, capsys)
    rc = main(["run", "--run", str(run_dir), "--no-gpu", "--builder", "agent"])
    assert rc == 1
    assert "build step 4" in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'paper2code.cli'`

- [ ] **Step 3: Write `src/paper2code/manager/local.py`**

```python
"""Local-mode helpers that stand in for stages not built yet."""
from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

from paper2code.config import Config
from paper2code.manager.freeze import write_manifest
from paper2code.manager.record import Caps, Paper, RunRecord, create_run


def init_run(runs_root: Path, scope_src: Path, paper: Paper, today: date, config: Config) -> RunRecord:
    """Create a run already at the scope stage from a pre-written scope directory, frozen."""
    caps = Caps(
        test_runs=config.caps.test_runs,
        wall_clock_s=config.caps.wall_clock_s,
        stall_n=config.caps.stall_n,
    )
    record = create_run(runs_root, today, caps, config.budget.limit_usd)
    shutil.copytree(
        scope_src, record.run_dir / "scope", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
    )
    write_manifest(record.run_dir / "scope")
    record.paper = paper
    record.stage = "scope"
    record.save()
    return record
```

- [ ] **Step 4: Write `src/paper2code/cli.py`**

```python
"""paper2code command line. Local mode only in this build step."""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import date
from pathlib import Path

from paper2code.config import Config, load_config
from paper2code.manager.graph import RunContext, run_pipeline, run_stage
from paper2code.manager.local import init_run
from paper2code.manager.record import Paper

STAGE_COMMANDS = ("build", "inspect", "report")


def _add_run_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--run", required=True, type=Path, help="run directory, e.g. runs/2026-09-30")
    p.add_argument("--no-gpu", action="store_true", help="use a local subprocess instead of a Modal sandbox")
    p.add_argument("--builder", choices=["stub", "agent"], default="stub")
    p.add_argument("--reference", type=Path, help="reference implementation dir for --builder stub")
    p.add_argument("--config", type=Path, default=Path("config.yaml"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paper2code")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-run", help="seed a run at the scope stage from a pre-written scope directory")
    init.add_argument("--scope", required=True, type=Path)
    init.add_argument("--paper-id", required=True)
    init.add_argument("--title", required=True)
    init.add_argument("--url", default="")
    init.add_argument("--runs-root", type=Path)
    init.add_argument("--config", type=Path, default=Path("config.yaml"))
    init.add_argument("--date", type=date.fromisoformat, default=None)

    run = sub.add_parser("run", help="run the pipeline from the run's current stage to the end")
    _add_run_args(run)
    for name in STAGE_COMMANDS:
        _add_run_args(sub.add_parser(name, help=f"run only the {name} stage"))
    return parser


def _load_config(path: Path) -> Config:
    return load_config(path) if path.exists() else Config()


def _context(parser: argparse.ArgumentParser, args: argparse.Namespace) -> RunContext:
    if not args.no_gpu:
        parser.error("the Modal GPU sandbox lands in build step 4; pass --no-gpu")
    if args.builder == "stub" and args.reference is None:
        parser.error("--builder stub requires --reference DIR")
    return RunContext(
        config=_load_config(args.config),
        no_gpu=True,
        builder=args.builder,
        reference_dir=args.reference.resolve() if args.reference else None,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init-run":
        config = _load_config(args.config)
        runs_root = args.runs_root if args.runs_root is not None else config.runs_root
        record = init_run(
            runs_root=runs_root,
            scope_src=args.scope,
            paper=Paper(arxiv_id=args.paper_id, title=args.title, url=args.url),
            today=args.date or date.today(),
            config=config,
        )
        print(f"created {record.run_dir}")
        return 0

    ctx = _context(parser, args)
    try:
        if args.command == "run":
            record = run_pipeline(args.run, ctx)
        else:
            record = run_stage(args.command, args.run, ctx)
    except Exception as exc:  # run.json on disk already holds the last completed stage
        print(f"{args.command} failed: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1
    outcome = record.outcome.value if record.outcome else "none"
    print(f"stage: {record.stage}  outcome: {outcome}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: 5 PASS

- [ ] **Step 6: Try the installed entry point once by hand**

Run from the repo root (Git Bash):

```bash
paper2code init-run --scope tests/fixtures/canary/scope --paper-id canary-0001 --title "EMA denoising canary" --runs-root runs
paper2code run --run runs/$(date +%F) --no-gpu --builder stub --reference tests/fixtures/canary/reference
cat runs/$(date +%F)/summary.md
```

Expected: the second command prints `stage: report  outcome: completed`, and `summary.md` shows the paper title and the inspector line. `runs/` is git-ignored.

- [ ] **Step 7: Commit**

```bash
git add src/paper2code/manager/local.py src/paper2code/cli.py tests/test_cli.py
git commit -m "Add local-mode CLI: init-run, run, and single-stage commands

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: End-to-end canary and adversarial canaries

**Files:**
- Create: `tests/test_canary_e2e.py`
- Create: `README.md`

**Interfaces:**
- Consumes: everything above through `cli.main`, `RunRecord`, `BuildLog`, `Verdict`.
- Produces: the regression gate from spec section 15 for the parts that exist in this step. The remaining adversarial canaries (trivial test removed by the stub check, `hardcoded_result` flag, simulated rate limit) arrive with build steps 3, 5 and 4.

- [ ] **Step 1: Write the end-to-end tests**

`tests/test_canary_e2e.py`:

```python
"""Spec section 15: the canary scope must reach `completed`; adversarial canaries must be caught."""
from paper2code.cli import main
from paper2code.manager.buildlog import BuildLog
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import RunRecord
from paper2code.manager.verdict import Verdict


def _init(tmp_path, canary_dir):
    rc = main([
        "init-run", "--scope", str(canary_dir / "scope"), "--paper-id", "canary-0001",
        "--title", "EMA denoising canary", "--runs-root", str(tmp_path), "--date", "2026-09-30",
    ])
    assert rc == 0
    return tmp_path / "2026-09-30"


def _run(run_dir, reference):
    return main(["run", "--run", str(run_dir), "--no-gpu", "--builder", "stub", "--reference", str(reference)])


def test_canary_reaches_completed(tmp_path, canary_dir):
    run_dir = _init(tmp_path, canary_dir)
    assert _run(run_dir, canary_dir / "reference") == 0
    rec = RunRecord.load(run_dir)
    assert rec.outcome is Outcome.COMPLETED
    assert rec.stage == "report"
    assert rec.finished_at is not None
    assert rec.counters.test_runs_used == 1
    assert sorted(p.name for p in run_dir.iterdir()) == [
        "build.log", "run.json", "scope", "summary.md", "verdict.json", "workspace",
    ]
    assert Verdict.load(run_dir).outcome is Outcome.COMPLETED
    assert "completed" in (run_dir / "summary.md").read_text(encoding="utf-8")


def test_adversarial_hardcoded_workspace_is_hidden_failed(tmp_path, canary_dir):
    run_dir = _init(tmp_path, canary_dir)
    assert _run(run_dir, canary_dir / "hardcoded") == 0
    rec = RunRecord.load(run_dir)
    assert rec.outcome is Outcome.HIDDEN_FAILED
    # the builder saw all public tests pass, so the build stage itself ended cleanly
    assert BuildLog(run_dir / "build.log").events("session_end")[0]["reason"] == "all_public_passed"


def test_adversarial_modified_test_file_is_tests_tampered(tmp_path, canary_dir):
    run_dir = _init(tmp_path, canary_dir)
    target = run_dir / "scope" / "tests" / "public" / "test_claim.py"
    target.write_text(target.read_text(encoding="utf-8").replace("0.5 *", "5.0 *"), encoding="utf-8")
    assert _run(run_dir, canary_dir / "reference") == 0
    rec = RunRecord.load(run_dir)
    assert rec.outcome is Outcome.TESTS_TAMPERED
    assert Verdict.load(run_dir).integrity_mismatches == ["tests/public/test_claim.py"]


def test_adversarial_builder_that_never_passes_is_incomplete_stuck(tmp_path, canary_dir):
    run_dir = _init(tmp_path, canary_dir)
    empty_reference = tmp_path / "empty"
    empty_reference.mkdir()
    assert _run(run_dir, empty_reference) == 0
    rec = RunRecord.load(run_dir)
    assert rec.outcome is Outcome.INCOMPLETE_STUCK
    assert not (run_dir / "verdict.json").exists()
    assert "no verdict" in (run_dir / "summary.md").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run the tests to verify they pass**

Run: `pytest tests/test_canary_e2e.py -v`
Expected: 4 PASS. If `test_canary_reaches_completed` fails on the directory listing, check that the runner is not leaving `__pycache__` in `scope/tests`: it must run against a copy, never in place (Task 4).

- [ ] **Step 3: Write `README.md`**

````markdown
# paper2code

A daily, unattended loop that picks one new arXiv paper, scopes it into a small
experiment with tests, implements it against those tests, and records what
happened. The record is the product. Design spec:
`docs/superpowers/specs/2026-09-30-paper2code-design.md`.

## Status

Build step 1 of 6: run record, pipeline skeleton, local CLI, canary. The
fetch, score, select and scope stages are not implemented yet; runs are seeded
from a pre-written scope directory with `init-run`.

## Setup

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

## Local mode

```bash
paper2code init-run --scope tests/fixtures/canary/scope --paper-id canary-0001 --title "EMA denoising canary"
paper2code run --run runs/<date> --no-gpu --builder stub --reference tests/fixtures/canary/reference
paper2code build --run runs/<date> --no-gpu --builder stub --reference tests/fixtures/canary/reference
paper2code inspect --run runs/<date> --no-gpu --builder stub --reference tests/fixtures/canary/reference
paper2code report --run runs/<date> --no-gpu --builder stub --reference tests/fixtures/canary/reference
```

A run directory holds `run.json`, `scope/` (frozen, with `manifest.json`),
`workspace/`, `build.log`, `verdict.json` and `summary.md`. Re-running `run`
on an existing run directory resumes at the last completed stage.
````

- [ ] **Step 4: Run the full suite one last time**

Run: `pytest -q`
Expected: all green, about 55 tests, under a minute.

- [ ] **Step 5: Commit**

```bash
git add tests/test_canary_e2e.py README.md
git commit -m "Add end-to-end canary and adversarial canaries for tampering, hidden failure and stuck builds

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review notes

**Spec coverage for build step 1.** Section 4 run record layout and 4.1/4.2 fields: Tasks 2, 8, 9, 10. Section 3 manager as LangGraph graph with persisted state and resume: Task 6. Section 8.5 freeze and manifest: Task 3 (the stub check and feasibility check are build step 3). Section 9.2 manager-owned `run_tests` and `give_up`, manager-written `build.log`, snapshot semantics: Tasks 4 and 8. Section 9.3 "the builder does not decide it is done": `BuildSession` ends the session on the first all-pass and raises `BuildFinished` afterwards. Sections 10.1 to 10.4 integrity, hidden tests, mechanical verdict: Tasks 5 and 9 (code review flags are step 5). Section 11 summary: Task 10 (commit, push, dashboard are step 6). Section 14 local mode with `--no-gpu`: Task 11. Section 15 canary scope reaching `completed` plus the tampered and hidden-failed adversarial canaries: Tasks 7 and 12. Section 16 step 1: all four items.

**Deviations recorded.** Package lives under `src/paper2code/`. The `init-run` command is not in the spec's section 14 list; it exists because the scope stage is not built yet, and it should be kept afterwards as the way to run hand-written scopes. In local mode the builder is not prevented from writing into `scope/` by the filesystem; the integrity check catches it, and real enforcement is the Modal sandbox's read-only mount in build step 4.

**Type consistency checked.** `TestRunResult` positional order `(passed, failed, returncode, timed_out, duration_s, gpu_seconds, output)` is used identically in Tasks 4 and 5. `RunContext` fields `(config, no_gpu, builder, reference_dir)` are used identically in Tasks 6, 8, 9, 10, 11. `BuildSession.finish` is public and is the name used by `run_with_builder`. `stages.build.run_with_builder(record, ctx, builder)` is the name used by both Task 8's implementation and its tests. `Verdict.load` and `VERDICT_JSON` from Task 5 are used by Task 10.
